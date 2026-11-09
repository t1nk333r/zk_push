// One pull: read the device's new records and post them to the site.
#pragma once

// Returns false if the pull did not complete; progress up to the last accepted upload is kept.
bool run_sync();
