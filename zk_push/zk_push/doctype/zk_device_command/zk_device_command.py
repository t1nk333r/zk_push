import frappe
from frappe import _
from frappe.model.document import Document

SAFE_COMMANDS = ("CHECK", "INFO", "REBOOT")


class ZKDeviceCommand(Document):
	def validate(self):
		self.command = (self.command or "").strip()
		if "\n" in self.command or "\r" in self.command:
			frappe.throw(_("Command must be a single line."))
		if self.command not in SAFE_COMMANDS and "System Manager" not in frappe.get_roles():
			frappe.throw(_("Only System Managers can send custom commands."))
		if self.is_new():
			self.status = "Pending"
