from typing import Literal
from ...model_compat import BaseModel, Field


class Evidence(BaseModel):
    chapter_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=2000)
    source_revision: int = Field(default=1, ge=1)
    sentence_id: str | None = None


class EntityInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    expected_revision: int | None = None


class FactInput(BaseModel):
    key: Literal['translated_name','gender','speaker','relationship','description']
    value: str = Field(default='', max_length=500)
    status: Literal['candidate','confirmed','unknown','rejected'] = 'candidate'
    evidence: list[Evidence] = Field(default_factory=list, max_length=12)
    expected_revision: int | None = None


class MergeInput(BaseModel):
    target_entity_id: str
    expected_revision: int


class PreflightInput(BaseModel):
    expected_provider_url: str | None = Field(default=None,max_length=2000)
    expected_model: str | None = Field(default=None,max_length=100)
    mode: Literal['local','ai'] = 'local'
    allow_unread: bool = False
    max_calls: int = Field(default=10, ge=0, le=1000)
    max_tokens: int = Field(default=30000, ge=0, le=1000000)
    chunk_chars: int = Field(default=4000, ge=500, le=12000)
    through_chapter_id: str | None = None
