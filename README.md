# ZK Push

Receive attendance from ZKTeco devices **directly into ERPNext / HRMS** using the device's
built-in ADMS ("Cloud Server") push protocol.

The device opens the connection to ERPNext, so **no ports are opened at the office**, and no
PC, Raspberry Pi, ZKBio Time or Windows server is needed. Works for any number of devices and
branches.

```
[ZKTeco device] --outbound HTTP(S)--> [ERPNext /iclock/*] --> ZK Punch --> Employee Checkin
```

Tested on Frappe 15.126 / ERPNext 15.93 / HRMS 15.54 with a ZKTeco MB20-VL
(ZMM510_TFT platform, firmware Ver 6.60).

## Features

- **Zero-config endpoint**: `/iclock/cdata`, `/iclock/getrequest`, `/iclock/devicecmd` are served
  by the app itself; no nginx rewrite.
- **Safe onboarding**: unknown devices register themselves as *disabled*; nothing is accepted
  until someone ticks **Enabled**. *Ignore Punches Before* keeps years of device history out.
- **No duplicates**: each punch has a deterministic name (`<serial>-<user>-<timestamp>`), so
  retries and full-history re-uploads are ignored; existing checkins are detected.
- **Fast**: a full history upload (12,000+ punches) is accepted in one request and turned into
  checkins by a background job in about a minute.
- **Nothing gets lost**: punches from users not mapped to an employee wait as *Unmatched* and are
  processed automatically as soon as the employee's Attendance Device ID is set.
- **Debugging UI**
  - Device form: Online / Offline, last seen, counts per status, last punch, unmapped users,
    failed punches.
  - Punch form: plain-language explanation of the status, key pressed, verification method,
    *Retry* and *Map to Employee* buttons.
  - **ZK Unmatched Users** report: one row per unmapped device user with a *Map* button.
  - **ZK Request Log**: the raw request and response of every exchange (per-device *Debug
    Logging*, failures always logged, kept 7 days).
  - **ZK Device Command**: send `INFO`, `CHECK` (re-upload) or `REBOOT` to the device remotely.
  - **ZK Push workspace** with live counts of unmatched, failed and pending punches.

## How it works

| Device request | What ZK Push does |
|---|---|
| `GET /iclock/cdata` (handshake) | Replies with push options (real-time attendance logs only) |
| `POST /iclock/cdata?table=ATTLOG` | Stores each line as a **ZK Punch** (Pending), replies `OK: n` |
| `GET /iclock/getrequest` | Updates device status; hands out the next queued command |
| `POST /iclock/devicecmd` | Stores the command result |
| background job | Converts Pending punches into **Employee Checkins** |

Punches are matched on the Employee field **Attendance Device ID (Biometric/RF tag ID)**, not
on the ERPNext Employee ID, so device user IDs and ERPNext IDs can be completely different.

| Punch status | Meaning |
|---|---|
| Pending | Received, waiting for the background job |
| Created | Employee Checkin created |
| Duplicate | A checkin for that employee at that exact time already existed |
| Unmatched | No employee has this Attendance Device ID (yet) |
| Skipped | Older than the device's *Ignore Punches Before* |
| Error | HRMS rejected the checkin; the reason is shown on the punch |

## Install

### Bench

```bash
cd ~/frappe-bench
bench get-app https://github.com/t1nk333r/zk_push
bench --site <your-site> install-app zk_push
```

Then restart the web and worker processes so they load the new app (`bench restart`, or
`sudo supervisorctl restart all`, or the `frappe-bench-*` systemd units). A graceful `HUP`
reload is **not** enough the first time: bench runs gunicorn with `--preload`, so a newly
installed app is only importable after a real restart.

On a bench with several sites, the app is only active on sites where you run `install-app`;
always pass `--site`. The restart is shared, so the other sites are unavailable for a few
seconds.

Update later with `bench update --apps zk_push` or `cd apps/zk_push && git pull`, then
`bench --site <your-site> migrate`.

### frappe_docker

Add `{"url": "https://github.com/t1nk333r/zk_push", "branch": "main"}` to `apps.json` and
rebuild your image, or layer the app onto an existing image:

```bash
git clone https://github.com/t1nk333r/zk_push && cd zk_push
docker build -f deploy/Containerfile --build-arg BASE_IMAGE=my-erpnext:v15 -t my-erpnext:v15-zkpush .
```

Use the new image for every service, `docker compose up -d`, then
`docker compose exec backend bench --site <your-site> install-app zk_push`.

## Reverse proxy: let `/iclock/` through on plain HTTP

Many ZKTeco firmwares (including the MB20-VL's) **can only push over plain HTTP and do not
follow redirects**. If your site redirects HTTP → HTTPS, add an exception for `/iclock/`.

Bench-generated nginx (`/etc/nginx/conf.d/frappe-bench.conf`), port-80 block of your site:

```nginx
server {
	listen 80;
	server_name erp.example.com;

	location /iclock/ {
		proxy_pass http://frappe-bench-frappe;      # upstream defined at the top of the file
		proxy_set_header Host $host;
		proxy_set_header X-Frappe-Site-Name erp.example.com;
		proxy_set_header X-Forwarded-For $remote_addr;
		proxy_set_header X-Forwarded-Proto $scheme;
		proxy_read_timeout 120s;
		client_max_body_size 20m;
	}

	location / {
		return 301 https://$host$request_uri;
	}
}
```

If one port-80 block lists several sites, give this site its own block so the others keep
their plain redirect. `bench setup nginx` / `bench setup lets-encrypt` regenerate this file:
re-apply the change afterwards.

Caddy:

```caddy
http://erp.example.com {
	handle /iclock/* {
		reverse_proxy 127.0.0.1:8000
	}
	handle {
		redir https://{host}{uri} permanent
	}
}
```

If the device supports HTTPS, use port 443 and skip the exception.

Check from anywhere:

```bash
curl -i "http://erp.example.com/iclock/getrequest?SN=TEST0000001"   # 403 ERROR: device not enabled
python tools/simulate_device.py http://erp.example.com --sn TEST0000001
```

then delete the `TEST0000001` ZK Device.

## Configure the device

On the terminal: **Menu → COMM. → Cloud Server Setting**

| Setting | Value |
|---|---|
| Server Mode | ADMS |
| Enable Domain Name | ON |
| Server Address | `erp.example.com` |
| Server Port | `80` (or `443` with HTTPS) |
| HTTPS | ON if available |
| Enable Proxy Server | OFF |

Make sure **COMM. → Ethernet** has a DNS server. Within a minute the device appears under
**ZK Device** as *New - Not Enabled*. Open it and, **before** ticking Enabled, set
*Ignore Punches Before* (the device uploads its whole history on first connect). Then set a
name and branch and tick **Enabled**.

Finally set a **Comm Key** on the device (COMM. → PC Connection): push mode does not use it,
and it stops anyone on the LAN from reading or wiping the device over TCP 4370.

### Devices without Cloud Server Setting (pull mode)

Some models and firmwares cannot push (COMM. has no *Cloud Server Setting* / ADMS). For these,
run `tools/pull_from_device.py` on any always-on machine on the device's LAN (PC, Raspberry Pi,
NAS). Every 2 minutes it reads the log over TCP 4370 (read-only) and posts new punches to
`/iclock/cdata` exactly as a push device would, so everything else in this README applies.
The machine needs outbound access to the site; nothing has to reach the device from outside.

```bash
pip install pyzk
python tools/pull_from_device.py 192.168.1.201 https://erp.example.com --comm-key 1234 --since 2026
```

`--since` (a year, month or day: `2026`, `2026-03`, `2026-03-15`) means punches before it are never
sent, on any run; leave it out to send the device's whole log once.

Ready-made packages: `make -C deploy/pull-agent pkg` builds `dist/zk-pull-agent-<version>-amd64.tar.gz`
(PC/server), `-arm64.tar.gz` (Raspberry Pi) — Docker image + `compose.yaml` + `.env.example` — and
`-python.zip` (no Docker, e.g. Windows). Each contains `INSTALL.md`; copy one to the office machine.

On its first run the device appears under **ZK Device** as *New - Not Enabled* (the script
logs HTTP 403 until then). Set *Ignore Punches Before*, tick **Enabled**, and the next run
sends the history. The last sent punch time is kept in `tools/zk_pull_<ip>.json`.

Differences from push: punches arrive within `--interval` seconds (default 120) instead of
instantly, the device shows as online only while the script runs, and device commands
(Reboot, Request Device Info, ...) are not delivered. To keep it running on Linux:

```ini
# /etc/systemd/system/zk-pull.service
[Unit]
Description=ZK Push pull agent
After=network-online.target

[Service]
ExecStart=/usr/bin/python3 /opt/zk_push/tools/pull_from_device.py 192.168.1.201 https://erp.example.com --comm-key 1234 --since 2026
Restart=always
User=nobody
WorkingDirectory=/opt/zk_push/tools

[Install]
WantedBy=multi-user.target
```

(`User=nobody` needs write access to the state file: pass `--state /var/lib/zk-pull/state.json`
and create that directory for it.) On Windows, run it with `--once` from Task Scheduler every
few minutes.

## Map employees

Set **Employee → Attendance Device ID** to the person's user ID on the device, or use
**ZK Unmatched Users → Map**, which does the same and processes their waiting punches.

`tools/export_device_users.py` exports a device's users (ID, name, punch count, last punch) to
CSV over the LAN to help with matching:

```bash
pip install pyzk
python tools/export_device_users.py 192.168.1.201 device_users.csv
```

Device user IDs must be **unique across all devices** (ERPNext has one Attendance Device ID per
employee). For a new branch, enrol people in a separate range, e.g. 2001+.

## Backfill history from the LAN

Normally the device uploads its own history on first connect. To copy a device's log into a
site without using its push connection (e.g. a staging site, or a device that is not yet
pointed at the server), `tools/backfill_from_device.py` reads the log over the LAN (read-only)
and posts it to `/iclock/cdata` exactly as the device would. Already received punches are
ignored, so it is safe to re-run. The ZK Device must exist and be enabled on the site.

```bash
pip install pyzk
python tools/backfill_from_device.py 192.168.1.201 https://erp.example.com --dry-run
python tools/backfill_from_device.py 192.168.1.201 https://erp.example.com --since 2026
```

If the device has a Comm Key, add `--comm-key <n>`.

## ZK Device settings

| Field | Notes |
|---|---|
| Enabled | Punches are only accepted from enabled devices |
| Branch | Copied onto each punch, for reporting / multi-location |
| Ignore Punches Before | Older punches are stored as Skipped (retry them after changing the date) |
| Discard Instead of Skipping | Drop punches before *Ignore Punches Before* as they arrive instead of storing them. The push equivalent of the pull agent's `--since`; dropped punches can only be recovered from the device |
| Checkin Log Type | *Leave Blank* (default) lets the Shift Type alternate IN/OUT. *From Device Status* uses the Check-In/Check-Out key pressed on the device |
| Latitude / Longitude | Only needed if HR Settings → *Allow Geolocation Tracking* is on |
| Debug Logging | Record every request in ZK Request Log; turn off once working |

Device actions: *View Punches*, *Unmatched Users*, *Retry Unmatched / Error Punches*,
*Reset Log Stamp + Re-upload*, and device commands *Request Device Info*,
*Re-read Options (CHECK)*, *Reboot Device*.

## Daily attendance

ZK Push creates **Employee Checkins**. To turn them into **Attendance** records, enable
*Auto Attendance* on a **Shift Type** and assign employees to it (standard HRMS).

## Troubleshooting

| Symptom | Check |
|---|---|
| No ZK Device appears | Device DNS / gateway; the nginx `/iclock/` exception; `curl` test above |
| Device stays *Offline* | Device network; turn on *Debug Logging* and look at ZK Request Log |
| Punches stay *Pending* | Background workers running? |
| Punches *Unmatched* | Attendance Device ID missing; use ZK Unmatched Users → Map |
| Punches *Error* | Open the punch: inactive employee, shift *Strictly based on Log Type* with blank log type, or geolocation tracking without device coordinates. Fix, then Retry |

## Development

```bash
python -m unittest zk_push.tests.test_protocol              # protocol tests, no Frappe needed
bench --site <test-site> run-tests --app zk_push            # full suite (ERPNext + HRMS, allow_tests)
```

## Security notes

- Only enabled serial numbers are accepted; auto-registration is capped at 20 pending devices.
- Custom device commands require System Manager; HR Managers can send INFO / CHECK / REBOOT.
- Over plain HTTP, punch data (device user ID + time) is unencrypted and the serial number could
  be observed and replayed. Prefer HTTPS when the firmware offers it.
- Never expose the device itself (TCP 4370, and the hidden SSH service some models run on 3718)
  to the internet; ZK Push does not need it.

## License

MIT
