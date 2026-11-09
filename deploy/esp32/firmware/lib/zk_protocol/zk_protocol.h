// ZKTeco TCP protocol: the pure parts (no I/O, no Arduino), so they can be unit-tested on a PC.
// A port of ../../zk_reference.py; see the protocol summary there.
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace zk {

constexpr uint16_t CMD_CONNECT = 1000;
constexpr uint16_t CMD_EXIT = 1001;
constexpr uint16_t CMD_AUTH = 1102;
constexpr uint16_t CMD_OPTIONS_RRQ = 11;
constexpr uint16_t CMD_ATTLOG_RRQ = 13;
constexpr uint16_t CMD_GET_FREE_SIZES = 50;
constexpr uint16_t CMD_PREPARE_BUFFER = 1503;
constexpr uint16_t CMD_READ_BUFFER = 1504;
constexpr uint16_t CMD_FREE_DATA = 1502;
constexpr uint16_t CMD_PREPARE_DATA = 1500;
constexpr uint16_t CMD_DATA = 1501;
constexpr uint16_t ACK_OK = 2000;
constexpr uint16_t ACK_UNAUTH = 2005;

constexpr uint16_t FRAME_MAGIC_1 = 0x5050;
constexpr uint16_t FRAME_MAGIC_2 = 0x7D82;
constexpr size_t FRAME_HEADER_SIZE = 8;   // magic1, magic2, u32 length
constexpr size_t PACKET_HEADER_SIZE = 8;  // command, checksum, session, reply
constexpr size_t RECORD_SIZE = 40;        // ATTLOG record on ZMM510-class firmware
constexpr size_t PIN_MAX = 24;

struct Record {
	char pin[PIN_MAX + 1];
	uint8_t verify;
	uint8_t state;
	uint32_t time;  // device-encoded time, see encode_time()
};

inline uint16_t get_u16(const uint8_t* p) { return p[0] | (p[1] << 8); }
inline uint32_t get_u32(const uint8_t* p) {
	return p[0] | (p[1] << 8) | (p[2] << 16) | (uint32_t(p[3]) << 24);
}
inline void put_u16(uint8_t* p, uint16_t v) { p[0] = v; p[1] = v >> 8; }
inline void put_u32(uint8_t* p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }

// Checksum over a packet whose checksum field is 0 (zkemsdk.c).
uint16_t checksum(const uint8_t* data, size_t len);

// Scrambled Comm Key sent with CMD_AUTH (commpro.c MakeKey).
void comm_key(uint32_t key, uint16_t session_id, uint8_t out[4], uint8_t ticks = 50);

// Device time encoding; monotonic, so encoded values can be compared directly.
uint32_t encode_time(int year, int month, int day, int hour, int minute, int second);
void decode_time(uint32_t value, int& year, int& month, int& day, int& hour, int& minute, int& second);

// Builds a full frame (frame header + packet) into out; returns its length (0 if out is too small).
size_t build_frame(uint16_t command, uint16_t session_id, uint16_t reply_id, const uint8_t* payload,
	size_t payload_len, uint8_t* out, size_t out_size);

bool parse_record(const uint8_t* raw, Record& record);

// The site's ATTLOG line, "PIN\tYYYY-MM-DD HH:MM:SS\tstate\tverify\t0\n"; returns its length.
size_t format_attlog_line(const Record& record, char* out, size_t out_size);

}  // namespace zk
