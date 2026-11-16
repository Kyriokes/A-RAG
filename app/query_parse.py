"""Light parsing of a question: which works it names and which language it is in.

detect_works() is deliberately conservative, because a false hit restricts retrieval to
the wrong work:

- Multi-word titles and aliases ("Ética a Nicómaco", "Nicomachean Ethics") match ignoring
  case and accents, on word boundaries.
- Single-word titles and aliases ("Política", "Metaphysics") are also common nouns, so they
  match only when capitalised in the question ("la Política", not "la política"). A leading
  article does not count as a word: the alias "La Política" is treated as "Política".
- Short abbreviations (three letters or fewer, like "EN" or "Pol.") match only as written,
  case included, and only when followed by a book or Bekker locator ("EN II.6",
  "Pol. 1253a"). Otherwise the Spanish "en" would name the Nicomachean Ethics.
"""

from __future__ import annotations

import re
import unicodedata

from app.models import Work

_SHORT_ABBR_LETTERS = 3
_ARTICLES = {"el", "la", "los", "las", "lo", "the"}
_LOCATOR_RE = re.compile(r"\s*(?:[IVXL]+\b|\d)")

_ES_WORDS = {
    "que", "qué", "como", "cómo", "cual", "cuál", "cuales", "cuáles", "por", "para", "según",
    "sobre", "el", "la", "los", "las", "del", "de", "un", "una", "es", "son", "en", "y", "se",
    "lo", "su", "sus", "dice", "aristóteles", "entre", "cuando", "dónde", "donde", "quién",
    "virtud", "felicidad", "amistad", "justicia", "alma", "bien", "hombre", "obra", "libro",
}  # fmt: skip
_EN_WORDS = {
    "what", "how", "which", "who", "why", "where", "when", "the", "of", "and", "is", "are",
    "does", "do", "according", "about", "to", "in", "his", "on", "say", "says", "aristotle",
    "between", "virtue", "happiness", "friendship", "justice", "soul", "good", "man", "book",
}  # fmt: skip
_ES_PUNCT_RE = re.compile(r"[¿¡]")
_ES_LETTERS_RE = re.compile(r"[ñáéíóúü]", re.IGNORECASE)


def _fold_char(ch: str) -> str:
    decomposed = unicodedata.normalize("NFD", ch)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _fold_with_map(text: str) -> tuple[str, list[int]]:
    """Accent- and case-folded text plus, for each folded char, its index in `text`."""
    out: list[str] = []
    index: list[int] = []
    for i, ch in enumerate(text):
        folded = _fold_char(ch)
        out.append(folded)
        index.extend([i] * len(folded))
    return "".join(out), index


def _fold(text: str) -> str:
    return _fold_with_map(text)[0]


def _names(work: Work) -> list[str]:
    names = [work.title_es, work.title_en, work.title_la, *work.aliases]
    if work.abbr:
        names.append(work.abbr)
    seen: set[str] = set()
    out = []
    for name in names:
        words = (name or "").split()
        if len(words) > 1 and words[0].casefold() in _ARTICLES:
            words = words[1:]
        name = " ".join(words)
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _is_short_abbr(name: str) -> bool:
    letters = re.sub(r"[^\w]", "", name)
    return len(letters) <= _SHORT_ABBR_LETTERS and " " not in name.strip()


def detect_works(query: str, works: dict[str, Work]) -> list[str]:
    """work_ids named in the query, in order of first mention."""
    folded, index = _fold_with_map(query)
    matches: list[tuple[int, int, str]] = []  # (start, end) in `query`, work_id
    for work_id, work in works.items():
        for name in _names(work):
            if _is_short_abbr(name):
                pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)")
                for m in pattern.finditer(query):
                    if _LOCATOR_RE.match(query, m.end()):
                        matches.append((m.start(), m.end(), work_id))
                continue
            single_word = len(name.split()) == 1
            pattern = re.compile(rf"(?<!\w){re.escape(_fold(name))}(?!\w)")
            for m in pattern.finditer(folded):
                start = index[m.start()]
                end = index[m.end() - 1] + 1
                if single_word and not query[start].isupper():
                    continue
                matches.append((start, end, work_id))
    # Longest match wins where matches overlap ("Ética a Nicómaco" over an alias "Ética").
    matches.sort(key=lambda m: (-(m[1] - m[0]), m[0]))
    taken: list[tuple[int, int, str]] = []
    for start, end, work_id in matches:
        if all(end <= s or start >= e for s, e, _ in taken):
            taken.append((start, end, work_id))
    ordered: list[str] = []
    for _, _, work_id in sorted(taken):
        if work_id not in ordered:
            ordered.append(work_id)
    return ordered


def detect_lang(query: str) -> str:
    """'es' or 'en'. Spanish wins ties: most questions in the pilot are in Spanish."""
    words = re.findall(r"\w+", query.casefold())
    es = sum(1 for w in words if w in _ES_WORDS)
    en = sum(1 for w in words if w in _EN_WORDS)
    # Opening ¿ or ¡ is a strong signal; accented letters a weak one (titles like
    # "Ética a Nicómaco" also appear in English questions).
    es += 3 * len(_ES_PUNCT_RE.findall(query)) + len(_ES_LETTERS_RE.findall(query))
    return "en" if en > es else "es"
