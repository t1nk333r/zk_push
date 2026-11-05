import frappe
from frappe import _
from frappe.model.document import Document

from zk_push import protocol


class ZKDevice(Document):
	def validate(self):
		self.serial_no = (self.serial_no or "").strip()
		if not protocol.is_valid_sn(self.serial_no):
			frappe.throw(_("Serial Number must be 6-32 letters or digits."))
		if not self.device_name:
			self.device_name = self.serial_no
		if self.enabled:
			self.auto_registered = 0
