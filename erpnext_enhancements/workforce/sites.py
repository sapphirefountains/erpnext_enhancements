# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Where a project's site is — the coordinates the off-site check and the map use.

Three places on this site can hold a project's coordinates, and on 2026-09-17 almost none
of them did: 16 Sapphire Maintenance Profiles with **0** carrying coordinates, 0 open
Projects linking an Address, and 11 of 1,024 Addresses geocoded. So this module does two
things: it resolves coordinates from whichever source has them, in a fixed order, and it
fills the gap by geocoding the project's own address text into new Project fields.

Resolution order (``site_coordinates``): the project's **Sapphire Maintenance Profile**
(a maintenance tech has stood on the site and pinned it) → the linked **Address**'s
``custom_latitude/longitude`` → the Project's **own** ``custom_site_latitude/longitude``
(``Geocoded`` by this module, or ``Manual``) → nothing. ``radius_m`` is the site-wide
``ERPNext Enhancements Settings.geofence_radius_m``; 0 means the geofence is off and
nothing is ever flagged off-site.

Geocoding goes through the Google Geocoding REST API with ``requests`` — no SDK, per ADR
0004 and the same reason the QuickBooks and Stripe clients hand-roll theirs: the host
cannot pip-install.

**This call needs its own key.** It uses ``Travel Settings.google_geocoding_api_key`` and
falls back to ``google_maps_api_key`` only when that is blank. The two cannot be one key:
the maps key is handed to browsers and so is restricted by HTTP referrer, and Google
refuses a referrer-restricted key on every server-side web-service call — ``REQUEST_DENIED``,
"API keys with referer restrictions cannot be used with this API". Loosening the browser
key's restriction to make this work would publish an unrestricted key to every device that
loads a map, so the answer is a second, IP-restricted key rather than a weaker first one.
Confirmed on production 2026-09-18: restricting the browser key correctly — which is the
right thing to do — stopped every geocode with exactly that status. Either way this module
logs and walks away — it never raises, because it runs in a background job and on the
Project save path.

Two re-drives, because a deploy ``FLUSHDB``s the queue redis and destroys every queued
job: the patch enqueues a bounded backfill once, and ``backfill_missing_site_coordinates``
runs daily to enqueue whatever is still missing. ``geocode_project`` is idempotent (it
returns at once when coordinates already exist), so the overlap is harmless.
"""

import frappe
from frappe.utils import cint, flt

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GEOCODE_TIMEOUT_S = 10

#: Project custom fields this module reads and writes (fixture custom_field.json).
PROJECT_COORD_FIELDS = (
	"custom_site_latitude",
	"custom_site_longitude",
	"custom_site_location_source",
	"custom_site_geocoded_from",
)

SOURCE_PROFILE = "Maintenance Profile"
SOURCE_ADDRESS = "Address"
SOURCE_GEOCODED = "Geocoded"
SOURCE_MANUAL = "Manual"

#: Per-run bound on the backfill, so a site with a thousand un-geocoded projects
#: cannot fire a thousand Google calls in one deploy.
BACKFILL_LIMIT = 200


# ------------------------------------------------------------------ guards


def _has_columns(doctype, columns):
	"""Which of ``columns`` exist on ``doctype``. ``has_column`` takes a DOCTYPE and
	raises on an unknown table, hence the try/except per column."""
	present = []
	for column in columns:
		try:
			if frappe.db.has_column(doctype, column):
				present.append(column)
		except Exception:
			pass
	return present


def geofence_radius_m():
	try:
		return cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "geofence_radius_m"))
	except Exception:
		return 0


# ------------------------------------------------------------------ resolution


def site_coordinates(project):
	"""``{"lat", "lng", "source", "radius_m"}`` for one project, or None."""
	if not project:
		return None
	return site_coordinates_bulk([project]).get(project)


def site_coordinates_bulk(project_names):
	"""Coordinates for many projects in three queries (profiles, projects, addresses).

	Returns ``{project: {"lat", "lng", "source", "radius_m"}}`` with only the projects
	that resolved. Used by ``get_kiosk_options`` for every active project at once.
	"""
	names = [n for n in (project_names or []) if n]
	if not names:
		return {}
	radius = geofence_radius_m()
	result = {}

	# 1. Sapphire Maintenance Profile — the tech pinned it.
	try:
		if frappe.db.table_exists("Sapphire Maintenance Profile"):
			for row in frappe.get_all(
				"Sapphire Maintenance Profile",
				filters={"project": ["in", names], "latitude": ["!=", 0], "longitude": ["!=", 0]},
				fields=["project", "latitude", "longitude"],
			):
				if row.project not in result and _valid(row.latitude, row.longitude):
					result[row.project] = _site(row.latitude, row.longitude, SOURCE_PROFILE, radius)
	except Exception:
		frappe.log_error(title="workforce.sites: maintenance profile lookup failed")

	remaining = [n for n in names if n not in result]
	if not remaining:
		return result

	# 2. The Project rows: linked Address + own coordinates, in one read.
	columns = _has_columns("Project", ("custom_customer__lead_address", *PROJECT_COORD_FIELDS))
	projects = []
	if columns:
		projects = frappe.get_all("Project", filters={"name": ["in", remaining]}, fields=["name", *columns])

	address_names = sorted({p.get("custom_customer__lead_address") for p in projects if p.get("custom_customer__lead_address")})
	addresses = {}
	if address_names and _has_columns("Address", ("custom_latitude", "custom_longitude")):
		for row in frappe.get_all(
			"Address",
			filters={"name": ["in", address_names]},
			fields=["name", "custom_latitude", "custom_longitude"],
		):
			if _valid(row.custom_latitude, row.custom_longitude):
				addresses[row.name] = row

	for project in projects:
		address = addresses.get(project.get("custom_customer__lead_address"))
		if address:
			result[project.name] = _site(address.custom_latitude, address.custom_longitude, SOURCE_ADDRESS, radius)
			continue
		lat, lng = project.get("custom_site_latitude"), project.get("custom_site_longitude")
		if _valid(lat, lng):
			source = project.get("custom_site_location_source") or SOURCE_MANUAL
			result[project.name] = _site(lat, lng, source, radius)

	return result


def _valid(lat, lng):
	lat, lng = flt(lat), flt(lng)
	return bool(lat or lng) and -90.0 <= lat <= 90.0 and -180.0 <= lng <= 180.0


def _site(lat, lng, source, radius):
	return {"lat": flt(lat), "lng": flt(lng), "source": source, "radius_m": radius}


# ------------------------------------------------------------------ geocoding


def project_address_text(project_row):
	"""The address string to geocode: the Project's own ``custom_project_address``, else
	the linked Address's ``custom_full_address``, else its address lines joined."""
	text = (project_row.get("custom_project_address") or "").strip()
	if text:
		return text
	address_name = project_row.get("custom_customer__lead_address")
	if not address_name:
		return ""
	fields = ["address_line1", "address_line2", "city", "state", "pincode", "country"]
	fields += _has_columns("Address", ("custom_full_address",))
	row = frappe.db.get_value("Address", address_name, fields, as_dict=True)
	if not row:
		return ""
	full = (row.get("custom_full_address") or "").strip()
	if full:
		return full
	parts = [row.get(f) for f in ("address_line1", "address_line2", "city", "state", "pincode", "country")]
	return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _geocoding_api_key():
	"""The key for THIS SERVER's own calls, falling back to the browser key.

	Two keys, because one cannot do both jobs. ``google_maps_api_key`` is handed to
	browsers and is therefore restricted by HTTP referrer — and Google refuses a
	referrer-restricted key on every server-side web-service call, answering
	``REQUEST_DENIED`` with "API keys with referer restrictions cannot be used with this
	API". That is not a misconfiguration to fix on the browser key: loosening its
	restriction so this call worked would publish an unrestricted key to every device
	that loads a map.

	So ``google_geocoding_api_key`` is a separate, IP-restricted key. The fallback to
	the browser key is deliberate: on a site that has not set the new key up, behaviour
	is exactly what it was before this field existed — geocoding fails with
	REQUEST_DENIED, logs once and walks away — rather than silently becoming a no-op
	that looks like "no address to geocode".
	"""
	try:
		# The server key is a PASSWORD field, so it lives in `__Auth` and NOT in
		# `tabSingles` -- `get_single_value` returns None for it, always, and would
		# silently fall through to the browser key and the REQUEST_DENIED this whole
		# mechanism exists to stop. It has to be read with get_password(). This app
		# has been bitten by that exact shape before: `Triton Settings.maps_api_key`
		# was stranded the same way (patches/rescue_renamed_doctype_auth_rows.py).
		#
		# raise_exception=False because a missing value is an ordinary state here --
		# the fallback below is the documented behaviour on a site that has not set
		# this up yet.
		settings = frappe.get_cached_doc("Travel Settings")
		server_key = (settings.get_password("google_geocoding_api_key", raise_exception=False) or "").strip()
		if server_key:
			return server_key
		# The browser key stays a plain Data field: it is handed to browsers by
		# design, so there is nothing to protect by encrypting it at rest.
		return (frappe.db.get_single_value("Travel Settings", "google_maps_api_key") or "").strip()
	except Exception:
		return ""


#: Back-compat alias. Kept because the name described where the key came from rather than
#: what it is for, and something may still import it.
_maps_api_key = _geocoding_api_key


def geocode_project(project, force=False):
	"""Geocode one Project's address into its site coordinate fields.

	No-op when coordinates exist (unless ``force``, which re-geocodes a ``Geocoded`` or
	blank-source project after its address changed — never a ``Manual`` one), when
	there is no address text, or when no API key is set. Logs and returns ``None`` on
	any failure; returns ``{"lat", "lng"}`` on success. **Never raises.**
	"""
	try:
		return _geocode_project(project, force=force)
	except Exception:
		frappe.log_error(title=f"workforce.sites: geocode_project {project}")
		return None


def _geocode_project(project, force=False):
	columns = _has_columns("Project", ("custom_project_address", "custom_customer__lead_address", *PROJECT_COORD_FIELDS))
	if "custom_site_latitude" not in columns:
		return None
	row = frappe.db.get_value("Project", project, ["name", *columns], as_dict=True)
	if not row:
		return None

	if _valid(row.get("custom_site_latitude"), row.get("custom_site_longitude")):
		if not force or (row.get("custom_site_location_source") or SOURCE_GEOCODED) == SOURCE_MANUAL:
			return {"lat": flt(row.custom_site_latitude), "lng": flt(row.custom_site_longitude)}

	address = project_address_text(row)
	if not address:
		return None
	if force and address == (row.get("custom_site_geocoded_from") or "") and _valid(
		row.get("custom_site_latitude"), row.get("custom_site_longitude")
	):
		return {"lat": flt(row.custom_site_latitude), "lng": flt(row.custom_site_longitude)}

	key = _geocoding_api_key()
	if not key:
		return None

	import requests

	response = requests.get(GEOCODE_URL, params={"address": address, "key": key}, timeout=GEOCODE_TIMEOUT_S)
	payload = response.json() if response.content else {}
	status = payload.get("status")
	if status != "OK" or not payload.get("results"):
		# ZERO_RESULTS is a fact about the address; REQUEST_DENIED is almost always the
		# referrer-restricted browser key. Either way, say so once and stop.
		frappe.log_error(
			title=f"workforce.sites: geocode {project} -> {status or response.status_code}",
			message=f"{address}\n{payload.get('error_message') or ''}",
		)
		return None

	location = payload["results"][0].get("geometry", {}).get("location", {})
	lat, lng = flt(location.get("lat")), flt(location.get("lng"))
	if not _valid(lat, lng):
		return None

	frappe.db.set_value(
		"Project",
		project,
		{
			"custom_site_latitude": lat,
			"custom_site_longitude": lng,
			"custom_site_location_source": SOURCE_GEOCODED,
			"custom_site_geocoded_from": address[:140],
		},
		update_modified=False,
	)
	return {"lat": lat, "lng": lng}


# ------------------------------------------------------------------ hooks


def on_project_update(doc, method=None):
	"""Project ``on_update`` doc_event: enqueue a geocode when the address text changed
	or the project has no coordinates yet. Enqueued, never inline — Google is a
	third-party call and a Project save must not wait on it — and after commit, so
	the worker reads the saved address rather than the previous one.
	"""
	if frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch:
		return
	try:
		address_now = _doc_address_text(doc)
		if not address_now:
			return
		before = doc.get_doc_before_save()
		changed = before is None or _doc_address_text(before) != address_now
		has_coords = _valid(getattr(doc, "custom_site_latitude", None), getattr(doc, "custom_site_longitude", None))
		if not changed and has_coords:
			return
		frappe.enqueue(
			"erpnext_enhancements.workforce.sites.geocode_project",
			queue="short",
			project=doc.name,
			force=bool(changed and has_coords),
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(title=f"workforce.sites: on_project_update {getattr(doc, 'name', '')}")


def _doc_address_text(doc):
	text = (getattr(doc, "custom_project_address", None) or "").strip()
	if text:
		return text
	return (getattr(doc, "custom_customer__lead_address", None) or "").strip()


def backfill_missing_site_coordinates(limit=BACKFILL_LIMIT):
	"""Daily scheduler job (and the patch's body): enqueue ``geocode_project`` for
	active projects with no coordinates and some address text, at most ``limit`` per
	run, on the ``long`` queue. Idempotent — a project that geocodes drops out of the
	next run's candidate list; one that cannot be geocoded is retried tomorrow, which is
	cheap and means a key fixed in the console needs no deploy to take effect.

	Returns the number enqueued.
	"""
	try:
		columns = _has_columns("Project", ("custom_project_address", "custom_customer__lead_address", "custom_site_latitude", "custom_site_longitude"))
		if "custom_site_latitude" not in columns:
			return 0
		if not _geocoding_api_key():
			return 0
		candidates = frappe.get_all(
			"Project",
			filters={"is_active": "Yes"},
			or_filters=[["custom_site_latitude", "is", "not set"], ["custom_site_latitude", "=", 0]],
			fields=["name", *columns],
			order_by="modified desc",
			limit=max(cint(limit), 1) * 5,
		)
		enqueued = 0
		for row in candidates:
			if _valid(row.get("custom_site_latitude"), row.get("custom_site_longitude")):
				continue
			if not (row.get("custom_project_address") or row.get("custom_customer__lead_address")):
				continue
			frappe.enqueue(
				"erpnext_enhancements.workforce.sites.geocode_project",
				queue="long",
				project=row.name,
				enqueue_after_commit=True,
			)
			enqueued += 1
			if enqueued >= cint(limit):
				break
		return enqueued
	except Exception:
		frappe.log_error(title="workforce.sites: backfill_missing_site_coordinates")
		return 0
