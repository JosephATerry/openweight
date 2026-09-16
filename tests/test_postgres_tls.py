from pathlib import Path

import pytest

from openweight_platform import postgres_tls


def write_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "ca-certificates.crt"
    bundle.write_text("test root bundle\n", encoding="utf-8")
    return bundle


def test_verify_full_accepts_only_an_explicit_readable_bundle(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path)

    assert postgres_tls.resolve_postgres_tls_config(
        "verify-full",
        str(bundle),
    ) == ("verify-full", str(bundle))


@pytest.mark.parametrize("root", [None, "", "system", "relative/roots.pem"])
def test_verify_full_rejects_missing_or_implicit_trust(root: str | None) -> None:
    with pytest.raises(ValueError, match="POSTGRES_SSLROOTCERT"):
        postgres_tls.resolve_postgres_tls_config("verify-full", root)


def test_verify_full_rejects_a_missing_bundle(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="readable regular CA bundle"):
        postgres_tls.resolve_postgres_tls_config(
            "verify-full",
            str(tmp_path / "missing.pem"),
        )


def test_verify_full_rejects_an_empty_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "empty.pem"
    bundle.touch()

    with pytest.raises(ValueError, match="readable regular CA bundle"):
        postgres_tls.resolve_postgres_tls_config("verify-full", str(bundle))


def test_verify_full_rejects_an_unreadable_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = write_bundle(tmp_path)
    monkeypatch.setattr(postgres_tls.os, "access", lambda *_args: False)

    with pytest.raises(ValueError, match="readable regular CA bundle"):
        postgres_tls.resolve_postgres_tls_config("verify-full", str(bundle))


def test_local_prefer_mode_can_omit_a_ca_bundle() -> None:
    assert postgres_tls.resolve_postgres_tls_config("prefer", None) == (
        "prefer",
        None,
    )


@pytest.mark.parametrize("mode", ["disable", "allow", "require", "verify-ca"])
def test_tls_validator_never_changes_a_mode_to_verify_full(mode: str) -> None:
    if mode == "allow":
        with pytest.raises(ValueError, match="POSTGRES_SSLMODE"):
            postgres_tls.resolve_postgres_tls_config(mode, None)
    else:
        resolved, _ = postgres_tls.resolve_postgres_tls_config(mode, None)
        assert resolved == mode
