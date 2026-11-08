import json

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from zk_push import iclock, processing

MANAGER_ROLES = ("System Manager", "HR Manager")
ONLINE_WINDOW_MINUTES = 5


# Alternative entry points for the device protocol, for setups that prefer an nginx rewrite
# (/iclock/<x> -> /api/method/zk_push.api.<x>) over the built-in /iclock page renderer.
@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def cdata(**kwargs):
	return iclock.handle("cdata")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def getrequest(**kwargs):
	return iclock.handle("getrequest")


@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def devicecmd(**kwargs):
	return iclock.handle("devicecmd")


@frappe.whitelist(methods=["POST"])
def retry_punches(device: str | None = None, pin: str | None = None, names: str | list | None = None) -> int:
	frappe.only_for(MANAGER_ROLES)
	if isinstance(names, str):
		names = json.loads(names)
	return processing.requeue(device=device, pin=pin, names=names, any_status=names is not None)


@frappe.whitelist(methods=["POST"])
def map_pin(device: str, pin: str, employee: str, overwrite: int | bool = 0) -> int:
	frappe.only_for(MANAGER_ROLES)
	return processing.map_pin(device, pin, employee, overwrite=bool(int(overwrite)))


@frappe.whitelist(methods=["POST"])
def queue_command(device: str, command: str) -> int:
	frappe.only_for(MANAGER_ROLES)
	doc = frappe.get_doc({"doctype": "ZK Device Command", "device": device, "command": command})
	doc.insert()
	return doc.name


@frappe.whitelist(methods=["POST"])
def reset_log_stamp(device: str) -> int:
	"""Clear the device's ATTLOGStamp and queue CHECK, so it resends its full log without a reboot."""
	frappe.only_for(MANAGER_ROLES)
	frappe.db.set_value("ZK Device", device, "att_stamp", None, update_modified=False)
	return queue_command(device, "CHECK")


@frappe.whitelist()
def device_summary(device: str) -> dict:
	frappe.has_permission("ZK Device", doc=device, throw=True)
	values = frappe.db.get_value(
		"ZK Device", device, ["enabled", "last_seen", "debug_logging"], as_dict=True
	)
	counts = dict(
		frappe.get_all(
			"ZK Punch",
			filters={"device": device},
			fields=["status", "count(name) as count"],
			group_by="status",
			as_list=True,
		)
	)
	last_punch = frappe.db.get_value(
		"ZK Punch", {"device": device}, ["punch_time", "pin", "status", "employee_name"],
		order_by="punch_time desc", as_dict=True,
	)  # fmt: skip
	unmatched_users = frappe.db.sql(
		"select count(distinct pin) from `tabZK Punch` where device=%s and status='Unmatched'", device
	)[0][0]
	pending_commands = frappe.db.count(
		"ZK Device Command", {"device": device, "status": ("in", ("Pending", "Sent"))}
	)
	return {
		"online": is_online(values.last_seen),
		"enabled": values.enabled,
		"debug_logging": values.debug_logging,
		"counts": counts,
		"last_punch": last_punch,
		"unmatched_users": unmatched_users,
		"pending_commands": pending_commands,
	}


def is_online(last_seen) -> bool:
	return bool(last_seen) and get_datetime(last_seen) >= add_to_date(
		now_datetime(), minutes=-ONLINE_WINDOW_MINUTES
	)
