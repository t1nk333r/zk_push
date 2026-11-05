frappe.query_reports["ZK Unmatched Users"] = {
	filters: [
		{ fieldname: "device", label: __("Device"), fieldtype: "Link", options: "ZK Device" },
		{
			fieldname: "from_date",
			label: __("Punched Since"),
			fieldtype: "Date",
			description: __("Hide users who left long ago"),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (column.fieldname === "action" && data && data.pin) {
			return `<button class="btn btn-xs btn-primary zk-map-pin" data-device="${frappe.utils.escape_html(
				data.device
			)}" data-pin="${frappe.utils.escape_html(data.pin)}">${__("Map")}</button>`;
		}
		if (column.fieldname === "pin" && data && data.pin) {
			const url = `/app/zk-punch?device=${encodeURIComponent(data.device)}&pin=${encodeURIComponent(data.pin)}`;
			return `<a href="${url}">${frappe.utils.escape_html(data.pin)}</a>`;
		}
		return default_formatter(value, row, column, data);
	},
	onload(report) {
		report.page.add_inner_button(__("All Punches"), () => frappe.set_route("List", "ZK Punch"));
		$(report.page.wrapper)
			.off("click", ".zk-map-pin")
			.on("click", ".zk-map-pin", (e) => {
				const { device, pin } = e.currentTarget.dataset;
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
									message: __("Mapped. {0} punches queued for processing.", [r.message || 0]),
									indicator: "green",
								});
								setTimeout(() => report.refresh(), 3000);
							},
						});
					},
				});
				dialog.show();
			});
	},
};
