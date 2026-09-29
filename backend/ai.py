import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import random
import ssl
import time
import urllib.error
import urllib.request

import certifi
import httpx

from .ai_store import record_usage


PROMPT_VERSION = "sentence-v5"
SYSTEM_PROMPT = """你是一名严谨的日语 N1 精读编辑。输出合法 JSON，中文使用简体中文。
不得改写日文原文。句意用于帮助理解，不追求文学精翻。只解释真正影响理解的内容，不凑注释。
"""


_shared_http_client: httpx.AsyncClient | None = None
_unsupported_request_fields: dict[tuple[str, str], set[str]] = {}
_OPTIONAL_REQUEST_FIELDS = {"response_format", "thinking", "reasoning_effort"}
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_HTTP_ATTEMPTS = 4


class AiRateLimitError(RuntimeError):
    def __init__(self, detail: str, retry_after: float | None = None):
        super().__init__(f"AI 服务限流（429）：{detail}")
        self.retry_after = retry_after


def _get_http_client() -> httpx.AsyncClient:
    """Reuse TCP/TLS connections across the many small translation batches."""
    global _shared_http_client
    if _shared_http_client is None or getattr(_shared_http_client, "is_closed", False):
        transport = httpx.AsyncHTTPTransport(
            retries=2,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8, keepalive_expiry=30.0),
        )
        _shared_http_client = httpx.AsyncClient(
            transport=transport, trust_env=False, timeout=httpx.Timeout(120.0),
        )
    return _shared_http_client


async def close_http_client() -> None:
    global _shared_http_client
    if _shared_http_client is not None and not _shared_http_client.is_closed:
        await _shared_http_client.aclose()
    _shared_http_client = None


def _capability_key(base_url: str, model: str) -> tuple[str, str]:
    return (base_url.rstrip("/").lower(), model.strip().lower())


def _unsupported_fields_from_response(response: httpx.Response) -> set[str]:
    """Recognize OpenAI-compatible providers rejecting optional request fields."""
    if response.status_code not in {400, 422}:
        return set()
    try:
        detail = response.text.lower()
    except Exception:
        detail = ""
    compatibility_error = any(value in detail for value in (
        "unsupported", "not support", "unknown parameter", "unrecognized", "extra field", "unexpected",
    ))
    if not compatibility_error:
        return set()
    rejected = {field for field in _OPTIONAL_REQUEST_FIELDS if field.lower() in detail}
    if "json_object" in detail or "structured output" in detail:
        rejected.add("response_format")
    return rejected


def _response_error_detail(response: httpx.Response) -> str:
    try:
        detail = response.text.strip()
    except Exception:
        detail = ""
    return detail[-800:] if detail else "未提供错误详情"


def _retry_delay_value(retry_after: str | None, retry_index: int) -> float:
    if retry_after:
        try:
            return min(60.0, max(0.0, float(retry_after)))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(str(retry_after))
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return min(60.0, max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    base = min(8.0, 0.5 * (2 ** retry_index))
    return base + random.uniform(0.0, min(0.5, base * 0.25))


def _retry_delay(response: httpx.Response, retry_index: int) -> float:
    """Respect Retry-After, otherwise use capped exponential backoff with jitter."""
    headers = getattr(response, "headers", {})
    return _retry_delay_value(headers.get("Retry-After") if headers else None, retry_index)


async def _post_with_retries(
    url: str,
    api_key: str,
    payload: dict,
    timeout: float,
    capability_key: tuple[str, str],
    model: str,
) -> httpx.Response:
    """Post once on 429; retry transient server failures in this layer."""
    logger = logging.getLogger(__name__)
    for request_attempt in range(_MAX_HTTP_ATTEMPTS):
        for _capability_attempt in range(len(_OPTIONAL_REQUEST_FIELDS) + 1):
            unsupported = _unsupported_request_fields.get(capability_key, set())
            compatible_payload = {key: value for key, value in payload.items() if key not in unsupported}
            response = await _get_http_client().post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=compatible_payload,
                timeout=httpx.Timeout(timeout),
            )
            rejected_fields = _unsupported_fields_from_response(response) - unsupported
            if rejected_fields:
                _unsupported_request_fields.setdefault(capability_key, set()).update(rejected_fields)
                logger.info(
                    "AI endpoint rejected optional fields %s for model %s; retrying without them",
                    sorted(rejected_fields), model,
                )
                continue
            break

        # Rate limiting is coordinated by the outer batch scheduler. Retrying
        # here multiplies with its retries and can create a self-inflicted
        # request storm.
        if response.status_code == 429:
            return response
        if response.status_code not in _RETRYABLE_STATUS_CODES or request_attempt == _MAX_HTTP_ATTEMPTS - 1:
            return response
        delay = _retry_delay(response, request_attempt)
        logger.warning(
            "AI service returned %s; retrying in %.2fs (%s/%s)",
            response.status_code, delay, request_attempt + 2, _MAX_HTTP_ATTEMPTS,
        )
        await asyncio.sleep(delay)
    raise RuntimeError("AI 请求重试状态异常")


def _urllib_chat(url: str, api_key: str, payload: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    context = ssl.create_default_context(cafile=certifi.where())
    for attempt in range(_MAX_HTTP_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[-800:]
            if exc.code == 401:
                raise RuntimeError("DeepSeek 拒绝了 API Key（401），请检查密钥是否有效或是否已失效") from exc
            if exc.code == 429:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                raise AiRateLimitError(detail, _retry_delay_value(retry_after, 0)) from exc
            if exc.code in _RETRYABLE_STATUS_CODES and attempt < _MAX_HTTP_ATTEMPTS - 1:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = _retry_delay_value(retry_after, attempt)
                logging.getLogger(__name__).warning(
                    "urllib AI fallback returned %s; retrying in %.2fs (%s/%s)",
                    exc.code, delay, attempt + 2, _MAX_HTTP_ATTEMPTS,
                )
                time.sleep(delay)
                continue
            raise RuntimeError(f"AI 服务返回 {exc.code}：{detail}") from exc
    raise RuntimeError("AI 请求重试状态异常")


async def _chat_json(
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    prompt: str,
    *,
    operation: str,
    item_count: int = 1,
    timeout: float = 120.0,
    max_tokens: int | None = None,
) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "reasoning_effort": "none",
        "temperature": 0.1,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    last_error: Exception | None = None
    for attempt in range(2):
        started = time.perf_counter()
        attempt_payload = dict(payload)
        if attempt:
            if max_tokens is not None:
                # A larger ceiling does not spend tokens by itself. Giving the retry
                # enough room avoids paying for the same prompt twice only to receive
                # another truncated JSON document.
                attempt_payload["max_tokens"] = min(8192, max_tokens * 2)
            attempt_payload["messages"] = [
                payload["messages"][0],
                {"role": "user", "content": prompt + "\n上次返回为空或不是合法JSON。请只返回完整、紧凑的JSON对象。"},
            ]
        body: dict | None = None
        try:
            try:
                capability_key = _capability_key(base_url, model)
                response = await _post_with_retries(
                    url, api_key, attempt_payload, timeout, capability_key, model,
                )
                if response.status_code == 401:
                    raise RuntimeError("DeepSeek 拒绝了 API Key（401），请检查密钥是否有效或是否已失效")
                if response.status_code == 429:
                    raise AiRateLimitError(_response_error_detail(response), _retry_delay(response, 0))
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    raise RuntimeError(
                        f"AI 服务连续 {_MAX_HTTP_ATTEMPTS} 次返回 {response.status_code}："
                        f"{_response_error_detail(response)}"
                    )
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"AI 服务返回 {response.status_code}：{_response_error_detail(response)}"
                    )
                response.raise_for_status()
                body = response.json()
            except (httpx.ConnectError, httpx.ConnectTimeout) as primary_error:
                logging.getLogger(__name__).warning("httpx AI connection failed; trying urllib fallback: %r", primary_error)
                fallback_payload = {
                    key: value for key, value in attempt_payload.items()
                    if key not in _unsupported_request_fields.get(_capability_key(base_url, model), set())
                }
                body = await asyncio.to_thread(_urllib_chat, url, api_key, fallback_payload, timeout)

            choice = body.get("choices", [{}])[0]
            content = str((choice.get("message") or {}).get("content") or "").strip()
            finish_reason = str(choice.get("finish_reason") or "")
            if not content:
                raise ValueError(f"AI 返回了空内容（finish_reason={finish_reason or 'unknown'}）")
            if finish_reason == "length":
                raise ValueError("AI 输出达到 token 上限，JSON 未完成")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("AI 返回的 JSON 顶层不是对象")
            record_usage(
                operation, model, body.get("usage"),
                round((time.perf_counter() - started) * 1000), item_count=item_count,
            )
            return result
        except (json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            record_usage(
                operation, model, body.get("usage") if body else None,
                round((time.perf_counter() - started) * 1000), item_count=item_count,
                success=False, error=str(exc),
            )
            if attempt == 0:
                continue
        except Exception as exc:
            record_usage(
                operation, model, None, round((time.perf_counter() - started) * 1000),
                item_count=item_count, success=False, error=str(exc),
            )
            if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, urllib.error.URLError)):
                raise RuntimeError(f"无法连接 AI 服务：{exc}") from exc
            raise
    raise RuntimeError(f"AI 两次都没有返回可解析的 JSON：{last_error}") from last_error


async def review_sentence_boundaries(
    candidates: list[dict], api_key: str, base_url: str, model: str,
) -> set[str]:
    """Return uncertain boundary IDs whose right side belongs to the left sentence."""
    compact = [[row["id"], row["left"], row["right"]] for row in candidates]
    prompt = (
        "判断日语文本中的少量可疑句界。每项是[id,左侧文本,右侧文本]。"
        "只有左右确属同一句时才返回id；无法确定时保持分开。"
        "不要翻译、不要复制原文。输出{\"merge\":[\"id\"]}。\n"
        f"边界：{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
    )
    result = await _chat_json(
        api_key, base_url, model,
        "你只判断日语句界，保守处理。输出合法 JSON。",
        prompt, operation="sentence_boundary", item_count=len(candidates),
        timeout=45.0, max_tokens=min(256, max(64, len(candidates) * 8)),
    )
    allowed = {row["id"] for row in candidates}
    return {str(value) for value in result.get("merge", []) if str(value) in allowed}


async def explain_sentences(
    items: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    annotation_mode: str = "none",
    detail_mode: str = "full",
    context_before: list[str] | None = None,
) -> list[dict]:
    context_before = context_before or []
    context_suffix = (
        "\n前文仅供消歧，不得为前文生成结果："
        + json.dumps(context_before[-2:], ensure_ascii=False, separators=(",", ":"))
        if context_before else ""
    )
    if annotation_mode == "grammar":
        compact = [{"id": item["sentence"]["id"], "text": item["sentence"]["original"]} for item in items]
        prompt = (
            "只分析下列日语句子的语法和句法，不翻译、不解释文化背景、不生成词义。"
            "只列出真正影响理解的结构，每项必须明确给出语法结构名称。"
            "偏移使用原句Unicode字符的[start,end)，quote必须等于原文切片。"
            "输出固定结构：{\"results\":[{\"id\":\"句ID\","
            "\"annotations\":[[\"grammar\",start,end,\"quote\",\"语法结构\",\"简短句法说明\"]]}]}。\n"
            f"{context_suffix}\n输入：{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
        )
    elif detail_mode == "meaning":
        compact = [{"id": item["sentence"]["id"], "text": item["sentence"]["original"]} for item in items]
        prompt = (
            "批量理解下列连续日语句子。每句只给出一句简短、准确的简体中文句意。"
            "不得生成词义、语法或文化说明。"
            "输出固定结构：{\"results\":[{\"id\":\"句ID\",\"meaning\":\"句意\","
            "\"words\":[],\"annotations\":[]}]}。"
            f"{context_suffix}\n输入：{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
        )
    else:
        compact = []
        for item in items:
            compact.append({
                "id": item["sentence"]["id"],
                "text": item["sentence"]["original"],
                "unknown": [
                    [token["id"], token["surface"], token["lemma"], token["reading"], token["part_of_speech"]]
                    for token in item.get("unresolved_tokens", [])
                ],
            })
        prompt = (
            "批量处理句子。每句给出简短简体中文句意。unknown仅包含本地词典未命中的词；"
            "只为这些词返回简短语境义gloss和1至3条可复用日中词典义senses，禁止补充其他词。"
            "annotations必须为空数组。"
            "输出固定结构：{\"results\":[{\"id\":\"句ID\",\"meaning\":\"句意\","
            "\"words\":[[\"token_id\",\"gloss\",[\"sense\"]]],\"annotations\":[]}]}。\n"
            f"{context_suffix}\n输入：{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}"
        )
    result = await _chat_json(
        api_key, base_url, model, SYSTEM_PROMPT, prompt,
        operation="sentence_grammar" if annotation_mode == "grammar" else "sentence_explanation",
        item_count=len(items),
        max_tokens=(
            min(4096, max(2048, len(items) * 420))
            if annotation_mode == "grammar"
            else min(3072, max(
                1024,
                len(items) * 120 + sum(len(item["sentence"].get("original", "")) for item in items) * 2,
            )) if detail_mode == "meaning"
            else min(8192, max(
                2048,
                len(items) * 520 + sum(len(item.get("unresolved_tokens", [])) for item in items) * 120,
            ))
        ),
    )
    rows = result.get("results", [])
    return rows if isinstance(rows, list) else []


async def explain_sentence(
    sentence: dict,
    tokens: list[dict],
    dictionary_by_token: dict[str, dict | None],
    api_key: str,
    base_url: str,
    model: str,
) -> dict:
    """Compatibility wrapper for callers that still submit one sentence."""
    unresolved = [
        token for token in tokens
        if token.get("is_content") and not (dictionary_by_token.get(token["id"]) or {}).get("senses_zh")
    ]
    rows = await explain_sentences([{
        "sentence": sentence, "unresolved_tokens": unresolved,
    }], api_key, base_url, model)
    row = rows[0] if rows else {"id": sentence["id"], "meaning": "", "words": [], "annotations": []}
    return {
        "sentence_id": row.get("id", sentence["id"]),
        "meaning_zh": row.get("meaning", ""),
        "token_senses": [
            {"token_id": value[0], "gloss_zh": value[1], "fallback_senses_zh": value[2]}
            for value in row.get("words", []) if isinstance(value, list) and len(value) >= 3
        ],
        "annotations": [],
    }
