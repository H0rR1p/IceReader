"""Public library-domain operations used by other modules."""

from .repository import find_personal_lexeme
from .sources import get_sentence_source


def resolve_personal_lexeme(
    user_id: str,
    exact_key: str,
    lemma: str,
    reading: str,
    surface: str,
) -> dict | None:
    return find_personal_lexeme(user_id, exact_key, lemma, reading, surface)
