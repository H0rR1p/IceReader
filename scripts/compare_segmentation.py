"""Compare the pre-optimization and optimized sentence segmentation on a stored chapter."""

import argparse
import asyncio
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.ai import _chat_json
from backend.app import _chapter_sentence_spans, _segment_chapter
from backend.modules.library.repository import LIBRARY_PATH
from backend.models import ImportedChapter
from backend.nlp import CLOSERS, SentenceSpan, split_sentences
from backend.settings_store import _read


OLD_SENTENCE_END = set("。！？!?…")


def old_split_sentences(text: str) -> list[SentenceSpan]:
    spans: list[SentenceSpan] = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        should_break = char in OLD_SENTENCE_END or char == "\n"
        if char == "…" and index + 1 < len(text) and text[index + 1] == "…":
            index += 1
        if should_break:
            end = index + 1
            while end < len(text) and text[end] in CLOSERS:
                end += 1
            raw = text[start:end]
            if raw.strip():
                left = len(raw) - len(raw.lstrip())
                right = len(raw.rstrip())
                spans.append(SentenceSpan(start + left, start + right, raw.strip()))
            start = end
            index = end - 1
        index += 1
    if start < len(text):
        raw = text[start:]
        if raw.strip():
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            spans.append(SentenceSpan(start + left, start + right, raw.strip()))
    return spans


def batches(rows: list[dict], max_items: int = 80, max_chars: int = 8_000) -> list[list[dict]]:
    output: list[list[dict]] = []
    current: list[dict] = []
    size = 0
    for row in rows:
        if current and (len(current) >= max_items or size + len(row["text"]) > max_chars):
            output.append(current)
            current, size = [], 0
        current.append(row)
        size += len(row["text"])
    if current:
        output.append(current)
    return output


async def old_review(rows: list[dict], api_key: str, base_url: str, model: str) -> set[str]:
    compact = [{
        "id": row["id"], "text": row["text"], "can_merge_next": row["can_merge_next"],
    } for row in rows]
    prompt = (
        "检查这些按原文顺序排列的日语句子候选边界。只在标点、引号或换行造成同一句被错误切成两段时，"
        "把前一段 id 放入 merge_with_next。can_merge_next=false 的条目绝对不能合并。"
        "不要翻译或复制原文。输出 {\"merge_with_next\":[\"id\"]}。\n"
        f"候选：{json.dumps(compact, ensure_ascii=False)}"
    )
    result = await _chat_json(
        api_key, base_url, model,
        "你只负责审校日语句子边界。保守处理，无法确定时维持现有边界。输出合法 JSON。",
        prompt, operation="sentence_boundary_baseline", item_count=len(rows),
    )
    allowed = {row["id"] for row in rows if row["can_merge_next"]}
    return {str(value) for value in result.get("merge_with_next", []) if str(value) in allowed}


async def old_segment(chapter: ImportedChapter, api_key: str, base_url: str, model: str) -> list[SentenceSpan]:
    spans = old_split_sentences(chapter.text)
    ranges = [(block.start, block.end) for block in chapter.blocks if block.text and block.end > block.start]
    candidates = []
    for index, span in enumerate(spans):
        next_span = spans[index + 1] if index + 1 < len(spans) else None
        same_block = any(start <= span.start and next_span and next_span.end <= end for start, end in ranges)
        candidates.append({
            "id": f"{chapter.id}:{index}", "text": span.text,
            "can_merge_next": bool(next_span and same_block),
        })
    merge_ids: set[str] = set()
    for batch in batches(candidates):
        merge_ids.update(await old_review(batch, api_key, base_url, model))
    merged: list[SentenceSpan] = []
    index = 0
    while index < len(spans):
        start, end = spans[index].start, spans[index].end
        while index < len(spans) - 1 and candidates[index]["id"] in merge_ids:
            index += 1
            end = spans[index].end
        merged.append(SentenceSpan(start, end, chapter.text[start:end]))
        index += 1
    return merged


def load_longest_chapter(database_path: Path) -> ImportedChapter:
    with sqlite3.connect(database_path) as connection:
        rows = [json.loads(row[0]) for row in connection.execute(
            """
            SELECT chapter.payload
            FROM records AS chapter
            WHERE chapter.table_name = 'chapters'
              AND EXISTS (
                SELECT 1 FROM records AS sentence
                WHERE sentence.table_name = 'sentences'
                  AND json_extract(sentence.payload, '$.chapter_id') = chapter.record_key
              )
            """
        )]
    if not rows:
        raise RuntimeError("项目中没有带旧版已保存切分结果的章节")
    row = max(rows, key=lambda value: len(value.get("text", "")))
    return ImportedChapter(
        id=row["id"], title=row.get("title", ""), order=int(row.get("order", 0)),
        text=row.get("text", ""), blocks=row.get("blocks", []),
    )


def load_stored_sentences(database_path: Path, chapter_id: str) -> list[SentenceSpan]:
    with sqlite3.connect(database_path) as connection:
        rows = [json.loads(row[0]) for row in connection.execute(
            """
            SELECT payload FROM records
            WHERE table_name = 'sentences' AND json_extract(payload, '$.chapter_id') = ?
            ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER)
            """,
            (chapter_id,),
        )]
    return [
        SentenceSpan(int(row["start"]), int(row["end"]), str(row["original"])) for row in rows
    ]


def first_difference(left: list[SentenceSpan], right: list[SentenceSpan]) -> dict | None:
    for index in range(max(len(left), len(right))):
        old = left[index] if index < len(left) else None
        new = right[index] if index < len(right) else None
        if old is None or new is None or (old.start, old.end, old.text) != (new.start, new.end, new.text):
            return {
                "index": index,
                "old": None if old is None else {"start": old.start, "end": old.end, "text": old.text},
                "new": None if new is None else {"start": new.start, "end": new.end, "text": new.text},
            }
    return None


def normalized_content(spans: list[SentenceSpan]) -> str:
    return re.sub(r"\s+", "", "".join(span.text for span in spans))


def offsets_are_valid(text: str, spans: list[SentenceSpan]) -> bool:
    previous_end = 0
    for span in spans:
        if span.start < previous_end or span.end < span.start:
            return False
        if text[span.start:span.end].strip() != span.text:
            return False
        previous_end = span.end
    return True


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-only", action="store_true", help="compare only the local candidate split")
    parser.add_argument("--live-ai", action="store_true", help="rerun the old AI strategy instead of using stored results")
    parser.add_argument("--database", type=Path, default=LIBRARY_PATH)
    args = parser.parse_args()
    chapter = load_longest_chapter(args.database)
    settings = _read()
    api_key = str(settings.get("api_key") or "")
    base_url = str(settings.get("base_url") or "https://api.deepseek.com")
    model = str(settings.get("model") or "deepseek-chat")
    if args.local_only:
        old = old_split_sentences(chapter.text)
        optimized = _chapter_sentence_spans(chapter)
        source = "local-baseline"
    elif args.live_ai:
        if not api_key:
            raise RuntimeError("本地项目尚未保存 API Key，无法执行优化前后的 AI 对照")
        old = await old_segment(chapter, api_key, base_url, model)
        optimized_chapter, warning = await _segment_chapter(
            chapter.model_copy(deep=True), api_key, base_url, model, asyncio.Semaphore(1),
        )
        if warning:
            raise RuntimeError(warning)
        optimized = [
            SentenceSpan(row.start, row.end, row.original) for row in optimized_chapter.sentences
        ]
        source = "ai-baseline-vs-optimized"
    else:
        old = load_stored_sentences(args.database, chapter.id)
        if not old:
            raise RuntimeError("最长章节没有保存的旧版切分结果，请使用 --live-ai")
        optimized_chapter, warning = await _segment_chapter(
            chapter.model_copy(deep=True), api_key, base_url, model, asyncio.Semaphore(1),
        )
        if warning:
            raise RuntimeError(warning)
        optimized = [
            SentenceSpan(row.start, row.end, row.original) for row in optimized_chapter.sentences
        ]
        source = "stored-baseline-vs-optimized"
    difference = first_difference(old, optimized)
    local_candidates = _chapter_sentence_spans(chapter)
    text_ranges = [(block.start, block.end) for block in chapter.blocks if block.text and block.end > block.start]
    optimized_review_items = sum(
        1 for index, span in enumerate(local_candidates[:-1])
        if span.uncertain_after and any(
            start <= span.start and local_candidates[index + 1].end <= end
            for start, end in text_ranges
        )
    )
    content_preserved = normalized_content(old) == normalized_content(optimized)
    valid_offsets = offsets_are_valid(chapter.text, optimized)
    safe_change = content_preserved and valid_offsets and len(optimized) <= len(old)
    print(json.dumps({
        "chapter_id": chapter.id,
        "chapter_title": chapter.title,
        "characters": len(chapter.text),
        "blocks": len(chapter.blocks),
        "source": source,
        "old_sentences": len(old),
        "optimized_sentences": len(optimized),
        "old_ai_boundary_items": len(old_split_sentences(chapter.text)),
        "optimized_ai_boundary_items": optimized_review_items,
        "identical": difference is None,
        "content_preserved": content_preserved,
        "offsets_valid": valid_offsets,
        "expected_visual_line_joins": max(0, len(old) - len(optimized)),
        "safe_change": safe_change,
        "first_difference": difference,
    }, ensure_ascii=False, indent=2))
    return 0 if safe_change else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
