import frappe
from frappe import _
from frappe.model.document import Document

from zk_push import protocol

# Written by the device exchange with update_modified=False, so Frappe's "document was modified"
# check never fires. Always keep the stored values, so saving a form opened earlier cannot roll
# them back (an old att_stamp makes the device resend its log).
SERVER_FIELDS = ("last_seen", "last_ip", "push_version", "device_info", "att_stamp")


class ZKDevice(Document):
	def validate(self):
		self.serial_no = (self.serial_no or "").strip()
		if not protocol.is_valid_sn(self.serial_no):
			frappe.throw(_("Serial Number must be 6-32 letters or digits."))
		if not self.is_new():
			self.update(frappe.db.get_value("ZK Device", self.name, SERVER_FIELDS, as_dict=True) or {})
		if not self.device_name:
			self.device_name = self.serial_no
		if self.enabled:
			self.auto_registered = 0
