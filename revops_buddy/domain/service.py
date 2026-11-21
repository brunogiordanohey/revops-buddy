"""Deterministic, typed RevOps tools used by the Angels voice agent."""

from __future__ import annotations

import difflib
import re
import unicodedata
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_DOWN
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

from ..repository import Repository
from .periods import parse_period
from .timeutil import TimeError, iso_utc, parse_utc, utc_now


SUPPORTED_FILTERS = {
    "owner_id",
    "owner_name",
    "pipeline_id",
    "stage_id",
    "stage_name",
    "segment",
    "forecast_category",
    "status",
    "include_closed",
}
STATUSES = {"open", "closed_won", "closed_lost"}
DEFAULT_PROPERTIES = (
    "dealname",
    "amount",
    "dealcurrency",
    "pipeline",
    "dealstage",
    "hubspot_owner_id",
    "closedate",
    "hs_lastmodifieddate",
    "createdate",
    "forecastcategory",
    "probability",
    "segment",
)


class RevOpsError(ValueError):
    """A deterministic domain input/configuration error."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _decimal_string(value: Decimal | None) -> str | None:
    if value is None:
        return None
    if not value.is_finite():
        return None
    return format(value, "f")


# Decimal arithmetic keeps presentation rounding separate from CRM calculations.
def round_spoken_amount(value: Decimal | str | int) -> Decimal:
    """Nearest thousand at |amount| >= 10,000; halfway ties toward zero.

    In particular 261,500 becomes 261,000, as required by the speech policy.
    """
    amount = Decimal(str(value))
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    return amount.quantize(Decimal("1E3"), rounding=ROUND_HALF_DOWN) if abs(amount) >= 10000 else amount


_SMALL = {
    "en": "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    "es": "cero uno dos tres cuatro cinco seis siete ocho nueve diez once doce trece catorce quince dieciséis diecisiete dieciocho diecinueve veinte veintiuno veintidós veintitrés veinticuatro veinticinco veintiséis veintisiete veintiocho veintinueve".split(),
    "it": "zero uno due tre quattro cinque sei sette otto nove dieci undici dodici tredici quattordici quindici sedici diciassette diciotto diciannove".split(),
}
_TENS = {
    "en": "twenty thirty forty fifty sixty seventy eighty ninety".split(),
    "es": "veinte treinta cuarenta cincuenta sesenta setenta ochenta noventa".split(),
    "it": "venti trenta quaranta cinquanta sessanta settanta ottanta novanta".split(),
}


def integer_words(number: int, language: str = "en") -> str:
    """Cardinals for spoken amounts in the three supported speech languages."""
    if language not in _SMALL:
        raise ValueError("unsupported speech language")
    if number < 0:
        return {"en": "minus ", "es": "menos ", "it": "meno "}[language] + integer_words(-number, language)
    if number < len(_SMALL[language]):
        return _SMALL[language][number]
    if number < 100:
        tens, unit = divmod(number, 10)
        stem = _TENS[language][tens - 2]
        if not unit:
            return stem
        if language == "it":
            return (stem[:-1] if unit in {1, 8} else stem) + ("tré" if unit == 3 else _SMALL[language][unit])
        return stem + ("-" if language == "en" else " y ") + _SMALL[language][unit]
    if number < 1000:
        hundreds, rest = divmod(number, 100)
        if language == "es":
            stem = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos", "ochocientos", "novecientos"][hundreds]
            if number == 100:
                return "cien"
        elif language == "it":
            stem = (integer_words(hundreds, language) if hundreds > 1 else "") + "cento"
            if rest == 8 or 80 <= rest < 90:
                stem = stem[:-1]
        else:
            stem = integer_words(hundreds, language) + " hundred"
        return stem + (("" if language == "it" else " ") + integer_words(rest, language) if rest else "")
    # Spanish uses the long scale; Italian uses miliardo for 10**9.
    scales = {
        "en": [(10**12, "trillion", "trillion"), (10**9, "billion", "billion"), (10**6, "million", "million"), (1000, "thousand", "thousand")],
        "es": [(10**12, "billón", "billones"), (10**6, "millón", "millones"), (1000, "mil", "mil")],
        "it": [(10**12, "bilione", "bilioni"), (10**9, "miliardo", "miliardi"), (10**6, "milione", "milioni"), (1000, "mille", "mila")],
    }
    for scale, singular, plural in scales[language]:
        if number < scale:
            continue
        count, rest = divmod(number, scale)
        prefix = integer_words(count, language)
        if language == "es":
            prefix = re.sub(r"veintiuno$", "veintiún", prefix)
            prefix = re.sub(r"uno$", "un", prefix)
            whole = "mil" if scale == 1000 and count == 1 else prefix + " " + (singular if count == 1 else plural)
        elif language == "it" and scale == 1000:
            whole = "mille" if count == 1 else prefix.removesuffix("é") + ("e" if prefix.endswith("é") else "") + "mila"
        else:
            whole = ("un" if language == "it" and count == 1 else prefix) + " " + (singular if count == 1 else plural)
        return whole + (("" if language == "it" and scale == 1000 else " ") + integer_words(rest, language) if rest else "")
    raise ValueError("unsupported amount")


def spoken_amount(value: Decimal | str | int, language: str = "en", *, exact: bool = False) -> str:
    amount = Decimal(str(value))
    rounded = amount if exact else round_spoken_amount(amount)
    if not rounded.is_finite():
        raise ValueError("amount must be finite")
    digits = format(abs(rounded), "f").partition(".")
    words = integer_words(int(digits[0]), language)
    if digits[2] and int(digits[2]):
        words += {"en": " point ", "es": " coma ", "it": " virgola "}[language]
        words += " ".join(integer_words(int(digit), language) for digit in digits[2])
    if rounded < 0:
        words = {"en": "minus ", "es": "menos ", "it": "meno "}[language] + words
    if rounded != amount:
        words = {"en": "about ", "es": "aproximadamente ", "it": "circa "}[language] + words
    return words


def with_spoken_amounts(node: Any) -> Any:
    """Add localized presentation fields without changing exact tool values."""
    if isinstance(node, list):
        return [with_spoken_amounts(item) for item in node]
    if not isinstance(node, dict):
        return node
    result = {key: (value if key.startswith("spoken_") else with_spoken_amounts(value))
              for key, value in node.items()}
    for key, value in node.items():
        if key.startswith("spoken_") or not (key == "amount" or key.endswith("_amount")):
            continue
        if value is None or isinstance(value, (dict, list, bool)):
            continue
        try:
            result["spoken_" + key] = {lang: spoken_amount(value, lang) for lang in _SMALL}
            result["spoken_exact_" + key] = {lang: spoken_amount(value, lang, exact=True) for lang in _SMALL}
        except (InvalidOperation, ValueError):
            continue
    return result


class RevOpsService:
    """Deterministic read-only tools over complete CRM snapshots."""

    def __init__(self, repository: Repository, company_config: dict) -> None:
        if not isinstance(repository, Repository):
            raise TypeError("repository must be an revops_buddy.repository.Repository")
        if not isinstance(company_config, dict):
            raise TypeError("company_config must be a dict")
        required = ("company_id", "config_version", "reporting_currency", "timezone")
        missing = [name for name in required if not company_config.get(name)]
        if missing:
            raise ValueError(f"company_config missing required fields: {', '.join(missing)}")
        self.repository = repository
        self.company_config = company_config

    @staticmethod
    def definitions() -> list[dict]:
        return [
            {
                "name": "get_company_context",
                "description": (
                    "Return approved company definitions, process rules, and notes. "
                    "Only configured, sourced context is returned."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Optional topic keyword such as pipeline, forecast, or fiscal calendar.",
                        }
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_pipeline_summary",
                "description": (
                    "Calculate deal counts and amounts by stage, owner, or segment from one complete "
                    "snapshot. Closed deals are excluded unless explicitly requested or filtered."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "period": _period_schema("Close-date period to include. Omit it to cover all close dates, which is the default for questions about open pipeline, stages, owners, won or lost deals unless a period is named."),
                        "filters": _filters_schema(),
                        "group_by": {
                            "type": "string",
                            "enum": ["stage", "owner", "segment"],
                            "default": "stage",
                        },
                        "as_of": _as_of_schema(),
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_forecast",
                "description": (
                    "Return forecast-category totals, weighted pipeline, or an explicitly submitted "
                    "manager forecast. Category totals are never labeled as submitted forecasts."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "period": _period_schema("Close-date period to include."),
                        "basis": {
                            "type": "string",
                            "enum": ["category", "weighted", "submitted"],
                            "description": "Forecast basis; the bases are intentionally not interchangeable.",
                        },
                        "filters": _filters_schema(),
                        "as_of": _as_of_schema(),
                    },
                    "required": ["period", "basis"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_deal",
                "description": (
                    "Resolve a deal by exact HubSpot ID, exact name, partial name, company name, or "
                    "misheard name; non-exact queries return a resolution or candidate deals. "
                    "Returns sourced fields and lists only changes observed in stored complete snapshots."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query_or_id": {"type": "string"},
                        "as_of": _as_of_schema(),
                    },
                    "required": ["query_or_id"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "compare_snapshots",
                "description": (
                    "Compare two available complete snapshots and explain aggregate deltas plus new, "
                    "won, lost, changed, and slipped deals."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "from_time": {
                            "type": "string",
                            "description": "UTC timestamp; the latest complete snapshot at or before it is used.",
                        },
                        "to_time": {
                            "type": "string",
                            "description": "UTC timestamp; the latest complete snapshot at or before it is used.",
                        },
                        "filters": _filters_schema(),
                    },
                    "required": ["from_time", "to_time"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "simulate_slip",
                "description": (
                    "Hypothetically move selected open deals to a target close period. This is a "
                    "scenario only and never mutates CRM data or a stored snapshot."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "deal_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "uniqueItems": True,
                        },
                        "target_period": _period_schema("Destination close period."),
                        "baseline": {
                            "type": "string",
                            "description": "Optional complete snapshot_id; latest is used when omitted.",
                        },
                    },
                    "required": ["deal_ids", "target_period"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "get_previous_commitments",
                "description": "Retrieve sourced prior meeting commitments and their recorded status.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "meeting_series": {"type": "string"},
                        "deal_id": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "list_deals",
                "description": (
                    "List individual deals matching filters, sorted and limited — use for questions like "
                    "the biggest open deal, deals closing this month, top deals in a stage, or which deals "
                    "an owner has. Returns deal names, stage, status, amount, currency, close date and owner."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "period": _period_schema("Close-date period to include."),
                        "filters": _filters_schema(),
                        "sort_by": {
                            "type": "string",
                            "enum": ["amount", "close_date"],
                            "default": "amount",
                        },
                        "order": {
                            "type": "string",
                            "enum": ["desc", "asc"],
                            "description": "Defaults to desc for amount and asc for close_date.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                        "as_of": _as_of_schema(),
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            },
        ]

    def tool_definitions(self) -> list[dict]:
        return self.definitions()

    def load_demo(self) -> dict:
        """Load the explicitly labeled synthetic fixture; never use it as live fallback."""
        if not self.company_config.get("synthetic"):
            raise RevOpsError(
                "demo_not_allowed",
                "Synthetic demo data cannot be loaded for a non-synthetic company configuration",
            )
        fixture_dir = Path(__file__).resolve().parents[1] / "fixtures"
        with (fixture_dir / "company.json").open(encoding="utf-8") as handle:
            fixture = json.load(handle)
        config = fixture.get("company_config", fixture)
        if config.get("company_id") != self.company_config.get("company_id"):
            raise RevOpsError(
                "demo_company_mismatch",
                "Fixture company_id does not match the configured company",
            )
        snapshot_ids: list[str] = []
        for filename in fixture.get("snapshots", []):
            with (fixture_dir / filename).open(encoding="utf-8") as handle:
                snapshot = json.load(handle)
            if not snapshot.get("synthetic"):
                raise RevOpsError("demo_invalid", f"Fixture snapshot is not synthetic: {filename}")
            if snapshot.get("company_id") != config["company_id"]:
                raise RevOpsError("demo_invalid", f"Fixture company mismatch: {filename}")
            existing = self.repository.get_snapshot(snapshot.get("snapshot_id", ""))
            snapshot_id = existing["snapshot_id"] if existing else self.repository.save_snapshot(snapshot)
            snapshot_ids.append(snapshot_id)
        return {
            "ok": True,
            "synthetic": True,
            "source": "revops_buddy/fixtures",
            "company_id": config["company_id"],
            "config_version": config["config_version"],
            "snapshot_ids": snapshot_ids,
            "warnings": [],
        }

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not isinstance(name, str) or not name:
            return _error("invalid_tool", "Tool name must be a non-empty string")
        if not isinstance(arguments, dict):
            return _error("invalid_arguments", "Tool arguments must be an object")
        handlers = {
            "get_company_context": self._tool_company_context,
            "get_pipeline_summary": self._tool_pipeline_summary,
            "get_forecast": self._tool_forecast,
            "get_deal": self._tool_deal,
            "compare_snapshots": self._tool_compare,
            "simulate_slip": self._tool_simulate_slip,
            "get_previous_commitments": self._tool_commitments,
            "list_deals": self._tool_list_deals,
        }
        handler = handlers.get(name)
        if handler is None:
            return _error("unknown_tool", f"Unknown tool: {name}", {"available_tools": list(handlers)})
        try:
            return with_spoken_amounts(handler(arguments))
        except RevOpsError as exc:
            return _error(exc.code, exc.message, exc.details)
        except (TimeError, ValueError, TypeError, LookupError) as exc:
            return _error("invalid_input", str(exc))

    # Snapshot and deal normalization -------------------------------------------------

    def _complete_snapshots(self) -> list[dict]:
        snapshots = [
            item
            for item in self.repository.list_snapshots()
            if item.get("company_id") == self.company_config["company_id"]
            and item.get("complete") is True
            and item.get("status") == "complete"
        ]
        return sorted(snapshots, key=lambda item: parse_utc(item["captured_at"], "captured_at"))

    def _select_snapshot(self, as_of: Any = None) -> dict:
        snapshots = self._complete_snapshots()
        if not snapshots:
            raise RevOpsError("missing_snapshot", "No complete snapshot is available for this company")
        if as_of is None:
            return snapshots[-1]
        requested = parse_utc(as_of, "as_of")
        eligible = [item for item in snapshots if parse_utc(item["captured_at"], "captured_at") <= requested]
        if not eligible:
            raise RevOpsError("snapshot_not_available", "No complete snapshot exists at or before as_of")
        return eligible[-1]

    def _snapshot_by_id(self, snapshot_id: str) -> dict:
        snapshot = self.repository.get_snapshot(snapshot_id)
        if snapshot is None or snapshot.get("company_id") != self.company_config["company_id"]:
            raise RevOpsError("snapshot_not_available", f"Complete snapshot not found: {snapshot_id}")
        if snapshot.get("complete") is not True or snapshot.get("status") != "complete":
            raise RevOpsError("incomplete_snapshot", "Incomplete snapshots cannot be queried")
        return snapshot

    def _stage_lookup(self, snapshot: dict) -> dict[tuple[str, str], dict[str, Any]]:
        result: dict[tuple[str, str], dict[str, Any]] = {}
        configured = self.company_config.get("pipelines", [])
        configured_list = configured.values() if isinstance(configured, dict) else configured
        for pipeline in configured_list:
            if not isinstance(pipeline, dict):
                continue
            pipeline_id = str(pipeline.get("id", "default"))
            stages = pipeline.get("stages", {})
            stage_items = stages.items() if isinstance(stages, dict) else (
                (str(stage.get("id", "")), stage) for stage in stages if isinstance(stage, dict)
            )
            for stage_id, stage in stage_items:
                if not isinstance(stage, dict):
                    continue
                result[(pipeline_id, stage_id)] = {
                    "stage_id": stage_id,
                    "stage_name": str(stage.get("label", stage.get("name", stage_id))),
                    "status": str(stage.get("status", "open")).lower(),
                    "forecast_category": str(
                        stage.get("forecast_category", stage.get("forecastCategory", "pipeline"))
                    ).lower(),
                    "probability": stage.get("probability"),
                    "display_order": stage.get("displayOrder"),
                }
        for pipeline in snapshot.get("pipelines", []):
            if not isinstance(pipeline, dict):
                continue
            pipeline_id = str(pipeline.get("id", pipeline.get("pipelineId", "default")))
            for stage in pipeline.get("stages", []):
                if not isinstance(stage, dict):
                    continue
                stage_id = str(stage.get("id", stage.get("stageId", "")))
                key = (pipeline_id, stage_id)
                if key in result:
                    # Tenant configuration may override stage semantics, but API order
                    # is authoritative and must survive that override.
                    result[key]["display_order"] = stage.get("displayOrder", result[key].get("display_order"))
                    continue
                metadata = stage.get("metadata", {}) if isinstance(stage.get("metadata", {}), dict) else {}
                is_closed = _parse_hubspot_flag(metadata.get("isClosed"), default=False)
                is_won = _parse_hubspot_flag(metadata.get("isWon"), default=None)
                probability, probability_ok = _parse_stage_probability(metadata.get("probability"))
                status = _derive_stage_status(is_closed, is_won, probability, probability_ok)
                result[key] = {
                    "stage_id": stage_id,
                    "stage_name": str(stage.get("label", stage.get("name", stage_id))),
                    "status": status,
                    "forecast_category": "closed_won" if status == "closed_won" else "pipeline",
                    "probability": metadata.get("probability"),
                    "display_order": stage.get("displayOrder"),
                }
        return result

    def _owner_lookup(self, snapshot: dict) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for owner in snapshot.get("owners", []):
            if not isinstance(owner, dict):
                continue
            owner_id = str(owner.get("id", owner.get("ownerId", owner.get("hubspot_owner_id", ""))))
            if owner_id:
                result[owner_id] = owner
        return result

    def _deal_views(self, snapshot: dict, warnings: list[str]) -> list[dict[str, Any]]:
        stages = self._stage_lookup(snapshot)
        owners = self._owner_lookup(snapshot)
        config = self.company_config
        amount_field = str(config.get("amount_field", "amount"))
        currency_field = str(config.get("currency_field", "dealcurrency"))
        segment_field = str(config.get("segment_field", "segment"))
        forecast_field = str(config.get("forecast_category_field", "forecastcategory"))
        probability_field = str(config.get("probability_field", "probability"))
        views: list[dict[str, Any]] = []
        source_updated_values: list[Any] = []
        for deal in snapshot.get("deals", []):
            if not isinstance(deal, dict):
                warnings.append("snapshot contains a non-object deal; it was ignored")
                continue
            props = deal.get("properties", {})
            if not isinstance(props, dict):
                props = {}
            deal_id = str(deal.get("id", props.get("hs_object_id", "")))
            if not deal_id:
                warnings.append("snapshot contains a deal without an ID; it was ignored")
                continue
            pipeline_id = str(props.get("pipeline", "default"))
            stage_id = str(props.get("dealstage", ""))
            stage = stages.get((pipeline_id, stage_id), {
                "stage_id": stage_id,
                "stage_name": stage_id or "unknown",
                "status": "unknown",
                "forecast_category": "unknown",
                "probability": None,
            })
            status = stage["status"]
            if status not in STATUSES:
                warnings.append(f"deal {deal_id} has an unmapped stage {stage_id!r}")
            owner_id_value = props.get("hubspot_owner_id")
            owner_id = str(owner_id_value) if owner_id_value is not None else None
            owner = owners.get(owner_id or "", {})
            owner_name = owner.get(
                "name",
                owner.get("firstName") and f"{owner.get('firstName')} {owner.get('lastName', '')}".strip(),
            )
            amount_raw = props.get(amount_field)
            amount, amount_warning = _parse_decimal(amount_raw)
            if amount_warning:
                warnings.append(f"deal {deal_id}: {amount_warning}")
            probability_raw = props.get(probability_field, stage.get("probability"))
            probability, probability_warning = _parse_decimal(probability_raw)
            if probability_warning:
                warnings.append(f"deal {deal_id} probability: {probability_warning}")
            close_raw = props.get("closedate")
            close_at = None
            if close_raw is not None:
                try:
                    close_at = parse_utc(close_raw, "closedate")
                except TimeError:
                    warnings.append(f"deal {deal_id} has an invalid closedate; period filters exclude it")
            updated_raw = props.get("hs_lastmodifieddate", deal.get("updatedAt"))
            if updated_raw is not None:
                try:
                    source_updated_values.append(parse_utc(updated_raw, "source_updated_at"))
                except TimeError:
                    warnings.append(f"deal {deal_id} has an invalid source update timestamp")
            category_raw = props.get(forecast_field)
            category = str(category_raw).lower() if category_raw is not None else stage["forecast_category"]
            if status == "closed_won":
                category = "closed_won"
            views.append(
                {
                    "id": deal_id,
                    "name": props.get("dealname"),
                    "properties": props,
                    "source": deal.get("source", snapshot.get("source", "snapshot")),
                    "pipeline_id": pipeline_id,
                    "stage_id": stage_id,
                    "stage_name": stage["stage_name"],
                    "status": status,
                    "forecast_category": category,
                    "owner_id": owner_id,
                    "owner_name": str(owner_name) if owner_name is not None else None,
                    "segment": props.get(segment_field),
                    "amount_raw": amount_raw,
                    "amount": amount,
                    "currency": str(props.get(currency_field, "") or "").upper() or None,
                    "probability_raw": probability_raw,
                    "probability": probability,
                    "close_raw": close_raw,
                    "close_at": close_at,
                    "updated_raw": updated_raw,
                }
            )
        if snapshot.get("source_updated_at"):
            try:
                source_updated_values.insert(0, parse_utc(snapshot["source_updated_at"], "source_updated_at"))
            except TimeError:
                warnings.append("snapshot.source_updated_at is invalid")
        snapshot["_computed_source_updated_at"] = (
            iso_utc(max(source_updated_values)) if source_updated_values else None
        )
        return views

    def _filters(self, raw: Any, *, default_include_closed: bool = False) -> dict[str, Any]:
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise RevOpsError("invalid_filter", "filters must be an object")
        unknown = sorted(set(raw) - SUPPORTED_FILTERS)
        if unknown:
            raise RevOpsError(
                "unsupported_filter",
                f"Unsupported filter keys: {', '.join(unknown)}",
                {"supported_filters": sorted(SUPPORTED_FILTERS)},
            )
        result = dict(raw)
        if "status" in result and result["status"] not in STATUSES:
            raise RevOpsError("invalid_filter", "status must be open, closed_won, or closed_lost")
        if "include_closed" in result and not isinstance(result["include_closed"], bool):
            raise RevOpsError("invalid_filter", "include_closed must be boolean")
        result.setdefault("include_closed", default_include_closed)
        if "forecast_category" in result and not isinstance(result["forecast_category"], str):
            raise RevOpsError("invalid_filter", "forecast_category must be a string")
        return result

    def _matches(self, deal: dict[str, Any], filters: dict[str, Any]) -> bool:
        status = filters.get("status")
        include_closed = filters["include_closed"]
        if status is not None:
            if deal["status"] != status:
                return False
        elif not include_closed and deal["status"] != "open":
            return False
        checks = {
            "owner_id": (deal["owner_id"], False),
            "pipeline_id": (deal["pipeline_id"], False),
            "stage_id": (deal["stage_id"], False),
            "segment": (deal["segment"], False),
        }
        for key, (actual, casefold) in checks.items():
            if key not in filters:
                continue
            expected = filters[key]
            if actual is None:
                return False
            actual_text = str(actual).casefold() if casefold else str(actual)
            expected_text = str(expected).casefold() if casefold else str(expected)
            if actual_text != expected_text:
                return False
        if "owner_name" in filters:
            if deal["owner_name"] is None or deal["owner_name"].casefold() != str(filters["owner_name"]).casefold():
                return False
        if "stage_name" in filters:
            if deal["stage_name"] is None or deal["stage_name"].casefold() != str(filters["stage_name"]).casefold():
                return False
        if "forecast_category" in filters:
            if deal["forecast_category"] != str(filters["forecast_category"]).casefold():
                return False
        return True

    def _period_matches(self, deal: dict[str, Any], period: dict[str, Any], warnings: list[str]) -> bool:
        if period["type"] == "none":
            return True
        if deal["close_at"] is None:
            if deal["close_raw"] is None:
                warnings.append(f"deal {deal['id']} has no closedate and was excluded from {period['label']}")
            return False
        start = parse_utc(period["start"], "period.start")
        end = parse_utc(period["end"], "period.end")
        return start <= deal["close_at"] < end

    def _currency_check(self, deals: list[dict[str, Any]]) -> None:
        reporting = str(self.company_config["reporting_currency"]).upper()
        assume_missing = bool(self.company_config.get("treat_missing_currency_as_reporting_currency", True))
        currencies: set[str] = set()
        missing = False
        for deal in deals:
            if deal["amount_raw"] is None:
                continue
            currency = deal["currency"]
            if currency is None:
                missing = True
            else:
                currencies.add(currency.upper())
        non_reporting = currencies - {reporting}
        if len(currencies) > 1 or (non_reporting and reporting in currencies):
            raise RevOpsError(
                "mixed_currency",
                f"Amounts have multiple currencies ({', '.join(sorted(currencies))}); no FX policy is configured",
                {"observed_currencies": sorted(currencies), "reporting_currency": reporting},
            )
        if non_reporting:
            raise RevOpsError(
                "unsupported_currency",
                f"Amounts are in {', '.join(sorted(non_reporting))}, not reporting currency {reporting}; no FX policy is configured",
                {"observed_currencies": sorted(non_reporting), "reporting_currency": reporting},
            )
        if missing and not assume_missing:
            raise RevOpsError(
                "missing_currency",
                "A non-null amount has no currency and missing currencies are not configured as reporting currency",
            )

    @staticmethod
    def _known_amount(deals: list[dict[str, Any]], weighted: bool = False) -> Decimal:
        total = Decimal("0")
        for deal in deals:
            amount = deal["amount"]
            if amount is None:
                continue
            if weighted:
                probability = deal["probability"]
                if probability is None:
                    continue
                total += amount * probability
            else:
                total += amount
        return total

    @staticmethod
    def _has_unknown_amount(deals: list[dict[str, Any]], weighted: bool = False) -> bool:
        for deal in deals:
            if deal["amount"] is None:
                return True
            if weighted and deal["probability"] is None:
                return True
        return False

    def _aggregate(self, deals: list[dict[str, Any]], weighted: bool = False) -> dict[str, Any]:
        self._currency_check(deals)
        known = self._known_amount(deals, weighted=weighted)
        unknown = self._has_unknown_amount(deals, weighted=weighted)
        return {
            "deal_count": len(deals),
            "known_deal_count": sum(deal["amount"] is not None for deal in deals),
            "amount": _decimal_string(known) if not unknown else None,
            "known_amount": _decimal_string(known),
            "weighted_amount": _decimal_string(self._known_amount(deals, weighted=True))
            if not self._has_unknown_amount(deals, weighted=True)
            else None,
            "known_weighted_amount": _decimal_string(self._known_amount(deals, weighted=True)),
            "source_record_ids": [deal["id"] for deal in deals],
        }

    def _metadata(
        self,
        metric: str,
        snapshot: dict,
        period: dict,
        filters: dict[str, Any],
        source_record_ids: list[str],
        warnings: list[str],
        as_of: Any = None,
    ) -> dict[str, Any]:
        as_of_value = iso_utc(parse_utc(as_of, "as_of")) if as_of is not None else snapshot["captured_at"]
        source_updated = snapshot.get("source_updated_at") or snapshot.get("_computed_source_updated_at")
        result_warnings = list(warnings)
        if source_updated is None:
            source_updated = None
            result_warnings.append("source_updated_at is unavailable")
        return {
            "as_of": as_of_value,
            "source_updated_at": source_updated,
            "metric": metric,
            "currency": str(self.company_config["reporting_currency"]).upper(),
            "period": _public_period(period),
            "filters": filters,
            "config_version": str(self.company_config["config_version"]),
            "source_record_ids": list(dict.fromkeys(source_record_ids)),
            "warnings": list(dict.fromkeys(result_warnings)),
        }

    @staticmethod
    def _missing_data_warnings(
        deals: list[dict[str, Any]], warnings: list[str], *, weighted: bool = False
    ) -> None:
        for deal in deals:
            if deal["amount_raw"] is None:
                warnings.append(
                    f"deal {deal['id']} amount is null; amount is null and known_amount excludes it"
                )
            if weighted and deal["probability_raw"] is None:
                warnings.append(f"deal {deal['id']} probability is null; weighted_amount is null")

    # Tool implementations -----------------------------------------------------------

    def _tool_company_context(self, arguments: dict[str, Any]) -> dict:
        topic = arguments.get("topic")
        if topic is not None and not isinstance(topic, str):
            raise RevOpsError("invalid_input", "topic must be a string")
        items = self.company_config.get("approved_context", [])
        if not isinstance(items, list):
            raise RevOpsError("invalid_config", "approved_context must be a list")
        selected: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            haystack = " ".join(
                str(item.get(key, "")) for key in ("topic", "title", "summary", "definition")
            ).casefold()
            if topic is None or topic.casefold() in haystack:
                selected.append(item)
        return {
            "ok": True,
            "topic": topic,
            "results": selected,
            "sources": [
                item["source"]
                for item in selected
                if isinstance(item, dict) and isinstance(item.get("source"), dict)
            ],
            "warnings": [] if selected or topic is None else ["No approved context matched the topic"],
        }

    def _tool_pipeline_summary(self, arguments: dict[str, Any]) -> dict:
        group_by = arguments.get("group_by", "stage")
        if group_by not in {"stage", "owner", "segment"}:
            raise RevOpsError("invalid_input", "group_by must be stage, owner, or segment")
        snapshot = self._select_snapshot(arguments.get("as_of"))
        period = parse_period(
            arguments.get("period"),
            self.company_config["timezone"],
            int(self.company_config.get("fiscal_year_start_month", 1)),
        )
        filters = self._filters(arguments.get("filters"), default_include_closed=False)
        warnings: list[str] = []
        views = self._deal_views(snapshot, warnings)
        selected = [
            deal
            for deal in views
            if self._matches(deal, filters) and self._period_matches(deal, period, warnings)
        ]
        self._missing_data_warnings(selected, warnings)
        groups: list[dict[str, Any]] = []
        keys = sorted({_summary_key(deal, group_by) for deal in selected})
        for key in keys:
            members = [deal for deal in selected if _summary_key(deal, group_by) == key]
            aggregate = self._aggregate(members)
            groups.append({"key": key, **_group_identity(members[0], group_by), **aggregate})
        total = self._aggregate(selected)
        metadata = self._metadata(
            f"pipeline_summary.by_{group_by}",
            snapshot,
            period,
            filters,
            [deal["id"] for deal in selected],
            warnings,
            arguments.get("as_of"),
        )
        return {"ok": True, **metadata, "group_by": group_by, "groups": groups, "total": total}

    def _tool_forecast(self, arguments: dict[str, Any]) -> dict:
        basis = arguments.get("basis")
        if basis not in {"category", "weighted", "submitted"}:
            raise RevOpsError("invalid_input", "basis must be category, weighted, or submitted")
        snapshot = self._select_snapshot(arguments.get("as_of"))
        period = parse_period(
            arguments.get("period"),
            self.company_config["timezone"],
            int(self.company_config.get("fiscal_year_start_month", 1)),
        )
        filters = self._filters(arguments.get("filters"), default_include_closed=False)
        warnings: list[str] = []
        views = self._deal_views(snapshot, warnings)
        selected = [
            deal
            for deal in views
            if self._matches(deal, filters) and self._period_matches(deal, period, warnings)
        ]
        # Forecast category and weighted views include closed-won actuals, but omit closed-lost
        # unless a caller explicitly asks for closed deals.
        forecast_candidates = []
        for deal in selected:
            if deal["status"] == "closed_lost" and not filters["include_closed"] and filters.get("status") is None:
                continue
            forecast_candidates.append(deal)
        self._missing_data_warnings(
            forecast_candidates
            if basis == "category"
            else [deal for deal in forecast_candidates if deal["status"] == "open"],
            warnings,
            weighted=basis == "weighted",
        )
        metadata = self._metadata(
            f"forecast.{basis}",
            snapshot,
            period,
            filters,
            [deal["id"] for deal in forecast_candidates],
            warnings,
            arguments.get("as_of"),
        )
        if basis == "submitted":
            submitted = _find_submitted_forecast(snapshot, period, filters)
            if submitted is None:
                raise RevOpsError(
                    "submitted_forecast_unavailable",
                    "No submitted forecast is stored for this exact period and filter set",
                    {"period": period["label"], "basis_requested": "submitted"},
                )
            return {
                "ok": True,
                **metadata,
                "basis": "submitted",
                "submitted_forecast": submitted,
                "total": {
                    "amount": _decimal_string(_parse_decimal(submitted.get("amount"))[0]),
                    "deal_count": submitted.get("deal_count"),
                    "source": submitted.get("source"),
                },
            }
        if basis == "weighted":
            weighted_deals = [deal for deal in forecast_candidates if deal["status"] == "open"]
            categories: list[dict[str, Any]] = []
            for category in sorted({deal["forecast_category"] for deal in weighted_deals}):
                members = [deal for deal in weighted_deals if deal["forecast_category"] == category]
                categories.append({"key": category, **self._aggregate(members, weighted=True)})
            total = self._aggregate(weighted_deals, weighted=True)
            return {"ok": True, **metadata, "basis": "weighted", "categories": categories, "total": total}
        categories = []
        for category in sorted({deal["forecast_category"] for deal in forecast_candidates}):
            members = [deal for deal in forecast_candidates if deal["forecast_category"] == category]
            categories.append({"key": category, **self._aggregate(members, weighted=False)})
        total = self._aggregate(forecast_candidates, weighted=False)
        return {"ok": True, **metadata, "basis": "category", "categories": categories, "total": total}

    def _tool_list_deals(self, arguments: dict[str, Any]) -> dict:
        sort_by = arguments.get("sort_by", "amount")
        if sort_by not in {"amount", "close_date"}:
            raise RevOpsError("invalid_input", "sort_by must be amount or close_date")
        order = arguments.get("order")
        if order is not None and order not in {"desc", "asc"}:
            raise RevOpsError("invalid_input", "order must be desc or asc")
        if order is None:
            order = "desc" if sort_by == "amount" else "asc"
        limit = arguments.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise RevOpsError("invalid_input", "limit must be an integer between 1 and 10")
        snapshot = self._select_snapshot(arguments.get("as_of"))
        period = parse_period(
            arguments.get("period"),
            self.company_config["timezone"],
            int(self.company_config.get("fiscal_year_start_month", 1)),
        )
        filters = self._filters(arguments.get("filters"), default_include_closed=False)
        warnings: list[str] = []
        views = self._deal_views(snapshot, warnings)
        selected = [
            deal
            for deal in views
            if self._matches(deal, filters) and self._period_matches(deal, period, warnings)
        ]
        for deal in selected:
            if deal["amount_raw"] is None:
                warnings.append(f"deal {deal['id']} amount is null; it sorts last for amount sorting")

        # List deals even when currencies differ, but never present the result as a single
        # comparable ranking without warning (same policy as _currency_check, non-raising).
        reporting = str(self.company_config["reporting_currency"]).upper()
        assume_missing = bool(self.company_config.get("treat_missing_currency_as_reporting_currency", True))
        currencies: set[str] = set()
        missing_currency = False
        for deal in selected:
            if deal["amount_raw"] is None:
                continue
            if deal["currency"] is None:
                missing_currency = True
            else:
                currencies.add(deal["currency"].upper())
        non_reporting = currencies - {reporting}
        if len(currencies) > 1 or (non_reporting and reporting in currencies):
            warnings.append(
                "Amounts have multiple currencies ("
                + ", ".join(sorted(currencies))
                + "); no FX policy is configured, so amounts are not directly comparable"
            )
        elif non_reporting:
            warnings.append(
                f"Amounts are in {', '.join(sorted(non_reporting))}, not reporting currency {reporting}; "
                "no FX policy is configured"
            )
        if missing_currency and not assume_missing:
            warnings.append("A non-null amount has no currency and missing currencies are not configured as reporting currency")

        reverse = order == "desc"
        if sort_by == "amount":
            known = [deal for deal in selected if deal["amount"] is not None]
            unknown = [deal for deal in selected if deal["amount"] is None]
            sorted_deals = sorted(known, key=lambda deal: deal["amount"], reverse=reverse) + unknown
        else:
            dated = [deal for deal in selected if deal["close_at"] is not None]
            undated = [deal for deal in selected if deal["close_at"] is None]
            sorted_deals = sorted(dated, key=lambda deal: deal["close_at"], reverse=reverse) + undated
        limited = sorted_deals[:limit]

        def deal_entry(deal: dict[str, Any]) -> dict[str, Any]:
            parts = str(deal["name"]).split(" - ") if deal["name"] is not None else []
            return {
                "id": deal["id"],
                "name": deal["name"],
                "company": parts[-2] if len(parts) >= 2 else None,
                "deal": parts[-1] if len(parts) >= 2 else None,
                "stage_name": deal["stage_name"],
                "status": deal["status"],
                "amount": _decimal_string(deal["amount"]),
                "currency": deal["currency"],
                "close_date": deal["close_raw"],
                "owner_name": deal["owner_name"],
                "forecast_category": deal["forecast_category"],
            }

        try:
            total = self._aggregate(selected)
        except RevOpsError:
            total = None  # mixed or missing currencies: never speak a single combined total

        metadata = self._metadata(
            "list_deals",
            snapshot,
            period,
            filters,
            [deal["id"] for deal in limited],
            warnings,
            arguments.get("as_of"),
        )
        return {
            "ok": True,
            **metadata,
            "sort_by": sort_by,
            "order": order,
            "limit": limit,
            "total_matching": len(selected),
            # Totals over every matching deal (not just the listed ones), so spoken sums come from code.
            "total": total,
            "deals": [deal_entry(deal) for deal in limited],
        }

    def _tool_deal(self, arguments: dict[str, Any]) -> dict:
        query = arguments.get("query_or_id")
        if not isinstance(query, str) or not query:
            raise RevOpsError("invalid_input", "query_or_id must be a non-empty string")
        snapshot = self._select_snapshot(arguments.get("as_of"))
        warnings: list[str] = []
        views = self._deal_views(snapshot, warnings)
        exact_id = [deal for deal in views if deal["id"] == query]
        if exact_id:
            matches = exact_id
        else:
            matches = [deal for deal in views if isinstance(deal["name"], str) and deal["name"].casefold() == query.casefold()]
        resolution = {"type": "exact"} if matches else None
        suggestions: list[dict[str, Any]] = []
        if not matches:
            query_norm = _normalize_name(query)
            scored = sorted(
                (
                    (_name_score(query_norm, _normalize_name(deal["name"])), deal)
                    for deal in views
                    if isinstance(deal["name"], str) and _normalize_name(deal["name"])
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            top_score = scored[0][0] if scored else 0.0
            runner_up = scored[1][0] if len(scored) > 1 else 0.0
            # A clearly separated best match is accepted below 0.80 because the
            # resolved name is always spoken back; ties stay ambiguous.
            if query_norm and (top_score >= 0.80 or (top_score >= 0.70 and top_score - runner_up >= 0.15)):
                tied = [deal for score, deal in scored if top_score - score <= 0.02]
                if len(tied) > 1:
                    self._missing_data_warnings(tied, warnings)
                    metadata = self._metadata(
                        "deal_lookup",
                        snapshot,
                        parse_period(None, self.company_config["timezone"], int(self.company_config.get("fiscal_year_start_month", 1))),
                        {},
                        [deal["id"] for deal in tied],
                        warnings,
                        arguments.get("as_of"),
                    )
                    return {
                        "ok": False,
                        **metadata,
                        "error": {
                            "code": "ambiguous_deal",
                            "message": f"{len(tied)} deals plausibly match {query!r}; ask which one",
                            "candidates": [
                                {**_deal_candidate(deal), "score": round(score, 2)}
                                for score, deal in scored
                                if top_score - score <= 0.02
                            ][:5],
                        },
                    }
                matches = [tied[0]]
                resolution = {
                    # A whole-word substring ("Corvus Data") is a partial name, not a mishearing.
                    "type": "partial" if top_score >= 0.95 else "fuzzy",
                    "query": query,
                    "matched_name": matches[0]["name"],
                    "score": round(top_score, 2),
                }
            else:
                suggestions = [
                    {**_deal_candidate(deal), "score": round(score, 2)}
                    for score, deal in scored
                    if score >= 0.55
                ][:3]
        self._missing_data_warnings(matches, warnings)
        metadata = self._metadata(
            "deal_lookup",
            snapshot,
            parse_period(None, self.company_config["timezone"], int(self.company_config.get("fiscal_year_start_month", 1))),
            {},
            [deal["id"] for deal in matches],
            warnings,
            arguments.get("as_of"),
        )
        if len(matches) != 1:
            return {
                "ok": False,
                **metadata,
                "error": {
                    "code": "ambiguous_deal" if matches else "deal_not_found",
                    "message": (
                        f"{len(matches)} deals have the exact name {query!r}; ask for the HubSpot ID"
                        if matches
                        else f"No exact deal ID or name matched {query!r}"
                    ),
                    "candidates": [_deal_candidate(deal) for deal in matches],
                    **({"suggestions": suggestions} if suggestions else {}),
                },
            }
        deal = matches[0]
        history = []
        for historical in self._complete_snapshots():
            historical_deal = next(
                (item for item in historical.get("deals", []) if str(item.get("id", "")) == deal["id"]),
                None,
            )
            if historical_deal is None:
                continue
            props = historical_deal.get("properties", {}) if isinstance(historical_deal, dict) else {}
            history.append(
                {
                    "snapshot_id": historical.get("snapshot_id"),
                    "captured_at": historical.get("captured_at"),
                    "observed_fields": {
                        "dealname": props.get("dealname"),
                        "amount": props.get(self.company_config.get("amount_field", "amount")),
                        "dealstage": props.get("dealstage"),
                        "closedate": props.get("closedate"),
                        "hubspot_owner_id": props.get("hubspot_owner_id"),
                    },
                }
            )
        known = self._known_amount([deal])
        return {
            "ok": True,
            **metadata,
            "deal": {
                "id": deal["id"],
                "name": deal["name"],
                "stage_id": deal["stage_id"],
                "stage_name": deal["stage_name"],
                "status": deal["status"],
                "forecast_category": deal["forecast_category"],
                "owner_id": deal["owner_id"],
                "owner_name": deal["owner_name"],
                "segment": deal["segment"],
                "currency": deal["currency"],
                "amount": _decimal_string(deal["amount"]),
                "known_amount": _decimal_string(known),
                "probability": _decimal_string(deal["probability"]),
                "close_date": deal["close_raw"],
                "properties": deal["properties"],
                "source": deal["source"],
                "angels_notes": next((d.get("angels_notes", []) for d in snapshot["deals"] if str(d["id"]) == deal["id"]), []),
            },
            **({"resolution": resolution} if resolution else {}),
            "available_history": history,
            "history_scope": "only observed values from stored complete snapshots",
        }

    def _tool_compare(self, arguments: dict[str, Any]) -> dict:
        from_time = parse_utc(arguments.get("from_time"), "from_time")
        to_time = parse_utc(arguments.get("to_time"), "to_time")
        if from_time >= to_time:
            raise RevOpsError("invalid_input", "from_time must be before to_time")
        snapshots = self._complete_snapshots()
        from_candidates = [s for s in snapshots if parse_utc(s["captured_at"], "captured_at") <= from_time]
        to_candidates = [s for s in snapshots if parse_utc(s["captured_at"], "captured_at") <= to_time]
        if not from_candidates or not to_candidates:
            raise RevOpsError("snapshot_not_available", "Both requested comparison times need a prior complete snapshot")
        before_snapshot = from_candidates[-1]
        after_snapshot = to_candidates[-1]
        filters = self._filters(arguments.get("filters"), default_include_closed=True)
        warnings: list[str] = []
        before_all = self._deal_views(before_snapshot, warnings)
        after_all = self._deal_views(after_snapshot, warnings)
        before = [deal for deal in before_all if self._matches(deal, filters)]
        after = [deal for deal in after_all if self._matches(deal, filters)]
        self._missing_data_warnings(before, warnings)
        self._missing_data_warnings(after, warnings)
        before_map = {deal["id"]: deal for deal in before}
        after_map = {deal["id"]: deal for deal in after}
        before_total = self._aggregate(before)
        after_total = self._aggregate(after)
        before_known = self._known_amount(before)
        after_known = self._known_amount(after)
        delta_known = after_known - before_known
        new_ids = sorted(after_map.keys() - before_map.keys())
        removed_ids = sorted(before_map.keys() - after_map.keys())
        won_ids: list[str] = []
        lost_ids: list[str] = []
        slipped_ids: list[str] = []
        changes: list[dict[str, Any]] = []
        for deal_id in sorted(before_map.keys() & after_map.keys()):
            old, new = before_map[deal_id], after_map[deal_id]
            if new["status"] == "closed_won" and old["status"] != "closed_won":
                won_ids.append(deal_id)
            if new["status"] == "closed_lost" and old["status"] != "closed_lost":
                lost_ids.append(deal_id)
            if old["close_at"] is not None and new["close_at"] is not None and new["close_at"] > old["close_at"]:
                slipped_ids.append(deal_id)
            changed = _observed_changes(old, new)
            if changed:
                changes.append({"deal_id": deal_id, "changes": changed})
        period = parse_period(None, self.company_config["timezone"], int(self.company_config.get("fiscal_year_start_month", 1)))
        metadata = self._metadata(
            "snapshot_comparison",
            after_snapshot,
            period,
            filters,
            sorted(before_map.keys() | after_map.keys()),
            warnings,
            arguments.get("to_time"),
        )
        return {
            "ok": True,
            **metadata,
            "from_snapshot": _snapshot_reference(before_snapshot, before_total),
            "to_snapshot": _snapshot_reference(after_snapshot, after_total),
            "delta": {
                "deal_count": len(after) - len(before),
                "known_amount": _decimal_string(delta_known),
                "amount": None if before_total["amount"] is None or after_total["amount"] is None else _decimal_string(delta_known),
                "new_deal_count": len(new_ids),
                "removed_deal_count": len(removed_ids),
            },
            "new_deal_ids": new_ids,
            "removed_deal_ids": removed_ids,
            "won_deal_ids": won_ids,
            "lost_deal_ids": lost_ids,
            "slipped_deal_ids": slipped_ids,
            "deal_changes": changes,
            "history_scope": "only observed changes between the two selected complete snapshots",
        }

    def _tool_simulate_slip(self, arguments: dict[str, Any]) -> dict:
        deal_ids = arguments.get("deal_ids")
        if not isinstance(deal_ids, list) or not deal_ids or not all(isinstance(item, str) for item in deal_ids):
            raise RevOpsError("invalid_input", "deal_ids must be a non-empty array of strings")
        baseline_id = arguments.get("baseline")
        snapshot = self._snapshot_by_id(baseline_id) if baseline_id is not None else self._select_snapshot()
        target = parse_period(
            arguments.get("target_period"),
            self.company_config["timezone"],
            int(self.company_config.get("fiscal_year_start_month", 1)),
        )
        warnings: list[str] = []
        views = self._deal_views(snapshot, warnings)
        by_id = {deal["id"]: deal for deal in views}
        missing = [deal_id for deal_id in deal_ids if deal_id not in by_id]
        if missing:
            raise RevOpsError("deal_not_found", "One or more deal IDs are absent from the baseline", {"deal_ids": missing})
        selected = [by_id[deal_id] for deal_id in deal_ids]
        closed = [deal["id"] for deal in selected if deal["status"] != "open"]
        if closed:
            raise RevOpsError("scenario_invalid", "Slip simulation applies only to open deals", {"deal_ids": closed})
        self._missing_data_warnings(selected, warnings, weighted=True)
        target_end = parse_utc(target["end"], "target_period.end")
        backwards = [deal["id"] for deal in selected if deal["close_at"] is not None and target_end <= deal["close_at"]]
        if backwards:
            raise RevOpsError("scenario_invalid", "Target period would move a deal earlier", {"deal_ids": backwards})
        before = self._aggregate(selected)
        after_views: list[dict[str, Any]] = []
        for deal in selected:
            modified = dict(deal)
            scenario_close = parse_utc(target["end"], "target_period.end") - timedelta(microseconds=1)
            modified["close_at"] = scenario_close
            modified["close_raw"] = iso_utc(scenario_close)
            after_views.append(modified)
        after = self._aggregate(after_views)
        metadata = self._metadata(
            "simulate_slip",
            snapshot,
            target,
            {"deal_ids": list(deal_ids)},
            [deal["id"] for deal in selected],
            warnings,
        )
        return {
            "ok": True,
            **metadata,
            "scenario": {
                "hypothetical": True,
                "mutated_records": [],
                "mutation_target": "none",
                "baseline_snapshot_id": snapshot["snapshot_id"],
                "target_period": _public_period(target),
            },
            "before": before,
            "after": after,
            "deals": [
                {
                    "id": deal["id"],
                    "name": deal["name"],
                    "close_date_before": deal["close_raw"],
                    "close_date_after": iso_utc(scenario_deal["close_at"]),
                }
                for deal, scenario_deal in zip(selected, after_views, strict=True)
            ],
        }

    def _tool_commitments(self, arguments: dict[str, Any]) -> dict:
        series = arguments.get("meeting_series")
        deal_id = arguments.get("deal_id")
        if series is not None and not isinstance(series, str):
            raise RevOpsError("invalid_input", "meeting_series must be a string")
        if deal_id is not None and not isinstance(deal_id, str):
            raise RevOpsError("invalid_input", "deal_id must be a string")
        snapshot = self._select_snapshot()
        commitments = snapshot.get("commitments", [])
        if not isinstance(commitments, list):
            raise RevOpsError("invalid_config", "snapshot commitments must be a list")
        matches = [
            item
            for item in commitments
            if isinstance(item, dict)
            and (series is None or str(item.get("meeting_series", "")).casefold() == series.casefold())
            and (deal_id is None or str(item.get("deal_id", "")) == deal_id)
        ]
        period = parse_period(None, self.company_config["timezone"], int(self.company_config.get("fiscal_year_start_month", 1)))
        metadata = self._metadata(
            "previous_commitments",
            snapshot,
            period,
            {"meeting_series": series, "deal_id": deal_id},
            [str(item.get("id", "")) for item in matches if item.get("id")],
            [],
        )
        return {"ok": True, **metadata, "commitments": matches, "commitment_count": len(matches)}


def _period_schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "properties": {
            "type": {
                "type": "string",
                "enum": ["calendar_month", "calendar_quarter", "calendar_year", "fiscal_quarter", "date_range"],
            },
            "year": {"type": "integer"},
            "month": {"type": "integer", "minimum": 1, "maximum": 12},
            "quarter": {"type": "integer", "minimum": 1, "maximum": 4},
            "fiscal_year": {"type": "integer"},
            "fiscal_quarter": {"type": "integer", "minimum": 1, "maximum": 4},
            "start": {"type": "string", "description": "Inclusive timezone-aware UTC ISO timestamp."},
            "end": {"type": "string", "description": "Exclusive timezone-aware UTC ISO timestamp."},
        },
        "required": ["type"],
        "additionalProperties": False,
    }


def _filters_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Exact filters. Unknown keys are rejected rather than ignored.",
        "properties": {
            "owner_id": {"type": "string"},
            "owner_name": {"type": "string"},
            "pipeline_id": {"type": "string"},
            "stage_id": {"type": "string"},
            "stage_name": {"type": "string"},
            "segment": {"type": "string"},
            "forecast_category": {"type": "string", "enum": ["pipeline", "best_case", "commit", "closed_won"]},
            "status": {"type": "string", "enum": ["open", "closed_won", "closed_lost"]},
            "include_closed": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    }


def _as_of_schema() -> dict[str, Any]:
    return {
        "type": "string",
        "description": "Optional timezone-aware UTC ISO timestamp; the latest complete snapshot at or before it is used.",
    }


def _error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message, **(details or {})}}


def _parse_decimal(value: Any) -> tuple[Decimal | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, "boolean is not a valid decimal amount"
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, f"invalid decimal value {value!r}; it remains null and is not treated as zero"
    if not result.is_finite():
        return None, "non-finite decimal values are not supported"
    return result, None


def _parse_hubspot_flag(value: Any, default: Any) -> Any:
    """Parse HubSpot string metadata flags without truthiness coercion."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return default


def _parse_stage_probability(value: Any) -> tuple[Decimal | None, bool]:
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, False
    try:
        probability = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None, False
    if not probability.is_finite():
        return None, False
    return probability, True


def _derive_stage_status(
    is_closed: bool, is_won: bool | None, probability: Decimal | None, probability_known: bool
) -> str:
    if not is_closed:
        return "open"
    if is_won is True:
        return "closed_won"
    if is_won is False:
        return "closed_lost"
    if not probability_known:
        return "unknown"
    if probability == 1:
        return "closed_won"
    if probability == 0:
        return "closed_lost"
    return "unknown"


def _public_period(period: dict[str, Any]) -> dict[str, Any]:
    return {key: period.get(key) for key in ("type", "label", "start", "end", "timezone")}


def _summary_key(deal: dict[str, Any], group_by: str) -> str:
    if group_by == "owner":
        return deal.get("owner_name") or deal.get("owner_id") or "unassigned"
    if group_by == "segment":
        return str(deal.get("segment") or "unsegmented")
    return str(deal.get("stage_name") or deal.get("stage_id") or "unknown")


def _group_identity(deal: dict[str, Any], group_by: str) -> dict[str, Any]:
    if group_by == "owner":
        return {"owner_id": deal.get("owner_id"), "owner_name": deal.get("owner_name")}
    if group_by == "segment":
        return {"segment": deal.get("segment")}
    return {"pipeline_id": deal.get("pipeline_id"), "stage_id": deal.get("stage_id"), "stage_name": deal.get("stage_name")}


def _deal_candidate(deal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": deal["id"],
        "name": deal["name"],
        "status": deal["status"],
        "stage_name": deal["stage_name"],
        "owner_name": deal["owner_name"],
        "close_date": deal["close_raw"],
        "amount": _decimal_string(deal["amount"]),
        "currency": deal["currency"],
    }


_FILLER_TOKENS = {"angels", "demo", "the", "deal", "deals", "inc", "ltd", "llc", "sl", "srl", "gmbh"}


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^0-9a-z]+", " ", text.casefold())
    return " ".join(token for token in text.split() if token not in _FILLER_TOKENS)


def _name_score(query_norm: str, name_norm: str) -> float:
    if not query_norm or not name_norm:
        return 0.0
    if query_norm == name_norm:
        return 1.0
    if len(query_norm) >= 3 and re.search(rf"(?:^| ){re.escape(query_norm)}(?: |$)", name_norm):
        return 0.95
    full_ratio = difflib.SequenceMatcher(None, query_norm, name_norm).ratio()
    query_compact = query_norm.replace(" ", "")
    tokens = name_norm.split()
    best_window = 0.0
    for size in range(1, min(4, len(tokens)) + 1):
        for start in range(len(tokens) - size + 1):
            window = "".join(tokens[start : start + size])
            best_window = max(best_window, difflib.SequenceMatcher(None, query_compact, window).ratio())
    return max(full_ratio, best_window)


def _observed_changes(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    fields = ("name", "amount_raw", "stage_id", "status", "forecast_category", "owner_id", "segment", "close_raw", "currency")
    return {field: {"from": old.get(field), "to": new.get(field)} for field in fields if old.get(field) != new.get(field)}


def _snapshot_reference(snapshot: dict, aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "captured_at": snapshot.get("captured_at"),
        "source": snapshot.get("source"),
        "total": {key: value for key, value in aggregate.items() if key != "source_record_ids"},
        "source_record_ids": aggregate["source_record_ids"],
    }


def _find_submitted_forecast(
    snapshot: dict, period: dict[str, Any], filters: dict[str, Any]
) -> dict[str, Any] | None:
    for forecast in snapshot.get("submitted_forecasts", []):
        if not isinstance(forecast, dict):
            continue
        if str(forecast.get("period", "")) != period["label"]:
            continue
        stored_filters = forecast.get("filters", {})
        comparable = {key: value for key, value in filters.items() if key != "include_closed"}
        if stored_filters != comparable:
            continue
        return forecast
    return None


__all__ = ["RevOpsService", "RevOpsError"]
