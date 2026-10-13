#!/usr/bin/env python3
"""Derive the Project Planner's seed templates (T3, v1.608.0) from DOC-0041 Sapphire Cost Worksheet.

Writes ``erpnext_enhancements/project_enhancements/data/planner_templates.json``: the DOC-0041 phase
template (phases 100-700) and the six starter task bundles. The patch
``seed_planner_templates`` reads that JSON at migrate time; nothing parses a spreadsheet on a
server. Re-run this when DOC-0041 changes, review the diff, and commit the JSON:

    python -P scripts/derive_planner_templates.py "<path to the DOC-0041 .xlsx>"

(``-P`` keeps openpyxl in the user site-packages visible; the workbook is read with
``data_only=True`` and only cell VALUES are used: no formula, macro or link in it is evaluated.)

What it takes from the workbook, and the rules (Nik, 2026-10-12):

* The **Budget Sheet** rows between each ``NNN PHASE: ...`` heading. Phase 800 Maintenance is left
  out: maintenance contracts own it.
* Each line's DOC-0041 cost code is **never copied**. It is mapped through
  ``quality/cost_codes.OLD_TO_NEW`` (the corrected catalog, WI-077), except the two lines in
  :data:`CODE_OVERRIDES`, whose DOC-0041 code was wrong in the OLD catalog too.
* **Tasks**: every labor line (the corrected code's ``cost_type`` is Labor), and the subcontracted
  scope Nik named (demolition, earthwork, concrete, waterproofing, tile, stone, paint, electrical),
  listed by row in :data:`SUBCONTRACT_TASK_ROWS`. The Project Management line becomes ONE PM task per
  phase. A labor line's hours are its "# Units", unless every labor line reads 1 (the blank
  worksheet's placeholder, as in v1.0): then all are left blank. A subcontract task has none.
* **Everything else** (materials, equipment, expenses, subcontracts not on that list) goes on the
  phase's **procurement checklist**, never on the calendar.
* Within a phase the tasks run in order (each waits on the previous), PM aside; the order is the
  sheet's, except the phases in :data:`TASK_ORDER`. Phases run 100 > 200 > 300 > 400, then 500 and
  600 side by side, then 700 (:data:`RUNS_AFTER`).

DOC-0041 v1.0 defects deliberately NOT copied: "Merchanical" (phase 500 is the Mechanical Room); row
92 repeating "Exterior Fountains" under 13 1223, which is Interior Fountains; row 52 having no "#
Units" (a material, so it carries no hours anyway).

The six starter bundles are not in DOC-0041: they are Nik's list of 2026-10-12, drafted for him to
correct, so they are seeded "Needs review" with blank hours.
"""

import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "erpnext_enhancements" / "project_enhancements" / "data" / "planner_templates.json"
CATALOG = REPO / "erpnext_enhancements" / "quality" / "cost_codes.py"

PHASE_TEMPLATE = "Fountain build phases (DOC-0041)"
EXCLUDED_PHASES = (800,)

#: Phase titles: DOC-0041's, without the nicknames in brackets, and with "Merchanical" fixed.
PHASE_TITLES = {
	100: "Full System Overview",
	200: "Groundwork & Prep",
	300: "Rough-in",
	400: "Structural Vessel",
	500: "Mechanical Room",
	600: "Controls & Electrical Integration",
	700: "Above Ground Features & Finishes",
}

#: What each phase waits on. 500 and 600 run side by side; 700 waits for both.
RUNS_AFTER = {100: [], 200: [100], 300: [200], 400: [300], 500: [400], 600: [400], 700: [500, 600]}

#: Subcontracted scope that becomes a task (Nik: demolition, earthwork, concrete, waterproofing,
#: tile, stone, paint, electrical). Rows of the Budget Sheet, with the word that put them here.
SUBCONTRACT_TASK_ROWS = {
	21: "demolition",
	22: "earthwork",
	42: "concrete",
	44: "waterproofing",
	68: "electrical",
	73: "electrical",
	83: "earthwork",
	85: "stone",
	86: "waterproofing",
	87: "tile",
	89: "paint",
}

#: Lines whose DOC-0041 code names the wrong thing even in the OLD catalog, so OLD_TO_NEW would carry
#: the mistake forward. Row: (corrected code, why).
CODE_OVERRIDES = {
	53: (
		"22 1119",
		"Relocate Existing Hose Bib is coded 22 1150 (Manifold Materials) in DOC-0041; the catalog's "
		"own hose-bib line is old 22 1160, now 22 1119.",
	),
	76: (
		"26 0900.16",
		"Programming Labor is coded 01 0120 (Superintendent) in DOC-0041; the catalog's line for it "
		"is 26 0900.16 Control Panel Build and Programming.",
	),
}

#: Descriptions corrected from DOC-0041 v1.0. Row: (corrected text, why).
DESCRIPTION_FIXES = {
	92: (
		"Interior Fountains",
		"DOC-0041 repeats 'Exterior Fountains' under 13 1223, which is Interior Fountains.",
	),
}

#: Build order inside a phase, where the sheet's own order is not one (by Budget Sheet row).
TASK_ORDER = {
	# The panel is built and programmed in the shop before the field electrical is terminated.
	600: [77, 76, 68, 73, 79],
	# Waterproof the vessel before stone and tile go on it; commissioning and the owner hand-off last.
	700: [83, 86, 85, 87, 89, 98, 90, 93],
}

PM_CODE = "LAB-PM"
LINE_LABOR = "Labor"
LINE_SUBCONTRACT = "Subcontract"
LINE_PM = "Project management"

BUNDLES = [
	(
		"Bundle: Control panel design + build",
		[
			"Panel design (schematic + BOM)",
			"Order components",
			"Shop build",
			"Bench test",
			"Program controller",
			"Install + terminate on site",
			"Commission + tune",
		],
	),
	(
		"Bundle: Interior fountain",
		[
			"Shop drawings",
			"Order or fabricate unit",
			"Rough-in (supply, drain, power)",
			"Set + level",
			"Connect + fill",
			"Commission",
			"Owner training",
		],
	),
	(
		"Bundle: Splash pad",
		[
			"Layout + stake",
			"Rough-in plumbing + drains",
			"Set nozzles and toys",
			"Controls + activation",
			"Water test + commission",
			"Owner training",
		],
	),
	(
		"Bundle: Lighting package",
		[
			"Fixture schedule",
			"Order fixtures",
			"Install fixtures + conduit",
			"Terminate + test",
			"Program scenes",
		],
	),
	(
		"Bundle: Equipment room (pump + filter skid)",
		[
			"Layout drawing",
			"Order equipment",
			"Set equipment",
			"Pipe + valves",
			"Electrical tie-in",
			"Start-up + test",
		],
	),
	(
		"Bundle: Commissioning + hand-off",
		[
			"Pre-commission checklist",
			"Start-up",
			"Water balance + chemistry",
			"Punch list",
			"Owner training",
			"O&Ms + warranty",
		],
	),
]

PHASE_NOTES = (
	"Seeded from DOC-0041 Sapphire Cost Worksheet v1.0 (scripts/derive_planner_templates.py). Labor "
	"lines and the subcontracted scope are tasks, one Project management task per phase; materials, "
	"equipment and expenses are each phase's procurement checklist. Cost codes are the corrected "
	"catalog's, never DOC-0041's. Every labor line's '# Units' in v1.0 is 1 (a placeholder), so the "
	"hours are blank: fill them in per template. Phase 800 Maintenance is left out."
)
#: "# Units" on every labor line of a worksheet nobody has priced yet. When ALL of them read this, they
#: are the template's placeholder, not hours, and the seed leaves every default blank.
PLACEHOLDER_UNITS = 1.0
BUNDLE_NOTES = (
	"A starter bundle drafted from Nik's list (2026-10-12) for him to correct: hours are blank and "
	"the tasks run in order. Set Review status to Reviewed once it is right."
)


def load_catalog():
	spec = importlib.util.spec_from_file_location("cost_codes", CATALOG)
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def clean(text):
	return re.sub(r"\s+", " ", str(text or "")).strip()


def read_sheet(path):
	import openpyxl

	sheet = openpyxl.load_workbook(path, data_only=True, read_only=True)["Budget Sheet"]
	phases, current = [], None
	for number, row in enumerate(sheet.iter_rows(min_row=1, max_col=11, values_only=True), start=1):
		block, code, description, units = row[1], row[2], row[3], row[10]
		heading = re.match(r"^\s*(\d{3})\s+PHASE:", str(code or ""))
		if heading:
			current = {"number": int(heading.group(1)), "heading": clean(code), "lines": []}
			phases.append(current)
			continue
		if current is None or not clean(description) or not clean(code):
			continue
		current["lines"].append(
			{
				"row": number,
				"block": clean(block),
				"code": clean(code),
				"description": clean(description),
				"units": units,
			}
		)
	return phases


def derive(path):
	catalog = load_catalog()
	by_code = {row["cost_code"]: row for row in catalog.COST_CODES}
	by_code.update({row["resource_code"]: row for row in catalog.RESOURCE_CODES})
	interpretations = []
	phases = []
	for phase in read_sheet(path):
		number = phase["number"]
		if number in EXCLUDED_PHASES:
			interpretations.append(
				f"Phase {number} ({phase['heading']}) left out: maintenance contracts own it."
			)
			continue
		title = f"{number} {PHASE_TITLES[number]}"
		tasks, checklist, pm = [], [], None
		for line in phase["lines"]:
			row = line["row"]
			description = line["description"]
			if row in DESCRIPTION_FIXES:
				description, why = DESCRIPTION_FIXES[row]
				interpretations.append(f"Row {row}: {why}")
			if row in CODE_OVERRIDES:
				code, why = CODE_OVERRIDES[row]
				interpretations.append(f"Row {row}: {why}")
			else:
				code = catalog.OLD_TO_NEW[line["code"]]
			entry = by_code[code]
			units = line["units"]
			if code == PM_CODE:
				pm = {"row": row, "code": code, "units": units}
				continue
			if entry["cost_type"] == "Labor" or row in SUBCONTRACT_TASK_ROWS:
				labor = entry["cost_type"] == "Labor"
				tasks.append(
					{
						"row": row,
						"subject": description,
						"hours": float(units)
						if labor and isinstance(units, int | float) and units > 0
						else None,
						"line": LINE_LABOR if labor else LINE_SUBCONTRACT,
						"cost_code": code,
					}
				)
				continue
			kind = {"Material": "Material", "Equipment": "Equipment", "Expense": "Expense"}.get(
				entry["cost_type"], "Subcontract"
			)
			checklist.append(f"{kind}: {description} ({code})")
		order = TASK_ORDER.get(number)
		if order:
			place = {row: index for index, row in enumerate(order)}
			tasks.sort(key=lambda task: place[task["row"]])
		out_tasks = []
		previous = None
		for task in tasks:
			out_tasks.append(
				{
					"subject": task["subject"],
					"hours": task["hours"],
					"line": task["line"],
					"cost_code": task["cost_code"],
					"waits_on": [previous] if previous else [],
					"source_row": task["row"],
				}
			)
			previous = task["subject"]
		if pm:
			out_tasks.append(
				{
					"subject": f"Project management ({title})",
					"hours": float(pm["units"])
					if isinstance(pm["units"], int | float) and pm["units"] > 0
					else None,
					"line": LINE_PM,
					"cost_code": PM_CODE,
					"waits_on": [],
					"source_row": pm["row"],
				}
			)
		phases.append(
			{
				"number": number,
				"title": title,
				"runs_after": RUNS_AFTER[number],
				"checklist": checklist,
				"tasks": out_tasks,
			}
		)
	# A uniform 1 in every labor line's "# Units" is the blank worksheet's placeholder, not an estimate:
	# a 1-hour default would book every templated task as a 1-hour job. Blank shows what still needs one.
	labor = [task for phase in phases for task in phase["tasks"] if task["line"] in (LINE_LABOR, LINE_PM)]
	if labor and all(task["hours"] == PLACEHOLDER_UNITS for task in labor):
		for task in labor:
			task["hours"] = None
		interpretations.append(
			"Every labor line's '# Units' is 1, the blank worksheet's placeholder, so every task's default "
			"hours are left blank: fill them in per template."
		)
	interpretations.append("Phase 500's 'Merchanical' is spelled 'Mechanical'.")
	interpretations.append(
		"Row 52 (Check Valves) has no '# Units'; it is a material, so it carries no hours."
	)
	interpretations.append(
		"Stone (row 85) and the electrical rows 68 and 73 are Material in the catalog but are subcontracted "
		"scope by Nik's decision, so they are tasks."
	)
	interpretations.append(
		"Phase titles drop DOC-0041's nicknames in brackets ('(The Big Picture)' and so on)."
	)
	interpretations.append(
		"Phases 600 and 700 are put in build order (TASK_ORDER); the rest keep the sheet's order."
	)
	bundles = [
		{
			"name": name,
			"kind": "Bundle",
			"review_status": "Needs review",
			"notes": BUNDLE_NOTES,
			"tasks": [
				{"subject": subject, "hours": None, "waits_on": [subjects[index - 1]] if index else []}
				for index, subject in enumerate(subjects)
			],
		}
		for name, subjects in BUNDLES
	]
	return {
		"source": {
			"document": "DOC-0041 Sapphire Cost Worksheet",
			"version": "1.0",
			"derived_by": "scripts/derive_planner_templates.py",
			"cost_codes": "erpnext_enhancements/quality/cost_codes.py (OLD_TO_NEW)",
			"interpretations": interpretations,
		},
		"templates": [
			{
				"name": PHASE_TEMPLATE,
				"kind": "Phase template",
				"review_status": "Needs review",
				"notes": PHASE_NOTES,
				"phases": phases,
			},
			*bundles,
		],
	}


def main(argv):
	if len(argv) != 2:
		print(__doc__)
		return 2
	data = derive(argv[1])
	OUT.parent.mkdir(parents=True, exist_ok=True)
	OUT.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8", newline="\n")
	print(f"Wrote {OUT.relative_to(REPO)}")
	return 0


if __name__ == "__main__":
	sys.exit(main(sys.argv))
