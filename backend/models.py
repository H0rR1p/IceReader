from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class AiSettings(BaseModel):
    base_url: HttpUrl = "https://api.deepseek.com"
    model: str = "deepseek-chat"


class LocalAiSettingsInput(BaseModel):
    api_key: str | None = None
    base_url: HttpUrl = "https://api.deepseek.com"
    model: str = Field(default="deepseek-chat", min_length=1, max_length=100)
    cache_hit_usd_per_million: float = Field(default=0, ge=0)
    cache_miss_usd_per_million: float = Field(default=0, ge=0)
    output_usd_per_million: float = Field(default=0, ge=0)


class LocalAiSettingsStatus(BaseModel):
    base_url: str
    model: str
    has_api_key: bool
    cache_hit_usd_per_million: float = 0
    cache_miss_usd_per_million: float = 0
    output_usd_per_million: float = 0


class VoiceSettingsInput(BaseModel):
    ymm_path: str = ""
    character_name: str = Field(default="", max_length=100)
    playback_rate: int = Field(default=85, ge=50, le=200)
    volume: int = Field(default=50, ge=0, le=100)


class VoiceSettingsStatus(BaseModel):
    ymm_path: str
    ymm_found: bool
    template_found: bool
    character_name: str = ""
    character_names: list[str] = Field(default_factory=list)
    playback_rate: int
    volume: int
    ready: bool


class VoiceSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    force: bool = False


class VoiceJobStatus(BaseModel):
    id: str
    status: Literal["queued", "running", "complete", "failed", "canceled"]
    message: str = ""
    audio_url: str | None = None
    cached: bool = False


class LibrarySnapshot(BaseModel):
    books: list[dict] = Field(default_factory=list)
    chapters: list[dict] = Field(default_factory=list)
    sentences: list[dict] = Field(default_factory=list)
    tokens: list[dict] = Field(default_factory=list)
    annotations: list[dict] = Field(default_factory=list)
    contextSenses: list[dict] = Field(default_factory=list)
    lexemes: list[dict] = Field(default_factory=list)
    cards: list[dict] = Field(default_factory=list)
    bookmarks: list[dict] = Field(default_factory=list)


class LibraryIndex(BaseModel):
    books: list[dict] = Field(default_factory=list)
    chapters: list[dict] = Field(default_factory=list)


class ChapterSnapshot(BaseModel):
    chapter: dict | None = None
    sentences: list[dict] = Field(default_factory=list)
    tokens: list[dict] = Field(default_factory=list)
    annotations: list[dict] = Field(default_factory=list)
    contextSenses: list[dict] = Field(default_factory=list)
    lexemes: list[dict] = Field(default_factory=list)


class StudyDataSnapshot(BaseModel):
    lexemes: list[dict] = Field(default_factory=list)
    cards: list[dict] = Field(default_factory=list)


class LibraryPatch(BaseModel):
    upserts: dict[str, list[dict]] = Field(default_factory=dict)
    deletes: dict[str, list[str]] = Field(default_factory=dict)


class AnalyzeRequest(BaseModel):
    chapter_id: str
    text: str = Field(min_length=1, max_length=120_000)
    only_sentence_ids: list[str] = Field(default_factory=list)
    known_lexeme_keys: list[str] = Field(default_factory=list)
    settings: AiSettings = Field(default_factory=AiSettings)


class TokenOut(BaseModel):
    id: str
    sentence_id: str
    start: int
    end: int
    surface: str
    lemma: str
    reading: str
    part_of_speech: str
    is_content: bool


class AnnotationOut(BaseModel):
    id: str
    sentence_id: str
    type: Literal["grammar", "pragmatics", "ellipsis", "culture"]
    anchor_start: int
    anchor_end: int
    quote: str
    structure: str = ""
    explanation_zh: str


class ContextSenseOut(BaseModel):
    token_id: str
    gloss_zh: str


class LexemeOut(BaseModel):
    key: str
    lemma: str
    reading: str
    part_of_speech: str
    senses_zh: list[str]
    source: str


class SentenceOut(BaseModel):
    id: str
    chapter_id: str
    start: int
    end: int
    original: str
    translation_zh: str
    status: Literal["complete", "failed"] = "complete"
    error: str | None = None
    explanation_status: Literal["idle", "processing", "complete", "failed"] = "idle"
    explanation_detail: Literal["meaning", "full"] | None = None


class AnalyzeResponse(BaseModel):
    sentences: list[SentenceOut]
    tokens: list[TokenOut]
    annotations: list[AnnotationOut]
    context_senses: list[ContextSenseOut]
    lexemes: list[LexemeOut]
    warnings: list[str] = Field(default_factory=list)


class ExplainSentenceRequest(BaseModel):
    sentence: SentenceOut
    tokens: list[TokenOut]
    annotation_mode: Literal["none", "grammar"] = "none"
    detail_mode: Literal["meaning", "full"] = "full"
    context_before: list[str] = Field(default_factory=list, max_length=2)
    settings: AiSettings = Field(default_factory=AiSettings)


class ExplainBatchItem(BaseModel):
    sentence: SentenceOut
    tokens: list[TokenOut]


class ExplainBatchRequest(BaseModel):
    items: list[ExplainBatchItem] = Field(min_length=1, max_length=12)
    annotation_mode: Literal["none", "grammar"] = "none"
    detail_mode: Literal["meaning", "full"] = "full"
    context_before: list[str] = Field(default_factory=list, max_length=2)
    settings: AiSettings = Field(default_factory=AiSettings)


class SegmentChapterRequest(BaseModel):
    chapter_id: str
    text: str = Field(min_length=1, max_length=120_000)
    blocks: list["ContentBlock"] = Field(default_factory=list)
    settings: AiSettings = Field(default_factory=AiSettings)


class TextImportRequest(BaseModel):
    title: str = "粘贴文本"
    text: str = Field(min_length=1, max_length=2_000_000)


class ContentBlock(BaseModel):
    id: str
    type: Literal["heading", "paragraph", "quote", "list-item", "image", "page-break", "separator"]
    start: int = 0
    end: int = 0
    text: str = ""
    level: int | None = None
    asset_url: str | None = None
    alt: str = ""
    placement: Literal["left", "center", "right", "inline"] = "left"
    width: int | None = None
    height: int | None = None


class ImportedChapter(BaseModel):
    id: str
    title: str
    order: int
    text: str
    blocks: list[ContentBlock] = Field(default_factory=list)
    original_html_url: str | None = None
    sentences: list[SentenceOut] = Field(default_factory=list)
    tokens: list[TokenOut] = Field(default_factory=list)
    segmentation_source: Literal["ai-reviewed", "local-fallback", "empty"] = "empty"


class ImportReport(BaseModel):
    source_documents: int = 0
    imported_sections: int = 0
    images: int = 0
    image_references: int = 0
    image_only_sections: int = 0
    broken_image_references: int = 0


class ImportedBook(BaseModel):
    title: str
    author: str = ""
    chapters: list[ImportedChapter]
    import_report: ImportReport | None = None
