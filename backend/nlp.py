import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass

from sudachipy import dictionary, tokenizer


_tokenizer = None
_tokenizer_lock = threading.Lock()
_tokenize_lock = threading.Lock()
_mode = tokenizer.Tokenizer.SplitMode.B


def _get_tokenizer():
    """Load the large Sudachi core dictionary only when NLP is first used."""
    global _tokenizer
    if _tokenizer is None:
        with _tokenizer_lock:
            if _tokenizer is None:
                _tokenizer = dictionary.Dictionary(dict="core").create()
    return _tokenizer


@dataclass(frozen=True)
class SentenceSpan:
    start: int
    end: int
    text: str
    uncertain_after: bool = False


SENTENCE_END = set("。！？!?…")
CLOSERS = set("」』）】〉》”’\"'")


def stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def split_sentences(text: str, hard_boundaries: set[int] | None = None) -> list[SentenceSpan]:
    """Split Japanese prose without treating visual line wrapping as a sentence.

    EPUBs commonly place every displayed line in its own span.  Those spans are
    joined with a single newline in the extracted text, so a lone newline must
    remain soft.  Blank lines and caller supplied structural boundaries remain
    hard boundaries.
    """
    spans: list[SentenceSpan] = []
    hard_boundaries = hard_boundaries or set()
    start = 0
    i = 0

    def append_span(end: int) -> None:
        raw = text[start:end]
        if not raw.strip():
            return
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        spans.append(SentenceSpan(start + left, start + right, raw.strip(), False))

    while i < len(text):
        char = text[i]
        if char in SENTENCE_END:
            end = i + 1
            while end < len(text) and text[end] in SENTENCE_END:
                end += 1
            while end < len(text) and text[end] in CLOSERS:
                end += 1
            append_span(end)
            start = end
            i = end
            continue
        if char == "\n":
            end = i + 1
            while end < len(text) and text[end] == "\n":
                end += 1
            if end - i >= 2:
                append_span(i)
                start = end
            i = end
            continue
        if i + 1 in hard_boundaries:
            append_span(i + 1)
            start = i + 1
        i += 1
    if start < len(text):
        raw = text[start:]
        if raw.strip():
            left = len(raw) - len(raw.lstrip())
            right = len(raw.rstrip())
            spans.append(SentenceSpan(start + left, start + right, raw.strip(), False))
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
    with _tokenize_lock:
        for morpheme in _get_tokenizer().tokenize(text, _mode):
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
    with _tokenize_lock:
        for index, morpheme in enumerate(_get_tokenizer().tokenize(sentence_text, _mode)):
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

