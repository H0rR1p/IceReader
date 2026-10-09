"""Compact, deterministic prompt dependencies shared by manual/background work."""
from .context import dependency_hash


def visible_batch_hash(items: list[dict], annotation_mode: str, detail_mode: str) -> str:
    """Hash everything visible to AI; IDs and generation masks are transport data.

Cached targets remain read-only original context. Dropping their text after a
cache lookup must not silently change the discourse used for the other targets.
"""
    return dependency_hash({"annotation_mode": annotation_mode, "detail_mode": detail_mode,
        "items": [{"text": row["sentence"]["original"],
                   "unknown": [[token["surface"], token["lemma"], token["reading"], token["part_of_speech"],
                                token.get("start"), token.get("end")]
                               for token in row.get("unresolved_tokens", [])],
                   "known": [value[1:] for value in row.get("known_tokens", [])],
                   "context": row.get("analysis_context", {}),
                   "learning_units": [value[1:] for value in row.get("learning_units", [])],
                   "manifest": row.get("analysis_manifest", {})}
                  for row in items]})


def compact_item(row: dict, annotation_mode: str, detail_mode: str) -> dict:
    value = {"id": row["sentence"]["id"], "text": row["sentence"]["original"]}
    if not row.get("generate", True):
        value["context_only"] = True
    if row.get("analysis_context"):
        value["context"] = row["analysis_context"]
    if annotation_mode != "grammar" and detail_mode != "meaning":
        value["unknown"] = [[token["id"], token["surface"], token["lemma"], token["reading"], token["part_of_speech"],
                             token.get("start"), token.get("end")]
                            for token in row.get("unresolved_tokens", [])]
        value["known"] = row.get("known_tokens", [])
    return value


def lexical_tokens(tokens, spans: list[dict]) -> list:
    """Keep dictionary heads while teaching auxiliaries inside local spans.

    Copies may have canonical lemma/reading; raw token objects/IDs stay intact.
    Unknown canonical readings remain unknown instead of borrowing stem kana.
    """
    heads, internal = {}, set()
    for span in spans:
        if span.get("kind") != "morphology" or span.get("status") == "unknown" or not span.get("token_ids"):
            continue
        ids = span["token_ids"]
        heads[ids[0]] = span
        internal.update(ids[1:])
    result = []
    for token in tokens:
        primary_pos = token.part_of_speech.split("-", 1)[0]
        covering = next((span for span in spans if span.get("kind") == "morphology" and span.get("status") != "unknown"
                         and span["start"] <= token.start and token.end <= span["end"]), None)
        is_internal = covering is not None and token.start > covering["start"]
        if not token.is_content or token.id in internal or is_internal or token.role == "grammatical" or primary_pos in {"助詞", "助動詞", "補助記号", "記号", "空白"}:
            continue
        span = heads.get(token.id) or (covering if covering and token.start == covering["start"] else None)
        lemma = str(span.get("lemma") or token.lemma) if span else token.lemma
        canonical_reading = str(span.get("reading") or "") if span else (token.lemma_reading or (token.reading if token.surface == lemma else ""))
        result.append(token.model_copy(update={"lemma": lemma, "reading": canonical_reading}))
    return result


def learning_units(spans: list[dict]) -> list[list]:
    return [[span["id"], span["surface"], span.get("lemma", ""),
             span.get("features", []), span.get("grammar_ids", []), span.get("status", "unknown"),
             [[choice["id"], choice["label"]] for choice in span.get("candidates", [])]]
            for span in spans]


def compact_learning_batch(items: list[dict]) -> tuple[dict, dict[str, str]]:
    """Dictionary senses once per canonical entry; context once per occurrence."""
    lexicon, rows, refs_by_token = {}, [], {}
    for item in items:
        lexical = []
        values = [[token["id"], token["surface"], token["lemma"], token["reading"], token["part_of_speech"], [], token.get("start"), token.get("end")]
                  for token in item.get("unresolved_tokens", [])] + item.get("known_tokens", [])
        for value in sorted(values, key=lambda row: int(row[6] or 0) if len(row) > 6 else 0):
            canonical = [value[2], value[3], value[4]]
            ref = "lex-" + dependency_hash(canonical)[:20]
            refs_by_token[str(value[0])] = ref
            if ref not in lexicon:
                lexicon[ref] = [ref, *canonical, value[5]]
            elif value[5] and not lexicon[ref][4]:
                lexicon[ref][4] = value[5]
            lexical.append([value[0], value[1], value[6] if len(value) > 6 else None,
                            value[7] if len(value) > 7 else None, ref])
        row = {"id": item["sentence"]["id"], "text": item["sentence"]["original"], "lexical": lexical,
               "learning_units": item.get("learning_units", [])}
        if item.get("analysis_context"):
            row["context"] = item["analysis_context"]
        if not item.get("generate", True):
            row["context_only"] = True
        rows.append(row)
    return {"lexicon": list(lexicon.values()), "sentences": rows}, refs_by_token
