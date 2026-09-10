from datetime import date

import pytest

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)
from openweight_platform.operations.service import (
    AmbiguousLookupError,
    OperationsLookupService,
)
from openweight_platform.rag.database import PostgresConfig


EMPLOYEE_ROW = (
    "fic-emp-001",
    "Avery Example",
    "active",
    "Information Security",
    None,
    True,
    False,
)

CONTRACTOR_ROW = (
    "fic-ctr-001",
    "Gray Example",
    "Fictional Northstar Services",
    "active",
    "fic-emp-004",
    date(2027, 3, 31),
    True,
    True,
)

ACCESS_REQUEST_ROW = (
    "fic-req-001",
    "employee",
    "fic-emp-002",
    "Fictional Ledger",
    "finance_reviewer",
    "approved",
    date(2026, 1, 15),
    None,
)


class FakeCursor:
    def __init__(self, query_results):
        self.query_results = list(query_results)
        self.executions = []
        self.current_rows = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, parameters):
        self.executions.append((query, parameters))
        self.current_rows = self.query_results.pop(0)

    def fetchone(self):
        return self.current_rows[0] if self.current_rows else None

    def fetchall(self):
        return self.current_rows


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def cursor(self):
        return self.fake_cursor


class FakeConnectionFactory:
    def __init__(self, query_results):
        self.cursor = FakeCursor(query_results)
        self.connect_calls = []

    def __call__(self, **kwargs):
        self.connect_calls.append(kwargs)
        return FakeConnection(self.cursor)


def build_service(query_results):
    config = PostgresConfig(
        database="operations_test",
        user="test_user",
        password="test_password",
    )
    factory = FakeConnectionFactory(query_results)
    service = OperationsLookupService(config, connection_factory=factory)
    return service, factory


def test_employee_lookup_by_id_returns_all_dataclass_fields():
    service, factory = build_service([[EMPLOYEE_ROW]])

    result = service.lookup_employee("fic-emp-001")

    assert result == Employee(
        employee_id="fic-emp-001",
        full_name="Avery Example",
        employment_status="active",
        department="Information Security",
        manager_employee_id=None,
        mfa_enrolled=True,
        security_training_current=False,
    )
    assert len(factory.cursor.executions) == 1


def test_employee_lookup_by_exact_name():
    service, factory = build_service([[], [EMPLOYEE_ROW]])

    result = service.lookup_employee("Avery Example")

    assert isinstance(result, Employee)
    assert result.employee_id == "fic-emp-001"
    assert len(factory.cursor.executions) == 2
    assert "WHERE full_name = %s" in factory.cursor.executions[1][0]


def test_contractor_lookup_by_id_returns_all_dataclass_fields():
    service, factory = build_service([[CONTRACTOR_ROW]])

    result = service.lookup_contractor("fic-ctr-001")

    assert result == Contractor(
        contractor_id="fic-ctr-001",
        full_name="Gray Example",
        vendor_name="Fictional Northstar Services",
        engagement_status="active",
        sponsor_employee_id="fic-emp-004",
        engagement_end_date=date(2027, 3, 31),
        managed_identity=True,
        mfa_enrolled=True,
    )
    assert len(factory.cursor.executions) == 1


def test_contractor_lookup_by_exact_name():
    service, factory = build_service([[], [CONTRACTOR_ROW]])

    result = service.lookup_contractor("Gray Example")

    assert isinstance(result, Contractor)
    assert result.contractor_id == "fic-ctr-001"
    assert len(factory.cursor.executions) == 2
    assert "WHERE full_name = %s" in factory.cursor.executions[1][0]


def test_access_request_lookup_returns_all_dataclass_fields():
    service, factory = build_service([[ACCESS_REQUEST_ROW]])

    result = service.lookup_access_request("fic-req-001")

    assert result == AccessRequest(
        request_id="fic-req-001",
        subject_type="employee",
        subject_id="fic-emp-002",
        system_name="Fictional Ledger",
        requested_role="finance_reviewer",
        approval_status="approved",
        requested_start_date=date(2026, 1, 15),
        requested_end_date=None,
    )
    assert len(factory.cursor.executions) == 1


def test_access_request_listing_is_bounded_filtered_and_parameterized():
    service, factory = build_service([[ACCESS_REQUEST_ROW]])

    result = service.list_access_requests(
        approval_status="approved",
        limit=10,
    )

    assert result == [AccessRequest(*ACCESS_REQUEST_ROW)]
    query, parameters = factory.cursor.executions[0]
    assert "%s::text IS NULL" in query
    assert "ORDER BY request_id" in query
    assert "LIMIT %s" in query
    assert parameters == ("approved", "approved", 10)


@pytest.mark.parametrize(
    ("approval_status", "limit"),
    [("unknown", 10), (None, 0), (None, 101), (None, True)],
)
def test_access_request_listing_rejects_unbounded_or_unknown_filters(
    approval_status,
    limit,
):
    service, factory = build_service([])

    with pytest.raises(ValueError):
        service.list_access_requests(
            approval_status=approval_status,
            limit=limit,
        )

    assert factory.connect_calls == []


def test_missing_result_returns_none():
    service, _ = build_service([[], []])

    assert service.lookup_employee("Missing Person") is None


def test_ambiguous_exact_name_raises_lookup_error():
    second_employee_row = (
        "fic-emp-999",
        *EMPLOYEE_ROW[1:],
    )
    service, _ = build_service([[], [EMPLOYEE_ROW, second_employee_row]])

    with pytest.raises(AmbiguousLookupError, match="Avery Example"):
        service.lookup_employee("Avery Example")


@pytest.mark.parametrize(
    "method_name",
    [
        "lookup_employee",
        "lookup_contractor",
        "lookup_access_request",
    ],
)
@pytest.mark.parametrize("identifier", ["", "   "])
def test_empty_identifier_is_rejected_before_connecting(method_name, identifier):
    service, factory = build_service([])

    with pytest.raises(ValueError, match="non-empty string"):
        getattr(service, method_name)(identifier)

    assert factory.connect_calls == []


def test_queries_use_parameters_instead_of_interpolation():
    identifier = "Avery Example'; DROP TABLE operations.employees; --"
    service, factory = build_service([[], []])

    result = service.lookup_employee(identifier)

    assert result is None
    assert len(factory.cursor.executions) == 2
    for query, parameters in factory.cursor.executions:
        assert "%s" in query
        assert identifier not in query
        assert parameters == (identifier,)
