// Copyright (c) 2026 Abdulrahman Aloqayli
// SPDX-License-Identifier: MIT
// The ERPNext site's /iclock/cdata endpoint, the same one push devices and the pull agent use.
#pragma once

#include <stddef.h>

// Handshake GET: marks the device online on the site. Returns the HTTP status (or < 0 on error).
int site_heartbeat(const char* serial);

// Posts lines for a table: "ATTLOG" (punches) or "OPERLOG" (USER lines). Returns the HTTP status (or
// < 0 on error); 0 if the site answered 200 without its "OK".
int site_post(const char* serial, const char* table, const char* body, size_t len);
