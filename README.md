# gatepi-anpr

All-in-one Raspberry Pi 5 gate controller with number plate recognition.
Provisioned by a single Ansible playbook, it reads an RTSP camera stream, opens your
gate automatically for allowlisted plates, and provides a fast LAN-hosted web UI
for live view, manual control, history, and diagnostics.

![Gatepi ANPR UI](docs/UI.png)

## Highlights
- One-command provisioning for a fresh Raspberry Pi 5 (Bookworm).
- Automatic gate opening for recognised plates with optional fuzzy matching.
- LAN-first web UI designed for low power tablets.
- Manual gate control and cooldown safety.
- Live camera view from RTSP with auto-restart watchdog.
- Candidates/history, timeline, stats, and service logs.
- Built-in data retention: image purge + DB cleanup.

## What it does
- Builds and installs OpenALPR from source when needed.
- Configures OpenALPR for GB plates and runtime paths.
- Installs and runs services:
  - `alprd` (OpenALPR daemon)
  - `gate_anpr` (gate worker + GPIO)
  - `gate_anpr_web` (web UI)
  - `gate_anpr_stream_jpeg` (RTSP -> JPEG stream)
- Sets up logging to `/var/log/gate-anpr/gate-anpr.log`.
- Adds a stream watchdog to auto-restart the JPEG stream when it stalls.
- Purges old plate images and stale events records on a schedule.

## Hardware Requirements
 - A Gate/Garage door controller which allows for a relay to toggle an open event - the pi is an accessory for your main gate controller, not a replacement for it 
 - A Raspberry Pi - This was developed on an RPi5 8GB, but had a previous version running on a Pi4 4GB without issue for a couple of years.  It's not very RAM hungry to may work on a 2GB model too
 - A Relay board - I'm using the now discontinued ModMyPi PiOT Relay Board, but there's nothing special about this board and any modern Relay board should do - you will have to adjust the pin to suit your setup
 - An IP camera on your network exposing an RTSP stream - after extensive testing I settled on a Annke CZ804, but have had success with Foscams before too.  Note a bullet camera with IR sensors and a configurable shutter speed and focus work best.

## Software Requirements
- Ansible on your local machine.
- SSH access to the Pi (I used default user `pi`).
- The Pi reachable on your LAN.

## Future to do list
- Get away from the legacy OpenALPR as its chewing through CPU
  - a more modern architecture would allow for hardware offloading or using a TPU
- Facial Recognition?


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
PLATE_ALLOWLIST_PATH=/opt/gate_anpr/allowlist.json
# Optional inline fallback (used only if PLATE_ALLOWLIST_PATH is empty)
PLATE_ALLOWLIST_JSON=[["A1ABC","Test Car"]]
GATE_PIN_BOARD=23
GATE_PIN_BCM=11
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
ALPRD_STREAM=rtsp://user:pass@camera-ip:554/h264Preview_01_main
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

### Fast deploys (no provisioning)
Use tags to avoid long runs when you only want app/web updates:

```sh
# Web UI only
ansible-playbook -i inventory.ini -u pi site.yml --tags web

# App/services only (no provisioning/build)
ansible-playbook -i inventory.ini -u pi site.yml --tags deploy

# Provision/build only
ansible-playbook -i inventory.ini -u pi site.yml --tags provision
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

## Web UI
A fast SPA is served from the Pi at port 80 by default:

```
http://<pi-ip>/
```

It lets you edit the allowlist, view recent events, browse captures, check logs,
and use a tablet-optimised homepage that keeps live view, gate control, and latest
recognitions on one screen (tuned for iPad mini).

You can override the port with `GATE_WEB_PORT` in `/etc/gate_anpr.env`.


## Hostname alias
The playbook can publish an mDNS alias so you can reach the Pi at `gate.local`.
Override with `GATEPI_ALIAS_HOSTNAME` (default `gate`) in your local env.

## Live stream (web UI tab)
The web UI shows the RTSP camera stream via a JPEG frame update (ffmpeg writing
`/static/stream.jpg`).

Configure in `/etc/gate_anpr.env` (or `files/gate_anpr.env` + re-run the playbook):

```
GATE_WEB_STREAM_URL=/static/stream.jpg
GATE_WEB_STREAM_RTSP_URL=rtsp://user:pass@camera-ip:554/h264Preview_01_main
GATE_WEB_STREAM_FPS=12.5
GATE_WEB_STREAM_WIDTH=1280
GATE_WEB_STREAM_HEIGHT=720
```


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
 - Tests are manual and rely on the local `tests/` assets.
