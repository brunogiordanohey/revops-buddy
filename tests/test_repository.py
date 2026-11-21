import pytest


from revops_buddy.repository import Repository


@pytest.fixture()
def repository(tmp_path):
    repo = Repository(f"sqlite:///{tmp_path}/revops_buddy.db")
    repo.initialize()
    return repo


def test_snapshot_round_trip_and_latest_complete(repository):
    incomplete_id = repository.save_snapshot(
        {
            "company_id": "synthetic-northwind",
            "captured_at": "2026-10-06T10:00:00+02:00",
            "complete": False,
            "status": "incomplete",
            "deals": [],
        }
    )
    complete_id = repository.save_snapshot(
        {
            "company_id": "synthetic-northwind",
            "captured_at": "2026-10-05T08:00:00Z",
            "source_updated_at": "2026-10-04T17:35:00Z",
            "deals": [{"id": "D-1"}],
        }
    )
    assert incomplete_id != complete_id
    assert repository.latest_snapshot()["snapshot_id"] == complete_id
    listed = repository.list_snapshots()
    assert [item["snapshot_id"] for item in listed] == [incomplete_id, complete_id]
    assert repository.latest_snapshot()["captured_at"] == "2026-10-05T08:00:00.000000Z"
    assert repository.get_snapshot(complete_id)["source_updated_at"] == "2026-10-04T17:35:00.000000Z"


