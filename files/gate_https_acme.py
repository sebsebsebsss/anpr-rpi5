#!/usr/bin/env python3
"""Issue and renew a LAN site's certificate through a dedicated DuckDNS alias."""

import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import stat
import subprocess
from pathlib import Path

from gate_https_validate import DOMAIN

DUCK_ALIAS = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.duckdns\.org$")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,256}$")
ROOT_X1 = "/etc/ssl/certs/ISRG_Root_X1.pem"


class AcmeError(Exception):
    """An operational failure whose message does not contain credentials."""


def load_config(path):
    config = json.loads(Path(path).read_text())
    if not isinstance(config, dict):
        raise AcmeError("ACME configuration must be a JSON object")
    domain = config.get("domain", "")
    alias = config.get("challenge_alias", "")
    if not isinstance(domain, str) or len(domain) > 253 or not DOMAIN.fullmatch(domain):
        raise AcmeError("ACME configuration needs a valid certificate hostname")
    if not isinstance(alias, str) or not DUCK_ALIAS.fullmatch(alias):
        raise AcmeError("Challenge alias must be a dedicated name.duckdns.org hostname")
    for field in ("credentials_file", "acme_home", "acme_script", "destination"):
        value = config.get(field)
        if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
            raise AcmeError(f"ACME configuration needs an absolute {field} path")
    if any(character.isspace() for character in config["acme_home"]):
        raise AcmeError("The managed ACME state path cannot contain whitespace")
    return config


def load_token(path):
    path = Path(path)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise AcmeError("DuckDNS credentials must have mode 0600")
    values = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if name.strip() != "DuckDNS_Token" or not separator:
            raise AcmeError("Credentials must contain only a DuckDNS_Token assignment")
        try:
            parsed = shlex.split(value, comments=True)
        except ValueError:
            raise AcmeError("DuckDNS token has invalid quoting") from None
        if len(parsed) != 1 or not TOKEN.fullmatch(parsed[0]):
            raise AcmeError("DuckDNS_Token is missing or invalid")
        values.append(parsed[0])
    if len(values) != 1:
        raise AcmeError("Credentials need exactly one DuckDNS_Token assignment")
    return values[0]


def _execute(command, environment, timeout=900):
    with subprocess.Popen(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # curl and activation hooks inherit the pipes. Terminate the whole
            # client group so a shell timeout cannot leave renewal running.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate()
            raise
        return process.returncode


def run_command(command, environment, stage, accepted=(0,)):
    # Provider/client output can contain authenticated URLs. Never forward it
    # to Ansible or the journal; pi can read the system journal on this host.
    try:
        returncode = _execute(command, environment)
    except subprocess.TimeoutExpired:
        raise AcmeError(f"{stage} timed out; activation status is unknown and the next check will retry") from None
    except OSError:
        raise AcmeError(f"Cannot execute the managed ACME client during {stage}") from None
    if returncode not in accepted:
        raise AcmeError(
            f"{stage} failed (exit {returncode}); check DNS delegation, credentials and certificate validity"
        )


def run(config):
    token = load_token(config["credentials_file"])
    home = Path(config["acme_home"])
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    home.chmod(0o700)
    staging = home / "staging"
    staging.mkdir(exist_ok=True, mode=0o700)
    staging.chmod(0o700)
    environment = dict(os.environ, DuckDNS_Token=token, ACME_PACKAGED="1")
    # The packaged source tree remains immutable. acme.sh stores accounts,
    # keys and provider settings only in its protected state directory.
    base = [
        "/bin/sh",
        config["acme_script"],
        "--home",
        str(home),
        "--config-home",
        str(home),
        "--auto-upgrade",
        "0",
        "--server",
        "letsencrypt",
    ]
    activation = [
        "/usr/bin/python3",
        str(Path(__file__).with_name("gate_https_activate.py")),
        "--domain",
        config["domain"],
        "--certificate",
        str(staging / "fullchain.pem"),
        "--key",
        str(staging / "privkey.pem"),
        "--destination",
        config["destination"],
        "--ca-file",
        ROOT_X1,
    ]
    with (home / ".gate-renewal.lock").open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        # --issue skips an existing certificate until its renewal window.
        # The separate --renew also checks fresh ACME Renewal Information,
        # allowing a CA to request an earlier emergency renewal.
        run_command(
            base
            + [
                "--issue",
                "--dns",
                "dns_duckdns",
                "--challenge-alias",
                config["challenge_alias"],
                "-d",
                config["domain"],
                "--keylength",
                "2048",
                "--preferred-chain",
                "ISRG Root X1",
            ],
            environment,
            "Certificate issuance",
            accepted=(0, 2),
        )
        renewal_error = None
        try:
            run_command(
                base + ["--renew", "-d", config["domain"]],
                environment,
                "Certificate renewal",
                accepted=(0, 2),
            )
        except AcmeError as error:
            # acme.sh may be retrying an older saved activation hook. Refresh
            # that hook with the current checked installation below, while
            # keeping renewal failure visible to systemd. Timeout cleanup has
            # already stopped the entire client group before reaching here.
            renewal_error = error
        # Reinstall even when renewal is not due. This retries a previously
        # failed activation and refreshes the saved activation hook on upgrades.
        # Only the hook publishes the validated pair to nginx's live paths.
        run_command(
            base
            + [
                "--install-cert",
                "-d",
                config["domain"],
                "--key-file",
                str(staging / "privkey.pem"),
                "--fullchain-file",
                str(staging / "fullchain.pem"),
                "--reloadcmd",
                shlex.join(activation),
            ],
            environment,
            "Certificate activation",
        )
        if renewal_error is not None:
            raise renewal_error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config")
    mode.add_argument("--check-credentials", metavar="FILE", help="Validate a private token file without DNS requests")
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.check_credentials:
            load_token(args.check_credentials)
        else:
            run(load_config(args.config))
    except (AcmeError, OSError, ValueError) as error:
        # Parser/OS exceptions can include input content or filenames; restrict
        # detailed messages to our fixed, deliberately nonsensitive labels.
        message = str(error) if isinstance(error, AcmeError) else "Cannot read ACME configuration or credentials"
        parser.exit(1, message + "\n")
    if args.check_credentials:
        print("Private DuckDNS credentials validated")
    else:
        print("Certificate renewal check and validated activation succeeded")


if __name__ == "__main__":
    main()
