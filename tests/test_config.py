from study_assistant.core.config import Settings


def test_resolved_database_url_converts_postgres_scheme_for_asyncpg():
    settings = Settings(database_url="postgresql://user:pass@db.railway.internal:5432/railway")

    assert settings.resolved_database_url == "postgresql+asyncpg://user:pass@db.railway.internal:5432/railway"


def test_resolved_database_url_keeps_sqlite_untouched():
    settings = Settings(database_url="sqlite+aiosqlite:///./study_assistant.db")

    assert settings.resolved_database_url == "sqlite+aiosqlite:///./study_assistant.db"
