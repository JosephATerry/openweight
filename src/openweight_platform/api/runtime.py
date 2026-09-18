"""Lazy application runtime adapting existing graph and approval capabilities."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import uuid4

import psycopg
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, JsonValue

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.contracts import DependencyStatus
from openweight_platform.api.errors import (
    DependencyUnavailableError,
    InvalidRequestError,
    ResourceNotFoundError,
    StateConflictError,
    UnsafeResultError,
)
from openweight_platform.api.logging import ACCESS_LOGGER_NAME
from openweight_platform.api.observability import Observability, elapsed_seconds
from openweight_platform.backends.base import GenerationResult, ModelBackend
from openweight_platform.operations.models import AccessRequest
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
    ApprovalDecisionError,
    build_human_approval_graph,
)
from openweight_platform.security.persistence import (
    ApprovalSessionConflictError,
    ApprovalSessionNotFoundError,
    ApprovalSessionRecord,
    ApprovalSessionStore,
    CheckpointHandle,
    MemoryApprovalSessionStore,
    PostgresApprovalSessionStore,
    open_checkpoint_handle,
)

if TYPE_CHECKING:
    from openweight_platform.operations.actions import AccessRequestStatusChange


Route = Literal["policy", "operations", "web"]
InferenceState = Literal[
    "not_initialized",
    "loaded",
    "unconfigured",
    "not_used",
    "requesting",
    "available",
    "unavailable",
]
PolicyWarmupState = Literal["not_started", "initializing", "ready", "failed"]
POLICY_WARMUP_LOGGER = logging.getLogger(ACCESS_LOGGER_NAME)
FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "analysis",
        "authorization",
        "api_key",
        "chain_of_thought",
        "password",
        "raw_prompt",
        "raw_response",
        "reasoning",
        "reasoning_content",
        "scratchpad",
        "secret",
    }
)


@dataclass(frozen=True)
class AgentExecution:
    route: Route
    routing_decision: dict[str, JsonValue]
    result: JsonValue


@dataclass(frozen=True)
class PolicyEvidence:
    citation_id: str
    policy_id: str
    title: str
    domain: str
    chunk_index: int
    content: str


@dataclass(frozen=True)
class GroundedPolicyAnswer:
    status: Literal["answered", "insufficient_evidence"]
    answer: str
    citations: list[str]
    citation_valid: bool
    evidence: list[PolicyEvidence]


@dataclass(frozen=True)
class PolicyStreamEvent:
    kind: Literal["stage", "delta", "complete"]
    value: str | None = None
    result: GroundedPolicyAnswer | None = None


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    proposal: ActionProposal


@dataclass(frozen=True)
class ApprovalOutcome:
    approval_id: str
    status: Literal["approved", "rejected"]
    action_result: JsonValue | None


class PlatformRuntime(Protocol):
    @property
    def backend_alias(self) -> str: ...

    @property
    def inference_state(self) -> InferenceState: ...

    async def readiness(self) -> list[DependencyStatus]: ...

    async def execute_query(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AgentExecution: ...

    async def query_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> GroundedPolicyAnswer: ...

    def stream_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AsyncIterator[PolicyStreamEvent]: ...

    async def search_policy(
        self,
        query: str,
        *,
        limit: int,
        request_id: str,
    ) -> list[JsonValue]: ...

    async def lookup_employee(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> JsonValue: ...

    async def lookup_contractor(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> JsonValue: ...

    async def lookup_access_request(
        self,
        access_request_id: str,
        *,
        request_id: str,
    ) -> JsonValue: ...

    async def list_access_requests(
        self,
        *,
        approval_status: str | None,
        limit: int,
        request_id: str,
    ) -> list[JsonValue]: ...

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval: ...

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome: ...

    async def close(self) -> None: ...


class AccessRequestReader(Protocol):
    def lookup_access_request(self, request_id: str) -> AccessRequest | None: ...


class AccessRequestWriter(Protocol):
    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> "AccessRequestStatusChange": ...


class _ObservedBackend(ModelBackend):
    """Instrument a backend without observing prompts or generated text."""

    def __init__(
        self,
        backend: ModelBackend,
        alias: str,
        observability: Observability,
    ) -> None:
        self._backend = backend
        self._alias = alias
        self._observability = observability

    @property
    def model_name(self) -> str:
        return self._backend.model_name

    def load(self) -> None:
        with self._observability.span(
            "backend.load",
            {
                "openweight.operation": "backend",
                "openweight.backend": self._alias,
            },
            error_type="backend_unavailable",
        ):
            self._backend.load()

    def generate(self, prompt: str) -> GenerationResult:
        started = time.perf_counter()
        with self._observability.span(
            "backend.generate",
            {
                "openweight.operation": "backend",
                "openweight.backend": self._alias,
            },
            error_type="backend_unavailable",
        ):
            try:
                result = self._backend.generate(prompt)
            except Exception:
                self._observability.record_backend(
                    backend=self._alias,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        self._observability.record_backend(
            backend=self._alias,
            result="success",
            seconds=elapsed_seconds(started),
        )
        return result

    def generate_stream(
        self,
        prompt: str,
        on_text: Callable[[str], None],
    ) -> GenerationResult:
        started = time.perf_counter()
        with self._observability.span(
            "backend.generate",
            {
                "openweight.operation": "backend",
                "openweight.backend": self._alias,
            },
            error_type="backend_unavailable",
        ):
            try:
                result = self._backend.generate_stream(prompt, on_text)
            except Exception:
                self._observability.record_backend(
                    backend=self._alias,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        self._observability.record_backend(
            backend=self._alias,
            result="success",
            seconds=elapsed_seconds(started),
        )
        return result

    def unload(self) -> None:
        with self._observability.span(
            "backend.unload",
            {
                "openweight.operation": "backend",
                "openweight.backend": self._alias,
            },
            error_type="backend_unavailable",
        ):
            self._backend.unload()


class _ObservedAccessRequestWriter:
    """Instrument only the fixed controlled write, never its arguments."""

    def __init__(
        self,
        writer: AccessRequestWriter,
        observability: Observability,
    ) -> None:
        self._writer = writer
        self._observability = observability

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> "AccessRequestStatusChange":
        started = time.perf_counter()
        with self._observability.span(
            "action.execute",
            {"openweight.operation": "controlled_write"},
            error_type="database_unavailable",
        ):
            try:
                result = self._writer.set_access_request_status_once(
                    effect_id,
                    request_id,
                    new_status,
                )
            except Exception:
                self._observability.record_controlled_write(
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        self._observability.record_controlled_write(
            result="success",
            seconds=elapsed_seconds(started),
        )
        return result


class ApprovalCoordinator:
    """Coordinate checkpointed approvals with a pluggable durable session store."""

    def __init__(
        self,
        lookup_service: AccessRequestReader,
        action_service: AccessRequestWriter,
        *,
        checkpointer: BaseCheckpointSaver | None = None,
        session_store: ApprovalSessionStore | None = None,
    ) -> None:
        from openweight_platform.orchestration.operations_approval import (
            build_access_request_action_executor,
        )

        self._lookup_service = lookup_service
        self._graph = build_human_approval_graph(
            build_access_request_action_executor(action_service),
            checkpointer=checkpointer or InMemorySaver(),
        )
        self._session_store = session_store or MemoryApprovalSessionStore()
        self._lock = threading.RLock()

    def propose(self, access_request_id: str, new_status: str) -> PendingApproval:
        from openweight_platform.operations.actions import (
            validate_access_request_status,
        )
        from openweight_platform.orchestration.operations_approval import (
            SET_ACCESS_REQUEST_STATUS_ACTION,
        )

        if not isinstance(access_request_id, str) or not access_request_id.strip():
            raise InvalidRequestError
        try:
            validated_status = validate_access_request_status(new_status)
        except ValueError as error:
            raise InvalidRequestError from error

        with self._lock:
            record = self._lookup_service.lookup_access_request(access_request_id)
            if record is None:
                raise ResourceNotFoundError
            try:
                validate_access_request_status(record.approval_status)
            except ValueError as error:
                raise StateConflictError from error
            if record.approval_status == validated_status:
                raise StateConflictError

            proposal = ActionProposal(
                action_type=SET_ACCESS_REQUEST_STATUS_ACTION,
                summary=(
                    f"Set access request {record.request_id} from "
                    f"{record.approval_status} to {validated_status}."
                ),
                arguments={
                    "request_id": record.request_id,
                    "new_status": validated_status,
                },
                consequence=(
                    "The access-request approval status will be changed only "
                    "after explicit approval."
                ),
            )
            approval_id = f"approval-{uuid4().hex}"
            graph_config = {"configurable": {"thread_id": approval_id}}
            session = ApprovalSessionRecord(
                approval_id=approval_id,
                access_request_id=record.request_id,
                proposed_status=validated_status,
                proposal=proposal,
            )
            try:
                self._session_store.create(session)
            except ApprovalSessionConflictError as error:
                raise StateConflictError from error
            try:
                paused = self._graph.invoke(
                    {"action_proposal": proposal, "effect_id": approval_id},
                    config=graph_config,
                )
                snapshot = self._graph.get_state(graph_config)
                if (
                    paused.get("approval_status") != "pending"
                    or snapshot.next != ("request_approval",)
                ):
                    raise RuntimeError("Approval graph did not pause safely")
            except Exception:
                self._session_store.delete_pending(approval_id)
                raise
            return PendingApproval(approval_id=approval_id, proposal=proposal)

    def resume(
        self,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> ApprovalOutcome:
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise InvalidRequestError
        try:
            locked_context = self._session_store.locked(approval_id)
            with locked_context as session:
                graph_config = {"configurable": {"thread_id": approval_id}}
                snapshot = self._graph.get_state(graph_config)
                if snapshot.values.get("approval_status") in (
                    "approved",
                    "rejected",
                ) and not snapshot.next:
                    recovered_status = snapshot.values["approval_status"]
                    recovered_result = (
                        safe_json_value(snapshot.values.get("action_result"))
                        if recovered_status == "approved"
                        else None
                    )
                    session.complete(recovered_status, recovered_result)
                    return ApprovalOutcome(
                        approval_id=approval_id,
                        status=recovered_status,
                        action_result=recovered_result,
                    )
                if (
                    snapshot.values.get("approval_status") != "pending"
                    or snapshot.next != ("request_approval",)
                ):
                    raise StateConflictError

                try:
                    from openweight_platform.operations.actions import (
                        AccessRequestNotFoundError,
                        IdempotencyConflictError,
                    )
                    from openweight_platform.orchestration.operations_approval import (
                        OperationalActionValidationError,
                    )

                    completed = self._graph.invoke(
                        Command(resume=decision.model_dump(mode="json")),
                        config=graph_config,
                    )
                except ApprovalDecisionError as error:
                    raise InvalidRequestError from error
                except OperationalActionValidationError as error:
                    raise InvalidRequestError from error
                except AccessRequestNotFoundError as error:
                    raise ResourceNotFoundError from error
                except IdempotencyConflictError as error:
                    raise StateConflictError from error

                status = completed.get("approval_status")
                if status not in ("approved", "rejected"):
                    raise RuntimeError("Approval graph did not complete")
                action_result = (
                    safe_json_value(completed.get("action_result"))
                    if status == "approved"
                    else None
                )
                session.complete(status, action_result)
                return ApprovalOutcome(
                    approval_id=approval_id,
                    status=status,
                    action_result=action_result,
                )
        except ApprovalSessionNotFoundError as error:
            raise ResourceNotFoundError from error
        except ApprovalSessionConflictError as error:
            raise StateConflictError from error


class _LazyPolicyRetriever:
    def __init__(
        self,
        settings: ServiceSettings,
        observability: Observability,
    ) -> None:
        self._settings = settings
        self._observability = observability
        self._engine: Any | None = None
        self._store: Any | None = None
        self._embeddings: Any | None = None

    def _ensure_embeddings(self) -> Any:
        if self._embeddings is None:
            from openweight_platform.rag.embeddings import QwenEmbeddings

            self._embeddings = QwenEmbeddings(
                model_id=self._settings.demo_embedding_model,
                device="cpu",
            )
        return self._embeddings

    def _ensure_store(self) -> Any:
        if self._settings.deployment_profile == "huggingface":
            if self._store is None:
                from openweight_platform.rag.portable import PortablePolicyStore

                self._store = PortablePolicyStore(
                    self._settings.demo_policy_index,
                    embeddings=self._ensure_embeddings(),
                )
            return self._store
        if self._settings.database_config is None:
            raise DependencyUnavailableError
        if self._store is None:
            from openweight_platform.rag.vectorstore import (
                create_policy_vector_store,
            )

            self._engine, self._store = create_policy_vector_store(
                self._settings.database_config,
                self._ensure_embeddings(),
                initialize=False,
            )
        return self._store

    def warmup(self) -> None:
        """Load and validate the local query encoder without remote inference."""

        self._ensure_embeddings().embed_query("OpenWeight retrieval readiness")

    def validate_portable(self) -> None:
        if self._settings.deployment_profile == "huggingface":
            self._ensure_store().validate()

    def similarity_search(self, query: str, k: int) -> list[Any]:
        started = time.perf_counter()
        with self._observability.span(
            "retrieval.policy",
            {"openweight.operation": "retrieval"},
            error_type="database_unavailable",
        ):
            try:
                result = self._ensure_store().similarity_search(query, k=k)
            except Exception:
                self._observability.record_retrieval(
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        self._observability.record_retrieval(
            result="success",
            seconds=elapsed_seconds(started),
        )
        return result

    def __call__(self, question: str, *, limit: int = 4) -> list[object]:
        from openweight_platform.rag.retrieval import search_policy_corpus

        return search_policy_corpus(self, question, k=limit)

    async def close(self) -> None:
        if self._engine is not None:
            engine = self._engine
            self._engine = None
            self._store = None
            await engine.close()
        self._embeddings = None


class _LazyOperationsTools:
    def __init__(
        self,
        settings: ServiceSettings,
        observability: Observability,
        demo_service: Any | None = None,
    ) -> None:
        self._settings = settings
        self._observability = observability
        self._tools: dict[str, BaseTool] | None = None
        self._demo_service = demo_service

    def build(self) -> list[BaseTool]:
        def get_employee_record(identifier: str) -> object:
            return self._invoke("get_employee_record", {"identifier": identifier})

        def get_contractor_record(identifier: str) -> object:
            return self._invoke("get_contractor_record", {"identifier": identifier})

        def get_access_request(request_id: str) -> object:
            return self._invoke("get_access_request", {"request_id": request_id})

        return [
            StructuredTool.from_function(
                func=function,
                name=function.__name__,
                description=f"Invoke the existing read-only {function.__name__} tool.",
            )
            for function in (
                get_employee_record,
                get_contractor_record,
                get_access_request,
            )
        ]

    def _invoke(self, name: str, arguments: dict[str, object]) -> object:
        started = time.perf_counter()
        with self._observability.span(
            "tool.execute",
            {
                "openweight.operation": "tool",
                "openweight.tool_name": name,
            },
            error_type="database_unavailable",
        ):
            try:
                if self._tools is None:
                    from openweight_platform.operations.tools import (
                        build_operational_tools,
                    )
                    if self._demo_service is not None:
                        service = self._demo_service
                    else:
                        if self._settings.database_config is None:
                            raise DependencyUnavailableError
                        from openweight_platform.operations.service import (
                            OperationsLookupService,
                        )

                        service = OperationsLookupService(
                            self._settings.database_config
                        )
                    self._tools = {
                        tool.name: tool for tool in build_operational_tools(service)
                    }
                result = self._tools[name].invoke(arguments)
            except Exception:
                self._observability.record_tool(
                    tool_name=name,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                raise
        self._observability.record_tool(
            tool_name=name,
            result="success",
            seconds=elapsed_seconds(started),
        )
        return result


class _LazyWebTool:
    def __init__(
        self,
        settings: ServiceSettings,
        observability: Observability,
    ) -> None:
        self._settings = settings
        self._observability = observability
        self._tool: BaseTool | None = None

    def build(self) -> BaseTool:
        def search_web(query: str, max_results: int = 5) -> object:
            started = time.perf_counter()
            with self._observability.span(
                "tool.execute",
                {
                    "openweight.operation": "tool",
                    "openweight.tool_name": "search_web",
                },
                error_type="dependency_unavailable",
            ):
                try:
                    result = self._real_tool().invoke(
                        {"query": query, "max_results": max_results}
                    )
                except Exception:
                    self._observability.record_tool(
                        tool_name="search_web",
                        result="error",
                        seconds=elapsed_seconds(started),
                    )
                    raise
            self._observability.record_tool(
                tool_name="search_web",
                result="success",
                seconds=elapsed_seconds(started),
            )
            return result

        return StructuredTool.from_function(
            func=search_web,
            name="search_web",
            description="Invoke the optional existing web-search tool.",
        )

    def _real_tool(self) -> BaseTool:
        if not self._settings.web_enabled:
            raise DependencyUnavailableError
        if self._tool is None:
            from openweight_platform.web.search import TavilySearchService
            from openweight_platform.web.tools import build_web_search_tool

            self._tool = build_web_search_tool(TavilySearchService())
        return self._tool


class DefaultPlatformRuntime:
    """Lazy runtime; importing or starting the API never loads a model."""

    def __init__(
        self,
        settings: ServiceSettings,
        *,
        observability: Observability,
    ) -> None:
        self._settings = settings
        self._observability = observability
        self._backend: ModelBackend | None = None
        self._backend_loaded = False
        self._graph: Any | None = None
        self._policy = _LazyPolicyRetriever(settings, observability)
        self._policy_warmup_state: PolicyWarmupState = "not_started"
        self._policy_warmup_lock = threading.Lock()
        self._demo_operations: Any | None = None
        if settings.deployment_profile == "huggingface":
            from openweight_platform.operations.demo import DemoOperationsService

            self._demo_operations = DemoOperationsService()
        self._operations = _LazyOperationsTools(
            settings,
            observability,
            demo_service=self._demo_operations,
        )
        self._web = _LazyWebTool(settings, observability)
        self._agent_lock = threading.RLock()
        self._approval: ApprovalCoordinator | None = None
        self._checkpoint_handle: CheckpointHandle | None = None
        self._approval_lock = threading.RLock()
        self._operations_lookup: Any | None = None
        self._operations_lookup_lock = threading.RLock()
        self._remote_inference_state: InferenceState = (
            "unconfigured"
            if settings.uses_huggingface_inference and not settings.hf_token
            else "not_used"
            if settings.uses_huggingface_inference
            else "not_initialized"
        )

    @property
    def backend_alias(self) -> str:
        return self._settings.backend_name

    @property
    def inference_state(self) -> InferenceState:
        if self._settings.uses_huggingface_inference:
            return self._remote_inference_state
        return "loaded" if self._backend_loaded else "not_initialized"

    async def readiness(self) -> list[DependencyStatus]:
        return await asyncio.to_thread(self._readiness)

    async def warm_policy_retrieval(self) -> bool:
        """Warm the Azure query encoder once without invoking GPT-OSS."""

        return await asyncio.to_thread(self._warm_policy_retrieval)

    def _warm_policy_retrieval(self) -> bool:
        with self._policy_warmup_lock:
            if self._policy_warmup_state == "ready":
                return True
            if self._policy_warmup_state == "failed":
                return False
            self._policy_warmup_state = "initializing"
            started = time.perf_counter()
            POLICY_WARMUP_LOGGER.info(
                "policy_encoder_warmup_started",
                extra={"dependency": "retrieval_encoder", "result": "started"},
            )
            try:
                self._policy.warmup()
            except Exception:
                self._policy_warmup_state = "failed"
                POLICY_WARMUP_LOGGER.info(
                    "policy_encoder_warmup_failed",
                    extra={
                        "dependency": "retrieval_encoder",
                        "result": "failed",
                        "latency_ms": round(elapsed_seconds(started) * 1_000, 3),
                    },
                )
                return False
            self._policy_warmup_state = "ready"
            POLICY_WARMUP_LOGGER.info(
                "policy_encoder_warmup_completed",
                extra={
                    "dependency": "retrieval_encoder",
                    "result": "success",
                    "latency_ms": round(elapsed_seconds(started) * 1_000, 3),
                },
            )
            return True

    def _readiness(self) -> list[DependencyStatus]:
        configuration_ready = not self._settings.configuration_errors
        states = [
            DependencyStatus(
                name="configuration",
                status="ready" if configuration_ready else "unavailable",
                required=True,
                detail=(
                    "validated"
                    if configuration_ready
                    else "; ".join(self._settings.configuration_errors)
                ),
            ),
            self._model_backend_readiness(),
        ]
        if self._settings.deployment_profile == "huggingface":
            states.extend(self._demo_readiness())
        else:
            states.append(self._database_readiness())
            if self._settings.checkpoint_backend == "postgres":
                states.append(self._checkpoint_readiness())
            if self._settings.deployment_profile == "azure":
                states.append(self._policy_encoder_readiness())
                states.append(self._policy_index_readiness())
        web_ready = self._settings.web_enabled and self._settings.tavily_configured
        states.append(
            DependencyStatus(
                name="web_search",
                status=(
                    "ready"
                    if web_ready
                    else "unavailable"
                    if self._settings.web_enabled
                    else "disabled"
                ),
                required=False,
                detail=(
                    "configured"
                    if web_ready
                    else "enabled but configuration is unavailable"
                    if self._settings.web_enabled
                    else "optional integration disabled"
                ),
            )
        )
        states.append(
            DependencyStatus(
                name="mlflow",
                status=("ready" if self._settings.mlflow_configured else "disabled"),
                required=False,
                detail=(
                    "configured"
                    if self._settings.mlflow_configured
                    else "runtime tracking disabled"
                ),
            )
        )
        return states

    def _policy_encoder_readiness(self) -> DependencyStatus:
        state = self._policy_warmup_state
        return DependencyStatus(
            name="retrieval_encoder",
            status="ready" if state == "ready" else "unavailable",
            required=True,
            detail={
                "not_started": "local retrieval encoder not started",
                "initializing": "local retrieval encoder initializing",
                "ready": "local retrieval encoder ready",
                "failed": "local retrieval encoder unavailable",
            }[state],
        )

    def _model_backend_readiness(self) -> DependencyStatus:
        if not self._settings.uses_huggingface_inference:
            return DependencyStatus(
                name="model_backend",
                status="ready",
                required=True,
                detail="configured for lazy initialization",
            )
        configured = bool(self._settings.hf_token)
        required = self._settings.deployment_profile == "azure"
        return DependencyStatus(
            name="model_backend",
            status="ready" if configured else "unavailable",
            required=required,
            detail=(
                "remote provider configured; no inference probe performed"
                if configured
                else "HF_TOKEN is not configured; no inference probe performed"
            ),
        )

    def _policy_index_readiness(self) -> DependencyStatus:
        """Check Azure policy structures without loading an embedding or LLM."""

        config = self._settings.database_config
        ready = False
        if config is not None:
            try:
                with psycopg.connect(
                    **config.connect_kwargs,
                    connect_timeout=self._settings.database_probe_timeout_seconds,
                ) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT
                                EXISTS (
                                    SELECT 1 FROM pg_extension WHERE extname = 'vector'
                                ),
                                to_regclass('public.policy_chunks') IS NOT NULL,
                                CASE
                                    WHEN to_regclass('public.policy_chunks') IS NULL
                                    THEN false
                                    ELSE EXISTS (SELECT 1 FROM public.policy_chunks)
                                END
                            """
                        )
                        ready = cursor.fetchone() == (True, True, True)
            except Exception:
                ready = False
        return DependencyStatus(
            name="policy_retrieval",
            status="ready" if ready else "unavailable",
            required=True,
            detail=(
                "pgvector policy index available"
                if ready
                else "pgvector policy index probe failed"
            ),
        )

    def _demo_readiness(self) -> list[DependencyStatus]:
        try:
            self._policy.validate_portable()
            retrieval_ready = True
        except Exception:
            retrieval_ready = False
        return [
            DependencyStatus(
                name="demo_retrieval",
                status="ready" if retrieval_ready else "unavailable",
                required=True,
                detail=(
                    "portable synthetic policy index validated"
                    if retrieval_ready
                    else "portable synthetic policy index validation failed"
                ),
            ),
            DependencyStatus(
                name="demo_state",
                status="ready",
                required=True,
                detail="synthetic process-local sandbox; resets on restart",
            ),
        ]

    def _checkpoint_readiness(self) -> DependencyStatus:
        config = self._settings.database_config
        if config is None:
            return DependencyStatus(
                name="approval_persistence",
                status="unavailable",
                required=True,
                detail="database configuration unavailable",
            )
        try:
            with psycopg.connect(
                **config.connect_kwargs,
                connect_timeout=self._settings.database_probe_timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                            to_regclass('public.checkpoints') IS NOT NULL,
                            to_regclass('security.approval_sessions') IS NOT NULL,
                            to_regclass('security.execution_ledger') IS NOT NULL
                        """
                    )
                    row = cursor.fetchone()
            ready = row == (True, True, True)
        except Exception:
            ready = False
        return DependencyStatus(
            name="approval_persistence",
            status="ready" if ready else "unavailable",
            required=True,
            detail=(
                "durable checkpoint and idempotency schemas available"
                if ready
                else "durable approval schema probe failed"
            ),
        )

    def _database_readiness(self) -> DependencyStatus:
        required = self._settings.database_required
        config = self._settings.database_config
        if config is None:
            return DependencyStatus(
                name="postgresql",
                status="unavailable" if required else "disabled",
                required=required,
                detail=(
                    "configuration unavailable"
                    if required
                    else "optional dependency disabled"
                ),
            )
        try:
            with psycopg.connect(
                **config.connect_kwargs,
                connect_timeout=self._settings.database_probe_timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    row = cursor.fetchone()
            ready = row == (1,)
        except Exception:
            ready = False
        return DependencyStatus(
            name="postgresql",
            status="ready" if ready else "unavailable",
            required=required,
            detail="reachable" if ready else "connection probe failed",
        )

    async def execute_query(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AgentExecution:
        return await asyncio.to_thread(
            self._execute_query,
            question,
            request_id=request_id,
        )

    def _execute_query(self, question: str, *, request_id: str) -> AgentExecution:
        if self._settings.uses_huggingface_inference:
            raise InvalidRequestError
        with self._agent_lock:
            try:
                self._ensure_agent()
                state = self._graph.invoke(
                    {"question": question},
                    config={"metadata": {"request_id": request_id}},
                )
            except DependencyUnavailableError:
                raise
            except Exception as error:
                raise DependencyUnavailableError from error
        route = state.get("route")
        if route not in ("policy", "operations", "web"):
            raise UnsafeResultError
        decision = safe_json_value(state.get("routing_decision"))
        if not isinstance(decision, dict):
            raise UnsafeResultError
        return AgentExecution(
            route=cast(Route, route),
            routing_decision=decision,
            result=safe_json_value(state.get("result")),
        )

    def _ensure_agent(self) -> None:
        if self._graph is None:
            from openweight_platform.orchestration.model_graph import (
                build_model_routed_capability_graph,
            )
            from openweight_platform.orchestration.model_routing import ModelRouter

            self._ensure_backend()
            self._graph = build_model_routed_capability_graph(
                model_router=ModelRouter(cast(ModelBackend, self._backend)),
                policy_retriever=self._policy,
                operations_tools=self._operations.build(),
                web_tool=self._web.build(),
            )

    def _ensure_backend(self) -> ModelBackend:
        if self._backend is None:
            from openweight_platform.backends.factory import build_model_backend

            self._backend = _ObservedBackend(
                build_model_backend(self._settings.backend_config),
                self._settings.backend_name,
                self._observability,
            )
        if not self._backend_loaded:
            self._backend.load()
            self._backend_loaded = True
        return self._backend

    async def query_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> GroundedPolicyAnswer:
        return await asyncio.to_thread(
            self._query_policy,
            question,
            request_id=request_id,
        )

    async def stream_policy(
        self,
        question: str,
        *,
        request_id: str,
    ) -> AsyncIterator[PolicyStreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[PolicyStreamEvent | None] = asyncio.Queue()
        outcome: dict[str, object] = {}

        def publish(kind: Literal["stage", "delta"], value: str) -> None:
            try:
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    PolicyStreamEvent(kind=kind, value=value),
                )
            except RuntimeError:
                # The client may disconnect while blocking inference finishes.
                return

        def execute() -> None:
            try:
                outcome["result"] = self._query_policy(
                    question,
                    request_id=request_id,
                    on_stage=lambda value: publish("stage", value),
                    on_text=lambda value: publish("delta", value),
                )
            except BaseException as error:
                outcome["error"] = error
            finally:
                try:
                    loop.call_soon_threadsafe(queue.put_nowait, None)
                except RuntimeError:
                    return

        task = asyncio.create_task(asyncio.to_thread(execute))
        while (event := await queue.get()) is not None:
            yield event
        await task
        error = outcome.get("error")
        if isinstance(error, BaseException):
            raise error
        result = outcome.get("result")
        if not isinstance(result, GroundedPolicyAnswer):
            raise RuntimeError("Policy streaming did not produce a result")
        yield PolicyStreamEvent(kind="complete", result=result)

    def _query_policy(
        self,
        question: str,
        *,
        request_id: str,
        on_stage: Callable[[str], None] | None = None,
        on_text: Callable[[str], None] | None = None,
    ) -> GroundedPolicyAnswer:
        del request_id
        from openweight_platform.rag.generation import generate_grounded_answer

        with self._agent_lock:
            try:
                if self._settings.uses_huggingface_inference:
                    self._remote_inference_state = (
                        "requesting" if self._settings.hf_token else "unconfigured"
                    )
                result = generate_grounded_answer(
                    question,
                    self._policy,
                    self._ensure_backend(),
                    top_k=3,
                    on_stage=on_stage,
                    on_text=on_text,
                )
            except (DependencyUnavailableError, UnsafeResultError):
                if self._settings.uses_huggingface_inference:
                    self._remote_inference_state = (
                        "unavailable" if self._settings.hf_token else "unconfigured"
                    )
                raise
            except ValueError as error:
                if self._settings.uses_huggingface_inference:
                    self._remote_inference_state = (
                        "unavailable" if self._settings.hf_token else "unconfigured"
                    )
                raise InvalidRequestError from error
            except Exception as error:
                if self._settings.uses_huggingface_inference:
                    self._remote_inference_state = (
                        "unavailable" if self._settings.hf_token else "unconfigured"
                    )
                raise DependencyUnavailableError from error

        if self._settings.uses_huggingface_inference:
            self._remote_inference_state = "available"

        if result.evidence_sufficient and not result.citation_valid:
            raise UnsafeResultError

        return GroundedPolicyAnswer(
            status=(
                "answered"
                if result.evidence_sufficient
                else "insufficient_evidence"
            ),
            answer=result.answer,
            citations=result.cited_source_ids,
            citation_valid=result.citation_valid,
            evidence=[
                PolicyEvidence(
                    citation_id=source.citation_id,
                    policy_id=source.policy_id,
                    title=source.title,
                    domain=source.domain,
                    chunk_index=source.chunk_index,
                    content=source.content,
                )
                for source in (
                    result.retrieved_sources
                    if result.evidence_sufficient
                    else []
                )
            ],
        )

    async def search_policy(
        self,
        query: str,
        *,
        limit: int,
        request_id: str,
    ) -> list[JsonValue]:
        del request_id
        try:
            documents = await asyncio.to_thread(self._policy, query, limit=limit)
        except ValueError as error:
            raise InvalidRequestError from error
        except DependencyUnavailableError:
            raise
        except Exception as error:
            raise DependencyUnavailableError from error
        return [
            safe_json_value(
                {
                    "content": getattr(document, "page_content", ""),
                    "metadata": getattr(document, "metadata", {}),
                }
            )
            for document in documents
        ]

    def _operations_lookup_service(self) -> Any:
        with self._operations_lookup_lock:
            if self._demo_operations is not None:
                return self._demo_operations
            if self._settings.database_config is None:
                raise DependencyUnavailableError
            if self._operations_lookup is None:
                from openweight_platform.operations.service import (
                    OperationsLookupService,
                )

                self._operations_lookup = OperationsLookupService(
                    self._settings.database_config
                )
            return self._operations_lookup

    async def lookup_employee(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> JsonValue:
        del request_id
        return await asyncio.to_thread(
            self._lookup_record,
            "lookup_employee",
            identifier,
        )

    async def lookup_contractor(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> JsonValue:
        del request_id
        return await asyncio.to_thread(
            self._lookup_record,
            "lookup_contractor",
            identifier,
        )

    async def lookup_access_request(
        self,
        access_request_id: str,
        *,
        request_id: str,
    ) -> JsonValue:
        del request_id
        return await asyncio.to_thread(
            self._lookup_record,
            "lookup_access_request",
            access_request_id,
        )

    def _lookup_record(self, method_name: str, identifier: str) -> JsonValue:
        try:
            method = getattr(self._operations_lookup_service(), method_name)
            record = method(identifier)
        except (LookupError, ValueError) as error:
            raise InvalidRequestError from error
        except psycopg.Error as error:
            raise DependencyUnavailableError from error
        if record is None:
            raise ResourceNotFoundError
        return safe_json_value(record)

    async def list_access_requests(
        self,
        *,
        approval_status: str | None,
        limit: int,
        request_id: str,
    ) -> list[JsonValue]:
        del request_id
        try:
            records = await asyncio.to_thread(
                self._operations_lookup_service().list_access_requests,
                approval_status=approval_status,
                limit=limit,
            )
        except ValueError as error:
            raise InvalidRequestError from error
        except psycopg.Error as error:
            raise DependencyUnavailableError from error
        return [safe_json_value(record) for record in records]

    def _approval_coordinator(self) -> ApprovalCoordinator:
        with self._approval_lock:
            if self._approval is None:
                if self._demo_operations is not None:
                    handle = open_checkpoint_handle("memory", None)
                    store: ApprovalSessionStore = MemoryApprovalSessionStore()
                    reader = self._demo_operations
                    writer: AccessRequestWriter = self._demo_operations
                else:
                    if self._settings.database_config is None:
                        raise DependencyUnavailableError
                    from openweight_platform.operations.actions import (
                        AccessRequestActionService,
                    )
                    from openweight_platform.operations.service import (
                        OperationsLookupService,
                    )

                    handle = open_checkpoint_handle(
                        self._settings.checkpoint_backend,
                        self._settings.database_config,
                    )
                    store = (
                        PostgresApprovalSessionStore(self._settings.database_config)
                        if self._settings.checkpoint_backend == "postgres"
                        else MemoryApprovalSessionStore()
                    )
                    reader = OperationsLookupService(self._settings.database_config)
                    writer = _ObservedAccessRequestWriter(
                        AccessRequestActionService(self._settings.database_config),
                        self._observability,
                    )
                try:
                    self._approval = ApprovalCoordinator(
                        reader,
                        writer,
                        checkpointer=handle.saver,
                        session_store=store,
                    )
                except Exception:
                    handle.close()
                    raise
                self._checkpoint_handle = handle
            return self._approval

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        return await asyncio.to_thread(
            self._propose_access_request_status,
            access_request_id,
            new_status,
            request_id=request_id,
        )

    def _propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        del request_id
        try:
            return self._approval_coordinator().propose(
                access_request_id,
                new_status,
            )
        except (InvalidRequestError, ResourceNotFoundError, StateConflictError):
            raise
        except psycopg.Error as error:
            raise DependencyUnavailableError from error

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        return await asyncio.to_thread(
            self._resume_approval,
            approval_id,
            decision,
            request_id=request_id,
        )

    def _resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        del request_id
        try:
            return self._approval_coordinator().resume(approval_id, decision)
        except (
            InvalidRequestError,
            ResourceNotFoundError,
            StateConflictError,
        ):
            raise
        except psycopg.Error as error:
            raise DependencyUnavailableError from error

    async def close(self) -> None:
        await self._policy.close()
        if self._backend is not None and self._backend_loaded:
            backend = self._backend
            self._backend_loaded = False
            await asyncio.to_thread(backend.unload)
        if self._checkpoint_handle is not None:
            handle = self._checkpoint_handle
            self._checkpoint_handle = None
            self._approval = None
            await asyncio.to_thread(handle.close)


def safe_json_value(value: object) -> JsonValue:
    """Convert expected results to JSON while rejecting sensitive key classes."""

    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise UnsafeResultError
        return value
    if isinstance(value, BaseModel):
        return safe_json_value(value.model_dump(mode="json"))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return safe_json_value(asdict(value))
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for key, item in value.items():
            text_key = str(key)
            normalized = text_key.lower().replace("-", "_")
            key_parts = frozenset(normalized.split("_"))
            if normalized in FORBIDDEN_PUBLIC_KEYS or key_parts.intersection(
                {"authorization", "password", "secret"}
            ):
                raise UnsafeResultError
            converted[text_key] = safe_json_value(item)
        return converted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [safe_json_value(item) for item in value]
    raise UnsafeResultError


__all__ = [
    "AccessRequestReader",
    "AccessRequestWriter",
    "AgentExecution",
    "ApprovalCoordinator",
    "ApprovalOutcome",
    "DefaultPlatformRuntime",
    "FORBIDDEN_PUBLIC_KEYS",
    "GroundedPolicyAnswer",
    "InferenceState",
    "PendingApproval",
    "PolicyEvidence",
    "PolicyStreamEvent",
    "PlatformRuntime",
    "safe_json_value",
]
