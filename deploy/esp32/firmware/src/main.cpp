// ESP32 pull agent (prototype): every INTERVAL_SECONDS, send the attendance device's new punches
// to the ERPNext site. Settings are in include/config.h.
#include <Arduino.h>
#include <WiFi.h>

#if !__has_include("config.h")
#error "Copy include/config.example.h to include/config.h and fill it in"
#endif
#include "config.h"
#include "sync.h"

// Reboot if the network has been unusable for this many runs in a row (stuck Wi-Fi stack, ...).
// A device that does not answer on TCP counts too: from here a stuck stack looks the same as a
// device that is off, and a needless reboot costs nothing. Other failures (wrong Comm Key, ZK Device
// not enabled) are not fixed by a reboot, so they don't count.
static constexpr int MAX_NETWORK_FAILURES = 30;
static int network_failures = 0;

static bool ensure_wifi() {
	static bool started = false;
	if (WiFi.status() == WL_CONNECTED) return true;
	if (!started) {
		Serial.printf("wifi: connecting to %s\n", WIFI_SSID);
		// The credentials come from config.h; don't let the Wi-Fi stack keep a second copy in NVS.
		WiFi.persistent(false);
		WiFi.mode(WIFI_STA);
		// No modem sleep: with it, some routers deliver packets to the board late or never (the
		// device's replies time out). The board is mains powered, so the saving does not matter.
		WiFi.setSleep(false);
		WiFi.setAutoReconnect(true);
		WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
		started = true;
	} else {
		// After a drop the Wi-Fi stack reconnects by itself; calling begin() while it does fails
		// ("sta is connecting, cannot set config"), so just wait for it.
		Serial.println("wifi: connection lost, waiting for it to reconnect");
	}
	for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
	if (WiFi.status() != WL_CONNECTED) {
		Serial.println("wifi: not connected");
		WiFi.disconnect();  // start the next attempt afresh
		WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
		return false;
	}
	Serial.printf("wifi: connected, IP %s\n", WiFi.localIP().toString().c_str());
	return true;
}

void setup() {
	Serial.begin(115200);
	delay(200);
	Serial.printf("\nzk pull agent: device %s -> %s every %ds\n", DEVICE_HOST, SITE_URL, INTERVAL_SECONDS);
}

void loop() {
	bool network_ok = ensure_wifi();
	if (network_ok) {
		const SyncResult result = run_sync();
		network_ok = result != SyncResult::SiteUnreachable && result != SyncResult::DeviceUnreachable;
	}
	network_failures = network_ok ? 0 : network_failures + 1;
	if (network_failures >= MAX_NETWORK_FAILURES) {
		Serial.println("network unusable for too long, restarting");
		delay(1000);
		ESP.restart();
	}
	delay(INTERVAL_SECONDS * 1000UL);
}
