// Weather — Finance Dashboard Custom HTML Block.
//
// Reuses the keyless Open-Meteo path from the /wall display: coordinates come
// from erpnext_enhancements.api.finance_dashboard.get_finance_config (shared with
// the Wall weather chip), then the browser fetches Open-Meteo directly. Shadow-DOM
// block model.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fwx-body")) {
            startApp(container);
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

    function renderWeather(body, label, data) {
        const esc = frappe.utils.escape_html;
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
                    <span class="fwx-cond">${esc(glyph[1])}</span>
                    <span class="fwx-loc">${esc(label || "")} ${range}</span>
                </span>
            </div>`;
    }

    function startApp(container) {
        const body = container.querySelector("#fwx-body");

        frappe
            .call({ method: "erpnext_enhancements.api.finance_dashboard.get_finance_config" })
            .then((r) => {
                const cfg = r.message || {};
                if (!cfg.enabled || !cfg.enabled.weather) {
                    body.innerHTML = `<div class="fwx-muted">${__("Weather is turned off in ERPNext Enhancements Settings.")}</div>`;
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
                    .then((data) => renderWeather(body, w.label, data))
                    .catch(() => {
                        body.innerHTML = `<div class="fwx-muted">${__("Weather unavailable.")}</div>`;
                    });
            })
            .catch(() => {
                body.innerHTML = `<div class="fwx-muted">${__("Weather unavailable.")}</div>`;
            });
    }

    waitForDOM();
})();
