from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


exporter = load_script("export_huggingface_space")
deployer = load_script("deploy_huggingface_space")


def test_export_is_an_explicit_public_demo_allowlist(tmp_path: Path) -> None:
    output = tmp_path / "space"
    entries = exporter.export_space(output)
    paths = {entry.destination for entry in entries}

    assert "README.md" in paths
    assert "Dockerfile" in paths
    assert "frontend/package.json" in paths
    assert "src/openweight_platform/api/app.py" in paths
    assert "data/policies/01_privileged_infrastructure_access.md" in paths
    assert "deploy/huggingface/policy_index.npz" in paths
    assert "scripts/setup_operations.py" in paths
    assert not any(path.startswith("data/evals/") for path in paths)
    assert not any(path.startswith("data/training/") for path in paths)
    assert not any(path.startswith("mlruns/") for path in paths)
    assert not any(path.startswith(".git/") for path in paths)
    assert not any("node_modules" in Path(path).parts for path in paths)
    assert not any(Path(path).name == ".env" for path in paths)


def test_export_rejects_an_unexpected_file(tmp_path: Path) -> None:
    output = tmp_path / "space"
    entries = exporter.export_space(output)
    (output / "unexpected.log").write_text("not deployable", encoding="utf-8")

    with pytest.raises(ValueError, match="does not match allowlist"):
        exporter.validate_export(output, entries)


def test_export_refuses_a_nonempty_destination(tmp_path: Path) -> None:
    output = tmp_path / "space"
    output.mkdir()
    (output / "existing").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must not exist or must be empty"):
        exporter.export_space(output)


def test_deployment_command_is_fixed_and_contains_no_token(tmp_path: Path) -> None:
    source_sha = "a" * 40
    command = deployer.build_upload_command(tmp_path / "space", source_sha)

    assert command[:3] == ("hf", "upload", "josephaterry/openweight")
    assert "--repo-type" in command
    assert "space" in command
    assert "--delete" in command
    assert "*" in command
    assert command[-1] == "Deploy OpenWeight from GitHub aaaaaaaaaaaa"
    assert "test-deploy-token" not in " ".join(command)


def test_deployer_maps_the_dedicated_secret_only_to_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export_dir = tmp_path / "space"
    exporter.export_space(export_dir)
    captured: dict[str, object] = {}

    def fake_run(command, *, check, env):
        captured.update(command=command, check=check, env=env)

    monkeypatch.setattr(deployer.subprocess, "run", fake_run)
    monkeypatch.setenv("HF_SPACE_DEPLOY_TOKEN", "test-deploy-token")

    deployer.deploy(export_dir, "b" * 40, "test-deploy-token")

    environment = captured["env"]
    assert isinstance(environment, dict)
    assert captured["check"] is True
    assert environment["HF_TOKEN"] == "test-deploy-token"
    assert "HF_SPACE_DEPLOY_TOKEN" not in environment
    assert "test-deploy-token" not in " ".join(captured["command"])
