// Awaiting Sign-Off — Design Dashboard Custom HTML Block.
//
// Designs that reached Reviewed but are not yet Issued, oldest first, from
// erpnext_enhancements.api.design_dashboard.get_signoff_queue.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.design_dashboard.get_signoff_queue";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dsq-body")) {
            startApp(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function esc(value) {
        return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
    }

    function money(value) {
        if (value === null || value === undefined || value === "") return "";
        try {
            return frappe.format(value, { fieldtype: "Currency" });
        } catch (e) {
            return String(value);
        }
    }

    function num(value, decimals) {
        if (value === null || value === undefined || value === "") return "—";
        return Number(value).toLocaleString(undefined, {
            minimumFractionDigits: decimals || 0,
            maximumFractionDigits: decimals || 0,
        });
    }

    function muted(text) {
        return `<div class="dsq-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#dsq-body");
        const count = container.querySelector("#dsq-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Awaiting Sign-Off is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const designs = message.designs || [];
        count.textContent = designs.length ? __("{0} waiting", [designs.length]) : "";
        if (!designs.length) {
            body.innerHTML = muted(__("Nothing is waiting on a sign-off."));
            return;
        }

        body.innerHTML = designs
            .map((d) => {
                // "Package Ready" with blockers open is the combination worth seeing:
                // it means the design believes it is done and the checks disagree.
                const state = d.blockers
                    ? `<span class="dsq-bad">${esc(__("{0} blockers", [d.blockers]))}</span>`
                    : d.ready
                      ? `<span class="dsq-good">${__("Ready")}</span>`
                      : "";
                const meta = [d.customer, state].filter(Boolean).join(" · ");
                const tone = d.waiting_days >= 14 ? "dsq-bad" : d.waiting_days >= 7 ? "dsq-warn" : "";
                return `
                    <div class="dsq-row">
                        <a class="dsq-name" href="/app/water-feature-design/${encodeURIComponent(d.name)}">${esc(d.title)}</a>
                        <span class="dsq-meta">${meta}</span>
                        <span class="dsq-age ${tone}">${esc(__("{0}d", [d.waiting_days]))}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#dsq-body");
        const refresh = container.querySelector("#dsq-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load the sign-off queue."));
                })
                .then(() => {
                    refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
    }

    waitForDOM();
})();
