import json
from collections.abc import Iterable

import httpx


SYSTEM_PROMPT = """你是一名严谨的日语 N1 精读编辑。输出必须是简体中文和合法 JSON。
你的任务类似 Satori Reader 的人工注释：忠实翻译，结合语境选择词义，只解释真正影响 N1 学习者理解的语法、语气、省略/指代和文化背景。
不要改写日文原文，不要虚构辞书来源或文化事实，不要为每个基础助词写注释。无法可靠判断时省略注释。
所有 anchor_start/anchor_end 都是对应句子中的 Unicode 字符偏移，必须满足 [start,end)，quote 必须严格等于原文切片。
"""


def chunks(items: list[dict], size: int = 18) -> Iterable[list[dict]]:
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
                "lexeme_key": token["lexeme_key"],
                "needs_dictionary_entry": token["lexeme_key"] not in known_lexeme_keys,
            })
        compact_sentences.append({
            "sentence_id": sentence["id"],
            "original": sentence["original"],
            "tokens": tokens,
        })

    schema_hint = {
        "sentences": [{
            "sentence_id": "string",
            "translation_zh": "简体中文忠实译文",
            "context_senses": [{"token_id": "string", "gloss_zh": "当前句中简短含义"}],
            "annotations": [{
                "type": "grammar|pragmatics|ellipsis|culture",
                "anchor_start": 0,
                "anchor_end": 1,
                "quote": "原文严格切片",
                "explanation_zh": "面向 N1 学习者的简体中文说明",
            }],
        }],
        "lexemes": [{
            "lexeme_key": "仅为 needs_dictionary_entry=true 的词返回",
            "lemma": "词典形",
            "reading": "片假名",
            "part_of_speech": "词性",
            "senses_zh": ["日中词典式简体中文释义，不包含当前句解释"],
        }],
    }
    user_prompt = (
        "处理以下连续句子。必须覆盖每个 sentence_id，并为每个内容词返回 context_senses。"
        "只为 needs_dictionary_entry=true 的词生成 lexemes；同一 lexeme_key 只返回一次。\n"
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
