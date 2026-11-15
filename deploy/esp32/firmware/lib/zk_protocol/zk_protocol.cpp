#include "zk_protocol.h"

#include <stdio.h>
#include <string.h>

namespace zk {

static constexpr int32_t USHRT_MAX_ = 65535;

uint16_t checksum(const uint8_t* data, size_t len) {
	int32_t total = 0;
	size_t i = 0;
	for (; i + 1 < len; i += 2) {
		total += get_u16(data + i);
		if (total > USHRT_MAX_) total -= USHRT_MAX_;
	}
	if (i < len) total += data[i];
	while (total > USHRT_MAX_) total -= USHRT_MAX_;
	total = ~total;
	while (total < 0) total += USHRT_MAX_;
	return uint16_t(total);
}

void comm_key(uint32_t key, uint16_t session_id, uint8_t out[4], uint8_t ticks) {
	uint32_t k = 0;
	for (int i = 0; i < 32; i++) k = (k << 1) | ((key >> i) & 1);  // bit-reverse
	k += session_id;
	uint8_t b[4];
	put_u32(b, k);
	b[0] ^= 'Z';
	b[1] ^= 'K';
	b[2] ^= 'S';
	b[3] ^= 'O';
	const uint8_t swapped[4] = {b[2], b[3], b[0], b[1]};  // swap the 16-bit halves
	out[0] = swapped[0] ^ ticks;
	out[1] = swapped[1] ^ ticks;
	out[2] = ticks;
	out[3] = swapped[3] ^ ticks;
}

uint32_t encode_time(int year, int month, int day, int hour, int minute, int second) {
	return ((((uint32_t(year - 2000) * 12 + (month - 1)) * 31 + (day - 1)) * 24 + hour) * 60 + minute) * 60 +
		second;
}

void decode_time(uint32_t v, int& year, int& month, int& day, int& hour, int& minute, int& second) {
	second = v % 60;
	v /= 60;
	minute = v % 60;
	v /= 60;
	hour = v % 24;
	v /= 24;
	day = v % 31 + 1;
	v /= 31;
	month = v % 12 + 1;
	year = v / 12 + 2000;
}

size_t build_frame(uint16_t command, uint16_t session_id, uint16_t reply_id, const uint8_t* payload,
	size_t payload_len, uint8_t* out, size_t out_size) {
	const size_t packet_len = PACKET_HEADER_SIZE + payload_len;
	if (out_size < FRAME_HEADER_SIZE + packet_len) return 0;
	uint8_t* packet = out + FRAME_HEADER_SIZE;
	// The checksum covers the packet with the previous reply id; the packet carries the next one.
	put_u16(packet, command);
	put_u16(packet + 2, 0);
	put_u16(packet + 4, session_id);
	put_u16(packet + 6, reply_id);
	if (payload_len) memcpy(packet + PACKET_HEADER_SIZE, payload, payload_len);
	const uint16_t sum = checksum(packet, packet_len);
	put_u16(packet + 2, sum);
	put_u16(packet + 6, uint16_t((reply_id + 1) % USHRT_MAX_));
	put_u16(out, FRAME_MAGIC_1);
	put_u16(out + 2, FRAME_MAGIC_2);
	put_u32(out + 4, packet_len);
	return FRAME_HEADER_SIZE + packet_len;
}

bool parse_record(const uint8_t* raw, Record& record) {
	// u16 uid, char[24] user_id, u8 verify, u32 time, u8 state, 8 reserved
	memcpy(record.pin, raw + 2, PIN_MAX);
	record.pin[PIN_MAX] = '\0';
	record.verify = raw[26];
	record.time = get_u32(raw + 27);
	record.state = raw[31];
	return record.pin[0] != '\0';
}

static bool valid_date(int y, int mo, int d) {
	static const int days[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
	const bool leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
	return d <= days[mo - 1] + (mo == 2 && leap ? 1 : 0);
}

size_t format_attlog_line(const Record& r, char* out, size_t out_size) {
	int y, mo, d, h, mi, s;
	decode_time(r.time, y, mo, d, h, mi, s);
	if (!valid_date(y, mo, d)) return 0;  // a corrupt record: the encoding allows day 31 in every month
	const int n = snprintf(out, out_size, "%s\t%04d-%02d-%02d %02d:%02d:%02d\t%u\t%u\t0\n", r.pin, y, mo, d, h,
		mi, s, r.state, r.verify);
	return (n > 0 && size_t(n) < out_size) ? size_t(n) : 0;
}

}  // namespace zk
