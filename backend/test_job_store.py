import pytest

from .modules.jobs import repository as job_store


def _isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(job_store, "JOBS_PATH", tmp_path / "jobs.sqlite3")
    monkeypatch.setattr(job_store, "_initialized_path", None)


def test_jobs_are_persistent_and_owner_scoped(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    job_store.create_job("user-a", "voice", job_id="job-1", request_id="request-1")
    job_store.update_job(
        "user-a", "job-1", status="complete", message="done",
        current=1, total=1, result={"cached": True},
    )

    job = job_store.get_job("user-a", "job-1")
    assert job["status"] == "complete"
    assert job["result"] == {"cached": True}
    assert job_store.list_jobs("user-b") == []
    with pytest.raises(KeyError):
        job_store.get_job("user-b", "job-1")


def test_job_id_cannot_be_reassigned_to_another_owner(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    job_store.create_job("user-a", "voice", job_id="shared-id")

    with pytest.raises(KeyError):
        job_store.create_job("user-b", "voice", job_id="shared-id")

    assert job_store.get_job("user-a", "shared-id")["owner_user_id"] == "user-a"
