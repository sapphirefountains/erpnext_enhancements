// Location Timeline — manager-facing replay of Time Kiosk Log points on a map.
// Reads erpnext_enhancements.api.time_kiosk.get_location_history (permission-checked).

frappe.pages['location-timeline'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Location Timeline',
        single_column: true
    });

    // Distinct colors cycled per clock-in interval.
    const PALETTE = [
        '#2490ef', '#e03636', '#28a745', '#f39c12', '#8e44ad',
        '#16a085', '#d63384', '#fd7e14', '#0dcaf0', '#6610f2'
    ];

    let map = null;
    let layerGroup = null;

    const today = frappe.datetime.get_today();

    const employeeField = page.add_field({
        fieldtype: 'Link', options: 'Employee',
        label: __('Employee'), fieldname: 'employee', reqd: 1
    });
    const fromField = page.add_field({
        fieldtype: 'Date', label: __('From'), fieldname: 'from_date',
        default: today
    });
    const toField = page.add_field({
        fieldtype: 'Date', label: __('To'), fieldname: 'to_date',
        default: today
    });

    page.set_primary_action(__('Load'), () => loadHistory(), 'fa fa-refresh');

    // --- Layout: map + side panel ----------------------------------------
    const $body = $(`
        <div class="lt-wrapper" style="display:flex; gap:16px; align-items:flex-start;">
            <div id="lt-map" style="flex:1 1 auto; height:640px; border-radius:8px; border:1px solid var(--border-color);"></div>
            <div id="lt-side" style="flex:0 0 320px; max-height:640px; overflow:auto;">
                <p class="text-muted">${__('Pick an employee and date range, then click Load.')}</p>
            </div>
        </div>
    `).appendTo(page.main);

    // --- Leaflet loader (uses Frappe's bundled leaflet) ------------------
    function ensureLeaflet() {
        return new Promise((resolve, reject) => {
            if (window.L) return resolve(window.L);
            frappe.require([
                '/assets/frappe/js/lib/leaflet/leaflet.css',
                '/assets/frappe/js/lib/leaflet/leaflet.js'
            ], () => {
                if (window.L) resolve(window.L);
                else reject(new Error('Leaflet failed to load'));
            });
        });
    }

    function initMap() {
        if (map) return;
        map = L.map('lt-map').setView([20, 0], 2);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors'
        }).addTo(map);
        layerGroup = L.layerGroup().addTo(map);
    }

    function fmt(ts) {
        return frappe.datetime.str_to_user(ts);
    }

    async function loadHistory() {
        const employee = employeeField.get_value();
        if (!employee) {
            frappe.msgprint(__('Please select an employee.'));
            return;
        }
        const from_dt = (fromField.get_value() || today) + ' 00:00:00';
        const to_dt = (toField.get_value() || today) + ' 23:59:59';

        page.set_indicator(__('Loading...'), 'orange');
        try {
            await ensureLeaflet();
        } catch (e) {
            frappe.msgprint(__('Could not load the map library (Leaflet). Check the asset path for your Frappe version.'));
            page.clear_indicator();
            return;
        }
        initMap();

        frappe.call({
            method: 'erpnext_enhancements.api.time_kiosk.get_location_history',
            args: { employee, from_datetime: from_dt, to_datetime: to_dt },
            callback: (r) => {
                page.clear_indicator();
                render(r.message || { intervals: [], point_count: 0 });
            },
            error: () => page.clear_indicator()
        });
    }

    function render(data) {
        layerGroup.clearLayers();
        const $side = $('#lt-side').empty();
        const bounds = [];

        if (!data.point_count) {
            $side.html(`<p class="text-muted">${__('No location points found for this range.')}</p>`);
            return;
        }

        $side.append(`<h5 class="mb-3">${data.point_count} ${__('points')}</h5>`);

        data.intervals.forEach((interval, idx) => {
            const color = PALETTE[idx % PALETTE.length];
            const latlngs = interval.points
                .filter(p => p.latitude != null && p.longitude != null)
                .map(p => [p.latitude, p.longitude]);

            if (!latlngs.length) return;

            // Connect the points for this session.
            L.polyline(latlngs, { color, weight: 3, opacity: 0.7 }).addTo(layerGroup);

            interval.points.forEach((p, i) => {
                if (p.latitude == null || p.longitude == null) return;
                const ll = [p.latitude, p.longitude];
                bounds.push(ll);
                const isEdge = i === 0 || i === interval.points.length - 1;
                L.circleMarker(ll, {
                    radius: isEdge ? 7 : 4,
                    color: '#fff', weight: 1,
                    fillColor: color, fillOpacity: 1
                }).addTo(layerGroup).bindPopup(
                    `<b>${frappe.utils.escape_html(label(interval))}</b><br>` +
                    `${fmt(p.timestamp)}` +
                    (p.accuracy ? `<br>±${Math.round(p.accuracy)}m` : '')
                );
            });

            // Side panel legend entry.
            const start = interval.points[0] ? fmt(interval.points[0].timestamp) : '';
            const end = interval.points.length
                ? fmt(interval.points[interval.points.length - 1].timestamp) : '';
            $side.append(`
                <div class="lt-legend-row" style="display:flex; gap:8px; margin-bottom:10px; cursor:pointer;" data-idx="${idx}">
                    <span style="flex:0 0 14px; height:14px; margin-top:3px; border-radius:3px; background:${color};"></span>
                    <div>
                        <div><b>${frappe.utils.escape_html(label(interval))}</b></div>
                        <div class="text-muted small">${start} → ${end} · ${interval.points.length} ${__('pts')}</div>
                    </div>
                </div>
            `);
        });

        if (bounds.length) map.fitBounds(bounds, { padding: [30, 30], maxZoom: 17 });

        // Click a legend row to zoom that interval.
        $side.find('.lt-legend-row').on('click', function () {
            const idx = $(this).data('idx');
            const pts = data.intervals[idx].points
                .filter(p => p.latitude != null && p.longitude != null)
                .map(p => [p.latitude, p.longitude]);
            if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 18 });
        });
    }

    function label(interval) {
        if (!interval.job_interval) return __('Unassigned');
        let s = interval.project_title || interval.project || interval.job_interval;
        if (interval.task_title || interval.task) s += ' — ' + (interval.task_title || interval.task);
        return s;
    }
};
