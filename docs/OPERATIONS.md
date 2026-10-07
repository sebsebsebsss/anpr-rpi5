# Operations

## Deploying changes

Run commands from the repository root. Reload controller settings after changing
`ansible.env`, including when enabling or disabling HTTPS:

```sh
set -a
source ansible.env
set +a
export ANSIBLE_BECOME_PASS="$ANSIBLE_BECOME_PASSWORD"
```

| Scope | Command |
|---|---|
| Complete installation/update | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml` |
| Web application/assets | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml --tags web` |
| Application and services | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml --tags deploy` |
| OS package updates only | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml --tags os_update` |
| OS packages/OpenALPR configuration | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml --tags provision` |
| Existing nginx configuration only | `ansible-playbook -i inventory.ini -u "$GATEPI_USER" site.yml --tags nginx` |

Complete runs, provisioning runs and `--tags os_update` refresh apt's package
indexes every time and upgrade installed packages, allowing new dependencies
while preserving installed configuration and refusing package removals. Package
updates can restart services; verify the gate services afterwards. The playbook
reports `/var/run/reboot-required` when present and leaves rebooting manual. On
the Pi, also check whether a newer kernel was installed, as this marker is not
always provided. Web and application-only deployments retain their existing scope.

The nginx-only tag validates and reloads nginx without restarting the web
application. It assumes certificate and stream paths are already configured;
use `--tags web` for HTTPS setup or changes to those settings.

Web-only changes leave recognition and the gate worker running. Changes to
recognition configuration can restart `alprd`; its dependent gate worker may
also stop. Use a quiet maintenance window and verify both services afterwards.

## Configuration

Normal deployments preserve `/etc/gate_anpr.env`; the controller's
`files/gate_anpr.env` seeds new installations. To deliberately replace the
remote file, first bring the local file up to date, then add
`-e gate_replace_env=true` to a full or `--tags deploy` run. The existing API
secret is retained if omitted from the replacement.

For preview dimensions/rate, use the narrower `gate_stream_overrides` option
with `--tags web,preview`. It preserves camera credentials and unrelated
settings; see [preview tuning and rollback](PERFORMANCE.md#preview-jpegs).
Masks are provisioned separately and require a recognition maintenance window.

## Services and logs

| Service | Role |
|---|---|
| `alprd` | Recognition daemon |
| `gate_anpr` | Recognition queue consumer and automatic relay commands |
| `gate_anpr_web` | Web API and manual controls |
| `gate_anpr_stream_jpeg` | Independent camera-to-JPEG preview |
| `beanstalkd` | Recognition queue |
| `nginx` | HTTP and optional HTTPS |

On the Pi:

```sh
systemctl status alprd gate_anpr gate_anpr_web gate_anpr_stream_jpeg
journalctl -u gate_anpr -f
journalctl -u alprd -n50
journalctl -u gate_anpr_web -n50
journalctl -u gate_anpr_stream_jpeg -n50
tail -n100 /var/log/gate-anpr/gate-anpr.log
```

Home's FPS counts completed browser image loads over five seconds, including
repeated frames. Expand the pill for the loaded JPEG size, viewport and sampled
producer age. A fresh preview does not prove the independent recognizer is
progressing; service activity does not prove physical gate movement.

Home's Latest seen shows at most two captures. A latest recognised sighting shows
the two latest recognised records; a latest unfamiliar sighting shows that record
alongside the latest recognised record, when one exists. Without recognised
history, it shows the latest two unfamiliar sightings. Records retain their
visible age and have no five-minute expiry. Failed updates preserve the last
received records and display an unavailable-updates warning.

The compact Home metrics show CPU temperature, latest sighting processing time,
latest OCR confidence and system disk space free. Unavailable readings show a
dash. If Home updates stop, live temperature and disk readings become unavailable
while the last sighting's recorded processing time and confidence remain visible.

Recent decision diagnostics are sampled and bounded to 200 records. They explain
matches, suppression and command outcomes, rather than recording every analysed
frame. Use capture history or `/api/decisions` for these diagnostics. Statistics
count stored detections/commands, not confirmed visits or OCR accuracy.

## Backups and restore

Full deployments back up existing application configuration before code changes.
`gate_anpr_backup.timer` also runs daily at 03:05, keeping the latest seven
successful snapshots under `/var/backups/gate-anpr` with root-only permissions.
Snapshots include the allowlist, UI settings, `/etc/gate_anpr.env` and, if present,
ACME configuration/DNS credentials. They do not include a full system image,
OpenALPR configuration or captured photos. Vehicle history is optional.

Set `-e gate_backup_dir=/path/to/mounted/backup/location` to use another
destination, `-e gate_backup_keep=14` to change retention, or
`-e gate_backup_database=true` to include a consistent SQLite history snapshot.
The default destination shares the Pi's disk; it does not cover disk failure.

```sh
sudo systemctl start gate_anpr_backup.service
sudo journalctl -u gate_anpr_backup.service -n20
sudo ls -lt /var/backups/gate-anpr
```

For restore, stop the gate worker and web service first. Each snapshot's
`manifest.json` records the original paths. Restore root-only permissions on
`/etc/gate_anpr.env` and ACME files, and ownership by `pi` on the allowlist and
UI settings. If restoring an optional database, remove the stopped database's
`events.db-wal` and `events.db-shm`, then restore `events.db` owned by `pi`.
Restart the stopped services and check their logs. Keep separate private copies
of any custom OpenALPR configuration and detection mask.
