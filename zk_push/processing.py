"""Turn stored ZK Punch rows into Employee Checkins (runs in a background job)."""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime

from zk_push import protocol

JOB_ID = "zk_push:process_pending"
BATCH_SIZE = 2000
RETRYABLE_STATUSES = ("Unmatched", "Error")
REQUEST_LOG_RETENTION_DAYS = 7
COMMAND_RETENTION_DAYS = 90


def store_punches(device: str, punches: list[protocol.Punch], branch: str | None = None):
	"""Fast insert of raw punches as Pending. Re-sent punches are ignored (deterministic name)."""
	if not punches:
		return
	now = now_datetime()
	fields = [
		"name", "creation", "modified", "owner", "modified_by", "docstatus",
		"device", "branch", "pin", "punch_time", "device_status", "verify_type", "workcode", "raw",
		"status", "attempts",
	]  # fmt: skip
	values = [
		(
			protocol.punch_name(device, p.pin, p.time),
			now, now, "Administrator", "Administrator", 0,
			device, branch, p.pin, p.time, p.status, p.verify, p.workcode, p.raw,
			"Pending", 0,
		)  # fmt: skip
		for p in punches
	]
	frappe.db.bulk_insert("ZK Punch", fields, values, ignore_duplicates=True)


def enqueue_processing():
	frappe.enqueue(
		"zk_push.processing.process_pending",
		queue="short",
		job_id=JOB_ID,
		deduplicate=True,
		enqueue_after_commit=True,
		now=frappe.flags.in_test,
	)


def process_pending():
	names = frappe.get_all(
		"ZK Punch",
		filters={"status": "Pending"},
		order_by="punch_time asc",
		limit=BATCH_SIZE,
		pluck="name",
	)
	context = _Context()
	for name in names:
		_process_one(name, context)

	if len(names) == BATCH_SIZE and not frappe.flags.in_test:
		# More waiting (e.g. the initial upload of the device's history).
		frappe.enqueue("zk_push.processing.process_pending", queue="short", enqueue_after_commit=True)


def requeue(
	device: str | None = None, pin: str | None = None, names: list[str] | None = None, any_status=False
) -> int:
	"""Send punches back through processing (after fixing the cause).

	By default only Unmatched / Error punches; with names + any_status, the given punches
	are re-evaluated whatever their status (Created / Duplicate punches are never redone).
	"""
	filters = {"status": ("in", RETRYABLE_STATUSES + (("Skipped",) if any_status else ()))}
	if device:
		filters["device"] = device
	if pin:
		filters["pin"] = pin
	if names is not None:
		if not names:
			return 0
		filters["name"] = ("in", names)

	count = frappe.db.count("ZK Punch", filters)
	if count:
		frappe.db.set_value("ZK Punch", filters, {"status": "Pending", "error": None})
		enqueue_processing()
	return count


def map_pin(device: str, pin: str, employee: str, overwrite: bool = False) -> int:
	"""Set the employee's Attendance Device ID to `pin` and process their waiting punches."""
	other = frappe.get_all(
		"Employee",
		filters={"attendance_device_id": pin, "name": ("!=", employee)},
		fields=["name", "employee_name"],
	)
	if other:
		frappe.throw(
			_("Device user {0} is already mapped to {1} ({2}).").format(
				pin, other[0].name, other[0].employee_name
			)
		)

	doc = frappe.get_doc("Employee", employee)
	if doc.attendance_device_id and doc.attendance_device_id != pin and not overwrite:
		frappe.throw(
			_("{0} already has Attendance Device ID {1}. Tick Overwrite to replace it.").format(
				doc.employee_name, doc.attendance_device_id
			)
		)

	waiting = frappe.db.count("ZK Punch", {"pin": pin, "status": ("in", RETRYABLE_STATUSES)})
	if doc.attendance_device_id != pin:
		doc.attendance_device_id = pin
		doc.save()  # on_employee_update requeues this pin's punches
	else:
		requeue(pin=pin)
	return waiting


def on_employee_update(doc, method=None):
	"""When an employee gets (or changes) an Attendance Device ID, pick up their unmatched punches."""
	if doc.get("attendance_device_id") and doc.has_value_changed("attendance_device_id"):
		requeue(pin=doc.attendance_device_id)


def cleanup():
	"""Daily: drop old request logs and finished device commands."""
	frappe.db.delete(
		"ZK Request Log", {"creation": ("<", add_days(now_datetime(), -REQUEST_LOG_RETENTION_DAYS))}
	)
	frappe.db.delete(
		"ZK Device Command",
		{
			"status": ("in", ("Done", "Failed")),
			"creation": ("<", add_days(now_datetime(), -COMMAND_RETENTION_DAYS)),
		},
	)


class _Context:
	"""Per-job caches."""

	def __init__(self):
		self.devices = {}
		self.employees = {}

	def device(self, name):
		if name not in self.devices:
			self.devices[name] = frappe.db.get_value(
				"ZK Device",
				name,
				["import_from", "log_type_mode", "latitude", "longitude"],
				as_dict=True,
			) or frappe._dict()
		return self.devices[name]

	def employees_for_pin(self, pin):
		if pin not in self.employees:
			self.employees[pin] = frappe.get_all(
				"Employee",
				filters={"attendance_device_id": pin},
				fields=["name", "employee_name"],
			)
		return self.employees[pin]


def _process_one(name: str, context: _Context):
	# Lock the row so two workers never create the same checkin twice.
	row = frappe.db.sql(
		"""select name, device, pin, punch_time, device_status, status, attempts
		from `tabZK Punch` where name = %s for update""",
		name,
		as_dict=True,
	)
	if not row or row[0].status != "Pending":
		_commit()
		return

	frappe.db.savepoint("zk_punch")
	try:
		result = _evaluate(row[0], context)
	except Exception as e:
		frappe.db.rollback(save_point="zk_punch")
		frappe.clear_messages()
		result = {"status": "Error", "error": _error_text(e)}

	result.setdefault("error", None)
	result["processed_at"] = now_datetime()
	result["attempts"] = (row[0].attempts or 0) + 1
	frappe.db.set_value("ZK Punch", name, result)
	_commit()


def _evaluate(punch, context: _Context) -> dict:
	device = context.device(punch.device)

	if device.import_from and get_datetime(punch.punch_time) < get_datetime(device.import_from):
		return {"status": "Skipped", "error": "Before the device's Ignore Punches Before date"}

	employees = context.employees_for_pin(punch.pin)
	if not employees:
		return {"status": "Unmatched", "employee": None, "employee_name": None}
	if len(employees) > 1:
		return {
			"status": "Error",
			"error": f"Attendance Device ID {punch.pin} is set on several employees: "
			+ ", ".join(e.name for e in employees),
		}

	employee = employees[0]
	matched = {"employee": employee.name, "employee_name": employee.employee_name}

	# Same employee, same second = same punch, whatever its log type ('' and NULL both mean blank).
	existing = frappe.db.get_value(
		"Employee Checkin", {"employee": employee.name, "time": punch.punch_time}, "name"
	)
	if existing:
		return {**matched, "status": "Duplicate", "employee_checkin": existing}

	checkin = frappe.new_doc("Employee Checkin")
	checkin.employee = employee.name
	checkin.time = punch.punch_time
	checkin.log_type = protocol.map_log_type(punch.device_status, device.log_type_mode)
	checkin.device_id = punch.device
	if device.latitude or device.longitude:
		checkin.latitude = device.latitude
		checkin.longitude = device.longitude
	checkin.insert(ignore_permissions=True)

	return {**matched, "status": "Created", "employee_checkin": checkin.name}


def _error_text(e: Exception) -> str:
	text = frappe.utils.strip_html(str(e)) or e.__class__.__name__
	return text[:1000]


def _commit():
	if not frappe.flags.in_test:
		frappe.db.commit()
