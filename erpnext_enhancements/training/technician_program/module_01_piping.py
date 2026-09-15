# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 1 — Piping & Hydraulics."""

from erpnext_enhancements.training.technician_program._common import ask_block, notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 1 — Piping & Hydraulics",
		"summary": (
			"Make a solvent-weld joint that holds, put pipe in the ground so it stays where you "
			"put it, set a pitch you can prove, test a system without hurting anybody, and seal a "
			"penetration through a wall."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "Making a joint that holds", "description": "Cutting, chamfering and solvent welding."},
		{"title": "Putting pipe in the ground", "description": "Bedding, backfill and pitch."},
		{"title": "Proving it and sealing it", "description": "Hydrostatic testing and penetrations."},
	],
	"lessons": [
		{
			"lesson_title": "PVC cutting, chamfering and solvent welding",
			"chapter": 0,
			"estimated_minutes": 15,
			"summary": "Why a solvent weld is not glue, and the preparation steps people skip.",
			"blocks": [
				notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "It is a weld, not a glue",
					"content": (
						"<p>Solvent cement does not stick two pieces of pipe together. It "
						"<b>softens the surface of both</b> so the plastic of the pipe and the "
						"plastic of the fitting flow into each other, and when the solvent leaves, "
						"what is left is one piece of PVC.</p>"
						"<p>Everything else in this lesson follows from that one fact:</p>"
						"<ul>"
						"<li><b>The two surfaces have to touch.</b> Cement is not a filler. A "
						"joint with a gap in it has nothing to fuse across.</li>"
						"<li><b>Both surfaces have to be softened.</b> Cement on the pipe only, or "
						"on the fitting only, welds on one side.</li>"
						"<li><b>The surfaces have to be clean and dry.</b> Dirt, oil and water all "
						"sit between the two plastics and stop the weld happening there.</li>"
						"<li><b>The solvent has to leave.</b> That takes time, and it takes longer "
						"when it is cold, when the pipe is big, and when the joint will be "
						"pressurised.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cut square, deburr inside, chamfer outside",
					"content": (
						"<p><b>Square.</b> A PVC socket is tapered — it grips at the mouth before "
						"the pipe reaches the bottom. A pipe cut out of square bottoms on one side "
						"and leaves a wedge-shaped void on the other. Use a wheel cutter or a saw "
						"in a mitre box; a freehand hacksaw cut is out of square more often than "
						"it is not.</p>"
						"<p><b>Deburr the inside.</b> The ridge a cutter leaves inside the pipe is "
						"a permanent turbulence generator in a system somebody spent a day sizing "
						"for low friction loss. Take it off.</p>"
						"<p><b>Chamfer the outside.</b> Bevel the outside edge of the pipe end — a "
						"shallow angle, roughly 10° to 15°, a small distance back from the end. "
						"This is the step that gets skipped, and it is the one that causes the "
						"failure you cannot see.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "What a missing chamfer actually does",
					"content": (
						"<p>A square, unchamfered pipe end is a scraper. As it goes into the socket "
						"it <b>pushes the cement ahead of it</b>, wipes the fitting dry, and piles "
						"all of that cement up at the bottom of the socket.</p>"
						"<p>The result is a joint with a thick plug of cement at the base, a dry "
						"band where the seal actually needs to be, and a perfect-looking bead "
						"around the outside. It holds through the test. It fails later, "
						"underground, under a slab.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The order of operations",
					"content": (
						"<p><b>Dry fit first.</b> The pipe should enter the socket part way and "
						"stop — an interference fit. If it drops straight to the bottom loose, that "
						"pipe and that fitting are not a pair; get another one.</p>"
						"<p><b>Clean and dry both surfaces.</b> Wipe the mud, the shavings and the "
						"water off. On a wet day this is most of the job.</p>"
						"<p><b>Primer.</b> Primer is a solvent that softens the surface before the "
						"cement gets there. Most jurisdictions require it on PVC pressure pipe and "
						"inspect for the purple dye, because the dye is the only way anybody can "
						"tell afterwards that you used it. One-step cements exist; whether one is "
						"acceptable on your job is a code and specification question, not a "
						"preference.</p>"
						"<p><b>Cement both surfaces</b> — pipe first, then the fitting socket. "
						"<b>Insert with a quarter turn</b> to spread it, push to the bottom of the "
						"socket, and <b>hold</b>.</p>"
						"<p><b>Hold it.</b> The taper pushes back. Let go early and the pipe walks "
						"part way out of a socket you cannot see into. Hold until it stops pushing, "
						"then wipe the excess bead.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Set time is not cure time",
					"content": (
						"<p>These are two different numbers on the same can, and they answer two "
						"different questions.</p>"
						"<p><b>Set time</b> — how long before you can handle the joint without "
						"disturbing it.</p>"
						"<p><b>Cure time</b> — how long before the joint can take pressure. This is "
						"the one that matters before a hydrostatic test, and it is dramatically "
						"longer than set time. It grows with pipe diameter, it grows as the "
						"temperature drops, and it grows with the test pressure.</p>"
						"<p>The can carries a table with all three of those on it. <b>Read the "
						"table for the size, the temperature and the pressure you actually "
						"have</b>, not the shortest number printed on it.</p>"
					),
				},
				ask_block(
					"Which cement, and which primer",
					"<p>PVC, CPVC and ABS all take <b>different</b> cements, and they are not "
					"interchangeable. There are also cements formulated for wet conditions, for "
					"cold weather and for large diameters, and there is a maximum pipe size on "
					"every can.</p>"
					"<p>The can tells you which materials it is rated for, which sizes, and at "
					"which temperatures. If what is on the truck is not rated for the joint in "
					"front of you, that is a stop-and-ask, not a judgement call.</p>",
				),
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Solvent cement is a chemical, and it burns",
					"content": (
						"<p>Primer and cement are <b>flammable</b>, and the vapour is heavier than "
						"air, so it pools in a trench, a vault and a pit. Ventilate, and keep "
						"ignition sources — grinders, torches, any hot work — away from where you "
						"are welding pipe.</p>"
						"<p>The vapour is also a respiratory irritant. In a confined or poorly "
						"ventilated space that stops being a nuisance and becomes the hazard. "
						"Gloves, eye protection, and read the safety data sheet for what is "
						"actually in your hand.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you walk away from a joint",
					"items": [
						"The cut was square and the outside edge is chamfered",
						"The inside burr is gone",
						"Both surfaces were clean and dry when the cement went on",
						"Primer was used where the job requires it, and you can see the dye",
						"Cement went on the pipe AND the socket",
						"You held it until it stopped pushing back",
						"The excess bead is wiped and the joint shows a continuous ring of cement",
						"Nothing gets pressure until the cure time on the can has passed",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is the outside edge of a cut pipe end chamfered before it goes into a fitting?",
						"type": "Single Choice",
						"explanation": (
							"A square edge acts as a scraper: it pushes cement ahead of it, wiping the socket dry "
							"where the seal is needed and piling the cement at the bottom."
						),
						"options": [
							{
								"text": "So the pipe does not scrape the cement out of the socket as it goes in",
								"is_correct": True,
							},
							{"text": "So the pipe slides in more easily and saves time", "is_correct": False},
							{
								"text": "So there is somewhere for the excess cement to collect",
								"is_correct": False,
							},
							{
								"text": "So the joint can be taken apart later if it needs to be",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A joint has been made. Which number on the can tells you when it can be pressure tested?",
						"type": "Single Choice",
						"explanation": (
							"Set time is when you can handle it. Cure time is when it can take pressure, and it is "
							"much longer — longer again for big pipe, cold weather and high test pressure."
						),
						"options": [
							{
								"text": "The cure time, for that pipe size, temperature and pressure",
								"is_correct": True,
							},
							{"text": "The set time", "is_correct": False},
							{"text": "The shortest time printed anywhere on the can", "is_correct": False},
							{
								"text": "Either — set and cure are two names for the same thing",
								"is_correct": False,
							},
						],
					},
					{
						"question": "During a dry fit the pipe drops straight to the bottom of the socket with no resistance. What does that tell you?",
						"type": "Single Choice",
						"explanation": (
							"A PVC socket is tapered and should grip before the pipe bottoms. A loose dry fit means "
							"there is a gap to weld across, and cement is not a gap filler."
						),
						"options": [
							{
								"text": "That pipe and fitting are not a matched pair — get another one",
								"is_correct": True,
							},
							{"text": "It is fine; the cement will fill the gap", "is_correct": False},
							{
								"text": "It is ideal, because it will be easy to push home",
								"is_correct": False,
							},
							{"text": "Use extra primer to swell the pipe", "is_correct": False},
						],
					},
					{
						"question": "Why hold the joint after pushing the pipe home?",
						"type": "Single Choice",
						"explanation": (
							"The taper pushes back. Let go early and the pipe walks out of a socket you cannot see "
							"into, leaving a partially engaged joint that looks finished from outside."
						),
						"options": [
							{
								"text": "The tapered socket pushes the pipe back out until the cement grabs",
								"is_correct": True,
							},
							{"text": "To squeeze out as much cement as possible", "is_correct": False},
							{
								"text": "To warm the joint with your hands so it cures faster",
								"is_correct": False,
							},
							{
								"text": "There is no reason to hold it; the quarter turn is what matters",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Underground piping and bedding procedures",
			"chapter": 1,
			"estimated_minutes": 14,
			"summary": "What actually holds buried pipe up, and the two things you cannot backfill over.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The bedding carries the pipe, not the pipe",
					"content": (
						"<p>Buried plastic pipe is a <b>flexible</b> pipe. It is not designed to "
						"span anything or to carry the soil above it on its own — it deflects under "
						"load, pushes out sideways, and the <b>soil beside it</b> pushes back. That "
						"side support is what actually carries the load.</p>"
						"<p>So the trench is part of the pipe system. A perfect weld sitting on a "
						"bad bed is a failure waiting for the first truck to drive over it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Continuous, uniform support",
					"content": (
						"<p>The pipe wants <b>continuous</b> support along its whole length and "
						"<b>uniform</b> support around the bottom of it. Two things break that:</p>"
						"<ul>"
						"<li><b>Point loads.</b> A rock, a lump of clay, a piece of the old system "
						"left in the trench. The pipe bridges across it and concentrates everything "
						"above onto that one spot.</li>"
						"<li><b>Voids.</b> Bell holes that were never filled, an unbedded section "
						"under a fitting, a washed-out pocket. The pipe spans the gap and "
						"bends.</li>"
						"</ul>"
						"<p>Bedding material is specified for this job: granular, free of rock, and "
						"placed and compacted in <b>lifts</b> — several shallow layers, each "
						"compacted — rather than one dump of spoil and a wheel over the top. "
						"Haunching, the material worked in under the bottom curve of the pipe on "
						"each side, is the part people leave out, and it is the part providing the "
						"side support.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Two things you cannot backfill over",
					"content": (
						"<p><b>An uncured joint.</b> Backfill puts load on the pipe. If the solvent "
						"weld has not reached its cure time, that load is acting on a joint that is "
						"still soft.</p>"
						"<p><b>An untested run.</b> Once the trench is closed, finding a leak costs "
						"a day and a machine. Test before you cover.</p>"
						"<p>Both of those pressures come from the schedule, and the schedule is "
						"somebody else's problem to solve rather than yours to absorb. Say it out "
						"loud.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Two things a trench does that have nothing to do with pipe",
					"content": (
						"<p><b>It can bury you.</b> Trench and excavation work has its own OSHA "
						"standard and its own protective systems — sloping, benching, shielding — "
						"and a competent person has to decide which applies. A trench that looks "
						"stable is the one people die in. Module 9 covers the rule; the point here "
						"is that <i>getting into a trench is not a plumbing decision</i>.</p>"
						"<p><b>Something else is already down there.</b> Utility locates are "
						"required before you dig, they expire, and they mark an approximate "
						"position — you still expose by hand within the tolerance zone. Hitting a "
						"gas line or a primary electrical feed is not a bad day, it is a "
						"fatality.</p>"
					),
				},
				ask_block(
					"Depth, bedding material and thrust restraint are specified",
					"<p>How deep the pipe goes, what the bedding and backfill materials are, how "
					"thick the lifts are and what compaction is required all come from the "
					"project's civil drawings, the local code, and the frost depth where you are "
					"standing. Same for <b>thrust restraint</b> at bends and tees on a pressurised "
					"line — thrust blocks and restrained joints are an engineered detail with a "
					"size on it.</p>"
					"<p>None of those is a figure this course can give you. If the drawing does not "
					"say, that is a question for the project manager before the trench is open, "
					"not after.</p>",
				),
				{
					"block_type": "Checklist",
					"heading": "Before backfill goes in",
					"items": [
						"Utility locates are current, and anything in the tolerance zone was exposed by hand",
						"Trench protection is in place and a competent person decided what it is",
						"The trench bottom is free of rock, debris and old material",
						"Bedding is placed and the pipe has continuous support along its length",
						"Bell holes are filled and the haunches are worked in on both sides",
						"Every joint has passed its cure time",
						"The run has been pressure tested, or the test is scheduled before cover",
						"Thrust restraint at bends and tees matches the detail on the drawing",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What actually carries the load over a buried flexible pipe?",
						"type": "Single Choice",
						"explanation": (
							"A flexible pipe deflects and pushes outward; the compacted soil at its sides pushes "
							"back. That side support is the load path, which is why haunching matters."
						),
						"options": [
							{
								"text": "The compacted soil beside the pipe, pushing back as the pipe deflects",
								"is_correct": True,
							},
							{"text": "The wall thickness of the pipe alone", "is_correct": False},
							{"text": "The solvent-weld joints, which stiffen the run", "is_correct": False},
							{"text": "The trench walls, which arch over the pipe", "is_correct": False},
						],
					},
					{
						"question": "A rock is left under a run of buried pipe. What is the problem?",
						"type": "Single Choice",
						"explanation": (
							"The pipe bridges the rock, so everything above is concentrated onto one point instead "
							"of being spread along a continuous bed."
						),
						"options": [
							{
								"text": "The pipe bridges it, turning the load above into a point load",
								"is_correct": True,
							},
							{
								"text": "It will slowly abrade a hole through the pipe wall",
								"is_correct": False,
							},
							{
								"text": "Nothing, as long as the pipe is not touching it directly",
								"is_correct": False,
							},
							{
								"text": "It makes the trench harder to compact but does not affect the pipe",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The schedule is tight and the crew wants to backfill a run welded an hour ago. What is the objection?",
						"type": "Single Choice",
						"explanation": (
							"Backfill loads the pipe. Loading a joint before its cure time is loading a joint that "
							"is still soft — and then it is buried."
						),
						"options": [
							{
								"text": "Backfill loads joints that have not reached cure time",
								"is_correct": True,
							},
							{
								"text": "None — cover protects fresh joints from temperature swings",
								"is_correct": False,
							},
							{"text": "The cement needs daylight to finish curing", "is_correct": False},
							{"text": "Only the pressure-test schedule is affected", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Precision pitch and laser level operation",
			"chapter": 1,
			"estimated_minutes": 12,
			"summary": "Setting a fall you can prove, and the mistake that puts a laser's error into the pipe.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Pitch is what makes a system drainable",
					"content": (
						"<p>A drain line that is level in places does not drain in those places. It "
						"holds water, and held water is what freezes and splits a line, grows "
						"biofilm, and makes winterisation impossible to finish.</p>"
						"<p>The fall is not a nice-to-have on a fountain. Almost everything Module "
						"7 does at the end of a season depends on the system emptying where it is "
						"supposed to empty.</p>"
						"<p>How much fall, and in which direction, comes from the drawing. What "
						"this lesson is about is <b>holding it</b> — because the usual reason a "
						"line has a belly in it is not that somebody chose the wrong grade, it is "
						"that the grade moved between setting it and backfilling it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "How a rotary laser is actually used",
					"content": (
						"<p>A laser gives you one thing: a <b>reference plane</b>. Everything else "
						"is arithmetic you do with a receiver on a grade rod.</p>"
						"<ol>"
						"<li>Set the instrument on firm ground where it can see the whole run, and "
						"let it self-level.</li>"
						"<li>Take a reading at a <b>known</b> point — a benchmark, or an invert the "
						"drawing gives you.</li>"
						"<li>That reading plus that known elevation gives you the height of the "
						"laser plane.</li>"
						"<li>Every other point is then the plane height minus your rod reading "
						"there.</li>"
						"</ol>"
						"<p>Read the pipe at the <b>invert</b> — the inside bottom — unless the "
						"drawing says otherwise, and be consistent. Mixing invert and top-of-pipe "
						"readings on one run is how a fall becomes a rise.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A self-levelling laser is not a correct laser",
					"content": (
						"<p>Self-levelling means it will put out a flat plane. It does not mean that "
						"plane is where you think it is. A laser knocked out of calibration — "
						"dropped, rattled in a truck, cooked in a hot vehicle — puts out a "
						"beautifully flat plane at the wrong angle. Every point you shoot from it "
						"is then wrong by a <i>different</i> amount, which is exactly the error "
						"that survives a check of any single point.</p>"
						"<p><b>Field-check it</b> against something that is not the laser: a known "
						"benchmark, a second instrument, a two-peg test. And tripods settle — in "
						"soft ground, in the sun, after somebody walks past. Re-shoot your reference "
						"before you trust a reading taken an hour after you set up.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Setting grade so it survives backfill",
					"content": (
						"<p>Pitch set on a bed of fluff is pitch until the first compactor pass. "
						"Support the pipe on the compacted bed itself, not on loose spoil and not on "
						"blocks you intend to pull out.</p>"
						"<p>Check the grade again <b>after</b> the haunches are worked in, and again "
						"before the trench closes. The cheapest time to find a belly is while the "
						"pipe is still visible; the most expensive is on commissioning day in front "
						"of the client.</p>"
					),
				},
				ask_block(
					"The fall comes from the drawing",
					"<p>Required slopes are set by code and by the engineer, and they differ by line "
					"type — a sanitary drain, a basin drain, a suction line and a conduit run are "
					"four different answers.</p>"
					"<p>Read it off the plan and the specification for <i>this</i> job. A number you "
					"remember from the last one is a number you are about to bury.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a section of level pipe in a drain line a real problem rather than just imperfect?",
						"type": "Single Choice",
						"explanation": (
							"It holds water. Held water freezes and splits lines, grows biofilm, and makes it "
							"impossible to fully drain the system for winter."
						),
						"options": [
							{
								"text": "It holds water, which freezes, fouls, and defeats winterisation",
								"is_correct": True,
							},
							{"text": "It slows flow but drains fully given time", "is_correct": False},
							{"text": "It only matters on lines carrying solids", "is_correct": False},
							{
								"text": "It increases pump head slightly, and nothing else",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A self-levelling rotary laser has been knocked out of calibration. What does that look like in the field?",
						"type": "Single Choice",
						"explanation": (
							"It still produces a perfectly flat plane — just a tilted one. Every point is wrong by "
							"a different amount, which is why checking one point proves nothing."
						),
						"options": [
							{
								"text": "A flat but tilted plane, so every reading is wrong by a different amount",
								"is_correct": True,
							},
							{"text": "The laser refuses to level and flashes an error", "is_correct": False},
							{
								"text": "Readings that are all wrong by the same fixed amount",
								"is_correct": False,
							},
							{"text": "A visibly wobbling beam", "is_correct": False},
						],
					},
					{
						"question": "When should the grade of a run be re-checked?",
						"type": "Multiple Choice",
						"explanation": (
							"Grade moves. It moves when the haunches are compacted and it moves as the trench is "
							"closed, and the pipe is only visible before that."
						),
						"options": [
							{"text": "After the haunching and bedding are compacted", "is_correct": True},
							{"text": "Immediately before the trench is closed", "is_correct": True},
							{"text": "Only once, when the pipe is first set", "is_correct": False},
							{"text": "After the system has been commissioned", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Hydrostatic pressure testing",
			"chapter": 2,
			"estimated_minutes": 14,
			"summary": "Why the test uses water, what a pressure drop is really telling you, and the mistake that kills.",
			"blocks": [
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Never pressure test plastic pipe with air",
					"content": (
						"<p>This is the most important sentence in this module.</p>"
						"<p>Water is effectively <b>incompressible</b>. A water-filled line at "
						"pressure holds almost no stored energy — when it fails it squirts and the "
						"pressure is gone. Air is compressible, and a line full of compressed air is "
						"a <b>spring</b>. When plastic pipe fails under air it does not leak, it "
						"<b>shatters</b>, and it throws fragments at lethal speed.</p>"
						"<p>People have been killed doing this. Not injured — killed. If somebody "
						"suggests putting a compressor on a PVC line to find a leak faster, the "
						"answer is no, and it is not a negotiation.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What the test is actually for",
					"content": (
						"<p>A hydrostatic test asks one question: <i>does this system hold "
						"pressure?</i> It is not a strength test, and it is not a substitute for "
						"having built it correctly. It is the check that finds the joint you were "
						"unsure about while the trench is still open and while it is still an "
						"hour's work to fix.</p>"
						"<p>The test pressure and the hold duration come from the specification and "
						"the code — commonly some multiple of the system's working pressure, held "
						"for a stated time. Both numbers live on the job, not in this course.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Running it",
					"content": (
						"<ol>"
						"<li><b>Every joint has passed its cure time</b>, at the temperature it "
						"actually cured at.</li>"
						"<li><b>Fill slowly from the low point</b> and let the air out at the high "
						"points. Trapped air is both a safety problem and a measurement "
						"problem.</li>"
						"<li><b>Isolate what should not be in the test.</b> Pumps, filters, heaters, "
						"gauges and valves all have their own pressure ratings, and many are far "
						"below the test pressure.</li>"
						"<li><b>Bring the pressure up gradually</b>, and keep people clear while it "
						"rises.</li>"
						"<li><b>Hold, and record</b> — start pressure, time, end pressure, ambient "
						"temperature.</li>"
						"</ol>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A pressure drop is not automatically a leak",
					"content": (
						"<p>Three things move the needle, and only one of them is a hole:</p>"
						"<ul>"
						"<li><b>Trapped air</b> compressing and dissolving. This is the usual reason "
						"a new line loses pressure for the first few minutes and then settles.</li>"
						"<li><b>Temperature.</b> Water expands and contracts, and so does the pipe. "
						"A line pressurised in the sun and read in the shade will have moved. This "
						"is why you record the temperature.</li>"
						"<li><b>An actual leak.</b></li>"
						"</ul>"
						"<p>The way to tell them apart is that a leak does not stop. Bleed the air "
						"properly, let the temperature settle, then read. And a drop on the gauge "
						"with <b>no water visible anywhere</b> is worth taking more seriously, not "
						"less — on a buried line, the water has somewhere to go.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before the pressure goes up",
					"items": [
						"Water, never air",
						"Every joint is past cure time for its size and the temperature it cured at",
						"Equipment that cannot take the test pressure is isolated or removed",
						"High points are vented and the line is genuinely full",
						"Nobody is standing over a joint, a cap, or a thrust point",
						"You have the test pressure and hold time from the specification, not from memory",
						"You have somewhere to write down start pressure, time, end pressure and temperature",
					],
				},
				ask_block(
					"Test pressure, duration and acceptance are specified",
					"<p>What pressure, for how long, and how much loss — if any — is acceptable are "
					"set by the project specification and the governing code, and they differ "
					"between a buried supply line, a basin, and a fire line.</p>"
					"<p>Get them from the documents for this job. Guessing a test pressure is how "
					"equipment gets destroyed, and how a system that failed gets signed off.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why must plastic pipe never be pressure tested with compressed air?",
						"type": "Single Choice",
						"explanation": (
							"Compressed air stores energy. Plastic pipe failing under air shatters and throws "
							"fragments at lethal speed; water is incompressible and simply squirts."
						),
						"options": [
							{
								"text": "Compressed air stores energy, so a failure shatters the pipe and throws fragments",
								"is_correct": True,
							},
							{
								"text": "Air leaks past joints that would hold water, so the test fails falsely",
								"is_correct": False,
							},
							{"text": "Air dries the solvent weld and weakens it", "is_correct": False},
							{
								"text": "It is acceptable as long as the pressure stays low",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A new line loses pressure over the first few minutes, then holds steady. What is the most likely cause?",
						"type": "Single Choice",
						"explanation": (
							"Trapped air compressing and dissolving. A real leak does not stop — that is how you "
							"tell them apart."
						),
						"options": [
							{"text": "Trapped air that was not fully vented", "is_correct": True},
							{"text": "A leak that has partially sealed itself", "is_correct": False},
							{"text": "The solvent welds finishing their cure", "is_correct": False},
							{"text": "The gauge settling in", "is_correct": False},
						],
					},
					{
						"question": "Why is ambient temperature recorded with the test readings?",
						"type": "Single Choice",
						"explanation": (
							"Water and pipe both expand and contract with temperature, so a line pressurised in "
							"sun and read in shade shows a pressure change that is not a leak."
						),
						"options": [
							{
								"text": "Temperature change moves the pressure without any water being lost",
								"is_correct": True,
							},
							{
								"text": "Cold water is denser and weighs more, changing the reading",
								"is_correct": False,
							},
							{
								"text": "It is only needed for records, not for interpreting the test",
								"is_correct": False,
							},
							{"text": "Because solvent cement cures faster when warm", "is_correct": False},
						],
					},
					{
						"question": "Which of these must be isolated or removed before a hydrostatic test?",
						"type": "Multiple Choice",
						"explanation": (
							"Equipment is routinely rated far below typical test pressures. Anything that cannot "
							"take the test pressure has to be out of the test."
						),
						"options": [
							{"text": "Pumps and filters", "is_correct": True},
							{"text": "Heaters and chillers", "is_correct": True},
							{"text": "Gauges rated below the test pressure", "is_correct": True},
							{"text": "The buried pipe run itself", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Pipe penetration sealing (Link-Seals)",
			"chapter": 2,
			"estimated_minutes": 10,
			"summary": "How a modular mechanical seal works, and why the annular space is a measurement.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "What it is, and why it beats grout",
					"content": (
						"<p>A modular mechanical seal is a chain of rubber links with bolts through "
						"them. It goes into the <b>annular space</b> — the gap between the pipe and "
						"the sleeve or cored hole it passes through — and tightening the bolts "
						"squeezes the rubber outward until it presses hard against both the pipe "
						"and the wall.</p>"
						"<p>It beats packing the gap with grout or sealant for three reasons: it "
						"<b>stays elastic</b>, so it tolerates the pipe moving, the structure "
						"settling and everything expanding in the heat; it seals against "
						"<b>hydrostatic pressure</b> from the wet side; and it can be taken apart "
						"and reused if the pipe ever has to come out.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The whole job is the annular space",
					"content": (
						"<p>The seal is sized for one specific combination: <b>the outside diameter "
						"of the pipe</b> and <b>the inside diameter of the hole</b>. Get either "
						"wrong and the right number of links will not do the job.</p>"
						"<p>Which means the sizing question is answered by <i>measuring</i>, not by "
						"naming pipe sizes. A 4-inch pipe is not 4 inches across the outside, and it "
						"is a different outside diameter in ductile iron, steel, PVC and copper. A "
						"cored hole is not the same diameter as a sleeve of the same nominal size. "
						"Measure both, then select.</p>"
						"<p>The manufacturer's table turns those two diameters into a model and a "
						"number of links. That table is the authority.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Torque is a specification, not a feel",
					"content": (
						"<p>These seals work by controlled compression, and the manufacturer "
						"publishes a torque figure. Under-tighten and it weeps. <b>Over-tighten and "
						"you damage the rubber, distort the pressure plates, and in thin-walled pipe "
						"you can deform the pipe itself</b> — and the result can look tight while "
						"still failing.</p>"
						"<p>Use a torque wrench, work around the ring gradually rather than "
						"finishing one bolt at a time, and use the figure for the model in your "
						"hands. Many of these need re-torquing after a settling period; the "
						"instructions say whether yours does.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Surfaces, and which way round it goes",
					"content": (
						"<p>Both sealing surfaces have to be <b>clean, sound and reasonably "
						"smooth</b>. A cored hole with a ragged lip, a sleeve with concrete "
						"splatter, or a pipe with a coating that is peeling all give the rubber "
						"somewhere to bypass.</p>"
						"<p>Position matters too: the seal sits within the wall thickness rather "
						"than proud of it, and on a penetration with a wet side and a dry side it "
						"goes where the instructions put it relative to the water. Guessing the "
						"orientation is a genuine failure mode.</p>"
					),
				},
				ask_block(
					"Fire, smoke and specified assemblies",
					"<p>A penetration through a rated wall or floor is not a plumbing detail. It is "
					"part of a <b>tested assembly</b>, and only a seal listed for that assembly may "
					"be used there.</p>"
					"<p>If the drawing calls a penetration rated, the model, the sleeve and the "
					"installation are all prescribed. That is an engineering and code question — "
					"take it to the project manager rather than substituting what is on the "
					"truck.</p>",
				),
				{
					"block_type": "Flashcards",
					"heading": "Terms worth having straight",
					"cards": [
						{
							"front": "Annular space",
							"back": "The gap between the outside of the pipe and the inside of the sleeve or cored hole. Sizing the seal means measuring both diameters.",
						},
						{
							"front": "Sleeve",
							"back": "A pipe cast or set into the wall that the carrier pipe passes through. Its actual inside diameter, not its nominal size, is what you measure.",
						},
						{
							"front": "Carrier pipe",
							"back": "The pipe actually carrying water through the penetration — the thing the seal grips on the inside.",
						},
						{
							"front": "Hydrostatic seal",
							"back": "A seal that holds against standing water pressure, not just splash. That is what a modular seal is for.",
						},
						{
							"front": "Re-torque",
							"back": "Tightening again after an initial settling period, where the manufacturer calls for it. Skipping it produces a slow weep.",
						},
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What two measurements select a modular mechanical seal?",
						"type": "Single Choice",
						"explanation": (
							"The pipe's actual outside diameter and the hole or sleeve's actual inside diameter. "
							"Nominal sizes do not answer it — outside diameter differs by pipe material."
						),
						"options": [
							{
								"text": "The actual outside diameter of the pipe and the actual inside diameter of the hole",
								"is_correct": True,
							},
							{"text": "The nominal pipe size and the wall thickness", "is_correct": False},
							{"text": "The pipe size and the system's working pressure", "is_correct": False},
							{
								"text": "The wall thickness and the number of links available",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What happens if the bolts on a modular seal are torqued well past the specified figure?",
						"type": "Single Choice",
						"explanation": (
							"Over-compression damages the rubber and can distort the plates or the pipe itself — "
							"and the result can look tight while still failing."
						),
						"options": [
							{
								"text": "The rubber and plates can be damaged and thin-wall pipe deformed, and it may still look tight",
								"is_correct": True,
							},
							{
								"text": "Nothing — more compression is always a better seal",
								"is_correct": False,
							},
							{
								"text": "The seal becomes permanent and cannot be removed, but seals well",
								"is_correct": False,
							},
							{
								"text": "The bolts shear off, which is how you know to stop",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is a modular seal preferred over packing the gap with grout on a fountain penetration?",
						"type": "Multiple Choice",
						"explanation": (
							"It stays elastic so it tolerates movement, it is rated against standing water "
							"pressure, and it can be taken apart if the pipe has to come out."
						),
						"options": [
							{
								"text": "It stays elastic and tolerates movement and settlement",
								"is_correct": True,
							},
							{
								"text": "It seals against hydrostatic pressure from the wet side",
								"is_correct": True,
							},
							{
								"text": "It can be removed and reused if the pipe is replaced",
								"is_correct": True,
							},
							{
								"text": "It structurally supports the pipe through the wall",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
