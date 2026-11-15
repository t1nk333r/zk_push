# ESP32 pull agent firmware (prototype)

Every 2 minutes: read the attendance device's new records over the LAN and post them to the site's
`/iclock/cdata`, like `tools/pull_from_device.py`. Settings are compiled in (`include/config.h`);
there is no setup page, Ethernet or OTA yet. Design and findings: `../README.md`.

Board: any ESP32-WROOM-32 DevKit (e.g. ESP32-DevKitC) with a USB data cable.

## Build and flash

```bash
python3 -m venv ~/.venvs/pio && ~/.venvs/pio/bin/pip install platformio   # once
cd deploy/esp32/firmware
cp include/config.example.h include/config.h    # Wi-Fi, device IP, Comm Key, site URL, since
~/.venvs/pio/bin/pio run -e esp32dev -t upload   # board on USB; first build downloads the toolchain
~/.venvs/pio/bin/pio device monitor              # serial log, 115200
```

If the board is not found, try another USB cable (many are charge-only) and check that your user
can open `/dev/ttyUSB0`: add it to the `uucp` group (Arch) or `dialout` group (Debian/Ubuntu),
then log out and in.

A healthy log:

```
zk pull agent: device 192.168.100.56 -> https://erp.example.com every 120s
wifi: connected, IP 192.168.100.77
sync: sent 7 punch(es)
```

## Site and HTTPS

- The site must be reachable from the office Wi-Fi.
- Use `https://`: over `http://` the device serial number, the only thing the site checks, travels
  in clear text.
- Set `SITE_ROOT_CA` to the site's root certificate (PEM). To see which root the site uses:
  `openssl s_client -connect erp.example.com:443 -showcerts </dev/null`. Include every root it may
  move to (for Let's Encrypt, "ISRG Root X1" and "ISRG Root X2", concatenated): without OTA, a
  missing root means reflashing over USB.
- The ZK Device must exist and be enabled on the site; until then the log shows `HTTP 403`.

## How it works

- `lib/zk_protocol`: protocol maths (checksum, Comm Key, time, line format); unit-tested on a PC
  with values from `../zk_reference.py`: `~/.venvs/pio/bin/pio test -e native`.
- `src/zk_device.*`: the TCP conversation with the device; reads records by index, in 4000-byte
  chunks.
- `src/sync.*`: sends records after the last sent one; its index and fingerprint are kept in NVS
  flash, so it resumes after a power cut, follows the log when a full device drops old records,
  and starts over when the log was cleared. `../zk_reference.py --sync` runs the same algorithm
  from a PC.
- `src/site.*`: heartbeat GET and ATTLOG POST.
- `src/main.cpp`: Wi-Fi, the 2-minute loop, a restart after 30 runs in a row without network
  (Wi-Fi down or the site unreachable; other failures don't trigger restarts).
