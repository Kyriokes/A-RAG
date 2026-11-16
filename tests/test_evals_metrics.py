import pytest

from app import bekker
from evals.metrics import (
    Location,
    MatchParams,
    abstention_matrix,
    all_evidence_at,
    behavior_met,
    bekker_overlap_hit,
    check_citations,
    first_hit_ranks,
    key_point_present,
    key_points_lexical,
    line_position,
    recall_at,
    reciprocal_rank,
    span_hit,
    spans_overlap,
    strict_pass,
)
from evals.schema import BekkerSpan, Case, DivisionSpan
from tests.test_evals_support import case, chunk, citation

P = MatchParams(tolerance_lines=3, min_overlap_lines=3)
EXACT = MatchParams(tolerance_lines=0, min_overlap_lines=3)


def hit(fragment: str, gold: str, params: MatchParams = P) -> bool:
    return bekker_overlap_hit(bekker.parse_range(fragment), bekker.parse_range(gold), params)


def loc(span: str | None, **kw) -> Location:
    return Location.from_chunk(chunk("c#1", span, **kw))


# --- line arithmetic ----------------------------------------------------------------------


def test_line_position_uses_the_configured_column_length():
    from app.settings import Settings

    lines = Settings().bekker_column_lines  # what chunking assumes across a column break
    assert MatchParams.from_settings(Settings()).column_lines == lines
    a1, b1 = bekker.parse("1106a1").to_int(), bekker.parse("1106b1").to_int()
    assert line_position(b1, lines) - line_position(a1, lines) == lines
    # Lines past the column length are clamped to its last line.
    b36 = bekker.parse("1106b36").to_int()
    assert line_position(b36, 35) == line_position(bekker.parse("1106b35").to_int(), 35)
    assert line_position(bekker.parse("1107a1").to_int(), 35) == line_position(b36, 35) + 1


def test_column_only_refs_are_clamped_to_their_column():
    start, end = bekker.parse_range("1106b")  # lines 0..99 in the encoding
    assert line_position(end, 35) - line_position(start, 35) + 1 == 35
    assert line_position(end, 35) < line_position(bekker.parse("1107a1").to_int(), 35)


# --- overlap edge cases -------------------------------------------------------------------


def test_overlap_inside_one_column():
    assert hit("1106b20-1106b40", "1106b30-1106b35")
    assert not hit("1106b10-1106b20", "1106b30-1106b35")  # 10 lines apart


def test_overlap_across_a_column_break():
    # Gold runs from column b into the next page's column a; the fragment starts there.
    assert hit("1107a1-1107a20", "1106b30-1107a5", EXACT)
    assert not hit("1107a6-1107a20", "1106b30-1107a5", EXACT)


def test_tolerance_reaches_across_a_column_break_both_ways():
    # 35-line columns (settings.bekker_column_lines): the fragment right before the gold
    # span hits like the one right after it, as within a column.
    gold = "1107a1-1107a5"
    assert hit("1107a6-1107a20", gold)  # after: shares a6..a8
    assert hit("1106b20-1106b35", gold)  # before: shares b33..b35
    assert hit("1106b1-1106b9", "1106b10-1106b14")  # the same within one column
    assert not hit("1106b20-1106b35", gold, EXACT)
    assert not hit("1106b20-1106b31", gold)  # 4 lines short of the column end


def test_minimum_overlap():
    assert not hit("1106b1-1106b31", "1106b30-1106b40", EXACT)  # 2 shared lines
    assert hit("1106b1-1106b32", "1106b30-1106b40", EXACT)  # 3 shared lines


def test_short_fragment_inside_the_gold_span_hits():
    assert hit("1106b36-1106b37", "1106b30-1106b40", EXACT)  # 2 lines, all inside


def test_single_line_gold_with_tolerance():
    assert hit("1106b20-1106b35", "1106b36-1106b36")  # 33..35 within 36±3
    assert not hit("1106b20-1106b32", "1106b36-1106b36")  # only 33..32: none
    assert hit("1106b36-1106b36", "1106b36-1106b36", EXACT)


def test_whole_column_gold_does_not_spill_without_tolerance():
    assert not hit("1107a1-1107a10", "1106b", EXACT)
    assert hit("1106b30-1107a10", "1106b", EXACT)


def test_span_hit_needs_same_work_and_bekker():
    gold = BekkerSpan(work_id="eth-nic", bekker_start="1106b30", bekker_end="1106b40")
    assert span_hit(loc("1106b30-1106b40"), gold, P)
    assert not span_hit(loc("1106b30-1106b40", work_id="oec", edition_id="oec.en.t"), gold, P)
    assert not span_hit(loc(None), gold, P)


def test_division_span():
    gold = DivisionSpan(work_id="nobekker", book="1", chapter="3", edition_note="Azcárate")
    divs = [("book", "1"), ("chapter", "3")]
    base = {"work_id": "nobekker", "edition_id": "nobekker.es.a", "divisions": divs}
    assert span_hit(loc(None, **base), gold, P)
    assert not span_hit(
        loc(None, **{**base, "divisions": [("book", "1"), ("chapter", "4")]}), gold, P
    )
    pinned = DivisionSpan(
        work_id="nobekker", book="1", chapter="3", edition_note="x", edition_id="nobekker.es.b"
    )
    assert not span_hit(loc(None, **base), pinned, P)
    chapter_only = DivisionSpan(work_id="nobekker", chapter="3", edition_note="x")
    assert span_hit(loc(None, **{**base, "divisions": [("chapter", "3")]}), chapter_only, P)


def test_spans_overlap_for_split_leaks():
    a = BekkerSpan(work_id="eth-nic", bekker_start="1106b30", bekker_end="1107a2")
    b = BekkerSpan(work_id="eth-nic", bekker_start="1107a2", bekker_end="1107a9")
    c = BekkerSpan(work_id="eth-nic", bekker_start="1107a3", bekker_end="1107a9")
    assert spans_overlap(a, b)
    assert not spans_overlap(a, c)
    assert not spans_overlap(
        a, BekkerSpan(work_id="oec", bekker_start="1343a1", bekker_end="1343a5")
    )


# --- groups, recall, MRR ------------------------------------------------------------------


def groups(*alternatives: list[str]) -> list[list[BekkerSpan]]:
    return [
        [
            BekkerSpan(work_id="eth-nic", bekker_start=s.split("-")[0], bekker_end=s.split("-")[1])
            for s in group
        ]
        for group in alternatives
    ]


def test_alternatives_inside_a_group():
    gold = groups(["1156a6-1156a20", "1238a30-1238a35"])  # parallel passages
    locs = [loc("1094a1-1094a20"), loc("1238a28-1238b5")]
    assert first_hit_ranks(locs, gold, P) == [2]


def test_recall_all_evidence_and_mrr():
    gold = groups(["1106b36-1107a2"], ["1156a6-1156a20"])
    locs = [loc("1094a1-1094a20"), loc("1156a1-1156a30"), loc("1106b30-1107a10")]
    ranks = first_hit_ranks(locs, gold, P)
    assert ranks == [3, 2]
    assert recall_at(ranks, 2) == 0.5
    assert recall_at(ranks, 3) == 1.0
    assert all_evidence_at(ranks, 2) is False
    assert all_evidence_at(ranks, 3) is True
    assert reciprocal_rank(ranks) is None  # MRR only for single-group cases
    assert reciprocal_rank([3]) == pytest.approx(1 / 3)
    assert reciprocal_rank([11]) == 0.0
    assert reciprocal_rank([None]) == 0.0
    assert recall_at([], 5) is None


# --- abstention, citations, strict pass -----------------------------------------------------


def test_abstention_matrix():
    m = abstention_matrix(
        [(True, False), (True, True), (False, False), (False, True), (False, True)]
    )
    assert (m.n_answer, m.n_abstain) == (2, 3)
    assert m.false_refusal_rate == 0.5
    assert m.false_answer_rate == pytest.approx(1 / 3)
    assert abstention_matrix([]).false_answer_rate is None


def test_check_citations():
    good = chunk("a#1", "1106b30-1107a10")
    other = chunk("a#2", "1094a1-1094a20")
    outside = chunk("z#9", "1106b30-1107a10")
    context = [Location.from_chunk(good), Location.from_chunk(other)]
    gold = groups(["1106b36-1107a2"], ["1156a6-1156a20"])
    check = check_citations(
        [citation(1, good), citation(2, other), citation(3, outside)], context, gold, P
    )
    assert (check.n_citations, check.in_context, check.valid) == (3, 2, 3)
    assert check.hits_gold == 2
    assert check.groups_cited == 1
    assert check.precision == pytest.approx(2 / 3)
    assert check.recall == 0.5
    assert not check.all_in_context
    assert [d["in_context"] for d in check.details] == [True, True, False]


def test_citation_with_wrong_metadata_is_invalid():
    good = chunk("a#1", "1106b30-1107a10")
    forged = citation(1, chunk("a#1", "1150a1-1150a9"))  # same id, other range
    check = check_citations([forged], [Location.from_chunk(good)], [], P)
    assert check.valid == 0 and check.in_context == 1
    assert check.precision is None  # no gold groups


def _case(**kw) -> Case:
    return Case.model_validate(case("x", **kw))


def test_strict_pass_rules():
    good = chunk("a#1", "1106b30-1107a10")
    context = [Location.from_chunk(good)]
    c = _case()
    ok = check_citations([citation(1, good)], context, c.evidence, P)
    passing = {"faithfulness": "pass", "key_points": "pass"}
    assert strict_pass(c, abstained=False, cites_dubious=False, citations=ok, verdicts=passing)
    assert strict_pass(c, abstained=False, cites_dubious=False, citations=ok, verdicts=None) is None
    assert not strict_pass(
        c,
        abstained=False,
        cites_dubious=False,
        citations=ok,
        verdicts={**passing, "faithfulness": "unknown"},
    )
    assert not strict_pass(c, abstained=True, cites_dubious=False, citations=ok, verdicts=passing)
    off_gold = check_citations(
        [citation(1, chunk("b#1", "1094a1-1094a9"))],
        [Location.from_chunk(chunk("b#1", "1094a1-1094a9"))],
        c.evidence,
        P,
    )
    assert not strict_pass(
        c, abstained=False, cites_dubious=False, citations=off_gold, verdicts=passing
    )
    outside = check_citations(
        [citation(1, good), citation(2, chunk("z#1", "1106b30-1107a10"))], context, c.evidence, P
    )
    assert not strict_pass(
        c, abstained=False, cites_dubious=False, citations=outside, verdicts=passing
    )


def test_strict_pass_premise_and_abstain():
    good = chunk("a#1", "1106b30-1107a10")
    fp = _case(type="false_premise", expected_behavior="correct_premise")
    ok = check_citations([citation(1, good)], [Location.from_chunk(good)], fp.evidence, P)
    base = {"faithfulness": "pass", "key_points": "pass"}
    assert not strict_pass(fp, abstained=False, cites_dubious=False, citations=ok, verdicts=base)
    assert strict_pass(
        fp,
        abstained=False,
        cites_dubious=False,
        citations=ok,
        verdicts={**base, "premise_corrected": "pass"},
    )
    ab = Case.model_validate(
        case(
            "y",
            type="unanswerable",
            unanswerable_reason="opinion",
            expected_behavior="abstain",
            evidence=[],
            key_points=[],
        )
    )
    assert strict_pass(ab, abstained=True, cites_dubious=False, citations=None, verdicts={})
    assert not strict_pass(ab, abstained=False, cites_dubious=False, citations=None, verdicts={})


def test_strict_pass_is_undefined_for_every_case_without_a_judge():
    # Otherwise only abstentions and rule failures would get a value: a biased subset.
    good = chunk("a#1", "1106b30-1107a10")
    c = _case()
    ok = check_citations([citation(1, good)], [Location.from_chunk(good)], c.evidence, P)
    ab = Case.model_validate(
        case(
            "y",
            type="unanswerable",
            unanswerable_reason="opinion",
            expected_behavior="abstain",
            evidence=[],
            key_points=[],
        )
    )
    for kw in (
        {"case": ab, "abstained": True, "citations": None},
        {"case": ab, "abstained": False, "citations": None},
        {"case": c, "abstained": True, "citations": ok},
        {"case": c, "abstained": False, "citations": ok},
    ):
        case_ = kw.pop("case")
        assert strict_pass(case_, cites_dubious=False, verdicts=None, **kw) is None


def test_behavior_with_caveat_needs_a_dubious_citation():
    c = _case(type="dubious_work", expected_behavior="answer_with_caveat")
    assert behavior_met(c, abstained=False, cites_dubious=True)
    assert not behavior_met(c, abstained=False, cites_dubious=False)


def test_key_points_lexical():
    assert key_point_present("El JUSTO MEDIO entre dos vicios", "término medio|justo medio")
    assert key_point_present("la virtud ética", "virtud")
    assert key_point_present("Es un termino medio", "término medio")  # accents folded
    assert not key_point_present("nada que ver", "virtud")
    assert key_points_lexical("virtud y término medio", ["virtud", "término medio", "hábito"]) == (
        pytest.approx(2 / 3)
    )
    assert key_points_lexical("x", []) is None
