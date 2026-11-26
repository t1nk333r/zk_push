Setup tool for the ESP32 pull agent: flashes a board with the firmware and its settings (site, Wi-Fi, attendance device) from a window. One firmware image (`zk-agent.bin`, included) serves every site.

**Download** `zk-agent-setup-windows.zip` (Windows) or `zk-agent-setup-linux.zip` (Linux), unzip, and keep `zk-agent.bin` next to the app. Instructions: `README.md` in the zip.

**New in version 2**
- The app updates itself: it looks for a newer version when it starts (or on *Check for updates*), asks, checks the download against the release's checksums, and restarts. Version 1 has no updater: replace it by hand once.
- A board plugged in after the window opened shows up by itself (the first time on a PC, Windows takes a minute to install the USB driver).
- With no port on Windows, the app says why: the board's driver is missing (with its download), or Windows sees no board (usually a charge-only cable).
- Version and copyright in the window and in the .exe's properties.

Windows: the app is not signed, so SmartScreen warns: More info → Run anyway.

This is not a zk_push app release: sites don't offer it as an upgrade.

Copyright (c) 2026 Abdulrahman Aloqayli. MIT License.
