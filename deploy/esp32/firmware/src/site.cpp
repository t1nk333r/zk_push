#include "site.h"

#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>

#include "config.h"

static constexpr uint32_t HTTP_TIMEOUT_MS = 30000;

// Runs one request with a fresh client; https uses SITE_ROOT_CA.
template <typename Fn>
static int with_http(const String& url, Fn request) {
	const bool https = url.startsWith("https://");
	if (https && sizeof(SITE_ROOT_CA) <= 1) {
		Serial.println("site: https needs SITE_ROOT_CA in config.h");
		return -1;
	}
	WiFiClient plain;
	WiFiClientSecure secure;
	if (https) secure.setCACert(SITE_ROOT_CA);
	HTTPClient http;
	http.setTimeout(HTTP_TIMEOUT_MS);
	if (!http.begin(https ? static_cast<WiFiClient&>(secure) : plain, url)) return -1;
	const int status = request(http);
	http.end();
	return status;
}

static String cdata_url(const char* serial, const char* query) {
	return String(SITE_URL) + "/iclock/cdata?SN=" + serial + "&" + query;
}

int site_heartbeat(const char* serial) {
	return with_http(cdata_url(serial, "options=all"), [](HTTPClient& http) { return http.GET(); });
}

int site_post_attlog(const char* serial, const char* body, size_t len) {
	return with_http(cdata_url(serial, "table=ATTLOG"), [&](HTTPClient& http) {
		http.addHeader("Content-Type", "text/plain");
		return http.POST((uint8_t*)body, len);
	});
}
