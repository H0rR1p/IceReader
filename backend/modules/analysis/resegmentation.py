"""Cancelable chapter-at-a-time orchestration for deliberate resegmentation."""
import asyncio
import threading

from fastapi import HTTPException

from ...models import ImportedChapter
from ..jobs import runner
from ..library import resegmentation_store as store
from ..linguistics.rules import rule_version
from .service import _chapter_sentence_spans, _local_analysis


KIND = "resegmentation-v1"
_replay_lock=threading.RLock()


def prepare_request(user_id: str, book_id: str, scope: str, chapter_id: str | None = None,
                    expected_revision: int | None = None, expected_text_hash: str | None = None) -> dict:
    if scope not in {"book", "chapter"} or scope == "chapter" and not chapter_id or scope == "book" and chapter_id:
        raise HTTPException(422, "请选择全书或一个指定章节进行重新切分")
    try:
        chapters = store.owned_chapters(user_id, book_id, chapter_id)
    except store.ResegmentationNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    if scope == "book" and (expected_revision is not None or expected_text_hash is not None):
        raise HTTPException(422, "全书重切由服务端冻结每章版本，请勿提供单章版本")
    targets = []
    for chapter in chapters:
        revision, digest = int(chapter.get("analysis_revision", 1)), store.text_hash(chapter.get("text", ""))
        if expected_revision is not None and expected_revision != revision or expected_text_hash is not None and expected_text_hash != digest:
            raise HTTPException(409, "章节正文或切分版本已变化，请刷新后重试")
        targets.append({"chapter_id": chapter["id"], "revision": revision, "text_hash": digest})
    if not targets:
        raise HTTPException(422, "书籍没有可重切章节")
    return {"book_id": book_id, "scope": scope, "chapter_id": chapter_id,
            "targets": targets, "rules_version": rule_version()}


def _build_stage(user_id: str, request: dict, target: dict, job_id: str) -> dict:
    chapter = store.owned_chapters(user_id, request["book_id"], target["chapter_id"])[0]
    if store.text_hash(chapter.get("text", "")) != target["text_hash"]:
        raise store.ResegmentationConflict("正文在任务期间改变，停止重切")
    imported = ImportedChapter(id=chapter["id"], title=chapter.get("title", ""), order=chapter.get("order", 0),
                               text=chapter.get("text", ""), blocks=chapter.get("blocks", []),
                               ruby=chapter.get("ruby", []))
    boundaries = _chapter_sentence_spans(imported)
    sentences, tokens = _local_analysis(chapter["id"], imported.text, boundaries)
    stage=store.stage_generation(user_id, chapter["id"], job_id, target["revision"], target["text_hash"],
        [item.model_dump() for item in sentences], [item.model_dump() for item in tokens], request["rules_version"])
    payload=store.stage_payload(user_id,stage['generation_id'])
    if payload['status']=='staged' and 'structures' not in payload:
        from ..linguistics.service import analyze_sentence
        by_sentence={item['id']:[] for item in payload['sentences']}
        for token in payload['tokens']: by_sentence[token['sentence_id']].append(token)
        structures=[analyze_sentence(item['id'],item['original'],by_sentence[item['id']],payload['revision']) for item in payload['sentences']]
        store.save_stage_structures(user_id,stage['generation_id'],structures)
    return stage


async def run_resegmentation(control, request: dict, checkpoint: dict) -> dict:
    if request.get("rules_version") != rule_version():
        raise store.ResegmentationConflict("规则版本已变化，请保留旧任务记录并创建新重切任务")
    completed = dict(checkpoint.get("completed", {}))
    targets = request["targets"]
    for target in targets:
        await control.check()
        chapter_id = target["chapter_id"]
        if chapter_id in completed:
            continue
        # Threads finish before cancellation is checked. Stages remain in the
        # DB after cancel or process loss, and active results are atomic.
        stage = await asyncio.to_thread(_build_stage, control.user_id, request, target, control.job_id)
        await control.progress(len(completed), len(targets), {"completed": completed, "staged_generation": stage["generation_id"]},
                               {"current_chapter_id": chapter_id, "staged": stage})
        await control.check()
        activated = await asyncio.to_thread(store.activate_generation, control.user_id, stage["generation_id"])
        await asyncio.to_thread(replay_events,control.user_id)
        completed[chapter_id] = activated
        await control.progress(len(completed), len(targets), {"completed": completed},
                               {"chapters": list(completed.values()), "completed": len(completed), "total": len(targets)})
    return {"book_id": request["book_id"], "scope": request["scope"], "chapters": list(completed.values()),
            "completed": len(completed), "total": len(targets), "rules_version": request["rules_version"]}


runner.register(KIND, run_resegmentation)


def replay_events(user_id: str | None=None):
    with _replay_lock:
        while events:=store.pending_events(user_id):
            _replay_batch(events)


def _replay_batch(events):
    from ..book_memory.service import rebase_sources
    from ..linguistics import repository as linguistics_store
    for event in events:
        owner,payload=event['user_id'],event['payload']
        try:
            rebase_sources(owner,payload)
            current=store.owned_chapters(owner,payload['book_id'],payload['chapter_id'])[0]
            if current.get('active_generation')==payload['new_generation'] and int(current.get('analysis_revision',1))==payload['new_revision']:
                old_ids=[item['old_id'] for item in payload['sentence_anchors']]
                linguistics_store.invalidate_sentences(owner,old_ids)
                stage=store.stage_payload(owner,payload['new_generation'])
                for result in stage.get('structures',[]):
                    linguistics_store.put_analysis(owner,result['sentence_id'],result)
            from ..sync.emission import after_commit
            after_commit('source_generation',owner,'resegmentation-worker',payload['new_generation'])
            after_commit('book_memory',owner,'resegmentation-worker',payload['book_id'])
        except (HTTPException,store.ResegmentationNotFound) as exc:
            if isinstance(exc,HTTPException) and exc.status_code!=404: raise
        store.acknowledge_event(owner,event['id'])
