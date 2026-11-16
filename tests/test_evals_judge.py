import pytest

from app.settings import Settings
from evals.judge import (
    VERDICT_SCHEMA,
    Judge,
    criteria_for,
    format_key_points,
    judge_settings,
    load_judge_prompts,
    load_prompt,
    parse_verdict,
)
from evals.schema import Case
from tests.test_evals_support import abstain_case, case


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"verdict": "pass", "reason": "ok"}', ("pass", "ok", True)),
        ('```json\n{"verdict": "fail", "reason": "falta X"}\n```', ("fail", "falta X", True)),
        ('Veredicto: {"verdict": "PASS", "reason": " r "} fin', ("pass", "r", True)),
        ('{"verdict": "unknown", "reason": "vacía"}', ("unknown", "vacía", True)),
    ],
)
def test_parse_verdict(text, expected):
    assert parse_verdict(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "pass",
        '{"verdict": "maybe", "reason": "x"}',
        '{"verdict": "pass"}',
        '{"verdict": "pass", "reason": 3}',
        '{"verdict": "pass", "reason": "x"',
        "[1, 2]",
    ],
)
def test_unparseable_verdicts_are_unknown(text):
    verdict, reason, ok = parse_verdict(text)
    assert (verdict, ok) == ("unknown", False)
    assert reason.startswith("respuesta del juez no interpretable")


def test_prompts_are_versioned_and_render():
    prompts = load_judge_prompts()
    for criterion, prompt in prompts.items():
        assert prompt.version.startswith(f"judge_{criterion}_v1@")
        messages = prompt.render(question="Q?", answer="A.", context="[1] ctx", key_points="- k")
        assert [m["role"] for m in messages] == ["system", "user"]
        assert "{{" not in messages[0]["content"] + messages[1]["content"]
        assert "Q?" in messages[1]["content"] and "A." in messages[1]["content"]
        assert '"verdict"' in messages[0]["content"]


def test_prompt_needs_both_sections(tmp_path):
    path = tmp_path / "p.md"
    path.write_text("## system\nhola\n", encoding="utf-8")
    with pytest.raises(ValueError, match="## user"):
        load_prompt(path)
    path.write_text("nota\n## system\nS {{x}}\n## user\nU\n", encoding="utf-8")
    prompt = load_prompt(path)
    with pytest.raises(KeyError):
        prompt.render()
    assert prompt.render(x="1")[0]["content"] == "S 1"


class StubLLM:
    def __init__(self, text):
        self.text = text
        self.calls = []

    def complete(self, messages, *, json_schema=None, max_tokens, temperature=0):
        self.calls.append((messages, json_schema, max_tokens, temperature))
        return type(
            "R",
            (),
            {
                "text": self.text,
                "model": "judge-x",
                "provider": "groq",
                "tokens_in": 50,
                "tokens_out": 10,
            },
        )()


def test_judge_evaluates_one_criterion_per_call():
    llm = StubLLM('{"verdict": "fail", "reason": "afirma algo sin respaldo"}')
    judge = Judge(llm=llm, model_id="judge-x", max_tokens=123)
    v = judge.evaluate(
        "faithfulness",
        question="¿Q?",
        answer="R",
        context=[(1, "EN 1106b", "T")],
        key_points=["a|b"],
    )
    assert (v.criterion, v.verdict, v.parse_ok, v.source) == ("faithfulness", "fail", True, "judge")
    assert (v.model, v.provider, v.tokens_in) == ("judge-x", "groq", 50)
    messages, schema, max_tokens, temperature = llm.calls[0]
    assert schema == VERDICT_SCHEMA and max_tokens == 123 and temperature == 0
    assert "[1] EN 1106b\nT" in messages[1]["content"]
    garbage = Judge(llm=StubLLM("no sé"), model_id="j", max_tokens=10).evaluate(
        "key_points", question="q", answer="a", key_points=["x"]
    )
    assert (garbage.verdict, garbage.parse_ok) == ("unknown", False)


def test_criteria_for():
    answer = Case.model_validate(case("a"))
    premise = Case.model_validate(
        case("b", type="false_premise", expected_behavior="correct_premise")
    )
    abstain = Case.model_validate(abstain_case("c"))
    assert criteria_for(answer, answered=True) == ["faithfulness", "key_points"]
    assert criteria_for(answer, answered=False) == ["key_points"]
    assert criteria_for(premise, answered=True) == [
        "faithfulness",
        "key_points",
        "premise_corrected",
    ]
    assert criteria_for(abstain, answered=True) == ["faithfulness"]
    assert criteria_for(abstain, answered=False) == []


def test_format_key_points():
    assert format_key_points(["a|b", "c"]) == "- a | b\n- c"
    assert format_key_points([]) == "(ninguno)"


def test_judge_settings_swap_in_the_judge_model():
    s = Settings(
        judge_base_url="https://j.example/v1",
        judge_model="j-1",
        judge_api_key="k",
        llm_fallback_base_url="https://f",
        llm_fallback_model="f",
    )
    js = judge_settings(s)
    assert (js.llm_base_url, js.llm_model, js.llm_api_key) == ("https://j.example/v1", "j-1", "k")
    assert js.llm_fallback_base_url == "" and js.llm_fallback_model == ""
    assert s.llm_model != "j-1"  # the original is untouched
