"""Read-only, paginated HubSpot CRM ingestion into immutable snapshots."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from .repository import Repository


HUBSPOT_API_BASE_URL = "https://api.hubapi.com"
DEAL_PROPERTY_DEFAULTS = [
    "dealname",
    "amount",
    "dealcurrency",
    "pipeline",
    "dealstage",
    "hubspot_owner_id",
    "closedate",
    "hs_lastmodifieddate",
    "createdate",
    "dealtype",
    "description",
]


class HubSpotSyncError(RuntimeError):
    """Raised before any partial result is promoted to the repository."""


class SyncResponse(Protocol):
    status_code: int
    headers: Any

    def json(self) -> Any:
        ...


class _UrllibResponse:
    def __init__(self, status_code: int, headers: Any, body: bytes) -> None:
        self.status_code = status_code
        self.headers = headers
        self._body = body

    def json(self) -> Any:
        return json.loads(self._body.decode("utf-8"))


class _StdlibHttpClient:
    """Minimal default GET transport; tests can inject httpx or a fake client."""

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any],
        timeout: float,
    ) -> _UrllibResponse:
        query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
        request = urllib.request.Request(url + ("?" + query if query else ""), headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return _UrllibResponse(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            return _UrllibResponse(exc.code, exc.headers, exc.read())
        except (urllib.error.URLError, TimeoutError) as exc:
            raise HubSpotSyncError(f"Transient HubSpot transport error: {type(exc).__name__}") from exc


class HubSpotSync:
    """Fetch complete HubSpot pages first, then atomically save one snapshot.

    An injected client only needs ``get(url, headers=..., params=..., timeout=...)``
    returning an object with ``status_code``, ``headers``, and ``json()``. This keeps
    production on the documented REST API while making offline tests deterministic.
    """

    def __init__(
        self,
        access_token: str,
        repository: Repository,
        company_config: dict,
        *,
        http_client: Any | None = None,
        api_base_url: str = HUBSPOT_API_BASE_URL,
        timeout: float = 15.0,
        page_size: int = 100,
        max_retries: int = 3,
        initial_retry_delay: float = 0.25,
        max_records: int = 100_000,
        max_pages_per_collection: int = 10_000,
        sleep: Any = time.sleep,
    ) -> None:
        if not isinstance(access_token, str) or not access_token:
            raise ValueError("access_token must be a non-empty server-side token")
        if not isinstance(repository, Repository):
            raise TypeError("repository must be an revops_buddy.repository.Repository")
        if not isinstance(company_config, dict) or not company_config.get("company_id"):
            raise ValueError("company_config.company_id is required")
        if max_retries < 1 or max_retries > 8:
            raise ValueError("max_retries must be between 1 and 8")
        if not 1 <= page_size <= 100:
            raise ValueError("page_size must be between 1 and 100")
        self.access_token = access_token
        self.repository = repository
        self.company_config = company_config
        self.http_client = http_client or _StdlibHttpClient()
        self.api_base_url = api_base_url.rstrip("/")
        self.timeout = timeout
        self.page_size = page_size
        self.max_retries = max_retries
        self.initial_retry_delay = initial_retry_delay
        self.max_records = max_records
        self.max_pages_per_collection = max_pages_per_collection
        self.sleep = sleep

    def sync(self) -> dict:
        """Run a bounded, read-only sync; all pages must succeed before promotion."""
        started_at = _utc_now()
        warnings: list[str] = []
        properties = _configured_properties(self.company_config)
        owners, owner_pages = self._collect(
            "/crm/v3/owners", {"limit": min(self.page_size, 100), "archived": "false"}
        )
        pipelines, pipeline_pages = self._collect(
            "/crm/v3/pipelines/deals", {"limit": min(self.page_size, 100), "archived": "false"}
        )
        deals, deal_pages = self._collect(
            "/crm/v3/objects/deals",
            {
                "limit": min(self.page_size, 100),
                "archived": "false",
                "properties": ",".join(properties),
            },
        )
        normalized_deals = [self._normalize_deal(item, warnings) for item in deals]
        source_updated_values = [
            datetime.fromisoformat(deal["properties"]["hs_lastmodifieddate"].replace("Z", "+00:00"))
            for deal in normalized_deals
            if deal["properties"].get("hs_lastmodifieddate")
        ]
        source_updated_at = (
            max(source_updated_values).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if source_updated_values
            else None
        )
        if source_updated_at is None:
            warnings.append("No HubSpot deal last-modified timestamp was available")
        snapshot_id = f"hubspot-{self.company_config['company_id']}-{uuid.uuid4()}"
        run_id = f"hubspot-run-{uuid.uuid4()}"
        snapshot = {
            "schema_version": "1",
            "snapshot_id": snapshot_id,
            "company_id": self.company_config["company_id"],
            "captured_at": _utc_now(),
            "source": "hubspot_api",
            "source_updated_at": source_updated_at,
            "complete": True,
            "status": "complete",
            "config_version": str(self.company_config.get("config_version", "unversioned")),
            "sync": {
                "run_id": run_id,
                "started_at": started_at,
                "completed_at": _utc_now(),
                "status": "complete",
                "read_only": True,
                "http_methods": ["GET"],
                "api_base_url": self.api_base_url,
                "collections": {
                    "owners": {"record_count": len(owners), "page_count": owner_pages},
                    "pipelines": {"record_count": len(pipelines), "page_count": pipeline_pages},
                    "deals": {"record_count": len(deals), "page_count": deal_pages},
                },
            },
            "owners": owners,
            "pipelines": pipelines,
            "deals": normalized_deals,
            "warnings": warnings,
        }
        self.repository.save_snapshot(snapshot)
        return {
            "ok": True,
            "snapshot_id": snapshot_id,
            "run_id": run_id,
            "status": "complete",
            "read_only": True,
            "captured_at": snapshot["captured_at"],
            "source_updated_at": source_updated_at,
            "deal_count": len(normalized_deals),
            "owner_count": len(owners),
            "pipeline_count": len(pipelines),
            "page_counts": {"owners": owner_pages, "pipelines": pipeline_pages, "deals": deal_pages},
            "source_record_ids": [deal["id"] for deal in normalized_deals],
            "warnings": warnings,
        }

    def _collect(self, path: str, base_params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        results: list[dict[str, Any]] = []
        after: str | None = None
        seen_after: set[str] = set()
        page_count = 0
        while True:
            params = dict(base_params)
            if after is not None:
                params["after"] = after
            response = self._request_json(path, params)
            page = response.get("results", [])
            if not isinstance(page, list):
                raise HubSpotSyncError(f"HubSpot response for {path} has an invalid results array")
            results.extend(item for item in page if isinstance(item, dict))
            page_count += 1
            if len(results) > self.max_records:
                raise HubSpotSyncError(
                    f"HubSpot collection exceeded configured max_records={self.max_records}"
                )
            paging = response.get("paging")
            if not isinstance(paging, dict):
                paging = {}
            next_page = paging.get("next") if isinstance(paging.get("next", {}), dict) else None
            next_after = next_page.get("after") if isinstance(next_page, dict) else None
            if next_after is None:
                return results, page_count
            next_after = str(next_after)
            if next_after in seen_after:
                raise HubSpotSyncError(f"HubSpot pagination cursor repeated for {path}")
            seen_after.add(next_after)
            after = next_after
            if page_count >= self.max_pages_per_collection:
                raise HubSpotSyncError(
                    f"HubSpot collection exceeded configured max_pages={self.max_pages_per_collection}"
                )

    def _request_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        last_status: int | None = None
        last_error_body: str | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.http_client.get(
                    self.api_base_url + path,
                    headers=headers,
                    params=params,
                    timeout=self.timeout,
                )
            except HubSpotSyncError:
                if attempt + 1 == self.max_retries:
                    raise
                self._sleep_backoff(attempt, None)
                continue
            status = int(getattr(response, "status_code", 0))
            if 200 <= status < 300:
                try:
                    parsed = response.json()
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    raise HubSpotSyncError(f"HubSpot returned invalid JSON for {path}") from exc
                if not isinstance(parsed, dict):
                    raise HubSpotSyncError(f"HubSpot returned a non-object response for {path}")
                return parsed
            last_status = status
            last_error_body = _response_text(response)
            retryable = status == 429 or status >= 500
            if not retryable:
                break
            retry_after = _header(response.headers, "Retry-After")
            if attempt + 1 < self.max_retries:
                self._sleep_backoff(attempt, retry_after)
        message = _safe_error_message(last_error_body)
        raise HubSpotSyncError(
            f"HubSpot GET {path} failed with status {last_status}: {message}"
        )

    def _sleep_backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = self.initial_retry_delay * (2**attempt)
        if retry_after is not None:
            delay = max(delay, _retry_after_seconds(retry_after))
        self.sleep(delay)

    def _normalize_deal(self, item: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        if "id" not in item:
            raise HubSpotSyncError("HubSpot deal response omitted its ID")
        properties = dict(item.get("properties", {}))
        for field in ("createdate", "closedate", "hs_lastmodifieddate"):
            if properties.get(field) is not None:
                properties[field] = _normalize_timestamp(properties[field])
        if properties.get("hs_lastmodifieddate") is None and item.get("updatedAt") is not None:
            properties["hs_lastmodifieddate"] = _normalize_timestamp(item["updatedAt"])
        if properties.get("createdate") is None and item.get("createdAt") is not None:
            properties["createdate"] = _normalize_timestamp(item["createdAt"])
        custom = self.company_config.get("custom_deal_properties", [])
        for field in custom if isinstance(custom, list) else []:
            if field not in properties:
                warnings.append(f"HubSpot omitted configured property {field} for deal {item['id']}")
        return {
            "id": str(item["id"]),
            "properties": properties,
            "archived": bool(item.get("archived", False)),
            "source": "hubspot_api",
            "api_record": item,
        }


def _configured_properties(company_config: dict[str, Any]) -> list[str]:
    result = list(DEAL_PROPERTY_DEFAULTS)
    mappings = [
        "amount_field",
        "currency_field",
        "forecast_category_field",
        "segment_field",
        "probability_field",
    ]
    for mapping in mappings:
        field = company_config.get(mapping)
        if isinstance(field, str) and field not in result:
            result.append(field)
    custom = company_config.get("custom_deal_properties", [])
    if not isinstance(custom, list):
        raise HubSpotSyncError("custom_deal_properties must be a list")
    for field in custom:
        if not isinstance(field, str):
            raise HubSpotSyncError("custom_deal_properties entries must be strings")
        if field not in result:
            result.append(field)
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _normalize_timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value >= 100_000_000_000 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value)
    if text.isdigit() and len(text) >= 12:
        seconds = int(text) / 1000
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise HubSpotSyncError(f"HubSpot timestamp lacks an offset: {value}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        value = getter(name)
        return str(value) if value is not None else None
    return None


def _retry_after_seconds(value: str) -> float:
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0


def _response_text(response: Any) -> str:
    try:
        data = response.json()
    except Exception:
        return ""
    try:
        return json.dumps(data, separators=(",", ":"))
    except TypeError:
        return ""


def _safe_error_message(body: str | None) -> str:
    if not body:
        return "no response body"
    # Keep API error payloads bounded and avoid echoing any request credential.
    return body[:1000]


__all__ = ["HubSpotSync", "HubSpotSyncError"]
