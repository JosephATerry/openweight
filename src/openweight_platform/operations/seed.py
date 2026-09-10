"""Deterministic fictional seed records for operational data."""

from dataclasses import astuple
from datetime import date

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)


# Names, organizations, identifiers, and records in this module are fictional.
EMPLOYEES = (
    Employee(
        employee_id="fic-emp-001",
        full_name="Avery Example",
        employment_status="active",
        department="Information Security",
        manager_employee_id=None,
        mfa_enrolled=True,
        security_training_current=True,
    ),
    Employee(
        employee_id="fic-emp-002",
        full_name="Blake Sample",
        employment_status="active",
        department="Finance Operations",
        manager_employee_id="fic-emp-001",
        mfa_enrolled=True,
        security_training_current=False,
    ),
    Employee(
        employee_id="fic-emp-003",
        full_name="Casey Placeholder",
        employment_status="inactive",
        department="Customer Success",
        manager_employee_id="fic-emp-001",
        mfa_enrolled=False,
        security_training_current=False,
    ),
    Employee(
        employee_id="fic-emp-004",
        full_name="Devon Demo",
        employment_status="active",
        department="Platform Engineering",
        manager_employee_id="fic-emp-001",
        mfa_enrolled=True,
        security_training_current=True,
    ),
    Employee(
        employee_id="fic-emp-005",
        full_name="Ellis Testperson",
        employment_status="inactive",
        department="People Operations",
        manager_employee_id="fic-emp-002",
        mfa_enrolled=False,
        security_training_current=True,
    ),
    Employee(
        employee_id="fic-emp-006",
        full_name="Finley Fiction",
        employment_status="active",
        department="Procurement",
        manager_employee_id="fic-emp-002",
        mfa_enrolled=False,
        security_training_current=False,
    ),
)

CONTRACTORS = (
    Contractor(
        contractor_id="fic-ctr-001",
        full_name="Gray Example",
        vendor_name="Fictional Northstar Services",
        engagement_status="active",
        sponsor_employee_id="fic-emp-004",
        engagement_end_date=date(2027, 3, 31),
        managed_identity=True,
        mfa_enrolled=True,
    ),
    Contractor(
        contractor_id="fic-ctr-002",
        full_name="Harper Sample",
        vendor_name="Fictional Bluebird Consulting",
        engagement_status="ended",
        sponsor_employee_id="fic-emp-002",
        engagement_end_date=date(2025, 11, 30),
        managed_identity=False,
        mfa_enrolled=False,
    ),
    Contractor(
        contractor_id="fic-ctr-003",
        full_name="Indigo Placeholder",
        vendor_name="Fictional Northstar Services",
        engagement_status="active",
        sponsor_employee_id="fic-emp-006",
        engagement_end_date=date(2026, 12, 15),
        managed_identity=True,
        mfa_enrolled=False,
    ),
    Contractor(
        contractor_id="fic-ctr-004",
        full_name="Jules Demo",
        vendor_name="Fictional Red Maple Partners",
        engagement_status="ended",
        sponsor_employee_id="fic-emp-004",
        engagement_end_date=date(2024, 8, 31),
        managed_identity=False,
        mfa_enrolled=True,
    ),
)

ACCESS_REQUESTS = (
    AccessRequest(
        request_id="fic-req-001",
        subject_type="employee",
        subject_id="fic-emp-002",
        system_name="Fictional Ledger",
        requested_role="finance_reviewer",
        approval_status="approved",
        requested_start_date=date(2026, 1, 15),
        requested_end_date=None,
    ),
    AccessRequest(
        request_id="fic-req-002",
        subject_type="employee",
        subject_id="fic-emp-006",
        system_name="Fictional Vendor Portal",
        requested_role="procurement_admin",
        approval_status="pending",
        requested_start_date=date(2026, 9, 1),
        requested_end_date=None,
    ),
    AccessRequest(
        request_id="fic-req-003",
        subject_type="contractor",
        subject_id="fic-ctr-001",
        system_name="Fictional Build Console",
        requested_role="release_viewer",
        approval_status="approved",
        requested_start_date=date(2026, 4, 1),
        requested_end_date=date(2027, 3, 31),
    ),
    AccessRequest(
        request_id="fic-req-004",
        subject_type="contractor",
        subject_id="fic-ctr-003",
        system_name="Fictional Vendor Portal",
        requested_role="vendor_editor",
        approval_status="denied",
        requested_start_date=date(2026, 8, 20),
        requested_end_date=date(2026, 12, 15),
    ),
    AccessRequest(
        request_id="fic-req-005",
        subject_type="employee",
        subject_id="fic-emp-003",
        system_name="Fictional Support Desk",
        requested_role="case_manager",
        approval_status="denied",
        requested_start_date=date(2026, 2, 1),
        requested_end_date=None,
    ),
    AccessRequest(
        request_id="fic-req-006",
        subject_type="contractor",
        subject_id="fic-ctr-004",
        system_name="Fictional Metrics Hub",
        requested_role="dashboard_viewer",
        approval_status="pending",
        requested_start_date=date(2024, 6, 1),
        requested_end_date=date(2024, 8, 31),
    ),
)


def employee_seed_rows() -> tuple[tuple[object, ...], ...]:
    """Return employee seed records in database column order."""

    return tuple(astuple(employee) for employee in EMPLOYEES)


def contractor_seed_rows() -> tuple[tuple[object, ...], ...]:
    """Return contractor seed records in database column order."""

    return tuple(astuple(contractor) for contractor in CONTRACTORS)


def access_request_seed_rows() -> tuple[tuple[object, ...], ...]:
    """Return access-request seed records in database column order."""

    return tuple(astuple(request) for request in ACCESS_REQUESTS)


def operations_seed_counts() -> dict[str, int]:
    """Return deterministic row counts for each operations seed collection."""

    return {
        "employees": len(EMPLOYEES),
        "contractors": len(CONTRACTORS),
        "access_requests": len(ACCESS_REQUESTS),
    }
