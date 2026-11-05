const ZK_COMMAND_INDICATORS = { Pending: "orange", Sent: "blue", Done: "green", Failed: "red", Cancelled: "gray" };

frappe.listview_settings["ZK Device Command"] = {
	get_indicator(doc) {
		return [__(doc.status), ZK_COMMAND_INDICATORS[doc.status] || "gray", "status,=," + doc.status];
	},
};
