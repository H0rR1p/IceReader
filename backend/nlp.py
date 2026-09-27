import hashlib
import re
import unicodedata
from dataclasses import dataclass

from sudachipy import dictionary, tokenizer


_tokenizer = dictionary.Dictionary(dict="core").create()
_mode = tokenizer.Tokenizer.SplitMode.B


@dataclass(frozen=True)
class SentenceSpan:
    start: int
    end: int
    text: str


SENTENCE_END = set("。！？!?…")
CLOSERS = set("」』）】〉》”’\"'")


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def split_sentences(text: str) -> list[SentenceSpan]:
    spans: list[SentenceSpan] = []
    start = 0
    i = 0
    while i < len(text):
        char = text[i]
        should_break = char in SENTENCE_END or char == "\n"
        if char == "…" and i + 1 < len(text) and text[i + 1] == "…":
            i += 1
        if should_break:
            end = i + 1
            while end < len(text) and text[end] in CLOSERS:
                end += 1
            raw = text[start:end]
            if raw.strip():
                left = len(raw) - len(raw.lstrip())
                right = len(raw.rstrip())
                spans.append(SentenceSpan(start + left, start + right, raw.strip()))
            start = end
            i = end - 1
        i += 1
    if start < len(text):
        raw = text[start:]
        if raw.strip():
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            spans.append(SentenceSpan(start + left, start + right, raw.strip()))
    return spans


def kata(text: str) -> str:
    result = []
    for char in unicodedata.normalize("NFKC", text):
        code = ord(char)
        if 0x3041 <= code <= 0x3096:
            result.append(chr(code + 0x60))
        else:
            result.append(char)
    return "".join(result)


def hira(text: str) -> str:
    result = []
    for char in unicodedata.normalize("NFKC", text):
        code = ord(char)
        if 0x30A1 <= code <= 0x30F6:
            result.append(chr(code - 0x60))
        else:
            result.append(char)
    return "".join(result)


def pronunciation_text(text: str) -> str:
    """Convert Japanese text to a hiragana pronunciation while preserving symbols."""
    output: list[str] = []
    for morpheme in _tokenizer.tokenize(text, _mode):
        if morpheme.part_of_speech()[0] in {"補助記号", "空白", "記号"}:
            output.append(morpheme.surface())
            continue
        reading = morpheme.reading_form()
        output.append(hira(reading) if reading and reading != "*" else morpheme.surface())
    return "".join(output)


def lexeme_key(lemma: str, reading: str, part_of_speech: str) -> str:
    return "|".join((unicodedata.normalize("NFKC", lemma), kata(reading), part_of_speech))


def tokenize_sentence(sentence_id: str, sentence_text: str) -> list[dict]:
    output: list[dict] = []
    cursor = 0
    for index, morpheme in enumerate(_tokenizer.tokenize(sentence_text, _mode)):
        surface = morpheme.surface()
        start = sentence_text.find(surface, cursor)
        if start < 0:
            start = cursor
        end = start + len(surface)
        cursor = end
        pos = morpheme.part_of_speech()
        pos_label = "-".join(x for x in pos[:2] if x and x != "*")
        lemma = morpheme.dictionary_form() or surface
        reading = kata(morpheme.reading_form() or surface)
        is_content = pos[0] not in {"補助記号", "空白", "記号"} and bool(re.search(r"[\w一-龯ぁ-ゖァ-ヺ]", surface))
        output.append({
            "id": stable_id("tok", f"{sentence_id}:{index}:{start}:{surface}"),
            "sentence_id": sentence_id,
            "start": start,
            "end": end,
            "surface": surface,
            "lemma": lemma,
            "reading": reading,
            "part_of_speech": pos_label or pos[0],
            "is_content": is_content,
            "lexeme_key": lexeme_key(lemma, reading, pos_label or pos[0]),
        })
    return output

