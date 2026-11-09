// One pull: read the device's new records and post them to the site.
#pragma once

enum class SyncResult {
	Ok,
	Failed,           // device or site problem a restart would not fix; progress up to the last accepted upload is kept
	SiteUnreachable,  // the site could not be reached at all (DNS, TCP, TLS, timeout)
};

SyncResult run_sync();
