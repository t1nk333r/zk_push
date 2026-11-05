import frappe
from frappe.model.document import Document


class ZKPunch(Document):
	# Rows are written in bulk by zk_push.processing; this controller is intentionally empty.
	pass


def on_doctype_update():
	# Used by the processing job (pending, oldest first) and the unmatched-users report.
	frappe.db.add_index("ZK Punch", ["status", "punch_time"])
	frappe.db.add_index("ZK Punch", ["device", "pin"])
