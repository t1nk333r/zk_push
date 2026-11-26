#include "settings.h"

#include <Preferences.h>
#include <memory>

#include "zk_protocol.h"

#if __has_include("config.h")
#include "config.h"
#define HAVE_CONFIG_H 1
#endif

// Must match the flasher's SETTINGS_VERSION: a newer layout is refused rather than misread.
static constexpr uint8_t SETTINGS_VERSION = 1;

Settings settings;

// since: YYYYMMDD.
static uint32_t encode_since(uint32_t ymd) {
	return zk::encode_time(ymd / 10000, ymd / 100 % 100, ymd % 100, 0, 0, 0);
}

static bool load_partition() {
	Preferences p;
	if (!p.begin("zkcfg", true, "zkcfg")) return false;  // no partition, or never written
	const uint8_t version = p.getUChar("version", 0);
	if (version == 0) {
		p.end();
		return false;
	}
	if (version != SETTINGS_VERSION) {
		Serial.printf("settings: written by a newer setup tool (format %u); update the firmware\n", version);
		p.end();
		return false;
	}
	settings.wifi_ssid = p.getString("wifi_ssid", "");
	settings.wifi_password = p.getString("wifi_pass", "");
	settings.device_host = p.getString("dev_host", "");
	settings.device_port = p.getUShort("dev_port", 4370);
	settings.comm_key = p.getUInt("comm_key", 0);
	settings.site_url = p.getString("site_url", "");
	settings.since = encode_since(p.getUInt("since", 20000101));
	settings.interval_seconds = p.getUInt("interval", 120);
	// A blob, not a string: NVS strings stop at 4000 bytes and four roots are more.
	const size_t len = p.getBytesLength("root_ca");
	if (len) {
		std::unique_ptr<char[]> pem(new char[len + 1]);
		p.getBytes("root_ca", pem.get(), len);
		pem[len] = 0;
		settings.root_ca = pem.get();
	}
	p.end();
	return true;
}

bool load_settings() {
	if (load_partition()) {
		Serial.println("settings: from the setup tool");
	} else {
#ifdef HAVE_CONFIG_H
		settings.wifi_ssid = WIFI_SSID;
		settings.wifi_password = WIFI_PASSWORD;
		settings.device_host = DEVICE_HOST;
		settings.device_port = DEVICE_PORT;
		settings.comm_key = DEVICE_COMM_KEY;
		settings.site_url = SITE_URL;
		settings.root_ca = SITE_ROOT_CA;
		settings.since = zk::encode_time(SINCE_YEAR, SINCE_MONTH, SINCE_DAY, 0, 0, 0);
		settings.interval_seconds = INTERVAL_SECONDS;
		Serial.println("settings: compiled in (config.h)");
#endif
	}
	if (settings.wifi_ssid.isEmpty() || settings.device_host.isEmpty() || settings.site_url.isEmpty()) {
		Serial.println("settings: none on this board; write them with the setup tool (deploy/esp32/flasher)");
		return false;
	}
	if (settings.interval_seconds < 10) settings.interval_seconds = 10;
	return true;
}
