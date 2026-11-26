// Copyright (c) 2026 Abdulrahman Aloqayli
// SPDX-License-Identifier: MIT
// The board's settings: written by the setup tool (../flasher) into the "zkcfg" flash partition, so
// one firmware image serves every site. A development build can compile them in instead
// (include/config.h, used when the partition is empty).
#pragma once

#include <Arduino.h>

struct Settings {
	String wifi_ssid;
	String wifi_password;
	String device_host;  // attendance device on the LAN
	uint16_t device_port = 4370;
	uint32_t comm_key = 0;
	String site_url;  // ERPNext site running zk_push
	String root_ca;  // the site's root certificate(s), PEM, several may be concatenated
	uint32_t since = 0;  // device-encoded time: never send punches before it
	uint32_t interval_seconds = 120;
};

extern Settings settings;

// Fills `settings` and logs where they came from. Returns false (and logs why) if the board has none.
bool load_settings();
