// One pull: read the device's new records and post them to the site.
#pragma once

enum class SyncResult {
	Ok,
	Failed,           // device or site problem a restart would not fix; progress up to the last accepted upload is kept
	SiteUnreachable,  // the site could not be reached at all (DNS, TCP, TLS, timeout)
	DeviceUnreachable,  // no TCP connection to the device: device off, or a stuck Wi-Fi stack
};

SyncResult run_sync();
