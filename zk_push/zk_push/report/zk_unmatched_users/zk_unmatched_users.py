import frappe
from frappe import _


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["status = 'Unmatched'"]
	if filters.device:
		conditions.append("device = %(device)s")
	if filters.from_date:
		conditions.append("punch_time >= %(from_date)s")

	data = frappe.db.sql(
		f"""
		select device, pin, count(*) as punches, min(punch_time) as first_punch, max(punch_time) as last_punch
		from `tabZK Punch`
		where {" and ".join(conditions)}
		group by device, pin
		order by last_punch desc
		""",
		filters,
		as_dict=True,
	)
	for row in data:
		row["action"] = ""

	columns = [
		{"fieldname": "device", "label": _("Device"), "fieldtype": "Link", "options": "ZK Device", "width": 160},
		{"fieldname": "pin", "label": _("Device User ID"), "fieldtype": "Data", "width": 120},
		{"fieldname": "punches", "label": _("Unmatched Punches"), "fieldtype": "Int", "width": 140},
		{"fieldname": "first_punch", "label": _("First Punch"), "fieldtype": "Datetime", "width": 170},
		{"fieldname": "last_punch", "label": _("Last Punch"), "fieldtype": "Datetime", "width": 170},
		{"fieldname": "action", "label": _("Action"), "fieldtype": "Data", "width": 110},
	]
	return columns, data
