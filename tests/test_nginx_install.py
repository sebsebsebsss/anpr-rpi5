import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from gate_nginx_install import install_config
from jinja2 import Environment, FileSystemLoader, StrictUndefined


def _render_nginx(https_enabled, domain=""):
    source = Path(__file__).resolve().parents[1] / "files" / "nginx"
    env = Environment(loader=FileSystemLoader(source), undefined=StrictUndefined)
    env.filters["bool"] = bool
    return env.get_template("gate-anpr.conf").render(
        ansible_default_ipv4={"address": "192.168.1.10"},
        ansible_hostname="gatepi",
        gatepi_alias_hostname="gate",
        gate_web_stream_jpeg="/run/gate-anpr/stream.jpg",
        gate_https_enabled=https_enabled,
        gate_https_domain=domain,
        gate_https_cert_path="/etc/gate-anpr/tls/fullchain.pem",
        gate_https_key_path="/etc/gate-anpr/tls/privkey.pem",
    )


def test_https_off_retains_http_and_does_not_require_certificate():
    config = _render_nginx(False)
    assert "listen 80;" in config and "192.168.1.10" in config and "gate.local" in config
    assert "listen 443" not in config
    assert "ssl_certificate" not in config
    assert "example.com" not in config
    assert "gate.example.com" in _render_nginx(False, "gate.example.com")


def test_https_on_rejects_unknown_hosts_and_preserves_http_proxy_origin():
    config = _render_nginx(True, "gate.example.com")
    assert "listen 443 ssl default_server;" in config
    assert "ssl_reject_handshake on;\n    return 444;" in config
    assert "server_name gate.example.com;" in config
    assert "listen 80;" in config and "192.168.1.10" in config
    assert "Strict-Transport-Security" not in config and "return 301" not in config and "return 308" not in config
    assert config.count("proxy_set_header Host $http_host;") == 4
    assert config.count("proxy_set_header X-Forwarded-Proto $scheme;") == 4


def _paths(tmp_path):
    available, enabled_dir = tmp_path / "available", tmp_path / "enabled"
    available.mkdir()
    enabled_dir.mkdir()
    site = available / "gate.conf"
    enabled, default = enabled_dir / "gate.conf", enabled_dir / "default"
    candidate = tmp_path / "candidate"
    candidate.write_text("candidate configuration")
    return candidate, site, enabled, default


def test_full_nginx_validation_failure_restores_previous_config_and_links(tmp_path, monkeypatch):
    candidate, site, enabled, default = _paths(tmp_path)
    site.write_text("original configuration")
    site.chmod(0o640)
    enabled.symlink_to(site)
    default.symlink_to("../available/default")

    def rejected(command, **kwargs):
        assert command == ["/usr/sbin/nginx", "-t"]
        assert site.read_text() == "candidate configuration"
        assert not os.path.lexists(default)
        return SimpleNamespace(returncode=1, stderr="conflicting server configuration")

    monkeypatch.setattr("gate_nginx_install.subprocess.run", rejected)
    with pytest.raises(RuntimeError, match="conflicting"):
        install_config(candidate, site, enabled, default)
    assert site.read_text() == "original configuration"
    assert site.stat().st_mode & 0o777 == 0o640
    assert os.readlink(enabled) == str(site)
    assert os.readlink(default) == "../available/default"
    assert not list(tmp_path.rglob(".gate-nginx-*"))


def test_failed_first_install_removes_new_files_and_restores_regular_default(tmp_path, monkeypatch):
    candidate, site, enabled, default = _paths(tmp_path)
    default.write_text("original default")
    monkeypatch.setattr(
        "gate_nginx_install.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=1, stderr="invalid")
    )
    with pytest.raises(RuntimeError):
        install_config(candidate, site, enabled, default)
    assert not site.exists()
    assert not os.path.lexists(enabled)
    assert default.read_text() == "original default"


def test_valid_configuration_installs_and_remains_idempotent(tmp_path, monkeypatch):
    candidate, site, enabled, default = _paths(tmp_path)
    monkeypatch.setattr("gate_nginx_install.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=0, stderr=""))
    assert install_config(candidate, site, enabled, default) is True
    assert site.read_text() == candidate.read_text()
    assert os.readlink(enabled) == str(site)
    assert install_config(candidate, site, enabled, default) is False
