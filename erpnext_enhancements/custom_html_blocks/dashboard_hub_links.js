// Dashboard Hub Links — the way back from a department dashboard to its hub.
//
// Every hub links to its dashboard with a URL shortcut, and that is safe: on prod on
// 2026-10-14 nobody who could open a hub was refused its dashboard. The other direction is
// not. A dashboard is gated on ERPNext's broad roles (Projects Manager, Sales User, Item
// Manager ...) and a hub on a team role, so 8 of the 14 people who could open the Design
// Dashboard could not open the Design Hub, and 11 of 14 on Operations. A URL shortcut is
// never permission-filtered, so a "Design Hub" shortcut on the dashboard would have been a
// dead tile for most of the people looking at it.
//
// So this block draws the link only for a hub the viewer can open: `frappe.workspaces` is
// the desk's map of the workspaces this session may open, keyed by slug (desk.js
// setup_workspaces, from frappe.boot.allowed_workspaces). When none survives, the block
// stays hidden.
//
// It asks the server nothing, so there is no panel to draw and nothing to reload when you
// come back to the dashboard: the hubs you may open do not change until the desk reloads.
// The pairs are pinned against the workspace JSONs by tests/test_s4a_hub_links.py.
//
// Shadow-DOM sandbox: `root_element` is the shadow root. The workspace runs this whole
// script again, with a fresh root, when it renders the page, and every listener lives on an
// element inside that root.

(function () {
    const MAX_ATTEMPTS = 50;
    let attempts = 0;

    // Dashboard -> the hubs it leads back to, most likely first.
    const HUBS = {
        "Sales Dashboard": ["Sales Hub"],
        "Design Dashboard": ["Design Hub"],
        "Marketing Dashboard": ["Marketing Hub"],
        "Operations Dashboard": ["Operations Hub"],
        "Production Dashboard": ["Production Hub"],
        "Service Dashboard": ["Support Hub", "Production Hub"],
        "Product Dashboard": ["Design Hub", "Operations Hub"],
        "HR Dashboard": ["HR Hub"],
        "Finance Dashboard": ["Finance Hub"],
        "Executive Dashboard": ["Executive Hub"],
    };

    function slug(name) {
        // frappe.router.slug, which desk.js keys frappe.workspaces with.
        return String(name || "")
            .toLowerCase()
            .replace(/ /g, "-");
    }

    function getContainer() {
        return typeof root_element !== "undefined" && root_element ? root_element : document;
    }

    function waitForDOM() {
        const container = getContainer();
        if (container.querySelector("#dhl-links")) {
            render(container);
        } else if (++attempts < MAX_ATTEMPTS) {
            setTimeout(waitForDOM, 100);
        }
    }

    // The dashboard on screen: ["Workspaces", "Sales Dashboard"], or a slug.
    function currentDashboard() {
        const route = (frappe.get_route && frappe.get_route()) || [];
        for (const seg of route.slice().reverse()) {
            let decoded = String(seg || "");
            try {
                decoded = decodeURIComponent(decoded);
            } catch (e) {
                /* a stray % is part of the name */
            }
            for (const name of Object.keys(HUBS)) {
                if (name === decoded || slug(name) === slug(decoded)) return name;
            }
        }
        return null;
    }

    function canOpen(hub) {
        const open = window.frappe && frappe.workspaces;
        return Boolean(open && open[slug(hub)]);
    }

    function link(hub) {
        const a = document.createElement("a");
        a.className = "dhl-link";
        a.href = "/desk/" + slug(hub);
        a.textContent = __(hub);
        // In place: a click inside a shadow root never reaches the router's own link
        // handler, so a plain link would load the whole Desk again. A modified click still
        // opens a new tab the browser's way.
        a.addEventListener("click", (ev) => {
            if (ev.button || ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
            ev.preventDefault();
            frappe.set_route(slug(hub));
        });
        return a;
    }

    function render(container) {
        const root = container.querySelector("#dhl-root");
        const links = container.querySelector("#dhl-links");
        try {
            const hubs = (HUBS[currentDashboard()] || []).filter(canOpen);
            links.textContent = "";
            hubs.forEach((hub) => links.appendChild(link(hub)));
            if (root) root.style.display = hubs.length ? "" : "none";
        } catch (e) {
            // A route or a desk shape this does not know: draw nothing rather than break the page.
            if (root) root.style.display = "none";
        }
    }

    waitForDOM();
})();
