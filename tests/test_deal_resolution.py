import json
from pathlib import Path

import pytest

from revops_buddy.domain.service import RevOpsService
from revops_buddy.repository import Repository


FIXTURES = Path(__file__).resolve().parents[1] / "revops_buddy" / "fixtures"


@pytest.fixture()
def service(tmp_path):
    repository = Repository(f"sqlite:///{tmp_path}/t.db")
    repository.initialize()
    with (FIXTURES / "company.json").open() as handle:
        config = json.load(handle)["company_config"]
    result = RevOpsService(repository, config)
    result.load_demo()
    return result


def _deal_query(service, query):
    return service.call_tool("get_deal", {"query_or_id": query})


def test_deal_exact_id_is_exact(service):
    result = _deal_query(service, "D-1009")
    assert result["ok"] is True
    assert result["deal"]["name"] == "Harbor Analytics"
    assert result["resolution"] == {"type": "exact"}


def test_deal_exact_name_is_exact(service):
    result = _deal_query(service, "Harbor Analytics")
    assert result["ok"] is True
    assert result["resolution"] == {"type": "exact"}


def test_deal_partial_name_is_partial(service):
    result = _deal_query(service, "Cedar")
    assert result["ok"] is True
    assert result["resolution"]["type"] == "partial"
    assert result["resolution"]["matched_name"] == "Cedar Renewal"
    assert result["resolution"]["score"] >= 0.9


def test_deal_misheard_name_is_fuzzy(service):
    result = _deal_query(service, "sedar renewal")
    assert result["ok"] is True
    assert result["resolution"]["type"] == "fuzzy"
    assert result["resolution"]["matched_name"] == "Cedar Renewal"


def test_deal_shared_name_is_ambiguous_with_candidates(service):
    result = _deal_query(service, "Acme Expansion")
    assert result["ok"] is False
    error = result["error"]
    assert error["code"] == "ambiguous_deal"
    assert len(error["candidates"]) == 2
    assert all("status" in candidate and "stage_name" in candidate for candidate in error["candidates"])


def test_deal_unknown_query_lists_suggestions(service):
    result = _deal_query(service, "zzz unknown")
    assert result["ok"] is False
    assert result["error"]["code"] == "deal_not_found"
