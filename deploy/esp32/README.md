# ESP32 pull agent

Status: **prototype firmware** in `firmware/`: it builds and passes its unit tests, but has not yet
run on a board. `zk_reference.py` is the spec it ports, verified against the real device. Goal:
replace the Raspberry Pi running `tools/pull_from_device.py` with a ~50 SAR ESP32 that does the
same job for devices without ADMS push.

## What was verified on the office device

Device: MB20-VL, serial CO8E214361288, platform ZMM510_TFT, firmware Ver 6.60 (May 8 2021),
12,803 of 50,000 records, 27 users. Using `zk_reference.py` (standard library only, no pyzk):

- Connect, Comm Key handshake, serial number and record count: ~0.9 s on Wi-Fi.
- **Records can be read by index.** After "prepare buffer", a read at `start = k * 40` (k >= 1)
  returns records from `k` on; `start = 0` returns the 4-byte length header first, so record 0 is
  read as `start = 0, size = 44`. Each run only transfers the new records (5 records = 200 bytes)
  instead of the whole 512 KB log. Output matches pyzk for all 12,803 records, read in 4000-byte
  (100-record) chunks.
- Record format on this firmware is 40 bytes (`u16 uid, char[24] user_id, u8 verify, u32 time,
  u8 state, 8 reserved`). Other firmwares use 8 or 16-byte records; the firmware detects that from
  the buffer size and stops with "unsupported log format" rather than misreading them.

The protocol summary is at the top of `zk_reference.py`.

## Why this fits an ESP32

| Need | ESP32 |
|---|---|
| RAM | Only new records are read, in 4000-byte chunks; HTTPS (mbedTLS) needs ~40–50 KB; the ESP32 has ~320 KB usable |
| Network | Wi-Fi built in; Ethernet on boards like WT32-ETH01 / Olimex ESP32-POE |
| State | Last record index + a fingerprint of that record, in NVS flash (written after each accepted upload, i.e. only when there are new punches) |
| Clock | Not needed: punch times come from the device |

Reading by index is also more robust than the Python agent's time-based state: a punch recorded
after the device clock was moved back is still a new record at the end of the log.

## How the firmware works

Every `INTERVAL_SECONDS` (see `firmware/README.md` for the code layout):

1. Connect to the device, read the serial number and record count.
2. Heartbeat: GET `/iclock/cdata?SN=<serial>&options=all` (the site shows the device online; a
   403 means the ZK Device is not enabled).
3. Prepare the log. Find the last sent record by its fingerprint (user id + time): normally at
   `next - 1`; if the device dropped old records (full log), up to 1,000 records lower. If it is
   not found, the log was cleared or rewritten: start again from record 0 (the site ignores
   punches it already has).
4. Read the records after it in 4000-byte chunks, skip those before `SINCE` and corrupt dates,
   and POST up to 8 KB of ATTLOG lines at a time to `/iclock/cdata?SN=<serial>&table=ATTLOG`.
5. After each HTTP 200, store the new index and fingerprint in NVS, so a large first upload is
   resumable after a power cut.

Use an `https://` site: over `http://` the device serial number, which is all the site checks,
travels in clear text.

Not built yet: a setup page (settings are compiled in), Ethernet, OTA updates.

## Hardware

| Board | Price (KSA) | Notes |
|---|---|---|
| ESP32-WROOM-32 dev board | ~44–59 SAR (Desertcart listings) | Wi-Fi only, USB power |
| WT32-ETH01 | not checked | Ethernet, needs a 5 V supply and a USB-serial adapter to flash |
| Olimex ESP32-POE | not checked | Ethernet, can be powered by PoE |

Compare: Raspberry Pi Zero 2 W kit ~150 SAR, runs the existing agent unchanged.

## Risks

- Firmware differences between device models (record size, UDP-only devices). Only the
  MB20-VL / ZMM510 above is verified.
- HTTPS certificates: the root CA(s) are compiled in and there is no OTA yet, so put every root the
  site may use in `SITE_ROOT_CA` (several PEMs can be concatenated); certificate dates are not
  checked (no clock), only the chain and hostname.
- Two clients at once (ESP32 plus ZKTime software, or the Python agent) can make reads fail;
  only one agent per device.
- Field debugging is harder than SSH into a Pi.

## Effort

The prototype (one device, compiled-in settings, Wi-Fi, HTTPS) is written. Field-ready (setup page,
Ethernet, OTA, a few days of soak testing on a board) is roughly another week.

Worth it when deploying to several offices; for one office the Pi Zero 2 W is cheaper overall.
