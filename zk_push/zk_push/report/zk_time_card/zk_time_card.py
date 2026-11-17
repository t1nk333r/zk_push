"""Attendance laid out like the ZKTeco software's Time Card, with overtime.

One row per submitted Attendance the user may read. The figures come from the Attendance's in /
out time and its Shift Type (start, end, grace periods when late entry / early exit marking is on);
see zk_push.time_card for the rules. On top of what HRMS records:
- A day with one punch, which HRMS marks Absent, is shown Present with a notice, the punch placed
  as clock in or clock out by the time of day; an approved one (Attendance Request) is not
  charged the missing punch.
- A Present / Work From Home day without punches (marked by HR or approved by an Attendance
  Request) counts as the whole shift, a Half Day as half.
- Half Day with leave: the leave half is neither Late nor Early.
- With one Employee, a Monthly Salary and at most 31 days, the totals add a pay estimate.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate

from zk_push.time_card import (
	Day,
	compute_day,
	estimate_pay,
	hhmm,
	minutes,
	place_single_punch,
	shift_bounds,
)

# Statuses where the employee was there (Absent and On Leave get no figures).
WORKED = ("Present", "Half Day", "Work From Home")
DURATIONS = ("late", "early", "overtime", "work", "attended", "actual_attended")
# The pay estimate is for one month: salary and GOSI are monthly amounts.
PAY_MAX_DAYS = 31


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not (filters.from_date and filters.to_date):
		frappe.throw(_("From Date and To Date are required"))
	# The desk leaves out filters that are 0 or unticked, and always sends the defaults set in the
	# .js, so a number that is not there means 0.
	overtime_after = max(cint(filters.overtime_after), 0)
	missing_punch = max(cint(filters.missing_punch), 0)
	in_minutes = bool(cint(filters.in_minutes))
	# A blank cell for zero, as on the device software's report.
	show = (lambda m: m or None) if in_minutes else hhmm

	rows = attendance_rows(filters)
	shifts = {
		s.name: s
		for s in frappe.get_all(
			"Shift Type",
			fields=[
				"name",
				"start_time",
				"end_time",
				"enable_late_entry_marking",
				"late_entry_grace_period",
				"enable_early_exit_marking",
				"early_exit_grace_period",
			],
		)
	}
	unpaid_leave = set(frappe.get_all("Leave Type", filters={"is_lwp": 1}, pluck="name"))

	data, totals = [], Day()
	for r in rows:
		row, day = time_card_row(r, shifts.get(r.shift), unpaid_leave, overtime_after, missing_punch)
		for field in DURATIONS:
			setattr(totals, field, getattr(totals, field) + getattr(day, field))
			row[field] = show(getattr(day, field))
		data.append(row)

	cards = summary(data, totals, in_minutes)
	if flt(filters.monthly_salary):
		cards += pay_cards(filters, data, totals)
	return columns(in_minutes), data, None, None, cards


def attendance_rows(filters):
	"""Submitted Attendance in the period, only what the user may read (User Permissions on
	Employee / Company apply), so the totals cannot show anyone the table hides."""
	conditions = {"docstatus": 1, "attendance_date": ["between", [filters.from_date, filters.to_date]]}
	if filters.employee:
		conditions["employee"] = filters.employee
	if filters.company:
		conditions["company"] = filters.company
	return frappe.get_list(
		"Attendance",
		filters=conditions,
		fields=[
			"name",
			"employee",
			"employee_name",
			"company",
			"attendance_date",
			"status",
			"half_day_status",
			"leave_type",
			"shift",
			"in_time",
			"out_time",
			"attendance_request",
		],
		order_by="employee asc, attendance_date asc",
		limit_page_length=0,
	)


def time_card_row(r, shift, unpaid_leave, overtime_after, missing_punch):
	"""The report row for one Attendance, and its figures. row["absent_days"] is what the pay
	estimate deducts for the day (1, 0.5 or 0)."""
	status, notice = r.status, ""
	clock_in, clock_out = r.in_time, r.out_time
	approved = bool(r.attendance_request)
	half_leave = status == "Half Day" and r.leave_type
	unpaid = r.leave_type in unpaid_leave

	on_duty = off_duty = None
	if shift:
		on_duty, off_duty = shift_bounds(r.attendance_date, shift.start_time, shift.end_time)

	if bool(clock_in) != bool(clock_out) and status in (*WORKED, "Absent"):
		# One punch: HRMS keeps it as the clock in and marks the day Absent (no working hours). The
		# device software counts the day, with the missing punch as Late / Early: show it Present
		# and flag it for review.
		if on_duty:
			clock_in, clock_out = place_single_punch(clock_in or clock_out, on_duty, off_duty)
		if status == "Absent":
			status = "Present"
		if clock_in:
			notice = _("One punch: no clock out (approved)") if approved else _("One punch: no clock out")
		else:
			notice = _("One punch: no clock in (approved)") if approved else _("One punch: no clock in")
	elif clock_in and clock_out and clock_out <= clock_in:
		notice = _("Clock out before clock in")
	elif clock_in and clock_out and status == "Absent":
		notice = _("Short hours: check the punches")

	absent_days = {"Absent": 1, "On Leave": 1 if unpaid else 0}.get(status, 0)
	day = Day()
	if status in WORKED and not on_duty:
		if clock_in and clock_out:
			day.attended = minutes(clock_out - clock_in)
		notice = notice or _("No shift: assign one to work out the figures")
	elif status in WORKED and not clock_in and not clock_out:
		# No punches: marked by HR or approved by an Attendance Request. The shift counts as worked;
		# for a Half Day half of it, the other half being leave (paid or not) or missed.
		full = minutes(off_duty - on_duty)
		if status != "Half Day":
			day.work = full
		elif half_leave and r.half_day_status == "Absent":
			# Leave for one half, nothing recorded for the other.
			absent_days = 1 if unpaid else 0.5
		else:
			day.work = full // 2
			absent_days = 0.5 if (unpaid or not half_leave) else 0
		day.actual_attended = day.work
	elif status in WORKED:
		day = compute_day(
			on_duty,
			off_duty,
			clock_in,
			clock_out,
			late_grace=cint(shift.late_entry_grace_period) if shift.enable_late_entry_marking else 0,
			early_grace=cint(shift.early_exit_grace_period) if shift.enable_early_exit_marking else 0,
			overtime_after=overtime_after,
			missing_punch=0 if approved else missing_punch,
		)
		if half_leave:
			# HRMS does not record which half was leave: neither arriving after it nor leaving for
			# it is Late / Early, and at most half the shift is Work.
			day.late = day.early = 0
			day.work = min(day.work, minutes(off_duty - on_duty) // 2)
			day.actual_attended = day.work + day.overtime
			absent_days = 0.5 if unpaid else 0

	row = {
		"attendance": r.name,
		"employee": r.employee,
		"employee_name": r.employee_name,
		"company": r.company,
		"attendance_date": r.attendance_date,
		"day": _(getdate(r.attendance_date).strftime("%A")),
		"status": status,
		"notice": notice,
		"shift": r.shift,
		"on_duty": on_duty.strftime("%H:%M") if on_duty else "",
		"off_duty": off_duty.strftime("%H:%M") if off_duty else "",
		"clock_in": clock_in.strftime("%H:%M") if clock_in else "",
		"clock_out": clock_out.strftime("%H:%M") if clock_out else "",
		"absent_days": absent_days,
	}
	return row, day


def pay_cards(filters, rows, totals):
	def message(text):
		return [{"label": _("Pay Estimate"), "value": text, "datatype": "Data"}]

	if not filters.employee:
		return message(_("Select one Employee"))
	if date_diff(filters.to_date, filters.from_date) + 1 > PAY_MAX_DAYS:
		return message(_("Pick at most {0} days (one pay month)").format(PAY_MAX_DAYS))
	if not rows:
		return []

	hours_per_day = flt(filters.hours_per_day) or 8
	pay = estimate_pay(
		flt(filters.monthly_salary),
		absent_days=sum(r["absent_days"] for r in rows),
		late_early_minutes=(totals.late + totals.early) if cint(filters.deduct_late_early) else 0,
		overtime_minutes=totals.overtime,
		gosi_percent=flt(filters.gosi_percent),
		gosi_wage=flt(filters.gosi_wage) or None,
		hours_per_day=hours_per_day,
		pay_overtime=bool(cint(filters.pay_overtime)),
		basic_salary=flt(filters.basic_salary) or None,
	)
	currency = frappe.get_cached_value("Company", rows[0]["company"], "default_currency")

	def money(label, value, indicator=None):
		card = {"label": label, "value": value or 0.0, "datatype": "Currency", "currency": currency}
		return {**card, "indicator": indicator} if indicator else card

	cards = [
		money(_("GOSI"), -pay.gosi, "Red"),
		money(_("Absence Deduction"), -pay.absence, "Red"),
		money(_("Late / Early Deduction"), -pay.late_early, "Red"),
	]
	if pay.overtime:
		cards.append(money(_("OT Pay"), pay.overtime, "Blue"))
	return [*cards, money(_("Estimated Net Pay"), pay.net, "Green" if pay.net >= 0 else "Red")]


def columns(in_minutes=False):
	def duration(fieldname, label):
		if in_minutes and fieldname in DURATIONS:
			return {"fieldname": fieldname, "label": _("{0} (min)").format(label), "fieldtype": "Int", "width": 95}
		return {"fieldname": fieldname, "label": label, "fieldtype": "Data", "width": 80}

	return [
		{"fieldname": "employee", "label": _("Employee"), "fieldtype": "Link", "options": "Employee", "width": 130},
		{"fieldname": "employee_name", "label": _("Name"), "fieldtype": "Data", "width": 150},
		{"fieldname": "attendance_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "day", "label": _("Day"), "fieldtype": "Data", "width": 95},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "notice", "label": _("Notice"), "fieldtype": "Data", "width": 190},
		{"fieldname": "shift", "label": _("Shift"), "fieldtype": "Link", "options": "Shift Type", "width": 120},
		duration("on_duty", _("On Duty")),
		duration("off_duty", _("Off Duty")),
		duration("clock_in", _("Clock In")),
		duration("clock_out", _("Clock Out")),
		duration("late", _("Late")),
		duration("early", _("Early")),
		duration("overtime", _("OT Time")),
		duration("work", _("Work Time")),
		duration("attended", _("ATT Time")),
		duration("actual_attended", _("Actual ATT")),
		{"fieldname": "attendance", "label": _("Attendance"), "fieldtype": "Link", "options": "Attendance", "width": 150},
	]


def summary(rows, totals, in_minutes=False):
	def total(label, value, indicator=None):
		if in_minutes:
			card = {"label": _("{0} (min)").format(label), "value": value, "datatype": "Int"}
		else:
			card = {"label": label, "value": hhmm(value) or "00:00", "datatype": "Data"}
		return {**card, "indicator": indicator} if indicator else card

	def count(label, statuses, indicator):
		value = sum(r["status"] in statuses for r in rows)
		return {"label": label, "value": value, "datatype": "Int", "indicator": indicator}

	return [
		total(_("Actual ATT"), totals.actual_attended, "Blue"),
		total(_("OT Time"), totals.overtime),
		total(_("Work Time"), totals.work),
		total(_("Late"), totals.late, "Red"),
		total(_("Early"), totals.early, "Red"),
		count(_("Present"), ("Present", "Work From Home"), "Green"),
		count(_("Half Day"), ("Half Day",), "Orange"),
		count(_("On Leave"), ("On Leave",), "Orange"),
		count(_("Absent"), ("Absent",), "Red"),
	]
