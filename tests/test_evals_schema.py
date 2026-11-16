import pytest
from pydantic import ValidationError

from evals.labels import check_corpus, check_format, evidence_clusters, split_leaks
from evals.schema import BekkerSpan, Case, DivisionSpan, SmokeCase, load_dataset, load_smoke
from tests.test_evals_support import WORKS, abstain_case, bspan, case, units, write_dataset


def test_valid_cases():
    c = Case.model_validate(case("a"))
    assert c.reviewed and c.expects_answer and c.scored_for_retrieval
    assert isinstance(c.evidence[0][0], BekkerSpan)
    assert c.evidence[0][0].encoded() == (221336, 221402)
    a = Case.model_validate(abstain_case("b"))
    assert not a.expects_answer and not a.scored_for_retrieval
    d = Case.model_validate(
        case("c", evidence=[[{"work_id": "nobekker", "chapter": "3", "edition_note": "Azcárate"}]])
    )
    assert isinstance(d.evidence[0][0], DivisionSpan)


@pytest.mark.parametrize(
    "overrides",
    [
        {"question_lang": "fr"},
        {"type": "nope"},
        {"evidence": [[bspan("1107a2", "1106b36")]]},  # end before start
        {"evidence": [[bspan("1106x36", "1107a2")]]},  # not Bekker
        {"evidence": [[]]},  # empty group
        {"evidence": []},  # an answer needs evidence
        {"key_points": ["uno"]},  # 2-4 key points
        {"key_points": ["a", "b", "c", "d", "e"]},
        {"key_points": ["a|", "b"]},  # empty alternative
        {"unanswerable_reason": "opinion"},  # only with type=unanswerable
        {"fecha_revision": None},  # reviewed without date
        {"source": "synthetic_unreviewed"},  # reviewed but marked unreviewed
        {"revisado_por": "  "},
        {"twin_of": "a"},  # itself
        {"extra_field": 1},
    ],
)
def test_invalid_cases(overrides):
    with pytest.raises(ValidationError):
        Case.model_validate(case("a", **overrides))


def test_unanswerable_must_abstain():
    with pytest.raises(ValidationError):
        Case.model_validate(abstain_case("a", expected_behavior="answer"))
    with pytest.raises(ValidationError):
        Case.model_validate(abstain_case("a", unanswerable_reason=None))


def test_loader_reports_errors_and_ignores_nothing(tmp_path):
    write_dataset(tmp_path, [case("a"), case("b", revisado_por=None, fecha_revision=None)])
    (tmp_path / "bad.jsonl").write_text('{"id": "x"}\nnot json\n\n', encoding="utf-8")
    ds = load_dataset(tmp_path)
    assert [c.id for c in ds.cases] == ["a", "b"]
    assert [c.id for c in ds.reviewed] == ["a"]
    assert [c.id for c in ds.unreviewed] == ["b"]
    assert len(ds.errors) == 2
    assert ds.errors[0].startswith("bad.jsonl:1:")
    assert ds.errors[1].startswith("bad.jsonl:2: JSON inválido")


def test_dataset_version_changes_with_content_not_line_endings(tmp_path):
    path = write_dataset(tmp_path, [case("a")])
    v1 = load_dataset(tmp_path).version
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    assert load_dataset(tmp_path).version == v1
    write_dataset(tmp_path, [case("a", question="otra")])
    assert load_dataset(tmp_path).version != v1


def test_format_check(tmp_path):
    write_dataset(
        tmp_path,
        [
            case("a"),
            case("a"),  # duplicate id
            case("b", twin_of="zz"),  # missing twin
            case("c", split="test", evidence=[[bspan("1107a1", "1107a5")]], twin_of="d"),
            case("d", evidence=[[bspan("1150a1", "1150a5")]]),  # twin in the other split
            case("e", evidence=[[bspan("1300a1", "1300a5")]]),  # outside eth-nic's range
            case("f", evidence=[[bspan("1106b1", "1106b5", work_id="nope")]]),
            case(
                "g",
                evidence=[[{"work_id": "nobekker", "bekker_start": "1a1", "bekker_end": "1a5"}]],
            ),
        ],
    )
    errors = check_format(load_dataset(tmp_path), WORKS)
    text = "\n".join(errors)
    assert "a: id repetido" in text
    assert "b: twin_of=zz no existe" in text
    assert "c: twin_of=d está en otro conjunto" in text
    assert "e: evidencia [0][0] eth-nic 1300a1–5: fuera del rango" in text
    assert "f: evidencia [0][0] nope 1106b1–5: obra desconocida" in text
    assert "g: evidencia [0][0] nobekker 1a1–5: la obra no tiene numeración Bekker" in text
    assert "c (test) y a (dev) comparten el pasaje" in text  # 1107a1-5 vs 1106b36-1107a2


def test_split_leaks_only_across_splits():
    dev = Case.model_validate(case("a"))
    dev2 = Case.model_validate(case("b"))
    test = Case.model_validate(case("c", split="test", evidence=[[bspan("1150a1", "1150a5")]]))
    assert split_leaks([dev, dev2, test]) == []


def test_corpus_check():
    cases = [
        Case.model_validate(
            case("ok", evidence=[[bspan("1106b36", "1107a2")], [bspan("1343a3", "1343a9", "oec")]])
        ),
        Case.model_validate(case("missing", evidence=[[bspan("1110a1", "1110a9")]])),
        Case.model_validate(
            case("div", evidence=[[{"work_id": "nobekker", "chapter": "3", "edition_note": "t"}]])
        ),
        Case.model_validate(
            case(
                "div-missing",
                evidence=[[{"work_id": "nobekker", "chapter": "4", "edition_note": "t"}]],
            )
        ),
    ]
    errors = check_corpus(cases, units())
    assert len(errors) == 2
    assert errors[0].startswith("missing: evidencia [0][0] eth-nic 1110a1–9 no resuelve")
    assert errors[1].startswith("div-missing:")


def test_evidence_clusters():
    cases = [
        Case.model_validate(case("b")),
        Case.model_validate(case("a", evidence=[[bspan("1107a1", "1107a9")]])),
        Case.model_validate(case("c", evidence=[[bspan("1150a1", "1150a9")]])),
        Case.model_validate(abstain_case("d")),
    ]
    assert evidence_clusters(cases) == {"a": "a", "b": "a", "c": "c", "d": "d"}


def test_smoke_file_format(tmp_path):
    path = tmp_path / "smoke.jsonl"
    path.write_text(
        '{"id": "s1", "question": "¿Qué es la virtud?", "question_lang": "es", '
        '"expected_behavior": "answer"}\n'
        '{"id": "s2", "question": "x", "question_lang": "es", "expected_behavior": "maybe"}\n',
        encoding="utf-8",
    )
    cases, errors = load_smoke(path)
    assert [c.id for c in cases] == ["s1"] and isinstance(cases[0], SmokeCase)
    assert len(errors) == 1 and errors[0].startswith("smoke.jsonl:2:")
    assert load_smoke(tmp_path / "missing.jsonl")[1][0].endswith(
        "faltan las 10 preguntas de `make smoke`"
    )


@pytest.mark.parametrize(
    "kind",
    [
        {"type": "unanswerable", "unanswerable_reason": "opinion", "expected_behavior": "abstain",
         "evidence": [], "key_points": []},
        {"type": "false_premise", "expected_behavior": "correct_premise"},
        {"type": "cross_work"},
        {"type": "dubious_work", "expected_behavior": "answer_with_caveat"},
    ],
)  # fmt: skip
def test_person_written_types_must_be_handwritten_once_reviewed(kind):
    # PLAN: comparison, unanswerable, false-premise and dubious-work questions are written
    # by the person; a reviewed model proposal of these types does not count.
    with pytest.raises(ValidationError, match="las escribe la persona"):
        Case.model_validate(case("p", **kind, source="synthetic_reviewed"))
    assert Case.model_validate(case("p", **kind, source="handwritten")).reviewed
    unreviewed = case(
        "p", **kind, source="synthetic_unreviewed", revisado_por=None, fecha_revision=None
    )
    assert not Case.model_validate(unreviewed).reviewed  # proposals may wait for review
    lookup = Case.model_validate(case("p", source="synthetic_reviewed"))
    assert lookup.reviewed  # other types may come from a model
