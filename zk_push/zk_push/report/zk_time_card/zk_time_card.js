// The desk does not send filters that are 0 or unticked; the server reads a missing number as 0.
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
		{ fieldname: "overtime_after", label: __("OT Starts After (min)"), fieldtype: "Int", default: 60 },
		{ fieldname: "missing_punch", label: __("Missing Punch Counts As (min)"), fieldtype: "Int", default: 60 },
		{ fieldname: "in_minutes", label: __("Show Durations in Minutes"), fieldtype: "Check" },
		{
			fieldname: "monthly_salary",
			label: __("Monthly Salary (basic + allowances)"),
			fieldtype: "Currency",
			depends_on: "employee",
		},
		{
			fieldname: "gosi_wage",
			label: __("GOSI Wage: basic + housing (blank: the salary)"),
			fieldtype: "Currency",
			depends_on: "monthly_salary",
		},
		{
			fieldname: "gosi_percent",
			label: __("GOSI Share % (9.75 / 10.75 for new since Jul 2024 / 0 non-Saudi)"),
			fieldtype: "Float",
			default: 9.75,
			depends_on: "monthly_salary",
		},
		{
			fieldname: "hours_per_day",
			label: __("Hours per Day (for the hourly rate)"),
			fieldtype: "Float",
			default: 8,
			depends_on: "monthly_salary",
		},
		{
			fieldname: "deduct_late_early",
			label: __("Deduct Late / Early Minutes"),
			fieldtype: "Check",
			default: 1,
			depends_on: "monthly_salary",
		},
		{
			fieldname: "pay_overtime",
			label: __("Pay OT (Labor Law: hourly wage + 50% of basic)"),
			fieldtype: "Check",
			depends_on: "monthly_salary",
		},
		{
			fieldname: "basic_salary",
			label: __("Basic Salary for OT (blank: the salary)"),
			fieldtype: "Currency",
			depends_on: "pay_overtime",
		},
	],
	onload(report) {
		report.page.add_inner_button(__("Excel Time Card"), () => {
			// Same filters as on screen; one sheet per employee, ready to print.
			open_url_post("/api/method/zk_push.zk_push.report.zk_time_card.time_card_excel.download", {
				filters: JSON.stringify(report.get_filter_values()),
			});
		});
	},
	formatter(value, row, column, data, default_formatter) {
		// Days flagged for review (one punch, short hours, no shift): status and notice in orange.
		if (["status", "notice"].includes(column.fieldname) && data && data.notice) {
			const text = frappe.utils.escape_html(data[column.fieldname] || "");
			const mark = column.fieldname === "status" ? " ⚠" : "";
			return `<span style="color: var(--orange-600); font-weight: 600">${text}${mark}</span>`;
		}
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "actual_attended" && data && data.actual_attended) return `<b>${value}</b>`;
		if (["late", "early"].includes(column.fieldname) && data && data[column.fieldname]) {
			return `<span style="color: var(--red-500)">${value}</span>`;
		}
		return value;
	},
};
