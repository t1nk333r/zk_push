"""ZK Time Card report on real Attendance. Run on a bench with ERPNext + HRMS:
bench --site <site> run-tests --app zk_push --module zk_push.tests.test_time_card_report"""

from datetime import datetime

import frappe
from erpnext.setup.doctype.employee.test_employee import make_employee
from frappe.tests.utils import FrappeTestCase

from zk_push.zk_push.report.zk_time_card.zk_time_card import execute

SHIFT = "_ZK Time Card Test Shift"


def attendance(employee, date, status, in_time=None, out_time=None, **values):
	doc = frappe.get_doc(
		{
			"doctype": "Attendance",
			"employee": employee,
			"attendance_date": date,
			"status": status,
			"shift": SHIFT,
			"in_time": in_time and datetime.fromisoformat(f"{date} {in_time}"),
			"out_time": out_time and datetime.fromisoformat(f"{date} {out_time}"),
			**values,
		}
	)
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc


def report(**filters):
	_columns, data, _, _, cards = execute(
		{"from_date": "2026-10-01", "to_date": "2026-10-31", "overtime_after": 60, "missing_punch": 60, **filters}
	)
	return {str(r["attendance_date"]): r for r in data}, {c["label"]: c["value"] for c in cards}


class TestTimeCardReport(FrappeTestCase):
	def setUp(self):
		if not frappe.db.exists("Shift Type", SHIFT):
			frappe.get_doc(
				{
					"doctype": "Shift Type",
					"name": SHIFT,
					"start_time": "08:00:00",
					"end_time": "17:00:00",
					"enable_late_entry_marking": 1,
					"late_entry_grace_period": 15,
					"enable_early_exit_marking": 1,
					"early_exit_grace_period": 15,
				}
			).insert(ignore_permissions=True)
		self.employee = make_employee("zk_time_card_test@example.com", company="_Test Company")
		self.other = make_employee("zk_time_card_other@example.com", company="_Test Company")
		for emp in (self.employee, self.other):
			frappe.db.delete("Attendance", {"employee": emp})

	def test_one_punch_is_present_with_a_notice(self):
		attendance(self.employee, "2026-10-05", "Absent", "07:57")  # morning: forgot to clock out
		attendance(self.employee, "2026-10-06", "Absent", "17:05")  # evening: forgot to clock in
		rows, cards = report(employee=self.employee)

		morning, evening = rows["2026-10-05"], rows["2026-10-06"]
		self.assertEqual((morning["status"], morning["notice"]), ("Present", "One punch: no clock out"))
		self.assertEqual((morning["early"], morning["work"]), ("01:00", "08:00"))
		self.assertEqual((evening["status"], evening["notice"]), ("Present", "One punch: no clock in"))
		self.assertEqual((evening["clock_in"], evening["clock_out"]), ("", "17:05"))
		self.assertEqual((evening["late"], evening["work"]), ("01:00", "08:00"))
		self.assertEqual((cards["Present"], cards["Absent"]), (2, 0))

	def test_no_punches_counts_the_shift(self):
		attendance(self.employee, "2026-10-07", "Present")
		attendance(self.employee, "2026-10-08", "Half Day")
		rows, cards = report(employee=self.employee, in_minutes=1)
		self.assertEqual(rows["2026-10-07"]["work"], 540)
		self.assertEqual(rows["2026-10-08"]["work"], 270)
		self.assertEqual(cards["Work Time (min)"], 810)

	def test_pay_estimate(self):
		attendance(self.employee, "2026-10-05", "Present", "08:20", "17:00")
		attendance(self.employee, "2026-10-06", "Absent")
		_, cards = report(employee=self.employee, monthly_salary=7000, gosi_percent=9.75, hours_per_day=8)
		self.assertEqual(cards["Absence Deduction"], -233.33)
		self.assertEqual(cards["Late / Early Deduction"], 0)  # not ticked
		_, cards = report(
			employee=self.employee, monthly_salary=7000, gosi_percent=9.75, hours_per_day=8, deduct_late_early=1
		)
		self.assertEqual(cards["Late / Early Deduction"], -9.72)  # 20 min x 7000/30/480
		self.assertEqual(cards["Estimated Net Pay"], round(7000 - 682.5 - 233.33 - 9.72, 2))

		_, cards = report(monthly_salary=7000)
		self.assertEqual(cards["Pay Estimate"], "Select one Employee")
		_, cards = report(employee=self.employee, monthly_salary=7000, to_date="2026-11-30")
		self.assertIn("Pick at most 31 days", cards["Pay Estimate"])

	def test_totals_only_count_what_the_user_may_see(self):
		attendance(self.employee, "2026-10-05", "Present", "08:00", "17:00")
		attendance(self.other, "2026-10-05", "Absent")
		user = frappe.get_doc("User", "zk_time_card_test@example.com")
		user.add_roles("HR User")
		# Linking the employee to the user usually creates this already.
		permission = {"user": user.name, "allow": "Employee", "for_value": self.employee}
		if not frappe.db.exists("User Permission", permission):
			frappe.get_doc({"doctype": "User Permission", **permission}).insert(ignore_permissions=True)

		frappe.set_user(user.name)
		try:
			rows, cards = report()
			self.assertEqual({r["employee"] for r in rows.values()}, {self.employee})
			self.assertEqual(cards["Absent"], 0)
			rows, cards = report(employee=self.other, monthly_salary=7000, gosi_percent=9.75, hours_per_day=8)
			self.assertEqual((rows, cards.get("Absent")), ({}, 0))
			self.assertNotIn("Estimated Net Pay", cards)
		finally:
			frappe.set_user("Administrator")
