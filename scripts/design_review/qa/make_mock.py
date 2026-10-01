"""Turn a bundle into the mock server data the Review Room browser test reads (``mock.json``).

``content`` has the shape ``design_review.service.get_content`` returns: the bundle as
``importer.import_bundle`` stores it, every screen through the real sanitizer, plus the kit
stylesheet it names. ``state`` is ``get_review``'s shape for a participant who also moderates, on
an Open review with nothing recorded yet. So the test draws exactly what production would draw.

    python scripts/design_review/qa/make_mock.py <bundle.json>
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, REPO)
from erpnext_enhancements.design_review import sanitize

KITS = {"sapphire-ux/1": "sapphire_ux_1.css"}

if len(sys.argv) != 2:
	sys.exit(__doc__)
b = json.load(open(sys.argv[1], encoding="utf-8"))
kit_css = ""
if b.get("kit"):
	kit_css = open(os.path.join(REPO, "erpnext_enhancements", "design_review", "kit", KITS[b["kit"]]), encoding="utf-8").read()
content = {
	"format": b["format"],
	"title": b["title"],
	"description": b.get("description") or "",
	"source_url": b.get("source_url") or "",
	"kit": b.get("kit"),
	"stylesheet": sanitize.sanitize_css(b.get("stylesheet") or "")[0],
	"flow": b.get("flow") or {},
	"tracks": [
		{
			"id": t["id"],
			"label": t.get("label") or t["id"],
			"short": t.get("short") or "",
			"votable": t.get("votable", True) is not False,
			"blurb": t.get("blurb") or "",
			"groups": t.get("groups") or [],
		}
		for t in b["tracks"]
	],
	"options": [
		{"track": o["track"], "code": o["code"], "name": o["name"], "what": o.get("what") or "", "tradeoff": o.get("tradeoff") or ""}
		for o in b["options"]
	],
	"screens": [
		{
			"track": s["track"],
			"option": s["option"],
			"screen": s["screen"],
			"name": s.get("name") or s["screen"],
			"group": s.get("group") or "",
			"frame": s["frame"],
			"w": s["w"],
			"h": s["h"],
			"html": sanitize.sanitize_html(s["html"])[0],
		}
		for s in b["screens"]
	],
	"parts": b.get("parts") or {},
	"kit_css": kit_css,
}
state = {
	"name": "DR-2026-001",
	"title": b["title"],
	"status": "Open",
	"description": b.get("description"),
	"revision": 1,
	"content_hash": "mock",
	"has_content": True,
	"me": {"user": "pat@example.com", "full_name": "Pat", "participant": True, "moderator": True, "promoter": True},
	"participants": [{"user": "pat@example.com", "full_name": "Pat"}],
	"tallies": {},
	"my_votes": {},
	"verdicts": {"live": {}, "imported": {}, "mine": {}},
	"notes": [],
	"decisions": [],
}
out = os.path.join(HERE, "mock.json")
json.dump({"state": state, "content": content}, open(out, "w", encoding="utf-8"))
print(f"{len(content['screens'])} screens -> {out}")
