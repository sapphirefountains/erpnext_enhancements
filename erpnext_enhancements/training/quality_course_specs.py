# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The four Quality course drafts — WI-075, the training that follows the build.

Each of these teaches a feature this programme shipped, and each was written **after** that
feature existed rather than alongside it: a course about a screen nobody can open is worthless,
and one written from a plan rather than from the built thing teaches the plan.

What these are, and what they are not
--------------------------------------

They are **Course Specs** — data, validated by
:mod:`~erpnext_enhancements.training.course_spec` and built by
``api/training_course_authoring.author_course_from_spec``, the same reviewed path the AI drafting
tool uses. Nothing here creates a document directly, so every rule that path enforces applies:
the course is created ``Draft``, its version is unpublished, and **every quiz question is stamped
``ai_generated`` with no reviewer**, which the publish gate refuses to let through until a person
has read it.

They teach **how this software behaves and why**, which is knowable from the code. They do not
assert Sapphire policy — how soon an inspection must happen, who may sign what off, what a
reasonable rework rate is — because inventing that is the same error as inventing a checklist,
and the strawman checklists in sub-phase I were only acceptable because they were labelled and
seeded Draft. Where a course reaches the edge of what the code decides, it says *your supervisor
decides* rather than filling in a number.

The safety property, and why it matters more than the plan expected
--------------------------------------------------------------------

The plan assumed the training module was dormant: *"Training Settings ships dormant
(training_enabled = 0, auto_assign_enabled = 0). Four Required courses assign nothing and mail
nobody while those are off."*

**That is no longer true.** Measured on production 2026-09-14, ``tabSingles`` holds
``training_enabled = 1``, ``auto_assign_enabled = 1`` and ``portal_enabled = 1``, against 14 live
courses, 278 lessons and 123 questions. Training is in real use.

So the dormancy the plan leaned on is gone, and the thing standing between these drafts and a
real assignment landing in a real technician's inbox is **``status = "Draft"``**.
``training/assignment.py`` selects on ``{"status": "Published", "weight": "Required",
"auto_assign": 1}`` — all three, so a Draft course assigns nobody no matter what the settings
say. Publishing one is the act of adopting it, it is a deliberate act by a named person, and it
will assign and email immediately. The seeding patch says so in as many words.
"""

#: Shown at the top of every course. The learner is told what they are reading before they read
#: it — the same courtesy the strawman checklists carry, and for the same reason.
DRAFT_NOTICE = (
	"<p><b>This course is a draft.</b> It was written from the software itself, by the same "
	"work that built the features it describes, and it has not yet been reviewed or adopted by "
	"anyone at Sapphire Fountains. Read it as a starting point to correct.</p>"
	"<p>It explains <i>how the system behaves</i>. Where a decision belongs to Sapphire rather "
	"than to the software — how quickly something must happen, who signs what off, what "
	"counts as acceptable — it says so instead of guessing.</p>"
)

#: Per-course assignment rules, applied by the seeding patch. They are recorded because they are
#: the design; they do nothing while the course is a Draft.
#:
#: ``Position`` values checked against production 2026-09-14. ``Technician`` is the job-family
#: group (tier 0) above Junior / Senior / Master, so one rule covers the family.
ASSIGNMENT_RULES = {
	"Running an inspection in the field": [
		("Position", "Technician", 30),
	],
	"Non-conformances and corrective actions, end to end": [
		("Position", "Project Manager", 30),
		("Position", "Operations Manager", 30),
		("Role", "Production Team", 45),
	],
	"Writing scope that can be inspected": [
		("Position", "Project Manager", 30),
		("Position", "Sales Representative", 30),
		("Role", "Sales Team", 45),
	],
	"Subcontractor agreements, purchase orders and rates": [
		("Position", "Project Manager", 30),
		("Position", "Purchasing Agent/Inventory Clerk", 30),
		("Position", "AP/AR, Purchasing Manager", 30),
	],
}

#: Courses that ask for a supervisor's signature as well as a pass mark. Only the field course:
#: it is a practical competency, and ``training/authority.py`` already knows who may sign.
REQUIRES_SIGNOFF = ("Running an inspection in the field",)


def _notice_block():
	return {
		"block_type": "Callout",
		"callout_tone": "Warning",
		"heading": "Draft — not yet adopted",
		"content": DRAFT_NOTICE,
	}


# =================================================================================================
# 1. Running an inspection in the field  (follows sub-phase H)
# =================================================================================================

FIELD_INSPECTION = {
	"course": {
		"course_title": "Running an inspection in the field",
		"summary": (
			"Open an inspection on a phone, record what you actually see, and finish it so the "
			"record stands up later. Covers the frozen checklist, measurements, photos and "
			"sign-off."
		),
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "Before you start", "description": "What an inspection is and where it comes from."},
		{"title": "On site", "description": "Working through the wizard."},
		{"title": "Finishing", "description": "Sign-off, and what happens next."},
	],
	"lessons": [
		{
			"lesson_title": "What an inspection is, and why the list cannot change under you",
			"chapter": 0,
			"estimated_minutes": 8,
			"summary": "Where the checklist comes from, and the one property that makes it evidence.",
			"blocks": [
				_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "It is a copy, not a link",
					"content": (
						"<p>When an inspection is generated, every check on it is <b>copied</b> "
						"onto the record. It is not a live link back to the template.</p>"
						"<p>That matters more than it sounds. If somebody edits the master "
						"template tomorrow — tightens a tolerance, reworders a check, adds a "
						"row — <b>your open inspection does not change</b>. What you were "
						"asked is what you were asked.</p>"
						"<p>The alternative would be an inspection that quietly becomes a "
						"different test between the day you started it and the day somebody "
						"reads it. That is not a record of anything.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Where the checks come from",
					"content": (
						"<p>Two sources, merged when the inspection is generated:</p>"
						"<ul>"
						"<li><b>The master template</b> for this milestone — the standard "
						"checks for this kind of work.</li>"
						"<li><b>The project's own acceptance criteria</b>, from its locked Scope "
						"of Work and from any change order. These appear as an addendum, so you "
						"are inspecting what was actually sold on this job.</li>"
						"</ul>"
						"<p>Plus any corrective action carried forward from a previous "
						"inspection, which is re-checked here.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "Why the addendum exists",
					"content": (
						"<p>The whole programme runs on one idea: <i>if a criterion cannot be "
						"inspected it should not be in the contract, and if an inspection item "
						"does not trace back to something contracted, question why it exists.</i> "
						"The addendum is the second half of that, made real.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Someone edits the master template while your inspection is open. What happens to your inspection?",
						"type": "Single Choice",
						"explanation": "The checks were copied onto the record when it was generated. A later template edit cannot reach it.",
						"options": [
							{"text": "Nothing — it keeps the checks it was generated with", "is_correct": True},
							{"text": "It updates to the new version automatically", "is_correct": False},
							{"text": "It is cancelled and you start again", "is_correct": False},
							{"text": "Only unanswered rows update", "is_correct": False},
						],
					},
					{
						"question": "Why does an inspection carry the project's own acceptance criteria as well as the standard checks?",
						"type": "Single Choice",
						"explanation": "So the inspection covers what was actually contracted on this job, not only the generic checks.",
						"options": [
							{"text": "So what was sold on this job actually gets inspected", "is_correct": True},
							{"text": "To make the inspection longer", "is_correct": False},
							{"text": "Because the template is usually incomplete", "is_correct": False},
							{"text": "To give the customer something to sign", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Recording what you see",
			"chapter": 1,
			"estimated_minutes": 10,
			"summary": "Outcomes, measurements, photos, and what the wizard will not let you do.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Three outcomes",
					"content": (
						"<p>Every check gets <b>Pass</b>, <b>Fail</b> or <b>N/A</b>.</p>"
						"<p><b>N/A is a real answer</b>, not a way to skip something. Use it when "
						"the check genuinely does not apply to this installation — a feature "
						"that was not built, equipment that is not fitted. If it applies and you "
						"could not check it, say so in the notes and raise it; a row left as N/A "
						"because you ran out of light is a check nobody ever did.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Measurements",
					"content": (
						"<p>A measurement check wants the <b>number you actually read</b>, in the "
						"unit shown. The system compares it to the range and tells you whether it "
						"is inside.</p>"
						"<p>Record the reading even when it fails. A failed measurement with the "
						"value beside it is evidence; a Fail with no number is an opinion.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "You cannot edit the standard",
					"content": (
						"<p>The wizard lets you write the outcome, the measured value, notes and "
						"a photo. <b>That is all.</b> You cannot change the acceptance range, the "
						"method or the wording of a check, and you cannot add a row.</p>"
						"<p>This is deliberate. A screen that could widen a tolerance could turn "
						"a failing reading into a passing one from a phone, on site, with nobody "
						"watching.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you leave a row",
					"items": [
						"The outcome matches what you actually saw",
						"Any measurement is the real reading, in the right unit",
						"A photo is attached where the check asks for one",
						"Notes explain anything a reader would otherwise have to guess at",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A measurement is outside its range. What do you record?",
						"type": "Single Choice",
						"explanation": "Record the real reading and mark it failed. The number is what makes the failure evidence.",
						"options": [
							{"text": "The actual reading, and Fail", "is_correct": True},
							{"text": "Fail, with the value left blank", "is_correct": False},
							{"text": "The nearest in-range value, and a note", "is_correct": False},
							{"text": "N/A, and tell the project manager", "is_correct": False},
						],
					},
					{
						"question": "Which of these can you change from the inspection wizard?",
						"type": "Single Choice",
						"explanation": "Outcome, measured value, notes and photo only. The standard itself is frozen.",
						"options": [
							{"text": "The outcome, the measured value, notes and a photo", "is_correct": True},
							{"text": "The acceptance range, if it is obviously wrong", "is_correct": False},
							{"text": "The wording of a check", "is_correct": False},
							{"text": "Anything, until the inspection is submitted", "is_correct": False},
						],
					},
					{
						"question": "When is N/A the right answer?",
						"type": "Single Choice",
						"explanation": "N/A means the check does not apply here. It is not a way to skip something you could not get to.",
						"options": [
							{"text": "When the check genuinely does not apply to this installation", "is_correct": True},
							{"text": "When you could not reach the equipment", "is_correct": False},
							{"text": "When you are not sure how to test it", "is_correct": False},
							{"text": "When the customer says not to worry about it", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Working offline, and not losing your work",
			"chapter": 1,
			"estimated_minutes": 6,
			"summary": "What the wizard does about a bad signal, and what it needs from you.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "It saves as you go",
					"content": (
						"<p>The wizard saves your answers as you work rather than only at the "
						"end, and it retries a save that fails. The save state is shown on screen "
						"— look at it before you walk away from a job.</p>"
						"<p>If you try to close the page with work that has not saved, it warns "
						"you. Do not dismiss that warning and leave site.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "Two people, one inspection",
					"content": (
						"<p>If somebody else changes the same inspection while you have it open, "
						"your save is <b>refused rather than merged</b>. You will be told, and "
						"you reload and re-enter. That is safer than a silent merge that keeps "
						"whichever answer arrived last.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Someone else edits the same inspection while you have it open. What happens to your save?",
						"type": "Single Choice",
						"explanation": "It is refused, not merged, and you are told. A silent merge would keep whichever answer happened to arrive last.",
						"options": [
							{"text": "It is refused and you are told to reload", "is_correct": True},
							{"text": "The two sets of answers are merged", "is_correct": False},
							{"text": "Yours wins, because you opened it first", "is_correct": False},
							{"text": "Nothing — the last save always wins", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Finishing, and what a failed check sets off",
			"chapter": 2,
			"estimated_minutes": 8,
			"summary": "Sign-off, and the chain a Fail starts.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Submitting locks it",
					"content": (
						"<p>Finishing an inspection submits it, and a submitted inspection cannot "
						"be edited. Check your answers before you finish — particularly any "
						"row you left N/A.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A Fail is not the end of it",
					"content": (
						"<p>A failed check raises a <b>non-conformance</b>, which gets a "
						"corrective action with an owner. That action is then <b>re-checked at "
						"the next inspection on this project</b> — automatically, whether or "
						"not anyone remembers.</p>"
						"<p>If the re-check fails, it reopens with its priority raised and comes "
						"round again. Nothing closes itself by being asserted fixed.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "If the system says you are not qualified",
					"content": (
						"<p>You may see a note saying your position does not meet what this "
						"template asks for. It is <b>advisory and never blocks you</b> — "
						"your supervisor is told, and the inspection is still recorded.</p>"
						"<p>That is deliberate: a hard block would stop the <i>record</i> of an "
						"inspection being made, not stop unqualified work happening.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A corrective action from your inspection is marked resolved. When is it verified?",
						"type": "Single Choice",
						"explanation": "It is carried onto the next inspection for that project and re-checked there. Marking it resolved is not the same as it being verified.",
						"options": [
							{"text": "It is re-checked at the next inspection on that project", "is_correct": True},
							{"text": "When the person who fixed it marks it resolved", "is_correct": False},
							{"text": "At the end of the job, in one batch", "is_correct": False},
							{"text": "Only if somebody asks for a re-inspection", "is_correct": False},
						],
					},
					{
						"question": "The system warns that your position does not meet the template's requirement. What happens?",
						"type": "Single Choice",
						"explanation": "The check is advisory: your supervisor is notified and the inspection is still recorded. Blocking would prevent the record, not the work.",
						"options": [
							{"text": "You are warned, a supervisor is told, and you carry on", "is_correct": True},
							{"text": "The inspection is blocked until someone qualified takes over", "is_correct": False},
							{"text": "The inspection is recorded but every row is marked N/A", "is_correct": False},
							{"text": "Nothing is recorded at all", "is_correct": False},
						],
					},
				]
			},
		},
	],
}


# =================================================================================================
# 2. Non-conformances and corrective actions  (follows sub-phase F)
# =================================================================================================

NCR_AND_ACTION = {
	"course": {
		"course_title": "Non-conformances and corrective actions, end to end",
		"summary": (
			"What a non-conformance records, how severity changes what happens, and how a "
			"corrective action gets verified rather than asserted. Includes punch-list items."
		),
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "Raising one", "description": "What it is and what it needs."},
		{"title": "Closing one", "description": "The lifecycle, and what verification means."},
	],
	"lessons": [
		{
			"lesson_title": "What a non-conformance is for",
			"chapter": 0,
			"estimated_minutes": 7,
			"summary": "The record, and what it must carry to be useful later.",
			"blocks": [
				_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "It names the standard, not just the problem",
					"content": (
						"<p>A non-conformance points back at <b>the exact check that failed</b> "
						"— the inspection, and the row on it. That link is what makes the "
						"difference between <i>the tile is wrong</i> and <i>the tile does not meet "
						"the flatness standard this job contracted, measured on this date, by "
						"this person</i>.</p>"
						"<p>It can also name the <b>subcontractor</b> responsible, which is what "
						"lets a scorecard say anything about them later.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "What a good one carries",
					"panels": [
						{"title": "The project", "body": "Which job this happened on."},
						{"title": "The inspection and the row", "body": "So the standard it failed against is unambiguous."},
						{"title": "Severity", "body": "Minor, Major or Critical — see the next lesson."},
						{"title": "Who is responsible", "body": "Sapphire, a subcontractor, the customer, or a third party. Naming a subcontractor is what feeds their scorecard."},
						{"title": "A corrective action", "body": "With an owner and a due date. Without one, nothing happens next."},
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does a non-conformance link back to the inspection row that failed?",
						"type": "Single Choice",
						"explanation": "The link makes the standard it failed against unambiguous, which is what makes the record defensible later.",
						"options": [
							{"text": "So the standard it failed against is unambiguous later", "is_correct": True},
							{"text": "So the inspection can be reopened", "is_correct": False},
							{"text": "So the row can be edited to match", "is_correct": False},
							{"text": "It is only for reporting counts", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Severity, and what Critical sets off",
			"chapter": 0,
			"estimated_minutes": 7,
			"summary": "Three levels, and the one that pages people.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Critical is not just a bigger Major",
					"content": (
						"<p>Marking a non-conformance <b>Critical</b> starts an alert: the "
						"project manager, the production manager and the president are each "
						"emailed, and each one has to <b>acknowledge individually</b>.</p>"
						"<p>Acknowledgement is per person and is recorded on the record. One "
						"person cannot acknowledge on behalf of the others, and the system "
						"re-sends to anyone still silent until they answer.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "Acknowledge in the system, not by replying",
					"content": (
						"<p>The email links to the record rather than carrying a one-click "
						"button. That is on purpose — a link in an email can be opened by a "
						"mail scanner or a forwarded copy, and the timestamp would then say "
						"somebody read it when a security appliance opened it.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A Critical non-conformance is raised. Who has to acknowledge it?",
						"type": "Single Choice",
						"explanation": "Each recipient acknowledges for themselves, and the record tracks who is still silent.",
						"options": [
							{"text": "Each notified person separately, on the record", "is_correct": True},
							{"text": "The project manager, on behalf of everyone", "is_correct": False},
							{"text": "Whoever raised it", "is_correct": False},
							{"text": "Nobody — the email is the record", "is_correct": False},
						],
					},
					{
						"question": "Why does the alert email link to the record instead of carrying a one-click acknowledge button?",
						"type": "Single Choice",
						"explanation": "A link in an email can be opened by a scanner or a forwarded copy, which would stamp an acknowledgement nobody made.",
						"options": [
							{"text": "So a mail scanner cannot acknowledge on somebody's behalf", "is_correct": True},
							{"text": "Because one-click links are harder to build", "is_correct": False},
							{"text": "To make people log in more often", "is_correct": False},
							{"text": "Because email cannot carry links", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "The lifecycle, and why 'resolved' is not 'closed'",
			"chapter": 1,
			"estimated_minutes": 9,
			"summary": "Five states, and the one transition nobody can make by asserting it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Open, In Progress, PM Resolved, Verified at Next Inspection, Closed",
					"content": (
						"<p>The state that matters is <b>PM Resolved</b>. It means somebody says "
						"it is fixed. It does not mean anybody has looked.</p>"
						"<p>An action in that state is <b>carried onto the next inspection for "
						"that project automatically</b> and re-checked. Pass, and it closes. "
						"Fail, and it goes back to In Progress with its <b>priority raised one "
						"step</b>, and it carries forward again.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "This is the whole point",
					"content": (
						"<p>The failure this replaces is the one where somebody marks a defect "
						"fixed, nobody checks, and it is found again at handover — by the "
						"customer.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "State check",
					"cards": [
						{"front": "PM Resolved", "back": "Somebody says it is fixed. Nobody has verified it yet."},
						{"front": "Verified at Next Inspection", "back": "Re-checked on a later inspection and passed."},
						{"front": "A failed re-check", "back": "Back to In Progress, priority raised one step, carried forward again."},
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What does PM Resolved mean?",
						"type": "Single Choice",
						"explanation": "It records a claim that the work is done. Verification happens at the next inspection.",
						"options": [
							{"text": "Somebody says it is fixed; nobody has verified it", "is_correct": True},
							{"text": "It has been checked and passed", "is_correct": False},
							{"text": "The project manager has closed it", "is_correct": False},
							{"text": "It is waiting for the customer", "is_correct": False},
						],
					},
					{
						"question": "A carried-forward action fails its re-check. What happens?",
						"type": "Single Choice",
						"explanation": "It reopens with its priority raised a step and is carried forward again. It cannot be closed by assertion.",
						"options": [
							{"text": "It reopens with higher priority and carries forward again", "is_correct": True},
							{"text": "It closes with a note that it failed", "is_correct": False},
							{"text": "A second action is raised and the first closes", "is_correct": False},
							{"text": "It stays PM Resolved until someone intervenes", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Punch-list items",
			"chapter": 1,
			"estimated_minutes": 5,
			"summary": "Same record, one flag, and why that was the right choice.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A punch item is a corrective action with a flag",
					"content": (
						"<p>Punch-list items are not a separate kind of record. They are "
						"corrective actions with a <b>punch list</b> flag, which means they "
						"inherit carry-forward and re-verification for free.</p>"
						"<p>A separate list would have needed its own chasing, its own "
						"re-checking and its own way of going stale. There is also a "
						"<b>client visible</b> flag, so the customer-facing list is a filter "
						"rather than a second list that can disagree with the first.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "How is a punch-list item stored?",
						"type": "Single Choice",
						"explanation": "It is a corrective action carrying a flag, so it inherits carry-forward and re-verification.",
						"options": [
							{"text": "As a corrective action with a punch-list flag", "is_correct": True},
							{"text": "As its own separate punch-list record", "is_correct": False},
							{"text": "As a task on the project", "is_correct": False},
							{"text": "As a note on the final inspection", "is_correct": False},
						],
					},
				]
			},
		},
	],
}


# =================================================================================================
# 3. Writing scope that can be inspected  (follows sub-phase B)
# =================================================================================================

INSPECTABLE_SCOPE = {
	"course": {
		"course_title": "Writing scope that can be inspected",
		"summary": (
			"Turn what we sold into acceptance criteria somebody can actually check: a "
			"verification method, a pass standard, and a hold point where it matters."
		),
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "The problem", "description": "Why this exists."},
		{"title": "Writing one", "description": "What a criterion needs."},
		{"title": "Locking it", "description": "Approval, and changing it afterwards."},
	],
	"lessons": [
		{
			"lesson_title": "Four places, and no single record",
			"chapter": 0,
			"estimated_minutes": 6,
			"summary": "The dispute this is designed to prevent.",
			"blocks": [
				_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "Where scope used to live",
					"content": (
						"<p>Scope, contract terms, pricing and inspection standards have "
						"historically lived in <b>four separate places</b>. When they disagree "
						"there is no single record either side can point at — and that is "
						"when an argument becomes expensive.</p>"
						"<p>The worked example is a blasting subcontractor: labour hours invoiced "
						"with no documented cap, no verification method, and no measurable "
						"acceptance standard tied back to the original scope. Every one of those "
						"three gaps is a field on an acceptance criterion now.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "The rule that follows",
					"content": (
						"<p><b>If a criterion cannot be inspected, it should not be in the "
						"contract. If an inspection item does not trace back to something "
						"contracted, question why it exists.</b></p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What was missing from the blasting subcontractor's scope?",
						"type": "Multiple Choice",
						"explanation": "All three: no documented cap, no verification method, and no measurable acceptance standard tied back to the scope.",
						"options": [
							{"text": "A documented cap on hours", "is_correct": True},
							{"text": "A verification method", "is_correct": True},
							{"text": "A measurable acceptance standard", "is_correct": True},
							{"text": "A signed purchase order", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "What makes a criterion inspectable",
			"chapter": 1,
			"estimated_minutes": 10,
			"summary": "The three fields that turn a sentence into a test.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A method and a standard, not an adjective",
					"content": (
						"<p>Every acceptance criterion carries a <b>verification method</b> "
						"— visual, measurement, test, document, third-party or customer "
						"sign-off — and a <b>pass standard</b>, which is what a person "
						"compares against.</p>"
						"<p>'Finished to a high standard' is not a criterion: two people will "
						"read it two ways, and neither is wrong. 'Joint width 3mm ± 1mm, "
						"measured at five points per elevation' is one.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Rewrite these",
					"cards": [
						{"front": "Tile laid neatly", "back": "Measurement: lippage under 1mm across any joint, checked with a straightedge at five points per elevation."},
						{"front": "Water feature runs properly", "back": "Test: flow at design GPM at commissioning, with nozzle pattern matching the approved drawing."},
						{"front": "Site left clean", "back": "Visual: no construction debris within the work area, photographed at handover."},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The test to apply",
					"content": (
						"<p>Ask: <b>could two people who disagree both check this and reach the "
						"same answer?</b> If not, it is not written yet.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Which is an inspectable acceptance criterion?",
						"type": "Single Choice",
						"explanation": "It names a method and a standard that two people would measure the same way.",
						"options": [
							{"text": "Lippage under 1mm across any joint, checked at five points per elevation", "is_correct": True},
							{"text": "Tiling finished to a high standard", "is_correct": False},
							{"text": "Workmanship to industry norms", "is_correct": False},
							{"text": "Customer satisfied with the finish", "is_correct": False},
						],
					},
					{
						"question": "What is the test for whether a criterion is written well enough?",
						"type": "Single Choice",
						"explanation": "If two people who disagree would reach different answers, the criterion has not settled anything.",
						"options": [
							{"text": "Two people who disagree would still reach the same answer", "is_correct": True},
							{"text": "It is under one sentence long", "is_correct": False},
							{"text": "The customer has signed it", "is_correct": False},
							{"text": "It uses a technical term", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Hold points, and what they cost",
			"chapter": 1,
			"estimated_minutes": 6,
			"summary": "The criteria that stop work, and why you should be sparing.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A hold point stops the job",
					"content": (
						"<p>Marking a criterion as a <b>hold point</b> says work cannot proceed "
						"past it until it has been checked — a pre-pour inspection is the "
						"obvious case, because once the concrete is in, the thing you wanted to "
						"look at is gone.</p>"
						"<p>Hold points appear on the printed Statement of Work, with the "
						"standard each is accepted against. That is a commitment to the "
						"customer as much as an instruction to the crew.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "Be sparing",
					"content": (
						"<p>A hold point costs schedule. Use them where the evidence disappears "
						"if you do not look now, not as a way of marking something important.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What makes something a good candidate for a hold point?",
						"type": "Single Choice",
						"explanation": "Hold points cost schedule, so they earn their place where the evidence is destroyed by the next step.",
						"options": [
							{"text": "The evidence is gone once the next step happens", "is_correct": True},
							{"text": "It is the most expensive part of the job", "is_correct": False},
							{"text": "The customer mentioned it more than once", "is_correct": False},
							{"text": "It is hard to do", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Locking it, and changing it afterwards",
			"chapter": 2,
			"estimated_minutes": 7,
			"summary": "Submit is the lock, and a change order is the only way in afterwards.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Submitting the Scope of Work locks it",
					"content": (
						"<p>A Scope of Work is drafted, approved, and then <b>submitted</b>. "
						"Submitting is the lock: after that the criteria are what the contract "
						"and the inspections both read from.</p>"
						"<p>Each criterion gets a permanent key when it is created. Reordering "
						"the list, inserting a row or rewording one never repoints anything that "
						"already referred to it — so a closed non-conformance cannot quietly "
						"end up attached to a different standard.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "After the lock: change orders",
					"content": (
						"<p>Scope added later goes on a <b>change order</b>, and criteria added "
						"there are inspected exactly like the original ones — they appear on "
						"the relevant inspection under a heading naming the change order.</p>"
						"<p>That closes the gap this programme exists to close: scope sold after "
						"the lock, checked by nothing, found at the walkthrough.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does each acceptance criterion carry a permanent key?",
						"type": "Single Choice",
						"explanation": "So reordering or rewording the list never repoints a record that already referred to a criterion.",
						"options": [
							{"text": "So reordering the list never repoints an existing record at a different standard", "is_correct": True},
							{"text": "So criteria can be sorted alphabetically", "is_correct": False},
							{"text": "To number them for the customer", "is_correct": False},
							{"text": "To stop duplicates being created", "is_correct": False},
						],
					},
					{
						"question": "Scope is added after the Scope of Work is locked. How does it get inspected?",
						"type": "Single Choice",
						"explanation": "Criteria added on a submitted change order are merged onto inspections under a heading naming that change order.",
						"options": [
							{"text": "Via a change order — its criteria appear on inspections too", "is_correct": True},
							{"text": "It does not; it is checked at handover", "is_correct": False},
							{"text": "The Scope of Work is unlocked and edited", "is_correct": False},
							{"text": "The inspector adds a row on site", "is_correct": False},
						],
					},
				]
			},
		},
	],
}


# =================================================================================================
# 4. Subcontractor agreements, purchase orders and rates  (follows sub-phase L)
# =================================================================================================

SUBCONTRACTOR_AGREEMENTS = {
	"course": {
		"course_title": "Subcontractor agreements, purchase orders and rates",
		"summary": (
			"What a master agreement has to say, why a signed rate is a snapshot, how a purchase "
			"order says what it was placed under, and what a scorecard can and cannot tell you."
		),
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "The agreement", "description": "Signed, and still in force."},
		{"title": "Buying under it", "description": "Purchase orders and rates."},
		{"title": "Looking back", "description": "Scorecards."},
	],
	"lessons": [
		{
			"lesson_title": "Signed is not the same as in force",
			"chapter": 0,
			"estimated_minutes": 8,
			"summary": "The question the old gate never asked.",
			"blocks": [
				_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "What the gate used to check",
					"content": (
						"<p>A Statement of Work has always needed a <b>Signed</b> master "
						"agreement with that subcontractor. That check asks whether the agreement "
						"was ever signed. It never asked whether it is <b>still in force</b>.</p>"
						"<p>An agreement signed in 2019, with superseded rates and lapsed "
						"insurance, passed that gate exactly as well as one signed last week.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Three answers, not two",
					"content": (
						"<p>An agreement is now <b>in force</b>, <b>expired</b>, or "
						"<b>unknown</b> — and unknown is a real third answer, not a polite "
						"way of saying expired.</p>"
						"<p>Most agreements have no expiry date recorded. Treating that as expired "
						"would block work for a gap in our paperwork; treating it as valid would "
						"be a check that passes forever and finds nothing. So unknown is "
						"<b>reported and never blocks</b>, and only a known, past expiry can "
						"refuse.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "What to do about an unknown",
					"content": (
						"<p>Fill in the term or the expiry date on the agreement. That is the "
						"whole fix, and it turns a permanent unknown into something the renewal "
						"reminder can act on — 60 days' notice, escalating inside 14.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "An agreement has no expiry date recorded. What does the system do?",
						"type": "Single Choice",
						"explanation": "Unknown is reported but never blocks. Only a known, past expiry can refuse a Statement of Work.",
						"options": [
							{"text": "Reports it as unknown, and does not block", "is_correct": True},
							{"text": "Treats it as expired and blocks the Statement of Work", "is_correct": False},
							{"text": "Treats it as valid and says nothing", "is_correct": False},
							{"text": "Sets an expiry twelve months from today", "is_correct": False},
						],
					},
					{
						"question": "What did the original gate check?",
						"type": "Single Choice",
						"explanation": "It asked only whether an agreement had ever been signed, not whether it was still current.",
						"options": [
							{"text": "Only that an agreement had been signed at some point", "is_correct": True},
							{"text": "That the agreement was signed and unexpired", "is_correct": False},
							{"text": "That insurance was current", "is_correct": False},
							{"text": "That rates matched the schedule", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Rates, and why a signed one never changes",
			"chapter": 1,
			"estimated_minutes": 8,
			"summary": "The snapshot, and what drift means.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A Statement of Work freezes its rates",
					"content": (
						"<p>The rates on a Statement of Work are a <b>snapshot</b>, taken the day "
						"it was issued. They are never re-read from the agreement afterwards.</p>"
						"<p>The reason is simple: a signed agreement prints its own copy. If "
						"rates were read live, a document somebody already signed would quietly "
						"say something different tomorrow — and 'the rate we agreed' versus "
						"'the rate the schedule says today' is exactly what a dispute is "
						"about.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Drift is reported, never applied",
					"content": (
						"<p>If a Statement of Work's rate differs from what the agreement's rate "
						"schedule currently says, you are told on save. Nothing is rewritten. A "
						"person decides what to do about it.</p>"
						"<p>One thing that is deliberately <b>not</b> flagged: a rate the "
						"agreement never published at all. Plenty of Statements of Work carry a "
						"negotiated figure that was never on the schedule, and calling that a "
						"discrepancy would make the check noise on its first day.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A rate on a signed Statement of Work differs from the agreement's current schedule. What happens?",
						"type": "Single Choice",
						"explanation": "Drift is reported for a person to decide about. The snapshot is never rewritten.",
						"options": [
							{"text": "You are told; nothing is changed automatically", "is_correct": True},
							{"text": "The Statement of Work updates to the current rate", "is_correct": False},
							{"text": "The agreement updates to match the Statement of Work", "is_correct": False},
							{"text": "The Statement of Work is cancelled", "is_correct": False},
						],
					},
					{
						"question": "Why are a Statement of Work's rates never re-read from the agreement?",
						"type": "Single Choice",
						"explanation": "A signed document that silently changes is worthless as evidence of what was agreed.",
						"options": [
							{"text": "Because a signed document must not change after signing", "is_correct": True},
							{"text": "Because the agreement is usually out of date", "is_correct": False},
							{"text": "To make the page load faster", "is_correct": False},
							{"text": "Because rates are not stored on the agreement", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "The purchase order is the work order",
			"chapter": 1,
			"estimated_minutes": 7,
			"summary": "Naming what an order was placed under, and the budget category it spends.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "There is no separate work order",
					"content": (
						"<p>When we instruct a subcontractor to do defined work for money, the "
						"instrument is a <b>purchase order</b>. It can now name the master "
						"agreement, the Statement of Work, the Scope of Work and the change order "
						"it was placed under.</p>"
						"<p>If the supplier has a signed agreement and the order does not name "
						"it, you get an advisory warning. It does not stop you — refusing to "
						"save would stop somebody buying materials, and an order placed outside an "
						"agreement is a commercial problem to correct rather than an emergency to "
						"prevent.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Budget category",
					"content": (
						"<p>A purchase order line can also name the <b>budget category</b> it "
						"spends against. It is optional, because you may not know the "
						"classification at the moment you order.</p>"
						"<p>A line with no category is reported as <b>unclassified</b> project "
						"spend rather than being pushed into a category nobody chose. Naming it "
						"is how a project budget's committed column becomes real.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "You place a purchase order with a subcontractor who has a signed agreement, but do not name it. What happens?",
						"type": "Single Choice",
						"explanation": "It is advisory. Blocking would stop somebody buying materials over a paperwork gap.",
						"options": [
							{"text": "You are warned, and the order still saves", "is_correct": True},
							{"text": "The order is blocked until you name it", "is_correct": False},
							{"text": "The agreement is attached automatically", "is_correct": False},
							{"text": "Nothing at all", "is_correct": False},
						],
					},
					{
						"question": "A purchase order line names no budget category. How is that money reported?",
						"type": "Single Choice",
						"explanation": "It is reported as unclassified rather than assigned to a category nobody chose.",
						"options": [
							{"text": "As unclassified project spend", "is_correct": True},
							{"text": "Spread evenly across the project's categories", "is_correct": False},
							{"text": "Assigned to Materials by default", "is_correct": False},
							{"text": "Left out of the project total", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Reading a scorecard honestly",
			"chapter": 2,
			"estimated_minutes": 7,
			"summary": "What the number means, and when there is no number at all.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Always read the coverage beside the score",
					"content": (
						"<p>A subcontractor scorecard reports several measures, and each one "
						"carries a <b>coverage</b> verdict saying whether the figure beside it "
						"means anything:</p>"
						"<ul>"
						"<li><b>Tracked</b> — the source is in use, so a zero means nothing "
						"was found.</li>"
						"<li><b>Not Tracked</b> — the source holds no records at all, so a "
						"zero means <i>nobody records this</i>.</li>"
						"<li><b>No Source</b> — nothing in the system could record it. "
						"Rework hours and certificates of insurance are both in this state "
						"today.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A scorecard with no score is not a good score",
					"content": (
						"<p>When nothing can be judged, the scorecard reads <b>Not Measurable</b> "
						"and has no score at all — not zero, and not a hundred.</p>"
						"<p>This is the state most scorecards will be in until the quality chain "
						"is in daily use. Do not take one into a negotiation as evidence a "
						"subcontractor performed well. It is evidence that we have not been "
						"measuring.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "If a score is wrong, say so on the record",
					"content": (
						"<p>A scorecard takes a <b>manual adjustment</b>, and an adjustment "
						"cannot be saved without a reason. Your name and the time are recorded "
						"against it.</p>"
						"<p>That exists because the first time a score is wrong and there is no "
						"way to say so, people stop using the record.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A scorecard measure reads 0 with coverage Not Tracked. What does that mean?",
						"type": "Single Choice",
						"explanation": "Not Tracked means the source holds no records at all, so the zero is about the instrument, not the subcontractor.",
						"options": [
							{"text": "Nobody records this — it says nothing about the subcontractor", "is_correct": True},
							{"text": "The subcontractor had no problems in this area", "is_correct": False},
							{"text": "The measure was manually zeroed", "is_correct": False},
							{"text": "The subcontractor scored badly", "is_correct": False},
						],
					},
					{
						"question": "A scorecard reads Not Measurable. What can you conclude about the subcontractor?",
						"type": "Single Choice",
						"explanation": "Nothing. It means we have not been measuring, not that they performed well or badly.",
						"options": [
							{"text": "Nothing — it means we have not been measuring", "is_correct": True},
							{"text": "They performed perfectly", "is_correct": False},
							{"text": "They performed badly", "is_correct": False},
							{"text": "They did no work in the period", "is_correct": False},
						],
					},
					{
						"question": "You believe a scorecard is wrong. What does the system require?",
						"type": "Single Choice",
						"explanation": "An adjustment needs a reason, and your name and the time are recorded with it.",
						"options": [
							{"text": "A manual adjustment with a written reason, signed", "is_correct": True},
							{"text": "Deleting the scorecard and rebuilding it", "is_correct": False},
							{"text": "Editing the measures directly", "is_correct": False},
							{"text": "Nothing — scorecards cannot be changed", "is_correct": False},
						],
					},
				]
			},
		},
	],
}


#: Every course, in the order the patch creates them.
COURSES = (
	FIELD_INSPECTION,
	NCR_AND_ACTION,
	INSPECTABLE_SCOPE,
	SUBCONTRACTOR_AGREEMENTS,
)


def course_titles():
	"""The four titles, used by the patch to decide what already exists."""
	return tuple(spec["course"]["course_title"] for spec in COURSES)
