# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Time card figures for one attendance day, computed the way the ZKTeco attendance software's
Time Card report does (On duty / Off duty / Late / Early / OT Time / Work Time / ATT Time), and a
rough monthly pay estimate from them (estimate_pay).

All figures are whole minutes. Durations are worked out from the punches with their seconds and
then truncated, as on the device software's report (08:18:30 gives Late 18 and Work 08:41):
- Late: clock in after on duty + grace, counted from on duty (08:18 with 15 min grace = 18).
- Early: clock out before off duty - grace, counted to off duty.
  Together they are never more than the shift.
- OT: time after off duty (or after the clock in, if that was later), counted in full once it
  reaches overtime_after minutes (with 60: 17:45 gives no OT, 18:18 gives 78). Arriving early
  is not overtime.
- Work: the part of on duty .. off duty that was attended (never more than the shift). Minutes
  inside a grace period are not Late / Early but are not Work either.
- Attended: clock in to clock out.
- Actual ATT: Work + OT, the time to pay.
- A missing clock in or out counts as missing_punch minutes Late / Early, taken off Work; the
  shift start / end stands in for it in Attended. A clock out at or before the clock in counts
  as missing.

No Frappe imports, so it can be tested on its own.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

# GOSI contributions are worked out on the contributory wage (basic + housing) within these limits.
GOSI_WAGE_MIN, GOSI_WAGE_MAX = 1500, 45000


@dataclass
class Day:
	late: int = 0
	early: int = 0
	overtime: int = 0
	work: int = 0
	attended: int = 0
	actual_attended: int = 0


def minutes(delta: timedelta) -> int:
	return max(0, int(delta.total_seconds() // 60))


def shift_bounds(day, start, end) -> tuple[datetime, datetime]:
	"""On / off duty datetimes for `day`. start / end are times or, as the database returns
	Time fields, timedeltas since midnight. A shift ending at or before its start ends next day."""

	def at(t):
		return datetime.combine(day, t) if isinstance(t, time) else datetime.combine(day, time()) + t

	on, off = at(start), at(end)
	if off <= on:
		off += timedelta(days=1)
	return on, off


def place_single_punch(punch: datetime, on_duty: datetime, off_duty: datetime):
	"""(clock_in, clock_out) for a day with one punch. HRMS stores a lone punch as the clock in,
	but one after the middle of the shift is the clock out of someone who forgot the first punch."""
	if punch >= on_duty + (off_duty - on_duty) / 2:
		return None, punch
	return punch, None


def compute_day(
	on_duty: datetime,
	off_duty: datetime,
	clock_in: datetime | None,
	clock_out: datetime | None,
	late_grace: int = 0,
	early_grace: int = 0,
	overtime_after: int = 60,
	missing_punch: int = 60,
) -> Day:
	day = Day()
	if clock_in and clock_out and clock_out <= clock_in:
		clock_out = None
	if not clock_in and not clock_out:
		return day

	shift = minutes(off_duty - on_duty)
	missing_punch = min(max(missing_punch, 0), shift)

	if not clock_in:
		day.late = missing_punch
	elif clock_in > on_duty + timedelta(minutes=late_grace):
		day.late = minutes(min(clock_in, off_duty) - on_duty)

	if not clock_out:
		day.early = missing_punch
	else:
		if clock_out < off_duty - timedelta(minutes=early_grace):
			day.early = minutes(off_duty - max(clock_out, on_duty))
		extra = minutes(clock_out - max(off_duty, clock_in or off_duty))
		if extra and extra >= overtime_after:
			day.overtime = extra

	start, end = clock_in or on_duty, clock_out or off_duty
	day.work = minutes(min(end, off_duty) - max(start, on_duty))
	if not clock_in or not clock_out:
		day.work = max(0, day.work - missing_punch)
	day.attended = minutes(end - start)
	day.actual_attended = day.work + day.overtime
	# A clock in after off duty with no usable clock out is Late for the whole shift; the missing
	# punch must not add Early on top.
	day.early = min(day.early, shift - day.late)
	return day


@dataclass
class Pay:
	salary: float
	gosi: float
	absence: float
	late_early: float
	overtime: float
	net: float


def estimate_pay(
	salary: float,
	absent_days: float,
	late_early_minutes: int,
	overtime_minutes: int,
	gosi_percent: float = 9.75,
	gosi_wage: float | None = None,
	hours_per_day: float = 8,
	pay_overtime: bool = False,
	basic_salary: float | None = None,
) -> Pay:
	"""A rough estimate of one month's pay, as Saudi payroll usually works it out.

	A day is salary / 30 (at most 30 absent days are deducted), an hour is a day / hours_per_day
	(8 in the Labor Law). GOSI is gosi_percent of the contributory wage (basic + housing; defaults
	to the salary), kept within 1,500 .. 45,000, and is not reduced by deductions. Overtime, when
	paid, is the hourly wage plus 50% of the basic hourly wage (Labor Law, Art. 107). Each amount
	is rounded first, so the net is exactly what the amounts add up to."""
	if hours_per_day <= 0:
		raise ValueError("hours_per_day must be more than 0")
	day = salary / 30
	minute = day / (hours_per_day * 60)
	basic_minute = (salary if basic_salary is None else basic_salary) / 30 / (hours_per_day * 60)
	contributory = min(max(salary if gosi_wage is None else gosi_wage, GOSI_WAGE_MIN), GOSI_WAGE_MAX)

	gosi = round(contributory * gosi_percent / 100, 2)
	absence = round(day * min(absent_days, 30), 2)
	late_early = round(minute * late_early_minutes, 2)
	overtime = round((minute + basic_minute / 2) * overtime_minutes, 2) if pay_overtime else 0.0
	net = round(salary - gosi - absence - late_early + overtime, 2)
	return Pay(round(salary, 2), gosi, absence, late_early, overtime, net)


def hhmm(total_minutes: int) -> str:
	"""78 -> '01:18'; 0 -> '' (blank, as on the device software's report)."""
	if not total_minutes:
		return ""
	return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
