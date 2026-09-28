"""Offline benchmark for the full-book translation scheduler.

The script uses the longest chapter currently stored by IceReader and the
median latency of successful historical API requests.  It never calls an AI
service and never writes to the library.  Results are projections whose main
purpose is to compare scheduler configurations on the same real workload.
"""

from __future__ import annotations

import json
import math
import sqlite3
import statistics
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def database_candidates(name: str) -> list[Path]:
    rows = [ROOT / "data" / name]
    rows.extend(ROOT.glob(f"build/**/{name}"))
    return [path for path in dict.fromkeys(rows) if path.is_file()]


def longest_chapter() -> tuple[Path, str, str, list[str]]:
    best: tuple[Path, str, str, list[str]] | None = None
    candidates = database_candidates("library.sqlite3")
    # The packaged library preserves the user's complete data and is normally
    # much larger than development/test copies.  Reading only the largest copy
    # avoids repeating the expensive JSON expression scan several times.
    for path in sorted(candidates, key=lambda value: value.stat().st_size, reverse=True)[:1]:
        with sqlite3.connect(path) as connection:
            try:
                row = connection.execute(
                    """SELECT json_extract(s.payload, '$.chapter_id'),
                              COALESCE(json_extract(c.payload, '$.title'), ''), COUNT(*)
                       FROM records AS s
                       LEFT JOIN records AS c
                         ON c.table_name = 'chapters'
                        AND c.record_key = json_extract(s.payload, '$.chapter_id')
                       WHERE s.table_name = 'sentences'
                       GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 1"""
                ).fetchone()
            except sqlite3.DatabaseError:
                continue
            if not row:
                continue
            chapter_id, title, _count = row
            texts = [
                str(json.loads(value[0]).get("original", ""))
                for value in connection.execute(
                    """SELECT payload FROM records
                       WHERE table_name = 'sentences'
                         AND json_extract(payload, '$.chapter_id') = ?
                       ORDER BY CAST(json_extract(payload, '$.start') AS INTEGER), record_key""",
                    (chapter_id,),
                )
            ]
            candidate = (path, str(chapter_id), str(title), texts)
            if best is None or len(texts) > len(best[3]):
                best = candidate
    if best is None:
        raise SystemExit("没有找到可用于基准的已切分章节。")
    return best


def historical_latency() -> tuple[Path | None, dict[int, float]]:
    best_path: Path | None = None
    best_rows: list[tuple[int, int]] = []
    for path in database_candidates("ai.sqlite3"):
        with sqlite3.connect(path) as connection:
            try:
                rows = connection.execute(
                    """SELECT duration_ms, item_count FROM usage
                       WHERE operation = 'sentence_explanation'
                         AND success = 1 AND duration_ms > 0 AND item_count > 0"""
                ).fetchall()
            except sqlite3.DatabaseError:
                continue
        if len(rows) > len(best_rows):
            best_path, best_rows = path, [(int(a), int(b)) for a, b in rows]
    if not best_rows:
        return None, {1: 3000.0, 8: 6100.0}
    grouped: dict[int, list[int]] = {}
    for duration, count in best_rows:
        grouped.setdefault(count, []).append(duration)
    return best_path, {count: statistics.median(values) for count, values in grouped.items()}


def batch_sizes(texts: list[str], maximum: int = 12, token_ceiling: int = 2500) -> list[int]:
    sizes: list[int] = []
    current_count = 0
    current_tokens = 0
    for text in texts:
        source = math.ceil(len(text) * 1.15)
        estimate = source + max(36, math.ceil(len(text) * 0.65))
        if current_count and (current_count >= maximum or current_tokens + estimate > token_ceiling):
            sizes.append(current_count)
            current_count = 0
            current_tokens = 0
        current_count += 1
        current_tokens += estimate
    if current_count:
        sizes.append(current_count)
    return sizes


def latency_for(size: int, medians: dict[int, float]) -> float:
    if size in medians:
        return medians[size]
    known = sorted(medians)
    lower = max((value for value in known if value < size), default=known[0])
    upper = min((value for value in known if value > size), default=known[-1])
    if lower != upper:
        ratio = (size - lower) / (upper - lower)
        return medians[lower] + ratio * (medians[upper] - medians[lower])
    if len(known) == 1:
        return medians[lower]
    previous = known[-2] if size > known[-1] else known[1]
    slope = max(0.0, (medians[lower] - medians[previous]) / (lower - previous))
    return max(500.0, medians[lower] + (size - lower) * slope)


def scheduled_ms(sizes: list[int], medians: dict[int, float], concurrency: int) -> float:
    workers = [0.0] * concurrency
    for size in sizes:
        worker = min(range(concurrency), key=workers.__getitem__)
        workers[worker] += latency_for(size, medians)
    return max(workers, default=0.0)


def duration(value_ms: float) -> str:
    seconds = round(value_ms / 1000)
    return f"{seconds // 60}分{seconds % 60:02d}秒"


def main() -> None:
    library_path, chapter_id, title, texts = longest_chapter()
    usage_path, medians = historical_latency()
    old_sizes = [min(8, len(texts) - index) for index in range(0, len(texts), 8)]
    new_sizes = batch_sizes(texts)
    old_ms = scheduled_ms(old_sizes, medians, 1)

    print("冰读后台翻译离线基准（不调用 API）")
    print(f"章节：{title or chapter_id}")
    print(f"句数：{len(texts)}；来源：{library_path}")
    print(f"历史延迟：{usage_path or '内置保守值'}")
    print(f"旧策略：固定最多8句、串行、{len(old_sizes)}批，预计 {duration(old_ms)}")
    print(f"新策略：最多12句/约2500 token、{len(new_sizes)}批")
    for concurrency in (1, 4, 6):
        value = scheduled_ms(new_sizes, medians, concurrency)
        speedup = old_ms / value if value else 0
        print(f"  并发{concurrency}：预计 {duration(value)}，相对旧策略 {speedup:.2f}x")
    print("说明：这是用真实章节分布与历史请求中位延迟得到的调度估算；网络限流会改变实测值。")


if __name__ == "__main__":
    main()
