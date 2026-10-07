import shutil
import subprocess
from pathlib import Path

import pytest
from gate_https_validate import validate_certificate


def _run(*args):
    subprocess.run(["openssl", *map(str, args)], capture_output=True, check=True, timeout=30)


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("OpenSSL is needed for certificate validation tests")
    directory = tmp_path_factory.mktemp("gate-certificate-tests")
    ca, ca_key = directory / "ca.pem", directory / "ca.key"
    cert, key, csr = directory / "server.pem", directory / "server.key", directory / "server.csr"
    # These ephemeral test certificates are never installed in nginx or a trust store.
    _run(
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "2",
        "-subj",
        "/CN=Gate Test CA",
        "-keyout",
        ca_key,
        "-out",
        ca,
    )
    _run("req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=gate.example.com", "-keyout", key, "-out", csr)
    extensions = directory / "server.ext"
    extensions.write_text(
        "subjectAltName=DNS:gate.example.com\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
    )
    _run(
        "x509",
        "-req",
        "-in",
        csr,
        "-CA",
        ca,
        "-CAkey",
        ca_key,
        "-set_serial",
        "1",
        "-days",
        "2",
        "-extfile",
        extensions,
        "-out",
        cert,
    )
    chain = directory / "fullchain.pem"
    chain.write_bytes(cert.read_bytes() + ca.read_bytes())
    unrelated = directory / "other-ca.pem"
    _run("req", "-x509", "-key", ca_key, "-days", "2", "-subj", "/CN=Unrelated Test CA", "-out", unrelated)
    return {"chain": chain, "key": key, "ca": ca, "ca_key": ca_key, "unrelated": unrelated}


def test_valid_trusted_hostname_key_and_lifetime(tls_material):
    material = tls_material
    validate_certificate("gate.example.com", material["chain"], material["key"], ca_file=material["ca"])


def test_untrusted_chain_is_rejected(tls_material):
    material = tls_material
    with pytest.raises(ValueError):
        validate_certificate("gate.example.com", material["chain"], material["key"], ca_file=material["unrelated"])


def test_wrong_hostname_key_and_expiring_certificate_are_rejected(tls_material):
    material = tls_material
    with pytest.raises(ValueError):
        validate_certificate("other.example.com", material["chain"], material["key"], ca_file=material["ca"])
    with pytest.raises(ValueError, match="do not match"):
        validate_certificate("gate.example.com", material["chain"], material["ca_key"], ca_file=material["ca"])
    with pytest.raises(ValueError, match="expires too soon"):
        validate_certificate(
            "gate.example.com", material["chain"], material["key"], min_valid_seconds=3 * 86400, ca_file=material["ca"]
        )


def test_exposed_private_key_is_rejected(tls_material, tmp_path):
    key = tmp_path / "exposed.key"
    key.write_bytes(tls_material["key"].read_bytes())
    key.chmod(0o644)
    with pytest.raises(ValueError, match="mode 0600"):
        validate_certificate("gate.example.com", tls_material["chain"], key, ca_file=tls_material["ca"])


@pytest.mark.parametrize("domain", ["127.0.0.1", "https://gate.example.com", "*.example.com", "gate.example.com;", ""])
def test_invalid_hostname_rejected_before_reading_files(domain):
    with pytest.raises(ValueError, match="DNS hostname"):
        validate_certificate(domain, Path("/missing/cert"), Path("/missing/key"))
