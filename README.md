# gatepi-anpr

Raspberry Pi 5 gate automation with number plate recognition and a local web UI.
An RTSP camera supplies the images; OpenALPR recognises plates and a GPIO relay
triggers your existing gate controller for allowlisted vehicles.

![Gatepi ANPR UI](docs/UI.png)

## Features

- Automatic opening for trusted vehicles, manual control and editable allowlist.
- Live camera, recent arrivals, filtered history, activity statistics and logs.
- Large touch controls and single-screen layouts for a wall iPad and Pi touchscreen.
- JPEG previews in RAM, optional smaller images for older screens, and recovery
  from interrupted camera connections.
- Configuration backups and automatic cleanup of old captures and history.

Everything works over ordinary LAN HTTP. Trusted HTTPS with automatic renewal is
optional and disabled by default; no domain or DNS account is needed for HTTP.
This is a local gate accessory, intended to use your existing gate controller.

## Requirements

- Raspberry Pi 5 running Raspberry Pi OS Lite 64-bit / Debian Trixie.
- Relay board connected to the gate controller's momentary trigger input.
- IP camera with a stable RTSP stream and a clear view of number plates.
- Ansible on your computer, SSH/sudo access to the Pi and internet access during
  initial provisioning.

The tested installation uses an 8GB Pi 5. Camera placement, shutter speed and
night-time illumination matter more to recognition than the camera model.
See [setup and wiring](docs/SETUP.md) before connecting the relay.

## Quickstart

Start with an accessible Pi and a working camera RTSP URL. For a new installation,
copy the example files; `-n` preserves any existing local configuration:

```sh
cp -n ansible.env.example ansible.env
cp -n inventory.ini.example inventory.ini
cp -n files/gate_anpr.env.example files/gate_anpr.env
cp -n files/plate_allowlist.json.example files/plate_allowlist.json
```

Edit the copies:

| File | Set |
|---|---|
| `inventory.ini` | Pi's LAN address in the `gatepi` group |
| `ansible.env` | SSH user, sudo password and `ALPRD_STREAM` camera URL |
| `files/gate_anpr.env` | GPIO/preview settings; remove Pushover placeholders if unused |
| `files/plate_allowlist.json` | Allowed plates and display names |

Keep these local files private; they are ignored by Git. The API secret is
created automatically on the Pi. Detailed options are in the
[setup guide](docs/SETUP.md).

From the repository root, load the controller settings and deploy:

```sh
set -a
source ansible.env
set +a
export ANSIBLE_BECOME_PASS="$ANSIBLE_BECOME_PASSWORD"
ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml
```

The first run may take a while to build OpenALPR. Subsequent deployments preserve
the Pi's environment file; see [updating configuration](docs/OPERATIONS.md#configuration)
for deliberate replacements and preview-only tuning.

## Screens

| URL | Use |
|---|---|
| `http://<pi-ip>/` | Home, optimised for a landscape wall iPad |
| `http://<pi-ip>/fullscreen` | Dedicated Pi touchscreen with no navigation |
| `http://<pi-ip>/#stream` | Larger live camera view |

The two wall layouts prioritise an uncropped camera and a large gate button,
without scrolling. Phone styling adapts separately. The Home FPS pill measures
completed image loads; its details distinguish a stalled display from a stale
camera preview. An active service or successful relay command does not confirm
recognition progress or physical gate position.

## Documentation

- [Setup](docs/SETUP.md): wiring, camera and local configuration.
- [Operations](docs/OPERATIONS.md): deployments, backups, logs and diagnostics.
- [Optional HTTPS](docs/HTTPS.md): trusted certificates, DuckDNS validation and renewal.
- [Performance](docs/PERFORMANCE.md): JPEG profiles, camera resolution and detection masks.
- [Development](CONTRIBUTING.md): tests, safe local UI preview and contribution checks.

## Future ideas

- Move beyond legacy OpenALPR towards more efficient recognition and hardware offload.
- Improve restart/outage recovery and monitoring of actual recognition progress.
- Add useful household controls such as a timed automatic-opening pause or guest access.

These are possible next steps, not requirements for a working installation.
Track specific agreed work in GitHub issues; completed changes belong in Git history.
