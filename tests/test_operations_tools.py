from datetime import date

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)
from openweight_platform.operations.tools import build_operational_tools


class FakeOperationsLookupService:
    def __init__(self):
        self.employee_calls = []
        self.contractor_calls = []
        self.access_request_calls = []

    def lookup_employee(self, identifier):
        self.employee_calls.append(identifier)

        if identifier == "missing":
            return None

        return Employee(
            employee_id="fic-emp-001",
            full_name="Avery Example",
            employment_status="active",
            department="Information Security",
            manager_employee_id=None,
            mfa_enrolled=True,
            security_training_current=False,
        )

    def lookup_contractor(self, identifier):
        self.contractor_calls.append(identifier)
        return Contractor(
            contractor_id="fic-ctr-001",
            full_name="Gray Example",
            vendor_name="Fictional Northstar Services",
            engagement_status="active",
            sponsor_employee_id="fic-emp-004",
            engagement_end_date=date(2027, 3, 31),
            managed_identity=True,
            mfa_enrolled=True,
        )

    def lookup_access_request(self, request_id):
        self.access_request_calls.append(request_id)
        return AccessRequest(
            request_id="fic-req-001",
            subject_type="employee",
            subject_id="fic-emp-002",
            system_name="Fictional Ledger",
            requested_role="finance_reviewer",
            approval_status="approved",
            requested_start_date=date(2026, 1, 15),
            requested_end_date=None,
        )


def build_tools_by_name():
    service = FakeOperationsLookupService()
    tools = build_operational_tools(service)
    return service, {tool.name: tool for tool in tools}


def test_factory_returns_exactly_three_expected_tools():
    service = FakeOperationsLookupService()

    tools = build_operational_tools(service)

    assert len(tools) == 3
    assert [tool.name for tool in tools] == [
        "get_employee_record",
        "get_contractor_record",
        "get_access_request",
    ]


def test_tools_expose_expected_input_schemas():
    _, tools = build_tools_by_name()

    employee_schema = tools["get_employee_record"].args_schema.model_json_schema()
    contractor_schema = tools[
        "get_contractor_record"
    ].args_schema.model_json_schema()
    request_schema = tools["get_access_request"].args_schema.model_json_schema()

    assert employee_schema["required"] == ["identifier"]
    assert employee_schema["properties"]["identifier"]["type"] == "string"
    assert contractor_schema["required"] == ["identifier"]
    assert contractor_schema["properties"]["identifier"]["type"] == "string"
    assert request_schema["required"] == ["request_id"]
    assert request_schema["properties"]["request_id"]["type"] == "string"


def test_tools_have_useful_descriptions():
    _, tools = build_tools_by_name()

    assert "employee" in tools["get_employee_record"].description.lower()
    assert "exact" in tools["get_employee_record"].description.lower()
    assert "contractor" in tools["get_contractor_record"].description.lower()
    assert "exact" in tools["get_contractor_record"].description.lower()
    assert "access-request" in tools["get_access_request"].description.lower()
    assert "exact" in tools["get_access_request"].description.lower()


def test_employee_tool_calls_service_and_preserves_structured_fields():
    service, tools = build_tools_by_name()

    result = tools["get_employee_record"].invoke(
        {"identifier": "Avery Example"}
    )

    assert service.employee_calls == ["Avery Example"]
    assert result == {
        "found": True,
        "record": {
            "employee_id": "fic-emp-001",
            "full_name": "Avery Example",
            "employment_status": "active",
            "department": "Information Security",
            "manager_employee_id": None,
            "mfa_enrolled": True,
            "security_training_current": False,
        },
    }


def test_contractor_tool_calls_service_and_preserves_structured_fields():
    service, tools = build_tools_by_name()

    result = tools["get_contractor_record"].invoke(
        {"identifier": "fic-ctr-001"}
    )

    assert service.contractor_calls == ["fic-ctr-001"]
    assert result["found"] is True
    assert result["record"]["contractor_id"] == "fic-ctr-001"
    assert result["record"]["engagement_end_date"] == date(2027, 3, 31)
    assert result["record"]["managed_identity"] is True


def test_access_request_tool_calls_service_and_preserves_structured_fields():
    service, tools = build_tools_by_name()

    result = tools["get_access_request"].invoke(
        {"request_id": "fic-req-001"}
    )

    assert service.access_request_calls == ["fic-req-001"]
    assert result["found"] is True
    assert result["record"]["request_id"] == "fic-req-001"
    assert result["record"]["approval_status"] == "approved"
    assert result["record"]["requested_start_date"] == date(2026, 1, 15)
    assert result["record"]["requested_end_date"] is None


def test_missing_record_has_clear_structured_result():
    service, tools = build_tools_by_name()

    result = tools["get_employee_record"].invoke({"identifier": "missing"})

    assert service.employee_calls == ["missing"]
    assert result == {
        "found": False,
        "record": None,
    }
