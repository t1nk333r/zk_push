#include "site.h"

#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>

#include "settings.h"

static constexpr uint32_t HTTP_TIMEOUT_MS = 30000;
static constexpr unsigned long TLS_HANDSHAKE_TIMEOUT_S = 30;  // the client default is 120 s

// Runs one request with a fresh client; https checks the site against settings.root_ca.
template <typename Fn>
static int with_http(const String& url, Fn request) {
	const bool https = url.startsWith("https://");
	if (https && settings.root_ca.isEmpty()) {
		Serial.println("site: https needs the site's root certificate in the settings");
		return -1;
	}
	WiFiClient plain;
	WiFiClientSecure secure;
	if (https) {
		secure.setCACert(settings.root_ca.c_str());  // may hold several PEM certificates, one after another
		secure.setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_S);
	}
	HTTPClient http;
	http.setTimeout(HTTP_TIMEOUT_MS);
	if (!http.begin(https ? static_cast<WiFiClient&>(secure) : plain, url)) return -1;
	const int status = request(http);
	http.end();
	return status;
}

static String cdata_url(const char* serial, const char* query) {
	String base(settings.site_url);
	while (base.endsWith("/")) base.remove(base.length() - 1);
	return base + "/iclock/cdata?SN=" + serial + "&" + query;  // serials are letters and digits only
}

int site_heartbeat(const char* serial) {
	return with_http(cdata_url(serial, "options=all"), [](HTTPClient& http) { return http.GET(); });
}

int site_post(const char* serial, const char* table, const char* body, size_t len) {
	return with_http(cdata_url(serial, (String("table=") + table).c_str()), [&](HTTPClient& http) {
		http.addHeader("Content-Type", "text/plain");
		const int status = http.POST((uint8_t*)body, len);
		// Only the site's "OK: <n>" means stored: a proxy, captive portal or wrong path can also answer
		// 200, and progress saved after that would skip these punches for good.
		if (status == 200 && !http.getString().startsWith("OK")) {
			Serial.println("site: HTTP 200 but not the site's OK (wrong site address, proxy or captive portal?)");
			return 0;
		}
		return status;
	});
}
