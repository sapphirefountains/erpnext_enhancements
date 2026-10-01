"""Turn a bundle into the mock server data the Review Room browser test reads (``mock.json``).

The shape is ``design_review.service.get_review``'s, and every screen goes through the real
sanitizer, so the test draws what production would draw.

    python scripts/design_review/qa/make_mock.py <bundle.json>
"""

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))
from erpnext_enhancements.design_review import sanitize

if len(sys.argv) != 2:
	sys.exit(__doc__)
b = json.load(open(sys.argv[1], encoding="utf-8"))
screens, html = [], {}
for i, s in enumerate(b["screens"]):
	name = hashlib.sha1(f"{s.get('option')}|{s['screen']}|{s['frame']}".encode()).hexdigest()[:10]
	html[name] = sanitize.sanitize_html(s["html"])[0]
	screens.append(
		{
			"name": name,
			"track": s["track"],
			"option_code": s.get("option") or "",
			"screen_code": s["screen"],
			"screen_name": s.get("name") or s["screen"],
			"group_name": s.get("group") or "",
			"frame": s["frame"],
			"width": s["w"],
			"height": s["h"],
			"sort_order": i,
		}
	)
review = {
	"name": "DR-2026-001",
	"title": b["title"],
	"status": "Open",
	"description": b.get("description"),
	"revision": 1,
	"source_url": b.get("source_url"),
	"stylesheet": sanitize.sanitize_css(b.get("stylesheet") or "")[0],
	"flow": b.get("flow") or {},
	"tracks": [
		{
			"id": t["id"],
			"label": t["label"],
			"short": t.get("short"),
			"votable": 1 if t.get("votable", True) else 0,
			"blurb": t.get("blurb"),
			"groups": t.get("groups") or [],
		}
		for t in b["tracks"]
	],
	"options": [
		{
			"option_code": o["code"],
			"option_name": o["name"],
			"track": o["track"],
			"what": o.get("what"),
			"tradeoff": o.get("tradeoff"),
			"sort_order": i,
		}
		for i, o in enumerate(b["options"])
	],
	"screens": screens,
	"parts": b["parts"],
	"me": {"user": "pat@example.com", "participant": True, "moderator": True, "promoter": True},
	"participants": [{"user": "pat@example.com", "full_name": "Pat"}],
	"tallies": {},
	"my_votes": {},
	"verdicts": {"live": {}, "imported": {}, "mine": {}},
	"notes": [],
	"decisions": [],
	"voter_names": {},
}
out = os.path.join(HERE, "mock.json")
json.dump({"review": review, "html": html}, open(out, "w", encoding="utf-8"))
print(f"{len(screens)} screens -> {out}")
