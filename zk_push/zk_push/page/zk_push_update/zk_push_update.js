// Copyright (c) 2026 Abdulrahman Aloqayli
// SPDX-License-Identifier: MIT
// Upgrade ZK Push from the site. The button only asks for the upgrade; the upgrade agent on the
// server (deploy/upgrade-agent) does it within a minute and reports back here.
frappe.pages["zk-push-update"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("ZK Push Update"),
		single_column: true,
	});
	const body = $('<div class="frappe-card" style="padding: var(--padding-lg); max-width: 760px;"></div>').appendTo(page.main);
	const esc = frappe.utils.escape_html;
	let timer = null;

	page.set_secondary_action(__("Check for updates"), () => load(1), "refresh");

	function agent_line(s) {
		if (s.agent_seen === null) {
			return `<p class="text-danger">${__("The upgrade agent is not installed on the server. An administrator installs it once; see deploy/upgrade-agent in the README.")}</p>`;
		}
		if (s.agent_seen > s.agent_stale_seconds) {
			return `<p class="text-danger">${__("The upgrade agent last ran {0} minutes ago: check its cron job on the server.", [Math.round(s.agent_seen / 60)])}</p>`;
		}
		return `<p class="text-muted">${__("Upgrade agent: running")}</p>`;
	}

	function state_line(s) {
		if (s.request) {
			return `<p><span class="indicator-pill orange">${__("Requested")}</span> ${esc(s.request.tag)} · ${esc(s.request.requested_by)} · ${__("starts within a minute")}</p>`;
		}
		const st = s.status;
		if (!st) return "";
		const color = { Running: "blue", Done: "green", Failed: "red" }[st.status] || "gray";
		return `<p><span class="indicator-pill ${color}">${esc(__(st.status))}</span> ${esc(st.tag || "")} · ${esc(st.at || "")}${st.message ? " · " + esc(st.message) : ""}</p>`;
	}

	function render(s) {
		const latest = s.releases[0];
		const newer = latest && latest.tag !== "v" + s.version;
		const busy = !!s.request || (s.status && s.status.status === "Running");
		const agent_ok = s.agent_seen !== null && s.agent_seen <= s.agent_stale_seconds;
		const options = s.releases
			.map((r) => `<option value="${esc(r.tag)}">${esc(r.tag)}${r.published ? " · " + esc(r.published) : ""}${r.tag === "v" + s.version ? " · " + __("installed") : ""}</option>`)
			.join("");

		body.html(`
			<h3>${__("Installed")}: ${esc(s.version)}</h3>
			<p>${
				!s.releases.length
					? __("Could not reach GitHub to list the releases.")
					: newer
					? __("{0} is available.", [`<b>${esc(latest.tag)}</b>`])
					: __("This is the latest release.")
			}</p>
			${agent_line(s)}
			${state_line(s)}
			${
				s.releases.length
					? `<div style="display:flex; gap: var(--margin-sm); flex-wrap: wrap; align-items: center; margin: var(--margin-md) 0;">
						<select class="form-control" style="max-width: 260px;">${options}</select>
						<button class="btn btn-primary btn-sm" ${busy || !agent_ok ? "disabled" : ""}>${__("Upgrade")}</button>
					</div>
					<p class="text-muted small">${__("Every site on this server is backed up, migrated and restarted: the sites are unavailable for a few minutes. Going back to an older release has to be allowed on the server first.")}</p>`
					: ""
			}
			${latest && latest.notes ? `<h5>${__("What's new in {0}", [esc(latest.tag)])}</h5><div class="small" style="white-space: pre-wrap;">${esc(latest.notes)}</div>` : ""}
			${s.log ? `<h5>${__("Last upgrade log")}</h5><pre style="max-height: 320px; overflow: auto; font-size: 12px;">${esc(s.log)}</pre>` : ""}
		`);

		body.find("button.btn-primary").on("click", () => {
			const tag = body.find("select").val();
			frappe.confirm(__("Upgrade every site on this server to {0}? They are unavailable for a few minutes.", [esc(tag)]), () =>
				frappe.call({
					method: "zk_push.upgrade.request_upgrade",
					args: { tag },
					freeze: true,
					callback: () => load(),
				})
			);
		});

		clearTimeout(timer);
		// While an upgrade is on, poll; the restart drops the connection for a moment.
		if (busy) timer = setTimeout(() => load(), 10000);
	}

	function load(refresh) {
		frappe.call({
			method: "zk_push.upgrade.get_status",
			args: { refresh: refresh ? 1 : 0 },
			callback: (r) => r.message && render(r.message),
			error: () => {
				clearTimeout(timer);
				timer = setTimeout(() => load(), 10000);
			},
		});
	}

	load();
};
