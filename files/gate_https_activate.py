#!/usr/bin/env python3
"""Validate and atomically activate an issued certificate, independently of its CA client."""

import argparse
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

from gate_https_validate import validate_certificate

GENERATION = re.compile(r"^generation-[a-z0-9_]{8}$")
MARKER = ".gate-https-generation"
MARKER_CONTENT = b"gate-anpr TLS generation v1\n"


class ActivationError(Exception):
    """A fixed error label that never contains certificate material or command output."""


def _validate(domain, certificate, key, min_valid_seconds, ca_file):
    try:
        validate_certificate(domain, certificate, key, min_valid_seconds, ca_file=ca_file)
    except Exception as exc:
        raise ActivationError("certificate_validation_failed") from exc


def _managed_https_enabled(nginx_config, certificate):
    try:
        content = Path(nginx_config).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    tokens = shlex.shlex(content, posix=True, punctuation_chars=";{}")
    tokens.whitespace_split = True
    words = list(tokens)
    return any(
        word == "ssl_certificate" and words[index + 1] == str(certificate) and words[index + 2].startswith(";")
        for index, word in enumerate(words[:-2])
    )


def _write_private(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_current(current, target):
    descriptor, temporary = tempfile.mkstemp(prefix=".current-", dir=current.parent)
    os.close(descriptor)
    os.unlink(temporary)
    try:
        os.symlink(target, temporary)
        os.replace(temporary, current)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _command(arguments, failure):
    try:
        result = subprocess.run(arguments, capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ActivationError(failure) from exc
    if result.returncode:
        raise ActivationError(failure)


def _prune_generations(destination, preserved):
    """Only remove this helper's marked directories, after a checked reload."""
    preserved = {path.resolve() for path in preserved}
    for generation in destination.iterdir():
        if not GENERATION.fullmatch(generation.name) or generation.is_symlink() or not generation.is_dir():
            continue
        if generation.resolve() in preserved:
            continue
        marker = generation / MARKER
        if marker.is_file() and not marker.is_symlink() and marker.read_bytes() == MARKER_CONTENT:
            shutil.rmtree(generation)


def activate_certificate(
    domain,
    certificate,
    key,
    destination="/etc/gate-anpr/tls",
    min_valid_seconds=86400,
    *,
    nginx_config="/etc/nginx/sites-enabled/gate-anpr.conf",
    nginx="/usr/sbin/nginx",
    systemctl="/usr/bin/systemctl",
    lock_path="/run/gate-nginx-config.lock",
    ca_file="/etc/ssl/certs/ca-certificates.crt",
):
    """Publish a coherent pair and return only nonsensitive change/reload flags.

    The managed nginx site is inspected for the exact current/fullchain.pem
    directive. Manual certificate paths are deliberately left alone. Initial
    issuance can therefore publish before Ansible enables the TLS listener.
    """
    destination = Path(destination).absolute()
    certificate, key = Path(certificate), Path(key)
    descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _validate(domain, certificate, key, min_valid_seconds, ca_file)
        certificate_data, key_data = certificate.read_bytes(), key.read_bytes()
        current = destination / "current"
        if os.path.lexists(current) and not current.is_symlink():
            raise ActivationError("current_path_is_not_a_symlink")
        previous = os.readlink(current) if current.is_symlink() else None
        active = _managed_https_enabled(nginx_config, current / "fullchain.pem")
        try:
            unchanged = (current / "fullchain.pem").read_bytes() == certificate_data and (
                current / "privkey.pem"
            ).read_bytes() == key_data
        except FileNotFoundError:
            unchanged = False
        if unchanged:
            # Validate the actual active material too, then allow a checked
            # reload to recover from a previously interrupted activation.
            _validate(domain, current / "fullchain.pem", current / "privkey.pem", min_valid_seconds, ca_file)
            if active:
                _command([nginx, "-t"], "nginx_validation_failed")
                _command([systemctl, "reload", "nginx"], "nginx_reload_failed")
            return {"changed": False, "reloaded": active}

        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.chmod(0o700)
        if os.geteuid() == 0:
            os.chown(destination, 0, 0)
        generation = Path(tempfile.mkdtemp(prefix="generation-", dir=destination))
        published = False
        reload_attempted = False
        try:
            _write_private(generation / "fullchain.pem", certificate_data)
            _write_private(generation / "privkey.pem", key_data)
            _write_private(generation / MARKER, MARKER_CONTENT)
            # Staging files may change while a CA client writes them. Verify
            # the copied pair before making its immutable generation current.
            _validate(domain, generation / "fullchain.pem", generation / "privkey.pem", min_valid_seconds, ca_file)
            _replace_current(current, generation.name)
            published = True
            if active:
                _command([nginx, "-t"], "nginx_validation_failed")
                reload_attempted = True
                _command([systemctl, "reload", "nginx"], "nginx_reload_failed")
        except BaseException:
            if published:
                if previous is None:
                    current.unlink(missing_ok=True)
                else:
                    _replace_current(current, previous)
                if reload_attempted and previous is not None:
                    # A failed reload command might have delivered its signal.
                    # Reapply the restored pair when possible; keep the original
                    # failure visible even if this recovery also fails.
                    try:
                        _command([nginx, "-t"], "nginx_rollback_validation_failed")
                        _command([systemctl, "reload", "nginx"], "nginx_rollback_reload_failed")
                    except ActivationError:
                        pass
            shutil.rmtree(generation)
            raise

        if active:
            preserved = [generation]
            if previous is not None:
                preserved.append(destination / previous)
            _prune_generations(destination, preserved)
        return {"changed": True, "reloaded": active}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--certificate", required=True, help="Issued staging fullchain PEM")
    parser.add_argument("--key", required=True, help="Issued staging private key PEM")
    parser.add_argument("--destination", default="/etc/gate-anpr/tls")
    parser.add_argument("--min-valid-seconds", type=int, default=86400)
    parser.add_argument("--ca-file", default="/etc/ssl/certs/ca-certificates.crt", help="Required trust root bundle")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Certificate activation must run as root")
    try:
        result = activate_certificate(
            args.domain, args.certificate, args.key, args.destination, args.min_valid_seconds, ca_file=args.ca_file
        )
    except Exception as exc:
        label = str(exc) if isinstance(exc, ActivationError) else "certificate_activation_failed"
        print(json.dumps({"ok": False, "error": label}))
        return 1
    print(json.dumps({"ok": True, **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
