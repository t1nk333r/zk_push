// Copyright (c) 2026 Abdulrahman Aloqayli
// SPDX-License-Identifier: MIT
frappe.ui.form.on("ZK Push Settings", {
	refresh(frm) {
		// The time zone names Frappe knows, as the field's choices (as in System Settings).
		frappe
			.xcall("zk_push.timezones.names")
			.then((names) => frm.set_df_property("device_time_zone", "options", names));
	},
});
