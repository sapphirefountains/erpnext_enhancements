// Maintenance Day Board — supervisor's live view of today's maintenance work.
// Four columns from erpnext_enhancements.api.maintenance_board.get_day_board_data:
// scheduled drafts, techs clocked in, submitted today, flagged (last 7 days).
// Auto-refreshes every 60s, but only while this page is the one on screen (Desk pages are
// never removed, so a timer left running would poll all day); on_page_show refreshes at
// once on the way back. Cards link to the underlying documents. Its loading, error and
// no-access panels and its server call come from sf_kit (v1.645.0).
//
// The day shown lives in the address (v1.662.0): /desk/maintenance-day-board is today, whatever
// day that is, and ?date=YYYY-MM-DD is that day. Each day stepped to with ‹ › Today or the date
// box is a history entry of its own, so Back returns to the day before it and Forward comes back;
// a reload or a pasted link opens the same day. The router copies the address's query into
// frappe.route_options on every route change, and a link from another page puts its options
// there too, so every show reads the date from route_options and consumes it. A date that is not
// a real calendar day is ignored, and so is today's own date: both leave the bare address, which
// keeps following the clock past midnight on a wall screen, as does a later day left on screen
// until it becomes today. A past day cannot change, so it is not polled. The page writes the
// address itself (history.pushState for a new day, replaceState to tidy the one on screen):
// routing to the page it is already on would make the router's push_state drop the query on
// older v16 builds.

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
		'.mdb-nav{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:4px 0;}\n' +
		'.mdb-nav .mdb-date{width:auto;}\n' +
		'.mdb-sublink{display:block;font-size:12px;margin:-4px 0 8px 10px;}\n' +
	'</style>').appendTo(page.body);
	// The day shown, and the way to another: ‹ and › step a day, Today comes back, the box jumps.
	const $nav = $(
		'<div class="mdb-nav">' +
		'<button type="button" class="btn btn-default btn-sm mdb-prev" title="' + esc(__('Previous day')) + '" aria-label="' + esc(__('Previous day')) + '">‹</button>' +
		'<input type="date" class="form-control input-sm mdb-date" aria-label="' + esc(__('Day shown')) + '">' +
		'<button type="button" class="btn btn-default btn-sm mdb-next" title="' + esc(__('Next day')) + '" aria-label="' + esc(__('Next day')) + '">›</button>' +
		'<button type="button" class="btn btn-default btn-sm mdb-today">' + esc(__('Today')) + '</button>' +
		'</div>'
	).insertBefore($board);
	// A refresh that failed while the board was showing says so here, and leaves the board be.
	const $stale = $('<div class="mdb-empty mdb-stale" role="status"></div>').hide().insertBefore($board);

	// sf_kit from the shared loader (public/js/sf_kit_loader.js, in the global bundle). It never
	// rejects: a kit that never arrived resolves to a plain stand-in (frappe.call and a line of
	// plain text), so the board carries on. A Desk opened before the deploy that added the loader
	// has no sf_kit_ready yet, and asks frappe.require for the kit itself.
	function kit() {
		if (typeof window.sf_kit_ready === 'function') return window.sf_kit_ready();
		return new Promise((resolve) =>
			frappe.require(['sf_kit.bundle.js', 'sf_kit.bundle.css'], () =>
				resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : bareKit())
			)
		);
	}

	// Neither the loader nor the kit: a Desk opened before this deploy whose old kit file is gone too.
	// A line of text and a button, so the page still says something instead of throwing.
	function bareKit() {
		const failed = (x) => ({ status: (x && x.status) || 0, message: '', exc_type: '', session_expired: (x && x.status) === 401 });
		const render = (el, kind, o) => {
			const $el = $(el).empty().append($('<div class="text-muted"></div>').text((o && o.title) || ''));
			if (o && o.on_action) $('<button class="btn btn-default btn-sm"></button>').text(o.action_label || __('Try again')).on('click', o.on_action).appendTo($el);
		};
		const call = (method, args) => Promise.resolve(frappe.call({ method, args: args || {} })).then((r) => r && r.message, (x) => Promise.reject(failed(x)));
		return { esc: (v) => frappe.utils.escape_html(v == null ? '' : String(v)), call, state: { render }, toast: (message) => frappe.show_alert({ message, indicator: 'orange' }) };
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

	// A draft visit is filled in through the Visit Wizard; its own address opens it there.
	function wizardLink(row) {
		return '<a class="mdb-sublink" href="/desk/visit-wizard/' + encodeURIComponent(row.name) + '">' +
			esc(__('Open in Visit Wizard')) + '</a>';
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
	// date: the day shown as YYYY-MM-DD, or null for today (the bare address). seq: the newest
	// load; a reply for an older one (the day has changed since it was asked) is dropped.
	const state = { timer: null, loaded: false, attempted: false, stale: false, loadedAt: '', inflight: false, date: null, seq: 0 };

	function isCurrent() {
		return frappe.get_route()[0] === 'maintenance-day-board';
	}

	function today() {
		return frappe.datetime.get_today();
	}

	// "YYYY-MM-DD" when the value is a real calendar day, else null: 2026-02-30 is not one.
	function validDay(value) {
		const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value == null ? '' : value).trim());
		if (!m || +m[1] < 1900 || +m[1] > 2999) return null;
		const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
		return d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3] ? m[0] : null;
	}

	function addDays(day, n) {
		const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
		const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + n));
		return d.toISOString().slice(0, 10);
	}

	function shownDay() {
		return state.date || today();
	}

	// The date the route asks for, taken out of frappe.route_options as the list view takes its
	// filters: the key goes, and an emptied object goes too, or a later plain set_route to this
	// page would replay it. Today's date and anything malformed come back as null (today).
	function takeRouteDate() {
		const opts = frappe.route_options;
		let asked = null;
		if (opts && Object.prototype.hasOwnProperty.call(opts, 'date')) {
			asked = validDay(opts.date);
			delete opts.date;
			if (!Object.keys(opts).length) frappe.route_options = null;
		}
		return asked && asked !== today() ? asked : null;
	}

	// The address for the day shown, on the path the router put on screen (which may carry a
	// workspace shell in front of the page name).
	function addressFor(date) {
		return window.location.pathname + (date ? '?date=' + encodeURIComponent(date) : '');
	}

	// push: a new day the person chose, so a new entry. Otherwise the entry on screen is corrected
	// in place. Nothing is written once the page has been left, or when the address already says it.
	function writeAddress(push) {
		if (!isCurrent()) return;
		const url = addressFor(state.date);
		if (url === window.location.pathname + window.location.search) return;
		if (push) window.history.pushState(null, '', url);
		else window.history.replaceState(window.history.state, '', url);
	}

	function drawNav() {
		const day = shownDay();
		$nav.find('.mdb-date').val(day);
		$nav.find('.mdb-today').prop('disabled', !state.date);
	}

	// Another day is on its way: what is on screen is the old day's, so the next load draws the
	// loading panel and a failure draws the error panel rather than keeping the old board.
	function dayChanged() {
		state.loaded = false;
		state.stale = false;
		$stale.hide().text('');
		drawNav();
		refresh({ asked: true, force: true });
	}

	// A day the person chose: its own history entry, then its board.
	function go(day) {
		const next = validDay(day);
		const date = next && next !== today() ? next : null;
		if (!next || date === state.date) return drawNav();
		state.date = date;
		writeAddress(true);
		dayChanged();
		// Stops the poll for a past day, starts it again for today or a later one.
		start();
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

	// Always the quiet path (v1.645.0), the timer's above all: a board left on a wall screen must
	// not stack a frappe dialog every minute while the server is unhappy. frappe.call's `silent`,
	// used here before, stops only the msgprint of a 417; a 500, 502, 504 or a lapsed session
	// still put up a dialog of their own on every tick.
	//
	// The loading panel is drawn on the first attempt only, or when someone presses Try again
	// (`asked`): a timer tick must never blank what is on screen, the board or the error panel. A
	// 403 or a lapsed session stops the timer, since asking again every minute cannot change the
	// answer; the next show of the page tries once more and starts it again.
	//
	// A new day (`force`) is asked for even with a load in flight; seq drops the older reply.
	function refresh(opts) {
		if (state.inflight && !(opts && opts.force)) return;
		state.inflight = true;
		const seq = ++state.seq;
		const date = state.date;
		const first = !state.attempted;
		state.attempted = true;
		kit().then((sf) => {
			if (seq !== state.seq) return;
			if (first || (opts && opts.asked)) sf.state.render($board, 'loading', { title: __('Loading the day board…') });
			// Today goes unnamed, so the server's own today answers it, as it always has.
			return sf.call('erpnext_enhancements.api.maintenance_board.get_day_board_data', date ? { date } : {}, { quiet: true }).then((message) => {
				if (seq !== state.seq) return;
				const data = message || {};
				state.loaded = true;
				state.stale = false;
				state.loadedAt = frappe.datetime.now_time();
				$stale.hide().text('');
				// Past midnight the bare address is a new today: the box follows it.
				drawNav();
				$board.html([
					column(__('Scheduled'), data.scheduled || [], (row) =>
						recordCard(row, pctBadge(row)) + wizardLink(row)),
					column(__('Clocked In'), data.in_progress || [], (row) =>
						'<a class="mdb-card" href="' + frappe.utils.get_form_link('Job Interval', row.name) + '">' +
						'<div class="mdb-title">' + esc(row.employee_name) + '</div>' +
						'<div class="mdb-sub">' + esc(row.project_title || '') + ' · ' +
						__('since {0}', [frappe.datetime.str_to_user(row.start_time)]) + '</div></a>'),
					column(date ? __('Submitted') : __('Submitted Today'), data.submitted_today || [], (row) =>
						recordCard(row, pctBadge(row) + flagBadges(row))),
					column(__('Flagged (7 days)'), data.flagged || [], (row) => recordCard(row, flagBadges(row))),
				].join(''));
			}, (err) => {
				if (seq !== state.seq) return;
				err = err || {};
				if (err.status === 403 || err.session_expired) stop();
				// A failure keeps the board already showing, says so in a toast (once per run of
				// failures, not every minute) and in the line above it, which stays for a wall screen
				// nobody was watching when the toast went; only a board that never loaded gets the panel.
				if (!state.loaded) return failed(sf, err);
				// A refusal is not a hiccup: the timer has stopped, so say why rather than "could not refresh".
				const said = err.session_expired
					? __('You have been signed out. Reload the page to sign in again.')
					: err.status === 403
						? __('You no longer have access to the Day Board. Showing it as of {0}.', [state.loadedAt])
						: __('Could not refresh the board. Showing it as of {0}.', [state.loadedAt]);
				if (!state.stale || err.session_expired || err.status === 403) sf.toast(said, { tone: 'warning' });
				state.stale = true;
				$stale.text(said).show();
			});
		}).finally(() => {
			if (seq === state.seq) state.inflight = false;
		});
	}

	function stop() {
		if (state.timer) clearInterval(state.timer);
		state.timer = null;
	}

	// Poll only while this is the current route and the tab is visible. Desk pages are never
	// removed (a 'remove' handler never fires), so the timer stops itself on its first tick
	// after the user has left, and on_page_show starts it again.
	//
	// A past day cannot change, so it is not polled at all; a move to today or a later day (go) and
	// the next show start the poll again.
	function start() {
		stop();
		if (isPast()) return;
		state.timer = setInterval(() => {
			if (!isCurrent()) return stop();
			reachedToday();
			if (!document.hidden) refresh();
		}, 60000);
	}

	function isPast() {
		return !!state.date && state.date < today();
	}

	// A later day left on screen until midnight has become today: from here on it is the bare today
	// (its labels, a disabled Today, the server's own today), the address corrected in place.
	function reachedToday() {
		if (!state.date || state.date !== today()) return;
		state.date = null;
		writeAddress(false);
		drawNav();
	}

	$nav.find('.mdb-prev').on('click', () => go(addDays(shownDay(), -1)));
	$nav.find('.mdb-next').on('click', () => go(addDays(shownDay(), 1)));
	$nav.find('.mdb-today').on('click', () => go(today()));
	$nav.find('.mdb-date').on('change', (e) => go(e.currentTarget.value));

	wrapper.mdb_day_board = {
		// Every show reads the day from the route: a reload, a link, or Back and Forward between
		// days (the router re-renders this page for each entry, and copies its ?date= into
		// route_options). A show of the day already on screen refreshes it as before.
		show() {
			const date = takeRouteDate();
			const changed = date !== state.date;
			state.date = date;
			// A malformed date, or today's, leaves the bare address: corrected in place, no entry.
			writeAddress(false);
			drawNav();
			if (changed && state.attempted) {
				dayChanged();
			} else {
				refresh();
			}
			start();
		},
		go,
		step: (n) => go(addDays(shownDay(), n)),
		day: shownDay,
	};
};

// Every show, the first included and every return from another screen: refresh now rather than
// waiting up to a minute, and restart the poll that stopped itself when the page was left.
frappe.pages['maintenance-day-board'].on_page_show = function (wrapper) {
	if (wrapper.mdb_day_board) wrapper.mdb_day_board.show();
};
