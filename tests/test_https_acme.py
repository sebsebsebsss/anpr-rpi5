import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import gate_https_acme as acme
import pytest


@pytest.fixture
def config(tmp_path):
    credentials = tmp_path / "dns.env"
    credentials.write_text("DuckDNS_Token=private-test-token-123456\n")
    credentials.chmod(0o600)
    return {
        "domain": "gate.example.com",
        "challenge_alias": "gate-cert-example.duckdns.org",
        "credentials_file": str(credentials),
        "acme_home": str(tmp_path / "private-acme-state"),
        "acme_script": str(tmp_path / "client/acme.sh"),
        "destination": str(tmp_path / "certificates"),
    }


def test_skipped_renewal_still_retries_validated_activation(config, monkeypatch):
    calls = []

    def client(command, environment):
        calls.append(command)
        assert "private-test-token-123456" not in " ".join(command)
        assert environment["DuckDNS_Token"] == "private-test-token-123456"
        assert environment["ACME_PACKAGED"] == "1"
        if "--install-cert" in command:
            activation = shlex.split(command[command.index("--reloadcmd") + 1])
            assert activation[activation.index("--ca-file") + 1] == acme.ROOT_X1
            assert activation[activation.index("--domain") + 1] == config["domain"]
            assert activation[activation.index("--destination") + 1] == config["destination"]
            assert activation[activation.index("--key") + 1] == str(Path(config["acme_home"]) / "staging/privkey.pem")
            return 0
        return 2

    monkeypatch.setattr(acme, "_execute", client)
    acme.run(config)
    assert len(calls) == 3
    assert "--issue" in calls[0] and "--renew" in calls[1] and "--install-cert" in calls[2]
    assert all("--force" not in call for call in calls)
    assert calls[0][calls[0].index("--preferred-chain") + 1] == "ISRG Root X1"
    assert calls[0][calls[0].index("--keylength") + 1] == "2048"
    assert Path(config["acme_home"]).stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("stage", ["--issue", "--install-cert"])
def test_client_failure_stops_pipeline_without_disclosing_output(config, monkeypatch, capsys, stage):
    calls = []

    def client(command, environment):
        calls.append(command)
        return 1 if stage in command else 0

    monkeypatch.setattr(acme, "_execute", client)
    with pytest.raises(acme.AcmeError) as error:
        acme.run(config)
    assert stage in calls[-1]
    assert "private-test-token" not in str(error.value)
    assert "https://" not in str(error.value)
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_failed_saved_renewal_hook_gets_replaced_but_failure_remains_visible(config, monkeypatch):
    calls = []

    def client(command, environment):
        calls.append(command)
        return 1 if "--renew" in command else 0

    monkeypatch.setattr(acme, "_execute", client)
    with pytest.raises(acme.AcmeError, match="Certificate renewal failed"):
        acme.run(config)
    assert "--install-cert" in calls[-1]


def test_timeout_does_not_include_subprocess_command_or_output(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret-command", 900, output=b"secret-response")

    monkeypatch.setattr(acme, "_execute", timeout)
    with pytest.raises(acme.AcmeError, match="renewal timed out") as error:
        acme.run_command(["secret-command"], {}, "renewal")
    assert "secret" not in str(error.value)


def test_real_client_output_is_captured_even_on_failure(capsys):
    command = [sys.executable, "-c", "import sys; print('private-token'); sys.stderr.write('private-url'); sys.exit(1)"]
    with pytest.raises(acme.AcmeError) as error:
        acme.run_command(command, dict(os.environ), "Client operation")
    assert "private" not in str(error.value)
    output = capsys.readouterr()
    assert not output.out and not output.err


def test_timeout_terminates_descendants_that_inherit_output_pipes():
    command = [
        sys.executable,
        "-c",
        "import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); time.sleep(30)",
    ]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        acme._execute(command, dict(os.environ), timeout=0.2)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    "value",
    [
        "DuckDNS_Token=\n",
        "Other_Token=private-test-token-123456\n",
        "DuckDNS_Token=$(touch /tmp/unwanted)\n",
        "DuckDNS_Token=`id`\n",
        "DuckDNS_Token=private-test-token-123456\nDuckDNS_Token=second-private-token\n",
    ],
)
def test_invalid_credentials_rejected_without_shell_execution(tmp_path, value):
    path = tmp_path / "credentials"
    path.write_text(value)
    path.chmod(0o600)
    with pytest.raises(acme.AcmeError):
        acme.load_token(path)


def test_quoted_token_and_comments_are_supported(tmp_path):
    path = tmp_path / "credentials"
    path.write_text('# Private\nDuckDNS_Token="private-test-token-123456" # inline comment\n')
    path.chmod(0o600)
    assert acme.load_token(path) == "private-test-token-123456"
    path.chmod(0o644)
    with pytest.raises(acme.AcmeError, match="0600"):
        acme.load_token(path)


def test_credentials_preflight_never_runs_the_client(config, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["gate_https_acme.py", "--check-credentials", config["credentials_file"]])
    monkeypatch.setattr(acme, "run", lambda *args: pytest.fail("Preflight must not make DNS or ACME requests"))
    acme.main()
    output = capsys.readouterr()
    assert output.out.strip() == "Private DuckDNS credentials validated"
    assert not output.err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "gate.example.com;command"),
        ("domain", ["gate.example.com"]),
        ("challenge_alias", "_acme-challenge.gate.duckdns.org"),
        ("challenge_alias", "gate.example.com"),
        ("acme_script", "relative/path"),
        ("acme_home", "/private acme state"),
    ],
)
def test_invalid_config_rejected(config, tmp_path, field, value):
    config[field] = value
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config))
    with pytest.raises(acme.AcmeError):
        acme.load_config(path)
