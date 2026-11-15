// The ERPNext site's /iclock/cdata endpoint, the same one push devices and the pull agent use.
#pragma once

#include <stddef.h>

// Handshake GET: marks the device online on the site. Returns the HTTP status (or < 0 on error).
int site_heartbeat(const char* serial);

// Posts ATTLOG lines. Returns the HTTP status (or < 0 on error).
int site_post_attlog(const char* serial, const char* body, size_t len);
