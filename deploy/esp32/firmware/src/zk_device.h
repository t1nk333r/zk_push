// A connection to a ZKTeco device over TCP 4370 (read-only use). Port of Device in zk_reference.py.
#pragma once

#include <Client.h>

#include "zk_protocol.h"

class ZkDevice {
   public:
	explicit ZkDevice(Client& client) : client_(client) {}

	bool connect(const char* host, uint16_t port, uint32_t comm_key);
	bool serial_number(char* out, size_t out_size);
	bool record_count(uint32_t& records, uint32_t& capacity);
	// Asks the device to build its log buffer; buffer_size = 4 + records * record size.
	bool prepare_log(uint32_t& buffer_size);
	// Records first..first+count-1 (oldest first) into out, count * RECORD_SIZE bytes.
	bool read_records(uint32_t first, uint32_t count, uint8_t* out);
	void free_log();
	void close();

	const char* error() const { return error_; }

   private:
	// Sends a command and reads the reply; the reply payload goes into dst (default: rx_).
	bool command(uint16_t cmd, const uint8_t* payload, size_t len, uint16_t& code, uint8_t* dst = nullptr,
		size_t dst_size = 0, size_t* got = nullptr);
	bool recv_packet(uint16_t& code, uint8_t* dst, size_t dst_size, size_t& got);
	bool read_chunk(uint32_t start, size_t size, uint8_t* dst);
	bool recv_exact(uint8_t* buf, size_t n);
	bool fail(const char* message);

	Client& client_;
	uint16_t session_ = 0;
	uint16_t reply_ = 65534;
	uint8_t rx_[1024];
	size_t rx_len_ = 0;
	const char* error_ = "";
};
