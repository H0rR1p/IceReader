import asyncio

import pytest

from . import ai


class _FakeResponse:
    status_code = 200

    def __init__(self, body: dict, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._body

    @property
    def text(self):
        return str(self._body)


def test_chat_json_disables_thinking_and_retries_empty_content(monkeypatch):
    responses = [
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10}},
        {"choices": [{"message": {"content": '{"results":[]}'}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 12}},
    ]
    payloads: list[dict] = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        is_closed = False

        async def post(self, _url, *, headers, json, timeout):
            assert headers["Authorization"] == "Bearer test-key"
            assert timeout.read == 120.0
            payloads.append(json)
            return _FakeResponse(responses.pop(0))

    monkeypatch.setattr(ai, "_shared_http_client", None)
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **_kwargs: _FakeClient())
    usage_rows: list[dict] = []
    monkeypatch.setattr(ai, "record_usage", lambda *args, **kwargs: usage_rows.append({"args": args, **kwargs}))

    result = asyncio.run(ai._chat_json(
        "test-key", "https://api.deepseek.com", "deepseek-v4-flash",
        "输出JSON", "JSON格式：{\"results\":[]}", operation="test",
    ))

    assert result == {"results": []}
    assert len(payloads) == 2
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert payloads[0]["reasoning_effort"] == "none"
    assert "上次返回为空" in payloads[1]["messages"][1]["content"]
    assert usage_rows[0]["success"] is False
    assert len(usage_rows) == 2


def test_chat_json_doubles_output_budget_after_truncation(monkeypatch):
    responses = [
        {"choices": [{"message": {"content": '{"results":['}, "finish_reason": "length"}]},
        {"choices": [{"message": {"content": '{"results":[]}'}, "finish_reason": "stop"}]},
    ]
    payloads: list[dict] = []

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        is_closed = False

        async def post(self, _url, *, headers, json, timeout):
            payloads.append(json)
            return _FakeResponse(responses.pop(0))

    monkeypatch.setattr(ai, "_shared_http_client", None)
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **_kwargs: _FakeClient())
    monkeypatch.setattr(ai, "record_usage", lambda *_args, **_kwargs: None)

    result = asyncio.run(ai._chat_json(
        "test-key", "https://api.deepseek.com", "deepseek-v4-flash",
        "输出JSON", "JSON格式：{\"results\":[]}", operation="test", max_tokens=4096,
    ))

    assert result == {"results": []}
    assert payloads[0]["max_tokens"] == 4096
    assert payloads[1]["max_tokens"] == 8192


def test_http_client_is_reused(monkeypatch):
    created = []

    class _ReusableClient:
        is_closed = False

    def build_client(**_kwargs):
        client = _ReusableClient()
        created.append(client)
        return client

    monkeypatch.setattr(ai, "_shared_http_client", None)
    monkeypatch.setattr(ai.httpx, "AsyncClient", build_client)

    assert ai._get_http_client() is ai._get_http_client()
    assert len(created) == 1


def test_http_client_does_not_force_ipv4_source_address(monkeypatch):
    transport_options = {}

    class _ReusableClient:
        is_closed = False

    def build_transport(**kwargs):
        transport_options.update(kwargs)
        return object()

    monkeypatch.setattr(ai, "_shared_http_client", None)
    monkeypatch.setattr(ai.httpx, "AsyncHTTPTransport", build_transport)
    monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **_kwargs: _ReusableClient())

    ai._get_http_client()

    assert "local_address" not in transport_options


@pytest.mark.parametrize("status_code", [429, 503])
def test_chat_json_retries_transient_status_and_respects_retry_after(monkeypatch, status_code):
    responses = [
        type("TransientFailure", (_FakeResponse,), {"status_code": status_code})(
            {"error": "slow down"}, {"Retry-After": "2"},
        ),
        _FakeResponse({"choices": [{"message": {"content": '{"results":[]}'}}]}),
    ]
    sleeps: list[float] = []

    class _FakeClient:
        is_closed = False

        async def post(self, _url, *, headers, json, timeout):
            return responses.pop(0)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(ai, "_shared_http_client", _FakeClient())
    monkeypatch.setattr(ai, "_unsupported_request_fields", {})
    monkeypatch.setattr(ai.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ai, "record_usage", lambda *_args, **_kwargs: None)

    result = asyncio.run(ai._chat_json(
        "key", "https://api.example", "model", "system", "prompt", operation="test",
    ))

    assert result == {"results": []}
    assert sleeps == [2.0]


def test_full_explanation_output_budget_scales_past_4096(monkeypatch):
    observed = {}

    async def fake_chat(*_args, **kwargs):
        observed.update(kwargs)
        return {"results": []}

    monkeypatch.setattr(ai, "_chat_json", fake_chat)
    items = [
        {
            "sentence": {"id": f"s-{index}", "original": "長い文章です。"},
            "unresolved_tokens": [],
        }
        for index in range(10)
    ]

    asyncio.run(ai.explain_sentences(
        items, "key", "https://api.example", "model", detail_mode="full",
    ))

    assert observed["max_tokens"] == 5200


def test_chat_json_removes_unsupported_optional_fields_and_caches_capability(monkeypatch):
    payloads: list[dict] = []

    class _CapabilityResponse(_FakeResponse):
        def __init__(self, status_code: int, body: dict, text: str = ""):
            super().__init__(body)
            self.status_code = status_code
            self._text = text

        @property
        def text(self):
            return self._text

    responses = [
        _CapabilityResponse(400, {}, "Unsupported parameter: response_format"),
        _CapabilityResponse(400, {}, "Unknown parameter: thinking"),
        _CapabilityResponse(400, {}, "Unknown parameter: reasoning_effort"),
        _CapabilityResponse(200, {"choices": [{"message": {"content": '{"results":[]}'}}]}),
        _CapabilityResponse(200, {"choices": [{"message": {"content": '{"results":[]}'}}]}),
    ]

    class _FakeClient:
        is_closed = False

        async def post(self, _url, *, headers, json, timeout):
            payloads.append(json)
            return responses.pop(0)

    monkeypatch.setattr(ai, "_shared_http_client", _FakeClient())
    monkeypatch.setattr(ai, "_unsupported_request_fields", {})
    monkeypatch.setattr(ai, "record_usage", lambda *_args, **_kwargs: None)

    args = ("key", "https://compatible.example/v1", "plain-model", "system", "prompt")
    assert asyncio.run(ai._chat_json(*args, operation="test")) == {"results": []}
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
    assert "thinking" not in payloads[2]
    assert "reasoning_effort" not in payloads[3]

    assert asyncio.run(ai._chat_json(*args, operation="test")) == {"results": []}
    assert not ({"response_format", "thinking", "reasoning_effort"} & payloads[4].keys())
