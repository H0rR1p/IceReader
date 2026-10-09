"""Explicit package compatibility: older exports always disclose lost data."""

PROTOCOL_VERSION = 3
CAPABILITIES = frozenset({
    "user-revisions-v1", "book-memory-v1", "source-generations-v1",
    "learning-history-v1", "knowledge-aliases-v1", "atomic-import-v1",
})
REBUILDABLE = ["linguistics.analyses", "linguistics.usage_budgets",
               "book_memory.preflight_responses", "book_memory.context_targets",
               "learning.card_search", "sync.transport_state"]
LEGACY_OMISSIONS = ["linguistics.preferences", "linguistics.span_overrides",
                    "linguistics.span_override_history", "linguistics.span_senses",
                    "book_memory.objects", "book_memory.revisions", "book_memory.summaries",
                    "library.segmentation_generations", "library.segmentation_anchor_maps",
                    "learning.knowledge_aliases", "learning.canonical_aliases",
                    "learning.notes.source_json", "learning.card_candidates.source_json",
                    "learning.transfer_daily_credits", "library.portability_book_origins"]
SHARE_OMISSIONS = ["learning.*", "book_memory.*", "linguistics.*",
                   "library.reading_progress", "library.bookmarks", "library.source_history",
                   "account.credentials", "account.bindings", "user.files"]


def compatibility(version: int, purpose: str) -> dict:
    if version not in {1, 2, 3}:
        raise ValueError("导出版本不受支持")
    required = sorted(CAPABILITIES) if version == 3 and purpose != "book-share" else []
    omitted = list(SHARE_OMISSIONS if purpose == "book-share" else REBUILDABLE)
    if version < 3:
        omitted += LEGACY_OMISSIONS
        if purpose == "account-migration":
            omitted += ["learning.card_undo_log"]
    return {"min_reader_schema_version": version, "required_capabilities": required,
            "omitted_data": sorted(set(omitted)), "complete_user_data": version == 3 and purpose != "book-share"}


def validate_compatibility(manifest: dict) -> None:
    minimum = manifest.get("min_reader_schema_version", manifest.get("schema_version", 1))
    if not isinstance(minimum, int) or minimum > PROTOCOL_VERSION:
        raise ValueError("此迁移包需要较新的冰读版本，已拒绝导入以避免数据丢失")
    required = manifest.get("required_capabilities", [])
    if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
        raise ValueError("迁移包能力声明格式错误")
    missing = set(required) - CAPABILITIES
    if missing:
        raise ValueError("目标服务不支持迁移包所需能力：" + ",".join(sorted(missing)))


def capabilities() -> dict:
    return {"schema_version": PROTOCOL_VERSION, "accepted_schema_versions": [1, 2, 3],
            "capabilities": sorted(CAPABILITIES), "legacy_export_omissions": LEGACY_OMISSIONS,
            "share_omissions": SHARE_OMISSIONS}
