import frappe

from zk_push import processing


def before_uninstall():
	frappe.db.delete("Scheduled Job Type", {"method": ("like", "zk_push.%")})
	try:
		from frappe.utils.background_jobs import get_job

		if job := get_job(processing.JOB_ID):
			job.cancel()
	except Exception:
		pass  # Redis down: the job fails once on the missing table, then nothing queues it again
