# Optional HTTPS

HTTP by IP address, `.local` name and the dedicated screen pages stays
available. HTTPS is disabled by default and only serves a configured DNS hostname.
Make that hostname resolve to the Pi on your LAN. DNS-01 certificate validation
uses public DNS records and does not require exposing the Pi or forwarding ports.

There are two certificate modes. Manual mode is the default: provide an existing
trusted full chain and root-owned private key (mode `0600`), then set these
controller settings in the gitignored `ansible.env`:

```ini
GATE_HTTPS_ENABLED=true
GATE_HTTPS_ACME_ENABLED=false
GATE_HTTPS_DOMAIN=gate.example.com
GATE_HTTPS_CERT_PATH=/etc/gate-anpr/tls/fullchain.pem
GATE_HTTPS_KEY_PATH=/etc/gate-anpr/tls/privkey.pem
```

For automatic issuance and renewal, use a dedicated DuckDNS subdomain for the
DNS challenge. The main domain can stay with its existing provider; its account
does not need an API. Configure the following once:

1. Create a dedicated DuckDNS name, for example `my-gate-acme.duckdns.org`.
2. At the main domain's DNS provider, add this permanent CNAME record:

   ```text
   _acme-challenge.gate.example.com. CNAME _acme-challenge.my-gate-acme.duckdns.org.
   ```

   Keep the CNAME for future renewals. This record is separate from the LAN DNS
   record pointing the website hostname at the Pi.
3. Create the gitignored `files/gate_https_dns.env` with this single line and
   protect the local file with `chmod 600 files/gate_https_dns.env`:

   ```ini
   DuckDNS_Token=your-duckdns-token
   ```
4. Enable both flags and set the dedicated alias in `ansible.env`:

   ```ini
   GATE_HTTPS_ENABLED=true
   GATE_HTTPS_ACME_ENABLED=true
   GATE_HTTPS_DOMAIN=gate.example.com
   GATE_HTTPS_CHALLENGE_ALIAS=my-gate-acme.duckdns.org
   ```

Reload `ansible.env` and deploy with `--tags web` once the CNAME resolves
publicly; see [deployment commands](OPERATIONS.md#deploying-changes). Automatic mode installs a checksum-pinned acme.sh release and
uses Let's Encrypt DNS-01. Tokens are copied privately to the Pi; existing remote
credentials can be reused when the local file is absent. Credentials and ACME
state are root-only, and client self-updates are disabled. DuckDNS has one shared
TXT value per subdomain, so use a dedicated alias rather than sharing it with
other certificate jobs.

Automatic mode manages `/etc/gate-anpr/tls/current/fullchain.pem` and
`/etc/gate-anpr/tls/current/privkey.pem`; manual certificate path settings are
ignored in this mode. Initial issuance runs before enabling nginx HTTPS. The
`gate_https_acme.timer` checks daily with up to one hour of random delay, and
catches up after downtime. New certificates are validated before activation and
a checked nginx reload. Failed validation or activation preserves the previous
valid certificate and key pair.
Changing the challenge alias later requires coordinating the public CNAME and
ACME account configuration; it is not just a website setting.

For iOS 12 screens, use a complete chain compatible with ISRG Root X1;
native ISRG Root X2 trust requires iOS 16 or later. Automatic mode selects and
validates an X1-compatible chain. Manual certificates are checked against the
Pi's CA store, hostname, key match and at least one day of remaining validity.
Missing, self-signed or untrusted certificates are rejected; there is no
self-signed fallback. The nginx candidate is tested against the complete nginx
configuration before reload, with previous files and links restored if validation
fails.

Visit `https://gate.example.com/` for HTTPS. With HTTPS enabled, opening the Pi's
bare-hostname homepage (for example `http://gatepi/` or `http://gatepi/index.html`)
redirects to the configured HTTPS domain, preserving the query and browser
fragment. Only GET/HEAD homepage requests redirect: HTTP API calls, image streams,
dedicated screen pages, IP addresses and `.local` URLs keep working directly.
The temporary redirect is not cached, and no HSTS is set. Publicly trusted
certificates cannot cover bare internal hostnames, so `https://gatepi/` cannot
redirect before TLS validation; start with `http://gatepi/` or the full HTTPS URL.
Unknown TLS hostnames and HTTP Host values are rejected.

For nginx-only changes on an already provisioned Pi, run the playbook with
`--tags nginx`; this validates and reloads nginx without restarting the app.
This shortcut assumes the current certificate and stream path are already
configured; use `--tags web` for HTTPS setup, certificate or stream-setting changes.

To disable HTTPS, set `GATE_HTTPS_ENABLED=false` and deploy again. Certificate and
credential checks are skipped, the automatic renewal timer is stopped and
disabled to prevent future checks, and the TLS listener is removed. A renewal
already in progress can finish without enabling the listener. HTTP keeps working.

Setting `GATE_HTTPS_ACME_ENABLED=false` alone selects manual certificate mode and stops
its timer. To retain the last managed certificate in manual mode, explicitly
set the certificate paths to the two `/etc/gate-anpr/tls/current/` files above.

Inspect automatic renewal with:

```sh
sudo systemctl status gate_https_acme.timer
sudo journalctl -u gate_https_acme.service -n30
```

For externally managed certificates in manual mode, validate and deploy after
renewal, then run `sudo nginx -t && sudo systemctl reload nginx` even when the
certificate path has not changed, or provide an equivalent checked renewal hook.
