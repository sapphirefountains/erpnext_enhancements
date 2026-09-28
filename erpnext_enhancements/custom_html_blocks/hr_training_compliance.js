// Training Compliance — HR Dashboard Custom HTML Block.
//
// Open training assignments that are overdue or due soon, from
// erpnext_enhancements.api.hr_dashboard.get_training_compliance.
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
    const METHOD = "erpnext_enhancements.api.hr_dashboard.get_training_compliance";
    let attempts = 0;

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#htc-body")) {
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
        return `<div class="htc-muted">${text}</div>`;
    }

    function render(container, message) {
        const body = container.querySelector("#htc-body");
        const count = container.querySelector("#htc-count");

        if (!message || message.enabled === false) {
            count.textContent = "";
            body.innerHTML = muted(__("Training Compliance is turned off in ERPNext Enhancements Settings."));
            return;
        }
        const assignments = message.assignments || [];
        count.textContent = message.overdue_count ? __("{0} overdue", [message.overdue_count]) : "";
        if (!assignments.length) {
            body.innerHTML = muted(__("Nothing is overdue or due in the next fortnight."));
            return;
        }

        body.innerHTML = assignments
            .map((a) => {
                const days = a.days_until;
                const label = a.overdue ? __("{0}d late", [Math.abs(days)]) : __("in {0}d", [days]);
                const tone = a.overdue ? "htc-pill-bad" : days <= 3 ? "htc-pill-warn" : "htc-pill";
                const meta = [a.learner, a.status].filter(Boolean).map(esc).join(" · ");
                return `
                    <div class="htc-row">
                        <a class="htc-name" href="/app/training-assignment/${encodeURIComponent(a.name)}">${esc(a.course)}</a>
                        <span class="htc-pill ${tone}">${esc(label)}</span>
                        <span class="htc-meta">${meta}</span>
                        <span class="htc-age">${esc(a.due_date || "")}</span>
                    </div>`;
            })
            .join("");
    }

    function startApp(container) {
        const body = container.querySelector("#htc-body");
        const refresh = container.querySelector("#htc-refresh");

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
                    if (current()) body.innerHTML = muted(__("Could not load training compliance."));
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
