// Reads records by index: the device appends records, so everything after the last sent index is
// new, including punches stamped after the device clock was moved back. A fingerprint of the last
// sent record detects a cleared or rewritten log, in which case the pull starts again from record
// 0 (the site ignores punches it already has).
#include "sync.h"

#include <Arduino.h>
#include <Preferences.h>
#include <WiFiClient.h>
#include <string.h>

#include "config.h"
#include "site.h"
#include "zk_device.h"

using namespace zk;

static constexpr size_t BODY_MAX = 8192;  // one POST
static constexpr size_t READ_RECORDS = 100;

namespace {

struct Progress {
	uint32_t next = 0;  // index of the first record not yet sent
	uint32_t fp_time = 0;  // fingerprint of record next - 1
	char fp_pin[PIN_MAX + 1] = "";

	void load() {
		Preferences p;
		p.begin("zkpull", true);
		next = p.getUInt("next", 0);
		fp_time = p.getUInt("fp_time", 0);
		p.getString("fp_pin", fp_pin, sizeof fp_pin);
		p.end();
	}

	void save() const {
		Preferences p;
		p.begin("zkpull", false);
		p.putUInt("next", next);
		p.putUInt("fp_time", fp_time);
		p.putString("fp_pin", fp_pin);
		p.end();
	}

	void set(uint32_t index_after, const Record& r) {
		next = index_after;
		fp_time = r.time;
		strncpy(fp_pin, r.pin, sizeof fp_pin);
	}

	bool matches(const Record& r) const { return r.time == fp_time && strcmp(r.pin, fp_pin) == 0; }
};

// ATTLOG lines waiting to be posted, and the progress they represent once accepted.
struct Batch {
	char body[BODY_MAX];
	size_t len = 0;
	size_t lines = 0;
	Progress pending;
};

}  // namespace

static Batch batch;
static uint8_t chunk[READ_RECORDS * RECORD_SIZE];

static bool flush(const char* serial, Progress& saved) {
	if (batch.len) {
		const int status = site_post_attlog(serial, batch.body, batch.len);
		if (status != 200) {
			Serial.printf("sync: upload rejected (HTTP %d), retrying next run\n", status);
			return false;
		}
		Serial.printf("sync: sent %u punch(es)\n", unsigned(batch.lines));
	}
	if (batch.pending.next != saved.next) {
		saved = batch.pending;
		saved.save();
	}
	batch.len = batch.lines = 0;
	return true;
}

static bool sync_records(ZkDevice& dev, const char* serial, uint32_t records) {
	Progress saved;
	saved.load();
	const uint32_t since = encode_time(SINCE_YEAR, SINCE_MONTH, SINCE_DAY, 0, 0, 0);

	if (saved.next > records) {
		Serial.println("sync: the device log is shorter than before (cleared?), starting from record 0");
		saved = Progress();
	} else if (saved.next > 0) {
		Record last;
		if (!dev.read_records(saved.next - 1, 1, chunk)) return false;
		if (!parse_record(chunk, last) || !saved.matches(last)) {
			Serial.println("sync: the device log changed (rewritten?), starting from record 0");
			saved = Progress();
		}
	}

	batch.pending = saved;
	batch.len = batch.lines = 0;
	for (uint32_t i = saved.next; i < records;) {
		const uint32_t n = min<uint32_t>(READ_RECORDS, records - i);
		if (!dev.read_records(i, n, chunk)) return false;
		for (uint32_t k = 0; k < n; k++) {
			Record r;
			const bool valid = parse_record(chunk + k * RECORD_SIZE, r);
			if (valid && r.time >= since) {
				char line[80];
				const size_t len = format_attlog_line(r, line, sizeof line);
				if (batch.len + len > BODY_MAX && !flush(serial, saved)) return false;
				memcpy(batch.body + batch.len, line, len);
				batch.len += len;
				batch.lines++;
			}
			batch.pending.set(i + k + 1, r);  // skipped records are never sent, so they count as done
		}
		i += n;
	}
	return flush(serial, saved);
}

bool run_sync() {
	WiFiClient tcp;
	ZkDevice dev(tcp);
	char serial[33];
	uint32_t records = 0, capacity = 0, buffer_size = 0;

	bool ok = dev.connect(DEVICE_HOST, DEVICE_PORT, DEVICE_COMM_KEY) && dev.serial_number(serial, sizeof serial) &&
		dev.record_count(records, capacity);
	if (!ok) {
		Serial.printf("sync: device: %s\n", dev.error());
		dev.close();
		return false;
	}

	const int status = site_heartbeat(serial);
	if (status != 200) {
		Serial.printf("sync: device %s: the site answered HTTP %d (is the ZK Device enabled?)\n", serial, status);
		dev.close();
		return false;
	}

	ok = dev.prepare_log(buffer_size);
	if (ok && buffer_size != 4 + records * RECORD_SIZE) {
		Serial.printf("sync: unsupported log format (%u bytes for %u records)\n", unsigned(buffer_size),
			unsigned(records));
		ok = false;
	} else if (ok) {
		ok = sync_records(dev, serial, records);
		if (!ok && *dev.error()) Serial.printf("sync: device: %s\n", dev.error());
	}
	dev.free_log();
	dev.close();
	return ok;
}
