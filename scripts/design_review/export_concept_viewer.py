"""Export a claude.ai Concept Viewer review as a ``sapphire-design-review/1`` bundle (WI-079 slice 5).

The September 2026 Training review was drawn by a Python generator (``vdata.py``, ``build.py``,
``flows*.py``, ``xmap.py``, ``entry_variants.py``) with its element numbering frozen in
``codes.json`` and its click-through rules in ``viewer_script.js``. This reads all three and writes
one bundle that the Review Room imports (``design_review/importer.py`` documents the format).

    python scripts/design_review/export_concept_viewer.py <generator dir> <out.json>

``codes.json`` is only read: its numbering is the review's element codes, and the importer refuses
any revision that would change one. Needs ``node`` on PATH for ``flow_extract.js``.
"""

import json
import os
import subprocess
import sys

if len(sys.argv) != 3:
	sys.exit(__doc__)
GEN = os.path.abspath(sys.argv[1])
OUT = os.path.abspath(sys.argv[2])
HERE = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, GEN)
cwd = os.getcwd()
os.chdir(GEN)
import build
import vdata

tracks, frames, docs = vdata.collect()
frames = vdata.normalise_frames(frames)
codes = json.load(open(os.path.join(GEN, "codes.json"), encoding="utf-8"))
os.chdir(cwd)

flow = json.loads(
	subprocess.run(
		["node", os.path.join(HERE, "flow_extract.js"), os.path.join(GEN, "viewer_script.js")],
		capture_output=True,
		text=True,
		check=True,
	).stdout
)
# The old viewer's hard-coded special cases, as data the Review Room reads.
flow["icon"] = [["^Search", "S02", "learner"]] + flow["icon"]
flow["aria"] = [["^Play video$", "done:Plays the lesson video. Watching 80% unlocks the quiz", "learner"]]
flow["disabled"] = {
	"canvas": {"Readiness bar": "done:PUBLISH stays locked until the 2 things are fixed. Tap See them."}
}
flow["dom"] = {
	"icon": ".ux-iconbtn",
	"step": ".ux-step",
	"step_label": "span:not(.ux-stepnum):not(.ux-badge)",
	"noinherit": flow.pop("noinherit"),
	"inert": ".ux-app-bg",
	"bars": ["Primary action", "Primary actions", "Step actions"],
}

DIMS = {"desk": (1280, 800), "phone": (390, 844)}
out_tracks, options, screens = [], [], []
for t in tracks:
	group_of = {}
	for g in t["groups"]:
		for sid in g["screens"]:
			group_of.setdefault(sid, g["name"])
	out_tracks.append(
		{
			"id": t["id"],
			"label": t["label"],
			"short": t["short"],
			"votable": True,
			"blurb": t["blurb"],
			"groups": t["groups"],
		}
	)
	for c in t["concepts"]:
		options.append(
			{
				"track": t["id"],
				"code": c["id"],
				"name": c["name"],
				"what": c["what"],
				"tradeoff": c["tradeoff"],
			}
		)
		for s in t["screens"]:
			for fr in s["frames"]:
				html = frames.get(f"{c['id']}-{s['id']}-{fr}")
				if html is None:
					continue
				w, h = DIMS[fr]
				screens.append(
					{
						"track": t["id"],
						"option": c["id"],
						"screen": s["id"],
						"name": s["name"],
						"group": group_of.get(s["id"], ""),
						"frame": fr,
						"w": w,
						"h": h,
						"html": html,
					}
				)

# The experience-map boards: a guide track with one pseudo-option, not ranked.
out_tracks.insert(
	0,
	{
		"id": "guide",
		"label": "Read me first",
		"short": "GUIDE",
		"votable": False,
		"blurb": "The experience maps and touchpoint journeys behind every option. Read them first; notes are welcome, there is nothing to rank.",
		"groups": [{"name": "Experience map", "screens": [d["id"] for d in docs]}],
	},
)
options.insert(
	0,
	{
		"track": "guide",
		"code": "G",
		"name": "Experience maps",
		"what": "The method, two experience maps and two touchpoint journeys.",
		"tradeoff": "",
	},
)
for d in docs:
	screens.append(
		{
			"track": "guide",
			"option": "G",
			"screen": d["id"],
			"name": d["name"],
			"group": "Experience map",
			"frame": "board",
			"w": d["w"],
			"h": d["h"],
			"html": d["html"],
		}
	)

parts = {("guide:" + key[4:] if key.startswith("doc:") else key): rows for key, rows in codes.items()}

bundle = {
	"format": "sapphire-design-review/1",
	"title": "Training UX Redesign",
	"description": "Five learner layouts, five authoring layouts and ten ways in, for the Training module. "
	"Rank each track and pin notes to parts of any screen.",
	"source_url": "https://claude.ai/artifact/HampNeHhPEPKYVVN2FMvFb",
	# build.CSS is the house kit verbatim (design_review/kit/sapphire_ux_1.css was cut from it), so the
	# bundle names the kit instead of carrying 11 KB of it in every revision.
	"kit": "sapphire-ux/1",
	"stylesheet": "",
	"flow": flow,
	"tracks": out_tracks,
	"options": options,
	"screens": screens,
	"parts": parts,
}
json.dump(bundle, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
print(
	f"{len(out_tracks)} tracks, {len(options)} options, {len(screens)} screens, "
	f"{sum(len(r) for r in parts.values())} parts -> {OUT} ({os.path.getsize(OUT)} bytes)"
)
