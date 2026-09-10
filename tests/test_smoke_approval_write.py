from __future__ import annotations

import importlib.util
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from openweight_platform.operations.actions import (
    AccessRequestStatusChange,
    UnsupportedAccessRequestStatusError,
)
from openweight_platform.operations.models import AccessRequest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "smoke_approval_write.py"
)
SCRIPT_SPEC = importlib.util.spec_from_file_location(
    "smoke_approval_write",
    SCRIPT_PATH,
)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
smoke = importlib.util.module_from_spec(SCRIPT_SPEC)
SCRIPT_SPEC.loader.exec_module(smoke)


class FakeDatabase:
    def __init__(self, record: AccessRequest | None) -> None:
        self.record = record
        self.lookup_calls: list[str] = []
        self.write_calls: list[tuple[str, str]] = []


class FakeConfig:
    password = "must-never-appear-in-output"
    calls = 0

    @classmethod
    def from_env(cls):
        cls.calls += 1
        return cls()


def fictional_request(status: str = "pending") -> AccessRequest:
    return AccessRequest(
        request_id="fic-req-002",
        subject_type="employee",
        subject_id="fic-emp-006",
        system_name="Fictional Vendor Portal",
        requested_role="procurement_admin",
        approval_status=status,
        requested_start_date=date(2026, 9, 1),
        requested_end_date=None,
    )


def install_fake_database(
    monkeypatch,
    record: AccessRequest | None = None,
) -> FakeDatabase:
    database = FakeDatabase(record if record is not None else fictional_request())

    class FakeLookupService:
        def __init__(self, config) -> None:
            assert isinstance(config, FakeConfig)

        def lookup_access_request(self, request_id: str):
            database.lookup_calls.append(request_id)
            if database.record is None or database.record.request_id != request_id:
                return None
            return replace(database.record)

    class FakeActionService:
        def __init__(self, config) -> None:
            assert isinstance(config, FakeConfig)

        def set_access_request_status_once(
            self,
            effect_id: str,
            request_id: str,
            new_status: str,
        ) -> AccessRequestStatusChange:
            assert effect_id
            database.write_calls.append((request_id, new_status))
            assert database.record is not None
            previous_status = database.record.approval_status
            database.record = replace(
                database.record,
                approval_status=new_status,
            )
            return AccessRequestStatusChange(
                request_id=request_id,
                previous_status=previous_status,
                new_status=new_status,
                changed=previous_status != new_status,
            )

    monkeypatch.setattr(
        smoke,
        "_load_database_components",
        lambda: (FakeConfig, FakeLookupService, FakeActionService),
    )
    FakeConfig.calls = 0
    return database


def test_default_mode_reaches_interrupt_and_performs_zero_writes(
    monkeypatch,
) -> None:
    database = install_fake_database(monkeypatch)

    result = smoke.run_approval_write_smoke(
        "fic-req-002",
        "approved",
        thread_id="dry-run-thread",
    )

    assert FakeConfig.calls == 1
    assert database.lookup_calls == ["fic-req-002", "fic-req-002"]
    assert database.write_calls == []
    assert database.record is not None
    assert database.record.approval_status == "pending"
    assert result["mode"] == "dry_run"
    assert result["write_performed"] is False
    assert result["pre_approval_database_unchanged"] is True
    assert result["paused_approval_status"] == "pending"
    assert result["interrupt_payload"]["type"] == "approval_required"
    assert "No write was performed" in result["message"]


def test_default_cli_has_no_implicit_execution_gate() -> None:
    args = smoke.parse_args(
        ["--request-id", "fic-req-002", "--new-status", "approved"]
    )

    assert args.execute is False
    assert {
        action.dest
        for action in smoke.build_parser()._actions
    } == {"help", "request_id", "new_status", "execute"}
    with pytest.raises(SystemExit):
        smoke.parse_args(["--execute"])


@pytest.mark.parametrize("request_id", ["", "   ", " req-002", "req-002"])
def test_blank_or_nonfictional_request_id_is_rejected(request_id) -> None:
    with pytest.raises(SystemExit):
        smoke.parse_args(
            ["--request-id", request_id, "--new-status", "approved"]
        )


def test_nonexistent_request_is_rejected_without_write(monkeypatch) -> None:
    database = install_fake_database(monkeypatch)

    with pytest.raises(LookupError, match="was not found"):
        smoke.run_approval_write_smoke("fic-req-999", "denied")

    assert database.lookup_calls == ["fic-req-999"]
    assert database.write_calls == []


@pytest.mark.parametrize("status", ["", "granted", "APPROVED"])
def test_invalid_status_is_rejected_before_loading_database(
    monkeypatch,
    status,
) -> None:
    database = install_fake_database(monkeypatch)

    with pytest.raises(UnsupportedAccessRequestStatusError):
        smoke.run_approval_write_smoke("fic-req-002", status)

    assert FakeConfig.calls == 0
    assert database.lookup_calls == []
    assert database.write_calls == []


def test_record_with_invalid_existing_status_is_rejected(monkeypatch) -> None:
    database = install_fake_database(
        monkeypatch,
        fictional_request(status="reviewing"),
    )

    with pytest.raises(ValueError, match="unsupported current status"):
        smoke.run_approval_write_smoke("fic-req-002", "approved")

    assert database.write_calls == []


def test_valid_proposal_shape_is_displayed_at_interrupt(monkeypatch) -> None:
    install_fake_database(monkeypatch)

    result = smoke.run_approval_write_smoke(
        "fic-req-002",
        "denied",
        thread_id="proposal-thread",
    )

    expected_action = {
        "action_type": "set_access_request_status",
        "summary": (
            "Set fictional access request fic-req-002 from pending to denied."
        ),
        "arguments": {
            "request_id": "fic-req-002",
            "new_status": "denied",
        },
        "consequence": (
            "The approval status of this fictional access request will be "
            "committed to local PostgreSQL if explicitly approved."
        ),
    }
    assert result["action_proposal"] == expected_action
    assert result["interrupt_payload"] == {
        "type": "approval_required",
        "action": expected_action,
    }


def test_explicit_execute_uses_existing_graph_and_executor_once(
    monkeypatch,
) -> None:
    database = install_fake_database(monkeypatch)
    graph_builds = []
    executor_builds = []
    original_graph_factory = smoke.build_human_approval_graph
    original_executor_factory = smoke.build_access_request_action_executor

    def build_graph(*args, **kwargs):
        graph_builds.append((args, kwargs))
        return original_graph_factory(*args, **kwargs)

    def build_executor(service):
        executor_builds.append(service)
        return original_executor_factory(service)

    monkeypatch.setattr(smoke, "build_human_approval_graph", build_graph)
    monkeypatch.setattr(
        smoke,
        "build_access_request_action_executor",
        build_executor,
    )

    result = smoke.run_approval_write_smoke(
        "fic-req-002",
        "approved",
        execute=True,
        thread_id="execute-thread",
    )

    assert len(graph_builds) == 1
    assert len(executor_builds) == 1
    assert database.lookup_calls == [
        "fic-req-002",
        "fic-req-002",
        "fic-req-002",
    ]
    assert database.write_calls == [("fic-req-002", "approved")]
    assert result["pre_approval_database_unchanged"] is True
    assert result["write_performed"] is True
    assert result["graph_completed"] is True
    assert result["action_result"] == {
        "request_id": "fic-req-002",
        "previous_status": "pending",
        "new_status": "approved",
        "changed": True,
    }
    assert result["request_after"]["approval_status"] == "approved"


def test_json_output_contains_no_configuration_secrets(
    monkeypatch,
    capsys,
) -> None:
    database = install_fake_database(monkeypatch)

    assert smoke.main(
        ["--request-id", "fic-req-002", "--new-status", "denied"]
    ) == 0
    output_text = capsys.readouterr().out
    output = json.loads(output_text)

    assert database.write_calls == []
    assert FakeConfig.password not in output_text
    assert "password" not in output_text.lower()
    assert output["mode"] == "dry_run"


def test_runner_contains_no_direct_write_sql_or_model_facing_path() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert ".set_access_request_status(" not in source
    assert "UPDATE operations" not in source
    assert "ModelRouter" not in source
    assert "build_operational_tools" not in source
    assert "Tavily" not in source
    assert "GptOss" not in source
