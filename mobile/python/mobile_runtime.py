"""In-process ASGI runtime: no TCP listener; session secrets never enter JavaScript."""
import asyncio
import base64
import json
import os
import secrets
import threading

_loop = None
_client = None
_secure = None
_pending = {}
_cancelled = set()
_startup_error = None

def start(data_path, secure_store, native_lib_dir):
    global _loop, _secure, _startup_error
    if _startup_error: raise RuntimeError(_startup_error)
    if _loop is not None: return
    _secure = secure_store
    os.environ['BINGDU_DATA_DIR'] = data_path
    os.environ['BINGDU_ANDROID'] = '1'
    os.environ['BINGDU_NATIVE_LIB_DIR'] = native_lib_dir
    # One SQLite engine must own all connections in this process. Mixing engines
    # on a WAL file can invalidate POSIX locks when either library closes a handle.
    import sqlite3
    from mobile_sqlite import Connection
    sqlite3.connect = Connection
    from pathlib import Path
    os.environ['BINGDU_LEGAL_DIR'] = str(Path(data_path).parent / 'legal')
    os.environ['BINGDU_DESKTOP_SECRET'] = secrets.token_urlsafe(32)
    os.environ.pop('BINGDU_PUBLIC_ORIGIN', None)
    _loop = asyncio.new_event_loop()
    threading.Thread(target=_loop.run_forever, name='bingdu-asgi', daemon=True).start()
    try: asyncio.run_coroutine_threadsafe(_initialize(), _loop).result(timeout=60)
    except Exception as error:
        import traceback
        _startup_error = traceback.format_exc()
        raise RuntimeError(_startup_error) from error

async def _initialize():
    global _client, _lifespan
    import httpx
    from backend.app import app
    from backend.modules.cloud_account import repository as cloud_store
    from backend import settings_store
    from cryptography.fernet import Fernet
    cloud_store._cipher = lambda: Fernet(str(_secure.getSecret('cloud-fernet-key')).encode())
    settings_store._secret_store = _secure
    _lifespan = app.router.lifespan_context(app)
    await _lifespan.__aenter__()
    _client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1',
        headers={'x-bingdu-desktop-secret': os.environ['BINGDU_DESKTOP_SECRET']})
    stored = str(_secure.read('session-cookies') or '')
    if stored:
        for key, value in json.loads(stored).items(): _client.cookies.set(key, value)
    await _client.get('/api/me')
    _save_cookies()

def _save_cookies():
    _secure.write('session-cookies', json.dumps({c.name: c.value for c in _client.cookies.jar}))

async def _request(method, path, headers, data):
    if not path.startswith('/api/') or path.startswith('/api/voice') or '\\' in path:
        raise ValueError('Unsupported application request')
    allowed = {k: v for k, v in headers.items() if k.lower() in {'content-type', 'accept', 'x-api-key'}}
    response = await _client.request(method, path, headers=allowed, content=data)
    _save_cookies()
    return json.dumps({'status': response.status_code,
        'headers': {k: v for k, v in response.headers.items() if k.lower() != 'set-cookie'},
        'body': base64.b64encode(response.content).decode()}, ensure_ascii=False)

def request(method, path, headers_json='{}', body_base64='', request_id=''):
    future = asyncio.run_coroutine_threadsafe(_request(method, path, json.loads(headers_json),
        base64.b64decode(body_base64)), _loop)
    if request_id: _pending[request_id] = future
    if request_id in _cancelled: future.cancel()
    try: return future.result(timeout=300)
    finally:
        _pending.pop(request_id, None)
        _cancelled.discard(request_id)

def cancel(request_id):
    future = _pending.get(request_id)
    if future: future.cancel()
    else:
        if len(_cancelled) > 1024: _cancelled.clear()
        _cancelled.add(request_id)

async def _upload(path, file_path, filename, mime, fields):
    if not path.startswith('/api/') or path.startswith('/api/voice'):
        raise ValueError('Unsupported upload')
    with open(file_path, 'rb') as stream:
        response = await _client.post(path, files={'file': (filename, stream, mime)}, data=fields)
    _save_cookies()
    return json.dumps({'status': response.status_code, 'headers': dict(response.headers),
        'body': base64.b64encode(response.content).decode()})

def upload(path, file_path, filename, mime, fields='{}'):
    return asyncio.run_coroutine_threadsafe(_upload(path, file_path, filename, mime, json.loads(fields)),
        _loop).result(timeout=300)

async def _export(path, method, body, destination):
    """Stream ASGI FileResponse to cache, without loading packages into the WebView."""
    from backend.app import app
    from urllib.parse import unquote
    request = _client.build_request(method, path, headers={'content-type': 'application/json'}, content=body)
    scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
        'method': method, 'scheme': 'http', 'path': request.url.path, 'raw_path': request.url.raw_path.split(b'?')[0],
        'query_string': request.url.query, 'root_path': '', 'headers': request.headers.raw,
        'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 0)}
    result = {}; consumed = False
    async def receive():
        nonlocal consumed
        if consumed: return {'type': 'http.disconnect'}
        consumed = True
        return {'type': 'http.request', 'body': body, 'more_body': False}
    with open(destination, 'wb') as writer:
        async def send(message):
            if message['type'] == 'http.response.start':
                result.update(status=message['status'], headers=dict(message.get('headers', [])))
            elif message['type'] == 'http.response.body': writer.write(message.get('body', b''))
        await app(scope, receive, send)
    if result.get('status', 500) >= 400:
        from pathlib import Path
        error = Path(destination).read_text(encoding='utf-8', errors='replace')[:1000]
        Path(destination).unlink(missing_ok=True)
        raise ValueError(error)
    disposition = result['headers'].get(b'content-disposition', b'').decode()
    filename = unquote(disposition.split("filename*=utf-8''")[-1]) if "filename*=utf-8''" in disposition else 'IceReader-source.zip'
    return filename

def export_file(path, method, body_json, destination):
    if path not in {'/api/data/backup', '/api/data/book-transfer', '/api/data/book-share', '/api/legal/source'}:
        raise ValueError('Unsupported export')
    return asyncio.run_coroutine_threadsafe(_export(path, method, body_json.encode(), destination),
        _loop).result(timeout=600)
