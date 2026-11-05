frappe.listview_settings["ZK Request Log"] = {
	add_fields: ["response_status"],
	get_indicator(doc) {
		const status = doc.response_status;
		const color = status >= 500 ? "red" : status >= 400 ? "orange" : "green";
		return [String(status), color, "response_status,=," + status];
	},
};
