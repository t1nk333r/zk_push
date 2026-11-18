import frappe
from frappe import _
from frappe.model.document import Document

from zk_push import protocol

# What HR Managers may send, plus re-uploading punches for a time range; anything else (changing
# or deleting data on the device) needs System Manager.
SAFE_COMMANDS = ("CHECK", "INFO", "LOG", "REBOOT")
# Status is only changed by the device exchange (via db.set_value). Read-only in the form is not
# enforced on the server, so without this a REST update could set a finished REBOOT back to Pending.
LOCKED_FIELDS = ("device", "command", "status")


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
		self.status = "Pending"
