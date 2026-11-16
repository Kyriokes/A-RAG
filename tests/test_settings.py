"""Settings: `.env.<ENV>` overrides `.env`, and environment variables override both."""

from app.settings import Settings, env_files


def test_env_specific_file_overrides_the_base_file(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\nLLM_MODEL=base-model\n", encoding="utf-8")
    (tmp_path / ".env.prod").write_text("LLM_MODEL=prod-model\n", encoding="utf-8")

    prod = Settings(_env_file=env_files("prod", tmp_path))
    assert (prod.log_level, prod.llm_model) == ("DEBUG", "prod-model")

    local = Settings(_env_file=env_files("local", tmp_path))  # no .env.local: base only
    assert local.llm_model == "base-model"

    monkeypatch.setenv("LLM_MODEL", "from-environment")
    assert Settings(_env_file=env_files("prod", tmp_path)).llm_model == "from-environment"


def test_env_files_follow_the_env_variable(monkeypatch, tmp_path):
    monkeypatch.setenv("ENV", "prod")
    assert env_files(root=tmp_path) == (tmp_path / ".env", tmp_path / ".env.prod")
    monkeypatch.delenv("ENV")
    assert env_files(root=tmp_path)[1].name == ".env.local"


def test_tests_ignore_the_ambient_configuration(monkeypatch):
    # tests/conftest.py removed every Settings variable and stopped reading .env files, so
    # the defaults hold whatever the developer's shell or .env says.
    s = Settings()
    assert (s.env, s.embedding_provider, s.active_index_version) == ("local", "ollama", "")
    assert s.retrieval_k == Settings.model_fields["retrieval_k"].default


def test_db_tests_refuse_production_and_remote_databases():
    from tests.conftest import DEFAULT_TEST_DATABASE_URL, choose_test_database

    local = "postgresql://postgres@localhost:5432/arag_int"
    assert choose_test_database({"DATABASE_URL": local}) == (local, "")
    assert choose_test_database({"TEST_DATABASE_URL": local, "DATABASE_URL": "x"})[0] == local
    assert choose_test_database({})[0] == DEFAULT_TEST_DATABASE_URL
    assert choose_test_database({"DATABASE_URL": "host=/var/run/postgresql dbname=a"})[0]

    url, why = choose_test_database({"DATABASE_URL": local, "ENV": "prod"})
    assert url is None and "ENV=prod" in why
    for remote in (
        "postgresql://postgres.ref:pw@aws-0-eu.pooler.supabase.com:5432/postgres",
        "host=db.x.supabase.co user=postgres dbname=postgres",
        "postgresql://u@localhost,db.example.org/x",
    ):
        url, why = choose_test_database({"DATABASE_URL": remote})
        assert url is None and "no es local" in why
