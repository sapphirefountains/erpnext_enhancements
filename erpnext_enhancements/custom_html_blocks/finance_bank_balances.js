// Bank Balances — Finance Dashboard Custom HTML Block.
//
// Reads the cached snapshot from
// erpnext_enhancements.plaid_banking.core.api.get_bank_balances (the widget never
// calls Plaid directly). "Refresh" spends one live Plaid call per linked bank via
// refresh_now. Balances are grouped by Bank, each with its own status: a bank in
// "Reconnect Required" shows an amber banner routing to that Bank's form, where the
// native "Refresh Plaid Link" button lives — re-linking is ERPNext's flow, not ours.
// Shadow-DOM block model: the workspace runs this script again only when it renders
// the page, so a return from a form or a list is reloaded by the shared helper,
// public/js/global_enhancements/workspace_block_return.js. It reloads the cached
// snapshot only: a return never spends a live Plaid call.

(function () {
    const MAX_ATTEMPTS = 50;
    const READ = "erpnext_enhancements.plaid_banking.core.api.get_bank_balances";
    const REFRESH = "erpnext_enhancements.plaid_banking.core.api.refresh_now";
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
        if (container.querySelector("#fbb-body")) {
            withKit(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    function money(value, currency) {
        if (value === null || value === undefined) return "—";
        return frappe.format(value, { fieldtype: "Currency", options: currency || "USD" });
    }

    function bankFormUrl(bank) {
        return `/desk/bank/${encodeURIComponent(bank)}`;
    }

    function renderAccount(sf, a) {
        const mask = a.mask ? `••${sf.esc(a.mask)}` : "";
        const sub = [sf.esc(a.subtype || a.type || ""), mask].filter(Boolean).join(" ");
        const available =
            a.available !== null && a.available !== undefined
                ? `<span class="fbb-avail">${__("avail")} ${sf.esc(money(a.available, a.currency))}</span>`
                : "";
        return `
            <div class="fbb-acct">
                <div class="fbb-acct-top">
                    <span class="fbb-acct-name">${sf.esc(a.name || __("Account"))}</span>
                    <span class="fbb-acct-bal">${sf.esc(money(a.current, a.currency))}</span>
                </div>
                <div class="fbb-acct-foot">
                    <span class="fbb-acct-sub">${sub}</span>
                    ${available}
                </div>
            </div>`;
    }

    function renderBank(sf, b) {
        const status = b.status || "Connected";
        let body;
        if (status === "Reconnect Required") {
            body = `<div class="fbb-reconnect">${__("Bank connection needs re-authentication.")}
                <a href="${bankFormUrl(b.bank)}">${__("Open {0} → Refresh Plaid Link", [sf.esc(b.bank)])}</a></div>`;
        } else if (status === "Error") {
            body = `<div class="fbb-bank-msg">${sf.esc(b.message || __("Could not fetch balances."))}</div>`;
        } else if (!(b.accounts || []).length) {
            body = `<div class="fbb-bank-msg">${__("No accounts returned yet — press Refresh.")}</div>`;
        } else {
            body = b.accounts.map((a) => renderAccount(sf, a)).join("");
        }
        const pill = status === "Connected" ? "" : sf.chip(status, status === "Error" ? "danger" : "warn");
        return `
            <div class="fbb-bank">
                <div class="fbb-bank-head">
                    <span class="fbb-bank-name">${sf.esc(b.bank)}</span>
                    ${pill}
                </div>
                ${body}
            </div>`;
    }

    function render(container, sf, message) {
        const body = container.querySelector("#fbb-body");
        const meta = container.querySelector("#fbb-meta");
        meta.textContent = "";

        if (!message || message.enabled === false) {
            sf.state.render(body, "empty", {
                title: __("Bank Balances are turned off. Switch the widget on in Plaid Banking Settings."),
            });
            return;
        }
        if (message.paused) {
            body.innerHTML = `<div class="fbb-reconnect">${sf.esc(message.status_message || __("Plaid is paused after a configuration error."))}
                <a href="/desk/plaid-settings">${__("Check the native Plaid Settings")}</a>
                <a href="/desk/plaid-banking-settings">${__("Plaid Banking Settings → Test Connection")}</a></div>`;
            return;
        }
        const banks = message.banks || [];
        if (!banks.length) {
            const reason =
                message.status === "Connected"
                    ? __("No balances cached yet — press Refresh.")
                    : __("No bank is linked. Link one in the native Plaid Settings (ERPNext Integrations).");
            body.innerHTML = `<div class="fbb-muted">${reason} <a href="/desk/plaid-settings">${__("Open Plaid Settings")}</a></div>`;
            return;
        }

        meta.textContent = __("{0} bank(s)", [banks.length]);
        body.innerHTML = banks.map((b) => renderBank(sf, b)).join("");

        if (message.last_sync) {
            const stamp = document.createElement("div");
            stamp.className = "fbb-stamp";
            stamp.textContent = __("as of {0}", [frappe.datetime.str_to_user(message.last_sync) || message.last_sync]);
            body.appendChild(stamp);
        }
    }

    function startApp(container, sf, ticket) {
        const body = container.querySelector("#fbb-body");
        const refresh = container.querySelector("#fbb-refresh");

        // Refresh and a return to the workspace can both ask while an answer is on its
        // way, so each read takes a ticket on the root (blocks.ticket) and only the newest
        // one draws.
        function load() {
            const current = ticket(container);
            sf.state.render(body, "loading");
            refresh.disabled = true;
            frappe
                .call({ method: READ })
                .then((r) => {
                    if (current()) render(container, sf, r.message);
                })
                .catch((err) => {
                    if (!current()) return;
                    const title = __("Could not load bank balances.");
                    if (err && err.status === 403) sf.state.render(body, "no-access");
                    else sf.state.render(body, "error", { title, on_action: load });
                })
                .then(() => {
                    if (current()) refresh.disabled = false;
                });
        }

        function refreshNow() {
            refresh.disabled = true;
            sf.state.render(body, "loading", { title: __("Refreshing from your banks…") });
            frappe
                .call({ method: REFRESH })
                .then((r) => {
                    const m = r.message || {};
                    if (!m.ok && m.message) {
                        frappe.show_alert({ message: m.message, indicator: "orange" });
                    }
                })
                .catch(() => {
                    frappe.show_alert({ message: __("Refresh failed."), indicator: "red" });
                })
                .then(() => load());
        }

        refresh.addEventListener("click", refreshNow);
        load();
        // load, never refreshNow: a return reads the cached snapshot. And not while Refresh or a
        // load is still out (the button is disabled then): load() would switch the button back on
        // mid-refresh, and a second click spends another billed Plaid call per bank.
        const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
        if (blocks && blocks.onWorkspaceReturn) {
            blocks.onWorkspaceReturn(container, () => {
                if (!refresh.disabled) load();
            });
        }
    }

    waitForDOM();
})();
