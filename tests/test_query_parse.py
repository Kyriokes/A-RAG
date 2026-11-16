import pytest

from app.models import Work
from app.query_parse import detect_lang, detect_works


def work(work_id, abbr, title_es, title_en, title_la, aliases=()) -> Work:
    return Work(
        work_id=work_id,
        abbr=abbr,
        title_es=title_es,
        title_en=title_en,
        title_la=title_la,
        aliases=list(aliases),
        authenticity="genuine",
        structure="book_chapter",
    )


WORKS = {
    "eth-nic": work(
        "eth-nic",
        "EN",
        "Ética a Nicómaco",
        "Nicomachean Ethics",
        "Ethica Nicomachea",
        aliases=["Moral a Nicómaco", "Ética nicomáquea"],
    ),
    "eth-eud": work(
        "eth-eud",
        "EE",
        "Ética a Eudemo",
        "Eudemian Ethics",
        "Ethica Eudemia",
        aliases=["Moral a Eudemo", "Ética"],  # "Ética" alone: tests longest-match-wins
    ),
    "pol": work("pol", "Pol.", "Política", "Politics", "Politica", aliases=["La Política"]),
    "met": work("met", "Met.", "Metafísica", "Metaphysics", "Metaphysica"),
}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("¿Qué dice la Ética a Nicómaco sobre la amistad?", ["eth-nic"]),
        ("que dice la etica a nicomaco sobre la amistad", ["eth-nic"]),  # no accents, lower
        ("QUÉ DICE LA ÉTICA A NICÓMACO", ["eth-nic"]),
        ("What does the Nicomachean Ethics say about friendship?", ["eth-nic"]),
        ("En la Moral a Nicómaco, ¿qué es la prudencia?", ["eth-nic"]),
        ("Ethica Nicomachea, liber II", ["eth-nic"]),
        ("¿Qué dice la Política sobre la esclavitud?", ["pol"]),
        ("What does Aristotle's Politics say about slavery?", ["pol"]),
        ("Compara la Política con la Ética a Nicómaco", ["pol", "eth-nic"]),
        ("¿Qué dice la Ética a Eudemo y la Ética a Nicómaco?", ["eth-eud", "eth-nic"]),
    ],
)
def test_detects_named_works(query, expected):
    assert detect_works(query, WORKS) == expected


@pytest.mark.parametrize(
    "query",
    [
        "¿Qué es la política para Aristóteles?",  # common noun, not the title
        "what is the relation between ethics and politics?",
        "¿Qué dice Aristóteles en el libro II sobre la virtud?",  # Spanish "en"
        "¿En qué consiste la felicidad?",
        "Según EN, la virtud es un hábito",  # abbreviation without a locator
        "pol. III",  # abbreviations are case-sensitive
        "Eudemonía y metafísica",
        "Las políticas públicas",
    ],
)
def test_no_false_hits(query):
    assert detect_works(query, WORKS) == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("¿Qué significa EN II.6?", ["eth-nic"]),
        ("Ver EN 1106b36", ["eth-nic"]),
        ("Pol. III 1278b", ["pol"]),
        ("compárese Met. IV con EE II", ["met", "eth-eud"]),
    ],
)
def test_abbreviations_need_a_locator(query, expected):
    assert detect_works(query, WORKS) == expected


def test_longest_match_wins():
    # "Ética" is an alias of the Eudemian Ethics, but it is part of "Ética a Nicómaco" here.
    assert detect_works("Según la Ética a Nicómaco, ¿qué es la virtud?", WORKS) == ["eth-nic"]
    assert detect_works("Según la Ética, ¿qué es la virtud?", WORKS) == ["eth-eud"]


def test_empty_catalogue():
    assert detect_works("Ética a Nicómaco", {}) == []


@pytest.mark.parametrize(
    ("query", "lang"),
    [
        ("¿Qué es la felicidad?", "es"),
        ("que dice aristoteles sobre la amistad", "es"),
        ("La virtud es un término medio", "es"),
        ("What is happiness according to Aristotle?", "en"),
        ("how does aristotle define courage", "en"),
        ("What does the Ética a Nicómaco say about friendship?", "en"),
        ("", "es"),
    ],
)
def test_detect_lang(query, lang):
    assert detect_lang(query) == lang
