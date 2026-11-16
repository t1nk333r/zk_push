// A connection to a ZKTeco device over TCP 4370 (read-only use). Port of Device in zk_reference.py.
#pragma once

#include <Client.h>

#include "zk_protocol.h"

class ZkDevice {
   public:
	explicit ZkDevice(Client& client) : client_(client) {}
	~ZkDevice() { release_inline_log(); }
	ZkDevice(const ZkDevice&) = delete;
	ZkDevice& operator=(const ZkDevice&) = delete;

	bool connect(const char* host, uint16_t port, uint32_t comm_key);
	bool serial_number(char* out, size_t out_size);
	bool record_count(uint32_t& records, uint32_t& capacity);
	// Asks the device to build its log buffer; buffer_size = 4 + records * record size.
	bool prepare_log(uint32_t& buffer_size);
	// The same for the user list: buffer_size = 4 + users * USER_RECORD_SIZE. One prepared buffer at a
	// time: free_log() before preparing the other.
	bool prepare_users(uint32_t& buffer_size);
	// Bytes of the prepared buffer at a plain offset (the u32 header is at 0), at most 4000 per call.
	// The user list is read this way; the attendance log has its own offsets, see read_records.
	bool read_bytes(uint32_t offset, size_t size, uint8_t* out);
	// Users on the device, from the last record_count().
	uint32_t user_count() const { return users_; }
	// Records first..first+count-1 (oldest first) into out, count * RECORD_SIZE bytes.
	bool read_records(uint32_t first, uint32_t count, uint8_t* out);
	void free_log();
	void close();

	const char* error() const { return error_; }
	// The last connect() failed at TCP level (no answer on the LAN at all).
	bool unreachable() const { return unreachable_; }

   private:
	// Sends a command and reads the reply; the reply payload goes into dst (default: rx_).
	bool command(uint16_t cmd, const uint8_t* payload, size_t len, uint16_t& code, uint8_t* dst = nullptr,
		size_t dst_size = 0, size_t* got = nullptr);
	bool recv_packet(uint16_t& code, uint8_t* dst, size_t dst_size, size_t& got);
	bool read_chunk(uint32_t start, size_t size, uint8_t* dst);
	bool prepare_buffer(uint16_t what, uint32_t fct, uint32_t& buffer_size);
	bool recv_exact(uint8_t* buf, size_t n);
	bool fail(const char* message);
	void release_inline_log();

	// Largest inline log kept on the heap (about 1,600 records); a bigger one fails the pull.
	static constexpr size_t INLINE_LOG_MAX = 64 * 1024;

	Client& client_;
	uint16_t session_ = 0;
	uint16_t reply_ = 65534;
	uint8_t rx_[1024];
	size_t rx_len_ = 0;
	bool inline_log_ = false;  // prepare_log returned the whole log (in rx_, or inline_buf_ if larger)
	bool accept_inline_log_ = false;  // only while prepare_log waits for its reply
	uint8_t* inline_buf_ = nullptr;
	size_t inline_len_ = 0;
	bool unreachable_ = false;
	uint32_t users_ = 0;
	const char* error_ = "";
};
