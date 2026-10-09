"""Read-only source references for analysis, within the library boundary."""
import json
from fastapi import HTTPException

from . import repository


def get_sentence_source(user_id: str, sentence_id: str) -> tuple[dict, dict] | None:
    with repository._connect(user_id) as connection:
        row = connection.execute("""SELECT s.payload,c.payload FROM records s
            JOIN records c ON c.owner_user_id=s.owner_user_id AND c.table_name='chapters'
              AND c.record_key=json_extract(s.payload,'$.chapter_id')
            JOIN records b ON b.owner_user_id=c.owner_user_id AND b.table_name='books'
              AND b.record_key=json_extract(c.payload,'$.bookId')
            WHERE s.owner_user_id=? AND s.table_name='sentences' AND s.record_key=?""",
            (user_id, sentence_id)).fetchone()
    return (json.loads(row[0]), json.loads(row[1])) if row else None


def get_book_source(user_id: str, book_id: str) -> tuple[dict, list[dict]]:
    with repository._connect(user_id) as connection:
        row = connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='books' AND record_key=?", (user_id, book_id)).fetchone()
        if row is None:
            raise HTTPException(404, '书籍不存在或无权访问')
        chapters = connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='chapters' AND json_extract(payload,'$.bookId')=? ORDER BY CAST(json_extract(payload,'$.order') AS INTEGER),record_key", (user_id, book_id)).fetchall()
    return json.loads(row[0]), [json.loads(item[0]) for item in chapters]


def mark_context_stale(user_id: str,sentence_ids: list[str],reason: str) -> None:
    if not sentence_ids: return
    with repository._connect(user_id) as connection:
        for sentence_id in sentence_ids:
            row=connection.execute("SELECT payload FROM records WHERE owner_user_id=? AND table_name='sentences' AND record_key=?",(user_id,sentence_id)).fetchone()
            if row is None: continue
            payload=json.loads(row[0])
            payload.update(translation_quality_status='needs_review',explanation_status='idle',analysis_stale_reason=reason)
            connection.execute("UPDATE records SET payload=? WHERE owner_user_id=? AND table_name='sentences' AND record_key=?",(json.dumps(payload,ensure_ascii=False),user_id,sentence_id))


def _scene_start(chapter: dict, target_start: int) -> int:
    boundaries = [0]
    for block in chapter.get("blocks", []):
        if block.get("type") in {"heading", "page-break", "separator"}:
            end = int(block.get("end", 0))
            if 0 <= end <= target_start:
                boundaries.append(end)
    for offset in chapter.get("scene_boundaries", []):
        if isinstance(offset, int) and 0 <= offset <= target_start:
            boundaries.append(offset)
    return max(boundaries)


def load_context_sources(user_id: str, sentence_ids: list[str], *,
                         preceding_sentences: int, cross_chapter: bool) -> dict[str, dict]:
    """Return owned targets and preceding originals in one SQLite read snapshot.

    Unknown IDs are omitted to support the separate transient text API. An
    existing inaccessible ID fails without returning any foreign metadata.
    """
    if preceding_sentences not in {0, 2, 4, 8}:
        raise ValueError("Unsupported discourse window")
    if not sentence_ids or len(sentence_ids) > 120 or len(set(sentence_ids)) != len(sentence_ids):
        raise HTTPException(422, "请选择1至120个不重复句子")
    if not repository.LIBRARY_PATH.resolve().exists():
        return {}
    result = {}
    with repository._connect(user_id) as connection:
        connection.execute("BEGIN")
        for sentence_id in sentence_ids:
            row = connection.execute(
                """SELECT s.payload,c.payload,b.record_key FROM records s
                   JOIN records c ON c.owner_user_id=s.owner_user_id AND c.table_name='chapters'
                     AND c.record_key=json_extract(s.payload,'$.chapter_id')
                   JOIN records b ON b.owner_user_id=c.owner_user_id AND b.table_name='books'
                     AND b.record_key=json_extract(c.payload,'$.bookId')
                   WHERE s.owner_user_id=? AND s.table_name='sentences' AND s.record_key=?""",
                (user_id, sentence_id),
            ).fetchone()
            if not row:
                existing = connection.execute("SELECT 1 FROM records WHERE table_name='sentences' AND record_key=? LIMIT 1", (sentence_id,)).fetchone()
                if existing:
                    raise HTTPException(404, "句子不存在或不属于当前账号")
                continue
            stored, chapter, book_id = json.loads(row[0]), json.loads(row[1]), str(row[2])
            lower = _scene_start(chapter, int(stored["start"]))
            rows = connection.execute(
                """SELECT s.payload FROM records s WHERE s.owner_user_id=? AND s.table_name='sentences'
                   AND json_extract(s.payload,'$.chapter_id')=?
                   AND CAST(json_extract(s.payload,'$.start') AS INTEGER)>=?
                   AND (CAST(json_extract(s.payload,'$.start') AS INTEGER)<?
                     OR (CAST(json_extract(s.payload,'$.start') AS INTEGER)=? AND s.record_key<?))
                   ORDER BY CAST(json_extract(s.payload,'$.start') AS INTEGER) DESC,s.record_key DESC LIMIT ?""",
                (user_id, stored["chapter_id"], lower, int(stored["start"]), int(stored["start"]), sentence_id, preceding_sentences),
            ).fetchall()
            previous = [(json.loads(value[0]), chapter) for value in reversed(rows)]
            if cross_chapter and lower == 0 and not chapter.get("context_break_before") and len(previous) < preceding_sentences:
                missing = preceding_sentences - len(previous)
                previous_chapter_row = connection.execute(
                    """SELECT payload FROM records WHERE owner_user_id=? AND table_name='chapters'
                       AND json_extract(payload,'$.bookId')=?
                       AND CAST(json_extract(payload,'$.order') AS INTEGER)<?
                       ORDER BY CAST(json_extract(payload,'$.order') AS INTEGER) DESC LIMIT 1""",
                    (user_id, book_id, int(chapter.get("order", 0))),
                ).fetchone()
                preceding_chapter = json.loads(previous_chapter_row[0]) if previous_chapter_row else None
                prior = connection.execute(
                    """SELECT s.payload,c.payload FROM records s JOIN records c
                       ON c.owner_user_id=s.owner_user_id AND c.table_name='chapters'
                         AND c.record_key=json_extract(s.payload,'$.chapter_id')
                       WHERE s.owner_user_id=? AND s.table_name='sentences'
                         AND json_extract(c.payload,'$.bookId')=? AND c.record_key=?
                         AND CAST(json_extract(s.payload,'$.start') AS INTEGER)>=?
                       ORDER BY CAST(json_extract(s.payload,'$.start') AS INTEGER) DESC,s.record_key DESC LIMIT ?""",
                    (user_id, book_id, preceding_chapter["id"],
                     _scene_start(preceding_chapter, len(preceding_chapter.get("text", ""))), missing),
                ).fetchall() if preceding_chapter else []
                previous = [(json.loads(value[0]), json.loads(value[1])) for value in reversed(prior)] + previous
            result[sentence_id] = {"sentence": stored, "chapter": chapter, "book_id": book_id,
                                   "preceding": previous}
    return result
