frappe.query_reports["ZK Time Card"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_start(),
		},
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.get_today() },
		{ fieldname: "employee", label: __("Employee"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{
			fieldname: "overtime_after",
			label: __("OT Starts After (min)"),
			fieldtype: "Int",
			default: 60,
			description: __("Staying past off duty counts as OT, from off duty, once it reaches this many minutes"),
		},
		{
			fieldname: "missing_punch",
			label: __("Missing Punch Counts As (min)"),
			fieldtype: "Int",
			default: 60,
			description: __("A missing clock in or out counts as this many minutes Late / Early"),
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "actual_attended" && data && data.actual_attended) return `<b>${value}</b>`;
		if (["late", "early"].includes(column.fieldname) && data && data[column.fieldname]) {
			return `<span style="color: var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
