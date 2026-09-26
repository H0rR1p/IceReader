from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class AiSettings(BaseModel):
    base_url: HttpUrl = "https://api.deepseek.com"
    model: str = "deepseek-chat"


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


class AnalyzeResponse(BaseModel):
    sentences: list[SentenceOut]
    tokens: list[TokenOut]
    annotations: list[AnnotationOut]
    context_senses: list[ContextSenseOut]
    lexemes: list[LexemeOut]
    warnings: list[str] = Field(default_factory=list)


class TextImportRequest(BaseModel):
    title: str = "粘贴文本"
    text: str = Field(min_length=1, max_length=2_000_000)


class ImportedChapter(BaseModel):
    id: str
    title: str
    order: int
    text: str


class ImportedBook(BaseModel):
    title: str
    author: str = ""
    chapters: list[ImportedChapter]
