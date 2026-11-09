// Protocol unit tests, run on this computer: pio test -e native
// Expected values come from ../../zk_reference.py, which is verified against a real MB20-VL.
#include <string.h>
#include <unity.h>

#include "zk_protocol.h"

using namespace zk;

void setUp() {}
void tearDown() {}

static void test_checksum() {
	uint8_t connect[8];
	put_u16(connect, CMD_CONNECT);
	put_u16(connect + 2, 0);
	put_u16(connect + 4, 0);
	put_u16(connect + 6, 65534);
	TEST_ASSERT_EQUAL_UINT16(64535, checksum(connect, sizeof connect));

	uint8_t read[16];
	put_u16(read, CMD_READ_BUFFER);
	put_u16(read + 2, 0);
	put_u16(read + 4, 0x1234);
	put_u16(read + 6, 7);
	put_u32(read + 8, 511920);
	put_u32(read + 12, 200);
	TEST_ASSERT_EQUAL_UINT16(5988, checksum(read, sizeof read));

	const uint8_t odd[] = {1, 2, 3};
	TEST_ASSERT_EQUAL_UINT16(65018, checksum(odd, sizeof odd));
}

static void test_comm_key() {
	uint8_t out[4];
	comm_key(0, 0x5A3C, out);
	const uint8_t k0[] = {0x61, 0x7d, 0x32, 0x23};
	TEST_ASSERT_EQUAL_HEX8_ARRAY(k0, out, 4);
	comm_key(1234, 0x5A3C, out);
	const uint8_t k1[] = {0x41, 0x36, 0x32, 0x23};
	TEST_ASSERT_EQUAL_HEX8_ARRAY(k1, out, 4);
	comm_key(999999, 1, out);
	const uint8_t k2[] = {0x23, 0x81, 0x32, 0x89};
	TEST_ASSERT_EQUAL_HEX8_ARRAY(k2, out, 4);
}

static void test_time() {
	TEST_ASSERT_EQUAL_UINT32(863171231, encode_time(2026, 11, 9, 9, 47, 11));
	TEST_ASSERT_EQUAL_UINT32(2682450, encode_time(2000, 2, 1, 1, 7, 30));
	TEST_ASSERT_EQUAL_UINT32(835660800, encode_time(2026, 1, 1, 0, 0, 0));
	int y, mo, d, h, mi, s;
	decode_time(863171231, y, mo, d, h, mi, s);
	TEST_ASSERT_EQUAL_INT(2026, y);
	TEST_ASSERT_EQUAL_INT(11, mo);
	TEST_ASSERT_EQUAL_INT(9, d);
	TEST_ASSERT_EQUAL_INT(9, h);
	TEST_ASSERT_EQUAL_INT(47, mi);
	TEST_ASSERT_EQUAL_INT(11, s);
	// Monotonic: a "since" cut-off can be compared as a plain number.
	TEST_ASSERT_TRUE(encode_time(2025, 12, 31, 23, 59, 59) < encode_time(2026, 1, 1, 0, 0, 0));
}

static void test_build_frame() {
	uint8_t frame[32];
	const size_t n = build_frame(CMD_CONNECT, 0, 65534, nullptr, 0, frame, sizeof frame);
	// magic 0x5050 0x7D82, length 8, then command 1000, checksum 64535, session 0, reply 0
	const uint8_t expected[] = {0x50, 0x50, 0x82, 0x7D, 8, 0, 0, 0, 0xE8, 0x03, 0x17, 0xFC, 0, 0, 0, 0};
	TEST_ASSERT_EQUAL_size_t(sizeof expected, n);
	TEST_ASSERT_EQUAL_HEX8_ARRAY(expected, frame, sizeof expected);
	TEST_ASSERT_EQUAL_size_t(0, build_frame(CMD_CONNECT, 0, 0, nullptr, 0, frame, 10));
}

static void test_record_and_line() {
	const uint8_t raw[RECORD_SIZE] = {0x03, 0x00, '4', '0', '0', 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
		0, 0, 0, 0, 0, 0, 0x01, 0xf2, 0xe2, 0x72, 0x33, 0x00, 0, 0, 0, 0, 0, 0, 0, 0};
	Record r;
	TEST_ASSERT_TRUE(parse_record(raw, r));
	TEST_ASSERT_EQUAL_STRING("400", r.pin);
	TEST_ASSERT_EQUAL_UINT8(1, r.verify);
	TEST_ASSERT_EQUAL_UINT8(0, r.state);
	char line[64];
	const size_t n = format_attlog_line(r, line, sizeof line);
	TEST_ASSERT_EQUAL_STRING("400\t2026-11-09 08:40:18\t0\t1\t0\n", line);
	TEST_ASSERT_EQUAL_size_t(strlen(line), n);
	TEST_ASSERT_EQUAL_size_t(0, format_attlog_line(r, line, 10));  // too small: nothing
}

int main() {
	UNITY_BEGIN();
	RUN_TEST(test_checksum);
	RUN_TEST(test_comm_key);
	RUN_TEST(test_time);
	RUN_TEST(test_build_frame);
	RUN_TEST(test_record_and_line);
	return UNITY_END();
}
