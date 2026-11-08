const ZK_STATUS_COLORS = {
	Pending: "orange",
	Created: "green",
	Duplicate: "gray",
	Unmatched: "yellow",
	Skipped: "gray",
	Error: "red",
};

frappe.ui.form.on("ZK Device", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.trigger("show_summary");
		frm.trigger("setup_setup_hint");

		frm.add_custom_button(__("View Punches"), () => {
			frappe.set_route("List", "ZK Punch", { device: frm.doc.name });
		});
		frm.add_custom_button(__("Unmatched Users"), () => {
			frappe.set_route("query-report", "ZK Unmatched Users", { device: frm.doc.name });
		});

		frm.add_custom_button(
			__("Retry Unmatched / Error Punches"),
			() => {
				frappe.call({
					method: "zk_push.api.retry_punches",
					args: { device: frm.doc.name },
					callback(r) {
						frappe.show_alert({
							message: __("{0} punches queued for processing", [r.message || 0]),
							indicator: "green",
						});
						frm.reload_doc();
					},
				});
			},
			__("Actions")
		);

		frm.add_custom_button(
			__("Reset Log Stamp + Re-upload"),
			() => {
				frappe.confirm(
					__(
						"The device will resend its full attendance history. Already received punches are ignored. A CHECK command is queued so this happens without rebooting the device. Continue?"
					),
					() => {
						frappe.call({
							method: "zk_push.api.reset_log_stamp",
							args: { device: frm.doc.name },
							callback() {
								frappe.show_alert({
									message: __("Log stamp cleared and CHECK queued"),
									indicator: "green",
								});
								frm.reload_doc();
							},
						});
					}
				);
			},
			__("Actions")
		);

		for (const [label, command] of [
			[__("Request Device Info"), "INFO"],
			[__("Re-read Options (CHECK)"), "CHECK"],
			[__("Reboot Device"), "REBOOT"],
		]) {
			frm.add_custom_button(
				label,
				() => {
					if (command === "REBOOT") {
						frappe.confirm(__("Reboot the device? It will be offline for about a minute."), () =>
							queue_command(frm, command)
						);
					} else {
						queue_command(frm, command);
					}
				},
				__("Device Commands")
			);
		}
	},

	setup_setup_hint(frm) {
		if (frm.doc.enabled) return;
		frm.set_intro(
			frm.doc.import_from
				? __("This device is disabled: its punches are rejected. Tick Enabled to start receiving.")
				: __(
						"This device is disabled. Before enabling, set <b>Ignore Punches Before</b>: on first connect the device uploads its whole history."
				  ),
			"orange"
		);
	},

	show_summary(frm) {
		frappe.call({
			method: "zk_push.api.device_summary",
			args: { device: frm.doc.name },
			callback(r) {
				const s = r.message;
				if (!s) return;

				frm.dashboard.clear_headline();
				const connection = !frm.doc.enabled
					? [__("Disabled"), "gray"]
					: s.online
					? [__("Online"), "green"]
					: [__("Offline"), "red"];
				frm.dashboard.add_indicator(
					`${connection[0]}${
						frm.doc.last_seen
							? " · " + __("last seen {0}", [frappe.datetime.prettyDate(frm.doc.last_seen)])
							: ""
					}`,
					connection[1]
				);

				for (const status of Object.keys(ZK_STATUS_COLORS)) {
					if (s.counts[status]) {
						frm.dashboard.add_indicator(
							`${__(status)}: ${format_number(s.counts[status], null, 0)}`,
							ZK_STATUS_COLORS[status]
						);
					}
				}

				const notes = [];
				if (s.last_punch) {
					notes.push(
						__("Last punch: {0} by user {1} ({2})", [
							frappe.datetime.str_to_user(s.last_punch.punch_time),
							s.last_punch.employee_name || s.last_punch.pin,
							__(s.last_punch.status),
						])
					);
				}
				if (s.unmatched_users) {
					notes.push(
						`<a href="/app/query-report/ZK Unmatched Users?device=${encodeURIComponent(
							frm.doc.name
						)}">${__("{0} device users are not mapped to an employee", [s.unmatched_users])}</a>`
					);
				}
				if (s.counts.Error) {
					notes.push(
						`<a href="/app/zk-punch?device=${encodeURIComponent(frm.doc.name)}&status=Error">${__(
							"{0} punches failed",
							[s.counts.Error]
						)}</a>`
					);
				}
				if (s.pending_commands) {
					notes.push(__("{0} command(s) waiting for the device", [s.pending_commands]));
				}
				if (s.debug_logging) {
					notes.push(__("Debug logging is on: every request is recorded in ZK Request Log."));
				}
				if (notes.length) {
					frm.dashboard.set_headline(notes.join(" · "), s.counts.Error ? "red" : "blue");
				}
			},
		});
	},
});

function queue_command(frm, command) {
	frappe.call({
		method: "zk_push.api.queue_command",
		args: { device: frm.doc.name, command },
		callback() {
			frappe.show_alert({
				message: __("{0} queued; the device picks it up on its next poll (a few seconds when online)", [
					command,
				]),
				indicator: "green",
			});
			frm.reload_doc();
		},
	});
}
