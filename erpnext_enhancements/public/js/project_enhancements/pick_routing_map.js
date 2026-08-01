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
	// Deliberately still 23, not the 25 the Routes API documents. The legacy
	// DirectionsService counted origin + destination + 23 intermediates toward 25; Routes
	// documents 25 *intermediates*. But no lower cap for optimisation is documented on
	// Routes, and that is a claim from absence rather than a positive statement -- and the
	// legacy engine is still rung 2, where 23 is the real ceiling. Raise it only after 25
	// intermediates with optimizeWaypointOrder has been run against a live key.
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

	// The pick sheet opens in its own window, so desk theme vars do not exist
	// there — every colour is literal, and the palette matches the app's other
	// print surfaces (#333 / #555 / #777 / #ccc / #eee / #f4f5f7).
	const PICK_SHEET_CSS = `
		* { box-sizing: border-box; }
		body {
			font-family: 'Helvetica Neue', Arial, sans-serif; color: #222;
			font-size: 11px; margin: 14px;
		}
		h1 { font-size: 19px; margin: 0; }
		h2 {
			font-size: 12px; text-transform: uppercase; letter-spacing: .04em;
			color: #777; margin: 16px 0 6px; border-bottom: 1px solid #ccc;
			padding-bottom: 3px;
		}
		.sheet-head { border-bottom: 2px solid #333; padding-bottom: 6px; margin-bottom: 10px; }
		.sheet-sub { font-size: 13px; margin-top: 2px; }
		.sheet-meta { color: #777; font-size: 10px; margin-top: 2px; }
		.sheet-endpoint {
			padding: 4px 6px; background: #f4f5f7; border-radius: 3px; margin: 6px 0;
		}
		.sheet-stop { margin: 10px 0 12px; page-break-inside: avoid; }
		.sheet-stop-head { display: flex; align-items: center; gap: 7px; }
		.sheet-seq {
			display: inline-flex; align-items: center; justify-content: center;
			width: 20px; height: 20px; border-radius: 50%; background: #333;
			color: #fff; font-weight: 700; font-size: 11px;
		}
		.sheet-stop-name { font-size: 14px; font-weight: 700; }
		.sheet-stop-addr { color: #555; margin: 2px 0 5px 27px; }
		.sheet-po { margin: 0 0 7px 27px; }
		.sheet-po-head { font-weight: 600; color: #555; margin-bottom: 2px; }
		table { width: 100%; border-collapse: collapse; }
		th {
			text-align: left; font-weight: 600; color: #777; font-size: 10px;
			border-bottom: 1px solid #ccc; padding: 3px 4px;
		}
		td { padding: 3px 4px; border-bottom: 1px solid #eee; vertical-align: top; }
		td.num, th.num { text-align: right; white-space: nowrap; }
		td.code { font-weight: 600; white-space: nowrap; }
		td.tick, th.tick { width: 16px; font-size: 13px; }
		tr.is-done td { color: #999; }
		.sheet-sign { margin-top: 22px; padding-top: 8px; border-top: 1px solid #ccc; }
		.sheet-foot { margin-top: 10px; font-style: italic; }
		@page { margin: 12mm; }
		@media print { body { margin: 0; } }
	`;

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
		if (mapsPromise) return mapsPromise;

		// The old fast path returned as soon as `window.google.maps` existed. That is
		// unsafe now: the singleton is shared with travel_trip_map.js and travel_poi.js,
		// neither of which imports a library, so whichever consumer loads first decides
		// what is present. `google.maps.routes` and `google.maps.marker` stay *undefined*
		// until importLibrary is awaited, even though the root namespace looks complete.
		// Resolve on the libraries we actually use, not on the namespace.
		mapsPromise = (async () => {
			if (!(window.google && window.google.maps && window.google.maps.importLibrary)) {
				if (!apiKey) throw new Error('Google Maps API key not set');
				await new Promise((resolve, reject) => {
					const callbackName = '__eeGoogleMapsPickupReady';
					window[callbackName] = () => {
						delete window[callbackName];
						resolve();
					};
					const script = document.createElement('script');
					script.src =
						'https://maps.googleapis.com/maps/api/js?key=' +
						encodeURIComponent(apiKey) +
						'&callback=' + callbackName +
						// Keep. Verified against Google bundle v3.65.11b: with this flag the
						// legacy root namespace (DirectionsService, TravelMode, UnitSystem,
						// Marker) *is* fully populated when the callback fires. An earlier
						// theory that this flag emptied the namespace was wrong.
						'&loading=async';
					script.async = true;
					script.onerror = () => reject(new Error('Google Maps failed to load'));
					document.head.appendChild(script);
				});
			}
			// `maps` and `core` cover everything the legacy path needs; `routes` is
			// requested lazily in computeViaRoutes so a project without the Routes API
			// enabled does not fail the whole map load.
			await window.google.maps.importLibrary('maps');
			return window.google.maps;
		})().catch((err) => {
			mapsPromise = null; // let a later open retry
			throw err;
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
			.ee-pickroute-detail { margin-top: 6px; }
			.ee-pickroute-detail > summary {
				cursor: pointer; font-size: var(--text-xs); color: var(--text-muted);
				list-style: none; user-select: none; display: inline-flex; gap: 4px;
				padding: 1px 6px; border-radius: 10px; border: 1px solid var(--border-color);
			}
			.ee-pickroute-detail > summary:hover { background: var(--subtle-fg); }
			.ee-pickroute-detail > summary::-webkit-details-marker { display: none; }
			.ee-pickroute-detail > summary::before { content: '\\25B8'; }
			.ee-pickroute-detail[open] > summary::before { content: '\\25BE'; }
			.ee-pickroute-po-group { margin-top: 6px; }
			.ee-pickroute-po-head {
				font-size: var(--text-xs); color: var(--text-muted); font-weight: 600;
				border-bottom: 1px solid var(--border-color); padding-bottom: 2px;
			}
			.ee-pickroute-line {
				display: flex; gap: 6px; align-items: baseline;
				padding: 3px 0; border-bottom: 1px solid var(--border-color);
				font-size: var(--text-sm);
			}
			.ee-pickroute-line.is-done { opacity: .45; }
			.ee-pickroute-line-code { font-weight: 600; white-space: nowrap; }
			.ee-pickroute-line-name {
				flex: 1 1 auto; min-width: 0; color: var(--text-muted);
				overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
			}
			.ee-pickroute-line-qty { white-space: nowrap; font-variant-numeric: tabular-nums; }
			.ee-pickroute-line-sub { font-size: var(--text-xs); color: var(--text-muted); }
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

	// ------------------------------------------------------------- item lines

	// Float quantities never land exactly on zero, so "fully received" is a
	// tolerance test, not an equality one. Matches TOLERANCE in
	// procurement_quantities.py, which computes the same idea for the tracker.
	const QTY_TOLERANCE = 0.005;

	/** Trim a float quantity to something a human reads: 2, not 2.0. */
	function qtyText(value) {
		const n = Number(value || 0);
		if (!isFinite(n)) return '0';
		return String(Math.round(n * 1000) / 1000);
	}

	/**
	 * The one place a PO line's numbers are worked out.
	 *
	 * Both the in-dialog disclosure and the printed pick sheet consume this, so a
	 * driver holding the sheet cannot be told a different outstanding quantity
	 * from the one on screen. Presentation differs by medium (a phone gets a
	 * compact list, paper gets a table); the arithmetic does not fork.
	 */
	function lineModel(line) {
		const ordered = Number(line.qty || 0);
		const received = Number(line.received_qty || 0);
		const outstanding = Math.max(0, ordered - received);
		return {
			code: line.item_code || '',
			name: line.item_name || '',
			uom: line.uom || '',
			ordered: ordered,
			received: received,
			outstanding: outstanding,
			// Dimmed rather than dropped: "did we already collect that?" is asked
			// at the counter, and absence from the list is ambiguous between
			// "already collected" and "never ordered".
			done: outstanding <= QTY_TOLERANCE,
		};
	}

	/** Every line at a stop, flattened, for counting. */
	function stopLineModels(stop) {
		const out = [];
		(stop.purchase_orders || []).forEach((po) => {
			(po.items || []).forEach((line) => out.push(lineModel(line)));
		});
		return out;
	}

	/** "4 lines · 3 still to collect" — the disclosure's summary label. */
	function stopLinesLabel(stop) {
		const models = stopLineModels(stop);
		if (!models.length) return __('No lines');
		const open = models.filter((m) => !m.done).length;
		const lines = models.length === 1 ? __('1 line') : __('{0} lines', [models.length]);
		if (!open) return lines + ' · ' + __('all collected');
		return lines + ' · ' + __('{0} still to collect', [open]);
	}

	function poGroupHeadText(po) {
		const bits = [po.name];
		if (po.status) bits.push(po.status);
		if (po.required_by) bits.push(__('needed {0}', [frappe.datetime.str_to_user(po.required_by)]));
		return bits.join(' · ');
	}

	/** Compact per-line list for the dialog's left pane (narrow, phone-first). */
	function stopLinesHtml(stop) {
		const groups = (stop.purchase_orders || []).map((po) => {
			const rows = (po.items || [])
				.map((line) => {
					const m = lineModel(line);
					const qty = m.done
						? __('collected')
						: __('{0} {1}', [qtyText(m.outstanding), m.uom]);
					const sub = m.received
						? ' <span class="ee-pickroute-line-sub">' +
							esc(__('({0} of {1} received)', [qtyText(m.received), qtyText(m.ordered)])) +
							'</span>'
						: '';
					return (
						'<div class="ee-pickroute-line' + (m.done ? ' is-done' : '') + '">' +
							'<span class="ee-pickroute-line-code">' + esc(m.code) + '</span>' +
							'<span class="ee-pickroute-line-name" title="' + esc(m.name) + '">' +
								esc(m.name) + '</span>' +
							'<span class="ee-pickroute-line-qty">' + esc(qty) + sub + '</span>' +
						'</div>'
					);
				})
				.join('');
			return (
				'<div class="ee-pickroute-po-group">' +
					'<div class="ee-pickroute-po-head">' + esc(poGroupHeadText(po)) + '</div>' +
					(rows || '<div class="ee-pickroute-line is-done">' +
						esc(__('No lines on this order.')) + '</div>') +
				'</div>'
			);
		});
		return groups.join('');
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
			// Which stops have their line list open. Held on the controller, not
			// in the DOM: renderList() rebuilds every row from scratch on each
			// tick, re-route and reorder, so anything read off the old nodes is
			// lost. Without this, unticking one stop silently collapses the lines
			// the driver was reading on another.
			this.expanded = new Set();
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
			// Held while waiting for the modal to finish sizing; see waitForContainerSize.
			this.sizeWatch = null;
			// Set once a Routes call has failed in this dialog, so later re-routes do not
			// each pay another doomed billable request before falling back.
			this.routesUnavailable = false;
			// Polylines drawn by the Routes engine. The legacy engine uses
			// directionsRenderer instead; clearRouteLine() tears down whichever is live.
			this.polylines = null;
			// Nothing in flight should touch a closed dialog.
			dialog.$wrapper.on('hidden.bs.modal', () => {
				this.destroyed = true;
				this.generation++;
				this.stopSizeWatch();
			});
			// The event Bootstrap provides for exactly this: the modal is on screen and
			// laid out. buildMap() is idempotent behind the `mapBuilt` latch, so calling
			// it from both here and render() costs nothing and means the map no longer
			// depends on winning a race against the show animation.
			dialog.$wrapper.on('shown.bs.modal', () => this.buildMap());
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
						this.stopDetailHtml(stop) +
					'</div>' +
					'<div class="ee-pickroute-toggle">' +
						'<input type="checkbox" data-role="include" ' +
							(ticked ? 'checked' : '') + '>' +
					'</div>' +
				'</div>'
			);
		}

		/**
		 * The collapsible line list for one stop.
		 *
		 * Rendered eagerly rather than on first open: the lines are already in the
		 * payload, a whole project is tens of rows, and lazy rendering would mean
		 * a second code path that renderList() has to re-run anyway.
		 */
		stopDetailHtml(stop) {
			const models = stopLineModels(stop);
			if (!models.length) return '';
			return (
				'<details class="ee-pickroute-detail" data-role="detail"' +
					' data-detail-key="' + esc(stop.key) + '"' +
					(this.expanded.has(stop.key) ? ' open' : '') + '>' +
					'<summary>' + esc(stopLinesLabel(stop)) + '</summary>' +
					stopLinesHtml(stop) +
				'</details>'
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
						// Lines shown here too: a stop with no address is precisely
						// the one where somebody has to ring the vendor and ask, and
						// the first question back is "what are you collecting?".
						this.stopDetailHtml(stop) +
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

			// Opening the lines is a local, free action: it records the state and
			// stops there. No recompute() — that would spend a billable Directions
			// call every time a driver looked at what they were collecting.
			this.$list.find('[data-role="detail"]').on('toggle', (e) => {
				const key = $(e.currentTarget).attr('data-detail-key');
				if (!key) return;
				if (e.currentTarget.open) this.expanded.add(key);
				else this.expanded.delete(key);
			});

			// Clicking the row (not its checkbox, links, or line list) pans to that
			// pin. Without the details guard, opening the lines also yanks the map.
			this.$list.find('.ee-pickroute-stop[data-key]').on('click', (e) => {
				if ($(e.target).is('input, a') || $(e.target).closest('a').length) return;
				if ($(e.target).closest('[data-role="detail"]').length) return;
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
					// render() replaces the map container, so any wait still running is
					// observing a node that is about to be detached. Drop it and let
					// buildMap() start a fresh one against the new element.
					this.stopSizeWatch();
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
				this.safeRoute();
			}
		}

		/**
		 * Decide whether the map can be built yet, and wait properly if it cannot.
		 *
		 * A dialog body is `display:none` until the modal finishes animating in, and a
		 * map initialised in a 0x0 box renders blank. The first version waited for real
		 * dimensions by re-scheduling itself on `requestAnimationFrame`, and that is the
		 * bug this replaces: **the chain could die and take the whole feature with it,
		 * silently.** On production the very first call measured 0x0, the retry never
		 * converged, and `ensureGoogleMaps()` was therefore never reached — no map, no
		 * route, no console error, and no notice, because every message this class can
		 * show lives *past* that early return. It presented as a Google Directions
		 * problem and cost a long diagnosis. Changing any control re-rendered against a
		 * sized container and the map appeared instantly, which is what identified it.
		 *
		 * So: observe the element rather than poll it, bound the wait, and make giving up
		 * *visible*. A blank pane that explains itself is recoverable; a blank pane that
		 * says nothing sends you to the wrong system.
		 */
		buildMap() {
			if (this.mapBuilt || this.destroyed) return;
			const container = this.$map[0];
			if (!container) return;
			if (container.offsetWidth && container.offsetHeight) {
				this.startMap(container);
				return;
			}
			this.waitForContainerSize(container);
		}

		/** Wait for the modal to finish sizing, then build. Always terminates. */
		waitForContainerSize(container) {
			if (this.sizeWatch || this.destroyed) return;

			const attempt = () => {
				if (this.destroyed || this.mapBuilt) {
					this.stopSizeWatch();
					return true;
				}
				const el = this.$map[0];
				if (el && el.offsetWidth && el.offsetHeight) {
					this.stopSizeWatch();
					this.startMap(el);
					return true;
				}
				return false;
			};

			this.sizeWatch = { observer: null, timer: null, deadline: null };

			// The element tells us the moment it has a box — no polling cadence to tune.
			if (window.ResizeObserver) {
				this.sizeWatch.observer = new window.ResizeObserver(() => attempt());
				this.sizeWatch.observer.observe(container);
			}
			// Belt and braces. ResizeObserver does not fire for a `display:none` ancestor
			// on every engine, so a slow interval backs it up; it is cheap because it
			// stops the instant either one wins.
			this.sizeWatch.timer = window.setInterval(attempt, 120);
			// And a hard stop, so "never sized" ends in a message rather than in silence.
			this.sizeWatch.deadline = window.setTimeout(() => {
				if (this.destroyed || this.mapBuilt) return;
				this.stopSizeWatch();
				this.mapBuilt = true; // do not keep retrying; the list is still usable
				this.degrade(
					__('The map could not be sized in this window, so the route was not drawn. The stops and their items below are complete; "Open in Google Maps" still works.')
				);
			}, 8000);
		}

		stopSizeWatch() {
			if (!this.sizeWatch) return;
			if (this.sizeWatch.observer) this.sizeWatch.observer.disconnect();
			if (this.sizeWatch.timer) window.clearInterval(this.sizeWatch.timer);
			if (this.sizeWatch.deadline) window.clearTimeout(this.sizeWatch.deadline);
			this.sizeWatch = null;
		}

		/** The container is real and sized: load Google and draw. */
		startMap(container) {
			if (this.mapBuilt || this.destroyed) return;
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
					// Only `destroyed` aborts here, NOT isStale. The map object itself is
					// not generation-specific; only the route drawn on it is, and route()
					// does its own staleness checks. Returning on a bumped generation left
					// `mapBuilt` latched true with no map ever created, so buildMap() would
					// never retry -- a permanently blank pane with no message, which is the
					// same silent failure v1.202.3 was about. Ticking a stop while Google
					// was still loading was enough to trigger it.
					if (this.destroyed) return;
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
					this.safeRoute();
				})
				.catch(() => {
					this.degrade(
						__('Map unavailable (Google Maps failed to load - check the API key).')
					);
				});
		}

		/**
		 * Call route() so a rejection can never escape.
		 *
		 * route() became async for the Routes migration. An uncaught rejection from an
		 * async method is invisible: it skips all four degradation rungs at once and leaves
		 * the dialog showing nothing, which is precisely the failure v1.202.3 was about.
		 * Every caller goes through here.
		 */
		safeRoute() {
			try {
				const p = this.route();
				if (p && p.catch) {
					p.catch((err) => {
						console.error('[pickroute] routing threw', err);
						if (!this.destroyed) {
							this.degrade(__('Could not build the route right now.'), this.routeStops());
						}
					});
				}
			} catch (err) {
				console.error('[pickroute] routing threw', err);
				this.degrade(__('Could not build the route right now.'), this.routeStops());
			}
		}

		/** Take whichever route line is on the map off it, whichever engine drew it. */
		clearRouteLine() {
			if (this.directionsRenderer) this.directionsRenderer.setDirections({ routes: [] });
			(this.polylines || []).forEach((p) => {
				if (p && p.setMap) p.setMap(null);
			});
			this.polylines = null;
		}

		/**
		 * Optimised route via the modern `routes.Route.computeRoutes`.
		 *
		 * Returns the same normalised shape as the legacy path so `drawRoute` does not
		 * care which engine ran. Three renames in here are silent-corruption traps rather
		 * than compile errors, so each is called out where it happens.
		 */
		async computeViaRoutes(stops) {
			// `routes` is imported here rather than in ensureGoogleMaps so a project
			// without the Routes API enabled still gets a working map on the legacy path.
			const lib = await window.google.maps.importLibrary('routes');
			const Route = lib && lib.Route;
			if (!Route) throw new Error('Routes library did not provide Route');

			const response = await Route.computeRoutes({
				origin: this.origin(),
				destination: this.destination(),
				// was `waypoints: [{location, stopover: true}]`. `stopover` has no
				// equivalent -- it is the default, and the inverse (`via: true`) is
				// documented as incompatible with optimizeWaypointOrder.
				intermediates: stops.map((s) => ({ location: s.address })),
				travelMode: 'DRIVING',
				units: this.maps.UnitSystem.IMPERIAL,
				optimizeWaypointOrder: true,
				// REQUIRED, not an optimisation. Documented verbatim: "If
				// 'optimizedIntermediateWaypointIndices' is not requested in
				// ComputeRoutesRequest.fields, the request fails." Never use ['*'].
				fields: ['legs', 'path', 'optimizedIntermediateWaypointIndices', 'viewport'],
			});

			const route = response && response.routes && response.routes[0];
			if (!route) {
				const err = new Error('computeRoutes returned no routes');
				err.computeRoutesResponse = response;
				throw err;
			}

			// DirectionalLocation extends LatLngAltitude, where lat/lng are PROPERTIES,
			// not the legacy LatLng .lat()/.lng() METHODS. Both forms are handled because
			// only the docs have been read, not the runtime; converting to a plain literal
			// also sidesteps whether Marker/LatLngBounds accept a LatLngAltitude directly.
			const toLiteral = (loc) => {
				if (!loc) return null;
				if (typeof loc.lat === 'number') return { lat: loc.lat, lng: loc.lng };
				if (typeof loc.lat === 'function') return { lat: loc.lat(), lng: loc.lng() };
				return null;
			};

			// was `route.waypoint_order`. Note the PLURAL "Indices" -- the REST API uses
			// the singular, so anything copied from REST docs is silently wrong. Same
			// zero-based semantics, so the index arithmetic below is unchanged.
			// Documented to be empty (not absent) when optimisation is off.
			const optimized = route.optimizedIntermediateWaypointIndices;
			const orderedIndexes = optimized && optimized.length
				? Array.prototype.slice.call(optimized)
				: stops.map((_s, i) => i);

			// Only accept the localized strings if they really are strings. Observed on a
			// live key, `leg.localizedValues` logs as an obfuscated object, so `.distance`
			// resolving to a string is not something to assume -- and a non-string here
			// would sail through the `||` fallback in drawRoute and print "[object
			// Object]" as a distance on a driver's pick sheet.
			const str = (v) => (typeof v === 'string' && v ? v : null);

			const legs = (route.legs || []).map((leg) => ({
				distanceText: str(leg.localizedValues && leg.localizedValues.distance),
				durationText: str(leg.localizedValues && leg.localizedValues.duration),
				distanceMeters: leg.distanceMeters || 0,
				// was `leg.duration.value` in SECONDS. `durationMillis` is MILLISECONDS.
				// Normalised here so formatDuration() keeps taking seconds; copying the
				// old arithmetic straight across would be silently 1000x wrong.
				durationSeconds: (leg.durationMillis || 0) / 1000,
				start: toLiteral(leg.startLocation),
				end: toLiteral(leg.endLocation),
			}));

			const path = (route.path || []).map(toLiteral).filter(Boolean);
			return {
				engine: 'routes',
				orderedIndexes: orderedIndexes,
				legs: legs,
				viewport: route.viewport || null,
				path: path,
				drawLine: () => {
					this.clearRouteLine();
					// RoutePolylineOptions has exactly two members: colorScheme and
					// polylineOptions. The stroke settings go INSIDE polylineOptions.
					this.polylines = route.createPolylines({
						polylineOptions: {
							map: this.map,
							strokeColor: STOP_COLOR,
							strokeWeight: 5,
							strokeOpacity: 0.8,
						},
					});
				},
			};
		}

		/** Optimised route via the deprecated DirectionsService. Same normalised shape. */
		computeViaDirections(stops) {
			return new Promise((resolve, reject) => {
				this.directionsService.route(
					{
						origin: this.origin(),
						destination: this.destination(),
						waypoints: stops.map((s) => ({ location: s.address, stopover: true })),
						optimizeWaypoints: true,
						travelMode: this.maps.TravelMode.DRIVING,
						unitSystem: this.maps.UnitSystem.IMPERIAL,
					},
					(result, status) => {
						if (status !== 'OK' || !result.routes || !result.routes.length) {
							const err = new Error('DirectionsService: ' + status);
							// The status string only exists on this rung. Carried on the
							// error so route() can still say something specific.
							err.directionsStatus = status;
							reject(err);
							return;
						}
						const route = result.routes[0];
						const legs = (route.legs || []).map((leg) => ({
							distanceText: (leg.distance && leg.distance.text) || null,
							durationText: (leg.duration && leg.duration.text) || null,
							distanceMeters: (leg.distance && leg.distance.value) || 0,
							durationSeconds: (leg.duration && leg.duration.value) || 0,
							start: leg.start_location || null,
							end: leg.end_location || null,
						}));
						resolve({
							engine: 'directions',
							orderedIndexes: route.waypoint_order || stops.map((_s, i) => i),
							legs: legs,
							viewport: null,
							path: null,
							drawLine: () => {
								this.clearRouteLine();
								this.directionsRenderer.setDirections(result);
							},
						});
					}
				);
			});
		}

		/**
		 * Ask Google for the optimised order, then draw it.
		 *
		 * Four rungs during the Routes transition: computeRoutes -> DirectionsService ->
		 * geocoded pins -> plain list with Maps deep links. The legacy rung stays because
		 * enabling the Routes API is a Google Cloud console change that no deploy or
		 * rollback can perform, and `main` auto-deploys here.
		 *
		 * Every `await` is a new suspension point, so `isStale` is re-checked after each
		 * one rather than once at the top -- the user re-ticks stops mid-flight, and the
		 * old callback form only ever gave one place for that to matter.
		 */
		async route() {
			const stops = this.routeStops();
			this.clearMarkers();

			if (!stops.length) {
				this.clearRouteLine();
				this.ordered = null;
				this.notice = null;
				this.setSummary('');
				this.renderList();
				this.updatePrimaryAction();
				return;
			}

			const generation = this.generation;
			const preferRoutes = !!this.data.use_routes_api && !this.routesUnavailable;

			// COMPUTE inside the try blocks; PAINT outside them, below. Keeping the paint
			// inside was a real defect: drawRoute() throwing for a rendering reason would
			// be caught by the *engine's* catch, blamed on the engine, and a correct
			// optimised route discarded in favour of re-running the other one. On a key
			// that has moved to Routes and no longer allows Directions, that second call
			// is denied and the driver ends up with purchase-order sequence plus a message
			// telling them to enable an API that was never the problem.
			let result = null;

			if (preferRoutes) {
				try {
					result = await this.computeViaRoutes(stops);
				} catch (err) {
					if (this.isStale(generation)) return;
					// Latch off for the life of this dialog. Without it every checkbox
					// toggle pays another doomed - and billable - Routes call before
					// falling back, and this dialog re-routes on every toggle.
					this.routesUnavailable = true;
					// Not user-facing: the legacy rung is about to run, and naming an
					// engine the driver has never heard of is noise. Logged because the
					// rejection shape of computeRoutes is undocumented and this is where
					// we will learn it - so keep rendering errors out of this bucket.
					console.warn('[pickroute] Routes API failed, falling back', err);
				}
			}

			if (!result) {
				try {
					result = await this.computeViaDirections(stops);
				} catch (err) {
					if (this.isStale(generation)) return;
					const status = err && err.directionsStatus;
					if (status === 'REQUEST_DENIED') {
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
						this.degrade(
							__('Could not build the route right now ({0}).', [status || 'error']),
							stops
						);
					}
					return;
				}
			}

			if (this.isStale(generation)) return;
			// A throw here escapes to safeRoute(), which degrades with a message. That is
			// the correct attribution: the route was computed fine and the drawing failed.
			this.drawRoute(stops, result);
		}

		/** Paint a normalised route result. Engine-agnostic by design. */
		drawRoute(stops, result) {
			const overflow = this.overflowStops().length;
			this.notice = overflow
				? __(
					'Google optimises at most {0} stops at a time — {1} more are listed below and are not on this route.',
					[MAX_ROUTE_STOPS, overflow]
				)
				: null;
			result.drawLine();

			// orderedIndexes maps the optimised sequence back onto the stops we submitted,
			// in submission order. legs[i] is the drive that *arrives* at the i-th
			// optimised stop, so the two indexes line up directly.
			//
			// VALIDATED, not trusted. An index outside `stops` used to reach
			// `stops[originalIdx] === undefined` and then throw from inside the marker
			// loop as "Cannot read properties of undefined (reading 'key')" -- a stack
			// trace three frames from the real cause, which is how this shipped. Worse,
			// an order that is merely *incomplete* would silently drop a supplier from a
			// driver's run while still looking like a valid optimised route.
			//
			// So: every index must be a whole number inside `stops`, with no duplicates,
			// and the set must cover every stop exactly once. Anything else is unusable,
			// and an unordered run with an honest message beats a confidently wrong one.
			const legs = result.legs || [];
			const raw = Array.isArray(result.orderedIndexes) ? result.orderedIndexes : [];
			const seen = new Set();
			const order = raw.filter((i) => {
				if (typeof i !== 'number' || !isFinite(i) || i % 1 !== 0) return false;
				if (i < 0 || i >= stops.length || seen.has(i)) return false;
				seen.add(i);
				return true;
			});

			// `raw.length` is checked as well as the filtered length: an over-long array
			// like [0,1,2] for two stops filters down to a perfectly valid-looking [0,1],
			// and accepting that would mean quietly reinterpreting a response whose
			// convention we evidently do not understand. Degrade instead.
			if (order.length !== stops.length || raw.length !== stops.length) {
				// Logged with both sides so the next occurrence names itself rather than
				// needing another round of production archaeology.
				console.warn(
					'[pickroute] unusable stop order from ' + (result.engine || '?') + ': ',
					result.orderedIndexes, 'for', stops.length, 'stops'
				);
				this.degrade(
					__(
						'Stops are in purchase-order sequence, not drive-time order: the optimised order Google returned did not match the stops on this run.'
					),
					stops
				);
				return;
			}

			this.ordered = order.map((originalIdx, position) => {
				const leg = legs[position];
				return {
					stop: stops[originalIdx],
					leg: leg
						? {
							// Fall back to formatting the numbers ourselves. On the Routes
							// engine the localized strings come from `leg.localizedValues`,
							// and whether the 'legs' field mask returns them is not
							// documented -- if it does not, they arrive null and the row
							// would otherwise render blank with no error.
							distance: leg.distanceText ||
								(leg.distanceMeters / 1609.344).toFixed(1) + ' mi',
							duration: leg.durationText || formatDuration(leg.durationSeconds),
						}
						: null,
				};
			});

			const meters = legs.reduce((sum, leg) => sum + (leg.distanceMeters || 0), 0);
			const seconds = legs.reduce((sum, leg) => sum + (leg.durationSeconds || 0), 0);
			this.setSummary(
				'<b>' + esc(__('{0} stops', [this.ordered.length])) + '</b><br>' +
				esc(
					__('{0} mi · {1} driving', [
						(meters / 1609.344).toFixed(1),
						formatDuration(seconds),
					])
				)
			);

			this.placeMarkers(legs, result);
			this.renderList();
			this.updatePrimaryAction();
		}

		placeMarkers(legs, result) {
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
			if (legs.length && legs[0].start) {
				// Keyed like the rest: an unkeyed marker is one clearMarkers()
				// can never take off the map, so re-routing would stack them.
				this.markers.__depot = add(legs[0].start, 'A', DEPOT_COLOR, __('Shop'), null);
			}
			this.ordered.forEach((entry, idx) => {
				const leg = legs[idx];
				if (!leg || !leg.end) return;
				this.markers[entry.stop.key] = add(
					leg.end,
					idx + 1,
					STOP_COLOR,
					entry.stop.supplier_name,
					markerHtml(entry.stop, idx + 1)
				);
			});
			// The final leg ends at the finish point, which is not a stop.
			if (legs.length && legs[legs.length - 1].end) {
				this.markers['__finish'] = add(
					legs[legs.length - 1].end,
					'B',
					FINISH_COLOR,
					__('Finish'),
					null
				);
			}

			// Prefer the route's own viewport when the engine supplies one; otherwise fit
			// over the markers as before. Routes returns `viewport` as a LatLngBounds, but
			// whether 'viewport' is even a legal field-mask string is undocumented, so
			// this must work when it comes back null.
			// Shape-checked, not just null-checked: fitBounds throws InvalidValueError on
			// anything that is not a LatLngBounds, and that throw would surface as a
			// routing failure rather than a rendering one. Falling through to the marker
			// bounds is always safe.
			const vp = result && result.viewport;
			const usableViewport = vp && this.maps && this.maps.LatLngBounds &&
				vp instanceof this.maps.LatLngBounds;
			if (usableViewport) {
				this.map.fitBounds(vp, 56);
			} else if (!bounds.isEmpty()) {
				this.map.fitBounds(bounds, 56);
			}
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
			// Both engines, not just the legacy renderer: a Routes polyline left behind
			// here would sit under the geocoded pins showing a route we just disowned.
			this.clearRouteLine();

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

		// ---- the printed pick sheet

		/**
		 * A printable sheet of what to collect, in the order now on screen.
		 *
		 * Built in the browser from the payload already in memory rather than as a
		 * Frappe Print Format, for two reasons that are not stylistic:
		 *
		 *  - **Route order only exists here.** The optimised sequence comes back
		 *    from Google inside this dialog; the server never sees it. A Print
		 *    Format would have to re-query and would print in purchase-order
		 *    sequence — a sheet that contradicts the screen it was printed from,
		 *    which is the exact failure docs/pick-routing-map-po-details.md argued
		 *    against when it rejected option (a).
		 *  - **It works while the PDF toolchain does not.** Both server-side PDF
		 *    backends are broken on this host (see docs/pdf-generation.md); browser
		 *    print needs neither.
		 *
		 * The trade-off is real and worth stating: paper is a snapshot, so a PO
		 * received after printing is invisible on the sheet. That is inherent to
		 * printing, not to this implementation.
		 */
		openPickSheet() {
			const win = window.open('', '_blank');
			if (!win) {
				frappe.msgprint(
					__('Allow pop-ups for this site to print the pick sheet.')
				);
				return;
			}
			win.document.write(this.pickSheetHtml());
			win.document.close();
			win.focus();
			// Let the new document lay out before the print dialog measures it;
			// printing a half-laid-out page drops the last table's borders.
			win.setTimeout(() => win.print(), 250);
		}

		pickSheetHtml() {
			const project = this.data.project || {};
			const title = [project.name, project.project_name].filter(Boolean).join(' — ');
			const scope = SCOPE_LABELS.find((o) => o.value === this.scope);
			const stops = this.displayStops();
			const overflow = this.overflowStops();
			const unroutable = (this.data.stops || []).filter((s) => !s.address);
			const excluded = (this.data.stops || []).filter(
				(s) => s.address && !this.selected.has(s.key)
			);

			const sections = [];
			sections.push(
				'<div class="sheet-endpoint"><b>' + esc(__('Start')) + ':</b> ' +
					esc(this.origin() || __('Not set')) + '</div>'
			);
			stops.forEach((stop, idx) => {
				sections.push(this.sheetStopHtml(stop, idx + 1));
			});
			sections.push(
				'<div class="sheet-endpoint"><b>' + esc(__('Finish')) + ':</b> ' +
					esc(this.destination() || __('Not set')) + '</div>'
			);

			if (overflow.length) {
				sections.push(
					'<h2>' + esc(__('Not on the optimised route — run separately')) + '</h2>'
				);
				overflow.forEach((stop) => sections.push(this.sheetStopHtml(stop, null)));
			}
			if (unroutable.length) {
				sections.push('<h2>' + esc(__('No address on file — ring before going')) + '</h2>');
				unroutable.forEach((stop) => sections.push(this.sheetStopHtml(stop, null)));
			}

			// Say out loud whether these stops are drive-time ordered, and when they are
			// not, say WHY. The first version printed one sentence for every unoptimised
			// case, which made "Google refused the request" and "the map never started"
			// indistinguishable on paper — and that ambiguity sent a real diagnosis at the
			// Directions API for hours while the actual fault was a 0x0 map container.
			// `this.notice` already holds the specific reason; carry it through.
			const orderNote = this.ordered
				? __('Stops are in drive-time order.')
				: __('Stops are in purchase-order sequence, not drive-time order.') +
					(this.notice ? ' ' + this.notice : '');

			return (
				'<!doctype html><html><head><meta charset="utf-8">' +
				'<title>' + esc(__('Pick sheet') + ' — ' + (project.name || '')) + '</title>' +
				'<style>' + PICK_SHEET_CSS + '</style></head><body>' +
					'<div class="sheet-head">' +
						'<h1>' + esc(__('Pick sheet')) + '</h1>' +
						'<div class="sheet-sub">' + esc(title) + '</div>' +
						'<div class="sheet-meta">' +
							esc(__('Printed {0}', [frappe.datetime.str_to_user(frappe.datetime.now_datetime())])) +
							' · ' + esc(scope ? scope.label : this.scope) +
							' · ' + esc(orderNote) +
						'</div>' +
					'</div>' +
					sections.join('') +
					(excluded.length
						? '<div class="sheet-meta sheet-foot">' +
							esc(__('{0} stop(s) were unticked and are not on this sheet.', [excluded.length])) +
							'</div>'
						: '') +
					'<div class="sheet-sign">' +
						esc(__('Collected by')) + ' _____________________&nbsp;&nbsp;&nbsp;' +
						esc(__('Date')) + ' _______________' +
					'</div>' +
				'</body></html>'
			);
		}

		sheetStopHtml(stop, seq) {
			const contact = [stop.contact, stop.phone].filter(Boolean).join(' · ');
			const tables = (stop.purchase_orders || [])
				.map((po) => {
					const rows = (po.items || [])
						.map((line) => {
							const m = lineModel(line);
							return (
								'<tr' + (m.done ? ' class="is-done"' : '') + '>' +
									'<td class="tick">' + (m.done ? '&#9745;' : '&#9744;') + '</td>' +
									'<td class="code">' + esc(m.code) + '</td>' +
									'<td>' + esc(m.name) + '</td>' +
									'<td class="num"><b>' + esc(qtyText(m.outstanding)) + '</b></td>' +
									'<td class="num">' + esc(qtyText(m.ordered)) + '</td>' +
									'<td class="num">' + esc(qtyText(m.received)) + '</td>' +
									'<td>' + esc(m.uom) + '</td>' +
								'</tr>'
							);
						})
						.join('');
					if (!rows) return '';
					return (
						'<div class="sheet-po">' +
							'<div class="sheet-po-head">' + esc(poGroupHeadText(po)) + '</div>' +
							'<table><thead><tr>' +
								'<th class="tick"></th>' +
								'<th>' + esc(__('Item')) + '</th>' +
								'<th>' + esc(__('Description')) + '</th>' +
								'<th class="num">' + esc(__('Collect')) + '</th>' +
								'<th class="num">' + esc(__('Ordered')) + '</th>' +
								'<th class="num">' + esc(__('Received')) + '</th>' +
								'<th>' + esc(__('UOM')) + '</th>' +
							'</tr></thead><tbody>' + rows + '</tbody></table>' +
						'</div>'
					);
				})
				.join('');

			return (
				'<div class="sheet-stop">' +
					'<div class="sheet-stop-head">' +
						'<span class="sheet-seq">' + esc(seq == null ? '–' : String(seq)) + '</span>' +
						'<span class="sheet-stop-name">' + esc(stop.supplier_name) + '</span>' +
					'</div>' +
					'<div class="sheet-stop-addr">' +
						esc(stop.address || __('No address on file')) +
						(contact ? ' · ' + esc(contact) : '') +
					'</div>' +
					(tables || '<div class="sheet-meta">' + esc(__('No lines on this stop.')) + '</div>') +
				'</div>'
			);
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
				// Secondary, not primary: the map is the point of this dialog and
				// "Open in Google Maps" is what the driver reaches for. The sheet is
				// for whoever is sending them.
				dialog.set_secondary_action_label(__('Print pick sheet'));
				dialog.set_secondary_action(() => controller.openPickSheet());
			},
		});
	}

	frappe.ui.form.on('Project', {
		custom_btn_pick_routing_map: function (frm) {
			openPickRoutingMap(frm);
		},
	});
})();
