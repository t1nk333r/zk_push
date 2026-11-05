const ZK_PUNCH_INDICATORS = {
	Pending: "orange",
	Created: "green",
	Duplicate: "gray",
	Unmatched: "yellow",
	Skipped: "gray",
	Error: "red",
};

frappe.listview_settings["ZK Punch"] = {
	add_fields: ["status", "error"],
	get_indicator(doc) {
		return [__(doc.status), ZK_PUNCH_INDICATORS[doc.status] || "gray", "status,=," + doc.status];
	},
	onload(listview) {
		listview.page.add_action_item(__("Retry Selected"), () => {
			const names = listview.get_checked_items(true);
			frappe.call({
				method: "zk_push.api.retry_punches",
				args: { names },
				callback(r) {
					frappe.show_alert({
						message: __("{0} punches queued (Created / Duplicate punches are left as they are)", [
							r.message || 0,
						]),
						indicator: "green",
					});
					setTimeout(() => listview.refresh(), 3000);
				},
			});
		});

		listview.page.add_inner_button(__("Unmatched Users"), () =>
			frappe.set_route("query-report", "ZK Unmatched Users")
		);
	},
};
