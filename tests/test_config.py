from study_assistant.core.config import Settings


def test_resolved_database_url_converts_postgres_scheme_for_asyncpg():
    settings = Settings(database_url="postgresql://user:pass@db.railway.internal:5432/railway")

    assert settings.resolved_database_url == "postgresql+asyncpg://user:pass@db.railway.internal:5432/railway"


def test_resolved_database_url_keeps_sqlite_untouched():
    settings = Settings(database_url="sqlite+aiosqlite:///./study_assistant.db")

    assert settings.resolved_database_url == "sqlite+aiosqlite:///./study_assistant.db"


def test_resolved_database_url_falls_back_when_value_is_invalid():
    settings = Settings(database_url="${{study-assistant-db.DATABASE_URL}}")

    assert settings.resolved_database_url == "sqlite+aiosqlite:///./study_assistant.db"


def test_resolved_database_url_uses_pg_component_variables_when_database_url_is_invalid():
    settings = Settings(
        database_url="${{study-assistant-db.DATABASE_URL}}",
        pghost="db.railway.internal",
        pgport=5432,
        pgdatabase="railway",
        pguser="postgres",
        pgpassword="secret",
    )

    assert settings.resolved_database_url == (
        "postgresql+asyncpg://postgres:secret@db.railway.internal:5432/railway"
    )


def test_database_backend_label_marks_local_sqlite_fallback():
    settings = Settings(database_url="${{study-assistant-db.DATABASE_URL}}")

    assert settings.database_backend_label == "sqlite+aiosqlite (local fallback)"
