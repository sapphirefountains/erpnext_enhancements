"""Who sees what on the Dock: one rail for the whole desk, arranged for each person's job.

frappe 16.50 draws the Dock (the icon rail on the left) from three layers of one app's
``Dock``: the app's own file, the site's arrangement (``user = ""``) and each person's own
(``user = <email>``). Later layers win, and a saved layer *is* the rail: an entry it does not
name is dropped rather than appended (``frappe/desk/layers.py``, ``keep_unnamed=False``). There is
no per-role layer, so a dock that differs by job has to be written person by person.

Our own rows ride on ERPNext's rail. ``dock/erpnext_enhancements/erpnext_enhancements.json``
mounts this app on ``erpnext`` (``mount_on``), so its rows (the Opportunities Kanban, Maintenance,
Travel, Training and the rest) are appended to ERPNext's, and the desk shows that one rail in
our modules as well. Before, our sidebars drew a second rail of their own: 22 "(Custom)" entries
the 16.50 conversion wrote, which left out Travel, HR and KPI Dashboards.

Three writers here, and each layer is written whole, naming every entry the rail holds (the ones
a person should not see as ``hidden: 1``), the way frappe's own Manage Dock saves:

* ``write_site_layer``: the site's arrangement, ``SITE_DEFAULT``. What anyone without a dock
  profile sees, and what every person's layer is laid over.
* ``apply_dock_profiles`` (``after_migrate``): each enabled System User's own layer, from their
  profile. The profile is the User's ``custom_dock_profile`` when set, else the first entry of
  ``BY_ROLE_PROFILE`` their Role Profiles match.
* ``on_user_update`` (User ``on_update``): the same for one person, when their dock profile,
  Role Profiles or enabled flag change.

A layer belongs to the app while Administrator wrote it last, and to a person once they save
it. Frappe's Manage Dock saves as the person, so ``modified_by`` says who arranged a layer, and
this file writes only layers that are missing or still its own (Nik, 2026-10-23: "Keep their
own"). The one override is choosing someone's Dock Profile on their User form, which is a
deliberate act and is applied even over a layer they arranged. Writes made inside a User save run
as whoever saved it, so the stamp is put back to Administrator afterwards.

Nothing here can hide a module from anyone: the rail only stops offering it. Search, links and
Manage Dock still reach everything, and the reach gate frappe applies on every boot (module
visibility, workspace roles) is untouched.
"""

import frappe

HOST_APP = "erpnext"
APP = "erpnext_enhancements"

# The one user whose writes count as the app's own. See the module docstring.
APP_WRITER = "Administrator"

DOCK_PROFILE_FIELD = "custom_dock_profile"

# The User field's option for "nobody's own layer: the shared dock".
SITE_DEFAULT_OPTION = "Site Default"

# An entry is what it points at: (link_type, link_to), or ("URL", url). The same identity
# frappe's `dock_key` uses, so a re-labelled entry is still the same entry.
BUYING = ("Sidebar", "Buying")
STOCK = ("Sidebar", "Stock")
PROJECTS = ("Sidebar", "Projects")
CRM = ("Sidebar", "CRM")
SELLING = ("Sidebar", "Selling")
ACCOUNTS = ("Sidebar", "Accounts")
# The stock Kanban everyone works their deals on (836 visits from six people in the 90 days to
# 2026-10-23). The form kit's /desk/opportunity-board replaces it at P6.10; repoint this then.
OPPORTUNITIES = ("URL", "/desk/opportunity/view/kanban/Opportunity")
MAINTENANCE = ("Workspace", "Sapphire Maintenance")
TRAVEL = ("Workspace", "Travel")
TRAINING = ("Workspace", "Training")
KPI_DASHBOARDS = ("Workspace", "KPI Dashboards")
WATER_ENGINEERING = ("Workspace", "Water Engineering")
QUICKBOOKS = ("Workspace", "QuickBooks Online")
AI_GOVERNANCE = ("Workspace", "AI Governance")

# Nik, 2026-10-23: "Buying, Stock, Projects, Opportunities Kanban (CRM)". CRM stays after them:
# its dashboard is the module's landing page and had 189 visits from six people.
SITE_DEFAULT = (BUYING, STOCK, PROJECTS, OPPORTUNITIES, CRM)

# Each job's rail, in order. Drawn from 90 days of Route History per person (CHANGELOG
# v1.734.0) and approved by Nik on 2026-10-23. Everything not listed is still on the rail's
# Hidden pane in Manage Dock, one click from coming back.
PROFILES = {
	"Management": (
		BUYING,
		STOCK,
		PROJECTS,
		OPPORTUNITIES,
		CRM,
		SELLING,
		ACCOUNTS,
		MAINTENANCE,
		TRAVEL,
		TRAINING,
		KPI_DASHBOARDS,
		QUICKBOOKS,
		AI_GOVERNANCE,
	),
	"Sales": (OPPORTUNITIES, CRM, SELLING, PROJECTS),
	"Finance": (ACCOUNTS, QUICKBOOKS, SELLING, PROJECTS, MAINTENANCE),
	"Purchasing": (BUYING, STOCK, PROJECTS),
	"Production Lead": (PROJECTS, OPPORTUNITIES, BUYING, STOCK, MAINTENANCE),
	"Design": (PROJECTS, OPPORTUNITIES, BUYING, WATER_ENGINEERING),
	"Field Tech": (MAINTENANCE, PROJECTS, TRAVEL, TRAINING),
}

# Role Profile -> dock profile for a User whose Dock Profile is blank. First match wins, so the
# order is the precedence: Logan holds Design Team and Production Team and is a designer, Lisa
# holds Finance Team and Production Team and works in finance, and Clegg holds Production Team
# and Projects & Operations and runs production. Nik, James and Parker hold no Role Profile and
# are set on their User forms.
BY_ROLE_PROFILE = (
	("Management", frozenset({"Executive"})),
	("Sales", frozenset({"Sales", "Sales Team", "Sales & Marketing"})),
	("Finance", frozenset({"Finance", "Finance Team", "Accounting"})),
	("Purchasing", frozenset({"Purchase", "Inventory"})),
	("Production Lead", frozenset({"Projects & Operations"})),
	("Design", frozenset({"Design Team"})),
	("Field Tech", frozenset({"Production Team", "Technician"})),
)


def profile_for(chosen, role_profiles):
	"""Return the dock profile one person gets, or None for the shared dock.

	`chosen` is their User's Dock Profile; `role_profiles` every Role Profile they hold.
	"""
	chosen = (chosen or "").strip()
	if chosen == SITE_DEFAULT_OPTION:
		return None
	if chosen in PROFILES:
		return chosen
	for profile, matches in BY_ROLE_PROFILE:
		if matches & set(role_profiles):
			return profile
	return None


def rows_for(visible, base):
	"""Return a whole layer: `visible` in its order, then every other entry of `base` hidden.

	`base` is the rail the layer is laid over, as entry keys. An entry `visible` names that the
	rail does not hold is skipped, because a row naming nothing is dropped by frappe anyway.
	"""
	present = set(base)
	shown = []
	for key in visible:
		if key in present and key not in shown:
			shown.append(key)
	return [_row(key, 0) for key in shown] + [_row(key, 1) for key in base if key not in shown]


def _row(key, hidden):
	link_type, target = key
	is_url = link_type == "URL"
	return {
		"link_type": link_type,
		"link_to": None if is_url else target,
		"url": target if is_url else None,
		"hidden": hidden,
		"added": 0,
	}


def _key(row):
	link_type = row.get("link_type")
	return (link_type, row.get("url") if link_type == "URL" else row.get("link_to"))


def _signature(rows):
	return [(*_key(row), int(row.get("hidden") or 0)) for row in rows]


def rail_base():
	"""Return the entry keys ERPNext's rail holds before any layer: ERPNext's own dock, then the
	rows this app mounts on it. Empty on a site before frappe 16.50, which has no Dock."""
	if not (frappe.db.table_exists("Dock") and frappe.db.table_exists("Dock Item")):
		return []
	docks = {
		row.app: row.name
		for row in frappe.get_all(
			"Dock", filters={"standard": 1, "app": ["in", [HOST_APP, APP]]}, fields=["name", "app"]
		)
	}
	if HOST_APP not in docks:
		return []

	keys = []
	for app in (HOST_APP, APP):
		if app not in docks:
			continue
		for row in frappe.get_all(
			"Dock Item",
			filters={"parenttype": "Dock", "parent": docks[app]},
			fields=["link_type", "link_to", "url"],
			order_by="idx asc",
		):
			key = _key(row)
			if key[0] and key[1] and key not in keys:
				keys.append(key)
	return keys


def write_layer(user, rows, force=False):
	"""Write one layer of ERPNext's dock: the site's (`user` blank) or one person's.

	Returns what happened: "created", "updated", "unchanged" or "kept" (the layer is someone's own
	and `force` is off).
	"""
	existing = frappe.get_all(
		"Dock",
		filters={"app": HOST_APP, "user": user or "", "standard": 0},
		fields=["name", "modified_by"],
	)
	if existing:
		if not force and existing[0].modified_by != APP_WRITER:
			return "kept"
		doc = frappe.get_doc("Dock", existing[0].name)
		if _signature(doc.items) == _signature(rows):
			return "unchanged"
		outcome = "updated"
	else:
		doc = frappe.new_doc("Dock")
		doc.app = HOST_APP
		doc.user = user or ""
		doc.standard = 0
		outcome = "created"

	doc.set("items", [])
	for row in rows:
		doc.append("items", row)
	doc.save(ignore_permissions=True)

	# Inside a User save this ran as whoever saved it, which would read as their arrangement.
	if doc.modified_by != APP_WRITER:
		frappe.db.set_value("Dock", doc.name, "modified_by", APP_WRITER, update_modified=False)
	return outcome


def drop_layer(user):
	"""Remove a person's layer if it is still the app's, so they fall back to the shared dock."""
	for row in frappe.get_all(
		"Dock", filters={"app": HOST_APP, "user": user, "standard": 0}, fields=["name", "modified_by"]
	):
		if row.modified_by == APP_WRITER:
			frappe.delete_doc("Dock", row.name, ignore_permissions=True, force=True)
			return "dropped"
	return "kept"


def write_site_layer(force=False):
	"""Write the site's arrangement of ERPNext's dock from `SITE_DEFAULT`."""
	base = rail_base()
	if not base:
		return None
	return write_layer("", rows_for(SITE_DEFAULT, base), force=force)


def apply_dock_profiles():
	"""``after_migrate``: keep the shared dock and everyone's own on their profiles.

	Runs on every migrate, so an entry an app adds later reaches the layers this file owns as a
	hidden row, ready to bring back. Logs and returns rather than raising: a rail is not worth a
	failed deploy.
	"""
	try:
		base = rail_base()
		if not base:
			return
		write_site_layer()
		people = _people()
	except Exception:
		frappe.log_error(title="Dock profiles not applied", message=frappe.get_traceback())
		return

	for person in people:
		try:
			_apply(person, base)
		except Exception:
			frappe.log_error(
				title=f"Dock profile not applied: {person['name']}", message=frappe.get_traceback()
			)


def apply_for_user(user, force=False):
	"""Apply one person's dock profile. Returns what happened, or None when there is no rail."""
	base = rail_base()
	if not base:
		return None
	person = next((row for row in _people(user) if row["name"] == user), None)
	if not person:
		return None
	return _apply(person, base, force=force)


def _apply(person, base, force=False):
	profile = profile_for(person["chosen"], person["role_profiles"])
	if not profile:
		return drop_layer(person["name"])
	return write_layer(person["name"], rows_for(PROFILES[profile], base), force=force)


def _people(user=None):
	"""Enabled System Users, each with their chosen dock profile and every Role Profile they hold.

	Never Administrator: a layer Administrator saved is how this file recognises its own, so
	theirs could not be told apart from one written here.
	"""
	if user in ("Administrator", "Guest"):
		return []
	fields = ["name", "role_profile_name"]
	if _has_dock_profile_field():
		fields.append(DOCK_PROFILE_FIELD)
	filters = {"enabled": 1, "user_type": "System User", "name": ["not in", ["Administrator", "Guest"]]}
	if user:
		filters["name"] = user
	users = frappe.get_all("User", filters=filters, fields=fields)
	if not users:
		return []

	held = {}
	try:
		for row in frappe.get_all(
			"User Role Profile",
			filters={"parenttype": "User", "parent": ["in", [u.name for u in users]]},
			fields=["parent", "role_profile"],
		):
			held.setdefault(row.parent, set()).add(row.role_profile)
	except Exception:
		# A frappe before multi-profile users has no such table; role_profile_name still answers.
		pass

	return [
		{
			"name": u.name,
			"chosen": u.get(DOCK_PROFILE_FIELD) or "",
			"role_profiles": held.get(u.name, set())
			| ({u.role_profile_name} if u.role_profile_name else set()),
		}
		for u in users
	]


def _has_dock_profile_field():
	# has_column raises on a missing table rather than returning False.
	try:
		return bool(frappe.db.has_column("User", DOCK_PROFILE_FIELD))
	except Exception:
		return False


def _role_profiles_of(doc):
	held = {row.get("role_profile") for row in (doc.get("role_profiles") or [])}
	held.add(doc.get("role_profile_name"))
	return held - {None, ""}


def on_user_update(doc, method=None):
	"""User ``on_update``: redo this person's dock when what decides it changed.

	Choosing a Dock Profile on the form is applied even over a layer they arranged themselves;
	a Role Profile change is not. Never raises: a User save is not worth losing to a rail. User is
	saved on paths next to login and install, so the people this never applies to leave first.
	"""
	if doc.get("name") in ("Administrator", "Guest") or doc.get("user_type") != "System User":
		return
	try:
		before = doc.get_doc_before_save()
		if before is None:
			chosen_changed = profiles_changed = enabled_changed = True
		else:
			chosen_changed = (before.get(DOCK_PROFILE_FIELD) or "") != (doc.get(DOCK_PROFILE_FIELD) or "")
			profiles_changed = _role_profiles_of(before) != _role_profiles_of(doc)
			enabled_changed = bool(before.get("enabled")) != bool(doc.get("enabled"))
		if not (chosen_changed or profiles_changed or enabled_changed):
			return
		if not doc.get("enabled"):
			return
		apply_for_user(doc.name, force=bool(before is not None and chosen_changed))
	except Exception:
		frappe.log_error(title=f"Dock profile not applied: {doc.name}", message=frappe.get_traceback())
