// Channel Spend & CPL — Marketing Dashboard Custom HTML Block.
//
// Month-to-date spend per channel against attributed leads, from
// erpnext_enhancements.api.marketing_dashboard.get_channel_spend.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this
// whole script again, with a fresh root, only when it renders the page: the first
// visit, and coming back from a different workspace. Coming back from a form or a
// list does not (v16's Workspace.show() returns early on the workspace already
// shown), so startApp hands load() to the shared return helper,
// public/js/global_enhancements/workspace_block_return.js, which runs it again
// then. Nothing is cached across renders, and the refresh listener is bound to the
// fresh DOM each time.

(function () {
    const MAX_ATTEMPTS = 50;
    const METHOD = "erpnext_enhancements.api.marketing_dashboard.get_channel_spend";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#mcs-body")) {
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
        return `<div class="mcs-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#mcs-body");
        const count = container.querySelector("#mcs-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Channel Spend & CPL is turned off in ERPNext Enhancements Settings."));
            return;
        }
        if (message.unavailable) {
            count.textContent = "";
            body.innerHTML = muted(esc(message.unavailable));
            return;
        }

        const channels = message.channels || [];
        count.textContent = message.blended_cpl
            ? __("{0} blended CPL", [money(message.blended_cpl)])
            : __("month to date");

        if (!channels.length) {
            body.innerHTML = muted(__("No spend recorded for this month yet."));
            return;
        }

        const warning = message.attribution_available
            ? ""
            : muted(__("Lead attribution is not installed, so no cost per lead can be computed."));

        body.innerHTML =
            warning +
            channels
                .map((c) => {
                    // A channel with spend and no attributed leads is the case worth
                    // seeing, so it is listed with an em dash rather than hidden.
                    const cpl = c.cpl ? money(c.cpl) : "—";
                    const leads = c.matched ? __("{0} leads", [c.leads]) : __("no attributed leads");
                    const tone = c.matched ? "" : "mcs-warn";
                    return `
                    <div class="mcs-row">
                        <span class="mcs-name">${esc(c.channel)}</span>
                        <span class="mcs-meta ${tone}">${esc(leads)}</span>
                        <span class="mcs-age">${esc(money(c.spend))} · ${esc(cpl)}</span>
                    </div>`;
                })
                .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#mcs-body");
        const refresh = container.querySelector("#mcs-refresh");

        // The refresh button and a return to the workspace can both ask while an answer
        // is on its way, so each ask takes a ticket and only the newest one draws. The
        // ticket lives on the root: an answer only ever draws into the root it was for.
        function load() {
            const ticket = (container.__ee_ticket = (container.__ee_ticket || 0) + 1);
            const current = () => container.__ee_ticket === ticket;
            refresh.disabled = true;
            frappe
                .call({ method: METHOD })
                .then((r) => {
                    if (current()) render(container, r.message);
                })
                .catch(() => {
                    if (current()) body.innerHTML = muted(__("Could not load channel spend."));
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        refresh.addEventListener("click", load);
        load();
        // Coming back to this workspace runs nothing again (the header), so the shared
        // helper reloads the block then. A bundle cached from before the helper has no
        // helper, and the block still draws; it just keeps its first answer.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
    }

    waitForDOM();
})();
