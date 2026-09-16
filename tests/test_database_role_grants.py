from __future__ import annotations

from collections.abc import Iterator

import pytest
from psycopg.sql import Composable

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.security import persistence


class RecordingCursor:
    def __init__(self, rows: Iterator[tuple[object, ...]]) -> None:
        self.rows = rows
        self.statements: list[str] = []
        self.parameters: list[tuple[object, ...] | None] = []

    def __enter__(self) -> RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        statement: str | Composable,
        _parameters: tuple[object, ...] | None = None,
    ) -> None:
        rendered = (
            statement.as_string(None) if isinstance(statement, Composable) else statement
        )
        self.statements.append(" ".join(rendered.split()))
        self.parameters.append(_parameters)

    def fetchone(self) -> tuple[object, ...]:
        return next(self.rows)


class RecordingConnection:
    def __init__(self, cursor: RecordingCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> RecordingConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> RecordingCursor:
        return self._cursor


@pytest.fixture
def database_config() -> DatabaseConfig:
    return DatabaseConfig(
        database="openweight_platform",
        user="migration-admin",
        password="not-a-real-secret",  # pragma: allowlist secret
        host="localhost",
        port=5432,
    )


def test_runtime_grants_first_revoke_broad_and_admin_access(
    monkeypatch: pytest.MonkeyPatch,
    database_config: DatabaseConfig,
) -> None:
    cursor = RecordingCursor(iter(((False, False), (True,))))
    connection = RecordingConnection(cursor)
    monkeypatch.setattr(
        persistence.psycopg,
        "connect",
        lambda **_kwargs: connection,
    )

    persistence.grant_runtime_database_role(database_config, "openweight_app")

    statements = "\n".join(cursor.statements)
    assert 'REVOKE CONNECT, TEMPORARY ON DATABASE "openweight_platform" FROM PUBLIC' in statements
    assert "REVOKE ALL PRIVILEGES ON SCHEMA openweight_admin FROM PUBLIC" in statements
    assert "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA openweight_admin" in statements
    assert "ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM PUBLIC" in statements
    assert 'ALTER DEFAULT PRIVILEGES REVOKE ALL ON TABLES FROM "openweight_app"' in statements
    assert 'GRANT CONNECT ON DATABASE "openweight_platform" TO "openweight_app"' in statements
    assert "GRANT UPDATE (approval_status) ON operations.access_requests" in statements
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON public.policy_chunks" in statements
    assert "GRANT TRUNCATE" not in statements


@pytest.mark.parametrize("role_exists", (True, False))
def test_runtime_role_is_forced_to_non_admin_attributes(
    monkeypatch: pytest.MonkeyPatch,
    database_config: DatabaseConfig,
    role_exists: bool,
) -> None:
    cursor = RecordingCursor(iter(((role_exists,),)))
    connection = RecordingConnection(cursor)
    connect_options: dict[str, object] = {}

    def connect(**options: object) -> RecordingConnection:
        connect_options.update(options)
        return connection

    monkeypatch.setattr(
        persistence.psycopg,
        "connect",
        connect,
    )

    persistence.ensure_runtime_database_role(
        database_config,
        "openweight_app",
        "not-a-real-runtime-secret",  # pragma: allowlist secret
    )

    statements = "\n".join(cursor.statements)
    if role_exists:
        assert "CREATE ROLE" not in statements
    else:
        assert 'CREATE ROLE "openweight_app" LOGIN NOSUPERUSER NOCREATEDB' in statements
    assert 'ALTER ROLE "openweight_app" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' in statements
    assert "NOREPLICATION NOBYPASSRLS PASSWORD %s" in statements
    assert "not-a-real-runtime-secret" not in statements
    assert cursor.parameters[-1] == ("not-a-real-runtime-secret",)
    assert connect_options["cursor_factory"] is persistence.psycopg.ClientCursor
