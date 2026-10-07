#!/usr/bin/env python3
"""Install a candidate nginx site, restoring every changed path if nginx -t fails."""

import argparse
import fcntl
import os
import stat
import subprocess
import tempfile
from pathlib import Path


def _snapshot(path):
    if path.is_symlink():
        return {"link": os.readlink(path)}
    if not path.exists():
        return None
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Refusing to replace non-file nginx configuration: {path}")
    return {"data": path.read_bytes(), "mode": stat.S_IMODE(info.st_mode), "uid": info.st_uid, "gid": info.st_gid}


def _write_atomic(path, data, mode=0o644, owner=None):
    fd, temporary = tempfile.mkstemp(prefix=".gate-nginx-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _link_atomic(path, target):
    fd, temporary = tempfile.mkstemp(prefix=".gate-nginx-", dir=path.parent)
    os.close(fd)
    os.unlink(temporary)
    try:
        os.symlink(target, temporary)
        os.replace(temporary, path)
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)


def _restore(path, previous):
    if previous is None:
        path.unlink(missing_ok=True)
    elif "link" in previous:
        _link_atomic(path, previous["link"])
    else:
        _write_atomic(path, previous["data"], previous["mode"], (previous["uid"], previous["gid"]))


def install_config(candidate, site, enabled, default_site, nginx="/usr/sbin/nginx"):
    paths = [Path(site), Path(enabled), Path(default_site)]
    site, enabled, default_site = paths
    previous = {path: _snapshot(path) for path in paths}
    desired = Path(candidate).read_bytes()
    changed = (
        previous[site] is None
        or previous[site].get("data") != desired
        or previous[enabled] != {"link": str(site)}
        or previous[default_site] is not None
    )
    try:
        if changed:
            _write_atomic(site, desired)
            _link_atomic(enabled, str(site))
            default_site.unlink(missing_ok=True)
        # Running workers keep their old configuration until Ansible reloads it.
        result = subprocess.run([nginx, "-t"], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "nginx configuration validation failed")
    except BaseException:
        if changed:
            for path in reversed(paths):
                _restore(path, previous[path])
        raise
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate")
    args = parser.parse_args()
    # Serialise deployments; a future renewal hook can use the same lock.
    with open("/run/gate-nginx-config.lock", "a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        changed = install_config(
            args.candidate,
            "/etc/nginx/sites-available/gate-anpr.conf",
            "/etc/nginx/sites-enabled/gate-anpr.conf",
            "/etc/nginx/sites-enabled/default",
        )
    print("changed" if changed else "unchanged")


if __name__ == "__main__":
    main()
