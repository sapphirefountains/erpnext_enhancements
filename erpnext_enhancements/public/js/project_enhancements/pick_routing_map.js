/**
 * Project form — Pick Routing Map (Budget tab).
 *
 * Targets: the `custom_btn_pick_routing_map` Button on the Project form's Budget
 *   tab, under the "Material Pickup" section (created by
 *   `patches/add_project_pick_routing_button.py`).
 * Loaded via: hooks.py `doctype_js["Project"]`.
 *
 * A job's material sits at several vendors' will-call counters at once. This
 * opens an extra-large dialog listing every supplier with material still to
 * collect, and drives Google's DirectionsService with `optimizeWaypoints: true`
 * to put them in drive-time order for one round trip out of the shop.
 *
 * Data comes from erpnext_enhancements.api.pickup_routing.get_pickup_route_data
 * in one call: `{ api_key, scope, project, depot, stops, routable_count }`. Each
 * stop is one supplier pick-up address with the Purchase Orders behind it, and
 * carries `address: null` when nothing could be resolved for that vendor — those
 * are listed separately rather than dropped, so the missing Supplier address is
 * visible and fixable.
 *
 * Four things this file is careful about:
 *  - **The map is built after `d.show()`, never before.** A dialog body is
 *    `display:none` until then, and any map library initialised in a 0x0
 *    container renders blank — the same trap travel_trip_map.js documents for a
 *    hidden tab. A `built` latch plus an explicit offsetWidth check guards it.
 *  - **Three degradation steps, each still useful.** Directions (optimised route
 *    + per-leg distance/time) -> geocoded pins in list order (when the key lacks
 *    the Directions API) -> a plain ordered list of Google Maps links (no key at
 *    all). Every step keeps the "Open in Google Maps" hand-off working.
 *  - **Colours painted on Google tiles are literal, not theme vars** (house rule
 *    — tiles are always light). The desk chrome around the map uses theme vars.
 *  - **Google's own limits are surfaced, not silently applied**: the Directions
 *    API takes at most 23 intermediate waypoints, and the `?api=1` Maps URL at
 *    most 9. When a run exceeds either, the UI says so instead of quietly
 *    routing a subset.
 */
(function () {
	'use strict';

	// Google caps a Directions request at 25 waypoints total (origin +
	// destination + 23 intermediate stops).
	const MAX_ROUTE_STOPS = 23;
	// The documented ceiling for the `?api=1` Maps URL's `waypoints` parameter.
	const MAX_LINK_WAYPOINTS = 9;

	// Marker fill per stop, cycled. Literal colours: these are painted on
	// (always-light) Google tiles, so readability there beats theme matching.
	const STOP_COLOR = '#1a56db';
	const DEPOT_COLOR = '#137547';
	const FINISH_COLOR = '#b32d2e';

	// Which step of the server's fallback chain placed a stop. Shown on the row
	// so "why is this vendor at that address?" — and "where do I fix it?" — are
	// answerable without opening the PO. Keys match api/pickup_routing.py.
	const ADDRESS_SOURCE_LABELS = {
		'po.dispatch_address': __('from the PO dispatch address'),
		'po.supplier_address': __('from the PO supplier address'),
		'supplier.supplier_primary_address': __('from the supplier’s primary address'),
		'supplier.address_directory': __('from the supplier’s address directory'),
	};

	const SCOPE_LABELS = [
		{ value: 'outstanding', label: __('Still to collect') },
		{ value: 'submitted', label: __('All submitted POs') },
		{ value: 'all', label: __('Include draft POs') },
	];

	// ---------------------------------------------------------------- maps API

	// The Maps JS API can only be injected once per page; share one promise so
	// repeated opens don't re-add the <script>. Its own callback name — the
	// page-level window.google singleton means whichever consumer loads first
	// wins and the others reuse it (same convention as travel_trip_map.js and
	// travel_poi.js).
	let mapsPromise = null;
	function ensureGoogleMaps(apiKey) {
		if (window.google && window.google.maps) return Promise.resolve(window.google.maps);
		if (mapsPromise) return mapsPromise;
		if (!apiKey) return Promise.reject(new Error('Google Maps API key not set'));
		mapsPromise = new Promise((resolve, reject) => {
			const callbackName = '__eeGoogleMapsPickupReady';
			window[callbackName] = () => {
				delete window[callbackName];
				resolve(window.google.maps);
			};
			const script = document.createElement('script');
			script.src =
				'https://maps.googleapis.com/maps/api/js?key=' +
				encodeURIComponent(apiKey) +
				'&callback=' + callbackName +
				'&loading=async';
			script.async = true;
			script.onerror = () => {
				mapsPromise = null; // let a later open retry
				reject(new Error('Google Maps failed to load'));
			};
			document.head.appendChild(script);
		});
		return mapsPromise;
	}

	// ------------------------------------------------------------------- style

	function ensureStyles() {
		if (document.getElementById('ee-pick-routing-css')) return;
		const style = document.createElement('style');
		style.id = 'ee-pick-routing-css';
		style.textContent = `
			.ee-pickroute-dialog .modal-dialog { max-width: min(96vw, 1500px); }
			.ee-pickroute-controls {
				display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end;
				padding-bottom: 10px; margin-bottom: 10px;
				border-bottom: 1px solid var(--border-color);
			}
			.ee-pickroute-control { display: flex; flex-direction: column; gap: 3px; }
			.ee-pickroute-control > label {
				margin: 0; font-size: var(--text-xs); color: var(--text-muted);
				text-transform: uppercase; letter-spacing: .04em; font-weight: 600;
			}
			.ee-pickroute-control select, .ee-pickroute-control input {
				height: 28px; font-size: var(--text-md);
				border: 1px solid var(--border-color); border-radius: var(--border-radius);
				background: var(--control-bg); color: var(--text-color); padding: 0 8px;
			}
			.ee-pickroute-summary {
				margin-left: auto; text-align: right; font-size: var(--text-sm);
				color: var(--text-muted); line-height: 1.4;
			}
			.ee-pickroute-summary b { color: var(--text-color); font-size: var(--text-md); }
			.ee-pickroute-panes {
				display: grid; grid-template-columns: minmax(300px, 5fr) 7fr; gap: 12px;
				height: 62vh; min-height: 380px;
			}
			.ee-pickroute-list { overflow-y: auto; padding-right: 4px; }
			.ee-pickroute-map {
				border-radius: var(--border-radius-md); border: 1px solid var(--border-color);
				min-height: 100%;
			}
			.ee-pickroute-stop {
				display: flex; gap: 10px; padding: 8px; margin-bottom: 6px;
				border: 1px solid var(--border-color); border-radius: var(--border-radius-md);
				background: var(--card-bg); cursor: pointer;
			}
			.ee-pickroute-stop:hover { background: var(--subtle-fg); }
			.ee-pickroute-stop.is-excluded { opacity: .5; }
			.ee-pickroute-stop.is-unroutable { cursor: default; border-style: dashed; }
			.ee-pickroute-seq {
				flex: 0 0 26px; height: 26px; border-radius: 50%;
				display: flex; align-items: center; justify-content: center;
				font-size: var(--text-sm); font-weight: 700; color: #fff;
				background: var(--text-muted);
			}
			.ee-pickroute-stop.is-routed .ee-pickroute-seq { background: ${STOP_COLOR}; }
			.ee-pickroute-body { flex: 1 1 auto; min-width: 0; }
			.ee-pickroute-name { font-weight: 600; color: var(--text-color); }
			.ee-pickroute-meta {
				font-size: var(--text-sm); color: var(--text-muted);
				overflow-wrap: anywhere;
			}
			.ee-pickroute-source { opacity: .7; font-size: var(--text-xs); white-space: nowrap; }
			.ee-pickroute-leg { font-size: var(--text-sm); color: var(--text-muted); font-style: italic; }
			.ee-pickroute-pos { margin-top: 4px; display: flex; flex-wrap: wrap; gap: 4px; }
			.ee-pickroute-po {
				font-size: var(--text-xs); padding: 1px 6px; border-radius: 10px;
				background: var(--control-bg); border: 1px solid var(--border-color);
				color: var(--text-color); text-decoration: none;
			}
			.ee-pickroute-po:hover { background: var(--subtle-fg); }
			.ee-pickroute-toggle { flex: 0 0 auto; }
			.ee-pickroute-section-title {
				font-size: var(--text-xs); text-transform: uppercase; letter-spacing: .04em;
				font-weight: 600; color: var(--text-muted); margin: 12px 0 6px;
			}
			.ee-pickroute-note {
				padding: 8px 10px; margin-bottom: 10px; border-radius: var(--border-radius-md);
				background: var(--subtle-fg); color: var(--text-muted); font-size: var(--text-sm);
			}
			.ee-pickroute-empty { padding: 24px; text-align: center; color: var(--text-muted); }
			@media (max-width: 900px) {
				.ee-pickroute-panes { grid-template-columns: 1fr; height: auto; }
				.ee-pickroute-map { height: 320px; }
				.ee-pickroute-list { max-height: 40vh; }
			}
		`;
		document.head.appendChild(style);
	}

	// ------------------------------------------------------------------ helpers

	const esc = (s) => frappe.utils.escape_html(s == null ? '' : String(s));

	/** A `?api=1` Google Maps directions URL for the whole run. */
	function mapsDirectionsUrl(origin, destination, waypoints) {
		const params = [
			'api=1',
			'travelmode=driving',
			'origin=' + encodeURIComponent(origin),
			'destination=' + encodeURIComponent(destination),
		];
		const legs = (waypoints || []).slice(0, MAX_LINK_WAYPOINTS);
		if (legs.length) {
			params.push('waypoints=' + legs.map(encodeURIComponent).join('|'));
		}
		return 'https://www.google.com/maps/dir/?' + params.join('&');
	}

	/** A single-destination navigation URL, for one leg of the run. */
	function mapsNavigateUrl(destination) {
		return (
			'https://www.google.com/maps/dir/?api=1&travelmode=driving&destination=' +
			encodeURIComponent(destination)
		);
	}

	function poChipsHtml(stop) {
		return (stop.purchase_orders || [])
			.map((po) => {
				const href = '/app/purchase-order/' + encodeURIComponent(po.name);
				const title = [po.status, po.date].filter(Boolean).join(' · ');
				return (
					'<a class="ee-pickroute-po" href="' + href + '" title="' + esc(title) + '"' +
					' target="_blank" rel="noopener">' + esc(po.name) + '</a>'
				);
			})
			.join('');
	}

	function stopSummaryLine(stop) {
		const bits = [];
		if (stop.po_count) {
			bits.push(stop.po_count === 1 ? __('1 PO') : __('{0} POs', [stop.po_count]));
		}
		if (stop.item_count) {
			bits.push(
				stop.item_count === 1 ? __('1 line') : __('{0} lines', [stop.item_count])
			);
		}
		if (stop.amount != null && stop.currency) {
			bits.push(format_currency(stop.amount, stop.currency));
		}
		return bits.join(' · ');
	}

	// --------------------------------------------------------------- controller

	/**
	 * Owns one open dialog: its controls, the selected stops, and whichever of
	 * the three render modes the Google key can actually support.
	 */
	class PickRoutingMap {
		constructor(frm, data, dialog) {
			this.frm = frm;
			this.data = data;
			this.dialog = dialog;
			this.$body = dialog.fields_dict.route.$wrapper;
			this.scope = data.scope;
			this.finish = 'shop';
			this.customFinish = '';
			// Everything with a usable address starts in the run; the driver
			// unticks what they are not collecting today.
			this.selected = new Set(
				(data.stops || []).filter((s) => s.address).map((s) => s.key)
			);
			// Optimised order, once Directions has answered: [{stop, leg}].
			this.ordered = null;
			this.markers = {};
			this.notice = null;
			this.mapBuilt = false;
			// Bumped on every change that invalidates an in-flight Google
			// response. Directions and geocode callbacks fire out of order once
			// the user toggles two stops quickly, and without this the older
			// answer repaints the dialog with a route that is no longer the one
			// on screen.
			this.generation = 0;
			this.destroyed = false;
			// Nothing in flight should touch a closed dialog.
			dialog.$wrapper.on('hidden.bs.modal', () => {
				this.destroyed = true;
				this.generation++;
			});
			this.render();
		}

		// ---- geometry of the run

		/** Selected stops that have an address — the candidates for the run. */
		routableStops() {
			return (this.data.stops || []).filter(
				(s) => s.address && this.selected.has(s.key)
			);
		}

		/** The candidates Google will actually accept in one request. */
		routeStops() {
			return this.routableStops().slice(0, MAX_ROUTE_STOPS);
		}

		/** Candidates past Google's waypoint ceiling — listed, never silently cut. */
		overflowStops() {
			return this.routableStops().slice(MAX_ROUTE_STOPS);
		}

		origin() {
			return this.data.depot.address;
		}

		destination() {
			if (this.finish === 'site') return this.data.project.site_address || this.origin();
			if (this.finish === 'custom') return (this.customFinish || '').trim() || this.origin();
			return this.origin();
		}

		/** Stops in the order currently displayed — optimised if we have it. */
		displayStops() {
			if (this.ordered) return this.ordered.map((entry) => entry.stop);
			return this.routeStops();
		}

		// ---- rendering

		render() {
			this.$body.empty();
			this.$body.append(this.controlsHtml());
			this.$panes = $(
				'<div class="ee-pickroute-panes">' +
					'<div class="ee-pickroute-list"></div>' +
					'<div class="ee-pickroute-map"></div>' +
				'</div>'
			).appendTo(this.$body);
			this.$list = this.$panes.find('.ee-pickroute-list');
			this.$map = this.$panes.find('.ee-pickroute-map');
			this.bindControls();
			this.renderList();
			this.buildMap();
		}

		controlsHtml() {
			const hasSite = !!this.data.project.site_address;
			const scopeOptions = SCOPE_LABELS.map(
				(o) =>
					'<option value="' + o.value + '"' +
					(o.value === this.scope ? ' selected' : '') +
					'>' + esc(o.label) + '</option>'
			).join('');
			const finishOptions =
				'<option value="shop"' + (this.finish === 'shop' ? ' selected' : '') + '>' +
					esc(__('Back at the shop')) + '</option>' +
				'<option value="site"' + (this.finish === 'site' ? ' selected' : '') +
					(hasSite ? '' : ' disabled') + '>' +
					esc(hasSite ? __('Job site') : __('Job site (no address on file)')) + '</option>' +
				'<option value="custom"' + (this.finish === 'custom' ? ' selected' : '') + '>' +
					esc(__('Somewhere else...')) + '</option>';

			return $(
				'<div class="ee-pickroute-controls">' +
					'<div class="ee-pickroute-control">' +
						'<label>' + esc(__('Include')) + '</label>' +
						'<select data-role="scope">' + scopeOptions + '</select>' +
					'</div>' +
					'<div class="ee-pickroute-control">' +
						'<label>' + esc(__('Finish at')) + '</label>' +
						'<select data-role="finish">' + finishOptions + '</select>' +
					'</div>' +
					'<div class="ee-pickroute-control" data-role="custom-wrap"' +
						(this.finish === 'custom' ? '' : ' style="display:none"') + '>' +
						'<label>' + esc(__('Finish address')) + '</label>' +
						'<input type="text" data-role="custom" size="34" placeholder="' +
							esc(__('Street, city, state')) + '" value="' + esc(this.customFinish) + '">' +
					'</div>' +
					'<div class="ee-pickroute-summary" data-role="summary"></div>' +
				'</div>'
			);
		}

		bindControls() {
			const $c = this.$body.find('.ee-pickroute-controls');

			$c.find('[data-role="scope"]').on('change', (e) => {
				this.reload(e.target.value);
			});

			$c.find('[data-role="finish"]').on('change', (e) => {
				this.finish = e.target.value;
				$c.find('[data-role="custom-wrap"]').toggle(this.finish === 'custom');
				if (this.finish === 'custom') {
					$c.find('[data-role="custom"]').trigger('focus');
					// Wait for an address before re-routing to nowhere.
					if (!this.customFinish.trim()) return;
				}
				this.recompute();
			});

			// Re-route on commit (blur / Enter), not on every keystroke — each
			// route is a billable Directions call.
			$c.find('[data-role="custom"]').on('change blur', (e) => {
				const value = e.target.value || '';
				if (value.trim() === this.customFinish.trim()) return;
				this.customFinish = value;
				if (this.finish === 'custom') this.recompute();
			});
		}

		setSummary(html) {
			this.$body.find('[data-role="summary"]').html(html);
		}

		renderList() {
			const stops = this.displayStops();
			const overflow = this.overflowStops();
			const excluded = (this.data.stops || []).filter(
				(s) => s.address && !this.selected.has(s.key)
			);
			const unroutable = (this.data.stops || []).filter((s) => !s.address);

			this.$list.empty();

			if (this.notice) {
				this.$list.append('<div class="ee-pickroute-note">' + esc(this.notice) + '</div>');
			}

			if (!stops.length && !excluded.length && !unroutable.length) {
				this.$list.append(
					'<div class="ee-pickroute-empty">' +
						esc(__('No purchase orders on this project match that filter.')) +
					'</div>'
				);
				return;
			}

			this.$list.append(this.endpointRowHtml('A', __('Start'), this.origin(), DEPOT_COLOR));
			stops.forEach((stop, idx) => {
				this.$list.append(this.stopRowHtml(stop, idx + 1, true));
			});
			this.$list.append(
				this.endpointRowHtml('B', __('Finish'), this.destination(), FINISH_COLOR)
			);

			if (overflow.length) {
				this.$list.append(
					'<div class="ee-pickroute-section-title">' +
						esc(
							__('Past Google\'s {0}-stop limit - run these separately', [
								MAX_ROUTE_STOPS,
							])
						) + '</div>'
				);
				// Still ticked: these ARE selected, they just did not fit. Unticking
				// one is how you make room for a stop you would rather collect.
				overflow.forEach((stop) =>
					this.$list.append(this.stopRowHtml(stop, null, false, true))
				);
			}

			if (excluded.length) {
				this.$list.append(
					'<div class="ee-pickroute-section-title">' +
						esc(__('Not on this run')) + '</div>'
				);
				excluded.forEach((stop) =>
					this.$list.append(this.stopRowHtml(stop, null, false, false))
				);
			}

			if (unroutable.length) {
				this.$list.append(
					'<div class="ee-pickroute-section-title">' +
						esc(__('No address on file')) + '</div>'
				);
				unroutable.forEach((stop) => this.$list.append(this.unroutableRowHtml(stop)));
			}

			this.bindListRows();
		}

		endpointRowHtml(badge, label, address, color) {
			return (
				'<div class="ee-pickroute-stop is-unroutable">' +
					'<div class="ee-pickroute-seq" style="background:' + color + '">' +
						esc(badge) + '</div>' +
					'<div class="ee-pickroute-body">' +
						'<div class="ee-pickroute-name">' + esc(label) + '</div>' +
						'<div class="ee-pickroute-meta">' + esc(address || __('Not set')) + '</div>' +
					'</div>' +
				'</div>'
			);
		}

		/**
		 * One stop row. `routed` drives the styling (on the route or greyed);
		 * `checked` is the tick state, which is NOT the same thing — a stop past
		 * Google's waypoint ceiling is still selected, just not routed.
		 */
		stopRowHtml(stop, seq, routed, checked) {
			const entry = this.ordered && this.ordered.find((e) => e.stop.key === stop.key);
			const leg = entry && entry.leg;
			const ticked = checked === undefined ? routed : checked;
			return (
				'<div class="ee-pickroute-stop' +
					(routed ? ' is-routed' : ' is-excluded') +
					'" data-key="' + esc(stop.key) + '">' +
					'<div class="ee-pickroute-seq">' + (seq == null ? '–' : seq) + '</div>' +
					'<div class="ee-pickroute-body">' +
						'<div class="ee-pickroute-name">' + esc(stop.supplier_name) + '</div>' +
						'<div class="ee-pickroute-meta">' + esc(stop.address) +
							(ADDRESS_SOURCE_LABELS[stop.address_source]
								? ' <span class="ee-pickroute-source">' +
									esc(ADDRESS_SOURCE_LABELS[stop.address_source]) + '</span>'
								: '') +
						'</div>' +
						(leg
							? '<div class="ee-pickroute-leg">' +
								esc(
									__('{0}, {1} from the previous stop', [
										leg.distance,
										leg.duration,
									])
								) + '</div>'
							: '') +
						'<div class="ee-pickroute-meta">' + esc(stopSummaryLine(stop)) + '</div>' +
						'<div class="ee-pickroute-pos">' + poChipsHtml(stop) +
							'<a class="ee-pickroute-po" href="' + mapsNavigateUrl(stop.address) +
								'" target="_blank" rel="noopener">' + esc(__('Navigate')) + '</a>' +
						'</div>' +
					'</div>' +
					'<div class="ee-pickroute-toggle">' +
						'<input type="checkbox" data-role="include" ' +
							(ticked ? 'checked' : '') + '>' +
					'</div>' +
				'</div>'
			);
		}

		unroutableRowHtml(stop) {
			const href = stop.supplier
				? '/app/supplier/' + encodeURIComponent(stop.supplier)
				: null;
			return (
				'<div class="ee-pickroute-stop is-unroutable">' +
					'<div class="ee-pickroute-seq">!</div>' +
					'<div class="ee-pickroute-body">' +
						'<div class="ee-pickroute-name">' + esc(stop.supplier_name) + '</div>' +
						'<div class="ee-pickroute-meta">' +
							esc(__('No address on the PO or the supplier record.')) + '</div>' +
						'<div class="ee-pickroute-meta">' + esc(stopSummaryLine(stop)) + '</div>' +
						'<div class="ee-pickroute-pos">' + poChipsHtml(stop) +
							(href
								? '<a class="ee-pickroute-po" href="' + href +
									'" target="_blank" rel="noopener">' +
									esc(__('Add an address')) + '</a>'
								: '') +
						'</div>' +
					'</div>' +
				'</div>'
			);
		}

		bindListRows() {
			this.$list.find('[data-role="include"]').on('change', (e) => {
				const key = $(e.target).closest('.ee-pickroute-stop').data('key');
				if (!key) return;
				if (e.target.checked) this.selected.add(key);
				else this.selected.delete(key);
				this.recompute();
			});

			// Clicking the row (not its checkbox or links) pans to that pin.
			this.$list.find('.ee-pickroute-stop[data-key]').on('click', (e) => {
				if ($(e.target).is('input, a') || $(e.target).closest('a').length) return;
				const key = $(e.currentTarget).data('key');
				const marker = this.markers && this.markers[key];
				if (marker && this.map) {
					this.map.panTo(marker.getPosition());
					this.map.setZoom(Math.max(this.map.getZoom(), 13));
				}
			});
		}

		// ---- data + route

		/** True once a newer change (or a closed dialog) superseded this request. */
		isStale(generation) {
			return this.destroyed || generation !== this.generation;
		}

		reload(scope) {
			this.scope = scope;
			this.ordered = null;
			this.notice = null;
			this.generation++;
			const generation = this.generation;
			frappe.call({
				method: 'erpnext_enhancements.api.pickup_routing.get_pickup_route_data',
				args: { project: this.frm.doc.name, scope: scope },
				freeze: true,
				freeze_message: __('Rebuilding the run...'),
				callback: (r) => {
					if (!r.message || this.isStale(generation)) return;
					this.data = r.message;
					this.selected = new Set(
						(this.data.stops || []).filter((s) => s.address).map((s) => s.key)
					);
					this.mapBuilt = false;
					this.render();
				},
			});
		}

		/** Re-run the optimiser after the stop set or the finish point changed. */
		recompute() {
			this.ordered = null;
			this.generation++;
			// Repaint before asking Google, not after: a Directions round trip is
			// hundreds of milliseconds and there is no freeze on it, so without
			// this the stop the driver just unticked stays in the routed list —
			// still numbered, still ticked — until the response lands.
			this.renderList();
			this.updatePrimaryAction();
			if (this.maps && this.map) {
				this.setSummary(esc(__('Optimising...')));
				this.route();
			}
		}

		buildMap() {
			if (this.mapBuilt || this.destroyed) return;
			const container = this.$map[0];
			// A dialog body is display:none until show(); a map built in a 0x0
			// box renders blank. Wait for real dimensions — but only while the
			// container is still in the document, or closing the dialog before it
			// ever sized would leave this rescheduling itself every frame forever.
			if (!container || !container.offsetWidth || !container.offsetHeight) {
				if (container && container.isConnected) {
					window.requestAnimationFrame(() => this.buildMap());
				}
				return;
			}
			this.mapBuilt = true;

			const generation = this.generation;
			const apiKey = this.data.api_key || '';
			if (!apiKey) {
				this.degrade(
					__('Add a Google Maps API key in Travel Settings to draw the route on a map.')
				);
				return;
			}

			ensureGoogleMaps(apiKey)
				.then((maps) => {
					if (this.isStale(generation)) return;
					this.maps = maps;
					this.map = new maps.Map(container, {
						zoom: 9,
						center: { lat: 40.8894, lng: -111.8808 }, // Bountiful; fitBounds adjusts
						mapTypeControl: false,
						streetViewControl: false,
						fullscreenControl: true,
						gestureHandling: 'cooperative',
					});
					this.directionsService = new maps.DirectionsService();
					this.directionsRenderer = new maps.DirectionsRenderer({
						map: this.map,
						suppressMarkers: true, // we draw our own numbered pins
						polylineOptions: { strokeColor: STOP_COLOR, strokeWeight: 5, strokeOpacity: 0.8 },
					});
					this.info = new maps.InfoWindow();
					this.route();
				})
				.catch(() => {
					this.degrade(
						__('Map unavailable (Google Maps failed to load - check the API key).')
					);
				});
		}

		/** Ask Google for the optimised order, then draw it. */
		route() {
			const stops = this.routeStops();
			this.clearMarkers();

			if (!stops.length) {
				this.directionsRenderer.setDirections({ routes: [] });
				this.ordered = null;
				this.notice = null;
				this.setSummary('');
				this.renderList();
				this.updatePrimaryAction();
				return;
			}

			const waypoints = stops.map((s) => ({ location: s.address, stopover: true }));
			const generation = this.generation;

			this.directionsService.route(
				{
					origin: this.origin(),
					destination: this.destination(),
					waypoints: waypoints,
					optimizeWaypoints: true,
					travelMode: this.maps.TravelMode.DRIVING,
					unitSystem: this.maps.UnitSystem.IMPERIAL,
				},
				(result, status) => {
					// The user changed the run while this was in flight.
					if (this.isStale(generation)) return;
					if (status === 'OK' && result.routes && result.routes.length) {
						this.drawRoute(stops, result);
					} else if (status === 'REQUEST_DENIED') {
						this.degrade(
							__(
								'Stops are in purchase-order sequence, not drive-time order: enable the Directions API on the Google Maps key to optimise the route.'
							),
							stops
						);
					} else if (status === 'ZERO_RESULTS' || status === 'NOT_FOUND') {
						this.degrade(
							__(
								'Google could not find a driving route through these addresses - check them on the supplier records.'
							),
							stops
						);
					} else {
						this.degrade(__('Could not build the route right now ({0}).', [status]), stops);
					}
				}
			);
		}

		drawRoute(stops, result) {
			const route = result.routes[0];
			const overflow = this.overflowStops().length;
			this.notice = overflow
				? __(
					'Google optimises at most {0} stops at a time — {1} more are listed below and are not on this route.',
					[MAX_ROUTE_STOPS, overflow]
				)
				: null;
			this.directionsRenderer.setDirections(result);

			// waypoint_order maps the optimised sequence back onto the stops we
			// submitted, in submission order. legs[i] is the drive that *arrives*
			// at the i-th optimised stop, so the two indexes line up directly.
			const order = route.waypoint_order || stops.map((_s, i) => i);
			const legs = route.legs || [];

			this.ordered = order.map((originalIdx, position) => {
				const leg = legs[position];
				return {
					stop: stops[originalIdx],
					leg: leg
						? {
							distance: leg.distance && leg.distance.text,
							duration: leg.duration && leg.duration.text,
						}
						: null,
				};
			});

			const meters = legs.reduce((sum, leg) => sum + ((leg.distance && leg.distance.value) || 0), 0);
			const seconds = legs.reduce((sum, leg) => sum + ((leg.duration && leg.duration.value) || 0), 0);
			this.setSummary(
				'<b>' + esc(__('{0} stops', [this.ordered.length])) + '</b><br>' +
				esc(
					__('{0} mi · {1} driving', [
						(meters / 1609.344).toFixed(1),
						formatDuration(seconds),
					])
				)
			);

			this.placeMarkers(legs);
			this.renderList();
			this.updatePrimaryAction();
		}

		placeMarkers(legs) {
			this.clearMarkers();
			if (!this.maps || !this.map) return;

			const bounds = new this.maps.LatLngBounds();
			const add = (position, label, color, title, html) => {
				const marker = new this.maps.Marker({
					position: position,
					map: this.map,
					title: title,
					label: { text: String(label), color: '#ffffff', fontWeight: '700', fontSize: '12px' },
					icon: {
						path: this.maps.SymbolPath.CIRCLE,
						scale: 13,
						fillColor: color,
						fillOpacity: 1,
						strokeColor: '#ffffff',
						strokeWeight: 2,
					},
				});
				if (html) {
					marker.addListener('click', () => {
						this.info.setContent(html);
						this.info.open({ map: this.map, anchor: marker });
					});
				}
				bounds.extend(position);
				return marker;
			};

			this.markers = {};
			if (legs.length) {
				// Keyed like the rest: an unkeyed marker is one clearMarkers()
				// can never take off the map, so re-routing would stack them.
				this.markers.__depot = add(legs[0].start_location, 'A', DEPOT_COLOR, __('Shop'), null);
			}
			this.ordered.forEach((entry, idx) => {
				const leg = legs[idx];
				if (!leg) return;
				this.markers[entry.stop.key] = add(
					leg.end_location,
					idx + 1,
					STOP_COLOR,
					entry.stop.supplier_name,
					markerHtml(entry.stop, idx + 1)
				);
			});
			// The final leg ends at the finish point, which is not a stop.
			if (legs.length) {
				const last = legs[legs.length - 1];
				this.markers['__finish'] = add(
					last.end_location,
					'B',
					FINISH_COLOR,
					__('Finish'),
					null
				);
			}
			if (!bounds.isEmpty()) this.map.fitBounds(bounds, 56);
		}

		clearMarkers() {
			Object.keys(this.markers || {}).forEach((key) => {
				const marker = this.markers[key];
				if (marker && marker.setMap) marker.setMap(null);
			});
			this.markers = {};
		}

		/**
		 * Drop to a lesser render mode, keeping the run usable. With a live map
		 * we still geocode the stops and drop pins in list order; without one the
		 * list plus the Google Maps hand-off is all that is left.
		 */
		degrade(message, stops) {
			this.notice = message;
			this.ordered = null;
			this.setSummary('');
			this.renderList();
			this.updatePrimaryAction();

			if (!this.maps || !this.map || !stops || !stops.length) return;
			if (this.directionsRenderer) this.directionsRenderer.setDirections({ routes: [] });

			const generation = this.generation;
			const geocoder = new this.maps.Geocoder();
			const bounds = new this.maps.LatLngBounds();
			this.clearMarkers();
			this.geocoded = this.geocoded || {};
			let plotted = 0;

			const drop = (stop, idx, position) => {
				this.markers[stop.key] = this.stopMarker(stop, idx + 1, position);
				bounds.extend(position);
				plotted++;
				if (plotted > 1) {
					this.map.fitBounds(bounds, 56);
				} else {
					this.map.setCenter(position);
					this.map.setZoom(12);
				}
			};

			stops.forEach((stop, idx) => {
				// Geocoding is billable and these addresses do not move, so a
				// second degrade in the same dialog reuses the points.
				if (this.geocoded[stop.address]) {
					drop(stop, idx, this.geocoded[stop.address]);
					return;
				}
				geocoder.geocode({ address: stop.address }, (results, status) => {
					// Google offers no cancel for an in-flight geocode, so this is
					// what stops a superseded batch stranding pins the current
					// clearMarkers() never saw.
					if (this.isStale(generation)) return;
					if (status !== 'OK' || !results || !results.length) return;
					this.geocoded[stop.address] = results[0].geometry.location;
					drop(stop, idx, this.geocoded[stop.address]);
				});
			});
		}

		/** A numbered pin for one stop, with its info window. */
		stopMarker(stop, seq, position) {
			const marker = new this.maps.Marker({
				position: position,
				map: this.map,
				title: stop.supplier_name,
				label: { text: String(seq), color: '#ffffff', fontWeight: '700', fontSize: '12px' },
				icon: {
					path: this.maps.SymbolPath.CIRCLE,
					scale: 13,
					fillColor: STOP_COLOR,
					fillOpacity: 1,
					strokeColor: '#ffffff',
					strokeWeight: 2,
				},
			});
			marker.addListener('click', () => {
				this.info.setContent(markerHtml(stop, seq));
				this.info.open({ map: this.map, anchor: marker });
			});
			return marker;
		}

		/** Keep "Open in Google Maps" pointing at whatever is on screen now. */
		updatePrimaryAction() {
			const stops = this.displayStops();
			this.dialog.set_primary_action(__('Open in Google Maps'), () => {
				if (!stops.length) {
					frappe.msgprint(__('Nothing to route - no stop on this run has an address.'));
					return;
				}
				const addresses = stops.map((s) => s.address);
				// The last stop becomes the URL's destination only when the run
				// has no explicit finish point of its own; here it always does.
				const url = mapsDirectionsUrl(this.origin(), this.destination(), addresses);
				if (addresses.length > MAX_LINK_WAYPOINTS) {
					frappe.show_alert({
						message: __(
							'Google Maps links carry at most {0} stops - the first {0} are in the link, the full order is in the list.',
							[MAX_LINK_WAYPOINTS]
						),
						indicator: 'orange',
					});
				}
				window.open(url, '_blank', 'noopener');
			});
		}
	}

	function markerHtml(stop, seq) {
		return (
			'<div style="min-width:180px">' +
				'<b>' + seq + '. ' + esc(stop.supplier_name) + '</b><br>' +
				esc(stop.address) + '<br>' +
				(stop.phone ? esc(stop.phone) + '<br>' : '') +
				esc(stopSummaryLine(stop)) + '<br>' +
				'<a href="' + mapsNavigateUrl(stop.address) + '" target="_blank" rel="noopener">' +
					__('Navigate') + '</a>' +
			'</div>'
		);
	}

	function formatDuration(seconds) {
		const total = Math.round(seconds / 60);
		const hours = Math.floor(total / 60);
		const minutes = total % 60;
		if (!hours) return __('{0} min', [minutes]);
		return __('{0} h {1} min', [hours, minutes]);
	}

	// ------------------------------------------------------------------- launch

	function openPickRoutingMap(frm) {
		if (frm.is_new()) {
			frappe.msgprint(__('Please save the Project before building a pick route.'));
			return;
		}
		ensureStyles();

		frappe.call({
			method: 'erpnext_enhancements.api.pickup_routing.get_pickup_route_data',
			args: { project: frm.doc.name },
			freeze: true,
			freeze_message: __('Working out the run...'),
			callback: (r) => {
				const data = r.message;
				if (!data) {
					frappe.msgprint(__('Could not load the pick route right now.'));
					return;
				}

				const dialog = new frappe.ui.Dialog({
					title: __('Pick Routing Map'),
					size: 'extra-large',
					fields: [{ fieldtype: 'HTML', fieldname: 'route' }],
				});
				dialog.$wrapper.addClass('ee-pickroute-dialog');
				// Build only once the modal is on screen and sized — see the
				// header note on the 0x0 trap.
				dialog.show();
				const controller = new PickRoutingMap(frm, data, dialog);
				controller.updatePrimaryAction();
			},
		});
	}

	frappe.ui.form.on('Project', {
		custom_btn_pick_routing_map: function (frm) {
			openPickRoutingMap(frm);
		},
	});
})();
