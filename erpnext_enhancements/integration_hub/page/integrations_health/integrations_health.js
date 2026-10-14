/**
 * Desk page: Integrations Health (route /desk/integrations-health).
 *
 * One ops screen for every external service this app depends on — QuickBooks,
 * Google Drive, Telephony (Triton/Twilio), AI drafting (Gemini), Analytics
 * (GA4/GSC), the MDM providers — plus scheduler liveness and a 24 h error digest.
 * The data comes from api.integrations_health.get_health (System-Manager-only,
 * DB-only — no outbound calls on load). The Drive tile carries a "Test connection"
 * button that runs the one live check on demand (api.integrations_health.run_drive_test).
 *
 * Drawn by the shared health board (public/js/health_board.bundle.js, v1.651.0), as the
 * Device Fleet Dashboard is: every tile is an sf_kit tile, each status a chip that says its
 * word, every colour a --sf-* token, and every server string escaped by the kit. This file
 * only says what to ask for and adds what is particular to this page: the scheduler and
 * error-digest tiles, and the Drive test.
 */
frappe.pages['integrations-health'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Integrations Health'),
		single_column: true,
	});

	const list = (value) => (Array.isArray(value) ? value : []);
	const obj = (value) => (value && typeof value === 'object' ? value : {});

	frappe.require(['health_board.bundle.js'], () => {
		const board = window.sf_health_board;
		if (!board) {
			$(page.body).text(__('This page could not load. Reload the page to try again.'));
			return;
		}
		board.mount(page, {
			method: 'erpnext_enhancements.api.integrations_health.get_health',
			loading: __('Loading integration status…'),
			failed: __('Could not load the integration status.'),
			refused: __('You do not have access to Integrations Health.'),
			meta: (data) => __('As of {0}', [data.generated_at || '']),
			tiles: (data) =>
				list(data.integrations)
					.map(board.tile)
					.concat([scheduler_tile(obj(data.scheduler)), errors_tile(obj(data.errors))]),
			after: ({ sf, data, tile }) =>
				list(data.integrations)
					.filter((it) => it && list(it.actions).includes('drive_test'))
					.forEach((it) => add_drive_test(sf, tile(it.key))),
		});
	});

	function scheduler_tile(s) {
		const enabled = s.enabled === false ? __('disabled') : s.enabled === null || s.enabled === undefined ? '—' : __('enabled');
		return {
			key: 'scheduler',
			title: __('Background jobs'),
			status: s.status,
			detail: s.enabled === false ? __('The Frappe scheduler is disabled: no daily or hourly jobs are running.') : '',
			metrics: [
				{ label: __('Scheduler'), value: enabled, tone: s.enabled === false ? 'danger' : '' },
				{ label: __('App jobs registered'), value: s.app_job_count || 0 },
				{ label: __('Failed (24h)'), value: s.failed_24h || 0, tone: s.failed_24h ? 'danger' : '' },
			].concat(list(s.recent_failures).map((f) => ({ label: obj(f).job, value: obj(f).when }))),
		};
	}

	function errors_tile(e) {
		const top = list(e.top);
		return {
			key: 'errors',
			title: __('Errors (24h)'),
			status: e.status,
			detail: top.length ? '' : __('No errors logged in the last 24 hours.'),
			metrics: [{ label: __('Total error logs'), value: e.total_24h || 0 }].concat(
				top.map((t) => ({ label: obj(t).title, value: obj(t).count }))
			),
		};
	}

	// The Drive test, added under the kit's Drive tile. Quiet: it reports its own failure in the
	// tile rather than in a frappe dialog. Each check's result is a chip with its word.
	function add_drive_test(sf, $tile) {
		const $out = $('<div class="ih-drive-result" aria-live="polite"></div>');
		const $button = $('<button type="button" class="sf-btn sf-btn--secondary"></button>').text(__('Test connection'));
		$tile.append($('<div class="ih-drive-test"></div>').append($button).append($out));
		$button.on('click', () => {
			$button.prop('disabled', true).text(__('Testing…'));
			$out.empty();
			const done = () => $button.prop('disabled', false).text(__('Test connection'));
			sf.call('erpnext_enhancements.api.integrations_health.run_drive_test', {}, { quiet: true }).then(
				(message) => {
					done();
					$out.html(drive_checks(sf, message));
				},
				(err) => {
					done();
					err = err || {};
					const said = err.session_expired
						? __('You have been signed out. Reload the page to sign in again.')
						: err.message || __('The test could not run. See the Error Log.');
					$out.html(check_line(sf, sf.chip(__('Failed'), 'danger'), said));
				}
			);
		});
	}

	// `mark` is kit markup (a chip); `text` is escaped here.
	function check_line(sf, mark, text) {
		return `<p style="margin: var(--sf-space-5) 0 0;">${mark} ${sf.esc(text)}</p>`;
	}

	// ok is true (pass), false (fail), or null (not configured).
	function drive_checks(sf, message) {
		const checks = message && Array.isArray(message.checks) ? message.checks : [];
		if (!checks.length) return message ? check_line(sf, '', JSON.stringify(message)) : '';
		return checks
			.map((c) => {
				c = obj(c);
				const mark =
					c.ok === true
						? sf.chip(__('Pass'), 'good')
						: c.ok === false
							? sf.chip(__('Fail'), 'danger')
							: sf.chip(__('Not set up'), 'neutral');
				return check_line(sf, mark, (c.check || '') + (c.detail ? ' — ' + String(c.detail) : ''));
			})
			.join('');
	}
};
