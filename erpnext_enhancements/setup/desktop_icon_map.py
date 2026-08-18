"""The Desk home-grid tile for every module this app ships: label -> artwork.

Deliberately importless. Three very different consumers read this one dict, and
two of them have no bench:

* ``scripts/build_desktop_icons.py`` regenerates the SVGs from it (dev-only).
* ``setup/desktop_icons.py`` stamps ``Desktop Icon.logo_url`` from it on every migrate.
* ``tests/test_desktop_icons.py`` asserts the artwork on disk still matches it.

Adding ``import frappe`` here would force the build script and the bench-free test
to stub frappe just to read a literal, so keep this module free of imports.

Why ``logo_url`` and not the icon set Frappe looks up by filename
----------------------------------------------------------------
v16 picks a tile's image in ``frappe/public/js/frappe/ui/desktop_icon.html``, in
order: (1) ``frappe.utils.get_desktop_icon()``, which needs ``Desktop Icon.app``
set and then loads ``assets/<app>/icons/desktop_icons/<subtle|solid>/<scrub(label)>.svg``;
(2) ``logo_url`` / ``icon_image``; (3) the folder thumbnail; (4) a grey letter avatar.

Every tile of ours sat on (4) because ``create_desktop_icons_from_workspace()``
assigns ``icon.app_name`` -- a field Desktop Icon does not have; the real one is
``app`` -- so ``app`` stays NULL, (1) fails its first guard, and the letter avatar
is the only branch left. That is an upstream bug, not a missing setting here.

We use (2) rather than fixing (1) on our rows: one SVG per tile instead of a
subtle/solid pair, no ``app``/``standard`` change for v16's
``unset_standard_field_for_auto_generated_icons`` patch to undo, and no dependence
on the ``Desktop Settings.icon_style`` global. ERPNext ships its Subcontracting
tile exactly this way.

Note that ``Workspace.icon`` -- which all of these workspaces already set -- has
nothing to do with the tile. Frappe copies it once into the hidden
``Desktop Icon.icon`` field, renders it as a dead ``data-icon`` attribute, and
never reads it back.

Slugs are ``frappe.scrub(label)`` so the filename is predictable from the tile.
Colour groups tiles by function so a 36-tile grid is scannable; the glyph, not
the colour, identifies the tile.
"""

# Family colours. Baked into the artwork at build time, never read at runtime.
PLATFORM = "#7C3AED"  # violet -- the app's own plumbing
DELIVERY = "#E11D48"  # rose   -- customers, projects, the work itself
DASHBOARD = "#4F46E5"  # indigo -- read-only reporting surfaces
FINANCE = "#059669"  # emerald-- money
FIELD = "#D97706"  # amber  -- things that leave the building
PRODUCT = "#0289F7"  # blue   -- engineering and product (ERPNext's own blue)
INTEGRATION = "#475569"  # slate  -- other people's systems, and documents
PEOPLE = "#0D9488"  # teal   -- staff

# Desktop Icon label (which is also its docname) -> (svg slug, lucide glyph, colour).
#
# Glyph names are lucide ids from `frappe/public/icons/lucide/icons.svg` WITHOUT the
# `icon-` prefix, on version-16 (lucide-static v0.552.0). That vintage uses the newer
# lucide naming, so it is `chart-line`, not `line-chart`; the build script hard-fails
# on a name the sprite does not have rather than emitting an empty tile.
TILES = {
	# Platform
	"Enhancements Core": ("enhancements_core", "settings", PLATFORM),
	"AI Governance": ("ai_governance", "bot", PLATFORM),
	# Delivery and customers
	"CRM Enhancements": ("crm_enhancements", "contact", DELIVERY),
	"Project Enhancements": ("project_enhancements", "folder-kanban", DELIVERY),
	"Task Enhancements": ("task_enhancements", "list-checks", DELIVERY),
	# Dashboards
	"KPI Dashboards": ("kpi_dashboards", "layout-dashboard", DASHBOARD),
	"Executive Dashboard": ("executive_dashboard", "briefcase", DASHBOARD),
	"Finance Dashboard": ("finance_dashboard", "chart-line", DASHBOARD),
	"Sales Dashboard": ("sales_dashboard", "trending-up", DASHBOARD),
	"Marketing Dashboard": ("marketing_dashboard", "megaphone", DASHBOARD),
	"Operations Dashboard": ("operations_dashboard", "gauge", DASHBOARD),
	"Production Dashboard": ("production_dashboard", "factory", DASHBOARD),
	"Product Dashboard": ("product_dashboard", "box", DASHBOARD),
	"Design Dashboard": ("design_dashboard", "pen-tool", DASHBOARD),
	"HR Dashboard": ("hr_dashboard", "user-check", DASHBOARD),
	"Morning Briefing": ("morning_briefing", "sunrise", DASHBOARD),
	# Finance
	"Finance Hub": ("finance_hub", "wallet", FINANCE),
	"QuickBooks Online": ("quickbooks_online", "book-open-check", FINANCE),
	# Field and maintenance
	"Sapphire Maintenance": ("sapphire_maintenance", "wrench", FIELD),
	"Fleet Maintenance": ("fleet_maintenance", "truck", FIELD),
	"Devices": ("devices", "smartphone", FIELD),
	"Travel": ("travel", "plane", FIELD),
	"Asset Management": ("asset_management", "boxes", FIELD),
	# Engineering and product
	"Water Engineering": ("water_engineering", "droplets", PRODUCT),
	"Product Configurator": ("product_configurator", "sliders-horizontal", PRODUCT),
	"Inventory Enhancements": ("inventory_enhancements", "warehouse", PRODUCT),
	"Shipping": ("shipping", "package", PRODUCT),
	# Integrations and documents
	"Integration Hub": ("integration_hub", "plug", INTEGRATION),
	"Google Drive": ("google_drive", "hard-drive", INTEGRATION),
	"Process Documentation": ("process_documentation", "file-text", INTEGRATION),
	"QuickBooks Time": ("quickbooks_time", "clock", INTEGRATION),
	# People
	"Workforce": ("workforce", "users-round", PEOPLE),
	"Training": ("training", "graduation-cap", PEOPLE),
}

# Where the generated artwork lives, relative to the app package, and the URL it is
# served at. `<app>/public` is symlinked to `sites/assets/<app>`, so the two agree.
ASSET_SUBDIR = "public/desktop_icons"
ASSET_URL = "/assets/erpnext_enhancements/desktop_icons/{slug}.svg"


def logo_url(slug: str) -> str:
	"""Return the ``Desktop Icon.logo_url`` value for a tile slug."""
	return ASSET_URL.format(slug=slug)
