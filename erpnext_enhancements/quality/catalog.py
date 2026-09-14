"""The inspection catalog as data — no ``frappe``, so CI can read it.

Sub-phase C seeds these through patches, and sub-phase D will read them again when it generates
an inspection from a milestone. They live here rather than inside the patch modules for the
reason that governs every split in this module: ``import frappe`` at module scope puts a value
out of reach of the bench-free test tier, and there is no Frappe integration-test job in CI, so
a constant nobody can import is a constant nobody checks.

What is pinned about these values is in ``tests/test_inspection_milestones.py``. The one worth
knowing: the Build and Products triggers name values of the existing
``Project.custom_build_status`` Select, so **renaming one of those options silently stops the
trigger matching** — and a trigger that matches nothing looks exactly like a milestone that has
not come round yet.
"""

#: (project_type, milestone_key, title, sequence, gate_kind, trigger_basis, trigger_value,
#:  interval_days, multi_day_only, description)
MILESTONES = [
	# -- Build -------------------------------------------------------------------------
	("Build", "build_pre_pour", "Pre-Pour / Pre-Installation", 10, "Physical Checklist",
	 "Build Status", "Procurement", 0, 0,
	 "Before anything is buried or poured. The last moment a mistake is cheap."),
	("Build", "build_in_process", "In-Process Fabrication", 20, "Physical Checklist",
	 "Build Status", "Assembly", 0, 0,
	 "Workmanship while it can still be seen and corrected."),
	("Build", "build_pre_final", "Pre-Final (Systems Startup)", 30, "Physical Checklist",
	 "Build Status", "QA", 0, 0,
	 "Commissioning: fill, leak, flow, electrical and nozzle pattern. The moment of truth for a "
	 "water feature, and the measure of first-pass yield."),
	("Build", "build_final_walkthrough", "Final Walkthrough / Turnover", 40, "Physical Checklist",
	 "Build Status", "Ready for Install", 0, 0,
	 "What the client is handed. Open punch-list items are carried into this inspection."),
	# -- Design ------------------------------------------------------------------------
	("Design", "design_concept_review", "Concept / Schematic Review", 10, "Review Gate",
	 "Manual", None, 0, 0, "Does the concept answer the brief and the site?"),
	("Design", "design_development_review", "Design Development Review", 20, "Review Gate",
	 "Manual", None, 0, 0, "Does the developed design hold together technically?"),
	("Design", "design_final_signoff", "Final Design Sign-Off", 30, "Review Gate",
	 "Manual", None, 0, 0, "Is this buildable, and is it what was sold?"),
	# -- Products (Controls Fab) --------------------------------------------------------
	("Products", "products_incoming", "Incoming Components", 10, "Physical Checklist",
	 "Build Status", "Procurement", 0, 0, "What arrived is what was ordered, and it is undamaged."),
	("Products", "products_in_process", "In-Process Wiring & Assembly", 20, "Physical Checklist",
	 "Build Status", "Assembly", 0, 0, "Workmanship inside the panel, before it is closed."),
	("Products", "products_pre_shipment", "Pre-Shipment Functional Test", 30, "Physical Checklist",
	 "Build Status", "QA", 0, 0, "It works on the bench before it leaves the building."),
	("Products", "products_commissioning", "On-Site Commissioning", 40, "Physical Checklist",
	 "Build Status", "Installed", 0, 0, "It works where it is going to live."),
	# -- Events ------------------------------------------------------------------------
	("Events", "events_pre_setup", "Pre-Event Setup Complete", 10, "Physical Checklist",
	 "Manual", None, 0, 0, "Before the client or their guests arrive."),
	("Events", "events_mid_check", "Mid-Event Spot Check", 20, "Physical Checklist",
	 "Manual", None, 0, 1, "Multi-day events only. Nothing to check mid-event on a one-day job."),
	("Events", "events_teardown", "Post-Event Teardown / Condition Check", 30, "Physical Checklist",
	 "Manual", None, 0, 0, "What came back, and in what state."),
	# -- Service (the spec's "Maintenance") ---------------------------------------------
	("Service", "service_pre_service", "Pre-Service (Condition on Arrival)", 10, "Physical Checklist",
	 "Manual", None, 0, 0, "What was found, before anybody touched it."),
	("Service", "service_post_service", "Post-Service (Work Verification)", 20, "Physical Checklist",
	 "Manual", None, 0, 0, "What was done, verified rather than asserted."),
	("Service", "service_interval_check", "Recurring Interval Check", 30, "Physical Checklist",
	 "Calendar Interval", None, 90, 0,
	 "Calendar-driven rather than project-stage driven, so it needs its own scheduling — which "
	 "sub-phase I owns. Ninety days is a placeholder recorded so the intent is not lost."),
]


#: (label, acceptance_criteria, check_type, method, uom, requires_photo)
COMMISSIONING_CHECKS = [
	(
		"Basin fills to design water line and holds",
		"Reaches the design water line with no measurable drop over the fill-and-hold period.",
		"Pass/Fail", "Visual against the marked water line", None, 1,
	),
	(
		"Leak test",
		"No visible loss at joints, penetrations or the vessel over the hold period, and no "
		"make-up water demand beyond evaporation.",
		"Pass/Fail", "Visual and make-up water observation", None, 1,
	),
	(
		"Flow verification",
		"Measured flow is within the design range for the feature.",
		"Measurement", "Flow meter or timed-volume check", "Gallon", 0,
	),
	(
		"Electrical and GFCI",
		"Every circuit energises, and each GFCI trips on test and resets. No nuisance trips "
		"through a full run cycle.",
		"Pass/Fail", "Push-to-test on each device, then a full run cycle", None, 0,
	),
	(
		"Nozzle pattern",
		"Every nozzle throws the specified pattern and height, with no clogged, misaimed or "
		"dead heads.",
		"Pass/Fail", "Visual against the design intent, feature running", None, 1,
	),
	(
		"Light function",
		"Every fixture lights, holds its colour and aim, and follows the programmed sequence.",
		"Pass/Fail", "Visual after dark or with the vault covered", None, 1,
	),
]

COMMISSIONING_INSTRUCTIONS = (
	"<p>Run the feature as the client will run it, not as a test rig. Fill fully, let it settle, "
	"then work down the list with the water moving.</p>"
	"<p><b>A fail here is not a snag — it is the thing this inspection exists to catch.</b> "
	"Record what you measured rather than what you expected, photograph anything you fail, and "
	"do not sign off around a failure to keep the schedule.</p>"
)


#: Names the Commissioning seed uses. Here rather than in the patch so a test can read them
#: without importing frappe, and so sub-phase D can find the template it is meant to generate
#: from without a second copy of the string.
COMMISSIONING_SECTION_TITLE = "Commissioning"
COMMISSIONING_TEMPLATE_NAME = "Build — Pre-Final (Systems Startup)"
COMMISSIONING_MILESTONE_KEY = "build_pre_final"
