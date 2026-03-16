# gatepi-anpr

Raspberry Pi 5 gate automation with ANPR and a LAN-first web UI.

This project turns a Pi into a practical gate controller that reads an RTSP camera,
recognises number plates, opens for allowlisted vehicles, and keeps an auditable
history with images and timings.

![Gatepi ANPR UI](docs/UI.png)

## Who this is for
- Home or small-site setups with a physical gate controller already in place.
- You want automatic opening for trusted vehicles, plus manual override and logs.
- You prefer local/LAN operation over cloud dependence.

## Highlights
- One-command provisioning on Raspberry Pi 5 (Debian Trixie target).
- OpenALPR daemon + gate worker + web UI + RTSP stream pipeline.
- Tablet-friendly interface for live view, events, allowlist edits, and control.
- Built-in housekeeping: old captures, logs, and event DB retention.

## Architecture
- `alprd`: OpenALPR daemon
- `gate_anpr`: queue consumer + GPIO relay trigger logic
- `gate_anpr_web`: Flask web app
- `gate_anpr_stream_jpeg`: RTSP -> JPEG stream for UI

## Physical setup (high level)
```mermaid
flowchart LR
  Controller["Controller machine<br/>(Ansible + browser)"]
  Camera["IP camera<br/>(RTSP stream)"]
  Pi["Raspberry Pi 5<br/>(alprd + gate_anpr + web UI)"]
  Relay["Relay board"]
  GateCtrl["Gate controller"]
  Gate["Gate motor"]

  Controller <-->|SSH deploy + HTTP UI| Pi
  Camera -->|RTSP video| Pi
  Pi -->|GPIO trigger| Relay
  Relay -->|momentary contact| GateCtrl
  GateCtrl -->|open/close control| Gate
```

## Hardware requirements
- Raspberry Pi 5 (8GB proven, 4GB likely to work)
- Relay board wired to your existing gate/garage controller
- IP camera with stable RTSP stream (>99% success rate on dome camera with manual shutter speed, night time IR and mechanical zoom - Annke CZ804)

## Software requirements
- Ansible installed on your controller machine
- SSH access to the Pi
- Pi reachable on your LAN
- Outbound internet access on first provision for `apt` packages and, if needed, an OpenALPR source clone

## Configure secrets
App settings live in a local file (gitignored):

```sh
cp files/gate_anpr.env.example files/gate_anpr.env
```

Required values in `files/gate_anpr.env`:

```ini
GATE_ANPR_DEBUG=0
PLATE_ALLOWLIST_PATH=/opt/gate_anpr/allowlist.json
GATE_PIN_BOARD=23
GATE_PIN_BCM=11
```

Optional values:

```ini
PUSHOVER_USER_KEY=...
PUSHOVER_APP_TOKEN=...
MATCH_DEDUP_SECONDS=60
FUZZY_ALLOWLIST=1
FUZZY_MAX_DISTANCE=1
FUZZY_MIN_CONFIDENCE=75
GATE_API_SHARED_SECRET=change-me
GATE_WEB_STREAM_URL=/static/stream.jpg
GATE_WEB_STREAM_FPS=12.5
GATE_WEB_STREAM_WIDTH=1280
GATE_WEB_STREAM_HEIGHT=720
```

If `PUSHOVER_USER_KEY` and `PUSHOVER_APP_TOKEN` are omitted, deploy still works; notifications are just disabled.

Create your local allowlist file:

```sh
cp files/plate_allowlist.json.example files/plate_allowlist.json
```

Example:

```json
[
  ["A1ABC", "Test Car"]
]
```

## Controller env
Controller-side runtime vars (gitignored):

```sh
cp ansible.env.example ansible.env
```

Fill in:

```ini
GATEPI_HOST=192.168.x.x
GATEPI_USER=pi
ANSIBLE_BECOME_PASSWORD=...
ALPRD_STREAM=rtsp://user:pass@camera-ip:554/h264Preview_01_main
```

Useful optional values from `ansible.env.example`:

```ini
ALPRD_CPU_AFFINITY=
ALPRD_ROI=1,208,2092,888
GATEPI_ALIAS_HOSTNAME=gate
```

## Inventory
`site.yml` targets host group `gatepi`.

Create inventory:

```sh
cp inventory.ini.example inventory.ini
```

If you want custom host groups, map them under `gatepi` using children.

## Quickstart

Load controller env before running any `ansible-playbook` command in this README:

```sh
set -a
source ansible.env
set +a
ANSIBLE_BECOME_PASSWORD="$ANSIBLE_BECOME_PASSWORD" ansible-playbook -i inventory.ini site.yml -e ansible_user="$GATEPI_USER"
```

Expected first run behavior:
- Can take a long time on a fresh Pi (source build path)
- Needs outbound package/source downloads during provisioning
- Ends with services enabled and started

## Fast reruns

```sh
set -a
source ansible.env
set +a

# Web UI only
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site.yml --tags web

# App/services only
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site.yml --tags deploy

# Provisioning/build only
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site.yml --tags provision
```

## Optional test workflows
Tests are split from the main deployment playbook.
Load `ansible.env` first as above. The smoketest uses `tests/Test Image.png` by default.

OpenALPR smoketest:

```sh
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site-tests.yml -e run_openalpr_smoketest=true
```

Use a different local test image:

```sh
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site-tests.yml -e run_openalpr_smoketest=true -e openalpr_test_image_path="tests/test.jpeg"
```

Synthetic queue job:

```sh
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site-tests.yml -e run_synthetic_job=true
```

With overrides:

```sh
ansible-playbook -i inventory.ini -e ansible_user="$GATEPI_USER" site-tests.yml -e run_synthetic_job=true -e synthetic_plate=A1ABC -e synthetic_delay=60
```

## Web UI
Default URL:

```text
http://<pi-ip>/
```

Override port with `GATE_WEB_PORT` in `/etc/gate_anpr.env`.

## Stream settings
In `/etc/gate_anpr.env` (or local `files/gate_anpr.env` then redeploy):

```ini
GATE_WEB_STREAM_URL=/static/stream.jpg
GATE_WEB_STREAM_RTSP_URL=rtsp://user:pass@camera-ip:554/h264Preview_01_main
GATE_WEB_STREAM_FPS=12.5
GATE_WEB_STREAM_WIDTH=1280
GATE_WEB_STREAM_HEIGHT=720
```

## Logs

```sh
journalctl -u gate_anpr -f
```

```sh
tail -n100 /var/log/gate-anpr/gate-anpr.log
```

## Known tradeoffs
- OpenALPR is effective but CPU-heavy on high frame rates.
- Recognition quality depends heavily on camera placement, exposure, and plate legibility.
- This stack is LAN-first and not designed as an internet-exposed service.
