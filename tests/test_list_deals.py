import json
from decimal import Decimal
from pathlib import Path

import pytest

from revops_buddy.domain.service import RevOpsService
from revops_buddy.repository import Repository


FIXTURES = Path(__file__).resolve().parents[1] / "revops_buddy" / "fixtures"


@pytest.fixture()
def service(tmp_path):
    repository = Repository(f"sqlite:///{tmp_path}/list-deals.db")
    repository.initialize()
    with (FIXTURES / "company.json").open() as handle:
        config = json.load(handle)["company_config"]
    result = RevOpsService(repository, config)
    result.load_demo()
    return result


def test_biggest_open_deal_is_highest_amount(service):
    result = service.call_tool("list_deals", {"filters": {"status": "open"}, "limit": 1})
    assert result["ok"] is True
    assert result["total_matching"] == 13
    assert [deal["id"] for deal in result["deals"]] == ["D-1003"]
    assert Decimal(result["deals"][0]["amount"]) == Decimal("250000")
    assert result["deals"][0]["status"] == "open"


def test_closing_in_calendar_month_sorted_by_close_date_asc(service):
    result = service.call_tool(
        "list_deals",
        {
            "period": {"type": "calendar_month", "year": 2026, "month": 11},
            "sort_by": "close_date",
            "order": "asc",
        },
    )
    assert [deal["id"] for deal in result["deals"]] == ["D-1009", "D-1018"]
    assert result["deals"][0]["close_date"] == "2026-11-03T00:00:00Z"


def test_close_date_defaults_to_ascending(service):
    result = service.call_tool("list_deals", {"sort_by": "close_date", "limit": 3})
    dates = [deal["close_date"] for deal in result["deals"]]
    assert dates == sorted(dates)


def test_limit_bounds_rejected(service):
    for limit in (0, 11):
        result = service.call_tool("list_deals", {"limit": limit})
        assert result["ok"] is False
        assert result["error"]["code"] == "invalid_input"
        assert "limit" in result["error"]["message"]


def test_filter_by_stage_name(service):
    result = service.call_tool("list_deals", {"filters": {"stage_name": "Proposal"}})
    assert result["ok"] is True
    assert {deal["id"] for deal in result["deals"]} == {
        "D-1001",
        "D-1009",
        "D-1012",
        "D-1013",
        "D-1018",
    }
    assert all(deal["stage_name"] == "Proposal" for deal in result["deals"])


def test_unknown_amounts_sort_last_with_warning(service):
    result = service.call_tool(
        "list_deals",
        {"filters": {"stage_name": "Qualification"}, "sort_by": "amount"},
    )
    ids = [deal["id"] for deal in result["deals"]]
    assert ids[-1] == "D-1011"
    assert result["deals"][-1]["amount"] is None
    assert any("D-1011" in warning and "null" in warning for warning in result["warnings"])


def test_closed_excluded_by_default_and_included_explicitly(service):
    default = service.call_tool("list_deals", {"filters": {"stage_name": "Closed Won"}})
    assert default["total_matching"] == 0
    included = service.call_tool(
        "list_deals",
        {"filters": {"stage_name": "Closed Won", "include_closed": True}},
    )
    assert included["total_matching"] == 4
    assert {deal["id"] for deal in included["deals"]} == {"D-1004", "D-1005", "D-1016", "D-1019"}
    assert all(deal["status"] == "closed_won" for deal in included["deals"])


def test_mixed_currency_is_listed_with_warning_not_raised(service):
    result = service.call_tool("list_deals", {"limit": 10})
    assert result["ok"] is True
    assert any("multiple currencies" in warning for warning in result["warnings"])


def test_total_covers_all_matching_deals_not_just_the_limit(service):
    """Spoken totals must come from the tool, not from the model adding listed amounts."""
    listed = service.call_tool("list_deals", {"filters": {"stage_name": "Negotiation"}, "limit": 1})
    assert listed["total_matching"] == 3 and len(listed["deals"]) == 1
    assert listed["total"]["deal_count"] == 3
    assert Decimal(listed["total"]["amount"]) == Decimal("565000")


def test_mixed_currencies_never_produce_a_combined_total(service):
    listed = service.call_tool("list_deals", {"filters": {"stage_name": "Proposal"}})
    assert listed["total"] is None
    assert any("multiple currencies" in warning for warning in listed["warnings"])
