// Source Health — Marketing Dashboard Custom HTML Block.
//
// Whether the nightly GA4 and Search Console pulls are landing, from
// erpnext_enhancements.api.marketing_dashboard.get_source_health.
//
// Shadow-DOM sandbox: `root_element` is the shadow root, and the workspace
// re-runs this whole script with a fresh root on every navigation — so nothing
// is cached across renders and the refresh listener is bound to the fresh DOM
// each time (same model as the Finance Dashboard widgets).

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_source_health";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#msh-body")) {
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
        return `<div class="msh-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#msh-body");
        const count = container.querySelector("#msh-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Source Health is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const sources = message.sources || [];
        count.textContent = message.snapshot_date ? __("as of {0}", [message.snapshot_date]) : "";

        const stale = message.stale
            ? `<div class="msh-row"><span class="msh-meta msh-bad">${esc(
                  __("No web snapshot for over {0} days — the nightly pull may have stopped.", [
                      message.stale_after_days,
                  ])
              )}</span></div>`
            : "";

        const rows = sources
            .map((s) => {
                const pill = s.ok
                    ? `<span class="msh-pill msh-pill-good">${__("OK")}</span>`
                    : `<span class="msh-pill msh-pill-bad">${__("Failing")}</span>`;
                return `
                    <div class="msh-row">
                        <span class="msh-name">${esc(s.name)}</span>
                        ${pill}
                        <span class="msh-meta">${esc(s.detail || "")}</span>
                    </div>`;
            })
            .join("");

        // The pull error is the thing nobody was seeing; show it in full rather than
        // behind a hover.
        const error = message.error
            ? `<div class="msh-group">${__("Last error")}</div>${muted(esc(message.error))}`
            : "";

        body.innerHTML = stale + rows + error;
    }

    function startApp(container) {
        const body = container.querySelector("#msh-body");
        const refresh = container.querySelector("#msh-refresh");

        function load() {
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => render(container, r.message))
                .catch(() => {
                    body.innerHTML = muted(__("Could not load source health."));
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
