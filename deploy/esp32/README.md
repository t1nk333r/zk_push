# ESP32 pull agent: exploration

Status: **exploration**. Nothing here is built yet except `zk_reference.py`, which is the spec the
firmware would port. Goal: replace the Raspberry Pi running `tools/pull_from_device.py` with a
~50 SAR ESP32 that does the same job for devices without ADMS push.

## What was verified on the office device

Device: MB20-VL, serial CO8E214361288, platform ZMM510_TFT, firmware Ver 6.60 (May 8 2021),
12,803 of 50,000 records, 27 users. Using `zk_reference.py` (standard library only, no pyzk):

- Connect, Comm Key handshake, serial number and record count: ~0.9 s on Wi-Fi.
- **Records can be read by index.** After "prepare buffer", a read at `start = k * 40` returns
  records from `k` on, so each run only transfers the new records (5 records = 200 bytes) instead
  of the whole 512 KB log. Output matches pyzk line for line, including multi-chunk reads in 4 KB
  pieces.
- Record format on this firmware is 40 bytes (`u16 uid, char[24] user_id, u8 verify, u32 time,
  u8 state, 8 reserved`). Other firmwares use 8 or 16-byte records; the record size is
  `data length / record count`, so the firmware can detect it.

The protocol summary is at the top of `zk_reference.py`.

## Why this fits an ESP32

| Need | ESP32 |
|---|---|
| RAM | Only new records are read, in 1–4 KB chunks; HTTPS (mbedTLS) needs ~40–50 KB; the ESP32 has ~320 KB usable |
| Network | Wi-Fi built in; Ethernet on boards like WT32-ETH01 / Olimex ESP32-POE |
| State | Last record index + a fingerprint of that record, in NVS flash (written a few times a day) |
| Clock | Not needed: punch times come from the device |

Reading by index is also more robust than the Python agent's time-based state: a punch recorded
after the device clock was moved back is still a new record at the end of the log.

## Firmware design (proposed)

Arduino core on PlatformIO (WiFi/ETH, `WiFiClientSecure`, `HTTPClient`, `Preferences`), or
ESP-IDF if we want tighter control.

Every `interval` seconds:

1. Connect to the device (`Device` in `zk_reference.py`), read `records`.
2. Check the stored fingerprint (user id + time of record `last - 1`). If `records < last` or the
   fingerprint differs (log cleared or rewritten), start again from record 0; the site ignores
   punches it already has.
3. Read records `last .. records-1` in 4 KB chunks, skip those before `since`, append ATTLOG lines
   to a ~8 KB buffer, and POST each full buffer to `<site>/iclock/cdata?SN=<serial>&table=ATTLOG`.
4. After each HTTP 200, store the new `last` and fingerprint in NVS (so a large first upload is
   resumable).
5. GET `/iclock/cdata?SN=<serial>&options=all` as a heartbeat (the site shows the device online).

Setup: a Wi-Fi captive portal on first boot (Wi-Fi, device IP, Comm Key, site URL, since).
Updates: OTA from the GitHub release. Status: serial log, plus the site's last-seen.

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
- HTTPS certificates: the firmware needs a CA bundle; a site certificate change must not brick it.
- Two clients at once (ESP32 plus ZKTime software, or the Python agent) can make reads fail;
  only one agent per device.
- Field debugging is harder than SSH into a Pi.

## Effort estimate

- Prototype (one device, hard-coded settings, Wi-Fi, HTTPS POST): 2–3 days.
- Field-ready (setup portal, Ethernet, OTA, resumable backlog, tests against `zk_reference.py`):
  1–2 weeks.

Worth it when deploying to several offices; for one office the Pi Zero 2 W is cheaper overall.
