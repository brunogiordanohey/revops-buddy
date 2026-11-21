import json
import re

import pytest

from revops_buddy.hubspot import HubSpotSync, HubSpotSyncError
from revops_buddy.repository import Repository


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        # FakeResponse({...}) means HTTP 200 with that JSON body.
        if isinstance(status_code, dict):
            # HubSpot list responses always describe pagination; default to
            # "no next page" when the shorthand omits the paging object.
            body = dict(status_code)
            body.setdefault("paging", {})
            status_code = 200
        self.status_code = status_code
        self.body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, params, timeout):
        self.calls.append(
            {
                "url": url,
                "method": "GET",
                "authorization": headers.get("Authorization"),
                "params": dict(params),
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected HubSpot request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(url=url, params=params)
        return item


def deal(deal_id, name, amount, currency="EUR", stage="proposal", owner="owner-1", close_ms=None):
    properties = {
        "dealname": name,
        "amount": amount,
        "dealcurrency": currency,
        "pipeline": "default",
        "dealstage": stage,
        "hubspot_owner_id": owner,
        "hs_lastmodifieddate": "1761884100000",
    }
    if close_ms is not None:
        properties["closedate"] = str(close_ms)
    return {"id": deal_id, "properties": properties, "archived": False, "createdAt": "2026-01-01T00:00:00Z"}


@pytest.fixture()
def repository(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path}/hubspot.db")
    repo.initialize()
    return repo


def config():
    return {
        "company_id": "test-company",
        "config_version": "test-v1",
        "amount_field": "amount",
        "custom_deal_properties": ["forecastcategory", "segment"],
    }


def test_complete_pagination_before_snapshot_promotion(repository):
    client = FakeClient(
        [
            FakeResponse({"results": [{"id": "owner-1", "firstName": "A", "lastName": "Owner"}]}),
            FakeResponse(
                {
                    "results": [
                        {
                            "id": "default",
                            "label": "Sales",
                            "stages": [
                                {"id": "proposal", "label": "Proposal", "metadata": {"isClosed": False}}
                            ],
                        }
                    ]
                }
            ),
            FakeResponse(
                {
                    "results": [
                        deal("1001", "First", "10000", close_ms=1761820800000),
                        deal("1002", "Second", None, stage="qualify"),
                    ],
                    "paging": {"next": {"after": "2"}},
                }
            ),
            FakeResponse({"results": [deal("1003", "Third", "55", "USD")]}),
        ]
    )
    sync = HubSpotSync("server-side-test-token", repository, config(), http_client=client)
    result = sync.sync()
    assert result["status"] == "complete"
    assert result["deal_count"] == 3
    assert result["page_counts"]["deals"] == 2
    assert result["source_updated_at"] == "2025-10-31T04:15:00Z"
    snapshot = repository.latest_snapshot()
    assert snapshot["source"] == "hubspot_api"
    assert [item["id"] for item in snapshot["deals"]] == ["1001", "1002", "1003"]
    assert snapshot["deals"][0]["properties"]["closedate"] == "2025-10-30T10:40:00Z"
    assert snapshot["deals"][0]["api_record"]["id"] == "1001"
    assert snapshot["deals"][0]["api_record"]["properties"]["amount"] == "10000"
    assert json.dumps(snapshot).find("server-side-test-token") == -1
    deal_call = client.calls[-1]
    assert deal_call["url"].endswith("/crm/v3/objects/deals")
    assert set(deal_call["params"]["properties"].split(",")) >= {
        "dealname",
        "amount",
        "dealcurrency",
        "pipeline",
        "dealstage",
        "hubspot_owner_id",
        "closedate",
        "forecastcategory",
        "segment",
    }
    assert deal_call["params"]["after"] == "2"
    assert all(call["method"] == "GET" for call in client.calls)


def test_rate_limit_uses_bounded_retry_and_retry_after(repository):
    client = FakeClient(
        [
            FakeResponse(429, {"message": "rate limited"}, {"Retry-After": "0.5"}),
            FakeResponse({"results": [{"id": "owner-1"}]}),
            FakeResponse({"results": []}),
            FakeResponse({"results": []}),
        ]
    )
    sleeps = []
    sync = HubSpotSync(
        "token",
        repository,
        config(),
        http_client=client,
        initial_retry_delay=0.01,
        sleep=sleeps.append,
    )
    result = sync.sync()
    assert result["ok"] is True
    assert sleeps == [0.5]


def test_failed_last_page_is_never_promoted(repository):
    client = FakeClient(
        [
            FakeResponse({"results": [{"id": "owner-1"}]}),
            FakeResponse({"results": []}),
            FakeResponse({"results": [deal("1001", "First", "1")], "paging": {"next": {"after": "1"}}}),
            FakeResponse(500, {"message": "temporary"}),
            FakeResponse(500, {"message": "temporary"}),
            FakeResponse(500, {"message": "temporary"}),
        ]
    )
    sync = HubSpotSync(
        "token",
        repository,
        config(),
        http_client=client,
        initial_retry_delay=0,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(HubSpotSyncError, match=r"/crm/v3/objects/deals.*status 500"):
        sync.sync()
    assert repository.list_snapshots() == []


def test_non_retryable_http_error_does_not_retry(repository):
    client = FakeClient(
        [
            FakeResponse(403, {"message": "missing scope"}),
            FakeResponse({"results": []}),
            FakeResponse({"results": []}),
        ]
    )
    sync = HubSpotSync("token", repository, config(), http_client=client, sleep=lambda _: None)
    with pytest.raises(HubSpotSyncError, match="status 403"):
        sync.sync()
    assert len(client.calls) == 1
    assert repository.list_snapshots() == []
