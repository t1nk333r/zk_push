# Copyright (c) 2026 t1nk333r
# SPDX-License-Identifier: MIT

import unittest
from datetime import date, datetime, time, timedelta

from zk_push.time_card import (
	compute_day,
	estimate_pay,
	hhmm,
	place_single_punch,
	shift_bounds,
)

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

	def test_missing_clock_in(self):
		self.assertEqual(card(None, at(17, 0)), ("01:00", "", "", "08:00", "09:00"))
		self.assertEqual(card(None, at(18, 30))[2], "01:30")

	def test_late_with_overtime(self):
		self.assertEqual(card(at(9, 0), at(18, 30)), ("01:00", "", "01:30", "08:00", "09:30"))

	def test_thresholds_are_inclusive(self):
		self.assertEqual(card(at(8, 0), at(18, 0))[2], "01:00")
		self.assertEqual(card(at(8, 0), at(17, 59, 59))[2], "")
		self.assertEqual(card(at(8, 0), at(16, 45))[1], "")
		self.assertEqual(card(at(8, 0), at(16, 44))[1], "00:16")

	def test_figures_never_exceed_the_shift(self):
		d = compute_day(ON, OFF, at(6, 0), at(7, 0), late_grace=15, early_grace=15)
		self.assertEqual((d.late, d.early, d.work), (0, 540, 0))
		d = compute_day(ON, OFF, at(18, 0), at(19, 30), late_grace=15, early_grace=15)
		self.assertEqual((d.late, d.early, d.work, d.overtime), (540, 0, 0, 90))  # OT from the clock in
		self.assertLessEqual(d.actual_attended, d.attended)
		self.assertEqual(compute_day(ON, OFF, at(8, 0), None, missing_punch=1000).early, 540)

	def test_clock_out_before_clock_in_counts_as_missing(self):
		d = compute_day(ON, OFF, at(17, 0), at(8, 0), missing_punch=60)
		self.assertEqual((d.late, d.early, d.work), (540, 0, 0))  # Late + Early never past the shift
		d = compute_day(ON, OFF, at(16, 30), at(8, 0), missing_punch=60)
		self.assertEqual((d.late, d.early), (510, 30))

	def test_place_single_punch(self):
		self.assertEqual(place_single_punch(at(7, 50), ON, OFF), (at(7, 50), None))
		self.assertEqual(place_single_punch(at(17, 5), ON, OFF), (None, at(17, 5)))  # forgot the first punch
		self.assertEqual(place_single_punch(at(12, 30), ON, OFF), (None, at(12, 30)))  # mid-shift: clock out

	def test_estimate_pay(self):
		# 7000, 2 absent days, 13:10 late + early: day 233.33, minute 0.486
		pay = estimate_pay(7000, 2, 790, 72)
		self.assertEqual((pay.gosi, pay.absence, pay.late_early, pay.overtime), (682.5, 466.67, 384.03, 0))
		self.assertEqual(pay.net, 5466.80)  # exactly what the amounts add up to
		self.assertEqual(estimate_pay(7000, 0, 0, 0, gosi_percent=0).net, 7000)
		self.assertEqual(estimate_pay(5000, 31, 0, 0).absence, 5000)  # at most 30 days

	def test_pay_amounts_add_up_to_net(self):
		for salary in (3007, 3333, 7000, 11111):
			pay = estimate_pay(salary, 1, 61, 95, pay_overtime=True)
			self.assertEqual(pay.net, round(pay.salary - pay.gosi - pay.absence - pay.late_early + pay.overtime, 2))

	def test_overtime_pay_is_hourly_wage_plus_half_basic(self):
		# Art. 107: 10 h at 10,000 (basic 6,000) = 10000/240 * 10 + 50% * 6000/240 * 10
		self.assertEqual(estimate_pay(10000, 0, 0, 600, pay_overtime=True, basic_salary=6000).overtime, 541.67)
		self.assertEqual(estimate_pay(10000, 0, 0, 600).overtime, 0)  # not paid unless asked

	def test_gosi_is_on_the_contributory_wage_within_limits(self):
		self.assertEqual(estimate_pay(10000, 0, 0, 0, gosi_wage=7500).gosi, 731.25)
		self.assertEqual(estimate_pay(60000, 0, 0, 0).gosi, 4387.5)  # capped at 45,000
		self.assertEqual(estimate_pay(1000, 0, 0, 0).gosi, 146.25)  # at least 1,500

	def test_hours_per_day_must_be_positive(self):
		with self.assertRaises(ValueError):
			estimate_pay(7000, 0, 60, 0, hours_per_day=0)

	def test_no_punches(self):
		self.assertEqual(card(None, None), ("", "", "", "", ""))

	def test_night_shift_ends_next_day(self):
		on, off = shift_bounds(DAY, timedelta(hours=22), timedelta(hours=6))
		self.assertEqual(off - on, timedelta(hours=8))
		d = compute_day(on, off, on, off + timedelta(minutes=70))
		self.assertEqual((d.work, d.overtime), (480, 70))


if __name__ == "__main__":
	unittest.main()
