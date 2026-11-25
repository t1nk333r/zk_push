// Copyright (c) 2026 t1nk333r
// SPDX-License-Identifier: MIT
// Keep in sync with zk_push.api.ONLINE_WINDOW_MINUTES
const ZK_ONLINE_WINDOW_MINUTES = 5;

frappe.listview_settings["ZK Device"] = {
	add_fields: ["enabled", "last_seen", "auto_registered"],
	get_indicator(doc) {
		if (!doc.enabled) {
			return doc.auto_registered
				? [__("New - Not Enabled"), "orange", "enabled,=,0"]
				: [__("Disabled"), "gray", "enabled,=,0"];
		}
		const online =
			doc.last_seen &&
			// last_seen is in the system time zone, not the browser's: compare it with "now" in that zone,
			// both read the same way (system_datetime() is a string; with true it is a Date, not a moment).
			moment(frappe.datetime.system_datetime(), frappe.defaultDatetimeFormat).diff(
				moment(doc.last_seen, frappe.defaultDatetimeFormat),
				"minutes"
			) < ZK_ONLINE_WINDOW_MINUTES;
		return online ? [__("Online"), "green", "enabled,=,1"] : [__("Offline"), "red", "enabled,=,1"];
	},
};
