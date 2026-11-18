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

		for (const item of device_commands()) {
			if (item.admin && !frappe.user.has_role("System Manager")) continue;
			frm.add_custom_button(item.label, () => run_device_command(frm, item), __("Device Commands"));
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
		// Refreshes can overlap (save, then reload_doc): only the latest answer is drawn.
		const request = (frm.zk_summary_request = (frm.zk_summary_request || 0) + 1);
		const device = frm.doc.name;
		frappe.call({
			method: "zk_push.api.device_summary",
			args: { device },
			callback(r) {
				const s = r.message;
				if (!s || request !== frm.zk_summary_request || frm.doc.name !== device) return;

				frm.dashboard.clear_headline();
				frm.dashboard.stats_area_row.empty();
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
							frappe.utils.escape_html(s.last_punch.employee_name || s.last_punch.pin),
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

// The common push commands; the server builds the command line from these values
// (zk_push.protocol.device_command). admin: changes or deletes data on the device, System Manager only.
function device_commands() {
	const pin = { fieldname: "pin", fieldtype: "Data", label: __("Device User ID"), reqd: 1 };
	return [
		{ action: "info", label: __("Request Device Info") },
		{ action: "log", label: __("Upload New Punches") },
		{ action: "check", label: __("Re-read Options (CHECK)") },
		{
			action: "reupload",
			label: __("Re-upload Punches for Dates..."),
			fields: [
				{ fieldname: "from_time", fieldtype: "Datetime", label: __("From"), reqd: 1 },
				{ fieldname: "to_time", fieldtype: "Datetime", label: __("To"), reqd: 1 },
			],
			description: __("The device sends its punches in this range again; ones already received are ignored."),
		},
		{
			action: "reboot",
			label: __("Reboot Device"),
			confirm: __("Reboot the device? It will be offline for about a minute."),
		},
		{
			action: "set_user",
			label: __("Add / Update User on Device..."),
			admin: true,
			fields: [
				pin,
				{ fieldname: "name", fieldtype: "Data", label: __("Name"), reqd: 1, length: 24 },
				{ fieldname: "admin", fieldtype: "Check", label: __("Device Administrator") },
				{ fieldname: "card", fieldtype: "Data", label: __("Card Number") },
				{ fieldname: "password", fieldtype: "Password", label: __("Password (digits)") },
			],
			description: __(
				"Replaces this user's name, privilege, card and password on the device; fingerprints and faces are kept."
			),
		},
		{
			action: "enroll_fingerprint",
			label: __("Enroll Fingerprint..."),
			admin: true,
			fields: [
				pin,
				{
					fieldname: "finger",
					fieldtype: "Select",
					label: __("Finger"),
					options: "0\n1\n2\n3\n4\n5\n6\n7\n8\n9",
					default: "6",
					description: __("0-4: left little to thumb, 5-9: right thumb to little (6 = right index)"),
				},
			],
			description: __("The device asks the person to place the finger three times."),
		},
		{
			action: "delete_user",
			label: __("Delete User from Device..."),
			admin: true,
			fields: [pin],
			description: __("Removes the user and their fingerprints, face and card from the device."),
		},
		{
			action: "open_door",
			label: __("Open Door"),
			admin: true,
			confirm: __("Release the door lock now? (access-control models only)"),
		},
		{
			action: "clear_log",
			label: __("Clear Attendance Log..."),
			admin: true,
			confirm: __(
				"Delete every punch stored on the device? Punches not yet received here are lost for good. Check the device is up to date first."
			),
		},
	];
}

function run_device_command(frm, item) {
	const send = (values) =>
		frappe.call({
			method: "zk_push.api.queue_device_command",
			args: { device: frm.doc.name, action: item.action, ...values },
			callback() {
				frappe.show_alert({
					message: __("{0} queued; the device picks it up on its next poll (a few seconds when online)", [
						item.label.replace(/\.\.\.$/, ""),
					]),
					indicator: "green",
				});
				frm.reload_doc();
			},
		});

	if (item.fields) {
		const dialog = new frappe.ui.Dialog({
			title: item.label.replace(/\.\.\.$/, ""),
			fields: [
				...(item.description ? [{ fieldtype: "HTML", options: `<p class="text-muted">${item.description}</p>` }] : []),
				...item.fields,
			],
			primary_action_label: __("Queue"),
			primary_action(values) {
				dialog.hide();
				send(values);
			},
		});
		dialog.show();
	} else if (item.confirm) {
		frappe.confirm(item.confirm, () => send({}));
	} else {
		send({});
	}
}
