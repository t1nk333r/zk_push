"""HTTP side of the ZKTeco ADMS push protocol.

Devices call /iclock/cdata, /iclock/getrequest and /iclock/devicecmd. These paths are
served by IclockRenderer (registered via the `page_renderer` hook), so no nginx rewrite
is needed. The same handler is also exposed as whitelisted methods in zk_push.api.
"""

import time

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.wrappers import Response

from zk_push import processing, protocol

# How often last_seen is refreshed; devices poll getrequest every few seconds.
TOUCH_INTERVAL_SECONDS = 60
# Unknown serials are auto-registered (disabled) so an admin only has to tick "Enabled".
# Cap them so random internet traffic cannot flood the table.
MAX_PENDING_REGISTRATIONS = 20
# Request log truncation.
MAX_LOGGED_BODY = 64 * 1024
# Device users stored per OPERLOG request (devices hold up to a few thousand).
MAX_USERS_PER_REQUEST = 10000
# Command results handled per devicecmd request (one per queued command is normal).
MAX_COMMAND_RESULTS = 100
# Unreadable ATTLOG lines kept in the Error Log per request (each line is at most 500 characters).
MAX_LOGGED_INVALID_LINES = 1000

DEVICE_FIELDS = [
	"name",
	"enabled",
	"branch",
	"debug_logging",
	"last_seen",
	"last_ip",
	"push_version",
	"device_info",
	"att_stamp",
	"import_from",
	"discard_before_import",
]


class IclockRenderer(BaseRenderer):
	def can_render(self):
		return protocol.parse_endpoint(self.path) is not None

	def render(self):
		return handle(protocol.parse_endpoint(self.path))


class _Exchange:
	"""What happened during one device request, for the response and the request log."""

	def __init__(self, endpoint, request):
		self.endpoint = endpoint
		self.request = request
		self.started = time.monotonic()
		self.device = None
		self.status = 200
		self.body = ""
		self.error = None
		self.punches = 0

	def respond(self, body, status=200):
		self.body, self.status = body, status
		return self


def handle(endpoint: str, request=None) -> Response:
	exchange = _Exchange(endpoint, request or frappe.local.request)
	try:
		_handle(exchange)
		_commit()
	except Exception:
		frappe.db.rollback()
		exchange.respond("ERROR", 500)  # non-200: the device keeps the data and retries
		exchange.error = frappe.get_traceback()
		frappe.log_error(title=f"ZK Push: /iclock/{endpoint} failed", message=exchange.error)
		_commit()

	_log_request(exchange)
	return Response(exchange.body, status=exchange.status, mimetype="text/plain")


def _handle(ex: _Exchange):
	request = ex.request
	sn = (request.args.get("SN") or "").strip()
	if not protocol.is_valid_sn(sn):
		return ex.respond("ERROR: invalid SN", 400)

	device = ex.device = _get_or_register_device(sn)
	if not device:
		return ex.respond("ERROR: unknown device", 403)

	_touch(device, request)
	if not device.enabled:
		return ex.respond("ERROR: device not enabled", 403)

	if ex.endpoint == "cdata" and request.method == "GET":
		return ex.respond(protocol.build_handshake(sn, device.att_stamp))
	if ex.endpoint == "cdata" and request.method == "POST":
		return ex.respond(_receive(ex))
	if ex.endpoint == "getrequest":
		return ex.respond(_next_command(device) or "OK")
	if ex.endpoint == "devicecmd":
		_command_results(device, request.get_data(as_text=True))
		return ex.respond("OK")
	# ping, registry, ...: acknowledge.
	return ex.respond("OK")


def _receive(ex: _Exchange) -> str:
	device, request = ex.device, ex.request
	table = (request.args.get("table") or "").upper()
	if table == "OPERLOG":
		# Only the USER lines matter: the names typed on the device, to make mapping easy.
		users = protocol.parse_operlog_users(request.get_data(as_text=True))[:MAX_USERS_PER_REQUEST]
		processing.store_users(device.name, users)
		return "OK"
	if table != "ATTLOG":
		# ATTPHOTO, ... are not needed; acknowledge so the device moves on.
		return "OK"

	punches, invalid = protocol.parse_attlog(request.get_data(as_text=True))
	if invalid:
		# They are acknowledged below, so the device will not send them again: keep them (up to a
		# limit, so one huge unreadable batch cannot make the log insert, and the request, fail).
		kept = invalid[:MAX_LOGGED_INVALID_LINES]
		more = len(invalid) - len(kept)
		frappe.log_error(
			title=f"ZK Push: {len(invalid)} unreadable ATTLOG line(s) from {device.name}",
			message="\n".join(kept) + (f"\n... and {more} more line(s) not shown" if more else ""),
		)

	received = len(punches)
	if device.discard_before_import and device.import_from:
		# Push devices upload their whole history on first connect; drop the part before the cut-off.
		import_from = get_datetime(device.import_from)
		punches = [p for p in punches if p.time >= import_from]

	processing.store_punches(device.name, punches, branch=device.branch)
	ex.punches = len(punches)

	stamp = request.args.get("Stamp")
	if protocol.is_valid_stamp(stamp) and stamp != device.att_stamp:
		frappe.db.set_value("ZK Device", device.name, "att_stamp", stamp, update_modified=False)

	if punches:
		processing.enqueue_processing()

	# Acknowledge every line (including unreadable ones), otherwise the device resends forever.
	return f"OK: {received + len(invalid)}"


def _next_command(device) -> str | None:
	# Locked until this request commits, so two overlapping polls never both send it (e.g. REBOOT twice).
	command = frappe.db.sql(
		"""select name, command from `tabZK Device Command`
		where device = %s and status = 'Pending' order by creation asc limit 1 for update""",
		device.name,
		as_dict=True,
	)
	if not command:
		return None
	command = command[0]
	try:
		line = protocol.format_command(command.name, command.command)
	except ValueError:
		# A multi-line command would inject extra commands; fail it so it does not block the queue.
		frappe.db.set_value(
			"ZK Device Command",
			command.name,
			{"status": "Failed", "response": "Not sent: the command is not a single line."},
		)
		return None
	frappe.db.set_value(
		"ZK Device Command", command.name, {"status": "Sent", "sent_at": now_datetime()}
	)
	return line


def _command_results(device, body: str):
	# One query for the device's commands named in the body (it could hold thousands of lines).
	results = [r for r in protocol.parse_devicecmd(body) if r["id"].isdigit()][:MAX_COMMAND_RESULTS]
	ids = {int(r["id"]) for r in results}
	own = set(
		frappe.get_all(
			"ZK Device Command", filters={"device": device.name, "name": ("in", list(ids))}, pluck="name"
		)
		if ids
		else ()
	)
	for result in results:
		name = int(result["id"])
		if name not in own:
			continue
		ok = result["return"] is not None and result["return"] >= 0
		frappe.db.set_value(
			"ZK Device Command",
			name,
			{
				"status": "Done" if ok else "Failed",
				"return_code": result["return"],
				"completed_at": now_datetime(),
				"response": "\n".join(result["extra"])[:10000] or None,
			},
		)


def _get_or_register_device(sn: str):
	device = frappe.db.get_value("ZK Device", sn, DEVICE_FIELDS, as_dict=True)
	if device:
		return device

	pending = frappe.db.count("ZK Device", {"enabled": 0, "auto_registered": 1})
	if pending >= MAX_PENDING_REGISTRATIONS:
		# Otherwise a real new device is turned away without a trace (no device, no request log).
		if not frappe.cache.get_value("zk_push:registration_limit_logged"):
			frappe.cache.set_value("zk_push:registration_limit_logged", 1, expires_in_sec=3600)
			frappe.log_error(
				title="ZK Push: device registration limit reached",
				message=f"Turned away unknown device {sn}: {pending} disabled, auto-registered ZK Devices are "
				"waiting. Delete the ones you do not recognise so new devices can register.",
			)
		return None

	try:
		frappe.get_doc(
			{"doctype": "ZK Device", "serial_no": sn, "device_name": sn, "enabled": 0, "auto_registered": 1}
		).insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		pass  # registered concurrently by another request
	# A locking read sees that other request's row; a plain read would use this transaction's
	# snapshot, taken before it existed.
	return frappe.db.get_value("ZK Device", sn, DEVICE_FIELDS, as_dict=True, for_update=True)


def _touch(device, request):
	now = now_datetime()
	updates = {}

	if not device.last_seen or get_datetime(device.last_seen) < add_to_date(
		now, seconds=-TOUCH_INTERVAL_SECONDS
	):
		updates["last_seen"] = now

	ip = protocol.safe_text(_client_ip(request) or "")
	if ip and ip != device.last_ip:
		updates["last_ip"] = ip

	pushver = protocol.safe_text(request.args.get("pushver") or "", 140)
	if pushver and pushver != device.push_version:
		updates["push_version"] = pushver

	info = protocol.safe_text(request.args.get("INFO") or "", 1000)
	if info and info != device.device_info:
		updates["device_info"] = info

	if updates:
		frappe.db.set_value("ZK Device", device.name, updates, update_modified=False)
		device.update(updates)


def _log_request(ex: _Exchange):
	"""Record the exchange when the device has Debug Logging on, or when something failed.

	Rejections of disabled devices are not logged unless debugging, since a waiting device
	retries every 30 seconds.
	"""
	device = ex.device
	if not device:
		return
	failed = ex.status >= 500 or (ex.status >= 400 and device.enabled)
	if not (device.debug_logging or failed):
		return

	try:
		request = ex.request
		body = request.get_data(as_text=True) if request.method == "POST" else ""
		frappe.get_doc(
			{
				"doctype": "ZK Request Log",
				"device": device.name,
				"method": request.method,
				"endpoint": ex.endpoint,
				"query_string": protocol.safe_text(request.query_string.decode(errors="replace"), 2000),
				"request_body": body[:MAX_LOGGED_BODY],
				"body_size": len(body),
				"punches": ex.punches,
				"response_status": ex.status,
				"response_body": ex.body[:5000],
				"ip": _client_ip(request),
				"duration_ms": round((time.monotonic() - ex.started) * 1000, 1),
				"error": ex.error,
			}
		).insert(ignore_permissions=True)
		_commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="ZK Push: could not write request log")
		_commit()


def _client_ip(request):
	return getattr(frappe.local, "request_ip", None) or request.remote_addr


def _commit():
	if not frappe.flags.in_test:
		frappe.db.commit()
