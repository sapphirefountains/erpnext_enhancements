// Maintenance Day Board — supervisor's live view of today's maintenance work.
// Four columns from erpnext_enhancements.api.maintenance_board.get_day_board_data:
// scheduled drafts, techs clocked in, submitted today, flagged (last 7 days).
// Auto-refreshes every 60s, but only while this page is the one on screen (Desk pages are
// never removed, so a timer left running would poll all day); on_page_show refreshes at
// once on the way back. Cards link to the underlying documents. Its loading, error and
// no-access panels and its server call come from sf_kit (v1.642.0).

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
		// A state panel is the whole board, not one column of it.
		'.mdb-board > .sf-state{grid-column:1/-1;}\n' +
		'.mdb-stale{margin:4px 0 0;}\n' +
	'</style>').appendTo(page.body);
	// A refresh that failed while the board was showing says so here, and leaves the board be.
	const $stale = $('<div class="mdb-empty mdb-stale" role="status"></div>').hide().insertBefore($board);

	// sf_kit from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never
	// rejects: a kit that never arrived resolves to a plain stand-in (frappe.call and a line of
	// plain text), so the board carries on. A Desk opened before the deploy that added the loader
	// has no sf_kit_ready yet, and asks frappe.require for the kit itself.
	function kit() {
		if (typeof window.sf_kit_ready === 'function') return window.sf_kit_ready();
		return new Promise((resolve) =>
			frappe.require(['sf_kit.bundle.js', 'sf_kit.bundle.css'], () => resolve(window.sf_kit))
		);
	}

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

	// attempted: a load has been asked for at least once (the loading panel is drawn only before
	// the first). stale: the last refresh failed over a board already showing (toast once a run).
	const state = { timer: null, loaded: false, attempted: false, stale: false, loadedAt: '', inflight: false };

	function isCurrent() {
		return frappe.get_route()[0] === 'maintenance-day-board';
	}

	// A board that never loaded: no access on a 403, a way back in when the session has lapsed,
	// otherwise what went wrong with Try again (and the timer keeps retrying meanwhile; a 403 or a
	// lapsed session has stopped it, see refresh).
	function failed(sf, err) {
		err = err || {};
		if (err.session_expired) {
			sf.state.render($board, 'error', {
				title: __('You have been signed out.'),
				body: __('Reload the page to sign in again.'),
				action_label: __('Reload'),
				on_action: () => window.location.reload(),
			});
		} else if (err.status === 403) {
			sf.state.render($board, 'no-access', {
				title: __('You do not have access to the Maintenance Day Board.'),
				body: err.message,
			});
		} else {
			sf.state.render($board, 'error', {
				title: __('Could not load the day board.'),
				body: err.message || __('It tries again every minute.'),
				on_action: () => refresh({ asked: true }),
			});
		}
	}

	// Always the quiet path (v1.642.0), the timer's above all: a board left on a wall screen must
	// not stack a frappe dialog every minute while the server is unhappy. frappe.call's `silent`,
	// used here before, stops only the msgprint of a 417; a 500, 502, 504 or a lapsed session
	// still put up a dialog of their own on every tick.
	//
	// The loading panel is drawn on the first attempt only, or when someone presses Try again
	// (`asked`): a timer tick must never blank what is on screen, the board or the error panel. A
	// 403 or a lapsed session stops the timer, since asking again every minute cannot change the
	// answer; the next show of the page tries once more and starts it again.
	function refresh(opts) {
		if (state.inflight) return;
		state.inflight = true;
		const first = !state.attempted;
		state.attempted = true;
		kit().then((sf) => {
			if (first || (opts && opts.asked)) sf.state.render($board, 'loading', { title: __('Loading the day board…') });
			return sf.call('erpnext_enhancements.api.maintenance_board.get_day_board_data', {}, { quiet: true }).then((message) => {
				const data = message || {};
				state.loaded = true;
				state.stale = false;
				state.loadedAt = frappe.datetime.now_time();
				$stale.hide().text('');
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
			}, (err) => {
				err = err || {};
				if (err.status === 403 || err.session_expired) stop();
				// A failure keeps the board already showing, says so in a toast (once per run of
				// failures, not every minute) and in the line above it, which stays for a wall screen
				// nobody was watching when the toast went; only a board that never loaded gets the panel.
				if (!state.loaded) return failed(sf, err);
				const said = err.session_expired
					? __('You have been signed out. Reload the page to sign in again.')
					: __('Could not refresh the board. Showing it as of {0}.', [state.loadedAt]);
				if (!state.stale || err.session_expired) sf.toast(said, { tone: 'warning' });
				state.stale = true;
				$stale.text(said).show();
			});
		}).finally(() => {
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
