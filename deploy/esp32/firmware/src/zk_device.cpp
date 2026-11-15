#include "zk_device.h"

#include <Arduino.h>
#include <string.h>

using namespace zk;

static constexpr uint32_t IO_TIMEOUT_MS = 10000;
static constexpr uint32_t CHUNK_RECORDS = 100;  // 4000 bytes per read

bool ZkDevice::fail(const char* message) {
	error_ = message;
	return false;
}

bool ZkDevice::recv_exact(uint8_t* buf, size_t n) {
	size_t got = 0;
	uint32_t last_data = millis();
	while (got < n) {
		const int r = client_.read(buf + got, n - got);
		if (r > 0) {
			got += r;
			last_data = millis();  // the timeout is for silence, not for the whole transfer
		} else if (!client_.connected()) {
			return fail("device closed the connection");
		} else if (millis() - last_data > IO_TIMEOUT_MS) {
			return fail("device did not answer in time");
		} else {
			delay(1);
		}
	}
	return true;
}

bool ZkDevice::recv_packet(uint16_t& code, uint8_t* dst, size_t dst_size, size_t& got) {
	uint8_t head[FRAME_HEADER_SIZE + PACKET_HEADER_SIZE];
	if (!recv_exact(head, sizeof head)) return false;
	if (get_u16(head) != FRAME_MAGIC_1 || get_u16(head + 2) != FRAME_MAGIC_2) return fail("bad frame from device");
	const uint32_t packet_len = get_u32(head + 4);
	if (packet_len < PACKET_HEADER_SIZE) return fail("short packet from device");
	code = get_u16(head + 8);
	session_ = get_u16(head + 12);
	reply_ = get_u16(head + 14);

	got = packet_len - PACKET_HEADER_SIZE;
	if (got > dst_size) {
		// Read and drop the payload so the next reply starts at a frame boundary.
		uint8_t scratch[64];
		for (size_t left = got; left > 0;) {
			const size_t n = left < sizeof scratch ? left : sizeof scratch;
			if (!recv_exact(scratch, n)) return false;
			left -= n;
		}
		return fail("device sent more data than expected");
	}
	return recv_exact(dst, got);
}

bool ZkDevice::command(uint16_t cmd, const uint8_t* payload, size_t len, uint16_t& code, uint8_t* dst,
	size_t dst_size, size_t* got) {
	uint8_t frame[64];
	const size_t n = build_frame(cmd, session_, reply_, payload, len, frame, sizeof frame);
	if (!n) return fail("command too large");
	reply_ = (reply_ + 1) % 65535;
	if (client_.write(frame, n) != n) return fail("could not send to device");
	if (!dst) {
		dst = rx_;
		dst_size = sizeof rx_;
		got = &rx_len_;
	}
	size_t received = 0;
	if (!recv_packet(code, dst, dst_size, received)) return false;
	if (got) *got = received;
	return true;
}

bool ZkDevice::connect(const char* host, uint16_t port, uint32_t key) {
	session_ = 0;
	reply_ = 65534;
	if (!client_.connect(host, port)) return fail("cannot reach the device (TCP 4370)");
	uint16_t code;
	if (!command(CMD_CONNECT, nullptr, 0, code)) return false;
	if (code == ACK_UNAUTH) {
		uint8_t auth[4];
		comm_key(key, session_, auth);
		if (!command(CMD_AUTH, auth, sizeof auth, code)) return false;
	}
	return code == ACK_OK || fail("device refused the connection (wrong Comm Key?)");
}

bool ZkDevice::serial_number(char* out, size_t out_size) {
	if (!out_size) return fail("no room for the serial number");
	static const char query[] = "~SerialNumber";
	uint16_t code;
	if (!command(CMD_OPTIONS_RRQ, (const uint8_t*)query, sizeof query, code)) return false;
	if (code != ACK_OK) return fail("device did not return its serial number");
	// Reply: "~SerialNumber=CO8E214361288\0"
	const char* text = (const char*)rx_;
	const char* eq = (const char*)memchr(text, '=', rx_len_);
	const char* value = eq ? eq + 1 : text;
	size_t i = 0;
	while (value + i < text + rx_len_ && value[i] && i + 1 < out_size) {
		out[i] = value[i];
		i++;
	}
	out[i] = '\0';
	return i > 0 || fail("empty serial number");
}

bool ZkDevice::record_count(uint32_t& records, uint32_t& capacity) {
	uint16_t code;
	if (!command(CMD_GET_FREE_SIZES, nullptr, 0, code)) return false;
	if (code != ACK_OK || rx_len_ < 80) return fail("device did not return its log size");
	records = get_u32(rx_ + 8 * 4);
	capacity = get_u32(rx_ + 16 * 4);
	return true;
}

bool ZkDevice::prepare_log(uint32_t& buffer_size) {
	uint8_t req[11];  // u8 1, i16 CMD_ATTLOG_RRQ, i32 0, i32 0
	memset(req, 0, sizeof req);
	req[0] = 1;
	put_u16(req + 1, CMD_ATTLOG_RRQ);
	uint16_t code;
	if (!command(CMD_PREPARE_BUFFER, req, sizeof req, code)) return false;
	if (code != ACK_OK || rx_len_ < 5) return fail("device cannot prepare its log");
	buffer_size = get_u32(rx_ + 1);
	return true;
}

bool ZkDevice::read_chunk(uint32_t start, size_t size, uint8_t* dst) {
	uint8_t req[8];
	put_u32(req, start);
	put_u32(req + 4, size);
	uint16_t code;
	size_t got = 0;
	if (!command(CMD_READ_BUFFER, req, sizeof req, code, dst, size, &got)) return false;
	if (code == CMD_PREPARE_DATA) {
		got = 0;  // that payload was only the size; the data follows as CMD_DATA packets, then ACK_OK
		while (true) {
			size_t part = 0;
			if (!recv_packet(code, dst + got, size - got, part)) return false;
			if (code == CMD_DATA) {
				got += part;
			} else if (code == ACK_OK) {
				break;
			} else {
				return fail("unexpected packet while reading the log");
			}
		}
	} else if (code != CMD_DATA) {
		return fail("device cannot read its log");
	}
	return got == size || fail("device returned a short log chunk");
}

bool ZkDevice::read_records(uint32_t first, uint32_t count, uint8_t* out) {
	uint32_t done = 0;
	if (first == 0 && count > 0) {
		// start 0 means the u32 length header, not record 0: read header + record 0, drop the header.
		uint8_t head[4 + RECORD_SIZE];
		if (!read_chunk(0, sizeof head, head)) return false;
		memcpy(out, head + 4, RECORD_SIZE);
		done = 1;
	}
	while (done < count) {
		const uint32_t n = min(CHUNK_RECORDS, count - done);
		// record k (k >= 1) is at k * RECORD_SIZE
		if (!read_chunk((first + done) * RECORD_SIZE, n * RECORD_SIZE, out + done * RECORD_SIZE)) return false;
		done += n;
	}
	return true;
}

void ZkDevice::free_log() {
	uint16_t code;
	command(CMD_FREE_DATA, nullptr, 0, code);
}

void ZkDevice::close() {
	uint16_t code;
	if (client_.connected()) command(CMD_EXIT, nullptr, 0, code);
	client_.stop();
}
