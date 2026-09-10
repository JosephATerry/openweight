"""LangChain tools for the native operations lookup service."""

from dataclasses import asdict
from typing import TypedDict

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)
from openweight_platform.operations.service import OperationsLookupService


class EmployeeLookupInput(BaseModel):
    identifier: str = Field(description="Exact employee ID or full name.")


class ContractorLookupInput(BaseModel):
    identifier: str = Field(description="Exact contractor ID or full name.")


class AccessRequestLookupInput(BaseModel):
    request_id: str = Field(description="Exact access-request ID.")


class OperationalToolResult(TypedDict):
    found: bool
    record: dict[str, object] | None


def _structured_result(
    record: Employee | Contractor | AccessRequest | None,
) -> OperationalToolResult:
    return {
        "found": record is not None,
        "record": asdict(record) if record is not None else None,
    }


def build_operational_tools(
    service: OperationsLookupService,
) -> list[BaseTool]:
    """Build LangChain tools backed by a native operations lookup service."""

    def get_employee_record(identifier: str) -> OperationalToolResult:
        return _structured_result(service.lookup_employee(identifier))

    def get_contractor_record(identifier: str) -> OperationalToolResult:
        return _structured_result(service.lookup_contractor(identifier))

    def get_access_request(request_id: str) -> OperationalToolResult:
        return _structured_result(service.lookup_access_request(request_id))

    return [
        StructuredTool.from_function(
            func=get_employee_record,
            name="get_employee_record",
            description=(
                "Retrieve an employee operations record by exact employee ID "
                "or exact full name."
            ),
            args_schema=EmployeeLookupInput,
        ),
        StructuredTool.from_function(
            func=get_contractor_record,
            name="get_contractor_record",
            description=(
                "Retrieve a contractor operations record by exact contractor ID "
                "or exact full name."
            ),
            args_schema=ContractorLookupInput,
        ),
        StructuredTool.from_function(
            func=get_access_request,
            name="get_access_request",
            description=(
                "Retrieve an access-request operations record by exact request ID."
            ),
            args_schema=AccessRequestLookupInput,
        ),
    ]


__all__ = [
    "AccessRequestLookupInput",
    "ContractorLookupInput",
    "EmployeeLookupInput",
    "OperationalToolResult",
    "build_operational_tools",
]
