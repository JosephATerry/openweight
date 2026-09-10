"""FastAPI application factory for the OpenWeight service boundary."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Annotated, Literal, cast
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server import MCPServer
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.routing import Match

from openweight_platform.api.config import SERVICE_NAME, ServiceSettings
from openweight_platform.api.contracts import (
    AccessRequestDetailResponse,
    AccessRequestListResponse,
    AccessRequestRecord,
    AccessRequestStatusProposalRequest,
    AgentQueryRequest,
    AgentQueryResponse,
    ApiErrorDetail,
    ApiErrorResponse,
    ApprovalProposalResponse,
    ApprovalResumeRequest,
    ApprovalResumeResponse,
    HealthResponse,
    PolicyEvidenceResponse,
    PolicyQueryRequest,
    PolicyQueryResponse,
    ReadinessResponse,
    ServiceInfoResponse,
)
from openweight_platform.api.errors import ServiceError
from openweight_platform.api.frontend import register_frontend_routes
from openweight_platform.api.logging import configure_api_logging
from openweight_platform.api.policy_presentation import (
    PolicyAnswerPresentation,
    present_policy_answer,
)
from openweight_platform.api.public_demo_limits import PublicDemoInferenceGate
from openweight_platform.api.observability import (
    Observability,
    create_observability,
    elapsed_seconds,
    safe_route,
)
from openweight_platform.api.runtime import (
    DefaultPlatformRuntime,
    GroundedPolicyAnswer,
    PlatformRuntime,
    safe_json_value,
)
from openweight_platform.api.request_context import (
    REQUEST_ID_HEADER,
    REQUEST_ID_PATTERN,
    choose_request_id,
    reset_request_id,
    set_request_id,
)
from openweight_platform.api.security import (
    AuthorizationBoundary,
    Permission,
    TokenVerifier,
)
from openweight_platform.mcp.server import (
    create_mcp_asgi_app,
    create_mcp_server,
)
from openweight_platform.orchestration.approval import ApprovalDecision


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unknown"))


def _route_template(request: Request) -> str:
    assigned = getattr(request.state, "route_template", None)
    if isinstance(assigned, str):
        return safe_route(assigned)
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str):
        return safe_route(route_path)
    for candidate in request.app.router.routes:
        match, _ = candidate.matches(request.scope)
        if match is Match.FULL:
            return safe_route(getattr(candidate, "path", None))
    return "unmatched"


def _operation(request: Request) -> str:
    return str(getattr(request.state, "operation", "http"))


def _service_error_type(request: Request, error: ServiceError) -> str:
    if error.code == "authentication_required":
        return "authentication_error"
    if error.code == "permission_denied":
        return "authorization_error"
    if error.code == "state_conflict":
        return "approval_conflict"
    if error.code == "public_demo_busy":
        return "rate_limited"
    if error.code == "not_found":
        return "not_found"
    if error.code == "invalid_request":
        return "validation_error"
    if error.code == "unsafe_internal_result":
        return "unsafe_result"
    if error.code == "dependency_unavailable":
        return (
            "database_unavailable"
            if _operation(request).startswith("approval")
            else "backend_unavailable"
        )
    return "internal_error"


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
) -> JSONResponse:
    payload = ApiErrorResponse(
        request_id=_request_id(request),
        error=ApiErrorDetail(code=code, message=message),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode="json"),
    )


def _sse_event(event: str, payload: object) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _policy_response(
    result: GroundedPolicyAnswer,
    *,
    request_id: str,
) -> PolicyQueryResponse:
    return PolicyQueryResponse(
        request_id=request_id,
        status=result.status,
        answer=present_policy_answer(result.answer),
        citations=result.citations,
        citation_valid=result.citation_valid,
        evidence=[
            PolicyEvidenceResponse(
                citation_id=item.citation_id,
                policy_id=item.policy_id,
                title=item.title,
                domain=item.domain,
                chunk_index=item.chunk_index,
                content=item.content,
            )
            for item in result.evidence
        ],
    )


def create_app(
    *,
    settings: ServiceSettings | None = None,
    runtime: PlatformRuntime | None = None,
    observability: Observability | None = None,
    token_verifier: TokenVerifier | None = None,
) -> FastAPI:
    """Create one testable service without eagerly loading model resources."""

    service_settings = settings or ServiceSettings.from_env()
    access_logger = configure_api_logging(service_settings.log_level)
    telemetry = observability or create_observability(service_settings)
    authorization_boundary = AuthorizationBoundary(
        service_settings,
        verifier=token_verifier,
    )
    public_demo_gate = PublicDemoInferenceGate(
        enabled=service_settings.deployment_profile == "huggingface",
        concurrency_limit=service_settings.hf_inference_concurrency_limit,
        requests_per_window=service_settings.hf_rate_limit_requests,
        window_seconds=service_settings.hf_rate_limit_window_seconds,
        max_tracked_clients=service_settings.hf_rate_limit_max_clients,
    )
    mcp_server: MCPServer | None = None
    mcp_asgi_app: object | None = None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.runtime = runtime or DefaultPlatformRuntime(
            service_settings,
            observability=telemetry,
        )
        async with AsyncExitStack() as stack:
            if mcp_server is not None:
                await stack.enter_async_context(mcp_server.session_manager.run())
            try:
                yield
            finally:
                await application.state.runtime.close()
                telemetry.shutdown()

    application = FastAPI(
        title="OpenWeight Platform API",
        summary="Guarded HTTP access to the OpenWeight enterprise agent.",
        description=(
            "The API exposes read/query capabilities and one approval-gated "
            "access-request action. It does not expose model training, "
            "evaluation, arbitrary SQL, or unrestricted tool execution."
        ),
        version=service_settings.service_version,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "service", "description": "Liveness, readiness, and metadata."},
            {"name": "agent", "description": "Guarded agent query execution."},
            {
                "name": "policy",
                "description": "Grounded internal policy questions and evidence.",
            },
            {
                "name": "approval",
                "description": "Explicitly gated operational actions.",
            },
        ],
    )
    if service_settings.hf_public_origin is not None:
        public_hostname = urlsplit(service_settings.hf_public_origin).hostname
        if public_hostname is None:  # Configuration validation guarantees this.
            raise ValueError("Hugging Face public origin has no hostname")
        application.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=[public_hostname],
        )
    application.state.settings = service_settings
    application.state.runtime = runtime
    application.state.observability = telemetry
    application.state.mcp_server = None
    application.state.public_demo_inference_gate = public_demo_gate

    if service_settings.mcp_enabled:
        mcp_server = create_mcp_server(
            settings=service_settings,
            runtime_provider=lambda: cast(
                PlatformRuntime,
                application.state.runtime,
            ),
            observability=telemetry,
            authorization_boundary=authorization_boundary,
            logger=access_logger,
        )
        mcp_asgi_app = create_mcp_asgi_app(mcp_server, service_settings)
        application.state.mcp_server = mcp_server

    @application.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied = request.headers.get(REQUEST_ID_HEADER)
        request_id = choose_request_id(supplied)
        request_id_token = set_request_id(request_id)
        request.state.request_id = request_id
        request.state.operation = "http"
        if service_settings.mcp_enabled and (
            request.url.path == service_settings.mcp_path
            or request.url.path.startswith(f"{service_settings.mcp_path}/")
            or request.url.path.startswith(
                "/.well-known/oauth-protected-resource/"
            )
        ):
            request.state.route_template = "/mcp"
        started = time.perf_counter()
        response: Response | None = None
        telemetry.observe_http_start(request.method)
        with telemetry.span(
            "http.request",
            {
                "http.request.method": request.method,
                "openweight.request_id": request_id,
                "openweight.backend": service_settings.backend_name,
                "openweight.operation": "http",
            },
        ) as span:
            try:
                response = await call_next(request)
                return response
            finally:
                elapsed = elapsed_seconds(started)
                latency_ms = round(elapsed * 1_000, 3)
                status_code = response.status_code if response is not None else 500
                route = _route_template(request)
                telemetry.set_span_attributes(
                    span,
                    {
                        "http.route": route,
                        "http.response.status_code": status_code,
                        "openweight.operation": _operation(request),
                    },
                )
                if status_code >= 400:
                    telemetry.mark_span_error(
                        span,
                        (
                            "rate_limited"
                            if status_code == 429
                            else "dependency_unavailable"
                            if status_code == 503
                            else "internal_error"
                            if status_code >= 500
                            else "validation_error"
                        ),
                    )
                telemetry.observe_http_end(
                    method=request.method,
                    route=route,
                    status_code=status_code,
                    seconds=elapsed,
                )
                if response is not None:
                    response.headers[REQUEST_ID_HEADER] = request_id
                trace_context = telemetry.trace_context()
                access_logger.info(
                    "request_completed",
                    extra={
                        "request_id": request_id,
                        "route": route,
                        "method": request.method,
                        "status_code": status_code,
                        "latency_ms": latency_ms,
                        "backend": service_settings.backend_name,
                        "operation": _operation(request),
                        "trace_id": trace_context.trace_id,
                        "span_id": trace_context.span_id,
                    },
                )
                reset_request_id(request_id_token)

    @application.exception_handler(ServiceError)
    async def service_error_handler(
        request: Request,
        error: ServiceError,
    ) -> JSONResponse:
        error_type = _service_error_type(request, error)
        telemetry.record_error(
            operation=_operation(request),
            error_type=error_type,
        )
        access_logger.warning(
            "request_rejected",
            extra={
                "request_id": _request_id(request),
                "route": _route_template(request),
                "method": request.method,
                "status_code": error.status_code,
                "operation": _operation(request),
                "error_type": error_type,
            },
        )
        return _error_response(
            request,
            status_code=error.status_code,
            code=error.code,
            message=error.public_message,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        del error
        telemetry.record_error(
            operation=_operation(request),
            error_type="validation_error",
        )
        access_logger.warning(
            "request_rejected",
            extra={
                "request_id": _request_id(request),
                "route": _route_template(request),
                "method": request.method,
                "status_code": status.HTTP_422_UNPROCESSABLE_CONTENT,
                "operation": _operation(request),
                "error_type": "validation_error",
            },
        )
        return _error_response(
            request,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="The request does not match the required schema.",
        )

    @application.exception_handler(Exception)
    async def unexpected_error_handler(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        del error
        telemetry.record_error(
            operation=_operation(request),
            error_type="internal_error",
        )
        access_logger.error(
            "request_failed",
            extra={
                "request_id": _request_id(request),
                "route": _route_template(request),
                "method": request.method,
                "status_code": 500,
                "operation": _operation(request),
                "error_type": "internal_error",
            },
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="The service could not complete the request.",
        )

    def get_runtime(request: Request) -> PlatformRuntime:
        current = request.app.state.runtime
        if current is None:
            raise RuntimeError("Application lifespan has not started")
        return current

    def require_permission(permission: Permission) -> Callable[..., object]:
        async def dependency(
            authorization: Annotated[
                str | None,
                Header(alias="Authorization", include_in_schema=False),
            ] = None,
        ) -> object:
            return authorization_boundary.require(authorization, permission)

        return dependency

    if service_settings.metrics_enabled:

        metrics_dependencies = (
            [Depends(require_permission("metrics.read"))]
            if service_settings.metrics_access_mode == "protected"
            else []
        )

        @application.get(
            "/metrics",
            include_in_schema=False,
            dependencies=metrics_dependencies,
        )
        async def metrics(request: Request) -> Response:
            request.state.operation = "http"
            return Response(
                content=telemetry.metrics_payload(),
                headers={"Content-Type": telemetry.metrics_content_type},
            )

    @application.get(
        "/healthz",
        tags=["service"],
        summary="Check API process liveness",
        response_model=HealthResponse,
    )
    async def health(request: Request) -> HealthResponse:
        return HealthResponse(
            request_id=_request_id(request),
            service=SERVICE_NAME,
        )

    @application.get(
        "/readyz",
        tags=["service"],
        summary="Check required service dependencies",
        response_model=ReadinessResponse,
        responses={503: {"model": ReadinessResponse}},
    )
    async def readiness(
        request: Request,
        response: Response,
    ) -> ReadinessResponse:
        request.state.operation = "readiness"
        platform = get_runtime(request)
        with telemetry.span(
            "dependency.readiness",
            {"openweight.operation": "readiness"},
            error_type="dependency_unavailable",
        ):
            dependencies = await platform.readiness()
        for dependency in dependencies:
            telemetry.record_dependency(
                dependency=dependency.name,
                status=dependency.status,
            )
        ready = all(
            dependency.status == "ready"
            for dependency in dependencies
            if dependency.required
        )
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(
            request_id=_request_id(request),
            status="ready" if ready else "not_ready",
            dependencies=dependencies,
        )

    @application.get(
        "/v1/service-info",
        tags=["service"],
        summary="Return safe service build metadata",
        response_model=ServiceInfoResponse,
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def service_info(
        request: Request,
    ) -> ServiceInfoResponse:
        platform = get_runtime(request)
        return ServiceInfoResponse(
            request_id=_request_id(request),
            service=SERVICE_NAME,
            version=service_settings.service_version,
            environment=service_settings.environment,
            deployment_profile=service_settings.deployment_profile,
            backend=platform.backend_alias,
            model_id=service_settings.gpt_oss_model_id,
            inference_provider=(
                f"huggingface:{service_settings.hf_provider}"
                if service_settings.deployment_profile == "huggingface"
                else "local_transformers"
            ),
            inference_configured=(
                bool(service_settings.hf_token)
                if service_settings.deployment_profile == "huggingface"
                else True
            ),
            inference_state=platform.inference_state,
            demo_state=(
                "ephemeral"
                if service_settings.deployment_profile == "huggingface"
                else "durable"
            ),
            build_sha=service_settings.build_sha,
        )

    @application.post(
        "/v1/agent/query",
        tags=["agent"],
        summary="Execute one guarded agent query",
        response_model=AgentQueryResponse,
        responses={503: {"model": ApiErrorResponse}},
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def execute_query(
        body: AgentQueryRequest,
        request: Request,
    ) -> AgentQueryResponse:
        request.state.operation = "agent"
        platform = get_runtime(request)
        started = time.perf_counter()
        with telemetry.span(
            "agent.execute",
            {
                "openweight.operation": "agent",
                "openweight.backend": platform.backend_alias,
            },
            error_type="backend_unavailable",
        ):
            try:
                execution = await platform.execute_query(
                    body.question,
                    request_id=_request_id(request),
                )
            except Exception:
                telemetry.record_agent(
                    backend=platform.backend_alias,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        telemetry.record_agent(
            backend=platform.backend_alias,
            result="success",
            seconds=elapsed_seconds(started),
        )
        routing_decision = safe_json_value(execution.routing_decision)
        if not isinstance(routing_decision, dict):
            raise RuntimeError("Runtime returned an invalid routing decision")
        return AgentQueryResponse(
            request_id=_request_id(request),
            route=execution.route,
            routing_decision=routing_decision,
            result=safe_json_value(execution.result),
        )

    @application.post(
        "/v1/policy/query",
        tags=["policy"],
        summary="Answer one question from internal policy evidence",
        response_model=PolicyQueryResponse,
        responses={503: {"model": ApiErrorResponse}},
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def query_policy(
        body: PolicyQueryRequest,
        request: Request,
    ) -> PolicyQueryResponse:
        request.state.operation = "policy_query"
        platform = get_runtime(request)
        started = time.perf_counter()
        peer_host = request.client.host if request.client is not None else None
        with public_demo_gate.acquire(peer_host):
            with telemetry.span(
                "policy.answer",
                {
                    "openweight.operation": "policy_query",
                    "openweight.backend": platform.backend_alias,
                    "openweight.source_scope": "internal_policy",
                },
                error_type="backend_unavailable",
            ):
                try:
                    result = await platform.query_policy(
                        body.question,
                        request_id=_request_id(request),
                    )
                except Exception:
                    telemetry.record_agent(
                        backend=platform.backend_alias,
                        result="error",
                        seconds=elapsed_seconds(started),
                    )
                    raise
        telemetry.record_agent(
            backend=platform.backend_alias,
            result="success",
            seconds=elapsed_seconds(started),
        )
        return _policy_response(
            result,
            request_id=_request_id(request),
        )

    @application.post(
        "/v1/policy/query/stream",
        tags=["policy"],
        summary="Stream one answer grounded in internal policy evidence",
        response_class=StreamingResponse,
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def stream_policy_query(
        body: PolicyQueryRequest,
        request: Request,
    ) -> StreamingResponse:
        request.state.operation = "policy_query"
        platform = get_runtime(request)
        request_id = _request_id(request)
        peer_host = request.client.host if request.client is not None else None
        lease = public_demo_gate.acquire(peer_host)

        async def events() -> AsyncIterator[str]:
            started = time.perf_counter()
            presentation = PolicyAnswerPresentation()
            with lease:
                with telemetry.span(
                    "policy.answer.stream",
                    {
                        "openweight.operation": "policy_query",
                        "openweight.backend": platform.backend_alias,
                        "openweight.source_scope": "internal_policy",
                    },
                    error_type="backend_unavailable",
                ) as span:
                    try:
                        async for event in platform.stream_policy(
                            body.question,
                            request_id=request_id,
                        ):
                            if event.kind == "stage" and event.value in {
                                "searching",
                                "generating",
                            }:
                                yield _sse_event(
                                    "stage",
                                    {"stage": event.value},
                                )
                            elif event.kind == "delta" and event.value:
                                visible = presentation.feed(event.value)
                                if visible:
                                    yield _sse_event("answer_delta", {"text": visible})
                            elif event.kind == "complete" and event.result is not None:
                                tail = presentation.finish()
                                if tail:
                                    yield _sse_event("answer_delta", {"text": tail})
                                response = _policy_response(
                                    event.result,
                                    request_id=request_id,
                                )
                                telemetry.record_agent(
                                    backend=platform.backend_alias,
                                    result="success",
                                    seconds=elapsed_seconds(started),
                                )
                                yield _sse_event(
                                    "complete",
                                    response.model_dump(mode="json"),
                                )
                    except ServiceError as error:
                        telemetry.mark_span_error(span, _service_error_type(request, error))
                        telemetry.record_agent(
                            backend=platform.backend_alias,
                            result="error",
                            seconds=elapsed_seconds(started),
                        )
                        payload = ApiErrorResponse(
                            request_id=request_id,
                            error=ApiErrorDetail(
                                code=error.code,
                                message=error.public_message,
                            ),
                        )
                        yield _sse_event("error", payload.model_dump(mode="json"))
                    except Exception:
                        telemetry.mark_span_error(span, "internal_error")
                        telemetry.record_agent(
                            backend=platform.backend_alias,
                            result="error",
                            seconds=elapsed_seconds(started),
                        )
                        payload = ApiErrorResponse(
                            request_id=request_id,
                            error=ApiErrorDetail(
                                code="internal_error",
                                message="The service could not complete the request.",
                            ),
                        )
                        yield _sse_event("error", payload.model_dump(mode="json"))

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get(
        "/v1/access-requests",
        tags=["operations"],
        summary="List fictional access requests",
        response_model=AccessRequestListResponse,
        responses={503: {"model": ApiErrorResponse}},
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def list_access_requests(
        request: Request,
        approval_status: Annotated[
            Literal["approved", "pending", "denied"] | None,
            Query(),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> AccessRequestListResponse:
        platform = get_runtime(request)
        records = await platform.list_access_requests(
            approval_status=approval_status,
            limit=limit,
            request_id=_request_id(request),
        )
        return AccessRequestListResponse(
            request_id=_request_id(request),
            access_requests=[
                AccessRequestRecord.model_validate(record, strict=False)
                for record in records
            ],
        )

    @application.get(
        "/v1/access-requests/{access_request_id}",
        tags=["operations"],
        summary="Look up one fictional access request",
        response_model=AccessRequestDetailResponse,
        responses={
            404: {"model": ApiErrorResponse},
            503: {"model": ApiErrorResponse},
        },
        dependencies=[Depends(require_permission("agent.query"))],
    )
    async def get_access_request(
        access_request_id: str,
        request: Request,
    ) -> AccessRequestDetailResponse:
        platform = get_runtime(request)
        record = await platform.lookup_access_request(
            access_request_id,
            request_id=_request_id(request),
        )
        return AccessRequestDetailResponse(
            request_id=_request_id(request),
            access_request=AccessRequestRecord.model_validate(
                record,
                strict=False,
            ),
        )

    @application.post(
        "/v1/actions/access-requests/{access_request_id}/proposals",
        tags=["approval"],
        summary="Propose a validated access-request status change",
        response_model=ApprovalProposalResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses={
            400: {"model": ApiErrorResponse},
            404: {"model": ApiErrorResponse},
            409: {"model": ApiErrorResponse},
            503: {"model": ApiErrorResponse},
        },
        dependencies=[Depends(require_permission("actions.propose"))],
    )
    async def propose_access_request_status(
        access_request_id: str,
        body: AccessRequestStatusProposalRequest,
        request: Request,
    ) -> ApprovalProposalResponse:
        request.state.operation = "approval_proposal"
        platform = get_runtime(request)
        started = time.perf_counter()
        with telemetry.span(
            "approval.propose",
            {"openweight.operation": "approval_proposal"},
            error_type="database_unavailable",
        ):
            try:
                pending = await platform.propose_access_request_status(
                    access_request_id,
                    body.new_status,
                    request_id=_request_id(request),
                )
            except Exception:
                telemetry.record_approval_proposal(
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        telemetry.record_approval_proposal(
            result="success",
            seconds=elapsed_seconds(started),
        )
        access_logger.info(
            "approval_required",
            extra={
                "request_id": _request_id(request),
                "route": _route_template(request),
                "method": request.method,
                "approval_state": "pending",
                "action_type": pending.proposal.action_type,
            },
        )
        return ApprovalProposalResponse(
            request_id=_request_id(request),
            approval_id=pending.approval_id,
            proposal=pending.proposal,
        )

    @application.post(
        "/v1/approvals/{approval_id}/resume",
        tags=["approval"],
        summary="Resume one pending action with an explicit decision",
        response_model=ApprovalResumeResponse,
        responses={
            400: {"model": ApiErrorResponse},
            404: {"model": ApiErrorResponse},
            409: {"model": ApiErrorResponse},
            503: {"model": ApiErrorResponse},
        },
        dependencies=[Depends(require_permission("approvals.resume"))],
    )
    async def resume_approval(
        approval_id: str,
        body: ApprovalResumeRequest,
        request: Request,
    ) -> ApprovalResumeResponse:
        request.state.operation = "approval_resume"
        platform = get_runtime(request)
        started = time.perf_counter()
        with telemetry.span(
            "approval.resume",
            {
                "openweight.operation": "approval_resume",
                "openweight.decision": body.decision,
            },
            error_type="database_unavailable",
        ) as span:
            try:
                outcome = await platform.resume_approval(
                    approval_id,
                    ApprovalDecision(decision=body.decision, comment=body.comment),
                    request_id=_request_id(request),
                )
            except Exception:
                telemetry.record_approval_resume(
                    decision=body.decision,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        telemetry.set_span_attributes(
            span,
            {
                "openweight.approval_state": outcome.status,
                "openweight.result": "success",
            },
        )
        telemetry.record_approval_resume(
            decision=body.decision,
            result=outcome.status,
            seconds=elapsed_seconds(started),
        )
        access_logger.info(
            "approval_completed",
            extra={
                "request_id": _request_id(request),
                "route": _route_template(request),
                "method": request.method,
                "approval_state": outcome.status,
                "action_type": "set_access_request_status",
            },
        )
        return ApprovalResumeResponse(
            request_id=_request_id(request),
            approval_id=outcome.approval_id,
            status=outcome.status,
            action_result=safe_json_value(outcome.action_result),
        )

    if service_settings.frontend_enabled:
        register_frontend_routes(
            application,
            service_settings.frontend_dist_dir,
        )

    if mcp_asgi_app is not None:
        # Keep product routes authoritative while allowing the SDK's origin-level
        # protected-resource metadata and configured MCP endpoint to remain intact.
        application.mount("/", mcp_asgi_app, name="mcp")

    return application


app = create_app()


__all__ = ["REQUEST_ID_HEADER", "REQUEST_ID_PATTERN", "app", "create_app"]
