app_name = "zk_push"
app_title = "ZK Push"
app_publisher = "ZK Push contributors"
app_description = "Receive ZKTeco ADMS (iclock) push attendance directly into ERPNext / HRMS"
app_email = "35288751+t1nk333r@users.noreply.github.com"
app_license = "mit"

required_apps = ["hrms"]

# Serve /iclock/* (the path ZKTeco devices push to) without any nginx rewrite.
page_renderer = ["zk_push.iclock.IclockRenderer"]

# Idempotent; makes sure the composite indexes exist even if the DocType was not re-imported.
after_migrate = ["zk_push.zk_push.doctype.zk_punch.zk_punch.on_doctype_update"]

doc_events = {
	"Employee": {
		"on_update": "zk_push.processing.on_employee_update",
	}
}

# Safety net: pick up punches whose background job was lost (e.g. worker restart).
scheduler_events = {
	"cron": {
		"*/5 * * * *": ["zk_push.processing.enqueue_processing"],
	},
	"daily": ["zk_push.processing.cleanup"],
}
