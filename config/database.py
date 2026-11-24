"""Portable database configuration for SQLite, PostgreSQL, and MySQL."""

import os
from pathlib import Path

import dj_database_url


SUPPORTED_DATABASE_ENGINES = {"sqlite", "postgresql", "mysql"}


def build_database_config(base_dir: Path):
    """Build Django's default database configuration from environment variables.

    ``DATABASE_URL`` takes precedence and supports the same three providers. When
    no URL is supplied, choose an engine with ``DATABASE_ENGINE`` and provide its
    explicit connection settings. This keeps local SQLite setup zero-config while
    making managed PostgreSQL and MySQL deployments equally straightforward.
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    conn_max_age = int(os.getenv("DATABASE_CONN_MAX_AGE", "60"))
    if database_url:
        return dj_database_url.parse(database_url, conn_max_age=conn_max_age)

    engine = os.getenv("DATABASE_ENGINE", "sqlite").lower()
    if engine not in SUPPORTED_DATABASE_ENGINES:
        supported = ", ".join(sorted(SUPPORTED_DATABASE_ENGINES))
        raise ValueError(f"DATABASE_ENGINE must be one of: {supported}.")

    if engine == "sqlite":
        database_name = os.getenv("DATABASE_NAME", str(base_dir / "db.sqlite3"))
        return {"ENGINE": "django.db.backends.sqlite3", "NAME": database_name}

    config = {
        "ENGINE": "django.db.backends.postgresql" if engine == "postgresql" else "django.db.backends.mysql",
        "NAME": os.getenv("DATABASE_NAME", "coaching_suite"),
        "USER": os.getenv("DATABASE_USER", ""),
        "PASSWORD": os.getenv("DATABASE_PASSWORD", ""),
        "HOST": os.getenv("DATABASE_HOST", "localhost"),
        "PORT": os.getenv("DATABASE_PORT", "5432" if engine == "postgresql" else "3306"),
        "CONN_MAX_AGE": conn_max_age,
    }
    if engine == "mysql":
        config["OPTIONS"] = {"charset": "utf8mb4"}
    return config
