# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

"""Attendance laid out like the ZKTeco software's Time Card, with overtime.

One row per submitted Attendance the user may read. The figures come from the Attendance's in /
out time and its Shift Type (start, end, grace periods when late entry / early exit marking is on);
see zk_push.time_card for the rules. On top of what HRMS records:
- A day with one punch, which HRMS marks Absent, is shown Present with a notice, the punch placed
  as clock in or clock out by the time of day; an approved one (Attendance Request) is not
  charged the missing punch.
- A Present / Work From Home day without punches (marked by HR or approved by an Attendance
  Request) counts as the whole shift, a Half Day as half.
- Half Day with leave: the leave half is neither Late nor Early; the worked half is half the
  shift, and less than that counts as Early. A Half Day without leave and with punches (HRMS marks
  one below the shift's half-day threshold) keeps its Late / Early, like the device software.
- With one Employee, a Monthly Salary and at most 31 days, the totals add a pay estimate. Leave
  Without Pay is deducted in full, Partially Paid Leave by the part not paid. A date with several
  shifts (several Attendance) deducts at most one day, shared by shift length.
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
# Without an Employee every row is built in one request (employees x days).
ALL_EMPLOYEES_MAX_DAYS = 62


def execute(filters=None):
	filters = check_filters(filters)
	if not filters.employee and date_diff(filters.to_date, filters.from_date) + 1 > ALL_EMPLOYEES_MAX_DAYS:
		frappe.throw(
			_("Pick at most {0} days, or select an Employee for a longer period").format(ALL_EMPLOYEES_MAX_DAYS)
		)
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
	unpaid_leave = unpaid_shares()

	data, totals = [], Day()
	for r in rows:
		row, day = time_card_row(r, shifts.get(r.shift), unpaid_leave, overtime_after, missing_punch)
		for field in DURATIONS:
			setattr(totals, field, getattr(totals, field) + getattr(day, field))
			row[field] = show(getattr(day, field))
		data.append(row)
	share_absence_between_shifts(data, shifts)

	cards = summary(data, totals, in_minutes)
	if flt(filters.monthly_salary):
		cards += pay_cards(filters, data, totals)
	return columns(in_minutes), data, None, None, cards


def check_filters(filters):
	"""The filters as the report expects them: the download and REST callers can send anything."""
	filters = frappe._dict(filters or {})
	if not (filters.from_date and filters.to_date):
		frappe.throw(_("From Date and To Date are required"))
	for field in ("employee", "company"):
		if filters.get(field) and not isinstance(filters[field], str):
			frappe.throw(_("Pick one {0}").format(_(field.title())))
	# getdate throws a ValidationError for text that is not a date.
	filters.from_date, filters.to_date = getdate(filters.from_date), getdate(filters.to_date)
	if filters.to_date < filters.from_date:
		frappe.throw(_("To Date cannot be before From Date"))
	return filters


def unpaid_shares():
	"""{leave type: part of the day not paid}: 1 for Leave Without Pay, 1 - the paid fraction for
	Partially Paid Leave. Other leave is paid in full."""
	shares = {}
	for leave in frappe.get_all(
		"Leave Type",
		or_filters={"is_lwp": 1, "is_ppl": 1},
		fields=["name", "is_lwp", "fraction_of_daily_salary_per_leave"],
	):
		paid = 0 if leave.is_lwp else min(max(flt(leave.fraction_of_daily_salary_per_leave), 0), 1)
		if paid < 1:
			shares[leave.name] = 1 - paid
	return shares


def shift_minutes(shift):
	if not shift or shift.start_time is None or shift.end_time is None or shift.start_time == shift.end_time:
		return 0
	on, off = shift_bounds(getdate(), shift.start_time, shift.end_time)
	return minutes(off - on)


def share_absence_between_shifts(rows, shifts):
	"""HRMS keeps one Attendance per shift, so a date with several shifts has several rows. Their
	absence is shared by shift length (equally when a length is unknown): missing one shift of
	two does not cost a whole day, and missing both costs one."""
	by_day = {}
	for row in rows:
		by_day.setdefault((row["employee"], row["attendance_date"]), []).append(row)
	for day_rows in by_day.values():
		if len(day_rows) < 2:
			continue
		lengths = [shift_minutes(shifts.get(row["shift"])) for row in day_rows]
		if not all(lengths):
			lengths = [1] * len(day_rows)
		for row, length in zip(day_rows, lengths, strict=True):
			row["absent_days"] = round(row["absent_days"] * length / sum(lengths), 3)


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
	estimate deducts for the day (1, 0.5, 0 or a part for Partially Paid Leave). unpaid_leave is
	{leave type: part of the day not paid}, see unpaid_shares."""
	status, notice = r.status, ""
	clock_in, clock_out = r.in_time, r.out_time
	approved = bool(r.attendance_request)
	half_leave = status == "Half Day" and r.leave_type
	unpaid = unpaid_leave.get(r.leave_type, 0) if r.leave_type else 0

	on_duty = off_duty = None
	# A shift that starts when it ends would be read as 24 hours. Midnight comes back as
	# timedelta(0), which is falsy, so test for None.
	bad_shift = shift and (
		shift.start_time is None or shift.end_time is None or shift.start_time == shift.end_time
	)
	if shift and not bad_shift:
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

	absent_days = {"Absent": 1, "On Leave": unpaid}.get(status, 0)
	if status == "Half Day":
		absent_days = half_day_absence(r, half_leave, unpaid, punched=bool(clock_in or clock_out))
	day = Day()
	if status in WORKED and not on_duty:
		if clock_in and clock_out:
			day.attended = minutes(clock_out - clock_in)
		notice = notice or (
			_("Shift has no length: fix its start and end") if bad_shift
			else _("No shift: assign one to work out the figures")
		)  # fmt: skip
	elif status in WORKED and not clock_in and not clock_out:
		# No punches: marked by HR or approved by an Attendance Request. The shift counts as worked;
		# for a Half Day half of it, the other half being leave (paid or not) or missed.
		full = minutes(off_duty - on_duty)
		if status != "Half Day":
			day.work = full
		elif not (half_leave and r.half_day_status == "Absent"):
			day.work = full // 2
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
			# HRMS does not record which half was leave: neither arriving after it nor leaving for it
			# is Late / Early. The worked half is half the shift; less than that counts as Early.
			# An unapproved missing punch still costs missing_punch, as on a Present day.
			full = minutes(off_duty - on_duty)
			half = full // 2
			one_punch = bool(clock_in) != bool(clock_out) and not approved
			# compute_day already took the missing punch off Work; put it back before the cap.
			attended = day.work + (min(missing_punch, full) if one_punch else 0)
			day.late = 0
			day.work = max(0, min(attended, half) - (min(missing_punch, half) if one_punch else 0))
			day.early = half - day.work
			day.actual_attended = day.work + day.overtime

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


def half_day_absence(r, half_leave, unpaid, punched):
	"""What a Half Day deducts, whether or not it has a shift. unpaid is the part of a leave day
	not paid (1 for Leave Without Pay, 0 for paid leave)."""
	if punched:
		# Worked one half; the other was leave. Without leave the punches' Late / Early stand for
		# the missing time.
		return 0.5 * unpaid if half_leave else 0
	if half_leave and r.half_day_status == "Absent":
		# Leave for one half, nothing recorded for the other.
		return 0.5 + 0.5 * unpaid
	# No punches: one half counted as worked; the other is the leave, or missed without leave.
	return 0.5 * unpaid if half_leave else 0.5


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
	amounts = (filters.monthly_salary, filters.gosi_wage, filters.gosi_percent, filters.basic_salary)
	if hours_per_day < 0 or any(flt(v) < 0 for v in amounts):
		return message(_("Salary, GOSI and hours cannot be negative"))
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
