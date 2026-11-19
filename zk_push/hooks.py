app_name = "zk_push"
app_title = "ZK Push"
app_publisher = "t1nk333r"
app_description = "Receive ZKTeco ADMS (iclock) push attendance directly into ERPNext / HRMS"
app_email = "35288751+t1nk333r@users.noreply.github.com"
app_license = "mit"

required_apps = ["hrms"]

# Serve /iclock/* (the path ZKTeco devices push to) without any nginx rewrite.
page_renderer = ["zk_push.iclock.IclockRenderer"]

# Idempotent; makes sure the composite indexes exist even if the DocType was not re-imported.
after_migrate = [
	"zk_push.zk_push.doctype.zk_punch.zk_punch.on_doctype_update",
	"zk_push.zk_push.doctype.zk_device_user.zk_device_user.on_doctype_update",
]

# Remove the scheduled jobs, which would otherwise keep failing against the dropped tables.
before_uninstall = "zk_push.install.before_uninstall"

# Commands that set a device user's password are for System Managers only (HR Managers may read
# the other commands).
permission_query_conditions = {
	"ZK Device Command": "zk_push.zk_push.doctype.zk_device_command.zk_device_command.get_permission_query_conditions",
}
has_permission = {
	"ZK Device Command": "zk_push.zk_push.doctype.zk_device_command.zk_device_command.has_permission",
}

doc_events = {
	"Employee": {
		"on_update": "zk_push.processing.on_employee_update",
	}
}

# Safety net: pick up punches whose background job was lost (e.g. worker restart).
scheduler_events = {
	"cron": {
		"*/5 * * * *": ["zk_push.processing.enqueue_processing", "zk_push.processing.expire_commands"],
	},
	"daily": ["zk_push.processing.cleanup"],
}
