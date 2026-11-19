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
  - **ZK Unmatched Users** report: one row per unmapped device user, with the name typed on the
    device and a *Map* button.
  - **ZK Request Log**: the raw request and response of every exchange (per-device *Debug
    Logging*, failures always logged, kept 7 days).
  - **ZK Device Command**: the common device commands from the device form (info, upload or
    re-upload punches, reboot; and for System Managers: add / update / delete a user, enroll a
    fingerprint, open the door, clear the log).
  - **ZK Push workspace** with live counts of unmatched, failed and pending punches.
- **ZK Time Card** report: Attendance laid out like the ZKTeco Time Card (Late, Early, OT, Work,
  ATT, Actual ATT, totals), one-punch days flagged, and an optional monthly pay estimate.

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

Step-by-step rollout to a test site, then production, including flashing the ESP32 agent:
[deploy/DEPLOYMENT-WORKBOOK.md](deploy/DEPLOYMENT-WORKBOOK.md).

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
ZK_COMM_KEY=1234 python tools/pull_from_device.py 192.168.1.201 https://erp.example.com --since 2026
```

The Comm Key can also be given as `--comm-key 1234`, but then anyone on the machine can read it in
the process list; the `ZK_COMM_KEY` environment variable keeps it out of there. The same goes for
`backfill_from_device.py` and `export_device_users.py`.

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
(Reboot, Request Device Info, ...) are not delivered. To keep it running on Linux, install pyzk
in a virtual environment the service can read (a `pip install` as root is refused on current
Debian/Ubuntu, and one as your own user is not visible to the service user):

```bash
sudo python3 -m venv /opt/zk_push/venv
sudo /opt/zk_push/venv/bin/pip install --require-hashes -r /opt/zk_push/deploy/pull-agent/requirements.txt
sudo install -m 600 /dev/null /etc/zk-pull.env && echo ZK_COMM_KEY=1234 | sudo tee /etc/zk-pull.env
```

```ini
# /etc/systemd/system/zk-pull.service
[Unit]
Description=ZK Push pull agent
After=network-online.target

[Service]
ExecStart=/opt/zk_push/venv/bin/python /opt/zk_push/tools/pull_from_device.py 192.168.1.201 https://erp.example.com --since 2026 --state /var/lib/zk-pull/state.json
# The Comm Key (ZK_COMM_KEY=...), readable by root only.
EnvironmentFile=/etc/zk-pull.env
Restart=always
User=nobody
# Creates /var/lib/zk-pull, writable by the service user, for the state file.
StateDirectory=zk-pull
WorkingDirectory=/opt/zk_push/tools

[Install]
WantedBy=multi-user.target
```

On Windows, run it with `--once` from Task Scheduler every few minutes. The Docker and Python
packages below bundle pyzk, so they skip the venv step.

## Map employees

Set **Employee → Attendance Device ID** to the person's user ID on the device, or use
**ZK Unmatched Users → Map**, which does the same and processes their waiting punches.

The report shows the **name typed on the device** next to each user ID, from **ZK Device User**
(one row per device user). The pull agents (Python and ESP32) send the device's user list on every
run. A push device only uploads punches (the site asks it for attendance logs only), so for push
devices the names come from `backfill_from_device.py` (it sends the user list too) or the CSV below.

`tools/export_device_users.py` exports a device's users (ID, name, punch count, last punch) to
CSV over the LAN to help with matching:

```bash
pip install pyzk
ZK_COMM_KEY=1234 python tools/export_device_users.py 192.168.1.201 device_users.csv
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

If the device has a Comm Key, set `ZK_COMM_KEY=<n>` (or add `--comm-key <n>`).

Backfilled punches go through the same checks as pushed ones: with *Discard Instead of Skipping*
on, punches older than *Ignore Punches Before* are dropped, not stored.

## ZK Device settings

| Field | Notes |
|---|---|
| Enabled | Punches are only accepted from enabled devices |
| Branch | Copied onto each punch, for reporting / multi-location |
| Ignore Punches Before | Older punches are stored as Skipped (retry them after changing the date) |
| Discard Instead of Skipping | Drop punches before *Ignore Punches Before* as they arrive instead of storing them. The push equivalent of the pull agent's `--since`; dropped punches can only be recovered from the device |
| Checkin Log Type | *From Device Status* (default for new devices) sets IN/OUT from the Check-In/Check-Out key pressed on the device. *Leave Blank* lets the Shift Type alternate IN/OUT. Devices that already exist keep their setting |
| Latitude / Longitude | Only needed if HR Settings → *Allow Geolocation Tracking* is on |
| Allowed IP Addresses | Optional addresses or networks (`203.0.113.7, 10.0.0.0/24`) the device connects from; requests from anywhere else are refused and change nothing. Set it when the device reaches the site over the internet |
| Debug Logging | Record every request in ZK Request Log; turn off once working |

Device actions: *View Punches*, *Unmatched Users*, *Retry Unmatched / Error Punches*,
*Reset Log Stamp + Re-upload*, and the **Device Commands** menu (push devices only: the device picks
them up on its next poll; pull mode cannot receive commands):

| Command | Sends | Who |
|---|---|---|
| Request Device Info | `INFO` | HR Manager |
| Upload New Punches | `LOG` | HR Manager |
| Re-read Options (CHECK) | `CHECK` | HR Manager |
| Re-upload Punches for Dates | `DATA QUERY ATTLOG StartTime=..\tEndTime=..` | HR Manager |
| Reboot Device | `REBOOT` | HR Manager |
| Add / Update User on Device | `DATA UPDATE USERINFO PIN=..\tName=..` (name, admin, card, password) | System Manager |
| Enroll Fingerprint | `ENROLL_FP PIN=..\tFID=..` | System Manager |
| Delete User from Device | `DATA DELETE USERINFO PIN=..` | System Manager |
| Open Door | `AC_UNLOCK` (access-control models) | System Manager |
| Clear Attendance Log | `CLEAR LOG` | System Manager |

The command line is built on the server from checked values (no tabs or line breaks from a form).
Not every firmware supports every command; the result comes back in ZK Device Command.

## Daily attendance

ZK Push is the middleware: it receives punches and creates **Employee Checkins**. HRMS does the
calculation (Attendance, late entry, early exit, working hours). On each **Shift Type**:

| Setting | Value |
|---|---|
| Enable Auto Attendance | On, with *Process Attendance After* set |
| Automatically update Last Sync of Checkin | **On**. HRMS only marks shifts that ended before *Last Sync of Checkin*; punches arrive in real time, so let HRMS move it forward after each shift. Left off, it stays where it was and no new Attendance is made |
| Determine Check-in and Check-out | *Alternating entries* is the safer choice (a wrong key press does not matter). *Strictly based on Log Type* uses the device keys, with Checkin Log Type *From Device Status* |

Then assign employees to the shift (Shift Assignment, or Default Shift on the Employee).

**Punches that arrive late.** HRMS moves *Last Sync of Checkin* forward by the clock, whether or
not the punches are in, and marks everyone without checkins **Absent** once the shift is past. If
punches for that day arrive afterwards (the device or pull agent was offline, a *Re-upload Punches
for Dates*, a backfill), ZK Push still creates the checkins, but HRMS does not change an Attendance
it has already made: it only skips those checkins and leaves a comment, and the day stays Absent
(also on the ZK Time Card). After such a catch-up: cancel the Absent Attendance for those dates,
untick *Skip Auto Attendance* on the affected Employee Checkins, then click **Mark Attendance** on
the Shift Type (or wait for the hourly run).

### ZK Time Card report

Attendance laid out like the ZKTeco software's Time Card, one row per day: day, status, notice,
on / off duty, clock in / out, Late, Early, **OT Time**, Work Time, ATT Time and **Actual ATT**,
with totals. Only the Attendance the user may read is shown and counted.

- **Late / Early**: after on duty / before off duty, beyond the Shift Type's grace periods (used
  when late entry / early exit marking is on), counted from on / off duty: 08:18 with 15 minutes
  grace is 00:18 late. Never more than the shift.
- **OT Time**: time after off duty, counted in full once it reaches *OT Starts After* (default
  60 minutes: leaving at 17:45 gives no OT, at 18:18 gives 01:18; 0 counts every minute).
  Arriving early is not overtime.
- **Work Time**: the part of the shift attended. **ATT Time**: clock in to clock out.
  **Actual ATT** = Work Time + OT Time, the time to pay (minutes inside a grace period are not
  Late / Early, but are not Work either).
- **One punch**: HRMS marks the day Absent. The report shows it **Present ⚠** with a notice, the
  punch taken as clock in or clock out by the time of day, and the missing punch counted as
  *Missing Punch Counts As* minutes (default 60) Late / Early, off Work. Not charged when the day
  has an approved Attendance Request. Two punches marked Absent (short hours), a clock out before
  the clock in, and a worked day without a shift are flagged too.
- **No punches**: a Present / Work From Home day (marked by HR or approved by an Attendance
  Request, e.g. a business trip) counts as the whole shift, a Half Day as half.
- **Half Day with leave**: the leave half is neither Late nor Early; time short of the worked half
  counts as Early. A Half Day without leave (below the shift's half-day threshold) keeps its
  Late / Early, like the device software.
- *Show Durations in Minutes* switches 01:18 to 78.

**Excel Time Card.** The button above the report downloads the same rows as a print-ready workbook:
one sheet per employee with a title block, the columns of the ZKTeco Time Card plus Day, Status,
Actual ATT and Notice, totals, day counts, the pay estimate (one employee only), and signature
lines. It prints landscape on A4, one page wide, with the header row repeated on each page.

**Pay estimate.** With one Employee, a *Monthly Salary* and at most 31 days (one pay month, e.g.
the 17th to the 16th), the totals add a rough estimate of that month's pay:

- GOSI: the employee share (default 9.75%: Saudis registered before 3 July 2024; 10.75% for those
  registered since, rising each July; 0 for non-Saudis) of the *GOSI Wage* (basic + housing,
  default the salary), within 1,500 .. 45,000.
- Absent days and unpaid leave (Leave Without Pay; half for a Half Day) at salary / 30.
- Late and early minutes at a day / *Hours per Day* (default 8), if *Deduct Late / Early* is
  ticked (default).
- OT, if *Pay OT* is ticked: the hourly wage plus 50% of the basic hourly wage (Labor Law,
  Art. 107).

It is not a payroll: work-regulation penalties, loans and other deductions are not in it, days
with no Attendance at all are not deducted, and holidays (work on them is not recorded unless the
Shift Type marks attendance on holidays) and Ramadan hours are not handled.

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
python -m unittest zk_push.tests.test_time_card             # time card / pay rules, no Frappe needed
bench --site <test-site> run-tests --app zk_push            # full suite (ERPNext + HRMS, allow_tests)
```

## Security notes

- Only enabled serial numbers are accepted; auto-registration is capped at 20 pending devices.
- The serial number is printed on the device, so it is not a secret: anyone who knows it could post
  punches. Set **Allowed IP Addresses** on each device that connects over the internet (or allow only
  those addresses for `/iclock` in nginx). Behind a proxy, the proxy must overwrite
  `X-Forwarded-For` (`proxy_set_header X-Forwarded-For $remote_addr;`), otherwise it can be forged.
- Uploads over 8 MB are refused (413) and the device keeps the data.
- ZK Request Log masks device user passwords, card numbers and fingerprint templates. Card numbers
  on ZK Device User are visible only to System Managers and HR Managers.
- Commands that change or delete data on the device, open a door, or are typed by hand require
  System Manager; HR Managers can send INFO / LOG / CHECK / REBOOT and re-upload a date range.
- Over plain HTTP, punch data (device user ID + time) is unencrypted and the serial number could
  be observed and replayed. Prefer HTTPS when the firmware offers it.
- Never expose the device itself (TCP 4370, and the hidden SSH service some models run on 3718)
  to the internet; ZK Push does not need it.

## License

MIT. Copyright (c) 2026 t1nk333r. See [license.txt](license.txt).
