// Astrology — Finance Dashboard Custom HTML Block.
//
// Daily horoscope with a pick-a-sign selector. The chosen sign is remembered in
// localStorage; the text comes from
// erpnext_enhancements.api.horoscope.get_horoscope (server-side fetched + cached).
// Shadow-DOM block model.

(function () {
    const MAX_ATTEMPTS = 50;
    const STORAGE_KEY = "ee_finance_astrology_sign";
    const SIGNS = [
        "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
        "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
    ];
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fas-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function storedSign() {
        try {
            return localStorage.getItem(STORAGE_KEY);
        } catch (e) {
            return null;
        }
    }

    function saveSign(sign) {
        try {
            localStorage.setItem(STORAGE_KEY, sign);
        } catch (e) {
            /* private mode — ignore */
        }
    }

    function render(body, message) {
        const esc = frappe.utils.escape_html;
        if (!message || message.enabled === false) {
            body.innerHTML = `<div class="fas-muted">${__("Horoscope is turned off in ERPNext Enhancements Settings.")}</div>`;
            return;
        }
        if (!message.text) {
            body.innerHTML = `<div class="fas-muted">${esc(message.reason || __("No horoscope available."))}</div>`;
            return;
        }
        body.innerHTML = `<p class="fas-text">${esc(message.text)}</p>`;
    }

    function startApp(container) {
        const body = container.querySelector("#fas-body");
        const select = container.querySelector("#fas-sign");

        const current = storedSign() && SIGNS.includes(storedSign()) ? storedSign() : "Leo";
        select.innerHTML = SIGNS.map(
            (s) => `<option value="${s}"${s === current ? " selected" : ""}>${s}</option>`,
        ).join("");

        function load(sign) {
            body.innerHTML = `<div class="fas-muted">${__("Loading…")}</div>`;
            frappe
                .call({ method: "erpnext_enhancements.api.horoscope.get_horoscope", args: { sign } })
                .then((r) => render(body, r.message))
                .catch(() => {
                    body.innerHTML = `<div class="fas-muted">${__("Could not load horoscope.")}</div>`;
                });
        }

        select.addEventListener("change", () => {
            saveSign(select.value);
            load(select.value);
        });
        load(current);
    }

    waitForDOM();
})();
