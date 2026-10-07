# Setup

Run controller commands from the repository root. The [README quickstart](../README.md#quickstart)
creates the local configuration and runs the complete playbook.

## Prepare the Pi and camera

Install Raspberry Pi OS Lite 64-bit based on Debian Trixie, enable SSH and assign
an address you can reach reliably, usually a fixed DHCP lease. The playbooks
install application files and services for user `pi`, so retain that account.
Confirm SSH and sudo work, and put the sudo password in the local `ansible.env`.

Check the camera's RTSP URL with a player such as VLC before provisioning.
Aim for a clear, close plate view, with manual shutter/exposure and sufficient
night-time illumination. Initial provisioning needs internet access for packages
and, when OpenALPR is not already installed, its source build.

## Wiring

The relay supplies a momentary contact to the existing gate controller:

```text
Pi GPIO: physical BOARD pin 23 / BCM pin 11 → relay IN
Relay COM → gate controller trigger terminal
Relay NO  → gate controller trigger terminal
```

The default pulse drives the output **HIGH for 500 ms**, then returns it LOW.
This suits an active-HIGH relay with normally open contacts. For an active-LOW
relay, change `RELAY_ON` / `RELAY_OFF` in
[gate_runtime.py](../files/gate_runtime.py) to `0` / `1` respectively.

| Application setting | Default | Meaning |
|---|---|---|
| `GATE_GPIO_BACKEND` | `lgpio` | Pi 5 GPIO backend; `rpi_gpio` selects the older backend |
| `GATE_GPIO_CHIP` | `0` | gpiochip used by lgpio |
| `GATE_PIN_BCM` | `11` | lgpio pin numbering |
| `GATE_PIN_BOARD` | `23` | Physical pin numbering for RPi.GPIO |

Manual and automatic commands share a process lock at
`/opt/gate_anpr/gate_gpio.lock`. `GATE_GPIO_LOCK_PATH` changes that path and
`GATE_GPIO_MIN_INTERVAL_SECONDS` changes the minimum pulse gap (default 1 second).

## Controller configuration

Copy [ansible.env.example](../ansible.env.example) to the ignored `ansible.env`.
Required values are:

```sh
GATEPI_USER=pi
ANSIBLE_BECOME_PASSWORD='your-sudo-password'
ALPRD_STREAM='rtsp://user:pass@camera-ip:554/h264Preview_01_main'
```

This file is sourced by your shell: quote values containing spaces or shell
characters. `ALPRD_STREAM` is the recognition feed. Preview can use an independent
camera substream, configured below.

`GATEPI_ALIAS_HOSTNAME` sets the optional Avahi alias (default `gate`).
`ALPRD_CPU_AFFINITY` can restrict the recognizer to selected CPU cores.
`OPENALPR_SHA` defaults to `HEAD`; pin a source commit when a repeatable build
is required. The provisioner reuses an existing usable OpenALPR installation.
Detection masks are optional; see [performance settings](PERFORMANCE.md#detection-mask).

Copy [inventory.ini.example](../inventory.ini.example) to `inventory.ini` and
replace the placeholder with the Pi's LAN address:

```ini
[gatepi]
192.168.x.x
```

Custom host groups must be children of `gatepi`.

## Application configuration

Copy [gate_anpr.env.example](../files/gate_anpr.env.example) to
`files/gate_anpr.env`. This seeds `/etc/gate_anpr.env` on a new installation.
Existing Pi settings are preserved on redeploy; see
[configuration updates](OPERATIONS.md#configuration).

The example includes GPIO and preview settings. Additional commonly used settings:

| Setting | Purpose |
|---|---|
| `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN` | Optional notifications; remove both example placeholders when unused |
| `PLATE_ALLOWLIST_PATH` | Allowlist path, normally `/opt/gate_anpr/allowlist.json` |
| `MATCH_DEDUP_SECONDS` | Automatic vehicle-match cooldown, default `60` |
| `FUZZY_ALLOWLIST` | Enable fuzzy plate matching, default `1` |
| `FUZZY_MAX_DISTANCE`, `FUZZY_MIN_CONFIDENCE` | Fuzzy-match limits, defaults `1` and `75` |
| `GATE_WEB_MANUAL_OPEN_MAX_AGE_SECONDS` | Reject delayed manual requests; default `15` seconds |
| `GATE_ALLOWED_ORIGINS` | Extra mutating-API origins for unusual proxy setups; normally empty |
| `GATE_UI_LAT`, `GATE_UI_LON` | Optional sunrise/sunset coordinates; otherwise uses a timezone-based approximation |

The playbook generates `GATE_API_SHARED_SECRET` on first deployment and keeps
it in the root-readable Pi environment file. No manual secret is required.
Setting it explicitly in a new installation's env file pins that value.

## Allowlist

Copy [plate_allowlist.json.example](../files/plate_allowlist.json.example) to
`files/plate_allowlist.json` and replace the example with your plates:

```json
[
  ["A1ABC", "Example car"]
]
```

Once deployed, the web UI can edit the allowlist. Presentation spaces are
accepted; saves reject plates that collide after the worker's OCR character
normalisation. Failed saves keep the unsaved edits visible.

## Web access

nginx serves HTTP on port 80 and proxies API requests to the local web process
on `127.0.0.1:8080`. It accepts the Pi's configured hostname, Avahi alias, LAN IP
and localhost; unknown Host values are dropped to prevent DNS rebinding.
If using another hostname, add it to `server_name` in
[gate-anpr.conf](../files/nginx/gate-anpr.conf).

To change the HTTP port, edit both HTTP `listen` directives in that template
(the rejecting default server and the application server), set `GATE_WEB_PORT`
to the same port for deployment checks, and redeploy. HTTPS is a separate,
[optional setup](HTTPS.md) and is disabled by default.
