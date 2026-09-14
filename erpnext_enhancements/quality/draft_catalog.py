# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Strawman inspection checklists — **drafts for correction, not Sapphire's standard of care**.

Sub-phases C and I both declined to seed these, and the reason still stands: *a checklist
carries the authority of the company that issued it, an inspector works through it assuming
somebody chose those items on purpose, and an invented one is indistinguishable from a real one
right up until it fails to catch something.*

Nik asked for strawmen to correct rather than a blank page, so this is the compromise that keeps
that reasoning intact:

**Every template here is seeded ``Draft``, and a Draft template can generate nothing.**
``api/quality_inspection.generate_inspection`` refuses a non-Active template, and
``quality/scheduling`` counts only Active ones, so until a person reads a template and sets it
Active the milestone keeps reporting *due and blocked* exactly as it did before. **Setting it
Active is the act of adopting it.** Nobody can be handed one of these by accident.

Provenance, which is the point
-------------------------------

Three of the four sets are **not invented**. They are lifted from what this company has already
written down somewhere else in this system, and each item records where:

* **Service** — Sapphire's own ``Sapphire Maintenance Section`` records, live on production and
  in technicians' hands since June. The chemistry ranges are theirs verbatim: pH 7.2–7.8, free
  chlorine 1.0–3.0 ppm, ORP 650–750 mV, total alkalinity 80–120 ppm. "GFCI protection verified"
  is mandatory here because it is mandatory there.
* **Design** — the ``Water Feature Design`` engineering model in ``water_engineering``: its
  status ladder, ``blocker_count``, ``issue_acks``, computed turnover, TDH and pump selection.
  The gates ask whether that record says what it needs to say.
* **Products / Controls Fab** — the ``Control Panel Design`` model: NEMA rating, controller
  hardware, fuse and interlock schedules, control voltages, and ``safe_state_on_power_up``.

**Events is the honest exception.** Nothing in this system describes an event setup, so that set
is drafted from the milestone descriptions in :mod:`erpnext_enhancements.quality.catalog` and
from general practice. It is the one to read hardest, and its sections say so.

**Build** is included for completeness — pre-pour, in-process and final walkthrough had no
template either — and is the same kind of draft. Only ``Build — Pre-Final (Systems Startup)``,
seeded back in sub-phase C, is *not* a strawman: those six commissioning checks came from
``docs/KPI_DASHBOARD_DESIGN.md``, written by somebody who knew the trade.

Imports nothing, so the shape is asserted on every push — every item having a pass standard and
a verification method is not a thing to discover when an inspector is standing in front of it.
"""

#: Prefixed to every section description this module seeds. It is the sentence somebody reads
#: when they open one of these in the Desk, and it must not be softened.
DRAFT_NOTICE = (
	"DRAFT FOR REVIEW — not yet Sapphire's standard of care. Correct it, then set the template "
	"Active. Until a person does that, nothing can generate an inspection from it."
)

#: UOMs the measurement checks below need. ERPNext ships 240 UOMs and none of these; the seed
#: creates them insert-only. Without them a Measurement check has no unit and its bounds cannot
#: be compared, which turns a measured reading back into an opinion.
REQUIRED_UOMS = ("pH", "ppm", "mV", "Volt", "PSI")

#: (label, acceptance_criteria, check_type, method, uom, min_value, max_value,
#:  is_mandatory, requires_photo, reference_standard)
#:
#: ``requires_photo`` means *a photo is required when this check FAILS* — the evidence gate in
#: ``merge.missing_evidence``, not a photo on every row.

# ---------------------------------------------------------------------------
# Service — lifted from Sapphire's own maintenance sections
# ---------------------------------------------------------------------------

_M = "Sapphire Maintenance"

SERVICE_ARRIVAL = [
	("Water level on arrival",
	 "At or above the auto-fill line, with no sign the basin has run low since the last visit.",
	 "Pass/Fail", "Visual against the marked water line", None, 0, 0, 0, 1,
	 f"{_M}: Auto-Fill & Water Level"),
	("Water clarity on arrival",
	 "Basin floor visible through the full depth, with no visible algae bloom or surface film.",
	 "Pass/Fail", "Visual, before any cleaning", None, 0, 0, 0, 1,
	 f"{_M}: Algae & Water Clarity"),
	("Water was in spec on arrival",
	 "pH and ORP within the ranges on the service plan, measured BEFORE any dosing.",
	 "Pass/Fail", "Test kit or probe, before touching anything", None, 0, 0, 1, 0,
	 f"{_M}: Chemistry On Arrival Check"),
	("Feature running as the client left it",
	 "Pumps, lights and any programmed sequence running as expected, with nothing switched off "
	 "at the panel.",
	 "Pass/Fail", "Observe before intervening", None, 0, 0, 0, 0, f"{_M}: Equipment Inspection"),
	("No new damage or vandalism since the last visit",
	 "No new damage to vessel, coping, nozzles, lighting or equipment. Anything found is "
	 "photographed and noted.",
	 "Pass/Fail", "Walk the feature", None, 0, 0, 0, 1, None),
]

SERVICE_CHEMISTRY = [
	("pH", "Between 7.2 and 7.8.", "Measurement", "Test kit or calibrated probe",
	 "pH", 7.2, 7.8, 1, 0, f"{_M}: Water Chemistry Readings"),
	("Free chlorine", "Between 1.0 and 3.0 ppm.", "Measurement", "Test kit or calibrated probe",
	 "ppm", 1.0, 3.0, 1, 0, f"{_M}: Water Chemistry Readings"),
	("ORP", "Between 650 and 750 mV.", "Measurement", "Calibrated ORP probe",
	 "mV", 650.0, 750.0, 1, 0, f"{_M}: Water Chemistry Readings"),
	("Total alkalinity", "Between 80 and 120 ppm.", "Measurement", "Test kit",
	 "ppm", 80.0, 120.0, 0, 0, f"{_M}: Water Chemistry Readings"),
]

SERVICE_SAFETY = [
	("GFCI protection verified",
	 "Every GFCI device on the feature trips on test and resets.",
	 "Pass/Fail", "Push-to-test on each device", None, 0, 0, 1, 0,
	 f"{_M}: Safety & Electrical (mandatory there too)"),
	("Bonding and grounding intact",
	 "Bonding conductors present, continuous and connected at every point the design calls for.",
	 "Pass/Fail", "Visual and continuity check", None, 0, 0, 0, 1, f"{_M}: Safety & Electrical"),
	("No exposed wiring",
	 "No conductor is accessible without a tool anywhere on the feature.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, f"{_M}: Safety & Electrical"),
	("Junction boxes sealed",
	 "Every enclosure is closed, gasketed and rated for where it sits.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, f"{_M}: Safety & Electrical"),
	("Signage and barriers in place",
	 "Any signage or barrier the site requires is present, legible and secure.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 0, f"{_M}: Safety & Electrical"),
	("Light fixtures and lenses sound",
	 "Every fixture lights, and no lens or gasket is cracked, clouded or missing.",
	 "Pass/Fail", "Visual with lights on", None, 0, 0, 0, 1, f"{_M}: Lighting Inspection"),
]

SERVICE_WORK_DONE = [
	("Pump basket cleaned",
	 "Basket removed, cleared and refitted with its seal seated.",
	 "Pass/Fail", "Visual", None, 0, 0, 1, 0, f"{_M}: Pump & Filter Service (mandatory there too)"),
	("Filter serviced and pressure normal",
	 "Filter backwashed or rinsed, and pressure back within the normal band for this feature.",
	 "Pass/Fail", "Gauge reading after service", None, 0, 0, 0, 0, f"{_M}: Pump & Filter Service"),
	("Filter pressure",
	 "Within the normal operating band for this feature, recorded so drift between visits is "
	 "visible.",
	 "Measurement", "Gauge on the filter", "PSI", 0, 0, 0, 0, f"{_M}: Pump & Filter Service"),
	("Skimmer and drains clear",
	 "No obstruction at any skimmer, weir or drain.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 0, f"{_M}: Equipment Inspection"),
	("Chemicals used are recorded",
	 "Every chemical added is written on the record with its quantity, or the record says none "
	 "were used.",
	 "Document", "Check the visit record", None, 0, 0, 1, 0, f"{_M}: Visit Wrap-Up Checks"),
	("Everything in order and running normally on departure",
	 "The feature is running as the client expects to find it, with nothing left isolated or "
	 "switched off.",
	 "Pass/Fail", "Final walk before leaving", None, 0, 0, 1, 0,
	 f"{_M}: Visit Wrap-Up Checks (mandatory there too)"),
	("Site left clean and client-ready",
	 "No tools, packaging, spent chemicals or debris left on site.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, None),
]

# ---------------------------------------------------------------------------
# Design — the questions the Water Feature Design record should already answer
# ---------------------------------------------------------------------------

_W = "water_engineering: Water Feature Design"

DESIGN_CONCEPT = [
	("The brief is written down",
	 "There is a recorded scope or brief for this feature, not only a conversation.",
	 "Document", "Open the Scope of Work or the brief document", None, 0, 0, 1, 0, None),
	("Venue type and fountain type are chosen and recorded",
	 "Both are set on the design record, because every downstream calculation keys off them.",
	 "Document", "Check the design record", None, 0, 0, 1, 0, f"{_W}.venue_type / .fountain_type"),
	("Governing code is named",
	 "The code this design will be held to is recorded, not assumed.",
	 "Document", "Check the design record", None, 0, 0, 1, 0, f"{_W}.governing_code"),
	("Site constraints are recorded",
	 "Access, available power, drainage and any structural limit are written down.",
	 "Pass/Fail", "Compare the concept against the site survey", None, 0, 0, 0, 0, None),
	("The concept answers the brief",
	 "Every element the client asked for is present in the concept, or its absence is recorded "
	 "with a reason.",
	 "Pass/Fail", "Read the brief and the concept side by side", None, 0, 0, 1, 0, None),
	("The concept is affordable against the budget sold",
	 "A concept estimate exists and is within the figure the client agreed, or the gap is "
	 "written down.",
	 "Pass/Fail", "Compare concept estimate to the contracted figure", None, 0, 0, 0, 0, None),
]

DESIGN_DEVELOPMENT = [
	("The engineering calculation exists and has been run",
	 "A Water Feature Design record for this project has reached at least Calculated status.",
	 "Document", "Check the design record status", None, 0, 0, 1, 0, f"{_W}.status"),
	("No open design blockers",
	 "blocker_count on the design record is zero.",
	 "Pass/Fail", "Read the design record", None, 0, 0, 1, 0, f"{_W}.blocker_count"),
	("Warnings are acknowledged rather than ignored",
	 "Every warning on the design is either resolved or carries a recorded acknowledgement.",
	 "Pass/Fail", "Read the design record", None, 0, 0, 0, 0, f"{_W}.issue_acks / .warning_count"),
	("Turnover time meets the governing code",
	 "Computed turnover is at or below the maximum the governing code allows for this venue type.",
	 "Pass/Fail", "Compare computed turnover to the code maximum", None, 0, 0, 1, 0,
	 f"{_W}.turnover_time_min vs .max_turnover_min"),
	("Pump selection covers the computed head",
	 "The selected pump's curve covers the computed TDH at design flow, with margin.",
	 "Pass/Fail", "Read the pump curve against the calculation", None, 0, 0, 1, 0,
	 f"{_W}.computed_tdh_ft / .selected_pump"),
	("Electrical load schedule is complete",
	 "Every load is on the schedule, and the total is within the supply available at the site.",
	 "Pass/Fail", "Read the load schedule", None, 0, 0, 0, 0, f"{_W}.electrical_loads"),
	("Drain and surge capacity are sized",
	 "Drain capacity and surge volume are calculated, not left at their defaults.",
	 "Pass/Fail", "Read the treatment and drainage tab", None, 0, 0, 0, 0,
	 f"{_W}.drain_capacity_gpm / .surge_basin_gallons"),
	("Chemistry targets are set for the water type",
	 "Chlorine, CYA and free-chlorine targets are set for the correct indoor, outdoor or "
	 "saltwater condition.",
	 "Pass/Fail", "Read the treatment tab", None, 0, 0, 0, 0, f"{_W}.chem_water_type"),
]

DESIGN_SIGNOFF = [
	("Design record is Reviewed or Issued",
	 "The engineering record has been through review, not left at Calculated.",
	 "Document", "Check the design record status", None, 0, 0, 1, 0, f"{_W}.status"),
	("Submittal packet is generated and attached",
	 "The packet exists as a document, not as a promise to produce one.",
	 "Document", "Open the packet from the design record", None, 0, 0, 1, 0,
	 f"{_W}.packet_document"),
	("Every issue acknowledgement is signed",
	 "No warning is carried into construction unacknowledged.",
	 "Pass/Fail", "Read the acknowledgement table", None, 0, 0, 0, 0, f"{_W}.issue_acks"),
	("It is buildable as drawn",
	 "Somebody who will build it has read it and says it can be built as drawn.",
	 "Pass/Fail", "Ask the person who will build it", None, 0, 0, 1, 0, None),
	("It is what was sold",
	 "The design matches the locked Scope of Work. Any difference is covered by a Change Order.",
	 "Pass/Fail", "Compare the design against the locked scope", None, 0, 0, 1, 0, None),
	("Drawn-by and issue date are recorded",
	 "The record says who drew it and when it was issued.",
	 "Document", "Check the design record", None, 0, 0, 0, 0, f"{_W}.drawn_by / .issue_date"),
]

# ---------------------------------------------------------------------------
# Products / Controls Fab — against the Control Panel Design record
# ---------------------------------------------------------------------------

_C = "water_engineering: Control Panel Design"

PRODUCTS_INCOMING = [
	("Delivered items match the purchase order",
	 "Every line on the PO is present in the quantity ordered, or the shortfall is recorded "
	 "against it.",
	 "Document", "Count against the PO", None, 0, 0, 1, 0, None),
	("Controller matches the specified hardware",
	 "The controller received is the one the panel design names.",
	 "Pass/Fail", "Compare label to the design record", None, 0, 0, 1, 0, f"{_C}.controller_hardware"),
	("Enclosure part number and NEMA rating match the design",
	 "Both match. An enclosure rated below the design is a failure, not a substitution.",
	 "Pass/Fail", "Compare nameplate to the design record", None, 0, 0, 1, 1,
	 f"{_C}.enclosure_part_no / .nema_rating"),
	("No shipping damage",
	 "No dents, cracked windows, bent flanges or damaged components.",
	 "Pass/Fail", "Visual on unpacking", None, 0, 0, 0, 1, None),
	("Serial numbers recorded for serialised items",
	 "Every serialised component has its serial recorded against this project.",
	 "Document", "Record as received", None, 0, 0, 0, 0, None),
]

PRODUCTS_IN_PROCESS = [
	("Wire gauge and colour follow the panel schedule",
	 "Every conductor matches the schedule for gauge and colour.",
	 "Pass/Fail", "Compare against the panel drawing", None, 0, 0, 0, 1, None),
	("Every conductor is landed and torqued",
	 "No unlanded conductor, and every terminal torqued to its specification and marked.",
	 "Pass/Fail", "Torque driver, then mark", None, 0, 0, 1, 0, None),
	("Fuses match the fuse schedule",
	 "Every fuse is the rating and type the design names.",
	 "Pass/Fail", "Compare against the design record", None, 0, 0, 1, 0, f"{_C}.fuses"),
	("Control transformer VA is as designed",
	 "The transformer fitted is at or above the designed VA.",
	 "Pass/Fail", "Compare nameplate to the design record", None, 0, 0, 0, 0,
	 f"{_C}.control_transformer_va"),
	("Wire markers and terminal labels present and legible",
	 "Every conductor and terminal is labelled, and the labels can be read.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, None),
	("Panel is clean inside",
	 "No swarf, offcuts, stripped insulation or packaging left inside the enclosure.",
	 "Pass/Fail", "Visual before closing", None, 0, 0, 1, 1, None),
]

PRODUCTS_PRE_SHIPMENT = [
	("Main breaker size matches the design",
	 "The breaker fitted is the size the design names.",
	 "Pass/Fail", "Compare to the design record", None, 0, 0, 1, 0, f"{_C}.main_breaker_size_a"),
	("Control voltage measured",
	 "Within ten per cent of the design control voltage under load.",
	 "Measurement", "Meter at the control terminals, panel energised", "Volt", 0, 0, 0, 0,
	 f"{_C}.control_voltage_1"),
	("Every pump starter operates from the HMI",
	 "Each starter runs and stops from its own control, with the correct feedback on screen.",
	 "Pass/Fail", "Operate each one from the HMI", None, 0, 0, 1, 0, f"{_C}.pumps"),
	("Every interlock trips the system as designed",
	 "Each interlock is forced and the system responds exactly as the design says it should.",
	 "Pass/Fail", "Force each interlock in turn", None, 0, 0, 1, 0, f"{_C}.interlocks"),
	("Safe state on power-up confirmed",
	 "After a power cycle the panel comes up in its safe state rather than resuming outputs.",
	 "Pass/Fail", "Cycle power and watch the outputs", None, 0, 0, 1, 0,
	 f"{_C}.safe_state_on_power_up"),
	("Every IO point reads and writes correctly",
	 "Each point on the IO schedule is exercised and behaves as scheduled.",
	 "Pass/Fail", "Work down the IO schedule", None, 0, 0, 1, 0, f"{_C}.io_points"),
	("All specified HMI screens present and navigable",
	 "Every screen the design lists is present and reachable.",
	 "Pass/Fail", "Navigate the HMI", None, 0, 0, 0, 0, f"{_C}.screen_main / .additional_screens"),
	("Insulation resistance meets specification",
	 "Measured insulation resistance is at or above the minimum for this panel.",
	 "Pass/Fail", "Insulation resistance tester, supply isolated", None, 0, 0, 0, 0, None),
	("As-built panel photographed with the door open",
	 "A photograph of the finished interior is attached to the record.",
	 "Document", "Photograph before shipping", None, 0, 0, 1, 0, None),
]

PRODUCTS_COMMISSIONING = [
	("Incoming supply voltage and phase match the design",
	 "Measured supply matches the voltage, phase and frequency the panel was designed for.",
	 "Pass/Fail", "Meter at the supply, before energising the panel", None, 0, 0, 1, 0,
	 f"{_C}.main_line_voltage / .phase / .frequency_hz"),
	("GFCI protection verified on every circuit that needs it",
	 "Every GFCI device trips on test and resets.",
	 "Pass/Fail", "Push-to-test on each device", None, 0, 0, 1, 0, None),
	("Every pump runs in the correct rotation",
	 "Each pump turns the right way and draws within its expected current.",
	 "Pass/Fail", "Bump-start and observe", None, 0, 0, 1, 0, f"{_C}.pumps"),
	("Every light circuit operates and follows its sequence",
	 "Each circuit energises and the programmed sequence runs as designed.",
	 "Pass/Fail", "Run the sequence after dark or with the vault covered", None, 0, 0, 0, 1,
	 f"{_C}.lights"),
	("Every interlock tested in place",
	 "Each interlock is forced on the installed system, not only on the bench.",
	 "Pass/Fail", "Force each interlock on site", None, 0, 0, 1, 0, f"{_C}.interlocks"),
	("Theory of operation demonstrated to the client",
	 "The client or their representative has seen the system run through its modes and had the "
	 "theory of operation explained.",
	 "Pass/Fail", "Walk them through it", None, 0, 0, 0, 0, f"{_C}.theory_of_operation"),
	("As-built panel drawings left on site",
	 "A current set of drawings is in the panel or with the site documentation.",
	 "Document", "Leave and record", None, 0, 0, 1, 0, None),
]

# ---------------------------------------------------------------------------
# Build — the three milestones sub-phase C left without a template
# ---------------------------------------------------------------------------

BUILD_PRE_POUR = [
	("Layout matches the approved drawings",
	 "Setting-out matches the issued drawing in position and orientation.",
	 "Pass/Fail", "Tape and drawing", None, 0, 0, 1, 1, None),
	("Key dimensions verified",
	 "Every dimension called out on the drawing is measured and within its stated tolerance.",
	 "Pass/Fail", "Measure against the drawing", None, 0, 0, 1, 0, None),
	("Sleeves, conduits and penetrations placed and protected",
	 "Every penetration is where the drawing puts it, and is capped or sleeved against the pour.",
	 "Pass/Fail", "Visual against the drawing", None, 0, 0, 1, 1, None),
	("Reinforcement placed and tied as specified",
	 "Bar size, spacing, lap and cover match the structural drawing.",
	 "Pass/Fail", "Measure and inspect", None, 0, 0, 0, 1, None),
	("Buried pipework pressure tested and holding",
	 "Pipework holds the specified test pressure for the specified duration, with the gauge "
	 "witnessed before cover.",
	 "Pass/Fail", "Pressure test, witnessed", None, 0, 0, 1, 1, None),
	("Electrical rough-in complete and bonded",
	 "Conduit, boxes and bonding are in place and continuous before anything is covered.",
	 "Pass/Fail", "Visual and continuity check", None, 0, 0, 1, 0, None),
	("Everything that will be buried is photographed",
	 "Photographs cover the full extent of the work about to be concealed.",
	 "Document", "Photograph before cover", None, 0, 0, 1, 0, None),
]

BUILD_IN_PROCESS = [
	("Materials match the specification",
	 "Grades, finishes and thicknesses are the specified ones, with certificates where the "
	 "specification asks for them.",
	 "Pass/Fail", "Compare delivery records to the specification", None, 0, 0, 1, 0, None),
	("Welds and joints sound and finished",
	 "No porosity, undercut or unfinished weld on any visible or wetted joint.",
	 "Pass/Fail", "Visual", None, 0, 0, 1, 1, None),
	("Finishes free of damage, staining and tooling marks",
	 "Visible surfaces are as specified, with no damage from fabrication.",
	 "Pass/Fail", "Visual in good light", None, 0, 0, 0, 1, None),
	("Fit-up and alignment within tolerance",
	 "Joints, levels and reveals are within the tolerance the drawing states.",
	 "Pass/Fail", "Measure", None, 0, 0, 0, 0, None),
	("Fixings and supports as specified",
	 "Type, size, spacing and material of every fixing match the specification.",
	 "Pass/Fail", "Visual and measure", None, 0, 0, 0, 0, None),
	("Concealed work photographed before it is closed up",
	 "Photographs cover anything that will not be visible again.",
	 "Document", "Photograph before closing", None, 0, 0, 1, 0, None),
]

BUILD_FINAL = [
	("Every open punch item is closed",
	 "No Quality Action for this project is still open or awaiting re-verification.",
	 "Pass/Fail", "Check the project's open actions", None, 0, 0, 1, 0, None),
	("Finishes clean and undamaged at handover",
	 "Every visible surface is clean and free of damage caused during construction.",
	 "Pass/Fail", "Walk the feature with the client", None, 0, 0, 0, 1, None),
	("Feature running as designed, in the client's presence",
	 "The feature runs through a full cycle with the client watching, and behaves as designed.",
	 "Pass/Fail", "Run it in front of them", None, 0, 0, 1, 0, None),
	("Operation and maintenance documentation handed over",
	 "The client has the O&M pack, and the record says what was handed over and when.",
	 "Document", "Hand over and record", None, 0, 0, 1, 0, None),
	("Warranty terms explained and dated",
	 "What is covered, for how long, and from what date, explained and recorded.",
	 "Document", "Explain and record", None, 0, 0, 0, 0, None),
	("Client walked through startup, shutdown and routine care",
	 "Somebody on the client's side can start it, stop it and look after it.",
	 "Pass/Fail", "Demonstrate, then have them do it", None, 0, 0, 0, 0, None),
	("Client representative signed acceptance",
	 "A named person on the client's side has signed that they accept the feature.",
	 "Pass/Fail", "Signature on the inspection", None, 0, 0, 1, 0, None),
]

# ---------------------------------------------------------------------------
# Events — the set with NO internal source. Read this one hardest.
# ---------------------------------------------------------------------------

EVENTS_SETUP = [
	("Everything on the manifest is on site",
	 "Every item on the dispatch manifest is present and accounted for.",
	 "Document", "Count against the manifest", None, 0, 0, 1, 0, None),
	("Feature set up in the agreed position and orientation",
	 "Position and orientation match what the venue and client agreed.",
	 "Pass/Fail", "Compare to the site plan", None, 0, 0, 0, 1, None),
	("Power supply adequate and GFCI protected",
	 "The supply carries the load, and every circuit feeding the feature is GFCI protected and "
	 "tested.",
	 "Pass/Fail", "Meter the supply, push-to-test each device", None, 0, 0, 1, 0, None),
	("No trip hazards in any guest area",
	 "Every cable and hose in a guest area is matted, taped or routed out of the way.",
	 "Pass/Fail", "Walk the guest route", None, 0, 0, 1, 1, None),
	("Water supply and drainage arranged and tested",
	 "Fill and drain routes are agreed with the venue and have been run once.",
	 "Pass/Fail", "Fill and drain before guests arrive", None, 0, 0, 0, 0, None),
	("Feature run at full for a settling period with no leaks",
	 "Run at full output long enough to settle, with no loss at any joint or the vessel.",
	 "Pass/Fail", "Run and watch", None, 0, 0, 1, 1, None),
	("Lighting and any programmed sequence tested",
	 "Every fixture lights and the sequence runs as the client expects to see it.",
	 "Pass/Fail", "Test in shade or after dark", None, 0, 0, 0, 0, None),
	("Site left clean and guest-ready",
	 "No packaging, tools or standing water left in any guest area.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, None),
	("Client or venue representative has seen it running",
	 "A named person has seen the feature running and is content with it.",
	 "Pass/Fail", "Show them", None, 0, 0, 1, 0, None),
]

EVENTS_MID = [
	("Water level topped up",
	 "Level is back at the operating line after the first period of running.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 0, None),
	("Water clarity acceptable for guests",
	 "Water is clear enough to look intentional, with no film, foam or debris.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 1, None),
	("No leaks or overflow since setup",
	 "No water anywhere it should not be, and no wet patch spreading.",
	 "Pass/Fail", "Walk the perimeter", None, 0, 0, 1, 1, None),
	("Cables and barriers still in place and safe",
	 "Nothing has been moved, lifted or unplugged by guests or other trades.",
	 "Pass/Fail", "Walk the guest route", None, 0, 0, 1, 0, None),
	("Lighting still operating",
	 "Every fixture that worked at setup still works.",
	 "Pass/Fail", "Visual", None, 0, 0, 0, 0, None),
	("Debris cleared",
	 "Anything guests have dropped in has been removed.",
	 "Pass/Fail", "Net and check the skimmer", None, 0, 0, 0, 0, None),
]

EVENTS_TEARDOWN = [
	("Everything on the manifest is accounted for",
	 "Every item is either packed for return or recorded as missing.",
	 "Document", "Count against the manifest", None, 0, 0, 1, 0, None),
	("Condition of each returned item recorded",
	 "Each item has its condition recorded, not only its presence.",
	 "Pass/Fail", "Inspect as packed", None, 0, 0, 1, 1, None),
	("New damage photographed and attributed",
	 "Any damage not present at setup is photographed and its likely cause recorded.",
	 "Pass/Fail", "Photograph as found", None, 0, 0, 0, 1, None),
	("Feature drained and dried before packing",
	 "No standing water is packed into a case or trailer.",
	 "Pass/Fail", "Visual before closing cases", None, 0, 0, 1, 0, None),
	("Site left as found",
	 "No damage, staining or debris left behind, and anything moved has been put back.",
	 "Pass/Fail", "Walk the site with the venue", None, 0, 0, 0, 1, None),
	("Venue representative signed off on the state of the site",
	 "A named person at the venue agrees the site is as it was.",
	 "Pass/Fail", "Signature on the inspection", None, 0, 0, 0, 0, None),
]

#: (section_title, section_type, description, instructions, items)
DRAFT_SECTIONS = [
	("Condition on Arrival", "Visual Inspection",
	 "What was found before anybody touched it. Lifted from Sapphire's own maintenance sections.",
	 "<p>Do this <b>before</b> cleaning, dosing or switching anything. The point is what the "
	 "client has been living with, not what it looks like after you have fixed it.</p>",
	 SERVICE_ARRIVAL),
	("Water Chemistry (Service)", "Measurement",
	 "The four readings, with Sapphire's own ranges: pH 7.2-7.8, free chlorine 1.0-3.0 ppm, "
	 "ORP 650-750 mV, total alkalinity 80-120 ppm.",
	 "<p>Record what you measured, not what you expected. An out-of-range reading flags the "
	 "record automatically.</p>",
	 SERVICE_CHEMISTRY),
	("Safety and Electrical (Service)", "Safety Check",
	 "Electrical safety on a service visit. GFCI verification is mandatory, as it is in the "
	 "maintenance form this is taken from.",
	 "<p>If a GFCI will not trip on test, that is a failure and the feature should not be left "
	 "running on that circuit.</p>",
	 SERVICE_SAFETY),
	("Work Verification (Post-Service)", "Visual Inspection",
	 "What was actually done, verified rather than asserted. From Sapphire's pump, filter and "
	 "wrap-up sections.",
	 None, SERVICE_WORK_DONE),
	("Concept Against the Brief", "Document Review",
	 "Does the concept answer the brief and the site? Keyed to the Water Feature Design record.",
	 None, DESIGN_CONCEPT),
	("Design Development — Engineering", "Document Review",
	 "Does the developed design hold together technically? Every item asks whether the "
	 "engineering record already says what it needs to say.",
	 "<p>This gate is passed by reading the design record, not by re-deriving the calculation. "
	 "If the record does not answer an item, that is the finding.</p>",
	 DESIGN_DEVELOPMENT),
	("Final Design Sign-Off", "Document Review",
	 "Is it buildable, and is it what was sold?",
	 None, DESIGN_SIGNOFF),
	("Incoming Components (Controls)", "Visual Inspection",
	 "What arrived is what was ordered, and it is undamaged. Keyed to the Control Panel Design "
	 "record.",
	 None, PRODUCTS_INCOMING),
	("In-Process Wiring and Assembly", "Visual Inspection",
	 "Workmanship inside the panel, before it is closed.",
	 "<p>Everything here stops being inspectable once the enclosure is closed and the panel "
	 "ships. Photograph anything you are unsure about.</p>",
	 PRODUCTS_IN_PROCESS),
	("Pre-Shipment Functional Test", "Functional Test",
	 "It works on the bench before it leaves the building.",
	 "<p>Force every interlock rather than assuming it is wired. An interlock that has never "
	 "been tripped is an interlock nobody knows about.</p>",
	 PRODUCTS_PRE_SHIPMENT),
	("On-Site Commissioning (Controls)", "Functional Test",
	 "It works where it is going to live.",
	 None, PRODUCTS_COMMISSIONING),
	("Pre-Pour / Pre-Installation", "Visual Inspection",
	 "The last moment a mistake is cheap.",
	 "<p>Once this is poured or covered, every item here costs a demolition to check. "
	 "Photograph the full extent of what is about to disappear.</p>",
	 BUILD_PRE_POUR),
	("In-Process Fabrication", "Visual Inspection",
	 "Workmanship while it can still be seen and corrected.",
	 None, BUILD_IN_PROCESS),
	("Final Walkthrough / Turnover", "Visual Inspection",
	 "What the client is handed. Open punch items are carried into this inspection "
	 "automatically.",
	 "<p>Carried-forward items appear in this inspection marked <b>Re-check</b>. A Pass closes "
	 "them; a Fail reopens them with the priority escalated.</p>",
	 BUILD_FINAL),
	("Pre-Event Setup", "Visual Inspection",
	 "Before the client or their guests arrive. DRAFTED WITHOUT AN INTERNAL SOURCE — nothing in "
	 "this system describes an event setup, so read this set hardest.",
	 "<p>Guests will be near this feature and will not be supervised. Trip hazards and GFCI "
	 "protection are the two items worth stopping the setup over.</p>",
	 EVENTS_SETUP),
	("Mid-Event Spot Check", "Visual Inspection",
	 "Multi-day events only. DRAFTED WITHOUT AN INTERNAL SOURCE.",
	 None, EVENTS_MID),
	("Post-Event Teardown and Condition", "Visual Inspection",
	 "What came back, and in what state. DRAFTED WITHOUT AN INTERNAL SOURCE.",
	 "<p>The condition recorded here is what a damage conversation with the client will rest "
	 "on. Photograph anything that is not perfect.</p>",
	 EVENTS_TEARDOWN),
]

#: (template_name, project_type, milestone_key, [section titles], safety, wrapup)
DRAFT_TEMPLATES = [
	("Build — Pre-Pour / Pre-Installation", "Build", "build_pre_pour",
	 ["Pre-Pour / Pre-Installation"],
	 "<p>Open excavations, reinforcement and live rough-in. Nothing here is worth an injury: if "
	 "an excavation is not safe to enter, the inspection waits.</p>", None),
	("Build — In-Process Fabrication", "Build", "build_in_process",
	 ["In-Process Fabrication"], None, None),
	("Build — Final Walkthrough / Turnover", "Build", "build_final_walkthrough",
	 ["Final Walkthrough / Turnover"], None,
	 "<p>This is the inspection the client remembers. Do not sign around an open item to keep "
	 "the schedule.</p>"),
	("Design — Concept / Schematic Review", "Design", "design_concept_review",
	 ["Concept Against the Brief"], None, None),
	("Design — Design Development Review", "Design", "design_development_review",
	 ["Design Development — Engineering"], None, None),
	("Design — Final Sign-Off", "Design", "design_final_signoff",
	 ["Final Design Sign-Off"], None, None),
	("Products — Incoming Components", "Products", "products_incoming",
	 ["Incoming Components (Controls)"], None, None),
	("Products — In-Process Wiring & Assembly", "Products", "products_in_process",
	 ["In-Process Wiring and Assembly"], None, None),
	("Products — Pre-Shipment Functional Test", "Products", "products_pre_shipment",
	 ["Pre-Shipment Functional Test"],
	 "<p>The panel is energised for most of this. Treat it as live work.</p>", None),
	("Products — On-Site Commissioning", "Products", "products_commissioning",
	 ["On-Site Commissioning (Controls)"],
	 "<p>Live supply, water and people. Isolate before working inside the panel.</p>", None),
	("Events — Pre-Event Setup", "Events", "events_pre_setup",
	 ["Pre-Event Setup"],
	 "<p>Guests will be near this feature unsupervised. Trip hazards and GFCI protection are "
	 "the two items worth stopping a setup over.</p>", None),
	("Events — Mid-Event Spot Check", "Events", "events_mid_check",
	 ["Mid-Event Spot Check"], None, None),
	("Events — Post-Event Teardown", "Events", "events_teardown",
	 ["Post-Event Teardown and Condition"], None, None),
	("Service — Condition on Arrival", "Service", "service_pre_service",
	 ["Condition on Arrival", "Water Chemistry (Service)", "Safety and Electrical (Service)"],
	 None, None),
	("Service — Work Verification", "Service", "service_post_service",
	 ["Work Verification (Post-Service)", "Water Chemistry (Service)"], None, None),
	("Service — Recurring Interval Check", "Service", "service_interval_check",
	 ["Condition on Arrival", "Water Chemistry (Service)", "Safety and Electrical (Service)",
	  "Work Verification (Post-Service)"], None, None),
]

#: Sections with no source inside this system. Named so the review page and the tests can point
#: at them, rather than the fact living only in prose somebody may not read.
UNSOURCED_SECTIONS = (
	"Pre-Event Setup",
	"Mid-Event Spot Check",
	"Post-Event Teardown and Condition",
)
