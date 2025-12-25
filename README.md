# gatepi-ansible

Ansible playbook to provision the Gate ANPR stack on a Raspberry Pi5 (Bookworm).

## What it does
- Builds and installs OpenALPR from source when needed.
- Configures OpenALPR and runtime data paths.
- Installs and runs the gate worker service (`gate_anpr`).
- Sets up logging to `/var/log/gate-anpr/gate-anpr.log`.

## Requirements
- Ansible on your workstation.
- SSH access to the Pi user (default `pi`).
- The Pi reachable on your LAN.

## Configure secrets
Pushover credentials live in a local env file (gitignored).

1) Create the env file:

```sh
cp files/gate_anpr.env.example files/gate_anpr.env
```

2) Fill in your values:

```
PUSHOVER_USER_KEY=...
PUSHOVER_APP_TOKEN=...
GATE_ANPR_DEBUG=0
PLATE_ALLOWLIST_PATH=/etc/gate_anpr_allowlist.json
# Optional inline fallback (used only if PLATE_ALLOWLIST_PATH is empty)
PLATE_ALLOWLIST_JSON=[["A1ABC","Test Car"]]
```

3) Create the allowlist file:

```sh
cp files/plate_allowlist.json.example files/plate_allowlist.json
```

4) Fill in your plates:

```
[
  ["A1ABC", "Test Car"]
]
```

## Ansible env
Host details and stream URL live in a local env file (gitignored).

1) Create the env file:

```sh
cp ansible.env.example ansible.env
```

2) Fill in your values:

```
GATEPI_HOST=192.168.x.x
GATEPI_USER=pi
ANSIBLE_BECOME_PASSWORD=...
ALPRD_STREAM=/dev/video0
```

## Inventory
The inventory just names the host. Host/IP comes from the env file at runtime.

Create a local inventory file (gitignored):

```sh
cp inventory.ini.example inventory.ini
```

## Run the playbook

```sh
set -a
source ansible.env
set +a
ANSIBLE_BECOME_PASSWORD="$ANSIBLE_BECOME_PASSWORD" ansible-playbook -i inventory.ini site.yml -e ansible_host="$GATEPI_HOST" -e ansible_user="$GATEPI_USER"
```

## Optional OpenALPR smoketest
If you have the test image locally, you can run a smoketest during the playbook:

```sh
ANSIBLE_BECOME_PASSWORD="$ANSIBLE_BECOME_PASSWORD" ansible-playbook -i inventory.ini site.yml -e ansible_host="$GATEPI_HOST" -e ansible_user="$GATEPI_USER" -e run_openalpr_smoketest=true
```

The smoketest requires `tests/Test Image.png` to exist locally. If it’s missing, the playbook will fail with a clear message.

## Logs
- Service logs (systemd):

```sh
journalctl -u gate_anpr -f
```

- File logs:

```sh
tail -n100 /var/log/gate-anpr/gate-anpr.log
```

## Debug logging
Set `GATE_ANPR_DEBUG=1` in `files/gate_anpr.env` and re-run the playbook.

## Synthetic test job (run on the Pi)
The playbook installs a helper at `/opt/gate_anpr/tests/synthetic_job.py`:

```sh
/opt/gate_anpr/venv/bin/python /opt/gate_anpr/tests/synthetic_job.py --plate A1ABC --delay 60
```

You can also trigger it via Ansible:

```sh
ansible-playbook -i inventory.ini site.yml -e run_synthetic_job=true
```

Optional overrides:

```sh
ansible-playbook -i inventory.ini site.yml -e run_synthetic_job=true -e synthetic_plate=A1ABC -e synthetic_delay=60
```

## Notes
- The test image for OpenALPR lives under `tests/`.
- OpenALPR apt packages are not available on Bookworm, so the playbook builds from source.
