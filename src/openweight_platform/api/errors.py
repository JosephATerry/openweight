"""Stable service errors mapped to safe public HTTP responses."""

from __future__ import annotations


class ServiceError(Exception):
    status_code = 500
    code = "internal_error"
    public_message = "The service could not complete the request."


class InvalidRequestError(ServiceError):
    status_code = 400
    code = "invalid_request"
    public_message = "The request is not valid for the requested operation."


class AuthenticationRequiredError(ServiceError):
    status_code = 401
    code = "authentication_required"
    public_message = "Valid bearer authentication is required."


class PermissionDeniedError(ServiceError):
    status_code = 403
    code = "permission_denied"
    public_message = "The authenticated identity is not authorized for this operation."


class ResourceNotFoundError(ServiceError):
    status_code = 404
    code = "not_found"
    public_message = "The requested resource was not found."


class StateConflictError(ServiceError):
    status_code = 409
    code = "state_conflict"
    public_message = "The request conflicts with the current resource state."


class PublicDemoBusyError(ServiceError):
    status_code = 429
    code = "public_demo_busy"
    public_message = (
        "The public demo is receiving several requests. Please try again shortly."
    )


class DependencyUnavailableError(ServiceError):
    status_code = 503
    code = "dependency_unavailable"
    public_message = "A required service dependency is unavailable."


class UnsafeResultError(ServiceError):
    status_code = 500
    code = "unsafe_internal_result"
    public_message = "The service produced a result that cannot be exposed."


__all__ = [
    "AuthenticationRequiredError",
    "DependencyUnavailableError",
    "InvalidRequestError",
    "PermissionDeniedError",
    "PublicDemoBusyError",
    "ResourceNotFoundError",
    "ServiceError",
    "StateConflictError",
    "UnsafeResultError",
]
