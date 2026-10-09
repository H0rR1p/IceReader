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


@pytest.mark.parametrize('status', ['queued', 'paused', 'failed'])
def test_cancel_unfinished_task_preserves_commits_and_releases_lock(tmp_path, monkeypatch, status):
    _isolated_store(tmp_path, monkeypatch)
    task = job_store.create_task('user-a', 'resegmentation-v1', {'book_id': 'b'}, 'resegment:b')
    checkpoint = {'completed': ['chapter-1']}
    result = {'chapters': [{'chapter_id': 'chapter-1'}]}
    job_store.checkpoint_task('user-a', task['id'], checkpoint, 1, 4, result)
    if status != 'queued':
        job_store.finish_task('user-a', task['id'], status, 'unfinished', result)
    with pytest.raises(KeyError):
        job_store.request_cancel('user-b', task['id'])
    canceled = job_store.request_cancel('user-a', task['id'])
    assert canceled['status'] == 'canceled' and canceled['cancel_requested']
    assert canceled['progress_current'] == 1 and canceled['result'] == result
    assert job_store.task_spec('user-a', task['id'])['checkpoint'] == checkpoint
    assert job_store.request_cancel('user-a', task['id'])['status'] == 'canceled'
    replacement = job_store.create_task('user-a', 'resegmentation-v1', {'book_id': 'b'}, 'resegment:b')
    assert replacement['status'] == 'queued'


def test_running_cancel_requests_boundary_stop(tmp_path, monkeypatch):
    _isolated_store(tmp_path, monkeypatch)
    task = job_store.create_task('a', 'resegmentation-v1', {'book_id': 'b'}, 'resegment:b')
    job_store.claim_task(['resegmentation-v1'])
    result = job_store.request_cancel('a', task['id'])
    assert result['status'] == 'running' and result['cancel_requested']
