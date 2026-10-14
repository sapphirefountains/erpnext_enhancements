/**
 * Desk page: Device Fleet Dashboard (route /desk/device-fleet-dashboard).
 *
 * A snapshot of the managed-device fleet — status mix, compliance split, stale
 * self-attestations, warranty expiries, platform / ownership breakdown — from
 * api.device_dashboard.get_fleet_health (Device-Manager / System-Manager only, DB-only).
 *
 * A thin caller of the shared health board (public/js/health_board.bundle.js, v1.649.0),
 * the same one Integrations Health uses: the server sends the health-check tile shape and
 * the board draws it on sf_kit tiles, states included. This page used to be a copy of the
 * Integrations Health page with its class prefix changed.
 */
frappe.pages['device-fleet-dashboard'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Device Fleet Dashboard'),
		single_column: true,
	});
	page.set_secondary_action(__('Device Console'), () => frappe.set_route('device-console'));

	frappe.require(['health_board.bundle.js'], () => {
		const board = window.sf_health_board;
		if (!board) {
			$(page.body).text(__('This page could not load. Reload the page to try again.'));
			return;
		}
		board.mount(page, {
			method: 'erpnext_enhancements.api.device_dashboard.get_fleet_health',
			loading: __('Loading fleet status…'),
			failed: __('Could not load the fleet status.'),
			refused: __('You do not have access to the Device Fleet Dashboard.'),
			meta: (data) => __('{0} devices · as of {1}', [data.total || 0, data.generated_at || '']),
			tiles: (data) => (Array.isArray(data.tiles) ? data.tiles : []).map(board.tile),
		});
	});
};
