import json
import os
from pathlib import Path
from types import SimpleNamespace

import gate_https_activate as activation
import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    destination = tmp_path / "tls"
    staging = tmp_path / "staging"
    staging.mkdir()
    certificate, key = staging / "chain.pem", staging / "key.pem"
    certificate.write_bytes(b"test-certificate-v1")
    key.write_bytes(b"test-private-key-v1")
    key.chmod(0o600)
    config = tmp_path / "nginx.conf"
    config.write_text("server { listen 80; }\n")
    calls, validations = [], []

    def validator(domain, cert_path, key_path, min_valid_seconds, *, ca_file):
        validations.append((Path(cert_path), Path(key_path), ca_file))

    def command(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(activation, "validate_certificate", validator)
    monkeypatch.setattr(activation.subprocess, "run", command)

    def activate(**kwargs):
        return activation.activate_certificate(
            "gate.example.test",
            certificate,
            key,
            destination,
            nginx_config=config,
            lock_path=tmp_path / "config.lock",
            **kwargs,
        )

    def enable():
        config.write_text(f'server {{ ssl_certificate "{destination}/current/fullchain.pem"; }}\n')

    return SimpleNamespace(
        destination=destination,
        certificate=certificate,
        key=key,
        config=config,
        calls=calls,
        validations=validations,
        activate=activate,
        enable=enable,
    )


def test_first_issue_publishes_private_coherent_pair_without_nginx_reload(setup):
    result = setup.activate(ca_file="/test/required-root.pem")
    assert result == {"changed": True, "reloaded": False}
    current = setup.destination / "current"
    assert current.is_symlink()
    assert (current / "fullchain.pem").read_bytes() == setup.certificate.read_bytes()
    assert (current / "privkey.pem").read_bytes() == setup.key.read_bytes()
    assert (current / "fullchain.pem").resolve().parent == (current / "privkey.pem").resolve().parent
    assert setup.destination.stat().st_mode & 0o777 == 0o700
    assert current.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in current.iterdir())
    assert setup.calls == []
    assert len(setup.validations) == 2
    assert all(value[2] == "/test/required-root.pem" for value in setup.validations)


def test_rejected_staging_certificate_leaves_current_and_generations_untouched(setup, monkeypatch):
    setup.activate()
    current = setup.destination / "current"
    old_target = os.readlink(current)
    original_files = sorted(setup.destination.iterdir())

    def reject(*args, **kwargs):
        raise ValueError("sensitive certificate details")

    monkeypatch.setattr(activation, "validate_certificate", reject)
    with pytest.raises(activation.ActivationError, match="^certificate_validation_failed$"):
        setup.activate()
    assert os.readlink(current) == old_target
    assert sorted(setup.destination.iterdir()) == original_files
    assert setup.calls == []


def test_active_replacement_checks_nginx_then_reloads_a_complete_pair(setup, monkeypatch):
    setup.activate()
    previous = (setup.destination / "current").resolve()
    setup.enable()
    setup.certificate.write_bytes(b"test-certificate-v2")
    setup.key.write_bytes(b"test-private-key-v2")
    commands = []

    def inspect_active(arguments, **kwargs):
        current = setup.destination / "current"
        assert (current / "fullchain.pem").read_bytes() == b"test-certificate-v2"
        assert (current / "privkey.pem").read_bytes() == b"test-private-key-v2"
        assert previous.is_dir(), "Keep previous generation until validation/reload succeeds"
        commands.append(arguments)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(activation.subprocess, "run", inspect_active)
    assert setup.activate() == {"changed": True, "reloaded": True}
    assert commands == [["/usr/sbin/nginx", "-t"], ["/usr/bin/systemctl", "reload", "nginx"]]
    assert previous.is_dir()
    assert (setup.destination / "current").resolve() != previous


@pytest.mark.parametrize("failure", ["validation", "reload"])
def test_nginx_failure_restores_previous_link_and_removes_failed_generation(setup, monkeypatch, failure):
    setup.activate()
    current = setup.destination / "current"
    previous = os.readlink(current)
    setup.enable()
    setup.certificate.write_bytes(b"test-certificate-v2")
    setup.key.write_bytes(b"test-private-key-v2")
    calls = []

    def failed_command(arguments, **kwargs):
        calls.append(arguments)
        failed = len(calls) == (1 if failure == "validation" else 2)
        return SimpleNamespace(returncode=1 if failed else 0)

    monkeypatch.setattr(activation.subprocess, "run", failed_command)
    with pytest.raises(activation.ActivationError, match=f"nginx_{failure}_failed"):
        setup.activate()
    assert os.readlink(current) == previous
    assert (current / "fullchain.pem").read_bytes() == b"test-certificate-v1"
    assert [path.name for path in setup.destination.glob("generation-*")] == [previous]
    if failure == "reload":
        assert len(calls) == 4, "A failed reload is followed by a checked reload of the restored generation"


@pytest.mark.parametrize("failure", ["validation", "reload"])
def test_failed_first_activation_removes_current_link(setup, monkeypatch, failure):
    setup.enable()
    calls = []

    def fail(arguments, **kwargs):
        calls.append(arguments)
        return SimpleNamespace(returncode=int(len(calls) == (1 if failure == "validation" else 2)))

    monkeypatch.setattr(activation.subprocess, "run", fail)
    with pytest.raises(activation.ActivationError, match=f"nginx_{failure}_failed"):
        setup.activate()
    assert not os.path.lexists(setup.destination / "current")
    assert not list(setup.destination.glob("generation-*"))


def test_existing_non_symlink_current_is_not_overwritten(setup):
    setup.destination.mkdir()
    current = setup.destination / "current"
    current.mkdir()
    (current / "unrelated-file").write_text("keep")
    with pytest.raises(activation.ActivationError, match="current_path_is_not_a_symlink"):
        setup.activate()
    assert (current / "unrelated-file").read_text() == "keep"
    assert not list(setup.destination.glob("generation-*"))


def test_unchanged_active_pair_is_revalidated_and_checked_reload_is_retryable(setup):
    setup.activate()
    previous = os.readlink(setup.destination / "current")
    setup.enable()
    setup.validations.clear()
    assert setup.activate() == {"changed": False, "reloaded": True}
    assert os.readlink(setup.destination / "current") == previous
    assert len(list(setup.destination.glob("generation-*"))) == 1
    assert len(setup.validations) == 2
    assert setup.calls == [["/usr/sbin/nginx", "-t"], ["/usr/bin/systemctl", "reload", "nginx"]]


def test_manual_nginx_certificate_paths_do_not_trigger_reload(setup):
    setup.config.write_text(
        f"# ssl_certificate {setup.destination}/current/fullchain.pem;\n"
        "server { ssl_certificate /manual/certificate.pem; }\n"
    )
    assert setup.activate() == {"changed": True, "reloaded": False}
    assert setup.calls == []


def test_successful_reload_keeps_current_and_previous_but_preserves_unrelated_directories(setup):
    setup.activate()
    oldest = (setup.destination / "current").resolve()
    unrelated = setup.destination / "generation-abcdefgh"
    unrelated.mkdir()
    setup.enable()
    setup.certificate.write_bytes(b"test-certificate-v2")
    setup.key.write_bytes(b"test-private-key-v2")
    setup.activate()
    previous = (setup.destination / "current").resolve()
    setup.certificate.write_bytes(b"test-certificate-v3")
    setup.key.write_bytes(b"test-private-key-v3")
    setup.activate()
    assert not oldest.exists()
    assert previous.is_dir()
    assert unrelated.is_dir(), "An unmarked lookalike directory must never be deleted"
    assert len(list(setup.destination.glob("generation-*"))) == 3


def test_staging_change_during_validation_cannot_publish_unvalidated_bytes(setup, monkeypatch):
    setup.activate()
    previous = os.readlink(setup.destination / "current")
    setup.certificate.write_bytes(b"test-certificate-v2")
    setup.key.write_bytes(b"test-private-key-v2")
    calls = []

    def validate_then_change(domain, certificate, key, *args, **kwargs):
        calls.append(certificate)
        if len(calls) == 1:
            setup.key.write_bytes(b"mismatched-key")
        else:
            assert Path(key).read_bytes() == b"mismatched-key"
            raise ValueError("mismatched pair")

    monkeypatch.setattr(activation, "validate_certificate", validate_then_change)
    with pytest.raises(activation.ActivationError, match="certificate_validation_failed"):
        setup.activate()
    assert os.readlink(setup.destination / "current") == previous
    assert len(list(setup.destination.glob("generation-*"))) == 1


@pytest.mark.parametrize("failure", [False, True])
def test_cli_never_prints_certificate_key_or_subprocess_details(monkeypatch, capsys, failure):
    monkeypatch.setattr(activation.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "sys.argv",
        [
            "gate_https_activate.py",
            "--domain",
            "gate.example.test",
            "--certificate",
            "/stage/cert",
            "--key",
            "/stage/key",
        ],
    )

    def activate(*args, **kwargs):
        if failure:
            raise ValueError("SECRET PRIVATE KEY MATERIAL")
        return {"changed": False, "reloaded": True}

    monkeypatch.setattr(activation, "activate_certificate", activate)
    assert activation.main() == (1 if failure else 0)
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err
    assert "gate.example.test" not in captured.out + captured.err
    assert json.loads(captured.out)["ok"] is not failure
