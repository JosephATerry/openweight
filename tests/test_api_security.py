from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from openweight_platform.api.app import create_app
from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.contracts import DependencyStatus
from openweight_platform.api.runtime import (
    AgentExecution,
    ApprovalOutcome,
    GroundedPolicyAnswer,
    PendingApproval,
    PolicyStreamEvent,
)
from openweight_platform.api.security import JwtTokenVerifier
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
)


ISSUER = "https://login.example.invalid/tenant/v2.0"
AUDIENCE = "api://openweight-platform"
JWKS = "https://login.example.invalid/tenant/discovery/v2.0/keys"


class FakeRuntime:
    backend_alias = "fake"
    inference_state = "not_initialized"

    async def readiness(self) -> list[DependencyStatus]:
        return [
            DependencyStatus(
                name="fake",
                status="ready",
                required=True,
                detail="available",
            )
        ]

    async def execute_query(self, question: str, *, request_id: str) -> AgentExecution:
        assert question and request_id
        return AgentExecution(
            route="policy",
            routing_decision={"route": "policy"},
            result={"answer": "safe"},
        )

    async def query_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> GroundedPolicyAnswer:
        assert question and request_id
        return GroundedPolicyAnswer(
            status="insufficient_evidence",
            answer="No sufficient internal policy evidence was found.",
            citations=[],
            citation_valid=False,
            evidence=[],
        )

    async def stream_policy(
        self,
        question: str,
        *,
        request_id: str,
    ):
        result = await self.query_policy(question, request_id=request_id)
        yield PolicyStreamEvent(kind="stage", value="searching")
        yield PolicyStreamEvent(kind="complete", result=result)

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        return PendingApproval(
            approval_id="approval-test",
            proposal=ActionProposal(
                action_type="set_access_request_status",
                summary="A fixed action is pending.",
                arguments={
                    "request_id": access_request_id,
                    "new_status": new_status,
                },
                consequence="The status changes only after approval.",
            ),
        )

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        return ApprovalOutcome(
            approval_id=approval_id,
            status="approved" if decision.decision == "approve" else "rejected",
            action_result=None,
        )

    async def close(self) -> None:
        return None


@pytest.fixture(scope="module")
def keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key(), other


def settings(**overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
            "OPENWEIGHT_AUTH_ENABLED": "true",
            "OPENWEIGHT_AUTH_ISSUER": ISSUER,
            "OPENWEIGHT_AUTH_AUDIENCE": AUDIENCE,
            "OPENWEIGHT_AUTH_JWKS_URL": JWKS,
            "OPENWEIGHT_METRICS_ACCESS_MODE": "protected",
            **overrides,
        }
    )


def token(
    private_key,
    *,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    roles: list[str] | None = None,
    scopes: str | None = None,
    expires: timedelta = timedelta(minutes=5),
    not_before: timedelta = timedelta(seconds=-1),
) -> str:
    now = datetime.now(timezone.utc)
    claims: dict[str, object] = {
        "sub": "fixture-subject",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now + not_before,
        "exp": now + expires,
    }
    if roles is not None:
        claims["roles"] = roles
    if scopes is not None:
        claims["scp"] = scopes
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "fixture"})


def call(application, method: str, path: str, **kwargs) -> httpx.Response:
    async def perform() -> httpx.Response:
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

    return asyncio.run(perform())


def app(keys, **setting_overrides: str):
    _, public, _ = keys
    verifier = JwtTokenVerifier(
        issuer=ISSUER,
        audience=AUDIENCE,
        jwks_url=JWKS,
        key_resolver=lambda _: public,
    )
    return create_app(
        settings=settings(**setting_overrides),
        runtime=FakeRuntime(),
        token_verifier=verifier,
    )


def bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def test_health_and_readiness_are_public_but_product_api_requires_auth(keys) -> None:
    application = app(keys)

    assert call(application, "GET", "/healthz").status_code == 200
    assert call(application, "GET", "/readyz").status_code == 200
    response = call(application, "GET", "/v1/service-info")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


def test_public_demo_read_does_not_authorize_governed_actions(keys) -> None:
    application = app(keys, OPENWEIGHT_PUBLIC_READ_ENABLED="true")

    info = call(application, "GET", "/v1/service-info")
    query = call(
        application,
        "POST",
        "/v1/policy/query",
        json={"question": "What does the policy permit?"},
    )
    proposal = call(
        application,
        "POST",
        "/v1/actions/access-requests/req-1/proposals",
        json={"new_status": "approved"},
    )
    malformed = call(
        application,
        "GET",
        "/v1/service-info",
        headers={"Authorization": "Bearer malformed"},
    )

    assert info.status_code == 200
    assert query.status_code == 200
    assert proposal.status_code == 401
    assert malformed.status_code == 401


@pytest.mark.parametrize(
    "kind",
    ["invalid_signature", "wrong_issuer", "wrong_audience", "expired", "not_yet_valid"],
)
def test_invalid_jwt_conditions_fail_closed(keys, kind: str) -> None:
    private, _, other = keys
    kwargs: dict[str, object] = {"roles": ["OpenWeight.Reader"]}
    signing_key = private
    if kind == "invalid_signature":
        signing_key = other
    elif kind == "wrong_issuer":
        kwargs["issuer"] = "https://wrong.example.invalid/"
    elif kind == "wrong_audience":
        kwargs["audience"] = "api://wrong"
    elif kind == "expired":
        kwargs["expires"] = timedelta(seconds=-1)
    else:
        kwargs["not_before"] = timedelta(minutes=5)

    response = call(
        app(keys),
        "GET",
        "/v1/service-info",
        headers=bearer(token(signing_key, **kwargs)),
    )

    assert response.status_code == 401
    assert response.json()["error"]["message"] == "Valid bearer authentication is required."


def test_reader_can_query_but_cannot_propose_or_approve(keys) -> None:
    private, _, _ = keys
    headers = bearer(token(private, roles=["OpenWeight.Reader"]))
    application = app(keys)

    query = call(
        application,
        "POST",
        "/v1/agent/query",
        headers=headers,
        json={"question": "What does the policy permit?"},
    )
    policy_query = call(
        application,
        "POST",
        "/v1/policy/query",
        headers=headers,
        json={"question": "What does the policy permit?"},
    )
    proposal = call(
        application,
        "POST",
        "/v1/actions/access-requests/req-1/proposals",
        headers=headers,
        json={"new_status": "approved"},
    )
    approval = call(
        application,
        "POST",
        "/v1/approvals/approval-test/resume",
        headers=headers,
        json={"decision": "approve"},
    )

    assert query.status_code == 200
    assert policy_query.status_code == 200
    assert proposal.status_code == 403
    assert approval.status_code == 403


def test_policy_query_requires_authentication(keys) -> None:
    response = call(
        app(keys),
        "POST",
        "/v1/policy/query",
        json={"question": "What does the policy permit?"},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"

    stream_response = call(
        app(keys),
        "POST",
        "/v1/policy/query/stream",
        json={"question": "What does the policy permit?"},
    )
    assert stream_response.status_code == 401


def test_operator_and_approver_permissions_are_bounded(keys) -> None:
    private, _, _ = keys
    operator = bearer(token(private, roles=["OpenWeight.Operator"]))
    approver = bearer(token(private, roles=["OpenWeight.Approver"]))
    application = app(keys)

    assert call(
        application,
        "POST",
        "/v1/actions/access-requests/req-1/proposals",
        headers=operator,
        json={"new_status": "approved"},
    ).status_code == 202
    assert call(
        application,
        "POST",
        "/v1/approvals/approval-test/resume",
        headers=approver,
        json={"decision": "approve"},
    ).status_code == 200


def test_metrics_are_protected_or_absent_by_configuration(keys) -> None:
    private, _, _ = keys
    reader = bearer(token(private, roles=["OpenWeight.Reader"]))
    operator = bearer(token(private, roles=["OpenWeight.Operator"]))

    assert call(app(keys), "GET", "/metrics").status_code == 401
    assert call(app(keys), "GET", "/metrics", headers=reader).status_code == 403
    assert call(app(keys), "GET", "/metrics", headers=operator).status_code == 200
    assert call(
        app(keys, OPENWEIGHT_METRICS_ACCESS_MODE="disabled"),
        "GET",
        "/metrics",
        headers=operator,
    ).status_code == 404


def test_bearer_token_is_not_returned_or_logged(keys, capsys) -> None:
    private, _, _ = keys
    marker = token(private, roles=["OpenWeight.Reader"])

    response = call(
        app(keys),
        "GET",
        "/v1/service-info",
        headers=bearer(marker),
    )
    captured = capsys.readouterr()

    assert response.status_code == 200
    assert marker not in response.text
    assert marker not in captured.out
    assert marker not in captured.err
