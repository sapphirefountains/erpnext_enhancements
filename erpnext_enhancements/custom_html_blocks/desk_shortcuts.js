// Desk Shortcuts — Custom HTML Block script (runs in the block's shadow-DOM
// sandbox; `root_element` is the shadow root provided by Frappe).
//
// Renders `frappe.boot.ee_desk_shortcuts` — the per-user tile list built
// server-side in `erpnext_enhancements/api/desk_shortcuts.py` and shipped via
// `boot.py` — as a grid of clickable icon tiles on the Home workspace. Icons
// are emoji because Frappe's SVG-sprite icons (`frappe.utils.icon`) can't
// resolve `<use href="#...">` across the shadow boundary. The whole block hides
// itself when the user has no visible shortcuts.
//
// Visibility is cosmetic — each target page enforces its own permissions, so a
// click that lands somewhere the user can't access still gets "not permitted".
//
// Workspace re-renders re-run this whole script with a NEW root_element; it is
// idempotent (re-reads boot and repaints) and all listeners live on elements
// inside root_element, so nothing stacks on `window`.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    // Accent hex per color name; applied at low alpha so the badge tint reads
    // correctly over both the light and dark card backgrounds.
    const ACCENTS = {
        Gray: "#9ca3af",
        Blue: "#3b82f6",
        Green: "#22c55e",
        Red: "#ef4444",
        Orange: "#f97316",
        Yellow: "#eab308",
        Purple: "#a855f7",
        Teal: "#14b8a6",
        Pink: "#ec4899",
    };

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#eds-grid")) {
            render(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        } else {
            console.warn("Desk Shortcuts: block DOM never appeared.");
        }
    }

    function routeTo(s) {
        try {
            if (s.type === "URL") {
                const u = s.url || s.link_to || "";
                if (!u) return;
                if (/^https?:\/\//i.test(u)) {
                    window.open(u, "_blank", "noopener");
                } else if (/^\/(desk|app)(?:[/?#]|$)/i.test(u)) {
                    // A Desk path routes in place. A full load boots the whole Desk again, and Back
                    // reloads it a second time. A legacy /app/... value is the same page.
                    const rel = u.replace(/^\/(desk|app)\/?/i, "");
                    const q = rel.indexOf("?");
                    const path = (q < 0 ? rel : rel.slice(0, q)).split("#")[0];
                    if (q >= 0) {
                        const opts = {};
                        new URLSearchParams(rel.slice(q + 1).split("#")[0]).forEach((v, k) => (opts[k] = v));
                        frappe.route_options = opts;
                    }
                    const segments = path.split("/").filter(Boolean).map((part) => {
                        try {
                            return decodeURIComponent(part);
                        } catch (e) {
                            return part; // a stray % is part of the name, not an escape
                        }
                    });
                    frappe.set_route(...segments);
                } else {
                    window.location.href = u; // site-relative www page, e.g. /kiosk
                }
            } else if (s.type === "DocType") {
                const view = s.doc_view || "List";
                if (view === "New") {
                    frappe.new_doc(s.link_to);
                } else {
                    frappe.set_route(view, s.link_to);
                }
            } else if (s.type === "Report") {
                frappe.set_route("query-report", s.link_to);
            } else {
                // Page (default)
                frappe.set_route(s.link_to);
            }
        } catch (e) {
            console.error("Desk Shortcuts: navigation failed", s, e);
        }
    }

    function makeTile(s) {
        const el = document.createElement("div");
        el.className = "eds-tile";
        el.setAttribute("role", "button");
        el.setAttribute("tabindex", "0");
        el.title = s.label || "";

        const badge = document.createElement("div");
        badge.className = "eds-badge";
        const accent = ACCENTS[s.color] || ACCENTS.Gray;
        badge.style.backgroundColor = accent + "22";
        badge.style.borderColor = accent + "55";
        badge.textContent = s.icon || "•";

        const label = document.createElement("div");
        label.className = "eds-label";
        label.textContent = s.label || "";

        el.appendChild(badge);
        el.appendChild(label);

        el.addEventListener("click", () => routeTo(s));
        el.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter" || ev.key === " ") {
                ev.preventDefault();
                routeTo(s);
            }
        });
        return el;
    }

    function render(container) {
        const root = container.querySelector("#eds-root");
        const grid = container.querySelector("#eds-grid");
        if (!grid) return;

        const items = (window.frappe && frappe.boot && frappe.boot.ee_desk_shortcuts) || [];
        grid.innerHTML = "";

        if (!items.length) {
            if (root) root.style.display = "none";
            return;
        }

        items.forEach((s) => grid.appendChild(makeTile(s)));
        if (root) root.style.display = "";
    }

    waitForDOM();
})();
