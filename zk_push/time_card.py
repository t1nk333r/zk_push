"""Time card figures for one attendance day, computed the way the ZKTeco attendance software's
Time Card report does (On duty / Off duty / Late / Early / OT Time / Work Time / ATT_Time).

All figures are whole minutes, seconds dropped, as on the device software's report:
- Late: clock in after on duty + grace, counted from on duty (08:18 with 15 min grace = 18).
- Early: clock out before off duty - grace, counted to off duty.
- OT: clock out after off duty, counted from off duty, but only once it reaches overtime_after
  minutes (with 60: 17:45 gives no OT, 18:18 gives 78). Arriving early is not overtime.
- Actual ATT: Work + OT, i.e. the shift + OT - Late - Early: the time to pay.
- Work: the part of on duty .. off duty that was attended (never more than the shift).
- Attended: clock in to clock out.
- A missing clock in or out counts as missing_punch minutes Late / Early, taken off Work; the
  shift start / end stands in for it in Attended.

No Frappe imports, so it can be tested on its own.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta


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
	if not clock_in and not clock_out:
		return day

	if not clock_in:
		day.late = missing_punch
	elif clock_in > on_duty + timedelta(minutes=late_grace):
		day.late = minutes(clock_in - on_duty)

	if not clock_out:
		day.early = missing_punch
	else:
		if clock_out < off_duty - timedelta(minutes=early_grace):
			day.early = minutes(off_duty - clock_out)
		extra = minutes(clock_out - off_duty)
		if extra and extra >= overtime_after:
			day.overtime = extra

	start, end = clock_in or on_duty, clock_out or off_duty
	day.work = minutes(min(end, off_duty) - max(start, on_duty))
	if not clock_in or not clock_out:
		day.work = max(0, day.work - missing_punch)
	day.attended = minutes(end - start)
	day.actual_attended = day.work + day.overtime
	return day


def hhmm(total_minutes: int) -> str:
	"""78 -> '01:18'; 0 -> '' (blank, as on the device software's report)."""
	if not total_minutes:
		return ""
	return f"{total_minutes // 60:02d}:{total_minutes % 60:02d}"
