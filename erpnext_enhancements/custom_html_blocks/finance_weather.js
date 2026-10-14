// Weather — Finance Dashboard Custom HTML Block.
//
// Reuses the keyless Open-Meteo path from the /wall display: coordinates come
// from erpnext_enhancements.api.finance_dashboard.get_finance_config (shared with
// the Wall weather chip), then the browser fetches Open-Meteo directly. Shadow-DOM
// block model: the workspace runs this script again only when it renders the page,
// so a return from a form or a list is reloaded by the shared helper,
// public/js/global_enhancements/workspace_block_return.js. The conditions it shows
// are current ones, so they are worth asking for again.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || kind);
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fwx-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    // WMO weather codes → glyph + label (ported from the /wall WeatherWidget).
    function wmo(code) {
        if (code === 0) return ["☀️", "Clear"];
        if (code <= 2) return ["🌤", "Partly cloudy"];
        if (code === 3) return ["☁️", "Overcast"];
        if (code <= 48) return ["🌫", "Fog"];
        if (code <= 57) return ["🌦", "Drizzle"];
        if (code <= 67) return ["🌧", "Rain"];
        if (code <= 77) return ["🌨", "Snow"];
        if (code <= 82) return ["🌦", "Showers"];
        if (code <= 86) return ["🌨", "Snow showers"];
        if (code <= 99) return ["⛈", "Thunderstorm"];
        return ["🌡", ""];
    }

    function renderWeather(body, sf, label, data) {
        const current = data.current || {};
        const daily = data.daily || {};
        const glyph = wmo(current.weather_code);
        const hi =
            daily.temperature_2m_max && daily.temperature_2m_max.length
                ? Math.round(daily.temperature_2m_max[0])
                : null;
        const lo =
            daily.temperature_2m_min && daily.temperature_2m_min.length
                ? Math.round(daily.temperature_2m_min[0])
                : null;
        const range = hi !== null && lo !== null ? `<span class="fwx-range">${hi}° / ${lo}°</span>` : "";
        body.innerHTML = `
            <div class="fwx-chip">
                <span class="fwx-icon">${glyph[0]}</span>
                <span class="fwx-temp">${Math.round(current.temperature_2m)}°</span>
                <span class="fwx-info">
                    <span class="fwx-cond">${sf.esc(glyph[1])}</span>
                    <span class="fwx-loc">${sf.esc(label || "")} ${range}</span>
                </span>
            </div>`;
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#fwx-body");

        // Two returns in quick succession can overlap, so each ask takes a ticket on the
        // root (blocks.ticket) and only the newest one draws.
        function load() {
            const current = ticket(container);
            // The settings call refused (403) is no access; anything else, Open-Meteo
            // included, is "unavailable", with Try again.
            const unavailable = (err) => {
                if (!current()) return;
                const title = __("Weather unavailable.");
                if (err && err.status === 403) sf.state.render(body, "no-access");
                else sf.state.render(body, "error", { title, on_action: load });
            };
            frappe
                .call({ method: "erpnext_enhancements.api.finance_dashboard.get_finance_config" })
                .then((r) => {
                    if (!current()) return;
                    const cfg = r.message || {};
                    if (!cfg.enabled || !cfg.enabled.weather) {
                        sf.state.render(body, "empty", {
                            title: __("Weather is turned off in ERPNext Enhancements Settings."),
                        });
                        return;
                    }
                    const w = cfg.weather || {};
                    const url =
                        "https://api.open-meteo.com/v1/forecast?latitude=" +
                        encodeURIComponent(w.latitude) +
                        "&longitude=" +
                        encodeURIComponent(w.longitude) +
                        "&current=temperature_2m,weather_code&daily=temperature_2m_max,temperature_2m_min" +
                        "&temperature_unit=fahrenheit&timezone=auto&forecast_days=1";
                    fetch(url)
                        .then((resp) => resp.json())
                        .then((data) => {
                            if (current()) renderWeather(body, sf, w.label, data);
                        })
                        .catch(unavailable);
                })
                .catch(unavailable);
        }

        sf.state.render(body, "loading");
        load();
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
