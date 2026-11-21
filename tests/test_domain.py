import json
from decimal import Decimal
from pathlib import Path

import pytest

from revops_buddy.domain.service import RevOpsService
from revops_buddy.repository import Repository


FIXTURES = Path(__file__).resolve().parents[1] / "revops_buddy" / "fixtures"


@pytest.fixture()
def service(tmp_path):
    repository = Repository(f"sqlite:///{tmp_path}/domain.db")
    repository.initialize()
    with (FIXTURES / "company.json").open() as handle:
        config = json.load(handle)["company_config"]
    result = RevOpsService(repository, config)
    result.load_demo()
    return result


def test_declares_exactly_the_shared_tools(service):
    definitions = service.tool_definitions()
    assert [item["name"] for item in definitions] == [
        "get_company_context",
        "get_pipeline_summary",
        "get_forecast",
        "get_deal",
        "compare_snapshots",
        "simulate_slip",
        "get_previous_commitments",
        "list_deals",
    ]
    for definition in definitions:
        assert definition["parameters"]["type"] == "object"
        assert definition["parameters"]["additionalProperties"] is False


def test_pipeline_summary_decimal_weighting_and_required_metadata(service):
    result = service.call_tool(
        "get_pipeline_summary",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "filters": {"owner_id": "o-alice"},
            "group_by": "stage",
        },
    )
    assert result["ok"] is True
    # Independent reference: 120,000 + 250,000. Latest snapshot D-1009 closes
    # Nov. 3 and is therefore outside October.
    assert Decimal(result["total"]["amount"]) == Decimal("370000")
    # Independent reference: 120,000*.70 + 250,000*.80.
    assert Decimal(result["total"]["weighted_amount"]) == Decimal("284000.00")
    assert result["total"]["deal_count"] == 2
    assert result["source_record_ids"] == ["D-1001", "D-1003"]
    assert result["as_of"] == "2026-10-05T08:00:00.000000Z"
    assert result["source_updated_at"] == "2026-10-04T17:35:00.000000Z"
    assert result["currency"] == "EUR"
    assert result["period"]["label"] == "2026-10"
    assert result["filters"]["owner_id"] == "o-alice"
    assert result["config_version"] == "synthetic-v1"
    proposal = next(group for group in result["groups"] if group["stage_id"] == "proposal")
    assert Decimal(proposal["known_amount"]) == Decimal("120000")


def test_closed_won_null_is_not_zero_and_is_warned(service):
    result = service.call_tool(
        "get_pipeline_summary",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "filters": {"status": "closed_won"},
        },
    )
    assert result["ok"] is True
    assert result["total"]["deal_count"] == 3
    assert result["total"]["known_deal_count"] == 2
    assert result["total"]["amount"] is None
    # 300,000 + 140,000; D-1019 is null and must not become zero.
    assert Decimal(result["total"]["known_amount"]) == Decimal("440000")
    assert any("D-1019" in warning and "null" in warning for warning in result["warnings"])


def test_unsupported_filters_and_mixed_currency_fail_explicitly(service):
    invalid = service.call_tool(
        "get_pipeline_summary",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "filters": {"region": "EMEA"},
        },
    )
    assert invalid["ok"] is False
    assert invalid["error"]["code"] == "unsupported_filter"
    assert invalid["error"]["supported_filters"]

    mixed = service.call_tool(
        "get_pipeline_summary",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "filters": {"status": "open"},
        },
    )
    assert mixed["ok"] is False
    assert mixed["error"]["code"] == "mixed_currency"
    assert set(mixed["error"]["observed_currencies"]) == {"EUR", "USD"}


def test_fiscal_period_uses_configured_calendar(service):
    result = service.call_tool(
        "get_pipeline_summary",
        {
            "period": {"type": "fiscal_quarter", "fiscal_year": 2027, "fiscal_quarter": 3},
            "filters": {"owner_id": "o-alice"},
        },
    )
    assert result["period"]["label"] == "FY2027-Q3"
    assert result["period"]["start"] == "2026-07-31T22:00:00.000000Z"
    assert result["period"]["end"] == "2026-10-31T23:00:00.000000Z"
    # August-October 2026; excludes D-1009, which closes Nov. 3.
    assert Decimal(result["total"]["amount"]) == Decimal("370000")


def test_duplicate_exact_name_requests_clarification(service):
    result = service.call_tool("get_deal", {"query_or_id": "Acme Expansion"})
    assert result["ok"] is False
    assert result["error"]["code"] == "ambiguous_deal"
    assert {item["id"] for item in result["error"]["candidates"]} == {"D-1001", "D-1002"}
    exact = service.call_tool("get_deal", {"query_or_id": "D-1009"})
    assert exact["ok"] is True
    assert exact["deal"]["name"] == "Harbor Analytics"
    assert Decimal(exact["deal"]["amount"]) == Decimal("110000")
    assert [item["snapshot_id"] for item in exact["available_history"]] == [
        "synthetic-northwind-2026-09-28",
        "synthetic-northwind-2026-10-05",
    ]


def test_forecast_bases_are_distinct_and_sourced(service):
    weighted = service.call_tool(
        "get_forecast",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "basis": "weighted",
            "filters": {"owner_id": "o-alice"},
        },
    )
    assert weighted["basis"] == "weighted"
    assert Decimal(weighted["total"]["amount"]) == Decimal("284000.00")

    submitted = service.call_tool(
        "get_forecast",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 10},
            "basis": "submitted",
            "filters": {"owner_id": "o-alice"},
        },
    )
    assert submitted["basis"] == "submitted"
    assert Decimal(submitted["total"]["amount"]) == Decimal("490000")
    assert submitted["submitted_forecast"]["basis"] == "submitted_manager_forecast"
    assert submitted["submitted_forecast"]["source"]["type"] == "synthetic_manager_submission"

    unavailable = service.call_tool(
        "get_forecast",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 11},
            "basis": "submitted",
        },
    )
    assert unavailable["error"]["code"] == "submitted_forecast_unavailable"


def test_snapshot_delta_uses_only_observed_history(service):
    result = service.call_tool(
        "compare_snapshots",
        {
            "from_time": "2026-09-28T09:00:00Z",
            "to_time": "2026-10-05T09:00:00Z",
            "filters": {"owner_id": "o-alice"},
        },
    )
    assert Decimal(result["from_snapshot"]["total"]["amount"]) == Decimal("1080000")
    assert Decimal(result["to_snapshot"]["total"]["amount"]) == Decimal("1100000")
    assert Decimal(result["delta"]["known_amount"]) == Decimal("20000")
    assert result["won_deal_ids"] == ["D-1005", "D-1016"]
    assert result["slipped_deal_ids"] == ["D-1009"]
    harbor = next(change for change in result["deal_changes"] if change["deal_id"] == "D-1009")
    assert harbor["changes"]["amount_raw"] == {"from": "100000", "to": "110000"}
    assert harbor["changes"]["close_raw"] == {
        "from": "2026-10-15T00:00:00Z",
        "to": "2026-11-03T00:00:00Z",
    }


def test_simulate_slip_is_non_mutating(service):
    before_snapshot = service.repository.latest_snapshot()
    result = service.call_tool(
        "simulate_slip",
        {
            "deal_ids": ["D-1009"],
            "target_period": {"type": "fiscal_quarter", "fiscal_year": 2027, "fiscal_quarter": 4},
            # D-1009 still closes Oct. 15 in this observed baseline; the
            # hypothetical moves it to the end of FY2027-Q4 without mutation.
            "baseline": "synthetic-northwind-2026-09-28",
        },
    )
    assert result["ok"] is True
    assert result["scenario"]["hypothetical"] is True
    assert result["scenario"]["mutated_records"] == []
    assert result["before"]["amount"] == "100000"
    assert result["after"]["amount"] == "100000"
    assert result["deals"][0]["close_date_after"] == "2027-01-31T22:59:59.999999Z"
    assert service.repository.latest_snapshot() == before_snapshot


def test_previous_commitments_are_sourced(service):
    result = service.call_tool(
        "get_previous_commitments",
        {"meeting_series": "Weekly Pipeline Review", "deal_id": "D-1009"},
    )
    assert result["ok"] is True
    assert result["commitment_count"] == 1
    commitment = result["commitments"][0]
    assert commitment["id"] == "C-002"
    assert commitment["status"] == "open"
    assert commitment["source"]["type"] == "synthetic_meeting_record"


def test_read_tools_supply_spoken_amounts_without_changing_exact_data(service):
    summary = service.call_tool("get_pipeline_summary", {"filters": {"owner_id": "o-alice"}})
    assert "spoken_amount" in summary["total"]
    assert "spoken_exact_amount" in summary["total"]
    listing = service.call_tool("list_deals", {"limit": 1, "filters": {"owner_id": "o-alice"}})
    deal = listing["deals"][0]
    lookup = service.call_tool("get_deal", {"query_or_id": deal["id"]})
    assert lookup["deal"]["amount"] == deal["amount"]
    assert lookup["deal"]["spoken_amount"] == deal["spoken_amount"]
    assert set(deal["spoken_amount"]) == {"en", "es", "it"}
    assert "spoken_deal_count" not in summary["total"]
