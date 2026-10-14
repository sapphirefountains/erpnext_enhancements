// Astrology — Finance Dashboard Custom HTML Block.
//
// Daily horoscope with a pick-a-sign selector. The chosen sign is remembered in
// localStorage; the text comes from
// erpnext_enhancements.api.horoscope.get_horoscope (server-side fetched + cached).
// Shadow-DOM block model: the workspace runs this script again only when it renders
// the page. Unlike the other Finance widgets it does NOT register with the return
// helper (public/js/global_enhancements/workspace_block_return.js): it is one text
// per sign per day, cached on the server, so asking again on every return would
// only redraw the same words.

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

    // The kit (panels, chips, escaping) and the ticket come from the shared helper,
    // workspace_block_return.js. A bundle cached from before them has neither: the block
    // then draws plain text, and every answer draws.
    function withKit(container) {
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.kit && blocks.ticket) {
            blocks.kit(container).then((sf) => startApp(container, sf, blocks.ticket));
            return;
        }
        const words = {
            loading: __("Loading…"),
            empty: __("Nothing to show"),
            error: __("Could not load"),
            "no-access": __("No access"),
        };
        const plain = (el, kind, opts) => (el.textContent = (opts && opts.title) || words[kind] || "");
        const escape = frappe.utils.escape_html;
        startApp(container, { esc: escape, chip: escape, state: { render: plain } }, () => () => true);
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#fas-body")) {
            withKit(container);
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

    function render(body, sf, message) {
        if (!message || message.enabled === false) {
            sf.state.render(body, "empty", {
                title: __("Horoscope is turned off in ERPNext Enhancements Settings."),
            });
            return;
        }
        if (!message.text) {
            sf.state.render(body, "empty", { title: message.reason || __("No horoscope available.") });
            return;
        }
        body.innerHTML = `<p class="fas-text">${sf.esc(message.text)}</p>`;
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#fas-body");
        const select = container.querySelector("#fas-sign");

        const current = storedSign() && SIGNS.includes(storedSign()) ? storedSign() : "Capricorn";
        select.innerHTML = SIGNS.map(
            (s) => `<option value="${s}"${s === current ? " selected" : ""}>${s}</option>`,
        ).join("");

        // Two sign changes in quick succession can overlap, so each ask takes a ticket on
        // the root (blocks.ticket) and only the newest sign's text draws.
        function load(sign) {
            const newest = ticket(container);
            sf.state.render(body, "loading");
            frappe
                .call({ method: "erpnext_enhancements.api.horoscope.get_horoscope", args: { sign } })
                .then((r) => {
                    if (newest()) render(body, sf, r.message);
                })
                .catch((err) => {
                    if (!newest()) return;
                    const title = __("Could not load horoscope.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: () => load(sign) });
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
