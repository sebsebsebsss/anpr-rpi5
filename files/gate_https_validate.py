#!/usr/bin/env python3
"""Validate an existing publicly trusted HTTPS certificate before deployment."""

import argparse
import re
import ssl
import stat
import subprocess
import tempfile
import time
from pathlib import Path

DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")


def _openssl(*args, input_data=None):
    result = subprocess.run(
        ["openssl", *map(str, args)],
        input=input_data if input_data is not None else b"",
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(detail or "OpenSSL certificate validation failed")
    return result.stdout


def validate_certificate(
    domain, certificate, key, min_valid_seconds=86400, ca_file="/etc/ssl/certs/ca-certificates.crt"
):
    if len(domain) > 253 or not DOMAIN.fullmatch(domain):
        raise ValueError("HTTPS needs a DNS hostname, not an IP address, wildcard or URL")
    if min_valid_seconds < 0:
        raise ValueError("Minimum remaining certificate lifetime cannot be negative")
    certificate = Path(certificate)
    key = Path(key)
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        raise ValueError("HTTPS private key must not be readable by group or other users (use mode 0600)")
    with tempfile.TemporaryDirectory(prefix="gate-tls-check-") as directory:
        leaf = Path(directory) / "leaf.pem"
        _openssl("x509", "-in", certificate, "-out", leaf)
        _openssl(
            "verify",
            "-CAfile",
            ca_file,
            "-no-CApath",
            "-no-CAstore",
            "-untrusted",
            certificate,
            "-verify_hostname",
            domain,
            "-purpose",
            "sslserver",
            leaf,
        )
        end_date = _openssl("x509", "-in", leaf, "-enddate", "-noout").decode("ascii").strip()
        expires_at = ssl.cert_time_to_seconds(end_date.split("=", 1)[1])
        if expires_at < time.time() + min_valid_seconds:
            raise ValueError("HTTPS certificate expires too soon; renew it before deployment")
        cert_key = _openssl("x509", "-in", leaf, "-pubkey", "-noout")
        cert_public_der = _openssl("pkey", "-pubin", "-outform", "DER", input_data=cert_key)
        key_public_der = _openssl("pkey", "-in", key, "-pubout", "-outform", "DER")
        if cert_public_der != key_public_der:
            raise ValueError("HTTPS certificate and private key do not match")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--certificate", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--min-valid-seconds", type=int, default=86400)
    args = parser.parse_args()
    validate_certificate(args.domain, args.certificate, args.key, args.min_valid_seconds)
    print("HTTPS certificate, trust chain, hostname, lifetime and private key validated")


if __name__ == "__main__":
    main()
