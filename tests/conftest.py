"""Hermetic settings for every test.

Tests must not depend on the developer's shell, `.env` or `.env.<ENV>`: a stray
`EMBEDDING_PROVIDER=fake`, `ENV=prod` or `ACTIVE_INDEX_VERSION` would change results, and a
production `DATABASE_URL` sourced into the terminal would point the DB tests (which create
and drop schemas) at production.

So, when this file is imported (before any test module):

- the database for the DB tests is chosen from TEST_DATABASE_URL, else DATABASE_URL, else
  the local default of docker-compose.yml, and refused when ENV=prod or the host is not
  local (the DB tests then skip, see tests.test_db_support.test_database_url);
- every environment variable named like a Settings field is removed, and DATABASE_URL is
  set to the chosen test database (or removed when it was refused);
- Settings stops reading `.env` files.

The settings cache is cleared before and after each test.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from app.settings import Settings, get_settings

DEFAULT_TEST_DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/arag"
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}


def choose_test_database(environ: dict[str, str]) -> tuple[str | None, str]:
    """(url, reason): the database the DB tests may use, or None and why not."""
    url = environ.get("TEST_DATABASE_URL") or environ.get("DATABASE_URL")
    url = url or DEFAULT_TEST_DATABASE_URL
    if environ.get("ENV", "").strip().lower() == "prod":
        return None, "ENV=prod: los tests de base no corren contra producción"
    try:
        params = psycopg.conninfo.conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return None, "DATABASE_URL de tests ilegible"
    hosts = [h.strip() for h in str(params.get("host") or "").split(",")]
    remote = [h for h in hosts if h not in _LOCAL_HOSTS and not h.startswith("/")]
    if remote:
        return None, f"la base de tests no es local ({', '.join(remote)}): usar TEST_DATABASE_URL"
    return url, ""


TEST_DATABASE_URL, TEST_DATABASE_REFUSED = choose_test_database(dict(os.environ))

_FIELDS = {name.upper() for name in Settings.model_fields}
for _name in list(os.environ):
    if _name.upper() in _FIELDS:
        del os.environ[_name]
if TEST_DATABASE_URL is not None:
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
Settings.model_config["env_file"] = None
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
