"""ZK Time Card as a print-ready Excel workbook: one sheet per employee, laid out like the ZKTeco
software's Time Card, with totals, the pay estimate (when the report has one) and signature lines.
Durations are always HH:MM, as on the device software, whatever Show Durations in Minutes says.

The figures are the report's own (execute), so the same User Permissions apply."""

import json
import re
from io import BytesIO

import frappe
from frappe import _
from frappe.desk.query_report import get_report_doc
from frappe.utils import formatdate, getdate
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from zk_push.time_card import hhmm
from zk_push.zk_push.report.zk_time_card.zk_time_card import (
	DURATIONS,
	check_filters,
	execute,
)

# (field, label, width). Durations are minutes from the report, shown as HH:MM.
COLUMNS = (
	("attendance_date", "Date", 11),
	("day", "Day", 12),
	("status", "Status", 12),
	("shift", "Timetable", 17),
	("on_duty", "On Duty", 8),
	("off_duty", "Off Duty", 8),
	("clock_in", "Clock In", 8),
	("clock_out", "Clock Out", 9),
	("late", "Late", 7),
	("early", "Early", 7),
	("absent_days", "Absent", 7),
	("overtime", "OT Time", 8),
	("work", "Work Time", 9),
	("attended", "ATT Time", 9),
	("actual_attended", "Actual ATT", 10),
	("notice", "Notice", 30),
)
HEADER_ROW = 6
THIN = Side(style="thin", color="999999")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
TOTAL_FILL = PatternFill("solid", fgColor="F2F2F2")
NOTICE_FONT = Font(color="C65911", bold=True)
LATE_FONT = Font(color="C00000")
CENTER = Alignment(horizontal="center", vertical="center")
# Characters Excel does not allow in a sheet name.
SHEET_NAME_RE = re.compile(r"[\[\]:*?/\\]")
# One sheet per employee, built in the web request: more than this times out.
MAX_EMPLOYEES = 300


@frappe.whitelist(methods=["POST"])
def download(filters: str | dict):
	# The same checks as opening the report (roles, disabled, report access on Attendance); the
	# rows themselves follow User Permissions (execute uses get_list).
	get_report_doc("ZK Time Card")
	filters = check_filters(json.loads(filters) if isinstance(filters, str) else filters)
	content = build(filters)
	name = "Time Card"
	# The filename goes into a header: ASCII only.
	employee = re.sub(r"[^\w.-]+", " ", filters.employee or "", flags=re.ASCII).strip()
	if employee:
		name += f" {employee}"
	frappe.response["filename"] = f"{name} {filters.from_date} to {filters.to_date}.xlsx"
	frappe.response["filecontent"] = content
	frappe.response["type"] = "binary"


def build(filters) -> bytes:
	# Minutes, so the totals can be added up here.
	_columns, rows, _message, _chart, cards = execute({**filters, "in_minutes": 1})
	# The pay estimate, or the message saying why there is none.
	pay = [c for c in cards if c.get("datatype") == "Currency" or c["label"] == _("Pay Estimate")]
	date_format = frappe.db.get_default("date_format") or "dd-mm-yyyy"  # same codes in Excel

	workbook = Workbook()
	workbook.remove(workbook.active)
	by_employee = {}
	for row in rows:
		by_employee.setdefault(row["employee"], []).append(row)
	if len(by_employee) > MAX_EMPLOYEES:
		frappe.throw(
			_("{0} employees are too many for one file (at most {1}): pick a Company or an Employee").format(
				len(by_employee), MAX_EMPLOYEES
			)
		)
	if not by_employee:
		sheet = workbook.create_sheet(_("Time Card"))
		sheet["A1"] = _("No attendance in this period.")
	used = set()
	for employee_rows in by_employee.values():
		sheet = workbook.create_sheet(sheet_name(employee_rows[0], used))
		write_sheet(sheet, employee_rows, filters, date_format, pay if len(by_employee) == 1 else [])

	out = BytesIO()
	workbook.save(out)
	return out.getvalue()


def clean(value):
	"""Text Excel accepts: no control characters."""
	return ILLEGAL_CHARACTERS_RE.sub("", value) if isinstance(value, str) else value


def put(sheet, row, col, value):
	"""Write a value from the data. Text starting with = is kept as text, not run as a formula."""
	cell = sheet.cell(row, col, clean(value))
	if isinstance(cell.value, str) and cell.value.startswith("="):
		cell.data_type = "s"
	return cell


def excel_cut(text, units):
	"""text cut to `units` UTF-16 code units, which is how Excel counts a sheet name's length."""
	while len(text.encode("utf-16-le")) // 2 > units:
		text = text[:-1]
	return text


def sheet_name(row, used) -> str:
	text = clean(SHEET_NAME_RE.sub(" ", f"{row['employee_name'] or row['employee']}"))
	# Excel refuses a name that starts or ends with an apostrophe, and "History" (reserved).
	base = excel_cut(text, 28).strip().strip("'").strip() or "Employee"
	used.add("history")
	name, n = base, 1
	while name.lower() in used:
		n += 1
		name = f"{excel_cut(base, 25).rstrip()} {n}"
	used.add(name.lower())
	return name


def write_sheet(sheet, rows, filters, date_format, pay):
	last_col = get_column_letter(len(COLUMNS))
	first = rows[0]

	put(sheet, 1, 1, first["company"] or "")
	sheet["A1"].font = Font(bold=True, size=13)
	sheet["A2"] = _("Time Card")
	sheet["A2"].font = Font(bold=True, size=16)
	put(sheet, 3, 1, _("Employee: {0} - {1}").format(first["employee"], first["employee_name"] or ""))
	sheet["A4"] = _("Period: {0} to {1}").format(formatdate(filters.from_date), formatdate(filters.to_date))
	for cell in ("A3", "A4"):
		sheet[cell].font = Font(bold=True)
	for r in range(1, 5):
		sheet.merge_cells(f"A{r}:{last_col}{r}")

	for col, (_field, label, width) in enumerate(COLUMNS, 1):
		cell = sheet.cell(HEADER_ROW, col, _(label))
		cell.font, cell.fill, cell.border = Font(bold=True), HEADER_FILL, BORDER
		cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
		sheet.column_dimensions[get_column_letter(col)].width = width

	totals = dict.fromkeys(DURATIONS, 0)
	absent = 0
	r = HEADER_ROW
	for row in rows:
		r += 1
		for col, (field, _label, _width) in enumerate(COLUMNS, 1):
			value = row.get(field)
			if field in DURATIONS:
				totals[field] += value or 0
				value = hhmm(value)
			elif field == "attendance_date":
				value = getdate(value)
			elif field == "absent_days":
				absent += value or 0
				value = value or ""
			elif field in ("status", "day"):
				value = _(value) if value else ""
			cell = put(sheet, r, col, value if value is not None else "")
			cell.border = BORDER
			if field == "attendance_date":
				cell.number_format = date_format
			if field == "notice":
				cell.alignment = Alignment(wrap_text=True, vertical="center")
			else:
				cell.alignment = CENTER
			if row.get("notice") and field in ("status", "notice"):
				cell.font = NOTICE_FONT
			elif field in ("late", "early") and row.get(field):
				cell.font = LATE_FONT
			elif field == "actual_attended":
				cell.font = Font(bold=True)

	r += 1
	sheet.cell(r, 1, _("Total"))
	for col, (field, _label, _width) in enumerate(COLUMNS, 1):
		if field in DURATIONS:
			sheet.cell(r, col, hhmm(totals[field]) or "00:00")
		elif field == "absent_days":
			sheet.cell(r, col, absent or "")
		cell = sheet.cell(r, col)
		cell.font, cell.fill, cell.border, cell.alignment = Font(bold=True), TOTAL_FILL, BORDER, CENTER
	last_data_row = r

	r += 2
	counts = (
		(_("Present"), ("Present", "Work From Home")),
		(_("Half Day"), ("Half Day",)),
		(_("On Leave"), ("On Leave",)),
		(_("Absent"), ("Absent",)),
	)
	for label, statuses in counts:
		sheet.cell(r, 1, label).font = Font(bold=True)
		sheet.cell(r, 3, sum(row["status"] in statuses for row in rows)).alignment = Alignment(horizontal="left")
		r += 1

	if pay and pay[0].get("datatype") != "Currency":
		# Asked for, but there is none: say why rather than leave it out.
		r += 1
		sheet.cell(r, 1, f"{_('Pay Estimate')}: {pay[0]['value']}").font = Font(italic=True, color="666666")
		r += 1
	elif pay:
		r += 1
		sheet.cell(r, 1, _("Pay Estimate")).font = Font(bold=True, size=12)
		r += 1
		for card in pay:
			sheet.cell(r, 1, card["label"]).font = Font(bold=card["label"] == _("Estimated Net Pay"))
			cell = sheet.cell(r, 4, card["value"])
			cell.number_format = "#,##0.00"
			cell.font = Font(bold=card["label"] == _("Estimated Net Pay"))
			sheet.cell(r, 5, card.get("currency") or "")
			r += 1
		sheet.cell(r, 1, _("An estimate from the attendance above, not a salary slip.")).font = Font(
			italic=True, color="666666"
		)
		r += 1

	r += 2
	for col, label in ((1, _("Employee Signature")), (7, _("HR Signature")), (13, _("Date"))):
		sheet.cell(r, col, f"{label}: ____________________")

	sheet.freeze_panes = sheet.cell(HEADER_ROW + 1, 1)
	sheet.print_title_rows = f"{HEADER_ROW}:{HEADER_ROW}"
	sheet.print_area = f"A1:{last_col}{max(r, last_data_row)}"
	setup = sheet.page_setup
	setup.orientation, setup.paperSize = "landscape", sheet.PAPERSIZE_A4
	setup.fitToWidth, setup.fitToHeight = 1, 0
	sheet.sheet_properties.pageSetUpPr.fitToPage = True
	sheet.print_options.horizontalCentered = True
	sheet.page_margins.left = sheet.page_margins.right = 0.4
	sheet.page_margins.top = sheet.page_margins.bottom = 0.5
	sheet.oddFooter.center.text = _("Page &P of &N")
	# & starts a header / footer code.
	sheet.oddFooter.right.text = clean(first["employee"]).replace("&", "&&")
