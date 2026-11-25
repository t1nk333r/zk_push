# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

import re

import frappe
from frappe import _
from frappe.model.document import Document

from zk_push import protocol

# What HR Managers may send, plus re-uploading punches for a time range; anything else (changing
# or deleting data on the device) needs System Manager.
SAFE_COMMANDS = ("CHECK", "INFO", "LOG", "REBOOT")
# Status and the result are only changed by the device exchange (via db.set_value). Read-only in the
# form is not enforced on the server, so without this a REST update could set a finished REBOOT back
# to Pending, or rewrite what the device reported.
LOCKED_FIELDS = ("device", "command", "status", "sent_at", "completed_at", "return_code", "response")
# Add / Update User carries the device user's password; only System Managers may read those commands.
SECRET_MARKER = "Passwd="
# A password or card number with a value, in a USERINFO line.
CREDENTIAL_RE = re.compile(r"(?:^|\s)(?:Passwd|Card)=\S")


class ZKDeviceCommand(Document):
	def validate(self):
		if not self.is_new():
			before = self.get_doc_before_save()
			if before and any(before.get(f) != self.get(f) for f in LOCKED_FIELDS):
				frappe.throw(_("A queued command cannot be changed. Queue a new command instead."))
			return

		self.command = (self.command or "").strip()
		if "\n" in self.command or "\r" in self.command:
			frappe.throw(_("Command must be a single line."))
		safe = self.command in SAFE_COMMANDS or protocol.is_reupload_command(self.command)
		if not safe and "System Manager" not in frappe.get_roles():
			frappe.throw(_("Only System Managers can send custom commands."))
		if CREDENTIAL_RE.search(self.command) and not (
			frappe.db.get_value("ZK Device", self.device, "allowed_ips") or ""
		).strip():
			# Whoever knows the serial can poll for it from anywhere and get the password or card number.
			frappe.throw(
				_(
					"A password or card number is only sent to a device with Allowed IP Addresses. "
					"Fill them in on {0}."
				).format(self.device)
			)
		self.status = "Pending"


def _system_manager(user=None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def get_permission_query_conditions(user=None):
	if _system_manager(user):
		return ""
	# A row filter, so a filter on the command (e.g. like '%Passwd=1%') cannot find them either.
	return f"locate({frappe.db.escape(SECRET_MARKER)}, `tabZK Device Command`.`command`) = 0"


def has_permission(doc, ptype=None, user=None):
	if SECRET_MARKER in (doc.command or "") and not _system_manager(user):
		return False
	return None
