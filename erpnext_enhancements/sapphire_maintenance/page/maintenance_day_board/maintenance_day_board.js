// Maintenance Day Board — supervisor's live view of today's maintenance work.
// Four columns from erpnext_enhancements.api.maintenance_board.get_day_board_data:
// scheduled drafts, techs clocked in, submitted today, flagged (last 7 days).
// Auto-refreshes every 60s, but only while this page is the one on screen (Desk pages are
// never removed, so a timer left running would poll all day); on_page_show refreshes at
// once on the way back. Cards link to the underlying documents.

frappe.pages['maintenance-day-board'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Maintenance Day Board'),
		single_column: true,
	});

	const $board = $('<div class="mdb-board"></div>').appendTo(page.body);
	$('<style>\n' +
		'.mdb-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;padding:8px 0;}\n' +
		'.mdb-col{background:var(--bg-color,#f8f9fa);color:var(--text-color);border:1px solid var(--border-color,#e2e6e9);border-radius:8px;padding:10px;}\n' +
		'.mdb-col h5{margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.04em;display:flex;justify-content:space-between;}\n' +
		'.mdb-card{display:block;background:var(--card-bg,#fff);border:1px solid var(--border-color,#e2e6e9);border-radius:6px;padding:8px 10px;margin-bottom:8px;text-decoration:none;color:var(--text-color);}\n' +
		'.mdb-card:hover{border-color:var(--primary,#2490ef);text-decoration:none;}\n' +
		'.mdb-title{font-weight:600;font-size:13px;}\n' +
		'.mdb-sub{font-size:12px;color:var(--text-muted,#6c7680);margin-top:2px;}\n' +
		'.mdb-badge{display:inline-block;font-size:11px;border-radius:10px;padding:1px 8px;margin-right:4px;margin-top:4px;}\n' +
		'.mdb-red{background:#fde8e8;color:#b91c1c;}\n' +
		'.mdb-orange{background:#fef3e2;color:#b45309;}\n' +
		'.mdb-green{background:#e7f7ed;color:#15803d;}\n' +
		'.mdb-empty{font-size:12px;color:var(--text-muted,#6c7680);}\n' +
		// Status badges carry their own colours, so the dark theme gets its own pair: light text on a tint.
		'[data-theme="dark"] .mdb-red{background:rgba(239,68,68,.22);color:#fca5a5;}\n' +
		'[data-theme="dark"] .mdb-orange{background:rgba(245,158,11,.22);color:#fcd34d;}\n' +
		'[data-theme="dark"] .mdb-green{background:rgba(34,197,94,.22);color:#86efac;}\n' +
	'</style>').appendTo(page.body);

	function esc(value) {
		return frappe.utils.escape_html(String(value == null ? '' : value));
	}

	function recordCard(row, badges) {
		let sub = esc(row.project_title || '');
		if (row.visit_label) sub += ' · ' + esc(row.visit_label);
		else if (row.serial_no) sub += ' · ' + esc(row.serial_no);
		if (row.technician) sub += ' · ' + esc(row.technician);
		return (
			'<a class="mdb-card" href="' + frappe.utils.get_form_link('Sapphire Maintenance Record', row.name) + '">' +
			'<div class="mdb-title">' + esc(row.name) + '</div>' +
			'<div class="mdb-sub">' + sub + '</div>' +
			(badges || '') +
			'</a>'
		);
	}

	function pctBadge(row) {
		const pct = row.completion_percent || 0;
		const cls = pct >= 100 ? 'mdb-green' : pct >= 50 ? 'mdb-orange' : 'mdb-red';
		return '<span class="mdb-badge ' + cls + '">' + pct + '%</span>';
	}

	function flagBadges(row) {
		let html = '';
		if (row.has_out_of_range_readings) html += '<span class="mdb-badge mdb-red">Chemistry</span>';
		if (row.warranty_rma_flag) html += '<span class="mdb-badge mdb-orange">Warranty</span>';
		return html;
	}

	function column(title, rows, render) {
		let html = '<div class="mdb-col"><h5>' + esc(title) + '<span>' + rows.length + '</span></h5>';
		html += rows.length ? rows.map(render).join('') : '<div class="mdb-empty">' + __('Nothing here.') + '</div>';
		return html + '</div>';
	}

	const state = { timer: null, loaded: false, inflight: false };

	function isCurrent() {
		return frappe.get_route()[0] === 'maintenance-day-board';
	}

	$board.html('<div class="mdb-empty">' + __('Loading…') + '</div>');

	function refresh() {
		if (state.inflight) return;
		state.inflight = true;
		frappe.call('erpnext_enhancements.api.maintenance_board.get_day_board_data').then((r) => {
			const data = r.message || {};
			state.loaded = true;
			$board.html([
				column(__('Scheduled'), data.scheduled || [], (row) => recordCard(row, pctBadge(row))),
				column(__('Clocked In'), data.in_progress || [], (row) =>
					'<a class="mdb-card" href="' + frappe.utils.get_form_link('Job Interval', row.name) + '">' +
					'<div class="mdb-title">' + esc(row.employee_name) + '</div>' +
					'<div class="mdb-sub">' + esc(row.project_title || '') + ' · ' +
					__('since {0}', [frappe.datetime.str_to_user(row.start_time)]) + '</div></a>'),
				column(__('Submitted Today'), data.submitted_today || [], (row) =>
					recordCard(row, pctBadge(row) + flagBadges(row))),
				column(__('Flagged (7 days)'), data.flagged || [], (row) => recordCard(row, flagBadges(row))),
			].join(''));
		}).catch(() => {
			// A transient failure keeps the board already showing; only a board that never loaded says so.
			if (!state.loaded) $board.html('<div class="mdb-empty">' + __('Could not load the day board. Retrying shortly.') + '</div>');
		}).always(() => {
			state.inflight = false;
		});
	}

	function stop() {
		if (state.timer) clearInterval(state.timer);
		state.timer = null;
	}

	// Poll only while this is the current route and the tab is visible. Desk pages are never
	// removed (a 'remove' handler never fires), so the timer stops itself on its first tick
	// after the user has left, and on_page_show starts it again.
	function start() {
		stop();
		state.timer = setInterval(() => {
			if (!isCurrent()) return stop();
			if (!document.hidden) refresh();
		}, 60000);
	}

	wrapper.mdb_day_board = {
		show() {
			refresh();
			start();
		},
	};
};

// Every show, the first included and every return from another screen: refresh now rather than
// waiting up to a minute, and restart the poll that stopped itself when the page was left.
frappe.pages['maintenance-day-board'].on_page_show = function (wrapper) {
	if (wrapper.mdb_day_board) wrapper.mdb_day_board.show();
};
