// Copy to config.h (git-ignored) and fill in. The prototype has no setup page yet.
#pragma once

// Office Wi-Fi (2.4 GHz: the ESP32 has no 5 GHz radio).
#define WIFI_SSID "office-wifi"
#define WIFI_PASSWORD "secret"

// Attendance device on the LAN (Menu -> COMM. -> Ethernet), and its Comm Key (0 if none).
#define DEVICE_HOST "192.168.100.56"
#define DEVICE_PORT 4370
#define DEVICE_COMM_KEY 0

// ERPNext site running zk_push. Use https: over http the device serial number, its only credential,
// travels in clear text. Paste the site's root CA certificate(s) as PEM below. Several certificates
// can be concatenated (e.g. ISRG Root X1 and X2 for Let's Encrypt), so the board keeps working if
// the site's certificate moves to another root; without OTA, a missing root means a USB reflash.
#define SITE_URL "https://erp.example.com"
#define SITE_ROOT_CA ""

// Never send punches before this date (the pull agent's --since). 2000-01-01 = everything.
#define SINCE_YEAR 2026
#define SINCE_MONTH 1
#define SINCE_DAY 1

// Seconds between pulls.
#define INTERVAL_SECONDS 120
