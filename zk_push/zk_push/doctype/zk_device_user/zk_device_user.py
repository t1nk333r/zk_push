from frappe.model.document import Document


class ZKDeviceUser(Document):
	# Rows are written by zk_push.processing.store_users from the device's user list; read-only here.
	pass
