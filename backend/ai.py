import json
from collections.abc import Iterable

import httpx


SYSTEM_PROMPT = """你是一名严谨的日语 N1 语法分析员。输出必须是简体中文和合法 JSON。
你的职责仅限于检查句子结构，并解释真正影响 N1 学习者理解的语法、语气、省略/指代和文化背景。
不要编写词典释义，不要改写日文原文，不要为每个基础助词写注释。无法可靠判断时省略注释。
所有 anchor_start/anchor_end 都是对应句子中的 Unicode 字符偏移，必须满足 [start,end)，quote 必须严格等于原文切片。
"""


def chunks(items: list[dict], size: int = 60) -> Iterable[list[dict]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


async def enrich_batch(
    sentences: list[dict],
    tokens_by_sentence: dict[str, list[dict]],
    api_key: str,
    base_url: str,
    model: str,
    known_lexeme_keys: set[str],
) -> dict:
    compact_sentences = []
    for sentence in sentences:
        tokens = []
        for token in tokens_by_sentence[sentence["id"]]:
            if not token["is_content"]:
                continue
            tokens.append({
                "token_id": token["id"],
                "surface": token["surface"],
                "lemma": token["lemma"],
                "reading": token["reading"],
                "part_of_speech": token["part_of_speech"],
            })
        compact_sentences.append({
            "sentence_id": sentence["id"],
            "original": sentence["original"],
            "tokens": tokens,
        })

    schema_hint = {
        "sentences": [{
            "sentence_id": "string",
            "annotations": [{
                "type": "grammar|pragmatics|ellipsis|culture",
                "anchor_start": 0,
                "anchor_end": 1,
                "quote": "原文严格切片",
                "explanation_zh": "面向 N1 学习者的简体中文说明",
            }],
        }],
    }
    user_prompt = (
        "检查以下候选句。只返回有实际学习价值的语法、语气、省略、指代或文化注释；"
        "不得生成词义或词典条目。没有需要说明的句子返回空 annotations。\n"
        f"输出结构示例：{json.dumps(schema_hint, ensure_ascii=False)}\n"
        f"输入：{json.dumps(compact_sentences, ensure_ascii=False)}"
    )
    url = base_url.rstrip("/") + "/chat/completions"
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        if response.status_code == 401:
            raise RuntimeError("DeepSeek 拒绝了 API Key（401），请检查密钥是否有效或是否已失效")
        response.raise_for_status()
        body = response.json()
    content = body["choices"][0]["message"]["content"]
    return json.loads(content)
