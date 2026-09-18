import os
import unittest
import json
import re

class TestGoogleMapsLoader(unittest.TestCase):
	def setUp(self):
		self.repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
		self.js_dir = os.path.join(self.repo_root, "erpnext_enhancements", "public", "js")

	def test_loader_exists_and_defines_interfaces(self):
		loader_path = os.path.join(self.js_dir, "global_enhancements", "google_maps_loader.js")
		self.assertTrue(os.path.exists(loader_path))
		with open(loader_path, "r", encoding="utf-8") as f:
			content = f.read()

		self.assertIn("window.EEGoogleMaps", content)
		self.assertIn("load:", content)
		self.assertIn("mapOptions:", content)
		self.assertIn("DARK_STYLES:", content)

	def test_only_one_loader_in_js_directory(self):
		"""Ensures google_maps_loader.js is the only file that injects the Maps API."""
		# Strip comments to avoid matching explanations naming the tokens
		def strip_comments(text):
			text = re.sub(r'/\*[\s\S]*?\*/', '', text)
			text = re.sub(r'//.*', '', text)
			return text

		for root, dirs, files in os.walk(self.js_dir):
			for file in files:
				if not file.endswith(".js"):
					continue
				if file == "google_maps_loader.js":
					continue

				path = os.path.join(root, file)
				with open(path, "r", encoding="utf-8") as f:
					content = strip_comments(f.read())
				
				self.assertNotIn("maps.googleapis.com/maps/api/js", content, f"Found legacy injection in {file}")
				self.assertNotIn("(g=>{", content.replace(" ", ""), f"Found inline bootstrap in {file}")

	def test_load_always_imports_the_core_maps_library(self):
		"""`load()` must import "maps" even when the caller requests no libraries.

		`bootstrap()` only DEFINES `google.maps.importLibrary` — the script itself is
		fetched by the first importLibrary() call and by nothing else. So a caller
		passing no `libraries` used to receive a `google.maps` carrying importLibrary
		and not one other symbol, and `new google.maps.Map(...)` threw. That is a
		silent, total failure of any map whose caller happened to request nothing:
		travel_trip_map.js does exactly that, and its agenda map went blank.

		The `<script src=...>` tag these call sites were originally written against
		populated the namespace on its own, so importing the core library is what
		preserves the contract they still assume.
		"""
		loader_path = os.path.join(self.js_dir, "global_enhancements", "google_maps_loader.js")
		with open(loader_path, "r", encoding="utf-8") as f:
			content = f.read()

		self.assertIn(
			'var libs = ["maps"]',
			content,
			'load() must seed its library list with the core "maps" library',
		)

	def test_every_call_site_survives_with_the_libraries_it_requests(self):
		"""Every google.maps.X a file uses must come from a library it actually loads.

		A file may only reach symbols from the libraries it passes to EEGoogleMaps.load
		plus any it importLibrary()s itself. "maps" is implicit (see the test above), so
		this mainly catches a file that uses Advanced Markers or Directions without
		asking for 'marker' / 'routes'.
		"""
		needs = {
			"AdvancedMarkerElement": "marker",
			"PinElement": "marker",
			"DirectionsService": "routes",
			"DirectionsRenderer": "routes",
			"Autocomplete": "places",
			"PlaceAutocompleteElement": "places",
			"spherical": "geometry",
		}

		def strip_comments(text):
			text = re.sub(r"/\*[\s\S]*?\*/", "", text)
			text = re.sub(r"//.*", "", text)
			return text

		for root, _dirs, files in os.walk(self.js_dir):
			for file in files:
				if not file.endswith(".js") or file == "google_maps_loader.js":
					continue
				path = os.path.join(root, file)
				with open(path, "r", encoding="utf-8") as f:
					body = strip_comments(f.read())
				if "EEGoogleMaps.load" not in body:
					continue

				declared = set(re.findall(r"['\"]([a-z]+)['\"]",
					" ".join(re.findall(r"libraries:\s*\[([^\]]*)\]", body))))
				declared |= set(re.findall(r"importLibrary\(\s*['\"]([a-z]+)['\"]", body))
				declared.add("maps")

				for symbol, lib in needs.items():
					if symbol in body:
						self.assertIn(
							lib,
							declared,
							f"{file} uses google.maps.{symbol} but never loads the "
							f"'{lib}' library — that symbol is undefined at runtime",
						)

	def test_mapOptions_never_emits_both_mapId_and_styles(self):
		"""Ensures mapId and styles are never emitted together.
		
		The Maps API logs a console error and ignores styles whenever a mapId is present.
		Callers spread the return straight into a google.maps.MapOptions literal.
		"""
		loader_path = os.path.join(self.js_dir, "global_enhancements", "google_maps_loader.js")
		with open(loader_path, "r", encoding="utf-8") as f:
			content = f.read()

		# Simple textual check: mapId branch returns only mapId, else branch returns only styles.
		self.assertRegex(content, r'return\s*\{\s*mapId:\s*mapId\s*\}')
		self.assertRegex(content, r'return\s*\{\s*styles:.*\}')
		self.assertNotIn("return { mapId: mapId, styles:", content)

	def test_travel_settings_new_fields(self):
		"""travel_settings.json must declare both new Map ID fields with NO default.
		
		A default on a new field of a Single doctype never reaches the row that already exists.
		Blank is the correct and intended value on the existing production row, so no backfill
		patch is needed.
		"""
		path = os.path.join(self.repo_root, "erpnext_enhancements", "travel_management", "doctype", "travel_settings", "travel_settings.json")
		with open(path, "r", encoding="utf-8") as f:
			data = json.load(f)
			
		fields = {f["fieldname"]: f for f in data["fields"]}
		
		self.assertIn("google_maps_map_id_light", fields)
		self.assertIn("google_maps_map_id_dark", fields)
		
		self.assertTrue(fields["google_maps_map_id_light"].get("description"))
		self.assertTrue(fields["google_maps_map_id_dark"].get("description"))
		
		self.assertNotIn("default", fields["google_maps_map_id_light"])
		self.assertNotIn("default", fields["google_maps_map_id_dark"])

	def test_travel_api_definitions(self):
		path = os.path.join(self.repo_root, "erpnext_enhancements", "api", "travel.py")
		with open(path, "r", encoding="utf-8") as f:
			content = f.read()
			
		self.assertIn("def get_maps_api_key():", content)
		self.assertIn("return _maps_api_key()", content)
		
		self.assertIn("def get_maps_config():", content)
		self.assertIn("google_maps_map_id_light", content)

	def test_bundle_imports_loader(self):
		path = os.path.join(self.js_dir, "erpnext_enhancements.bundle.js")
		with open(path, "r", encoding="utf-8") as f:
			content = f.read()
			
		self.assertIn("global_enhancements/google_maps_loader.js", content)
