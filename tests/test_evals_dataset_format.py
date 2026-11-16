"""`make test` checks the format of the real dataset (no corpus, network or LLM needed):
schema, unique ids, twins, works and Bekker ranges against corpus/works.yaml, and no
evidence passage in both dev and test."""

from app.corpus.catalog import load_works
from evals.labels import check_format
from evals.schema import DATASET_DIR, load_dataset


def test_dataset_format():
    dataset = load_dataset(DATASET_DIR)
    errors = check_format(dataset, load_works())
    assert errors == [], "\n".join(errors)


def test_judge_prompts_load():
    from evals.judge import load_judge_prompts

    prompts = load_judge_prompts()
    assert set(prompts) == {"faithfulness", "key_points", "premise_corrected"}
