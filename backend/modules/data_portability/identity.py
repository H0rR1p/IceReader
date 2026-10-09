"""Rewrite identity fields only; quoted source text must remain byte-for-byte intact."""
import json
import uuid

FIELD_DOMAINS = {
    "book_id": "books", "bookId": "books", "chapter_id": "chapters", "chapterId": "chapters",
    "currentChapterId": "chapters", "sentence_id": "sentences", "sentenceId": "sentences",
    "currentSentenceId": "sentences", "token_id": "tokens", "tokenId": "tokens",
    "token_ids": "tokens", "span_id": "spans", "span_ids": "spans", "children": "spans",
    "entity_id": "entities", "entity_ids": "entities", "fact_id": "facts", "fact_ids": "facts",
    "target_entity_id": "entities", "merged_into": "entities", "knowledge_item_id": "knowledge",
    "card_id": "cards", "note_id": "notes", "bookmark_id": "bookmarks", "change_id": "changes",
    "generation_id": "generations", "active_generation": "generations", "generation": "generations",
    "parent_generation": "generations", "old_generation": "generations", "new_generation": "generations",
    "chunk_id": "chunks", "job_id": "jobs", "alias_id": "knowledge", "target_id": "knowledge",
    "user_id": "users", "owner_user_id": "users", "source_user_id": "users", "target_user_id": "users",
}
NESTED_DOMAINS = {"chapter": "chapters", "sentences": "sentences", "tokens": "tokens",
                  "annotations": "annotations", "contextSenses": "tokens", "context_senses": "tokens",
                  "learning_spans": "spans", "entities": "entities", "facts": "facts",
                  "card": "cards", "note": "notes", "cards": "cards", "notes": "notes",
                  "candidate": "card_candidates", "candidates": "card_candidates"}


def mapped_id(source: str, target: str, domain: str, value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"bingdu-transfer:{target}:{source}:{domain}:{value}"))


class TransferIds:
    def __init__(self, source: str, target: str):
        self.source, self.target = source, target
        self.maps = {"users": {source: target}}

    def add(self, domain: str, value: str, replacement: str | None = None):
        if value:
            self.maps.setdefault(domain, {})[value] = replacement or (value if self.source == self.target else mapped_id(self.source, self.target, domain, value))

    def get(self, domain: str, value):
        return self.maps.get(domain, {}).get(value, value) if isinstance(value, str) else value

    def rewrite(self, value, domain: str | None = None):
        if isinstance(value, list):
            return [self.rewrite(item, domain) for item in value]
        if isinstance(value, str):
            return self.get(domain, value) if domain else value
        if not isinstance(value, dict):
            return value
        result = {}
        for key, item in value.items():
            item_domain = FIELD_DOMAINS.get(key) or NESTED_DOMAINS.get(key)
            if key == "id":
                item_domain = domain
            elif key == "object_id":
                item_domain = "entities" if value.get("kind") == "entity" else "facts"
            elif key in {"old_id", "new_id"}:
                item_domain = {"sentence": "sentences", "token": "tokens", "span": "spans"}.get(value.get("entity_kind"))
            if key.endswith("_json") or key in {"payload", "before_json", "after_json"}:
                nested_domain = domain
                if key in {"before_json", "after_json", "payload"} and value.get("kind") in {"entity", "fact"}:
                    nested_domain = "entities" if value["kind"] == "entity" else "facts"
                if isinstance(item, str):
                    result[key] = json.dumps(self.rewrite(json.loads(item), nested_domain), ensure_ascii=False)
                else:
                    result[key] = self.rewrite(item, nested_domain)
            else:
                result[key] = self.rewrite(item, item_domain)
        return result
