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
			moment().diff(frappe.datetime.str_to_obj(doc.last_seen), "minutes") < ZK_ONLINE_WINDOW_MINUTES;
		return online ? [__("Online"), "green", "enabled,=,1"] : [__("Offline"), "red", "enabled,=,1"];
	},
};
