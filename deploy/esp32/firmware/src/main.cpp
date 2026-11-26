// ESP32 pull agent (prototype): every few minutes, send the attendance device's new punches
// to the ERPNext site. Settings come from the setup tool (see settings.h).
#include <Arduino.h>
#include <WiFi.h>

#include "settings.h"
#include "sync.h"

// Reboot if the network has been unusable for this many runs in a row (stuck Wi-Fi stack, ...).
// A device that does not answer on TCP counts too: from here a stuck stack looks the same as a
// device that is off, and a needless reboot costs nothing. Other failures (wrong Comm Key, ZK Device
// not enabled) are not fixed by a reboot, so they don't count.
static constexpr int MAX_NETWORK_FAILURES = 30;
static int network_failures = 0;
static bool configured = false;

static bool ensure_wifi() {
	static bool started = false;
	if (WiFi.status() == WL_CONNECTED) return true;
	if (!started) {
		Serial.printf("wifi: connecting to %s\n", settings.wifi_ssid.c_str());
		// The credentials are in the settings; don't let the Wi-Fi stack keep a second copy in NVS.
		WiFi.persistent(false);
		WiFi.mode(WIFI_STA);
		// No modem sleep: with it, some routers deliver packets to the board late or never (the
		// device's replies time out). The board is mains powered, so the saving does not matter.
		WiFi.setSleep(false);
		WiFi.setAutoReconnect(true);
		WiFi.begin(settings.wifi_ssid.c_str(), settings.wifi_password.c_str());
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
		WiFi.begin(settings.wifi_ssid.c_str(), settings.wifi_password.c_str());
		return false;
	}
	Serial.printf("wifi: connected, IP %s\n", WiFi.localIP().toString().c_str());
	return true;
}

void setup() {
	Serial.begin(115200);
	delay(200);
	Serial.println("\nzk pull agent");
	configured = load_settings();
	if (configured) {
		Serial.printf("zk pull agent: device %s -> %s every %us\n", settings.device_host.c_str(),
			settings.site_url.c_str(), unsigned(settings.interval_seconds));
	}
}

void loop() {
	if (!configured) {  // nothing to do until the setup tool writes settings (it resets the board)
		delay(30000);
		Serial.println("settings: none on this board; write them with the setup tool (deploy/esp32/flasher)");
		return;
	}
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
	delay(settings.interval_seconds * 1000UL);
}
