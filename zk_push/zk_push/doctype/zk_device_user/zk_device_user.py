# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

import frappe
from frappe.model.document import Document


class ZKDeviceUser(Document):
	# Rows are written by zk_push.processing.store_users from the device's user list; read-only here.
	pass


def on_doctype_update():
	# Joined on by the unmatched-users report and looked up by store_users.
	frappe.db.add_index("ZK Device User", ["device", "pin"])
