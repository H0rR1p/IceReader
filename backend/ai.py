import json

import httpx


SYSTEM_PROMPT = """你是一名严谨的日语 N1 精读编辑。输出必须是合法 JSON，中文必须使用简体中文。
不得改写日文原文，不得虚构词典来源。词典已命中的词只能从给定候选义中选择当前语境义；只有 dictionary_senses 为空时才可补充词义。
句意用于帮助理解，不追求文学精翻。语法注释只解释真正影响 N1 学习者理解的语法、语气、省略、指代或文化背景。
"""


async def _chat_json(api_key: str, base_url: str, model: str, system: str, prompt: str, timeout: float = 120.0) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        if response.status_code == 401:
            raise RuntimeError("DeepSeek 拒绝了 API Key（401），请检查密钥是否有效或是否已失效")
        response.raise_for_status()
        body = response.json()
    return json.loads(body["choices"][0]["message"]["content"])


async def review_sentence_boundaries(
    candidates: list[dict], api_key: str, base_url: str, model: str,
) -> set[str]:
    """Return candidate IDs whose following candidate belongs to the same sentence."""
    compact = [{
        "id": row["id"],
        "text": row["text"],
        "can_merge_next": bool(row.get("can_merge_next")),
    } for row in candidates]
    prompt = (
        "检查这些按原文顺序排列的日语句子候选边界。只在标点、引号或换行造成同一句被错误切成两段时，"
        "把前一段 id 放入 merge_with_next。can_merge_next=false 的条目绝对不能合并。"
        "不要翻译或复制原文。输出 {\"merge_with_next\":[\"id\"]}。\n"
        f"候选：{json.dumps(compact, ensure_ascii=False)}"
    )
    result = await _chat_json(
        api_key, base_url, model,
        "你只负责审校日语句子边界。保守处理，无法确定时维持现有边界。输出合法 JSON。",
        prompt,
    )
    allowed = {row["id"] for row in candidates if row.get("can_merge_next")}
    return {str(value) for value in result.get("merge_with_next", []) if str(value) in allowed}


async def explain_sentence(
    sentence: dict,
    tokens: list[dict],
    dictionary_by_token: dict[str, dict | None],
    api_key: str,
    base_url: str,
    model: str,
) -> dict:
    content_tokens = []
    for token in tokens:
        if not token.get("is_content"):
            continue
        entry = dictionary_by_token.get(token["id"])
        content_tokens.append({
            "token_id": token["id"],
            "surface": token["surface"],
            "lemma": token["lemma"],
            "reading": token["reading"],
            "part_of_speech": token["part_of_speech"],
            "dictionary_senses": entry.get("senses_zh", []) if entry else [],
            "dictionary_source": entry.get("source", "") if entry else "",
        })
    schema = {
        "sentence_id": sentence["id"],
        "meaning_zh": "当前句的简体中文句意",
        "token_senses": [{
            "token_id": "必须覆盖每个输入内容词",
            "gloss_zh": "当前语境中的简短含义",
            "fallback_senses_zh": ["仅 dictionary_senses 为空时，给出可复用的日中词典式释义"],
        }],
        "annotations": [{
            "type": "grammar|pragmatics|ellipsis|culture",
            "anchor_start": 0,
            "anchor_end": 1,
            "quote": "必须严格等于原文切片",
            "explanation_zh": "面向 N1 学习者的简体中文说明",
        }],
    }
    prompt = (
        "只处理这一句话。先理解整句，再为每个输入内容词返回 token_senses。"
        "有 dictionary_senses 时，gloss_zh 必须选择或简短归纳其中符合语境的义项，fallback_senses_zh 必须为空。"
        "没有 dictionary_senses 时，才由你给出语境义和 1 至 3 条可复用释义。"
        "anchor_start/anchor_end 使用原句 Unicode 字符偏移 [start,end)。\n"
        f"输出结构：{json.dumps(schema, ensure_ascii=False)}\n"
        f"原句：{json.dumps(sentence, ensure_ascii=False)}\n"
        f"词元：{json.dumps(content_tokens, ensure_ascii=False)}"
    )
    return await _chat_json(api_key, base_url, model, SYSTEM_PROMPT, prompt)
