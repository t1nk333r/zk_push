# Copyright (c) 2026 Abdulrahman Aloqayli
# SPDX-License-Identifier: MIT

"""Device clocks in another time zone than the site: their punch times are converted to the site's
time zone (System Settings) when the Employee Checkin is made. ZK Punch keeps the time as sent."""

from datetime import datetime
from zoneinfo import ZoneInfo, available_timezones

import frappe
from frappe import _
from frappe.utils import get_datetime, get_system_timezone


@frappe.whitelist()
def names() -> list[str]:
	return sorted(available_timezones())


def check(name: str | None) -> str | None:
	"""The time zone name, stripped; throws if it is not one."""
	name = (name or "").strip() or None
	if name and name not in available_timezones():
		frappe.throw(_("{0} is not a time zone (e.g. Asia/Riyadh, Asia/Dubai)").format(name))
	return name


def device_time_zone(device_zone: str | None) -> str | None:
	"""The device's own time zone, else the settings' one, else None (the site's)."""
	return device_zone or frappe.db.get_single_value("ZK Push Settings", "device_time_zone") or None


def to_site_time(value, zone: str | None) -> datetime:
	"""A device's local time as the site's local time (both without tzinfo, as Frappe stores them)."""
	value = get_datetime(value)
	site = get_system_timezone()
	if not zone or zone == site:
		return value
	return value.replace(tzinfo=ZoneInfo(zone)).astimezone(ZoneInfo(site)).replace(tzinfo=None)
