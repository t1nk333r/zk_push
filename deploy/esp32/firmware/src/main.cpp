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
// Other failures (device off, ZK Device not enabled) are not fixed by a reboot, so they don't count.
static constexpr int MAX_NETWORK_FAILURES = 30;
static int network_failures = 0;

static bool ensure_wifi() {
	if (WiFi.status() == WL_CONNECTED) return true;
	Serial.printf("wifi: connecting to %s\n", WIFI_SSID);
	WiFi.mode(WIFI_STA);
	WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
	for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
	if (WiFi.status() != WL_CONNECTED) {
		Serial.println("wifi: not connected");
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
	const bool network_ok = ensure_wifi() && run_sync() != SyncResult::SiteUnreachable;
	network_failures = network_ok ? 0 : network_failures + 1;
	if (network_failures >= MAX_NETWORK_FAILURES) {
		Serial.println("network unusable for too long, restarting");
		delay(1000);
		ESP.restart();
	}
	delay(INTERVAL_SECONDS * 1000UL);
}
