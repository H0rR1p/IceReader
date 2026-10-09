"""Isolated browser acceptance server. AI output is simulated; no cloud access."""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get('BINGDU_DATA_DIR', '')).resolve()
if os.environ.get('BINGDU_UI_TEST_RUNTIME') != '1' or not DATA.is_relative_to(ROOT / 'build' / 'ui-linguistics-full'):
    raise SystemExit('This test runtime requires an isolated build/ui-linguistics-full data directory.')
DATA.mkdir(parents=True, exist_ok=True)

import httpx

original_async_transport = httpx.AsyncHTTPTransport.handle_async_request
original_sync_transport = httpx.HTTPTransport.handle_request


async def local_async_transport(self, request):
    if request.url.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise AssertionError('External network access is forbidden in browser acceptance.')
    return await original_async_transport(self, request)


def local_sync_transport(self, request):
    if request.url.host not in {'127.0.0.1', 'localhost', '::1'}:
        raise AssertionError('External network access is forbidden in browser acceptance.')
    return original_sync_transport(self, request)


httpx.AsyncHTTPTransport.handle_async_request = local_async_transport
httpx.HTTPTransport.handle_request = local_sync_transport

from backend import ai
from backend.app import app
from backend.modules.analysis import resegmentation
from backend.modules.book_memory import preflight
from backend.modules.learning import repository as learning_repository

calls = []
original_stage = resegmentation._build_stage


def slow_stage(*args, **kwargs):
    time.sleep(0.7)
    return original_stage(*args, **kwargs)


async def fake_chat(user_id, key, url, model, system, payload, **kwargs):
    operation = kwargs.get('operation', '')
    data = json.loads(payload)
    calls.append({'operation': operation, 'url': url, 'model': model, 'payload': data})
    (DATA.parent / 'mock-ai-calls.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding='utf-8')
    await asyncio.sleep(0.25)
    if operation == 'book_preflight':
        text = data['text']
        quote = '僕は男です'
        start = text.find(quote)
        return {'entities': [{'name': '田中', 'facts': [{'key': 'gender', 'value': '男性', 'start': start, 'end': start + len(quote), 'quote': quote}]}] if start >= 0 and '田中' in text else [],
                'summary': '田中が自分について語る。これは模擬AIの参考要約です。', '_usage': {'total_tokens': 80}}
    raise AssertionError(f'Unexpected mocked AI operation: {operation}')


resegmentation._build_stage = slow_stage
ai._chat_json = fake_chat


from fastapi import Depends
from backend.core.request_context import RequestContext, current_request_context
from backend.modules.jobs import repository as job_repository


@app.post('/__test__/failed-task')
async def failed_task(payload: dict, context: RequestContext = Depends(current_request_context)):
    job = job_repository.create_task(context.user_id, 'resegmentation-v1', {'book_id': payload['book_id']}, 'fixture-failed')
    job_repository.checkpoint_task(context.user_id, job['id'], {}, 10, 34)
    job_repository.finish_task(context.user_id, job['id'], 'failed', '重切词素原文锚点不合法。已完成的章节保留，长说明用于验证面板自动换行。')
    return job_repository.get_job(context.user_id, job['id'])


@app.get('/__test__/state')
async def test_state():
    with learning_repository._connect() as connection:
        events = [dict(row) for row in connection.execute('SELECT e.event_type,k.type,k.canonical_key,e.context_json FROM learning_events e JOIN knowledge_items k ON k.id=e.knowledge_item_id ORDER BY e.occurred_at')]
    return {'isolated': True, 'data_dir': str(DATA), 'ai_calls': calls, 'events': events}


# The production StaticFiles catchall is already registered; test diagnostics
# must precede it. This runtime is reachable only on the loopback test port.
for route in list(app.router.routes):
    if getattr(route, 'path', '').startswith('/__test__/'):
        app.router.routes.remove(route)
        app.router.routes.insert(0, route)


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('BINGDU_UI_TEST_PORT', '8792')))
