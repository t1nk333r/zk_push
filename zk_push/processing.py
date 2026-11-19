"""Turn stored ZK Punch rows into Employee Checkins (runs in a background job)."""

import time

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, get_datetime, now_datetime
from rq.timeouts import JobTimeoutException

from zk_push import protocol

JOB_ID = "zk_push:process_pending"
BATCH_SIZE = 2000
# Seconds; a full batch of checkins must fit (the short queue's default is 300).
JOB_TIMEOUT = 1500
# One job works through a backlog until then and leaves the rest to the next run (every 5 minutes),
# so there is only ever one processing job: a chain of follow-up jobs would get past the dedup.
JOB_TIME_BUDGET = 1200
# A deadlock or lock wait timeout is retried on later runs; after this many tries the punch is an Error.
MAX_TRANSIENT_ATTEMPTS = 5
RETRYABLE_STATUSES = ("Unmatched", "Error")
REQUEST_LOG_RETENTION_DAYS = 7
COMMAND_RETENTION_DAYS = 90
# Commands still Pending after this many minutes are failed, not sent: an Open Door or Clear Log
# queued while the device was offline must not run whenever it comes back.
COMMAND_TTL_MINUTES = {"AC_UNLOCK": 10, "CLEAR LOG": 10}
DEFAULT_COMMAND_TTL_MINUTES = 24 * 60


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


def store_users(device: str, users: list[protocol.DeviceUser]) -> int:
	"""Create or update ZK Device User rows (one per device and pin); only changed rows are written,
	since pull agents send the whole list on every run. Returns the number written."""
	if not users:
		return 0
	# Keyed by lower case: names are compared case-insensitively, so pins 'ab1' and 'AB1' are one row
	# (the first one sent wins), instead of a duplicate-name error failing the whole upload.
	existing = {
		row.pin.lower(): row
		for row in frappe.get_all(
			"ZK Device User",
			filters={"device": device, "pin": ("in", [u.pin for u in users])},
			fields=["name", "pin", "user_name", "is_admin", "card"],
		)
	}
	now = now_datetime()
	written = 0
	seen = set()
	for user in users:
		key = user.pin.lower()
		if key in seen:
			continue
		seen.add(key)
		values = {"user_name": user.name, "is_admin": int(user.is_admin), "card": user.card}
		row = existing.get(key)
		if row is None:
			frappe.get_doc(
				{"doctype": "ZK Device User", "device": device, "pin": user.pin, "last_synced": now, **values}
			).insert(ignore_permissions=True)
			written += 1
			continue
		current = {"user_name": row.user_name or "", "is_admin": row.is_admin or 0, "card": row.card or ""}
		if current != values:
			frappe.db.set_value("ZK Device User", row.name, {**values, "last_synced": now})
			written += 1
	return written


def enqueue_processing():
	"""Queue the processing job once this transaction commits. Once per request or job: the dedup
	check runs at commit time, so several calls (a bulk Employee edit) do not queue it several times."""
	if frappe.flags.in_test:
		_enqueue()
		return
	if frappe.flags.zk_push_processing_queued:
		return
	frappe.flags.zk_push_processing_queued = True
	frappe.db.after_commit.add(_enqueue)


def _enqueue():
	try:
		frappe.enqueue(
			"zk_push.processing.process_pending",
			queue="short",
			timeout=JOB_TIMEOUT,
			job_id=JOB_ID,
			deduplicate=True,
			now=frappe.flags.in_test,
		)
	except Exception:
		# Best effort (e.g. Redis down): the punches are stored, and the 5-minute run picks them up.
		# Raising here would make the device upload, or the Employee save, fail.
		if frappe.flags.in_test:
			raise
		frappe.log_error(title="ZK Push: could not queue punch processing")
	finally:
		frappe.flags.zk_push_processing_queued = False


def process_pending():
	_requeue_mapped_pins()
	deadline = time.monotonic() + JOB_TIME_BUDGET
	context = _Context()
	while True:
		names = frappe.get_all(
			"ZK Punch",
			filters={"status": "Pending"},
			order_by="punch_time asc",
			limit=BATCH_SIZE,
			pluck="name",
		)
		for name in names:
			if time.monotonic() > deadline:
				return  # the rest stay Pending for the next run
			_process_one(name, context)
		# A full batch: more waiting (e.g. the initial upload of the device's history).
		if len(names) < BATCH_SIZE or frappe.flags.in_test:
			return


def _requeue_mapped_pins():
	"""Unmatched punches whose pin an employee has by now. on_employee_update requeues them when the
	pin is set, but misses punches a running job was holding then (Pending, marked Unmatched after)."""
	frappe.db.sql(
		"""update `tabZK Punch` p join `tabEmployee` e on e.attendance_device_id = p.pin
		set p.status = 'Pending', p.error = null
		where p.status = 'Unmatched'"""
	)
	_commit()


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
	doc = frappe.get_doc("Employee", employee)
	# A System Manager may lack Employee write permission; anyone else must be allowed to edit this
	# employee (User Permissions can limit an HR Manager to one company).
	manager = "System Manager" in frappe.get_roles()
	if not manager:
		frappe.has_permission("Employee", "write", doc, throw=True)

	other = frappe.get_all(
		"Employee",
		filters={"attendance_device_id": pin, "name": ("!=", employee)},
		fields=["name", "employee_name"],
	)
	if other:
		# Name the other employee only to someone who may see them.
		if manager or frappe.has_permission("Employee", "read", other[0].name):
			frappe.throw(
				_("Device user {0} is already mapped to {1} ({2}).").format(
					pin, other[0].name, other[0].employee_name
				)
			)
		frappe.throw(_("Device user {0} is already mapped to another employee.").format(pin))
	if doc.attendance_device_id and doc.attendance_device_id != pin and not overwrite:
		frappe.throw(
			_("{0} already has Attendance Device ID {1}. Tick Overwrite to replace it.").format(
				doc.employee_name, doc.attendance_device_id
			)
		)

	waiting = frappe.db.count("ZK Punch", {"pin": pin, "status": ("in", RETRYABLE_STATUSES)})
	if doc.attendance_device_id != pin:
		doc.attendance_device_id = pin
		doc.save(ignore_permissions=True)  # on_employee_update requeues this pin's punches
	else:
		requeue(pin=pin)
	return waiting


def on_employee_update(doc, method=None):
	"""When an employee gets (or changes) an Attendance Device ID, pick up their unmatched punches."""
	if doc.get("attendance_device_id") and doc.has_value_changed("attendance_device_id"):
		requeue(pin=doc.attendance_device_id)


def expire_commands(device: str | None = None):
	"""Fail Pending commands older than their time to live (COMMAND_TTL_MINUTES)."""
	filters = {"status": "Pending"}
	if device:
		filters["device"] = device
	now = now_datetime()
	for command in frappe.get_all("ZK Device Command", filters=filters, fields=["name", "command", "creation"]):
		ttl = COMMAND_TTL_MINUTES.get(command.command.strip().upper(), DEFAULT_COMMAND_TTL_MINUTES)
		if get_datetime(command.creation) < add_to_date(now, minutes=-ttl):
			frappe.db.set_value(
				"ZK Device Command",
				{"name": command.name, "status": "Pending"},
				{"status": "Failed", "response": "Expired before the device came online."},
			)


def cleanup():
	"""Daily: fail commands the device never answered, drop old request logs and finished commands."""
	expire_commands()
	frappe.db.set_value(
		"ZK Device Command",
		{"status": "Sent", "sent_at": ("<", add_days(now_datetime(), -1))},
		{"status": "Failed", "response": "No reply from the device within a day."},
	)
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
		if pin in self.employees:
			return self.employees[pin]
		employees = frappe.get_all(
			"Employee",
			filters={"attendance_device_id": pin},
			fields=["name", "employee_name"],
		)
		# Not cached when nobody has the pin: it may be mapped while this job runs, and the
		# requeue that follows only resets punches already marked Unmatched, not Pending ones.
		if employees:
			self.employees[pin] = employees
		return employees


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

	attempts = (row[0].attempts or 0) + 1
	frappe.db.savepoint("zk_punch")
	try:
		result = _evaluate(row[0], context)
	except JobTimeoutException:
		# The job's own timeout, not this punch's fault: leave it Pending for the next run.
		frappe.db.rollback(save_point="zk_punch")
		raise
	except Exception as e:
		try:
			frappe.db.rollback(save_point="zk_punch")
		except Exception:
			# A deadlock ends the whole transaction, savepoint (and the row lock) included.
			frappe.db.rollback()
		frappe.clear_messages()
		transient = isinstance(e, frappe.QueryDeadlockError | frappe.QueryTimeoutError)
		# Busy, not wrong: leave it Pending so a later run tries again.
		status = "Pending" if transient and attempts < MAX_TRANSIENT_ATTEMPTS else "Error"
		result = {"status": status, "error": _error_text(e)}

	result.setdefault("error", None)
	result["processed_at"] = now_datetime()
	result["attempts"] = attempts
	# Only if still Pending: after a full rollback the row is no longer locked, and another job may
	# have processed it meanwhile.
	frappe.db.set_value("ZK Punch", {"name": name, "status": "Pending"}, result)
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

	# Lock the employee so a job running at the same time (two devices, same second) waits here
	# and then finds this checkin, instead of both inserting one.
	frappe.db.sql("select name from `tabEmployee` where name = %s for update", employee.name)
	# Same employee, same second = same punch, whatever its log type ('' and NULL both mean blank).
	# A locking read: it sees checkins committed after this transaction's snapshot was taken (a plain
	# read under REPEATABLE READ would not, and the other job's checkin would be inserted again).
	existing = frappe.db.sql(
		"select name from `tabEmployee Checkin` where employee = %s and time = %s limit 1 for update",
		(employee.name, punch.punch_time),
	)
	if existing:
		return {**matched, "status": "Duplicate", "employee_checkin": existing[0][0]}

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
