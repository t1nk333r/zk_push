// Copyright (c) 2026 t1nk333r
// SPDX-License-Identifier: MIT
const ZK_DEVICE_STATUS = {
	0: __("Check-In"),
	1: __("Check-Out"),
	2: __("Break-Out"),
	3: __("Break-In"),
	4: __("Overtime-In"),
	5: __("Overtime-Out"),
};
const ZK_VERIFY_TYPE = {
	0: __("Password"),
	1: __("Fingerprint"),
	2: __("Card"),
	3: __("Password"),
	4: __("Card"),
	15: __("Face"),
	25: __("Palm"),
};

// The roles zk_push.api checks for Retry and Map; HR Users can read punches but not change them.
const ZK_MANAGER_ROLES = ["System Manager", "HR Manager"];

function zk_reload_later(frm) {
	const name = frm.doc.name;
	setTimeout(() => {
		if (cur_frm === frm && frm.doc.name === name) frm.reload_doc();
	}, 3000);
}

frappe.ui.form.on("ZK Punch", {
	refresh(frm) {
		frm.trigger("explain_status");
		const manager = frappe.user.has_role(ZK_MANAGER_ROLES);

		if (manager && ["Unmatched", "Error", "Skipped"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Retry"), () => {
				frappe.call({
					method: "zk_push.api.retry_punches",
					args: { names: [frm.doc.name] },
					callback() {
						frappe.show_alert({ message: __("Queued for processing"), indicator: "green" });
						zk_reload_later(frm);
					},
				});
			});
		}

		if (manager && frm.doc.status === "Unmatched") {
			frm.add_custom_button(__("Map to Employee"), () =>
				map_pin_dialog(frm.doc.device, frm.doc.pin, () => zk_reload_later(frm))
			).addClass("btn-primary");
		}

		frm.add_custom_button(
			__("Other Punches of User {0}", [frappe.utils.escape_html(frm.doc.pin)]),
			() => frappe.set_route("List", "ZK Punch", { device: frm.doc.device, pin: frm.doc.pin }),
			__("View")
		);
		if (!manager) return;  // ZK Request Log is for managers only
		frm.add_custom_button(
			__("Device Requests Around This Time"),
			() =>
				frappe.set_route("List", "ZK Request Log", {
					device: frm.doc.device,
					creation: [
						"between",
						[
							frappe.datetime.add_days(frm.doc.creation, -1).slice(0, 10),
							frappe.datetime.add_days(frm.doc.creation, 1).slice(0, 10),
						],
					],
				}),
			__("View")
		);
	},

	explain_status(frm) {
		const doc = frm.doc;
		const parts = [];
		if (doc.device_status !== null && doc.device_status !== undefined) {
			parts.push(__("Key: {0}", [ZK_DEVICE_STATUS[doc.device_status] || doc.device_status]));
		}
		if (doc.verify_type !== null && doc.verify_type !== undefined) {
			parts.push(__("Verified by: {0}", [ZK_VERIFY_TYPE[doc.verify_type] || doc.verify_type]));
		}

		const messages = {
			Pending: [__("Waiting for the background job. If this stays for more than a few minutes, check that the workers are running."), "orange"],
			Created: [__("Employee Checkin created."), "green"],
			Duplicate: [__("A checkin for this employee at this exact time already existed, so nothing was created."), "blue"],
			Unmatched: [__("No employee has Attendance Device ID {0}. Use Map to Employee, or set it on the Employee.", [frappe.utils.escape_html(doc.pin)]), "yellow"],
			Skipped: [__("Older than the device's Ignore Punches Before date. Retry after changing that date to import it."), "blue"],
			Error: [__("HRMS rejected the checkin: {0}", [frappe.utils.escape_html(doc.error || "?")]), "red"],
		};
		const [message, color] = messages[doc.status] || ["", "blue"];
		frm.set_intro([message, parts.join(" · ")].filter(Boolean).join("<br>"), color);
	},
});

function map_pin_dialog(device, pin, after) {
	const dialog = new frappe.ui.Dialog({
		title: __("Map device user {0} to an employee", [pin]),
		fields: [
			{ fieldname: "employee", fieldtype: "Link", options: "Employee", label: __("Employee"), reqd: 1 },
			{
				fieldname: "overwrite",
				fieldtype: "Check",
				label: __("Overwrite the employee's existing Attendance Device ID"),
			},
		],
		primary_action_label: __("Map and Process Punches"),
		primary_action(values) {
			frappe.call({
				method: "zk_push.api.map_pin",
				args: { device, pin, employee: values.employee, overwrite: values.overwrite ? 1 : 0 },
				freeze: true,
				callback(r) {
					dialog.hide();
					frappe.show_alert({
						message: __("Mapped. {0} waiting punches queued for processing.", [r.message || 0]),
						indicator: "green",
					});
					after && after();
				},
			});
		},
	});
	dialog.show();
}
