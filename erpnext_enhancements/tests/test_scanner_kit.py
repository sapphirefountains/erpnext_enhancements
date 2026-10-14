"""The scanner kit (S6b, v1.666.0, TASK-2026-02808): one camera scanner for the Desk pages.

The Device Console and the Inventory Scanner Audit each carried their own copy of the camera sheet
and of the history handling that makes the phone's Back button close a sheet. The Device Console's
copy had no jsQR fallback, so it could not scan in iPhone Safari, which has no BarcodeDetector.
Both now load ``public/js/scanner_kit/`` through ``scanner_kit.bundle.js`` (``window.sf_scanner``).

What this pins, statically (comments stripped, so an explanation cannot satisfy an absence check):

* **Neither page keeps a camera loop of its own**: no getUserMedia, no detector, no jsQR, no
  frame drawing, no track handling, no history writes. A third copy is how the iPhone gap opened.
* **The pages load the kit behind a guard**: the kit already on the page answers at once, and
  otherwise one ``frappe.require(['scanner_kit.bundle.js'])``; never ``app_include_js``, and never
  a raw /assets path (served immutable for a year with no hash).
* **jsQR stays the vendored raw file**: the kit asks frappe.require for its bare path, only when
  BarcodeDetector cannot read QR, and no module imports it (a UMD file in the bundle would capture
  its export, and cost every Android phone 130 KB it never runs). Nothing is copied out of
  /stock-scan's own scanner, which another page owns.
* **iOS**: an inline, muted, autoplaying video (``muted`` set as a property too); the back camera
  with no audio; every track stopped on close, on visibilitychange, on pagehide, and on frappe's
  "hide" of the page's wrapper.

The behaviour runs in ``scripts/test_scanner_kit.mjs`` (the kit alone, against stubbed
mediaDevices, BarcodeDetector present / absent / without QR, and jsQR), run from here, and in
``scripts/test_desk_page_history.js`` (both pages on the real kit), run from test_desk_page_history.
Bench-free: reads files, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_scanner_kit -v
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
JS = APP / "public" / "js"
KIT = JS / "scanner_kit"
BUNDLE = JS / "scanner_kit.bundle.js"
JSQR = JS / "stock_scan" / "lib" / "jsQR-1.4.0.min.js"
HARNESS = REPO / "scripts" / "test_scanner_kit.mjs"
HOOKS = APP / "hooks.py"
CI = REPO / ".github" / "workflows" / "ci.yml"

PAGES = {
	"device-console": APP / "device_management/page/device_console/device_console.js",
	"inventory-scanner-audit": APP
	/ "inventory_enhancements/page/inventory_scanner_audit/inventory_scanner_audit.js",
}
PREFIX = {"device-console": "dc", "inventory-scanner-audit": "isa"}
MODULES = ("env", "sheets", "reader", "camera", "index")

# What a camera loop, a decoder or a sheet's history handling is made of. None of it may appear in
# a page: each is the kit's.
CAMERA_LOOP = (
	"getUserMedia",
	"mediaDevices",
	"BarcodeDetector",
	"jsQR",
	"stock_scan/lib",
	".detect(",
	"getTracks",
	"srcObject",
	"drawImage",
	"getImageData",
	"facingMode",
	"visibilitychange",
	"<video",
	"history.replaceState",
	"history.back(",
	"replace_route",
)


def _text(path):
	return path.read_text(encoding="utf-8")


def _code(path):
	"""The script with comments stripped: an absence assertion must not trip on its own explanation."""
	src = re.sub(r"/\*[\s\S]*?\*/", "", _text(path))
	return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


def _between(src, start, end):
	at = src.index(start)
	return src[at : src.index(end, at + len(start))]


@unittest.skipUnless(shutil.which("node"), "node is not on PATH")
class TestTheKitHarness(unittest.TestCase):
	def test_the_scanner_kit_harness_passes(self):
		result = subprocess.run(
			[shutil.which("node"), str(HARNESS)], capture_output=True, text=True, timeout=300
		)
		output = (result.stdout + result.stderr)[-6000:]
		self.assertEqual(result.returncode, 0, output)
		self.assertIn("checks passed", result.stdout, output)


class TestTheKitIsInPlace(unittest.TestCase):
	def test_every_module_and_the_bundle(self):
		for name in MODULES:
			with self.subTest(module=name):
				self.assertTrue((KIT / f"{name}.js").is_file())
		bundle = _code(BUNDLE)
		self.assertIn('import { install } from "./scanner_kit/index.js";', bundle)
		self.assertIn("install(window)", bundle)

	def test_loaded_only_by_the_pages_that_use_it(self):
		"""Never a global include: the Desk pages that scan ask for it."""
		hooks = _text(HOOKS)
		self.assertNotIn("scanner_kit", hooks)
		self.assertNotIn("scanner_kit", _text(JS / "erpnext_enhancements.bundle.js"))

	def test_registered_in_ci(self):
		ci = _text(CI)
		for step in (
			"node scripts/test_scanner_kit.mjs",
			"python -m unittest erpnext_enhancements.tests.test_scanner_kit -v",
		):
			with self.subTest(step=step):
				self.assertTrue(step in ci, f"ci.yml does not run: {step}")


class TestNeitherPageKeepsItsOwnCameraLoop(unittest.TestCase):
	def test_no_camera_loop_decoder_or_history_write_in_a_page(self):
		for name, path in PAGES.items():
			code = _code(path)
			for token in CAMERA_LOOP:
				with self.subTest(page=name, token=token):
					self.assertNotIn(token, code)

	def test_each_page_hands_its_camera_to_the_kit(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertIn("this.kit.camera", code)
				self.assertIn(".prepare({", _between(code, "openCamera(reopen) {", "showCamera(reader) {"))
				self.assertIn("this.kit.camera.show({", _between(code, "showCamera(reader) {", "\n\t}"))
				self.assertIn(f"video_class: '{PREFIX[name]}-video',", code)
				self.assertIn("page: this.wrapper,", code)

	def test_the_camera_button_follows_the_secure_context_not_barcode_detector(self):
		"""BarcodeDetector decided the console's camera button, so it never appeared on an iPhone."""
		console = _code(PAGES["device-console"])
		self.assertIn(
			"return this.state.camera && !!this.kit && this.kit.camera.available();",
			_between(console, "cameraOn() {", "\n\t}"),
		)
		audit = _code(PAGES["inventory-scanner-audit"])
		self.assertIn(
			"return !!this.kit && this.kit.camera.available();",
			_between(audit, "cameraAvailable() {", "\n\t}"),
		)

	def test_the_camera_buttons_keep_their_names(self):
		"""S3.6: a glyph-only button carries an aria-label."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				self.assertIn(
					f'<button class="{PREFIX[name]}-cam" title="${{__(\'Camera scan\')}}" aria-label="${{__(\'Camera scan\')}}">📷</button>',
					_code(path),
				)


class TestThePagesLoadTheKitBehindAGuard(unittest.TestCase):
	def test_the_kit_on_the_page_answers_at_once_else_one_require(self):
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				loader = _between(code, f"function {PREFIX[name]}_scanner(then) {{", "\n}")
				self.assertIn(
					"window.sf_scanner && window.sf_scanner.version >= 1 ? window.sf_scanner : null", loader
				)
				self.assertIn("if (kit()) return then(kit());", loader)
				self.assertIn("frappe.require(['scanner_kit.bundle.js'], () => then(kit()));", loader)
				# A require that throws still answers, with no kit.
				self.assertIn("then(null);", loader)
				self.assertEqual(code.count("scanner_kit.bundle.js"), 1)
				self.assertNotIn("/assets/erpnext_enhancements/js/scanner_kit", code)

	def test_without_the_kit_the_page_falls_back_to_plain_sheets(self):
		"""A file that failed to load: the page still scans, picks and counts, its sheets plain
		dialogs with no entry of their own, and its camera button hidden."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				code = _code(path)
				self.assertIn(f"this.nav = {PREFIX[name]}_bare_sheets();", code)
				bare = _between(code, f"function {PREFIX[name]}_bare_sheets() {{", "\n}")
				self.assertIn("current: () => null,", bare)
				self.assertNotIn("set_route", bare)
				self.assertNotIn("history", bare)

	def test_the_start_up_never_waits_for_the_scanner_kit(self):
		"""The S3.1 loading / error panel is sf_kit's, and a scanner kit that is slow or missing
		must not hold it up."""
		for name, path in PAGES.items():
			with self.subTest(page=name):
				boot = _between(_code(path), "\tboot() {", "\n\t}")
				self.assertNotIn("scanner", boot.lower())


class TestJsqrStaysTheVendoredRawFile(unittest.TestCase):
	def test_loaded_by_its_bare_path_only_on_need(self):
		reader = _code(KIT / "reader.js")
		self.assertIn(
			'export const QR_DECODER = "/assets/erpnext_enhancements/js/stock_scan/lib/jsQR-1.4.0.min.js";',
			reader,
		)
		self.assertTrue(JSQR.is_file())
		self.assertIn("env.frappe.require(QR_DECODER)", reader)
		# Only after the detector has been found not to read QR.
		create = _between(reader, "export function create_reader(env) {", "\n}")
		self.assertLess(
			create.index('if (native && formats.includes("qr_code")) return { mode: "native"'),
			create.index("load_jsqr(env)"),
		)

	def test_no_module_imports_jsqr_or_stock_scan_code(self):
		for path in [*KIT.glob("*.js"), BUNDLE]:
			with self.subTest(module=path.name):
				imports = re.findall(r"^\s*import\b[^;]*;", _code(path), flags=re.M)
				for line in imports:
					self.assertNotIn("jsQR", line)
					self.assertNotIn("stock_scan", line)


class TestTheCameraOnAnIphone(unittest.TestCase):
	def setUp(self):
		self.camera = _code(KIT / "camera.js")

	def test_an_inline_muted_video(self):
		self.assertIn("playsinline muted autoplay></video>", self.camera)
		self.assertIn("video.muted = true;", self.camera)
		self.assertIn("if (playing && playing.catch) playing.catch(() => {});", self.camera)

	def test_the_back_camera_and_no_audio(self):
		self.assertIn('getUserMedia({ video: { facingMode: "environment" }, audio: false })', self.camera)

	def test_every_track_stops_on_every_way_out(self):
		stop = _between(self.camera, "const stop = () => {", "\n\t};")
		self.assertIn("stop_tracks(stream);", stop)
		self.assertIn("track.stop();", _between(self.camera, "const stop_tracks = (s) => {", "\n\t};"))
		self.assertIn('env.document.addEventListener("visibilitychange", on_visibility);', self.camera)
		self.assertIn('env.listen("pagehide", on_pagehide);', self.camera)
		self.assertIn("env.$(page).on(ns, (e) => {", self.camera)
		onhide = _between(self.camera, "d.onhide = () => {", "\n\t};")
		self.assertLess(onhide.index("stop();"), onhide.index("opts.on_close"))
		# A stream that arrives after the sheet closed.
		self.assertIn("stop_tracks(s);", self.camera)


if __name__ == "__main__":
	unittest.main()
