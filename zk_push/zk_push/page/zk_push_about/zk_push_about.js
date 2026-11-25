// Static About page: app version, what it does, the push URL to set on devices, and licence.
frappe.pages["zk-push-about"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("About ZK Push"),
		single_column: true,
	});

	const version = (frappe.boot.versions || {}).zk_push || "";
	const origin = window.location.origin;

	$(`
		<div class="frappe-card" style="padding: var(--padding-lg); max-width: 720px;">
			<h3>ZK Push ${frappe.utils.escape_html(version)}</h3>
			<p>${__(
				"Receives ZKTeco ADMS (iclock) push attendance and turns it into Employee Checkins in ERPNext / HRMS."
			)}</p>

			<h5>${__("Device setup")}</h5>
			<p>${__("Cloud Server Setting on the device:")}</p>
			<ul>
				<li>${__("Server address")}: <code>${frappe.utils.escape_html(origin)}</code></li>
				<li>${__("Push path")}: <code>/iclock/cdata</code></li>
			</ul>
			<p>${__(
				"Punches are matched on the Employee field Attendance Device ID (Biometric/RF tag ID)."
			)}</p>

			<h5>${__("Device commands")}</h5>
			<p>${__(
				"Open a ZK Device and use Device Commands: info, upload or re-upload punches, reboot; and for System Managers: add / update / delete a user, enroll a fingerprint, open the door, clear the log. Results are in ZK Device Command."
			)}</p>

			${frappe.user.has_role("System Manager") ? `<h5>${__("Updates")}</h5>
			<p><a href="/app/zk-push-update">${__("ZK Push Update")}</a>: ${__("check for a new release and upgrade from the site.")}</p>` : ""}

			<p style="margin-top: var(--margin-lg);">
				<a href="https://github.com/t1nk333r/zk_push" target="_blank" rel="noopener noreferrer">github.com/t1nk333r/zk_push</a><br>
				Copyright © 2026 t1nk333r · MIT
			</p>
		</div>
	`).appendTo(page.main);
};
