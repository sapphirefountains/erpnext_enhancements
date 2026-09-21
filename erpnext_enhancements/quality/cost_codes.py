# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The cost code catalog, renumbered onto real MasterFormat sections - WI-077.

Imports no frappe, deliberately: this is the single authority the seed patch reads, and it has to
be testable in the only CI tier that actually runs. See ``tests/test_cost_codes.py``.

Why the whole catalog moved
---------------------------

Sapphire's cost codes were **MasterFormat 1995 numbers wearing the post-2004 format**. Someone took
the retired 16-division broadscope list, split each five-digit number after two digits and
left-zero-padded the rest: ``05500 Metal Fabrications`` became ``05 0500``, ``16300 Transmission and
Distribution`` became ``26 0300``. The transform is exact and holds across Divisions 02-09 and the
whole 26 03/04/05/07/08 block; nine of twelve Division 02-09 titles are verbatim 1995 titles.

The output *reads* as a current six-digit section number and points somewhere unrelated. Of 71 codes
checked against CSI/CSC *MasterFormat Numbers and Titles* (2011, 2016 and 2020 editions, read as
PDFs and text-extracted), only 8 were correct; 24 were real sections meaning something else, 32 were
not sections at all, and 7 described resources the standard has no home for.

The 24 are the damaging class, and they fail in the direction this codebase keeps meeting: they do
not error, they misinform. ``22 3100`` (Pumps) is *Domestic Water Softeners*. ``22 5100`` (Nozzles)
is *Swimming Pool Plumbing Systems*. ``00 5000`` (Fee) is *Contracting Forms and Supplements*, so a
pay application showed money against the number for the agreement form. These codes print on
Schedules of Values and AIA G702/G703 applications that general contractors read.

Two collisions are **newer than the codes**: ``02 03 00`` and ``09 03 00`` are absent from
MasterFormat 2011 and present in 2016. They were merely meaningless when adopted and have since
become live collisions - so "it has worked so far" was never evidence it would keep working.

Work results and resources are different things
-----------------------------------------------

MasterFormat classifies **work results**. A superintendent, a plane ticket and the fee are
**resources** - they say who or what was consumed, not what got built - and the standard has no home
for them by design. They live in :data:`RESOURCE_CODES` with codes that are visibly *not*
MasterFormat-shaped, because ``00 2200`` was indistinguishable from a real section reference and
would be read as one the moment it reached a GC. Division 00 is vacated entirely: every number in it
names a procurement document, and none of it is ever a cost line.

Numbering
---------

A six-digit section is CSI **Level 3**, not Level 4 - an off-by-one worth stating, because CSI writes
its publication restriction in terms of Levels 4 and 5. The decimal tier is CSI's Level 4 and CSI
publishes it itself (1,750 such numbers in 2011, 2,417 in 2016), so it is occupied territory rather
than free space. True Level 5 (``22 52 13.00.F1``) is internal-use-only and must never reach a pay
application.

Never invent six-digit siblings such as ``22 5214``. CSI allocates 13/16/19/23 precisely to leave
room for future assignments, so inventing there is the ``22 3100`` failure mode aimed at live growth
space. Extending downward with a Level-4 decimal is strictly safer, and the decimals used here follow
CSI's own spacing so that if CSI ever populates a section our list lines up rather than interleaving.

Verified against MasterFormat 2011, 2016 and 2020, which agree unanimously. **2026 is the current
edition and is licence-gated behind CSI Dynamic Standards**, so it could not be read; CSI's own 2026
changes page attributes over 80% of structural change to Divisions 32 and 34, not to the divisions
used here. Low risk, not zero. Closing it needs a licence, not another search.
"""

import re

MASTERFORMAT_EDITIONS_VERIFIED = ("2011", "2016", "2020")

#: Sapphire display form of a section number: ``22 5213`` or ``22 5213.29``.
#: This is CSI display option 2 (``11 2233.44``), not an invention.
COST_CODE_PATTERN = r"^\d{2} \d{4}(\.\d{2})?$"
COST_CODE_RE = re.compile(COST_CODE_PATTERN)


#: Every MasterFormat section this catalog uses, with its verbatim CSI title.
#: A cost code whose section is not in here is a bug - validate against this list, never against
#: :data:`COST_CODE_PATTERN` alone. ``01 5433`` is why: a legal Level-4 shape inside a real family
#: (01 54 00 Construction Aids) whose children stop at 01 54 26, so it passes any pattern check
#: and fails only a list check.
MASTERFORMAT_SECTIONS = {
	"01 21 16": "Contingency Allowances",
	"01 43 39": "Mockups",
	"01 53 00": "Temporary Construction",
	"01 54 16": "Temporary Hoists",
	"01 54 23": "Temporary Scaffolding and Platforms",
	"01 58 00": "Project Identification",
	"01 71 33": "Protection of Adjacent Construction",
	"01 74 00": "Cleaning and Waste Management",
	"01 76 00": "Protecting Installed Construction",
	"01 79 00": "Demonstration and Training",
	"02 41 13": "Selective Site Demolition",
	"03 30 00": "Cast-in-Place Concrete",
	"03 40 00": "Precast Concrete",
	"03 60 00": "Grouting",
	"04 40 00": "Stone Assemblies",
	"05 50 00": "Metal Fabrications",
	"06 10 00": "Rough Carpentry",
	"07 10 00": "Dampproofing and Waterproofing",
	"09 30 00": "Tiling",
	"09 70 00": "Wall Finishes",
	"09 90 00": "Painting and Coating",
	"13 01 12": "Operation and Maintenance of Fountains",
	"13 08 12": "Commissioning of Fountains",
	"13 12 00": "Fountains",
	"13 12 13": "Exterior Fountains",
	"13 12 23": "Interior Fountains",
	"22 01 50": "Operation and Maintenance of Pool and Fountain Plumbing Systems",
	"22 11 19": "Domestic Water Piping Specialties",
	"22 45 16": "Eyewash Equipment",
	"22 52 00": "Fountain Plumbing Systems",
	"22 52 13": "Fountain Piping",
	"22 52 16": "Fountain Pumps",
	"22 52 19": "Fountain Water Treatment Equipment",
	"22 52 23": "Fountain Equipment Controls",
	"23 64 00": "Packaged Water Chillers",
	"26 01 50": "Operation and Maintenance of Lighting",
	"26 05 00": "Common Work Results for Electrical",
	"26 05 19": "Low-Voltage Electrical Power Conductors and Cables",
	"26 05 83": "Wiring Connections",
	"26 09 00": "Instrumentation and Control for Electrical Systems",
	"26 20 00": "Low-Voltage Electrical Distribution",
	"26 27 16": "Electrical Cabinets and Enclosures",
	"26 50 00": "Lighting",
	"26 55 00": "Special Purpose Lighting",
	"26 55 29": "Underwater Lighting",
	"27 00 00": "Communications",
	"27 40 00": "Audio-Video Communications",
	"31 23 00": "Excavation and Fill",
}

#: Section assignments that were medium or low confidence. Not blockers; each is a judgement a
#: human should confirm before the catalog is treated as settled.
SECTIONS_NEEDING_RECHECK = {
	"03 60 00": "04 0600 Grouts is ambiguous: masonry setting grout (04 05 16) or structural grout.",
	"22 11 19": "22 1160 Relocate Existing Hose Bib: the standard has no relocate-existing section.",
	"22 52 00": "22 3300 Water Heater: MasterFormat has no fountain-heating section at all.",
	"26 05 19": "26 0420 Low-Voltage Materials and Methods: proposed here, not agent-verified.",
	"26 09 00": "26 0750/26 0760 control-panel work: medium confidence.",
	"26 20 00": "26 0300 Transmission and Distribution: medium confidence.",
	"26 01 50": "26 5550 Relocate Existing Light: no relocate-existing section exists.",
}

COST_CODES = (
	{
		"cost_code": "01 2116",
		"code_title": "Contingency Allowances",
		"section": "01 21 16",
		"old_code": "01 1100",
		"cost_type": "Expense",
		"budget_category": "Contingency",
		"default_labor_class": "",
		"is_overhead": 1,
		"note": "Computed, never keyed as a line.",
	},
	{
		"cost_code": "01 4339",
		"code_title": "Mockups",
		"section": "01 43 39",
		"old_code": "00 1100",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "A billable physical mockup for owner approval. Internal R&D prototyping uses OH-PROTO instead - Nik 2026-09-21: 'either depending on the project', so the estimator picks per job.",
	},
	{
		"cost_code": "01 5300",
		"code_title": "Temporary Construction",
		"section": "01 53 00",
		"old_code": "01 0200",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "01 5416",
		"code_title": "Temporary Hoists",
		"section": "01 54 16",
		"old_code": "01 5433",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "01 5423",
		"code_title": "Temporary Scaffolding and Platforms",
		"section": "01 54 23",
		"old_code": "01 0210",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "01 5800",
		"code_title": "Project Identification",
		"section": "01 58 00",
		"old_code": "01 0300",
		"cost_type": "Material",
		"budget_category": "General Conditions",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title was already verbatim correct; only the number moved.",
	},
	{
		"cost_code": "01 7133",
		"code_title": "Protection of Adjacent Construction",
		"section": "01 71 33",
		"old_code": "01 0500",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title was already verbatim correct.",
	},
	{
		"cost_code": "01 7400",
		"code_title": "Cleaning and Waste Management",
		"section": "01 74 00",
		"old_code": "01 0400",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "01 7600",
		"code_title": "Protecting Installed Construction",
		"section": "01 76 00",
		"old_code": "01 0410",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title was already verbatim correct.",
	},
	{
		"cost_code": "01 7900",
		"code_title": "Demonstration and Training",
		"section": "01 79 00",
		"old_code": "01 7900",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"is_overhead": 0,
		"note": "ALREADY CORRECT. Bundles training, O&M data and warranties - split if they must bill separately.",
	},
	{
		"cost_code": "02 4113",
		"code_title": "Selective Site Demolition",
		"section": "02 41 13",
		"old_code": "02 1200",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "03 3000",
		"code_title": "Cast-in-Place Concrete",
		"section": "03 30 00",
		"old_code": "03 0210",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "03 4000",
		"code_title": "Precast Concrete",
		"section": "03 40 00",
		"old_code": "03 0400",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "03 6000",
		"code_title": "Grouting",
		"section": "03 60 00",
		"old_code": "04 0600",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Moves division: grouting is 03, not 04. RECHECK what this actually prices.",
	},
	{
		"cost_code": "04 4000",
		"code_title": "Stone Assemblies",
		"section": "04 40 00",
		"old_code": "04 0400",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Judgment call #6 moot - neither 04 0400 nor 04 0450 was a real section.",
	},
	{
		"cost_code": "05 5000",
		"code_title": "Metal Fabrications",
		"section": "05 50 00",
		"old_code": "05 0500",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "06 1000",
		"code_title": "Rough Carpentry",
		"section": "06 10 00",
		"old_code": "06 0100",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "07 1000",
		"code_title": "Dampproofing and Waterproofing",
		"section": "07 10 00",
		"old_code": "07 0100",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title changes from 'Waterproofing'.",
	},
	{
		"cost_code": "09 3000",
		"code_title": "Tiling",
		"section": "09 30 00",
		"old_code": "09 0300",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title changes from 'Tile'.",
	},
	{
		"cost_code": "09 7000",
		"code_title": "Wall Finishes",
		"section": "09 70 00",
		"old_code": "09 0700",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "09 9000",
		"code_title": "Painting and Coating",
		"section": "09 90 00",
		"old_code": "09 0900",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Title changes from 'Paints and Coatings'.",
	},
	{
		"cost_code": "13 0112.13",
		"code_title": "Seasonal Start-up",
		"section": "13 01 12",
		"old_code": "13 1260",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"is_overhead": 0,
		"note": "User-assigned Level 4 on CSI spacing.",
	},
	{
		"cost_code": "13 0112.16",
		"code_title": "Seasonal Shut-down",
		"section": "13 01 12",
		"old_code": "13 1250",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"is_overhead": 0,
		"note": "User-assigned Level 4 on CSI spacing.",
	},
	{
		"cost_code": "13 0812",
		"code_title": "Commissioning of Fountains",
		"section": "13 08 12",
		"old_code": "13 0812",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"is_overhead": 0,
		"note": "ALREADY CORRECT.",
	},
	{
		"cost_code": "13 1200",
		"code_title": "Fountains",
		"section": "13 12 00",
		"old_code": "13 0600",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Was 'Tiered Fountains and Sculpture'.",
	},
	{
		"cost_code": "13 1213",
		"code_title": "Exterior Fountains",
		"section": "13 12 13",
		"old_code": "13 1213",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "ALREADY CORRECT.",
	},
	{
		"cost_code": "13 1213.13",
		"code_title": "Splash Pad Play Features",
		"section": "13 12 13",
		"old_code": "13 0700",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "RECHECK: water play sits between 13 12 13 and 13 11 00 Swimming Pools.",
	},
	{
		"cost_code": "13 1223",
		"code_title": "Interior Fountains",
		"section": "13 12 23",
		"old_code": "13 1223",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "ALREADY CORRECT. Judgment call #9 - the one of nine that survives untouched.",
	},
	{
		"cost_code": "22 0150",
		"code_title": "Operation and Maintenance of Pool and Fountain Plumbing Systems",
		"section": "22 01 50",
		"old_code": "22 0150",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"is_overhead": 0,
		"note": "Number already correct; title takes the full CSI wording.",
	},
	{
		"cost_code": "22 1119",
		"code_title": "Domestic Water Piping Specialties",
		"section": "22 11 19",
		"old_code": "22 1160",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Relocate Existing Hose Bib. Genuinely domestic water, not fountain. RECHECK.",
	},
	{
		"cost_code": "22 4516",
		"code_title": "Eyewash Equipment",
		"section": "22 45 16",
		"old_code": "22 4516",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Number already correct; title changes from 'Emergency Eye Wash (installed)'.",
	},
	{
		"cost_code": "22 5200",
		"code_title": "Fountain Plumbing Systems",
		"section": "22 52 00",
		"old_code": "22 3300",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Water Heater. RECHECK: no fountain-heating section exists; rolls to the parent.",
	},
	{
		"cost_code": "22 5213.13",
		"code_title": "Rough Fountain Piping",
		"section": "22 52 13",
		"old_code": "22 1100",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5213.16",
		"code_title": "Equipment Room Piping",
		"section": "22 52 13",
		"old_code": "22 1110",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5213.19",
		"code_title": "Water Hammer Arrestors",
		"section": "22 52 13",
		"old_code": "22 1119",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5213.23",
		"code_title": "Check Valves",
		"section": "22 52 13",
		"old_code": "22 1120",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5213.26",
		"code_title": "Manifold Materials",
		"section": "22 52 13",
		"old_code": "22 1150",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5213.29",
		"code_title": "Fountain Nozzles",
		"section": "22 52 13",
		"old_code": "22 5100",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Was 22 5100, which is Swimming Pool Plumbing Systems - a live collision.",
	},
	{
		"cost_code": "22 5216",
		"code_title": "Fountain Pumps",
		"section": "22 52 16",
		"old_code": "22 3100",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Was 22 3100, which is Domestic Water Softeners.",
	},
	{
		"cost_code": "22 5219",
		"code_title": "Fountain Water Treatment Equipment",
		"section": "22 52 19",
		"old_code": "22 3200",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5223.13",
		"code_title": "Chemical Controller",
		"section": "22 52 23",
		"old_code": "22 3250",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "22 5223.16",
		"code_title": "Solenoid Valves",
		"section": "22 52 23",
		"old_code": "26 0425",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "RECHECK: moved out of Division 26 - a controlled valve in the water circuit.",
	},
	{
		"cost_code": "23 6400",
		"code_title": "Packaged Water Chillers",
		"section": "23 64 00",
		"old_code": "22 3400",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Moves to Division 23. Was 22 3400, which is Fuel-Fired Domestic Water Heaters.",
	},
	{
		"cost_code": "26 0150.81",
		"code_title": "Luminaire Removal and Replacement",
		"section": "26 01 50",
		"old_code": "26 5550",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "RECHECK: no relocate-existing section exists; CSI's O&M .81 is least-wrong.",
	},
	{
		"cost_code": "26 0500",
		"code_title": "Common Work Results for Electrical",
		"section": "26 05 00",
		"old_code": "26 0410",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
	},
	{
		"cost_code": "26 0519",
		"code_title": "Low-Voltage Electrical Power Conductors and Cables",
		"section": "26 05 19",
		"old_code": "26 0420",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "RECHECK: proposed here rather than agent-verified.",
	},
	{
		"cost_code": "26 0583",
		"code_title": "Wiring Connections",
		"section": "26 05 83",
		"old_code": "26 0350",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Journeyman",
		"is_overhead": 0,
	},
	{
		"cost_code": "26 0900",
		"code_title": "Instrumentation and Control for Electrical Systems",
		"section": "26 09 00",
		"old_code": "26 0750",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Control panel components. RECHECK.",
	},
	{
		"cost_code": "26 0900.16",
		"code_title": "Control Panel Build and Programming",
		"section": "26 09 00",
		"old_code": "26 0760",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Programmer",
		"is_overhead": 0,
		"note": "Building and programming a panel is a different work result from supplying its parts.",
	},
	{
		"cost_code": "26 2000",
		"code_title": "Low-Voltage Electrical Distribution",
		"section": "26 20 00",
		"old_code": "26 0300",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "RECHECK.",
	},
	{
		"cost_code": "26 2716",
		"code_title": "Electrical Cabinets and Enclosures",
		"section": "26 27 16",
		"old_code": "26 1000",
		"cost_type": "Equipment",
		"budget_category": "Equipment",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Was 26 1000, which is Medium-Voltage Electrical Distribution.",
	},
	{
		"cost_code": "26 5000",
		"code_title": "Lighting",
		"section": "26 50 00",
		"old_code": "26 0500",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Was 26 0500, which is Common Work Results for Electrical.",
	},
	{
		"cost_code": "26 5500",
		"code_title": "Special Purpose Lighting",
		"section": "26 55 00",
		"old_code": "26 5500",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "ALREADY CORRECT.",
	},
	{
		"cost_code": "26 5529",
		"code_title": "Underwater Lighting",
		"section": "26 55 29",
		"old_code": "26 5529",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "ALREADY CORRECT.",
	},
	{
		"cost_code": "27 0000",
		"code_title": "Communications",
		"section": "27 00 00",
		"old_code": "26 0700",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Moves to Division 27.",
	},
	{
		"cost_code": "27 4000",
		"code_title": "Audio-Video Communications",
		"section": "27 40 00",
		"old_code": "26 0800",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Moves to Division 27. Was 26 0800, which is Commissioning of Electrical Systems.",
	},
	{
		"cost_code": "31 2300",
		"code_title": "Excavation and Fill",
		"section": "31 23 00",
		"old_code": "02 0300",
		"cost_type": "Subcontract",
		"budget_category": "Subcontractors",
		"default_labor_class": "",
		"is_overhead": 0,
		"note": "Judgment call #7 reversed: 02 30 00 is Subsurface Investigation; 31 0100 is not a section.",
	},
)

#: The second dimension. Resources, not work results, and deliberately not MasterFormat-shaped.
RESOURCE_CODES = (
	{
		"resource_code": "LAB-PM",
		"title": "Project Management",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Project Manager",
		"old_code": "01 0100",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-PE",
		"title": "Project Engineer",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Journeyman",
		"old_code": "01 0110",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-SUPT",
		"title": "Superintendent",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Foreman",
		"old_code": "01 0120",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-COORD",
		"title": "Project Coordinator",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Technician",
		"old_code": "01 0130",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-EXEC",
		"title": "Project Executive",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Project Manager",
		"old_code": "01 0140",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-DIRECT",
		"title": "Direct Labor",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Journeyman",
		"old_code": "01 0150",
		"is_overhead": 0,
		"note": "Used once per phase; the Budget Sheet's eight texts are line descriptions.",
	},
	{
		"resource_code": "LAB-DESIGN",
		"title": "Design",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Designer",
		"old_code": "00 1000",
		"is_overhead": 0,
	},
	{
		"resource_code": "LAB-DESIGN-ASSIST",
		"title": "Design Labor and Assistance",
		"cost_type": "Labor",
		"budget_category": "Labor",
		"default_labor_class": "Designer",
		"old_code": "00 1200",
		"is_overhead": 0,
	},
	{
		"resource_code": "OH-PROTO",
		"title": "Prototyping (internal)",
		"cost_type": "Material",
		"budget_category": "Materials",
		"default_labor_class": "",
		"old_code": "00 1100",
		"is_overhead": 0,
		"note": "Internal R&D. A billable physical mockup uses 01 4339 Mockups instead - the estimator picks per job.",
	},
	{
		"resource_code": "OH-EXPENSE",
		"title": "Expenses (software, hardware)",
		"cost_type": "Expense",
		"budget_category": "General Conditions",
		"default_labor_class": "",
		"old_code": "00 2000",
		"is_overhead": 0,
		"note": "D3 correction: 0% markup, was carrying the 145% labor burden.",
	},
	{
		"resource_code": "OH-TRAVEL",
		"title": "Travel",
		"cost_type": "Expense",
		"budget_category": "General Conditions",
		"default_labor_class": "",
		"old_code": "00 2200",
		"is_overhead": 0,
		"note": "D3 correction: 0% markup, was carrying the 145% labor burden.",
	},
	{
		"resource_code": "FEE",
		"title": "Fee (based on the Cost of Work)",
		"cost_type": "Expense",
		"budget_category": "Fee",
		"default_labor_class": "",
		"old_code": "00 5000",
		"is_overhead": 1,
		"note": "Computed. Also supersedes the older 01 1000.",
	},
)

#: Old codes that map to a replacement but are not themselves carried forward.
RETIRED_CODES = {
	"00 5000": ("FEE", "Division 00 vacated: 00 50 00 is Contracting Forms and Supplements."),
	"01 1000": ("FEE", "01 10 00 is Summary."),
	"04 0450": ("04 4000", "Never a real section; duplicate of 04 0400."),
	"22 1123": ("22 5216", "OLD list's Domestic water pumps. 22 11 23 IS that section - but these are fountain pumps."),
	"26 0400": ("26 0500", "Not a real section."),
	"31 0100": ("31 2300", "Not a real section; 31 10 00 is Site Clearing."),
}

#: One old code deliberately maps to two homes; the estimator picks per job.
SPLIT_OLD_CODES = {
	"00 1100": ("01 4339", "OH-PROTO"),
}

#: Which home a split old code takes when something must choose one.
SPLIT_PRIMARY = {
	"00 1100": "01 4339",
}

#: Every old code, to whatever replaces it. Total over the pre-renumbering catalog, so a
#: historical estimate can always be read forward. A split old code resolves to its primary.
OLD_TO_NEW = {}
for _row in COST_CODES:
	OLD_TO_NEW.setdefault(_row["old_code"], _row["cost_code"])
for _row in RESOURCE_CODES:
	OLD_TO_NEW.setdefault(_row["old_code"], _row["resource_code"])
for _old, (_new, _why) in RETIRED_CODES.items():
	OLD_TO_NEW.setdefault(_old, _new)
OLD_TO_NEW.update(SPLIT_PRIMARY)

del _row, _old, _new, _why

#: Code strings that exist in BOTH catalogs with DIFFERENT meanings. The migration-order hazard:
#: un-migrated data holding one of these silently changes meaning rather than failing to resolve.
#: Migrate by ``old_code``, never by matching the string.
REUSED_CODES = {
	"22 1119": {"was": "Water Hammer Arrestors", "now": "Domestic Water Piping Specialties", "old_moved_to": "22 5213.19"},
	"26 0500": {"was": "General Lighting", "now": "Common Work Results for Electrical", "old_moved_to": "26 5000"},
}

#: New codes that deliberately absorb more than one old code.
MERGED_OLD_CODES = {
	"04 4000": ("04 0400", "04 0450",),
	"22 5216": ("22 1123", "22 3100",),
	"26 0500": ("26 0400", "26 0410",),
	"31 2300": ("02 0300", "31 0100",),
	"FEE": ("00 5000", "01 1000",),
}
