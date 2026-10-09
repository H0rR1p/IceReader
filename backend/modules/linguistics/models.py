from typing import Literal
from ...model_compat import BaseModel, Field


class ContextPolicy(BaseModel):
    preceding_sentences: Literal[0, 2, 4, 8] = 2
    token_budget: int = Field(default=400, ge=0, le=16000)
    include_previous_translation: bool = False
    cross_chapter: bool = False
    entity_token_budget: int = Field(default=200, ge=0, le=4000)
    allow_future_facts: bool = False


class MorphStep(BaseModel):
    surface: str
    lemma: str = ""
    feature: str
    explanation_zh: str = ""


class MorphCandidate(BaseModel):
    id: str
    label: str
    features: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    status: Literal["supported", "possible", "selected", "rejected"] = "possible"
    lemma: str = ""
    reading: str = ""
    derivation: list[str] = Field(default_factory=list)


class LearningSpan(BaseModel):
    override_revision: int = 0
    choice_id: str | None = None
    id: str
    sentence_id: str
    start: int
    end: int
    surface: str
    lemma: str = ""
    reading: str = ""
    kind: Literal["morphology", "construction", "idiom", "lexical"] = "morphology"
    grammar_ids: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    token_ids: list[str] = Field(default_factory=list)
    steps: list[MorphStep] = Field(default_factory=list)
    derivation: list[str] = Field(default_factory=list)
    candidates: list[MorphCandidate] = Field(default_factory=list)
    explanation_zh: str = ""
    children: list[str] = Field(default_factory=list)
    source: Literal["rule", "parser", "ai", "user"] = "rule"
    version: str = "1"
    status: Literal["determined", "ambiguous", "unknown", "user_confirmed"] = "determined"
    captures: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class AnalysisManifest(BaseModel):
    version: str
    tokenizer_version: str
    dictionary_version: str
    rules_version: str
    parser_version: str = "none"
    text_hash: str
    revision: int = 1


class StructureRequest(BaseModel):
    sentence_ids: list[str] = Field(default_factory=list)
    required_version: str | None = None
    force: bool = False
