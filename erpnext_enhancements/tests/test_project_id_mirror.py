"""The Project form must not open as "Not Saved" on account of the Project ID mirror (v1.816.2).

``public/js/project.js`` shows the saved Project's name in the read-only ``custom_project_id``
field on every refresh. It used to do that with ``frm.set_value``, which dirties the form whenever
the stored value differs (empty on older Projects, stale after a rename): the form opened as "Not
Saved" with nothing edited, and reloading or leaving the page asked "Leave site? There are unsaved
changes".

* ``scripts/test_project_id_mirror.js`` runs the real script against a stubbed Desk and asserts the
  form stays clean (and that a real edit still marks it changed).
* The source check below is the cheap second line: no ``set_value`` on that field anywhere in the
  Project form scripts that run on their own.

Bench-free: reads files and runs node, no ``frappe`` stub.

Run: python -m unittest erpnext_enhancements.tests.test_project_id_mirror
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
SCRIPT = APP / "public" / "js" / "project.js"
HARNESS = REPO / "scripts" / "test_project_id_mirror.js"


def _strip_comments(src):
	src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
	return re.sub(r"(?m)//.*$", "", src)


class TestProjectIdMirrorDoesNotDirtyTheForm(unittest.TestCase):
	def test_no_set_value_on_the_field(self):
		code = _strip_comments(SCRIPT.read_text(encoding="utf-8"))
		self.assertNotRegex(
			code,
			r"set_value\s*\(\s*['\"]custom_project_id['\"]",
			"frm.set_value / frappe.model.set_value dirties the form on every open",
		)

	def test_executed_under_node(self):
		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run([node, str(HARNESS)], capture_output=True, text=True, timeout=60)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
	unittest.main()
