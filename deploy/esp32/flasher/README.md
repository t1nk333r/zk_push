# ZK pull agent setup

Flashes an ESP32 board with the pull agent firmware and its settings: site, Wi-Fi, attendance device.
One firmware file (`zk-agent.bin`) serves every site; the settings are written next to it.

## Get it

From the GitHub release (or the `esp32-firmware` workflow run): `zk-agent-setup-windows.zip` or
`zk-agent-setup-linux.zip`. Unzip; keep `zk-agent.bin` next to the app, which picks it up.

- **Windows:** the app is not signed, so SmartScreen says "Windows protected your PC": More info →
  Run anyway. If no port shows up for the board, install its USB driver: CP210x (Silicon Labs) or
  CH340 (WCH), depending on the chip next to the USB socket.
- **Linux:** `chmod +x zk-agent-setup`. Your user needs access to the serial port: add it to the
  `uucp` group (Arch) or `dialout` (Debian/Ubuntu), then log out and in.

From source instead (Python 3.13+ with tkinter): `pip install -r requirements.txt`, then
`python zk_flasher.py [zk-agent.bin]`.

## Use

1. Plug the board in with a USB **data** cable (many are charge-only). It shows under USB port.
2. **Site address**: `https://…` of the ERPNext site. **Get certificate** fetches the root
   certificate(s) the board needs; for a Let's Encrypt site it takes all four of their roots, so the
   board keeps working when the site's certificate moves to another one.
3. **Wi-Fi**: the network this computer is on is picked and, if the computer has it saved, its
   password filled in. The board only has 2.4 GHz: networks marked (5 GHz) will not work.
4. **Attendance device**: its IP (device Menu > COMM. > Ethernet) and Comm Key (0 if none).
5. **Send punches from**: the first punch date to send; older punches are never sent.
6. **Install firmware + settings**. The board restarts and its log follows; healthy:

   ```
   board: settings: from the setup tool
   board: wifi: connected, IP 192.168.100.77
   board: sync: sent 7 punch(es)
   ```

   If it hangs on `Connecting...`, hold the board's **BOOT** button until writing starts.

The ZK Device must exist and be enabled on the site; until then the log shows
`the site answered 403 (is the ZK Device enabled?)`.

## Later

- **New Wi-Fi password, other device IP**: fill in the form, **Write settings only**.
- **Firmware update**: **Install firmware + settings** with the new `zk-agent.bin`. The board keeps
  its upload position, so nothing is sent twice.
- **Another site**: change the site address and write. The board starts over and sends everything
  since the date (the site ignores punches it already has). **Send all punches again** does the
  same for the same site.
- **Show board log** resets the board and shows what it is doing.

The settings, including the Wi-Fi password and Comm Key, sit in the board's flash in plain text:
anyone with the board and a USB cable can read them. Give the board its own or a guest Wi-Fi
network and mount it out of reach.
