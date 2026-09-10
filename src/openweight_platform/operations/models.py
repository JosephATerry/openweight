"""Data models for fictional operational records."""

from dataclasses import dataclass
from datetime import date


@dataclass
class Employee:
    employee_id: str
    full_name: str
    employment_status: str
    department: str
    manager_employee_id: str | None
    mfa_enrolled: bool
    security_training_current: bool


@dataclass
class Contractor:
    contractor_id: str
    full_name: str
    vendor_name: str
    engagement_status: str
    sponsor_employee_id: str
    engagement_end_date: date
    managed_identity: bool
    mfa_enrolled: bool


@dataclass
class AccessRequest:
    request_id: str
    subject_type: str
    subject_id: str
    system_name: str
    requested_role: str
    approval_status: str
    requested_start_date: date
    requested_end_date: date | None
