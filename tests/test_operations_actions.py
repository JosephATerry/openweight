from __future__ import annotations

import inspect

import pytest

from openweight_platform.operations.actions import (
    ALLOWED_ACCESS_REQUEST_STATUSES,
    SET_ACCESS_REQUEST_STATUS_QUERY,
    AccessRequestActionService,
    AccessRequestNotFoundError,
    AccessRequestStatusChange,
    UnsupportedAccessRequestStatusError,
)
from openweight_platform.rag.database import PostgresConfig


class FakeCursor:
    def __init__(self, row=None, *, execute_error=None) -> None:
        self.row = row
        self.execute_error = execute_error
        self.executions = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, parameters):
        self.executions.append((query, parameters))
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor) -> None:
        self.fake_cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commits += 1
        else:
            self.rollbacks += 1
        return False

    def cursor(self):
        return self.fake_cursor


class FakeConnectionFactory:
    def __init__(self, row=None, *, execute_error=None) -> None:
        self.cursor = FakeCursor(row, execute_error=execute_error)
        self.connection = FakeConnection(self.cursor)
        self.connect_calls = []

    def __call__(self, **kwargs):
        self.connect_calls.append(kwargs)
        return self.connection


def build_service(row=None, *, execute_error=None):
    config = PostgresConfig(
        database="operations_action_test",
        user="test_user",
        password="test_password",
    )
    factory = FakeConnectionFactory(row, execute_error=execute_error)
    service = AccessRequestActionService(
        config,
        connection_factory=factory,
    )
    return service, factory


def test_status_domain_matches_existing_seed_conventions() -> None:
    assert ALLOWED_ACCESS_REQUEST_STATUSES == {
        "approved",
        "pending",
        "denied",
    }


def test_valid_update_is_parameterized_and_committed() -> None:
    request_id = "fic-req-002'; DROP TABLE operations.access_requests; --"
    service, factory = build_service(
        (request_id, "pending", "approved")
    )

    result = service.set_access_request_status(request_id, "approved")

    assert result == AccessRequestStatusChange(
        request_id=request_id,
        previous_status="pending",
        new_status="approved",
        changed=True,
    )
    assert len(factory.cursor.executions) == 1
    query, parameters = factory.cursor.executions[0]
    assert query == SET_ACCESS_REQUEST_STATUS_QUERY
    assert request_id not in query
    assert "approved" not in query
    assert parameters == (request_id, "approved")
    assert "UPDATE operations.access_requests AS target" in query
    assert "SET approval_status = %s" in query
    assert "WHERE request_id = %s" in query
    assert factory.connection.commits == 1
    assert factory.connection.rollbacks == 0


def test_setting_current_status_is_allowed_and_reports_unchanged() -> None:
    service, factory = build_service(
        ("fic-req-001", "approved", "approved")
    )

    result = service.set_access_request_status("fic-req-001", "approved")

    assert result.changed is False
    assert result.previous_status == "approved"
    assert result.new_status == "approved"
    assert len(factory.cursor.executions) == 1
    assert factory.connection.commits == 1


def test_nonexistent_request_fails_and_rolls_back() -> None:
    service, factory = build_service(None)

    with pytest.raises(AccessRequestNotFoundError, match="fic-req-999"):
        service.set_access_request_status("fic-req-999", "denied")

    assert len(factory.cursor.executions) == 1
    assert factory.connection.commits == 0
    assert factory.connection.rollbacks == 1


@pytest.mark.parametrize("request_id", ["", "   ", None])
def test_empty_request_id_is_rejected_before_database_work(request_id) -> None:
    service, factory = build_service()

    with pytest.raises(ValueError, match="non-empty string"):
        service.set_access_request_status(request_id, "approved")

    assert factory.connect_calls == []


@pytest.mark.parametrize(
    "new_status",
    ["", "granted", "APPROVED", 1, None],
)
def test_unsupported_status_is_rejected_before_database_work(
    new_status,
) -> None:
    service, factory = build_service()

    with pytest.raises(UnsupportedAccessRequestStatusError):
        service.set_access_request_status("fic-req-001", new_status)

    assert factory.connect_calls == []


def test_database_failure_does_not_commit_or_report_success() -> None:
    service, factory = build_service(
        execute_error=RuntimeError("fictional database failure")
    )

    with pytest.raises(RuntimeError, match="fictional database failure"):
        service.set_access_request_status("fic-req-001", "denied")

    assert factory.connection.commits == 0
    assert factory.connection.rollbacks == 1


def test_inconsistent_database_result_rolls_back() -> None:
    service, factory = build_service(
        ("fic-req-unexpected", "pending", "approved")
    )

    with pytest.raises(RuntimeError, match="inconsistent status update"):
        service.set_access_request_status("fic-req-002", "approved")

    assert factory.connection.commits == 0
    assert factory.connection.rollbacks == 1


def test_public_method_exposes_no_arbitrary_sql_table_or_column_input() -> None:
    parameters = inspect.signature(
        AccessRequestActionService.set_access_request_status
    ).parameters

    assert list(parameters) == ["self", "request_id", "new_status"]
    assert SET_ACCESS_REQUEST_STATUS_QUERY.count(
        "UPDATE operations.access_requests"
    ) == 1
    assert "operations.access_requests" in SET_ACCESS_REQUEST_STATUS_QUERY
    assert "approval_status" in SET_ACCESS_REQUEST_STATUS_QUERY
