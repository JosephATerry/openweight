from datetime import date

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)


def test_employee_construction_preserves_fields():
    employee = Employee(
        employee_id="emp-001",
        full_name="Avery Chen",
        employment_status="active",
        department="Security",
        manager_employee_id="emp-000",
        mfa_enrolled=True,
        security_training_current=True,
    )

    assert employee.employee_id == "emp-001"
    assert employee.full_name == "Avery Chen"
    assert employee.employment_status == "active"
    assert employee.department == "Security"
    assert employee.manager_employee_id == "emp-000"
    assert employee.mfa_enrolled is True
    assert employee.security_training_current is True


def test_contractor_construction_preserves_fields():
    engagement_end_date = date(2027, 3, 31)
    contractor = Contractor(
        contractor_id="ctr-001",
        full_name="Jordan Rivera",
        vendor_name="Example Consulting",
        engagement_status="active",
        sponsor_employee_id="emp-001",
        engagement_end_date=engagement_end_date,
        managed_identity=True,
        mfa_enrolled=False,
    )

    assert contractor.contractor_id == "ctr-001"
    assert contractor.full_name == "Jordan Rivera"
    assert contractor.vendor_name == "Example Consulting"
    assert contractor.engagement_status == "active"
    assert contractor.sponsor_employee_id == "emp-001"
    assert contractor.engagement_end_date == engagement_end_date
    assert contractor.managed_identity is True
    assert contractor.mfa_enrolled is False


def test_access_request_construction_preserves_fields():
    requested_start_date = date(2026, 9, 1)
    request = AccessRequest(
        request_id="req-001",
        subject_type="contractor",
        subject_id="ctr-001",
        system_name="Example ERP",
        requested_role="auditor",
        approval_status="pending",
        requested_start_date=requested_start_date,
        requested_end_date=None,
    )

    assert request.request_id == "req-001"
    assert request.subject_type == "contractor"
    assert request.subject_id == "ctr-001"
    assert request.system_name == "Example ERP"
    assert request.requested_role == "auditor"
    assert request.approval_status == "pending"
    assert request.requested_start_date == requested_start_date
    assert request.requested_end_date is None
