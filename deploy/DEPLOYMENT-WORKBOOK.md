# Deployment workbook: zk_push v0.3.7 and the ESP32 pull agent

Order: **test site first, production only after the test has run cleanly for a day.**

| | Test | Production |
|---|---|---|
| Site | `https://test.nss.sa` | `https://nss.sa` |
| zk_push today | installed (older version) | **not installed** |
| What to do | update to v0.3.7, migrate | install v0.3.7 |

Every step is done by you; nothing here runs automatically. Tick each box as you go.

What v0.3.6 and v0.3.7 add on the site:
- upgrade agent: installs only releases signed with the zk_push release key (the agent checks the
  signature, and a release it cannot verify is refused); a failed migrate restores the database of
  every site it migrated, not only the code; an approved Attendance Request clears the One Punch flag
  on every day it covers;
- the agent must be installed by hand once more: copy `deploy/upgrade-agent/release-signers` to
  `~/zk-push-release-signers` on the server and the 0.3.7 agent over the old one (see
  `deploy/upgrade-agent/README.md`);
- in v0.3.5: upgrade agent hardening (only the sites in `ZK_SITES` may start an upgrade, no release
  older than v0.3.5, a moved release tag is refused, an interrupted upgrade no longer blocks the page),
  release notes shown as text, an amended One Punch day is not flagged again, a new install flags the
  one-punch days already marked;
- in v0.3.4: **ZK Push Update**, upgrade from the site (System Managers), once an administrator has
  installed the upgrade agent (`deploy/upgrade-agent/README.md`);
- in v0.3.3: **One Punch** on Attendance: a day with only a clock in or only a clock out is
  flagged, with a comment, when HRMS marks it, so HR can filter the Attendance list on it and check
  each day (an approved Attendance Request clears it); days already marked are flagged by the migrate;
- in v0.3.2: ZK Time Card figures for shifts that start or end at midnight; Half Day without
  a shift, split shifts and Partially Paid Leave deducted correctly; safer Excel export (at most
  300 employees per file); device commands expire if the device is offline (door / clear log
  after 10 minutes, others after a day); HR User no longer reads ZK Device User; device passwords
  in commands only for System Managers; new indexes; the 16-pass audit fixes;
- in v0.3.1: checkins get **IN/OUT** from the key pressed on the device (new devices default to
  *From Device Status*); the **Excel Time Card** button on ZK Time Card (one print-ready sheet per
  employee); **Allowed IP Addresses** on ZK Device; uploads over 8 MB refused; passwords, card
  numbers and fingerprint data masked in ZK Request Log; card numbers only for System / HR
  Managers; the audit fixes; step **B6** so HRMS keeps making Attendance;
- in v0.3.0: the **ZK Time Card** report (Attendance like the ZKTeco Time Card, with Late,
  Early, OT, Actual ATT, one-punch days flagged and an optional monthly pay estimate), linked from
  the ZK Push workspace; the common device commands in the ZK Device form's **Device Commands**
  menu (upload / re-upload punches for dates, and for System Managers: users, fingerprints, open
  door, clear log; push devices only);
- from v0.2.0: the **ZK Device User** doctype (names typed on the device, shown in **ZK Unmatched
  Users** and its *Map* dialog), an index on ZK Device Command, and the audit fixes.

A `bench migrate` is required (permission changes and new indexes; from v0.3.0 also a new ZK
Device field).

**One bench for both sites.** If test.nss.sa and nss.sa run on the same bench, they share
`apps/zk_push`: updating it changes the code for both, and a restart restarts both. `migrate` is
per site. While zk_push is not installed on nss.sa, nss.sa never loads it, so Part B does not
touch production beyond the few seconds of the restart (B3).

---

## Part A: once, on the laptop used for flashing

- [ ] **A1. Serial port access.** Your user must be in the `uucp` group (Arch / Omarchy) to flash:
  ```bash
  sudo usermod -aG uucp $USER
  ```
  Log out and back in. Check with `groups`: it must list `uucp`.
- [ ] **A2. PlatformIO.** `pio --version` should print `PlatformIO Core, version 6.2.0`. If not:
  ```bash
  uv tool install platformio==6.2.0
  ```
- [ ] **A3. Code at the release.**
  ```bash
  cd ~/Projects/zk_push
  git fetch --tags && git checkout v0.3.7
  ```
- [ ] **A4. `config.h` exists.** `deploy/esp32/firmware/include/config.h` holds the Wi-Fi password
  and is git-ignored (never commit it). If it is missing:
  ```bash
  cp deploy/esp32/firmware/include/config.example.h deploy/esp32/firmware/include/config.h
  ```
  and fill in `WIFI_SSID`, `WIFI_PASSWORD`, `DEVICE_HOST`, `DEVICE_COMM_KEY`. These end up in the
  board's flash in plain text (anyone with the board and a USB cable can read them), so give it its
  own or a guest Wi-Fi network and mount it where people cannot reach it.
- [ ] **A5. Root certificates.** Both sites use Let's Encrypt (chain `YE1 ← Root YE ← ISRG Root X2`).
  `SITE_ROOT_CA` in `config.h` must contain **ISRG Root X1, ISRG Root X2, Root YE and Root YR**
  (four `BEGIN CERTIFICATE` blocks):
  ```bash
  grep -c "BEGIN CERTIFICATE" deploy/esp32/firmware/include/config.h    # expect 4
  ```
  If not: X1 and X2 are in `/etc/ca-certificates/extracted/cadir/`, Root YE and YR at
  https://letsencrypt.org/certificates/ (`root-ye.pem`, `root-yr.pem`). The same four work for
  both sites.

---

## Part B: update zk_push on test.nss.sa

Use the commands for the way the site is hosted. In the Docker commands, run them in the
directory of the site's `compose.yaml`.

- [ ] **B1. Backup.**
  - Bench: `bench --site test.nss.sa backup --with-files`
  - Docker: `docker compose exec backend bench --site test.nss.sa backup --with-files`
- [ ] **B2. Get v0.3.7.**
  - Bench:
    ```bash
    cd ~/frappe-bench/apps/zk_push && git fetch --tags && git checkout v0.3.7 && cd ../..
    ```
    (Works from an older tag too; `git pull` does not, as a checked-out tag is not a branch.)
  - Docker (layer the app onto the image the site uses now; replace `IMAGE` with it, e.g. `erp-nss-hrms:v15`):
    ```bash
    git clone --branch v0.3.7 https://github.com/t1nk333r/zk_push && cd zk_push
    docker build -f deploy/Containerfile --build-arg BASE_IMAGE=IMAGE -t IMAGE-zkpush-0.3.7 .
    ```
    If the current image already contains an older zk_push, build from the image *without* it
    (the one in `apps.json` / your base build), or add zk_push v0.3.7 to `apps.json` and rebuild.
    Then set `IMAGE-zkpush-0.3.7` as the image of **every** service and `docker compose up -d`.
- [ ] **B3. Migrate and restart.**
  - Bench: `bench --site test.nss.sa migrate`, then restart the web server and workers:
    ```bash
    sudo supervisorctl status                     # group names, usually frappe-bench-web / -workers
    sudo supervisorctl restart frappe-bench-web:
    sudo supervisorctl restart frappe-bench-workers:
    ```
    The web restart takes every site on the bench offline for a few seconds (do it at a quiet
    time); devices resend what did not get through. Workers finish their current job first.
  - Docker: `docker compose exec backend bench --site test.nss.sa migrate`, then
    `docker compose restart backend queue-short queue-long scheduler`
  A restart is required: the web server keeps running the old code until then.
- [ ] **B4. Check the endpoint.**
  ```bash
  curl -s -w "  HTTP %{http_code}\n" https://test.nss.sa/iclock/cdata
  ```
  Expect `ERROR: invalid SN  HTTP 400` (zk_push answering). `404` = the app is not active on the site.
- [ ] **B5. Check the desk.** Log in, search **ZK Device User**: the list opens (empty). Press
  **Ctrl + Shift + R** once so the browser drops its cached forms. Search **ZK Time Card**: the
  report opens (rows appear for employees with Attendance this month). If the **ZK Push**
  workspace has no *ZK Time Card* link (someone edited the workspace on this site), either add
  the link in the workspace editor, or run
  `bench --site test.nss.sa --force reload-doc zk_push workspace zk_push` (Docker: prefix with
  `docker compose exec backend`). `--force` is needed (without it nothing happens) and it replaces
  the site's own edits to that workspace.
- [ ] **B6. Let HRMS calculate.** Open each **Shift Type** used by staff and tick **Enable Auto
  Attendance** (with *Process Attendance After*) and **Automatically update Last Sync of
  Checkin**. Without the second one HRMS never marks new days: zk_push delivers the checkins, HRMS
  makes the Attendance after each shift ends. Keep *Determine Check-in and Check-out* on
  *Alternating entries*. HRMS marks a day **Absent** once its shift is past, even if the punches
  have not arrived yet (device offline, a re-upload or backfill later); punches that come after
  that do not change it. Then cancel that Absent Attendance, untick *Skip Auto Attendance* on those
  Employee Checkins and click **Mark Attendance** on the Shift Type (README, "Punches that arrive
  late").

---

## Part C: ESP32 → test.nss.sa

- [ ] **C1. One agent per device.** The attendance device serves one connection at a time. Stop
  anything else reading it: the Python pull agent (`docker compose down` in its folder, or its
  systemd timer / Task Scheduler job) and the local trial:
  ```bash
  # local trial clean-up (if you did it)
  sudo ufw delete allow from 192.168.100.0/24 to any port 8096 proto tcp
  # and stop the socat window with Ctrl + C
  ```
- [ ] **C2. Point the firmware at the test site.** In `deploy/esp32/firmware/include/config.h`:
  ```c
  #define SITE_URL "https://test.nss.sa"
  #define SINCE_YEAR 2026      // first punch date to send; older punches are never sent
  #define SINCE_MONTH 1
  #define SINCE_DAY 1
  ```
- [ ] **C3. Plug in the board** (USB data cable). Find its port:
  ```bash
  pio device list          # e.g. /dev/ttyUSB0 (CH340)
  ```
- [ ] **C4. Erase, then flash.** The board keeps its upload position in flash, and a normal flash
  does not clear it: without the erase the new site would only get punches made from now on.
  ```bash
  cd ~/Projects/zk_push/deploy/esp32/firmware
  pio run -e esp32dev -t erase  --upload-port /dev/ttyUSB0
  pio run -e esp32dev -t upload --upload-port /dev/ttyUSB0
  ```
  Expect `[SUCCESS]` for both. If it hangs on `Connecting...`, hold the **BOOT** button until
  writing starts.
- [ ] **C5. Watch the first run.**
  ```bash
  pio device monitor -p /dev/ttyUSB0 -b 115200        # Ctrl + C to quit
  ```
  Press the board's **EN** (reset) button to see it from the start. Healthy:
  ```
  zk pull agent: device 192.168.100.56 -> https://test.nss.sa every 120s
  wifi: connected, IP 192.168.100.x
  sync: sent the list of 27 user(s)
  sync: sent 100 punch(es)        (repeats during the first upload)
  ```
  A run with nothing new prints nothing. Problems:

  | Line | Meaning / fix |
  |---|---|
  | `wifi: not connected` | wrong SSID/password, or a 5 GHz-only network |
  | `cannot reach the device (TCP 4370)` | board not on the device's LAN, device off, or another agent is connected |
  | `device did not answer in time` | usually another agent connected to the device (C1) |
  | `device refused the connection` | wrong `DEVICE_COMM_KEY` |
  | `the site answered 403 (is the ZK Device enabled?)` | expected on first contact: do C6 |
  | `the site answered 404 (is zk_push installed ...)` | Part B not done on this site |
  | `the site answered -1 (not reachable)` | no internet, or a TLS problem: re-check A5 |
  | `HTTP 200 but not the site's OK` | wrong `SITE_URL` or a captive portal |

- [ ] **C6. Enable the device on the site.** On first contact the site registers the device as
  *disabled* (the board logs 403). In the desk: **ZK Device** → the new device (named after its
  serial number) →
  - **Ignore Punches Before**: the first date you want imported (or leave empty to take
    everything from the firmware's `SINCE` date on),
  - **Enabled** ✓ → Save.
  The next run (within 2 minutes) uploads.
- [ ] **C7. Verify.**
  - ZK Device form: **Online**, *last seen* under 2 minutes ago, counts rising.
  - **ZK Device User**: one row per device user, with names.
  - **ZK Unmatched Users**: the *Name on Device* column is filled.
  - **Live punch**: punch on the device; within 2 minutes it is in **ZK Punch** (Created if that
    device user is mapped to an employee, else Unmatched).
- [ ] **C8. Soak for a day.** Leave it running 24 hours, then check the device stays **Online**
  and today's punches all arrived. Put the board where Wi-Fi is strong: weak signal shows as
  long gaps in *last seen*.

---

## Part D: production, nss.sa

- [ ] **D1. Backup.** As B1, with `--site nss.sa`.
- [ ] **D2. Install the app** (first time on this site).
  - Bench:
    ```bash
    cd ~/frappe-bench
    bench get-app --branch v0.3.7 https://github.com/t1nk333r/zk_push    # skip if apps/zk_push exists (one bench: it does, from Part B)
    bench --site nss.sa install-app zk_push
    bench restart
    ```
  - Docker: the image from B2 (or rebuild for this server the same way), every service on it,
    `docker compose up -d`, then
    `docker compose exec backend bench --site nss.sa install-app zk_push` and restart as in B3.
- [ ] **D3. Check the endpoint**: as B4 with `https://nss.sa` → `ERROR: invalid SN  HTTP 400`.
- [ ] **D4. Map employees first.** With the user names from the test site as a guide (or
  `tools/export_device_users.py`), set **Employee → Attendance Device ID** for each person. Done
  before go-live, punches become checkins straight away instead of waiting as Unmatched (they are
  still processed later if you map afterwards).
- [ ] **D5. Point the board at production** (`config.h`):
  ```c
  #define SITE_URL "https://nss.sa"
  #define SINCE_YEAR 2026      // go-live date: punches before it are never sent
  #define SINCE_MONTH 12
  #define SINCE_DAY 1
  ```
- [ ] **D6. Erase and flash**: exactly C3–C5. The erase matters here: it resets the upload position
  that points at the end of what the test site received.
- [ ] **D7. Enable on nss.sa**: as C6. Set **Ignore Punches Before** to the go-live date as a second
  guard, and the **Log Type** mode you want.
- [ ] **D8. Verify**: as C7, on nss.sa.
- [ ] **D9. Close the test side.** On test.nss.sa untick **Enabled** on the ZK Device, so nothing
  else can post as this device there.

---

## Part E: rollback

- **Stop uploads at once:** untick **Enabled** on the ZK Device. The board gets 403 and keeps
  retrying; nothing is lost (punches stay on the attendance device and are sent once re-enabled).
- **Move the board back:** set the previous `SITE_URL`, erase + flash (C4). The site ignores
  punches it already has.
- **Undo the site update:** restore the B1/D1 backup
  (`bench --site <site> restore <database.sql.gz> --with-public-files ... --with-private-files ...`),
  then put the previous app version back and restart.

## Part F: daily operation

- New employee: enrol on the device, then set their **Attendance Device ID**, or use
  **ZK Unmatched Users → Map** once they have punched (the board sends new user names within the
  hour, at once if the user count changed).
- Board unplugged or power cut: nothing is lost; it continues where it stopped.
- Device log cleared or replaced: the board notices and resends from its `SINCE` date; the site
  ignores what it already has.
- Changing Wi-Fi, site or a later `SINCE`: edit `config.h`, flash (C4: erase only when moving to
  another site).
- Moving `SINCE` **earlier** (to send older punches): edit `config.h`, then erase + flash (C4).
  Without the erase the board resumes where it stopped, past the older punches, and never sends
  them. The site ignores the punches it already has.

## Security note

The device serial number is the only thing the site checks: anyone who knows it can post punches
as this device. Keep it out of public places (tickets, chats, this repository) and keep the
ZK Device disabled on any site the board does not use (D9).
