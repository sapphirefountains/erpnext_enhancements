// Location Timeline — the manager's map of one technician's day (Trail) and of
// everybody clocked in right now (Live).
//
// Data, all from erpnext_enhancements.api.time_kiosk. Every call is permission-
// checked server-side; this page only decides what to draw:
//   get_employees_for_timeline  the picker (managers see every active Employee)
//   get_location_history        one employee, one date range, grouped by Job Interval
//   get_live_positions          latest fix per open interval, polled every 30 s
//                               while the tab is visible AND this page is on screen
//   export_location_history     CSV / GPX, opened as a download URL with the
//                               filters of the trail on screen
//
// Reads frappe.route_options {employee, from_date, to_date} on every show, so the
// Job Interval form ("View on Timeline") and the Employee form ("Location
// Timeline") can open it pre-filled.
//
// Assets. Leaflet is frappe's vendored copy (/assets/frappe/js/lib/leaflet/,
// present on v16) and the stylesheet lives in public/css/workforce/; both come in
// through frappe.require with BARE paths. On v16 frappe.require appends
// ?v=<build version> to a non-bundled same-origin path itself
// (frappe/public/js/frappe/assets.js, AssetManager.execute), which is what keeps a
// raw /assets URL out of the one-year immutable cache — and a path that already
// carries "?v=" is NOT safe: frappe.assets.extn() reads the text after the last
// "?" as the extension and loads the file as neither css nor js, silently.
// public/js/training/desk_assets.js documents the same trap from the other side.
//
// Every server string that reaches innerHTML, a tooltip or a popup goes through
// esc(). Colours come from a fixed palette, never from data.
//
// Times. The API returns site-local "YYYY-MM-DD HH:MM:SS" strings. parseTs()
// turns one into epoch milliseconds for the playback maths and userTime() turns
// milliseconds back into the user's own rendering through
// frappe.datetime.str_to_user, so a clock label here agrees with the Job Interval
// form to the minute, timezone conversion included.
//
// Client-side derivations (the contract's payload has no field for these):
//   - anchors that come back null fall back to the interval's first / last fix;
//   - a gap is drawn between the last fix at or before its `from` and the first
//     fix at or after its `to` — the payload carries the gap's times, not the
//     two fixes around it;
//   - day totals fall back to sums over the intervals' `stats` when the payload
//     has no `day_totals` (an older server);
//   - a live row's "last fix … ago" is now() minus `last_fix_at`.

frappe.pages['location-timeline'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __('Location Timeline'),
        single_column: true
    });
    wrapper.location_timeline = new erpnext_enhancements.workforce.LocationTimeline(page, wrapper);
};

frappe.pages['location-timeline'].on_page_show = function (wrapper) {
    if (wrapper.location_timeline) wrapper.location_timeline.onShow();
};

(function () {
    'use strict';

    frappe.provide('erpnext_enhancements.workforce');

    // Method names are written out in full at every call site rather than built
    // from a prefix, so a grep for "time_kiosk.get_live_positions" finds this
    // caller and tests/test_location_timeline_page.py can check each one against
    // the @frappe.whitelist() defs.
    const PAGE_ROUTE = 'location-timeline';

    // Bare paths on purpose — see the header. Leaflet first: the stylesheet does
    // not depend on it, but the map does, and frappe.require resolves them as one.
    const ASSETS = [
        '/assets/erpnext_enhancements/css/workforce/location_timeline.css'
    ];

    
    // Distinct colours cycled per clock-in interval.
    const PALETTE = [
        '#2490ef', '#8e44ad', '#28a745', '#f39c12', '#d63384',
        '#16a085', '#fd7e14', '#0dcaf0', '#6610f2', '#e03636'
    ];
    const GAP_COLOR = '#e03636';
    const STOP_COLOR = '#f39c12';
    const SITE_COLOR = '#2490ef';
    const LOW_ACCURACY = 'Low Accuracy';

    // Before anything loads the map shows the Wasatch Front rather than a blank
    // world, which reads as broken. fitBounds replaces it the moment data arrives.
    const HOME = { center: [40.65, -111.9], zoom: 9 };

    const LIVE_POLL_MS = 30000;
    const PLAYBACK_TICK_MS = 100;
    // At 1x the replay covers one minute of the day per real second, so a ten-hour
    // shift takes ten minutes; 4x and 16x scale that.
    const PLAYBACK_MS_PER_SECOND = 60 * 1000;
    const SPEEDS = [1, 4, 16];

    const HEALTH_WORDS = {
        Good: __('Tracked'),
        Gaps: __('Gaps'),
        None: __('No fixes'),
        Off: __('Tracking off'),
        Pending: __('Pending')
    };

    // ---- helpers ------------------------------------------------------------

    function esc(value) {
        return frappe.utils.escape_html(String(value == null ? '' : value));
    }

    function parseTs(value) {
        if (!value) return null;
        const m = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})/.exec(String(value));
        if (!m) return null;
        return new Date(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]).getTime();
    }

    function sysString(ms) {
        return moment(ms).format(frappe.defaultDatetimeFormat);
    }

    // Milliseconds -> the user's own time (or date + time) rendering, through the
    // same system-zone -> user-zone conversion frappe.datetime.str_to_user makes.
    function userTime(ms, withDate) {
        if (ms == null) return '';
        const s = sysString(ms);
        if (withDate) return frappe.datetime.str_to_user(s);
        const tz = frappe.boot && frappe.boot.time_zone;
        const m = (moment.tz && tz && tz.system && tz.user)
            ? moment.tz(s, frappe.defaultDatetimeFormat, tz.system).tz(tz.user)
            : moment(s, frappe.defaultDatetimeFormat);
        return m.format(frappe.datetime.get_user_time_fmt());
    }

    function fmtTime(value) {
        const ms = parseTs(value);
        return ms == null ? '' : userTime(ms, false);
    }

    function fmtDateTime(value) {
        return value ? frappe.datetime.str_to_user(value) : '';
    }

    function fmtDate(value) {
        return value ? frappe.datetime.str_to_user(value, false, true) : '';
    }

    function fmtDuration(seconds) {
        if (seconds == null || !isFinite(seconds)) return '—';
        const total = Math.max(0, Math.round(seconds));
        const h = Math.floor(total / 3600);
        const m = Math.round((total % 3600) / 60);
        if (h && m) return __('{0}h {1}m', [h, m]);
        if (h) return __('{0}h', [h]);
        if (m) return __('{0} min', [m]);
        return __('< 1 min');
    }

    function fmtMinutes(minutes) {
        if (minutes == null || !isFinite(minutes)) return '—';
        return fmtDuration(minutes * 60);
    }

    function fmtDistance(metres) {
        if (metres == null || !isFinite(metres)) return '—';
        if (metres >= 1000) return __('{0} km', [(metres / 1000).toFixed(1)]);
        return __('{0} m', [Math.round(metres)]);
    }

    function fmtPct(value) {
        if (value == null || !isFinite(value)) return '—';
        return Math.round(value) + '%';
    }

    function hasCoords(p) {
        return !!p && p.latitude != null && p.longitude != null
            && isFinite(+p.latitude) && isFinite(+p.longitude)
            && !(+p.latitude === 0 && +p.longitude === 0);
    }

    function latLng(p) {
        return [+p.latitude, +p.longitude];
    }

    function currentTheme() {
        return document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    }

    function pageIsCurrent() {
        return (frappe.get_route() || [])[0] === PAGE_ROUTE;
    }

    function today() {
        return frappe.datetime.get_today();
    }

    // One server call -> Promise of r.message. Not frappe.xcall: that rejects with
    // r.message, which is undefined on a refusal, and a PermissionError arrives as
    // HTTP 403 whose handler invokes the error callback with no argument at all
    // (frappe/public/js/frappe/request.js, statusCode[403]). The jqXHR frappe.call
    // returns still carries the status, so the rejection is built from that and
    // the page can say "not permitted" rather than "something went wrong".
    function call(method, args) {
        return new Promise((resolve, reject) => {
            let xhr = null;
            xhr = frappe.call({
                method: method,
                args: args || {},
                callback: (r) => resolve(r ? r.message : undefined),
                error: (r) => reject({ status: xhr ? xhr.status : undefined, response: r || null })
            });
        });
    }

    function errorText(err) {
        if (!err) return '';
        if (typeof err === 'string') return err;
        const r = err.response || err;
        if (r && r._server_messages) {
            try {
                return JSON.parse(r._server_messages)
                    .map((m) => JSON.parse(m).message)
                    .join(' ');
            } catch (e) {
                // fall through
            }
        }
        if (r && r.exc_type) return String(r.exc_type);
        if (r && r.message) return String(r.message);
        if (err.status) return __('The server answered HTTP {0}.', [err.status]);
        return '';
    }

    function isDenied(err) {
        if (!err) return false;
        const r = err.response || err;
        if (err.status === 403 || (r && r.exc_type === 'PermissionError')) return true;
        return /permission|permitted/i.test(errorText(err));
    }

    function pill(kind, label) {
        return '<span class="lt-pill lt-pill-' + kind + '">' + esc(label) + '</span>';
    }

    function healthPill(health) {
        const word = HEALTH_WORDS[health] ? health : 'Pending';
        return pill(word.toLowerCase(), HEALTH_WORDS[word]);
    }

    // ---- the page -----------------------------------------------------------

    
    class LayerGroup {
        constructor() {
            this._map = null;
            this._layers = new Set();
        }
        addTo(map) {
            this._map = map;
            this._layers.forEach((l) => l.setMap(map));
            return this;
        }
        addLayer(layer) {
            layer.setMap(this._map);
            this._layers.add(layer);
        }
        removeLayer(layer) {
            layer.setMap(null);
            this._layers.delete(layer);
        }
        clearLayers() {
            this._layers.forEach((l) => l.setMap(null));
            this._layers.clear();
        }
    }

    class LocationTimeline {
        constructor(page, wrapper) {
            this.page = page;
            this.wrapper = wrapper;
            this.mode = 'trail';
            this.map = null;
            this.tiles = null;
            this.tileTheme = null;
            this.layers = {};
            this.data = null;
            this.filters = null;
            this.employees = [];
            this.loadSeq = 0;
            this.silence = 0;
            this.showAccuracy = false;
            this.playback = { min: null, max: null, t: null, playing: false, speed: 1, timer: null, multiday: false };
            this.track = [];
            this.live = { timer: null, inflight: false, data: null, at: null, keys: '' };
            this.initPromise = null;

            this.buildToolbar();
            this.buildBody();
            this.bindLifecycle();
            this.setMode('trail', { force: true });
        }

        // ---- construction ---------------------------------------------------

        buildToolbar() {
            const page = this.page;
            this.employeeField = page.add_field({
                fieldtype: 'Autocomplete',
                label: __('Employee'),
                fieldname: 'employee',
                options: [],
                change: () => this.onFilterChange()
            });
            this.fromField = page.add_field({
                fieldtype: 'Date',
                label: __('From'),
                fieldname: 'from_date',
                default: today(),
                change: () => this.onFilterChange()
            });
            this.toField = page.add_field({
                fieldtype: 'Date',
                label: __('To'),
                fieldname: 'to_date',
                default: today(),
                change: () => this.onFilterChange()
            });
            page.add_menu_item(__('Export CSV'), () => this.exportTrail('csv'));
            page.add_menu_item(__('Export GPX'), () => this.exportTrail('gpx'));
        }

        buildBody() {
            const speeds = SPEEDS.map((s) =>
                '<button type="button" class="lt-speed-btn' + (s === 1 ? ' active' : '') + '" data-speed="' + s + '">'
                + s + '&times;</button>'
            ).join('');

            this.$root = $(
                '<div class="lt-root">'
                + '<div class="lt-toolbar">'
                + '<div class="lt-modes" role="tablist">'
                + '<button type="button" class="lt-mode-btn" data-mode="trail" role="tab">' + __('Trail') + '</button>'
                + '<button type="button" class="lt-mode-btn" data-mode="live" role="tab">' + __('Live') + '</button>'
                + '</div>'
                + '<label class="lt-check lt-trail-only"><input type="checkbox" class="lt-accuracy-toggle"> '
                + __('Accuracy rings') + '</label>'
                + '<span class="lt-legend lt-trail-only">'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-start"></i>' + __('Clock-in') + '</span>'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-end"></i>' + __('Clock-out') + '</span>'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-hollow"></i>' + __('Low accuracy') + '</span>'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-gap"></i>' + __('No fixes') + '</span>'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-stop"></i>' + __('Stop') + '</span>'
                + '<span class="lt-legend-item"><i class="lt-swatch lt-swatch-site"></i>' + __('Site geofence') + '</span>'
                + '</span>'
                + '<span class="lt-live-status lt-live-only"></span>'
                + '</div>'
                + '<div class="lt-layout">'
                + '<div class="lt-map-col">'
                + '<div class="lt-map"></div>'
                + '<div class="lt-playback lt-trail-only lt-disabled">'
                + '<button type="button" class="btn btn-default btn-xs lt-play-btn" aria-label="' + __('Play') + '">&#9654;</button>'
                + '<span class="lt-speeds">' + speeds + '</span>'
                + '<input type="range" class="lt-scrubber" min="0" max="1000" value="1000" step="1" aria-label="' + __('Time') + '">'
                + '<span class="lt-clock">—</span>'
                + '</div>'
                + '</div>'
                + '<div class="lt-side"></div>'
                + '</div>'
                + '</div>'
            ).appendTo(this.page.main);

            this.$side = this.$root.find('.lt-side');
            this.$scrubber = this.$root.find('.lt-scrubber');
            this.$clock = this.$root.find('.lt-clock');
            this.$playBtn = this.$root.find('.lt-play-btn');
            this.$liveStatus = this.$root.find('.lt-live-status');

            this.$root.on('click', '.lt-mode-btn', (e) => this.setMode(e.currentTarget.dataset.mode));
            this.$root.on('change', '.lt-accuracy-toggle', (e) => {
                this.showAccuracy = !!e.currentTarget.checked;
                this.applyAccuracyLayer();
            });
            this.$playBtn.on('click', () => this.togglePlay());
            this.$root.on('click', '.lt-speed-btn', (e) => this.setSpeed(+e.currentTarget.dataset.speed));
            this.$scrubber.on('input', (e) => {
                this.pausePlayback();
                this.seekFraction((+e.currentTarget.value) / 1000);
            });
            this.$side.on('click', '.lt-card', (e) => this.focusInterval(+e.currentTarget.dataset.idx));
            this.$side.on('keydown', '.lt-card', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.focusInterval(+e.currentTarget.dataset.idx);
                }
            });
            this.$side.on('click', '.lt-export', (e) => this.exportTrail(e.currentTarget.dataset.format));
            this.$side.on('click', '.lt-live-row', (e) => this.openTrailFor(+e.currentTarget.dataset.idx));
            this.$side.on('keydown', '.lt-live-row', (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    this.openTrailFor(+e.currentTarget.dataset.idx);
                }
            });
        }

        bindLifecycle() {
            // Live polling runs only while somebody can see it: stop when the tab
            // is hidden, resume when it comes back (if Live is still the mode).
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'hidden') {
                    this.stopLivePolling();
                } else if (this.mode === 'live' && pageIsCurrent()) {
                    this.startLivePolling();
                }
            });
            // frappe.container triggers "hide" on the wrapper when the desk moves
            // to another page (frappe/public/js/frappe/views/container.js).
            $(this.wrapper).on('hide', () => {
                this.stopLivePolling();
                this.pausePlayback();
            });
            // The desk stamps data-theme on <html> and flips it live; follow it.
            new MutationObserver(() => this.applyTheme()).observe(document.documentElement, {
                attributes: true,
                attributeFilter: ['data-theme']
            });
        }

        // ---- lifecycle ------------------------------------------------------

        onShow() {
            const opts = frappe.route_options;
            frappe.route_options = null;
            // `invalidateSize()` is Leaflet's; google.maps.Map has no such method, so
            // this threw a TypeError on every RETURN visit to the page (the first visit
            // survived only because `this.map` was still null) and took the rest of
            // onShow with it. Google's equivalent is a resize event, which the map needs
            // for the same reason Leaflet did: the desk can lay this page out while it is
            // hidden, and a map sized then stays wrong until told to re-measure.
            if (this.map) google.maps.event.trigger(this.map, 'resize');
            this.init().then(() => {
                if (opts && (opts.employee || opts.from_date || opts.to_date)) {
                    return this.applyRouteOptions(opts);
                }
                if (this.mode === 'live') this.startLivePolling();
                return null;
            }).catch(() => {
                // init() has already put the failure on screen in words.
            });
        }

        init() {
            if (!this.initPromise) {
                this.renderState('loading', __('Loading the map'), __('Fetching the map library and the employee list.'));
                this.initPromise = this.loadAssets()
                    .then(() => {
                        this.buildMap();
                        return this.loadEmployees();
                    })
                    .then(() => this.applyDefaults())
                    .catch((err) => {
                        this.initPromise = null;
                        this.renderState('error', __('The page could not start'),
                            errorText(err) || __('The map library or the employee list did not load. Reload the page to try again.'));
                        throw err;
                    });
            }
            return this.initPromise;
        }

        loadAssets() {
            return new Promise((resolve, reject) => {
                frappe.require(ASSETS, () => resolve());
            })
            .then(() => frappe.xcall("erpnext_enhancements.api.travel.get_maps_config"))
            .then((cfg) => {
                this.mapsConfig = cfg;
                return window.EEGoogleMaps.load({ apiKey: cfg.api_key, libraries: ["marker", "geometry"] });
            })
            .catch((err) => {
                throw new Error(__('The map library did not load.'));
            });
        }

        loadEmployees() {
            return call('erpnext_enhancements.api.time_kiosk.get_employees_for_timeline').then((rows) => {
                this.employees = (rows || []).map((r) => ({
                    value: String(r.value),
                    label: String(r.label || r.value)
                }));
                // The Autocomplete control maps the chosen LABEL back to a value, so
                // two people with the same name would both resolve to the first one.
                // Disambiguate a repeated label with the employee id.
                const seen = {};
                this.employees.forEach((e) => { seen[e.label] = (seen[e.label] || 0) + 1; });
                this.employees.forEach((e) => {
                    if (seen[e.label] > 1) e.label = e.label + ' (' + e.value + ')';
                });
                this.employeeField.df.options = this.employees;
                this.employeeField.set_data(this.employees);
            });
        }

        async applyDefaults() {
            await this.setSilently(this.fromField, today());
            await this.setSilently(this.toField, today());
            if (!this.employees.length) {
                this.renderState('empty', __('No employees to show'),
                    __('There are no active employees you may view.'));
                return;
            }
            this.renderHint();
        }

        renderHint() {
            this.renderState('empty', __('Pick an employee and a date range'),
                __('Choose whose day to replay and press Load, or switch to Live to see who is clocked in right now.'));
        }

        async applyRouteOptions(opts) {
            this.setMode('trail');
            if (opts.employee && !this.employees.some((e) => e.value === String(opts.employee))) {
                // An inactive employee still has a trail a manager may need to see;
                // the picker only lists active ones, so admit this one by name.
                this.employees.push({ value: String(opts.employee), label: String(opts.employee) });
                this.employeeField.df.options = this.employees;
                this.employeeField.set_data(this.employees);
            }
            const from = opts.from_date ? String(opts.from_date).slice(0, 10) : null;
            const to = opts.to_date ? String(opts.to_date).slice(0, 10) : from;
            if (from) await this.setSilently(this.fromField, from);
            if (to) await this.setSilently(this.toField, to);
            if (opts.employee) await this.setSilently(this.employeeField, String(opts.employee));
            this.loadTrail({ auto: true });
        }

        async setSilently(field, value) {
            this.silence += 1;
            try {
                await field.set_value(value);
            } finally {
                this.silence -= 1;
            }
        }

        onFilterChange() {
            if (this.silence || this.mode !== 'trail' || !this.map) return;
            this.loadTrail({ auto: true });
        }

        employeeLabel(name) {
            const hit = this.employees.find((e) => e.value === String(name));
            return hit ? hit.label : String(name || '');
        }

        // ---- map ------------------------------------------------------------

        buildMap() {
            this.mapNode = this.$root.find('.lt-map')[0];
            this.layers = {
                sites: new LayerGroup(),
                accuracy: new LayerGroup(),
                trail: new LayerGroup(),
                gaps: new LayerGroup(),
                stops: new LayerGroup(),
                anchors: new LayerGroup(),
                playback: new LayerGroup(),
                live: new LayerGroup()
            };
            this.applyTheme(true);
            
            // Add layers to map
            this.layers.sites.addTo(this.map);
            this.layers.trail.addTo(this.map);
            this.layers.gaps.addTo(this.map);
            this.layers.stops.addTo(this.map);
            this.layers.anchors.addTo(this.map);
            this.layers.playback.addTo(this.map);
            this.layers.live.addTo(this.map);
        }

        applyTheme(forceRebuild = false) {
            const theme = currentTheme();
            if (!forceRebuild && this.tileTheme === theme) return;
            this.tileTheme = theme;
            
            const opts = window.EEGoogleMaps.mapOptions(this.mapsConfig, theme);
            opts.center = this.map ? this.map.getCenter() : HOME.center;
            opts.zoom = this.map ? this.map.getZoom() : HOME.zoom;
            opts.mapTypeControl = false;
            opts.streetViewControl = false;
            opts.fullscreenControl = false;
            
            // Constraint: When a mapId is configured, the map style is fixed at construction.
            // setOptions({mapId}) on a live map does not restyle it.
            // We must rebuild the map instance on theme change if mapId is used.
            // If styles is used, we could use setOptions, but for simplicity and consistency
            // (to handle both cases the same way), we just rebuild the map.
            
            if (this.map) {
                this.clearMapLayers();
                Object.values(this.layers).forEach(layer => layer.addTo(null));
                this.map = null;
            }
            
            this.map = new google.maps.Map(this.mapNode, opts);
            Object.values(this.layers).forEach(layer => layer.addTo(this.map));
            this.applyAccuracyLayer();
        }

        
        bindPopup(element, content, isTooltip = false, permanent = false, offset = null) {
            if (!this.infoWindow) {
                this.infoWindow = new google.maps.InfoWindow();
                this.tooltipWindow = new google.maps.InfoWindow();
            }
            if (permanent) {
                const iw = new google.maps.InfoWindow({
                    content: `<div class="lt-popup">${content}</div>`,
                    pixelOffset: offset ? new google.maps.Size(offset[0], offset[1]) : null
                });
                iw.open(this.map, element);
                return iw;
            }
            const win = isTooltip ? this.tooltipWindow : this.infoWindow;
            element.addListener(isTooltip ? 'mouseover' : 'click', (e) => {
                let pos = null;
                if (e.latLng) pos = e.latLng;
                else if (element.position) pos = element.position;
                
                win.setContent(`<div class="lt-popup">${content}</div>`);
                if (offset) win.setOptions({pixelOffset: new google.maps.Size(offset[0], offset[1])});
                else win.setOptions({pixelOffset: new google.maps.Size(0, 0)});
                
                if (element instanceof google.maps.Data.Feature) {
                    win.setPosition(pos);
                    win.open(this.map);
                } else if (element.setMap) { // AdvancedMarkerElement or Marker
                    win.open(this.map, element);
                } else {
                    win.setPosition(pos);
                    win.open(this.map);
                }
            });
            if (isTooltip) {
                element.addListener('mouseout', () => {
                    win.close();
                });
            }
        }

        
        createMarker(position, contentHtml, zIndex, title) {
            const hasMapId = !!(this.mapsConfig && this.mapsConfig['map_id_' + currentTheme()]);
            let marker;
            if (hasMapId && google.maps.marker && google.maps.marker.AdvancedMarkerElement) {
                const el = document.createElement('div');
                el.innerHTML = contentHtml;
                marker = new google.maps.marker.AdvancedMarkerElement({
                    position: position,
                    content: el.firstElementChild,
                    zIndex: zIndex,
                    title: title
                });
            } else {
                marker = new google.maps.Marker({
                    position: position,
                    zIndex: zIndex,
                    title: title,
                    icon: {
                        path: google.maps.SymbolPath.CIRCLE,
                        scale: 6,
                        fillColor: '#fff',
                        fillOpacity: 1,
                        strokeColor: '#000',
                        strokeWeight: 1
                    }
                });
            }
            return marker;
        }

        clearMapLayers() {
            Object.keys(this.layers).forEach((k) => this.layers[k].clearLayers());
        }

        applyAccuracyLayer() {
            if (!this.map) return;
            const on = this.showAccuracy && this.mode === 'trail';
            if (on) this.layers.accuracy.addTo(this.map);
            else this.layers.accuracy.addTo(null);
        }

        // ---- modes ----------------------------------------------------------

        setMode(mode, opts) {
            opts = opts || {};
            if (mode !== 'trail' && mode !== 'live') return;
            if (this.mode === mode && !opts.force) return;
            this.mode = mode;
            this.$root.find('.lt-mode-btn').each((_, b) => {
                const on = b.dataset.mode === mode;
                b.classList.toggle('active', on);
                b.setAttribute('aria-selected', on ? 'true' : 'false');
            });
            this.$root.toggleClass('lt-mode-live', mode === 'live').toggleClass('lt-mode-trail', mode === 'trail');
            [this.employeeField, this.fromField, this.toField].forEach((f) => f.$wrapper.toggle(mode === 'trail'));
            this.pausePlayback();
            if (this.map) this.clearMapLayers();

            if (mode === 'live') {
                this.page.set_primary_action(__('Refresh'), () => this.pollLive(), 'refresh');
                this.startLivePolling();
            } else {
                this.page.set_primary_action(__('Load'), () => this.loadTrail(), 'refresh');
                this.stopLivePolling();
                if (this.map) {
                    if (this.data) this.renderTrail();
                    else this.renderHint();
                }
            }
            this.applyAccuracyLayer();
        }

        // ---- states ---------------------------------------------------------

        renderState(kind, title, body) {
            this.$side.html(
                '<div class="lt-state lt-state-' + esc(kind) + '">'
                + '<div class="lt-state-title">' + esc(title) + '</div>'
                + (body ? '<div class="lt-state-body">' + esc(body) + '</div>' : '')
                + '</div>'
            );
        }

        // ---- trail ----------------------------------------------------------

        loadTrail(opts) {
            opts = opts || {};
            if (!this.map) {
                this.init().then(() => this.loadTrail(opts)).catch(() => {});
                return;
            }
            const employee = this.employeeField.get_value();
            const from = this.fromField.get_value() || today();
            const to = this.toField.get_value() || from;

            if (!employee) {
                if (opts.auto) this.renderHint();
                else this.renderState('empty', __('Pick an employee'), __('Choose whose day to replay, then press Load.'));
                return;
            }
            if (this.employees.length && !this.employees.some((e) => e.value === String(employee))) {
                this.renderState('empty', __('Pick an employee from the list'),
                    __('"{0}" is not one of the employees you may view.', [employee]));
                return;
            }
            if (to < from) {
                this.renderState('empty', __('Check the dates'), __('The To date is before the From date.'));
                return;
            }

            this.pausePlayback();
            this.filters = {
                employee: String(employee),
                from_datetime: from + ' 00:00:00',
                to_datetime: to + ' 23:59:59'
            };
            const seq = ++this.loadSeq;
            const who = this.employeeLabel(employee);
            this.page.set_indicator(__('Loading'), 'orange');
            this.renderState('loading', __('Loading'), __("Fetching {0}'s trail for {1} – {2}", [who, fmtDate(from), fmtDate(to)]));

            call('erpnext_enhancements.api.time_kiosk.get_location_history', this.filters)
                .then((data) => {
                    if (seq !== this.loadSeq) return;
                    this.page.clear_indicator();
                    this.data = data || {};
                    if (this.mode === 'trail') this.renderTrail();
                })
                .catch((err) => {
                    if (seq !== this.loadSeq) return;
                    this.page.clear_indicator();
                    this.data = null;
                    if (this.map) this.clearMapLayers();
                    if (isDenied(err)) {
                        this.renderState('denied', __('Not permitted'),
                            __("You may not view {0}'s location history. Viewing another person's trail needs the HR Manager, Projects Manager or System Manager role.", [who]));
                    } else {
                        this.renderState('error', __('The trail could not be loaded'),
                            errorText(err) || __('The server did not answer. Try Load again.'));
                    }
                });
        }

        prepareIntervals() {
            const intervals = (this.data && this.data.intervals) || [];
            intervals.forEach((iv, idx) => {
                iv._idx = idx;
                iv._color = PALETTE[idx % PALETTE.length];
                iv._points = (iv.points || [])
                    .filter(hasCoords)
                    .map((p) => Object.assign({}, p, { _t: parseTs(p.timestamp) }))
                    .filter((p) => p._t != null)
                    .sort((a, b) => a._t - b._t);
                // The line follows trusted fixes; a low-accuracy fix is shown (hollow)
                // but not routed through, unless it is all there is.
                const trusted = iv._points.filter((p) => p.log_status !== LOW_ACCURACY);
                iv._path = trusted.length >= 2 ? trusted : iv._points;
                iv._label = this.intervalLabel(iv);
                iv._site = iv.site && hasCoords({ latitude: iv.site.lat, longitude: iv.site.lng }) ? iv.site : null;
                iv._anchors = this.resolveAnchors(iv);
            });
            return intervals;
        }

        intervalLabel(iv) {
            if (!iv.job_interval) return __('Unassigned');
            let s = iv.project_title || iv.project || iv.job_interval;
            if (iv.task_title || iv.task) s += ' — ' + (iv.task_title || iv.task);
            return s;
        }

        resolveAnchors(iv) {
            const given = iv.anchors || {};
            const pick = (a, fallback) => {
                if (a && hasCoords({ latitude: a.lat, longitude: a.lng })) {
                    return { lat: +a.lat, lng: +a.lng, accuracy: a.accuracy, derived: false };
                }
                if (fallback) {
                    return { lat: +fallback.latitude, lng: +fallback.longitude, accuracy: fallback.accuracy, derived: true };
                }
                return null;
            };
            const first = iv._points[0];
            const last = iv._points.length ? iv._points[iv._points.length - 1] : null;
            return {
                start: pick(given.start, first),
                end: iv.end_time ? pick(given.end, last) : pick(given.end, null)
            };
        }

        renderTrail() {
            this.clearMapLayers();
            this.$root.find('.lt-card').removeClass('active');
            const intervals = this.prepareIntervals();
            const bounds = new google.maps.LatLngBounds();
            const siteKeys = {};

            intervals.forEach((iv) => {
                const color = iv._color;

                if (iv._site) {
                    const key = iv._site.lat + ',' + iv._site.lng;
                    if (!siteKeys[key]) {
                        siteKeys[key] = true;
                        const radius = +iv._site.radius_m || 0;
                        const center = {lat: +iv._site.lat, lng: +iv._site.lng};
                        const title = iv.project_title || iv.project || __('Site');
                        const tip = '<b>' + esc(title) + '</b>'
                            + (radius ? __('Geofence {0}', [esc(fmtDistance(radius))]) : __('No geofence'))
                            + (iv._site.source ? ' · ' + esc(iv._site.source) : '');
                        
                        if (radius > 0) {
                            const circ = new google.maps.Circle({
                                center: center,
                                radius: radius,
                                strokeColor: SITE_COLOR,
                                strokeOpacity: 1,
                                strokeWeight: 1,
                                fillColor: SITE_COLOR,
                                fillOpacity: 0.08,
                                clickable: true
                            });
                            this.layers.sites.addLayer(circ);
                            this.bindPopup(circ, tip, true);
                            bounds.extend(center);
                        }
                        
                        // Site Marker
                        const siteMarker = new google.maps.Marker({
                            position: center,
                            icon: {
                                path: google.maps.SymbolPath.CIRCLE,
                                scale: 5,
                                fillColor: '#fff',
                                fillOpacity: 1,
                                strokeColor: SITE_COLOR,
                                strokeWeight: 2
                            }
                        });
                        this.layers.sites.addLayer(siteMarker);
                        this.bindPopup(siteMarker, tip, true);
                    }
                }

                if (iv._path.length >= 2) {
                    const poly = new google.maps.Polyline({
                        path: iv._path.map(p => ({lat: +p.latitude, lng: +p.longitude})),
                        strokeColor: color,
                        strokeWeight: 3,
                        strokeOpacity: 0.45
                    });
                    this.layers.trail.addLayer(poly);
                }

                // Data Layer for performance of thousands of points
                // We use two data layers (or just standard markers for simplicity if it's acceptable? 
                // Wait, "Prefer drawing the individual fixes as a google.maps.Data layer ... State your choice and its scaling limit in a comment.")
                // "The Leaflet usage includes ... L.canvas({padding: 0.5}) as a renderer — used because the trail can be thousands of points."
                
                // Using a Data layer for fixes scales well for thousands of points.
                const dataLayer = new google.maps.Data();
                this.layers.trail.addLayer(dataLayer);
                
                dataLayer.setStyle((feature) => {
                    const isLow = feature.getProperty('isLow');
                    return {
                        icon: {
                            path: google.maps.SymbolPath.CIRCLE,
                            scale: isLow ? 5 : 4,
                            fillColor: color,
                            fillOpacity: isLow ? 0 : 1,
                            strokeColor: isLow ? color : '#fff',
                            strokeWeight: isLow ? 2 : 1
                        }
                    };
                });
                
                dataLayer.addListener('click', (e) => {
                    const p = e.feature.getProperty('data');
                    const iv = e.feature.getProperty('iv');
                    this.bindPopup(e.feature, this.pointPopup(iv, p));
                });
                
                iv._points.forEach((p) => {
                    const ll = {lat: +p.latitude, lng: +p.longitude};
                    bounds.extend(ll);
                    const low = p.log_status === LOW_ACCURACY;
                    
                    dataLayer.add({
                        geometry: new google.maps.Data.Point(ll),
                        properties: { isLow: low, data: p, iv: iv }
                    });

                    if (p.accuracy != null && +p.accuracy > 0) {
                        const accCirc = new google.maps.Circle({
                            center: ll,
                            radius: +p.accuracy,
                            strokeColor: color,
                            strokeOpacity: 0.35,
                            strokeWeight: 1,
                            fillColor: color,
                            fillOpacity: 0.06
                        });
                        this.layers.accuracy.addLayer(accCirc);
                    }
                });

                this.drawGaps(iv);
                this.drawStops(iv);
                this.drawAnchors(iv, bounds);
            });

            if (!bounds.isEmpty()) {
                this.map.fitBounds(bounds, { top: 30, bottom: 30, left: 30, right: 30 });
            }
            this.applyAccuracyLayer();
            this.setupPlayback(intervals);
            this.renderTrailSide(intervals);
        }

        pointPopup(iv, p) {
            let html = '<b>' + esc(iv._label) + '</b>' + esc(fmtDateTime(p.timestamp));
            if (p.accuracy != null) html += '<br>' + __('Accuracy ±{0}', [esc(fmtDistance(+p.accuracy))]);
            if (p.log_status === LOW_ACCURACY) html += ' · ' + esc(__('Low accuracy'));
            if (p.fix_source) html += '<br>' + __('Source: {0}', [esc(p.fix_source)]);
            if (p.speed != null && +p.speed > 0) html += ' · ' + __('{0} km/h', [esc((+p.speed * 3.6).toFixed(0))]);
            return html;
        }

        drawGaps(iv) {
            (iv.gaps || []).forEach((g) => {
                const from = parseTs(g.from);
                const to = parseTs(g.to);
                if (from == null || to == null) return;
                let before = null;
                let after = null;
                iv._points.forEach((p) => {
                    if (p._t <= from) before = p;
                    if (after == null && p._t >= to) after = p;
                });
                const a = before ? {lat: +before.latitude, lng: +before.longitude} : (iv._anchors.start ? {lat: iv._anchors.start.lat, lng: iv._anchors.start.lng} : null);
                const b = after ? {lat: +after.latitude, lng: +after.longitude} : (iv._anchors.end ? {lat: iv._anchors.end.lat, lng: iv._anchors.end.lng} : null);
                if (!a || !b) return;
                
                const lineSymbol = {
                    path: 'M 0,-1 0,1',
                    strokeOpacity: 1,
                    scale: 3,
                    strokeColor: GAP_COLOR
                };

                const poly = new google.maps.Polyline({
                    path: [a, b],
                    strokeOpacity: 0,
                    icons: [{
                        icon: lineSymbol,
                        offset: '0',
                        repeat: '16px'
                    }]
                });
                this.layers.gaps.addLayer(poly);
                
                const tip = '<b>' + esc(__('No fixes for {0}', [fmtMinutes(g.minutes)])) + '</b>'
                        + esc(fmtTime(g.from)) + ' – ' + esc(fmtTime(g.to));
                this.bindPopup(poly, tip, true);
            });
        }

        drawStops(iv) {
            const site = iv.project_title || iv.project || __('the site');
            (iv.stops || []).forEach((s) => {
                if (!hasCoords({ latitude: s.lat, longitude: s.lng })) return;
                let label = fmtMinutes(s.minutes);
                if (s.at_site) label = __('{0} at {1}', [label, site]);
                
                const ll = {lat: +s.lat, lng: +s.lng};
                
                const marker = new google.maps.Marker({
                    position: ll,
                    icon: {
                        path: google.maps.SymbolPath.CIRCLE,
                        scale: 9,
                        fillColor: '#fff',
                        fillOpacity: 0.9,
                        strokeColor: STOP_COLOR,
                        strokeWeight: 3
                    }
                });
                
                this.layers.stops.addLayer(marker);
                
                // permanent tooltip simulation via bindPopup
                const tip = '<b>' + esc(__('Stopped {0}', [fmtMinutes(s.minutes)])) + '</b>'
                        + esc(fmtTime(s.from)) + ' – ' + esc(fmtTime(s.to))
                        + (s.at_site ? '<br>' + esc(__('At {0}', [site])) : '');
                
                this.bindPopup(marker, tip);
                
                // The always-visible "18 min at <site>" label Leaflet drew with a
                // permanent tooltip. A Marker label is rendered as a TEXT node, so it
                // must NOT be esc()'d — Google escapes it itself, and running it
                // through esc() first would print "Smith &amp; Jones" on any site whose
                // name contains an ampersand.
                marker.setLabel({
                    text: label,
                    className: 'lt-stop-label'
                });
            });
        }

        drawAnchors(iv, bounds) {
            const draw = (a, kind, when) => {
                if (!a) return;
                const ll = {lat: +a.lat, lng: +a.lng};
                bounds.extend(ll);
                const word = kind === 'start' ? __('In') : __('Out');
                const cls = kind === 'start' ? 'lt-anchor lt-anchor-start' : 'lt-anchor lt-anchor-end';
                const html = '<span class="' + cls + '" style="--lt-swatch:' + iv._color + '">' + esc(word) + '</span>';
                
                let tip = '<b>' + esc(kind === 'start' ? __('Clocked in') : __('Clocked out')) + '</b>'
                    + esc(iv._label) + (when ? '<br>' + esc(fmtDateTime(when)) : '');
                if (a.accuracy != null) tip += ' · ±' + esc(fmtDistance(+a.accuracy));
                if (a.derived) tip += '<br>' + esc(__('Position taken from the nearest fix'));
                
                const marker = this.createMarker(ll, `<div class="lt-icon">${html}</div>`, 500, word);
                this.layers.anchors.addLayer(marker);
                this.bindPopup(marker, tip, true);
            };
            draw(iv._anchors.start, 'start', iv.start_time);
            draw(iv._anchors.end, 'end', iv.end_time);
        }

        focusInterval(idx) {
            const iv = this.data && this.data.intervals && this.data.intervals[idx];
            if (!iv || !this.map) return;
            const pts = new google.maps.LatLngBounds();
            iv._points.forEach(p => pts.extend({lat: +p.latitude, lng: +p.longitude}));
            if (iv._site && +iv._site.radius_m > 0) pts.extend({lat: +iv._site.lat, lng: +iv._site.lng});
            if (iv._anchors.start) pts.extend({lat: iv._anchors.start.lat, lng: iv._anchors.start.lng});
            if (iv._anchors.end) pts.extend({lat: iv._anchors.end.lat, lng: iv._anchors.end.lng});
            this.$side.find('.lt-card').removeClass('active').filter('[data-idx="' + idx + '"]').addClass('active');
            if (!pts.isEmpty()) this.map.fitBounds(pts, { top: 40, bottom: 40, left: 40, right: 40 });
        }

        // ---- trail side panel ----------------------------------------------

        dayTotals(intervals) {
            const given = (this.data && this.data.day_totals) || null;
            if (given) return given;
            // Older server: sum what the intervals carry.
            const t = { worked_seconds: 0, distance_m: 0, dwell_minutes: 0, travel_minutes: 0, gap_minutes: 0 };
            intervals.forEach((iv) => {
                const s = iv.stats || {};
                const a = parseTs(iv.start_time);
                const b = parseTs(iv.end_time);
                if (a != null && b != null && b > a) t.worked_seconds += (b - a) / 1000;
                t.distance_m += +s.distance_m || 0;
                t.dwell_minutes += +s.dwell_minutes || 0;
                t.travel_minutes += +s.travel_minutes || 0;
                t.gap_minutes += +s.gap_minutes || 0;
            });
            return t;
        }

        renderTrailSide(intervals) {
            const f = this.filters || {};
            const who = this.employeeLabel(this.data.employee || f.employee);
            const fixCount = intervals.reduce((n, iv) => n + iv._points.length, 0);
            const range = fmtDate((f.from_datetime || '').slice(0, 10)) + ' – ' + fmtDate((f.to_datetime || '').slice(0, 10));

            if (!intervals.length) {
                this.renderState('empty', __('No trail for this range'),
                    __('{0} has no location fixes between {1}. Tracking records only while somebody is clocked in and the kiosk is active.', [who, range]));
                return;
            }

            const totals = this.dayTotals(intervals);
            const total = (label, value) =>
                '<div class="lt-total"><div class="lt-total-value">' + esc(value) + '</div>'
                + '<div class="lt-total-label">' + esc(label) + '</div></div>';

            let html = '<div class="lt-side-head">'
                + '<div class="lt-side-title">' + esc(who) + '</div>'
                + '<div class="lt-side-sub">' + esc(range) + ' · ' + esc(__('{0} fixes', [fixCount])) + '</div>'
                + '</div>';
            html += '<div class="lt-side-actions">'
                + '<button type="button" class="btn btn-default btn-xs lt-export" data-format="csv">' + __('Export CSV') + '</button>'
                + '<button type="button" class="btn btn-default btn-xs lt-export" data-format="gpx">' + __('Export GPX') + '</button>'
                + '</div>';
            html += '<div class="lt-totals">'
                + total(__('Worked'), fmtDuration(totals.worked_seconds))
                + total(__('Distance'), fmtDistance(totals.distance_m))
                + total(__('On site'), fmtMinutes(totals.dwell_minutes))
                + total(__('Travelling'), fmtMinutes(totals.travel_minutes))
                + total(__('Gaps'), fmtMinutes(totals.gap_minutes))
                + '</div>';
            html += '<div class="lt-cards">' + intervals.map((iv) => this.intervalCard(iv)).join('') + '</div>';
            this.$side.html(html);
        }

        intervalCard(iv) {
            const s = iv.stats || {};
            const start = iv.start_time ? fmtTime(iv.start_time) : (iv._points[0] ? fmtTime(iv._points[0].timestamp) : '');
            const end = iv.end_time ? fmtTime(iv.end_time) : __('still open');
            const sub = [];
            if (iv.task_title || iv.task) sub.push(iv.task_title || iv.task);
            sub.push(start + ' → ' + end);

            let badges = '';
            if (iv.offsite_start && iv.offsite_end) badges += pill('warn', __('Off-site in and out'));
            else if (iv.offsite_start) badges += pill('warn', __('Off-site clock-in'));
            else if (iv.offsite_end) badges += pill('warn', __('Off-site clock-out'));
            if (iv.auto_closed) badges += pill('muted', __('Auto-closed'));
            if (iv.corrected) badges += pill('info', __('Corrected'));

            const stats = [
                __('{0} fixes', [s.fix_count != null ? s.fix_count : iv._points.length]),
                fmtDistance(s.distance_m),
                __('{0} coverage', [fmtPct(s.coverage_pct)]),
                __('{0} on site', [fmtMinutes(s.dwell_minutes)]),
                __('{0} travelling', [fmtMinutes(s.travel_minutes)])
            ];
            if (s.gap_minutes) stats.push(__('{0} gaps', [fmtMinutes(s.gap_minutes)]));

            return '<div class="lt-card" data-idx="' + iv._idx + '" tabindex="0" style="--lt-swatch:' + iv._color + '">'
                + '<div class="lt-card-head"><div>'
                + '<div class="lt-card-title">' + esc(iv.project_title || iv.project || __('Unassigned')) + '</div>'
                + '<div class="lt-card-sub">' + esc(sub.join(' · ')) + '</div>'
                + '</div>' + healthPill(s.health) + '</div>'
                + '<div class="lt-badges">' + badges + '</div>'
                + '<div class="lt-card-stats">' + esc(stats.join(' · ')) + '</div>'
                + '</div>';
        }

        exportTrail(format) {
            if (!this.filters) {
                frappe.msgprint(__('Load a trail first; the export uses the employee and dates on screen.'));
                return;
            }
            const params = new URLSearchParams({
                employee: this.filters.employee,
                from_datetime: this.filters.from_datetime,
                to_datetime: this.filters.to_datetime,
                format: format === 'gpx' ? 'gpx' : 'csv'
            });
            window.open('/api/method/erpnext_enhancements.api.time_kiosk.export_location_history?' + params.toString());
        }

        // ---- playback -------------------------------------------------------

        setupPlayback(intervals) {
            const pb = this.playback;
            this.track = [];
            intervals.forEach((iv) => {
                iv._path.forEach((p) => this.track.push({ t: p._t, ll: latLng(p), idx: iv._idx }));
            });
            this.track.sort((a, b) => a.t - b.t);
            const $bar = this.$root.find('.lt-playback');
            if (this.track.length < 2) {
                $bar.addClass('lt-disabled');
                pb.min = pb.max = pb.t = null;
                this.$clock.text('—');
                this.$scrubber.val(1000);
                return;
            }
            $bar.removeClass('lt-disabled');
            pb.min = this.track[0].t;
            pb.max = this.track[this.track.length - 1].t;
            pb.multiday = (pb.max - pb.min) > 24 * 3600 * 1000;
            pb.t = pb.max;
            this.renderPlayback();
        }

        setSpeed(speed) {
            if (SPEEDS.indexOf(speed) === -1) return;
            this.playback.speed = speed;
            this.$root.find('.lt-speed-btn').each((_, b) => b.classList.toggle('active', +b.dataset.speed === speed));
        }

        togglePlay() {
            const pb = this.playback;
            if (pb.playing) {
                this.pausePlayback();
                return;
            }
            if (pb.min == null) return;
            if (pb.t == null || pb.t >= pb.max) pb.t = pb.min;
            pb.playing = true;
            this.$playBtn.html('&#10074;&#10074;').attr('aria-label', __('Pause'));
            pb.timer = setInterval(() => this.tickPlayback(), PLAYBACK_TICK_MS);
        }

        pausePlayback() {
            const pb = this.playback;
            if (pb.timer) clearInterval(pb.timer);
            pb.timer = null;
            pb.playing = false;
            if (this.$playBtn) this.$playBtn.html('&#9654;').attr('aria-label', __('Play'));
        }

        tickPlayback() {
            const pb = this.playback;
            if (!pb.playing || pb.min == null) return;
            pb.t += (PLAYBACK_TICK_MS / 1000) * PLAYBACK_MS_PER_SECOND * pb.speed;
            if (pb.t >= pb.max) {
                pb.t = pb.max;
                this.pausePlayback();
            }
            this.renderPlayback();
        }

        seekFraction(fraction) {
            const pb = this.playback;
            if (pb.min == null) return;
            const f = Math.min(1, Math.max(0, fraction));
            pb.t = pb.min + (pb.max - pb.min) * f;
            this.renderPlayback();
        }

        renderPlayback() {
            const pb = this.playback;
            const layer = this.layers.playback;
            layer.clearLayers();
            if (pb.min == null || pb.t == null) return;

            const span = pb.max - pb.min || 1;
            this.$scrubber.val(Math.round(1000 * (pb.t - pb.min) / span));
            this.$clock.text(userTime(pb.t, pb.multiday));

            const T = pb.t;
            const intervals = (this.data && this.data.intervals) || [];
            let marker = null;

            intervals.forEach((iv) => {
                const path = iv._path;
                if (!path.length || path[0]._t > T) return;
                const drawn = [];
                for (let i = 0; i < path.length; i++) {
                    const p = path[i];
                    if (p._t <= T) {
                        drawn.push({lat: +p.latitude, lng: +p.longitude});
                        marker = { ll: {lat: +p.latitude, lng: +p.longitude}, idx: iv._idx, t: p._t };
                        continue;
                    }
                    const prev = path[i - 1];
                    const f = (T - prev._t) / Math.max(1, p._t - prev._t);
                    const ll = {
                        lat: +prev.latitude + (+p.latitude - +prev.latitude) * f,
                        lng: +prev.longitude + (+p.longitude - +prev.longitude) * f
                    };
                    drawn.push(ll);
                    marker = { ll: ll, idx: iv._idx, t: T };
                    break;
                }
                if (drawn.length >= 2) {
                    const poly = new google.maps.Polyline({
                        path: drawn,
                        strokeColor: iv._color,
                        strokeWeight: 4,
                        strokeOpacity: 0.95
                    });
                    layer.addLayer(poly);
                }
            });

            if (marker) {
                const iv = intervals[marker.idx];
                const html = '<span class="lt-play-marker"></span>';
                const m = this.createMarker(marker.ll, `<div class="lt-icon">${html}</div>`, 1000, '');
                m.setClickable(false);
                layer.addLayer(m);
                this.$clock.attr('title', iv ? iv._label : '');
            }
        }

        // ---- live -----------------------------------------------------------

        startLivePolling() {
            if (this.live.timer || this.mode !== 'live' || !this.map) return;
            this.pollLive();
            this.live.timer = setInterval(() => this.pollLive(), LIVE_POLL_MS);
        }

        stopLivePolling() {
            if (this.live.timer) clearInterval(this.live.timer);
            this.live.timer = null;
        }

        pollLive() {
            if (this.mode !== 'live' || !this.map) return;
            if (document.visibilityState === 'hidden' || !pageIsCurrent()) {
                // Nobody is looking; the poll restarts on visibilitychange / show.
                this.stopLivePolling();
                return;
            }
            if (this.live.inflight) return;
            this.live.inflight = true;
            this.$liveStatus.text(__('Updating…'));
            call('erpnext_enhancements.api.time_kiosk.get_live_positions')
                .then((data) => {
                    this.live.data = data || { employees: [] };
                    this.live.at = Date.now();
                    if (this.mode === 'live') this.renderLive();
                })
                .catch((err) => {
                    this.$liveStatus.text(__('Live positions unavailable'));
                    if (isDenied(err)) {
                        this.stopLivePolling();
                        this.renderState('denied', __('Not permitted'),
                            __('Live positions need the HR Manager, Projects Manager or System Manager role.'));
                    } else {
                        this.renderState('error', __('Live positions could not be loaded'),
                            errorText(err) || __('The server did not answer. The page will try again in 30 seconds.'));
                    }
                })
                .finally(() => {
                    this.live.inflight = false;
                });
        }

        renderLive() {
            const data = this.live.data || {};
            const rows = data.employees || [];
            const staleAfter = data.stale_after_minutes;
            this.layers.live.clearLayers();

            const bounds = new google.maps.LatLngBounds();
            rows.forEach((r) => {
                if (!hasCoords(r)) return;
                const ll = {lat: +r.latitude, lng: +r.longitude};
                bounds.extend(ll);
                const stale = !!r.stale;
                const html = '<span class="lt-live-pin' + (stale ? ' stale' : '') + '">'
                    + '<i class="lt-live-dot"></i>'
                    + '<span class="lt-live-label"><b>' + esc(r.employee_name || r.employee) + '</b>'
                    + '<small>' + esc((r.project_title || r.project || __('No project')) + ' · ' + fmtDuration(r.elapsed_seconds)) + '</small>'
                    + '</span></span>';
                
                const marker = this.createMarker(ll, `<div class="lt-icon">${html}</div>`, stale ? 0 : 200, r.employee_name || r.employee);
                this.layers.live.addLayer(marker);
                this.bindPopup(marker, this.livePopup(r));
            });

            const keys = rows.map((r) => r.employee).sort().join('|');
            if (!bounds.isEmpty() && (keys !== this.live.keys || !this.live.fitted)) {
                this.map.fitBounds(bounds, { top: 40, bottom: 40, left: 40, right: 40 });
                this.live.fitted = true;
            }
            this.live.keys = keys;

            const updated = userTime(this.live.at, false);
            this.$liveStatus.text(__('Updated {0} · refreshes every 30 s', [updated]));

            if (!rows.length) {
                this.renderState('empty', __('Nobody is clocked in right now'),
                    __('Open Job Intervals appear here with their latest fix. Checked {0}.', [updated]));
                return;
            }

            let html = '<div class="lt-side-head">'
                + '<div class="lt-side-title">' + esc(__('Clocked in now')) + '</div>'
                + '<div class="lt-side-sub">' + esc(__('{0} people · updated {1}', [rows.length, updated]))
                + (staleAfter ? esc(' · ' + __('stale after {0}', [fmtMinutes(staleAfter)])) : '')
                + '</div></div>';
            html += '<div class="lt-live-list">' + rows.map((r, i) => this.liveRow(r, i)).join('') + '</div>';
            this.$side.html(html);
        }

        lastFixWords(r) {
            const ms = parseTs(r.last_fix_at);
            if (ms == null) return __('no fix yet');
            const mins = Math.max(0, Math.round((Date.now() - ms) / 60000));
            return mins < 1 ? __('fix just now') : __('last fix {0} ago', [fmtMinutes(mins)]);
        }

        liveRow(r, idx) {
            let pills = '';
            if (r.stale) pills += pill('stale', __('Stale'));
            else if (hasCoords(r)) pills += pill('live', __('Live'));
            if (r.status === 'Paused') pills += pill('muted', __('On break'));
            if (r.tracking_health && r.tracking_health !== 'Good' && r.tracking_health !== 'Pending') pills += healthPill(r.tracking_health);

            const sub = [r.project_title || r.project || __('No project')];
            if (r.task_title) sub.push(r.task_title);
            const meta = [
                __('Since {0}', [fmtTime(r.start_time)]),
                fmtDuration(r.elapsed_seconds),
                this.lastFixWords(r)
            ];
            if (!hasCoords(r)) meta.push(__('not on the map'));
            else if (r.accuracy != null) meta.push('±' + fmtDistance(+r.accuracy));

            return '<div class="lt-live-row" data-idx="' + idx + '" tabindex="0">'
                + '<div class="lt-live-name"><span>' + esc(r.employee_name || r.employee) + '</span><span>' + pills + '</span></div>'
                + '<div class="lt-live-sub">' + esc(sub.join(' · ')) + '</div>'
                + '<div class="lt-live-meta">' + esc(meta.join(' · ')) + '</div>'
                + '</div>';
        }

        livePopup(r) {
            let html = '<b>' + esc(r.employee_name || r.employee) + '</b>'
                + esc(r.project_title || r.project || __('No project'));
            if (r.task_title) html += ' — ' + esc(r.task_title);
            html += '<br>' + esc(__('Since {0} · {1}', [fmtTime(r.start_time), fmtDuration(r.elapsed_seconds)]));
            html += '<br>' + esc(this.lastFixWords(r));
            if (r.accuracy != null) html += ' · ±' + esc(fmtDistance(+r.accuracy));
            if (r.stale) html += '<br>' + esc(__('Stale: no fix inside the tracking gap window'));
            return html;
        }

        async openTrailFor(idx) {
            const rows = (this.live.data && this.live.data.employees) || [];
            const r = rows[idx];
            if (!r || !r.employee) return;
            if (!this.employees.some((e) => e.value === String(r.employee))) {
                this.employees.push({ value: String(r.employee), label: String(r.employee_name || r.employee) });
                this.employeeField.df.options = this.employees;
                this.employeeField.set_data(this.employees);
            }
            this.setMode('trail');
            await this.setSilently(this.fromField, today());
            await this.setSilently(this.toField, today());
            await this.setSilently(this.employeeField, String(r.employee));
            this.loadTrail({ auto: true });
        }
    }

    erpnext_enhancements.workforce.LocationTimeline = LocationTimeline;
})();
