import unittest
from datetime import date, datetime, time, timedelta

from zk_push.time_card import compute_day, hhmm, shift_bounds

DAY = date(2026, 9, 28)
ON, OFF = shift_bounds(DAY, time(8, 0), time(17, 0))


def at(hour, minute, second=0):
	return datetime.combine(DAY, time(hour, minute, second))


def card(clock_in, clock_out, **kwargs):
	"""The day as the device software prints it: late, early, OT, work, attended."""
	d = compute_day(ON, OFF, clock_in, clock_out, late_grace=15, early_grace=15, **kwargs)
	return hhmm(d.late), hhmm(d.early), hhmm(d.overtime), hhmm(d.work), hhmm(d.attended)


class TestTimeCard(unittest.TestCase):
	# Rows from a real Time Card exported by the ZKTeco software (Daytime 08:00-17:00).

	def test_overtime_counted_from_off_duty(self):
		self.assertEqual(card(at(7, 56), at(18, 18, 30)), ("", "", "01:18", "09:00", "10:22"))

	def test_stay_below_overtime_threshold_is_not_overtime(self):
		self.assertEqual(card(at(7, 45), at(17, 45)), ("", "", "", "09:00", "10:00"))

	def test_late_after_grace_counted_from_on_duty(self):
		self.assertEqual(card(at(8, 18, 30), at(17, 0)), ("00:18", "", "", "08:41", "08:41"))

	def test_inside_grace_is_not_late(self):
		self.assertEqual(card(at(8, 11), at(17, 14)), ("", "", "", "08:49", "09:03"))

	def test_missing_clock_out(self):
		self.assertEqual(card(at(7, 57), None), ("", "01:00", "", "08:00", "09:03"))

	def test_early_leave(self):
		self.assertEqual(card(at(9, 11), at(15, 5)), ("01:11", "01:55", "", "05:54", "05:54"))

	def test_overtime_threshold_is_configurable(self):
		self.assertEqual(card(at(8, 0), at(17, 45), overtime_after=30)[2], "00:45")
		self.assertEqual(card(at(8, 0), at(17, 45), overtime_after=0)[2], "00:45")

	def test_actual_attended_is_shift_plus_overtime_minus_late_and_early(self):
		d = compute_day(ON, OFF, at(8, 20), at(18, 30), late_grace=15)
		self.assertEqual((d.late, d.overtime, d.actual_attended), (20, 90, 9 * 60 + 90 - 20))
		d = compute_day(ON, OFF, at(9, 56), at(18, 13), late_grace=15)
		self.assertEqual(d.actual_attended, 9 * 60 + 73 - 116)
		d = compute_day(ON, OFF, at(7, 56), at(18, 18), late_grace=15)
		self.assertEqual(d.actual_attended, 9 * 60 + 78)  # arriving early adds nothing
		d = compute_day(ON, OFF, at(8, 0), at(17, 45), late_grace=15)
		self.assertEqual(d.actual_attended, 9 * 60)  # stay below the OT threshold adds nothing
		d = compute_day(ON, OFF, at(8, 0), at(16, 0), early_grace=15)
		self.assertEqual(d.actual_attended, 8 * 60)  # early leave comes off

	def test_no_punches(self):
		self.assertEqual(card(None, None), ("", "", "", "", ""))

	def test_night_shift_ends_next_day(self):
		on, off = shift_bounds(DAY, timedelta(hours=22), timedelta(hours=6))
		self.assertEqual(off - on, timedelta(hours=8))
		d = compute_day(on, off, on, off + timedelta(minutes=70))
		self.assertEqual((d.work, d.overtime), (480, 70))


if __name__ == "__main__":
	unittest.main()
