"""Persisted bounded workers; cancellation stops at a transaction boundary."""
import asyncio
import sqlite3
from dataclasses import dataclass
from fastapi import HTTPException
from . import repository

_handlers = {}
_workers: list[asyncio.Task] = []
_wake: asyncio.Event | None = None
_stopping = False


class TaskCanceled(Exception):
    pass


class TaskPaused(Exception):
    pass


def register(kind: str, handler) -> None:
    _handlers[kind] = handler


@dataclass
class Control:
    user_id: str
    job_id: str
    checkpoint: dict

    async def check(self):
        job = await asyncio.to_thread(repository.get_job, self.user_id, self.job_id)
        if job['cancel_requested']:
            raise TaskCanceled()
        if _stopping:
            raise TaskPaused()

    async def progress(self, current: int, total: int, checkpoint: dict, result: dict | None = None):
        await asyncio.to_thread(repository.checkpoint_task, self.user_id, self.job_id,
                                checkpoint, current, total, result)
        self.checkpoint = checkpoint

    async def wait_retry(self, seconds: float):
        remaining = min(3600, max(1, seconds))
        while remaining > 0:
            await self.check()
            delay = min(1, remaining)
            await asyncio.sleep(delay)
            remaining -= delay


async def submit(user_id: str, kind: str, request: dict, *, lock_key: str,
                 request_id: str | None = None) -> dict:
    if kind not in _handlers:
        raise HTTPException(503, '任务处理器尚未初始化')
    try:
        result = await asyncio.to_thread(repository.create_task, user_id, kind, request, lock_key, request_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, '所选范围已有任务，请等待或取消后再试') from exc
    if _wake is not None:
        _wake.set()
    return result


async def resume(user_id: str, job_id: str):
    job = await asyncio.to_thread(repository.get_job, user_id, job_id)
    if job['kind'] not in _handlers:
        raise HTTPException(503, '此任务处理器不可用')
    try:
        result = await asyncio.to_thread(repository.resume_task, user_id, job_id)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(409, str(exc)) from exc
    if _wake is not None:
        _wake.set()
    return result


async def _worker():
    while not _stopping:
        job = await asyncio.to_thread(repository.claim_task, list(_handlers))
        if job is None:
            try:
                await asyncio.wait_for(_wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
            _wake.clear()
            continue
        owner, job_id = job['owner_user_id'], job['id']
        spec = await asyncio.to_thread(repository.task_spec, owner, job_id)
        control = Control(owner, job_id, spec['checkpoint'])
        try:
            await control.check()
            result = await _handlers[job['kind']](control, spec['request'], spec['checkpoint'])
            await control.check()
            await asyncio.to_thread(repository.finish_task, owner, job_id, 'complete', '处理完成', result)
        except (TaskCanceled, TaskPaused) as exc:
            status = 'canceled' if isinstance(exc, TaskCanceled) else 'paused'
            old = await asyncio.to_thread(repository.get_job, owner, job_id)
            await asyncio.to_thread(repository.finish_task, owner, job_id, status,
                '已安全停止，已提交步骤保留，可恢复' if status == 'canceled' else '保留断点，可继续', old['result'])
        except Exception as exc:
            old = await asyncio.to_thread(repository.get_job, owner, job_id)
            await asyncio.to_thread(repository.finish_task, owner, job_id, 'failed', str(exc)[:1000], old['result'])


async def start():
    global _wake, _stopping
    if _workers:
        return
    _stopping, _wake = False, asyncio.Event()
    await asyncio.to_thread(repository.recover_tasks)
    _workers.extend(asyncio.create_task(_worker()) for _ in range(2))


async def stop():
    global _stopping
    _stopping = True
    if _wake:
        _wake.set()
    # Never cancel a to_thread writer or remove its files underneath it.
    if _workers:
        await asyncio.gather(*_workers, return_exceptions=True)
    _workers.clear()
