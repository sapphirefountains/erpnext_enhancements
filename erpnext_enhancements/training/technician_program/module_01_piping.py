# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 1 — Piping & Hydraulics.

Rebuilt from Sapphire's own material: *Module 1: Hydraulic Layout & Pipe Mechanics*, plus the
separate deep-dive document on lesson 1.1. Where that material states a figure, a sequence or a
product, it is used as written — the 1/3-to-2/3 dry fit, the 10-15 degree chamfer, heavy-bodied
gray cement, the 30-second hold, 4-6 inches of bedding and 6 inches of shading, 1/4 inch per foot
under 4 inch pipe and 1/8 inch per foot at 4 inch and above, 50 PSI or 1.5x working pressure held
two hours with a needle that does not move, hand-tightening a Link-Seal in a star pattern. Those
are Sapphire's numbers rather than this course's, which is why lesson one carries
``sourced_notice_block()`` instead of the generic one.

The source runs to seven numbered topics; this course is five lessons and that count is pinned by
``tests/test_technician_training_program.py``. So two topics were folded in rather than dropped:
1.6 jobsite hose and cord management sits in the underground-piping lesson, which is already the
one that talks about the jobsite, and 1.7 hydraulic shock sits in the pressure-testing lesson,
which is the one about pressure. Both say in the lesson why they sit where they do, and the
module's own Technical Performance Checklist is split across the five lessons so each
demonstration sits next to the teaching it belongs to.

Where the document is silent — flexible-pipe bedding mechanics, trench and excavation safety,
solvent vapour, laser calibration, rated penetrations — the lesson keeps what it had, and keeps
the discipline that goes with it: no invented figure that really belongs on a label, a data sheet
or a drawing.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 1 — Piping & Hydraulics",
		"summary": (
			"Make a solvent-weld joint that holds, put pipe in the ground so it stays where you "
			"put it, set a pitch you can prove, test a system without hurting anybody, seal a "
			"penetration through a wall, and keep a shockwave out of the manifold."
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
			"estimated_minutes": 18,
			"summary": "Sapphire's solvent-weld procedure, step by step, and the preparation people skip.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "It is a weld, not a glue",
					"content": (
						"<p>A commercial feature runs continuously and spikes in pressure every time a "
						"pump starts and every time a valve shuts. The joints have to be permanent and "
						"leak-free at that duty. Everything in this lesson is aimed at that one "
						"outcome.</p>"
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
					"heading": "Cut square, chamfer outside, deburr inside",
					"content": (
						"<p><b>Square.</b> Use a dedicated wheel-style pipe cutter, or a fine-tooth saw "
						"in a mitre box. The cut must be square — 90°. An angled cut reduces the "
						"surface area inside the socket, so there is less area welded and what is "
						"left is a structural weak point that fails under pressure. A PVC socket is "
						"also tapered, so a pipe cut out of square bottoms on one side and leaves a "
						"wedge-shaped void on the other. A freehand hacksaw cut is out of square more "
						"often than it is not.</p>"
						"<p><b>Chamfer the outside.</b> With a chamfering tool or a fine rasp, cut a "
						"<b>10° to 15° bevel</b> on the outside edge of the pipe. Sapphire's own "
						"document calls this the non-negotiable step, and it is the one that gets "
						"skipped.</p>"
						"<p><b>Deburr the inside.</b> Run a deburring tool or medium-grit sandpaper "
						"around the internal edge and take off every loose burr and shaving. Anything "
						"left there breaks free on the first system flush and ends up in the small "
						"things — nozzle orifices and wedge-action valves — where it stays.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "What a missing chamfer actually does",
					"content": (
						"<p>A square, unchamfered pipe end is a scraper blade. As it goes into the "
						"socket it <b>plows the cement ahead of it</b>, wipes the socket walls bone "
						"dry, and piles all of that cement up at the bottom.</p>"
						"<p>The result is a joint with a thick plug of cement at the base, a dry "
						"band where the seal actually needs to be, and a perfect-looking bead "
						"around the outside. It holds through the test. It fails later, "
						"underground, under a slab.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The preparation phase",
					"content": (
						"<p><b>Dry fit.</b> Push the pipe into the fitting dry. It should go in easily "
						"<b>about one third to two thirds of the way</b> into the socket and stop "
						"there. If it bottoms out dry, the tolerances are too loose: <b>discard that "
						"fitting</b>. There is nothing to weld across a gap, and no amount of cement "
						"makes one up.</p>"
						"<p><b>Clean.</b> Wipe the outside of the pipe and the inside of the socket "
						"with a clean, dry rag — all of the moisture, grease and dirt. On a wet day "
						"this is most of the job.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The solvent weld, in order",
					"content": (
						"<ol>"
						"<li><b>Primer.</b> Apply purple primer aggressively: inside the fitting "
						"socket, then the outside of the pipe, then the socket once more. Primer is a "
						"solvent — it breaks down the glossy exterior of the PVC and opens the "
						"plastic's pores so the cement can fuse them. <b>The primer must still be wet "
						"when the cement goes on.</b> If it has flashed off, prime again.</li>"
						"<li><b>Cement.</b> On commercial features use <b>heavy-bodied gray PVC "
						"cement</b>, which is engineered for Schedule 80 and high-pressure systems. "
						"A liberal, even layer on the outside of the pipe, a thin layer inside the "
						"socket, then a quick second layer back on the pipe.</li>"
						"<li><b>Push and twist.</b> Immediately push the pipe firmly in until it "
						"bottoms out completely, giving it a <b>quarter turn</b> (90°) as it goes to "
						"spread the cement and drive out air pockets.</li>"
						"<li><b>Hold.</b> Hold the joint firmly for <b>at least 30 seconds</b>, longer "
						"in cold weather. The socket is tapered, so the pipe pushes itself back out "
						"until the chemical weld starts to set. Let go early and the pipe walks part "
						"way out of a socket you cannot see into.</li>"
						"<li><b>Wipe.</b> Take the excess bead off the outside. A clean, uniform ring "
						"of purple and gray is what a finished Sapphire joint looks like.</li>"
						"</ol>"
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
						"<p><b>Cure time</b> — how long before the joint can take water and pressure. "
						"This is the one that matters before a hydrostatic test, and it is "
						"dramatically longer than set time. It grows with pipe diameter, it grows as "
						"the temperature drops, and it grows with the test pressure.</p>"
						"<p>The can carries the manufacturer's temperature and diameter chart. "
						"<b>Read it for the size, the temperature and the pressure you actually "
						"have</b>, not the shortest number printed on it, and put no water in the "
						"system until that time has passed.</p>"
					),
				},
				ask_block(
					"Which cement, and for which pipe",
					"<p>The product choice for commercial work is settled: purple primer and "
					"heavy-bodied gray cement. What is not settled by that is everything the can "
					"itself governs — there is a <b>maximum pipe size</b> on every can, there are "
					"formulations for wet conditions and for cold weather, and PVC, CPVC and ABS take "
					"<b>different</b> cements that are not interchangeable.</p>"
					"<p>The can tells you which materials it is rated for, which sizes, and at which "
					"temperatures. If what is on the truck is not rated for the joint in front of "
					"you, that is a stop-and-ask, not a judgement call.</p>",
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
						"A vault, pit or sump is very likely a permit-required confined space, and "
						"ventilating it is not what makes it safe to enter: entry is determined in "
						"writing by the employer beforehand, and the atmosphere is tested and "
						"monitored by somebody trained to do it. Module 9 covers what that "
						"involves. Gloves, eye protection, and read the safety data sheet for what "
						"is actually in your hand.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you walk away from a joint",
					"items": [
						"The cut was square and the outside edge carries a 10° to 15° chamfer",
						"The inside burr and every loose shaving are gone",
						"The dry fit went one third to two thirds in, and any fitting that bottomed out dry was discarded",
						"Both surfaces were wiped clean and dry before anything was applied",
						"Primer went on socket, pipe, socket — and was still wet when the cement went on",
						"Heavy-bodied gray cement went on the pipe, the socket, then the pipe again",
						"It was pushed home with a quarter turn and held at least 30 seconds",
						"The excess bead is wiped and the joint shows a continuous purple and gray ring",
						"Nothing gets water or pressure until the cure time on the can has passed",
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Module 1 sign-off: demonstrate to a Lead Installer",
					"items": [
						"A square cut, a 15° chamfer and clean deburring on a piece of 3 inch Schedule 80 PVC",
						"A solvent weld joint showing a continuous, uniform external bead of cement",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is the outside edge of a cut pipe end chamfered before it goes into a fitting?",
						"type": "Single Choice",
						"explanation": (
							"An unchamfered edge is a scraper blade: it plows the cement ahead of it, wipes the "
							"socket walls bone dry where the seal is needed, and piles the cement at the bottom. "
							"Sapphire's procedure puts a 10° to 15° bevel on every pipe end."
						),
						"options": [
							{
								"text": "So the pipe does not plow the cement out of the socket as it goes in",
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
						"question": "On a dry fit the pipe slides straight to the bottom of the socket. What does Sapphire's procedure say to do?",
						"type": "Single Choice",
						"explanation": (
							"A dry pipe should go in easily about one third to two thirds of the way and stop. "
							"Bottoming out dry means the tolerances are too loose, and the fitting is discarded — "
							"cement is not a gap filler."
						),
						"options": [
							{
								"text": "Discard the fitting — the tolerances are too loose",
								"is_correct": True,
							},
							{"text": "Use it; the cement will fill the gap", "is_correct": False},
							{
								"text": "Use it, because an easy push means a fast, clean joint",
								"is_correct": False,
							},
							{
								"text": "Prime it twice to swell the pipe before cementing",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You primed the socket and the pipe, got called away, and came back to find the primer dried. What now?",
						"type": "Single Choice",
						"explanation": (
							"The primer must still be wet when the cement goes on — that is the state in which the "
							"softened surface takes the weld. Dry primer is a purple stain, not a prepared "
							"surface, so prime again."
						),
						"options": [
							{
								"text": "Prime again, so the primer is wet when the cement goes on",
								"is_correct": True,
							},
							{
								"text": "Cement it as it is — the purple dye shows the primer was used",
								"is_correct": False,
							},
							{"text": "Wipe the primer off and cement the bare pipe", "is_correct": False},
							{
								"text": "Nothing — primer works better once it has dried hard",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A joint has been made. Which number on the can tells you when it can take water and pressure?",
						"type": "Single Choice",
						"explanation": (
							"Set time is when you can handle it. Cure time is when it can take pressure, and it is "
							"much longer — longer again for big pipe, cold weather and high test pressure. Read it "
							"off the manufacturer's temperature and diameter chart."
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
				]
			},
		},
		{
			"lesson_title": "Underground piping and bedding procedures",
			"chapter": 1,
			"estimated_minutes": 16,
			"summary": "What actually holds buried pipe up, the bedding and shading figures, the two things you cannot backfill over, and the over-under coil.",
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
						"bad bed is a failure waiting for the first truck to drive over it. The job "
						"here is to protect a buried network from ground movement, deck loads and "
						"rock.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The trench floor, then the bedding layer",
					"content": (
						"<p><b>Depth</b> comes from the local building code, and in a freezing climate "
						"the lines sit <b>below the frost line</b>. That is not negotiable and it is "
						"not a judgement made in the trench.</p>"
						"<p><b>The floor must be continuous</b> and free of large rocks, jagged shale, "
						"tree roots and construction debris. Anything left in it becomes a point "
						"load.</p>"
						"<p><b>Then bed it.</b> Lay a minimum of <b>4 to 6 inches of clean, washed "
						"sand or pea gravel</b> along the entire floor of the trench, to give a level "
						"and forgiving base. <b>Never lay PVC directly onto hard, unyielding native "
						"rock or dirt.</b></p>"
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
						"above onto that one spot — and under compaction a sharp stone does not just "
						"load the wall, it punctures it.</li>"
						"<li><b>Voids.</b> Bell holes that were never filled, an unbedded section "
						"under a fitting, a washed-out pocket. The pipe spans the gap and "
						"bends.</li>"
						"</ul>"
						"<p>Haunching — the bedding material worked in under the bottom curve of the "
						"pipe on each side — is the part people leave out, and it is the part "
						"providing the side support.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Lay the pipes apart, not against each other",
					"content": (
						"<p>Set the assembled networks down on the bedding so that <b>pipes do not "
						"cross over one another and do not rest tightly against one another</b>.</p>"
						"<p>The reason is vibration. Every pump start shakes the lines, and two pipes "
						"pressed together rub in the same place for years. That is friction wear on "
						"a wall that was sized for pressure, not for abrasion, and it happens where "
						"nobody will ever look at it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Shading: the six inches that go on after the test",
					"content": (
						"<p><b>Once pressure testing passes</b>, fill the trench with a further "
						"<b>6 inches of sand or pea gravel</b> over the top and the sides of the "
						"pipes. That envelope is called <i>shading</i>.</p>"
						"<p>It is a cushion, and it is doing its work later: when the heavy native "
						"dirt goes back in and gets compacted, the shading is what stops a sharp "
						"stone puncturing or crushing the PVC. Native spoil dropped straight onto "
						"bare pipe puts the compactor's energy through whatever happens to be in "
						"it.</p>"
						"<p>Note the order Sapphire sets: bed, lay, <b>test</b>, shade, backfill. The "
						"test sits before the pipe disappears, on purpose.</p>"
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
						"stable is the one people die in. Excavation safety is its own standard and "
						"its own training, and this program is not it; the point here is that "
						"<i>getting into a trench is not a plumbing decision</i>.</p>"
						"<p><b>Something else is already down there.</b> Utility locates are "
						"required before you dig, they expire, and they mark an approximate "
						"position — you still expose by hand within the tolerance zone. Hitting a "
						"gas line or a primary electrical feed is not a bad day, it is a "
						"fatality.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Jobsite hose and cord: the over-under coil",
					"panels": [
						{
							"title": "Why the forearm wrap ruins a hose",
							"body": (
								"<p>Sapphire's module carries hose and cord management as a topic of its "
									"own. It sits in this lesson because this is the one about working in and "
									"around the trench, and a coiled line is both the thing you ruin fastest and "
									"the trip hazard somebody gets hurt on.</p>"
									"<p>Wrapping a high-pressure wash hose or a heavy electrical cord around "
								"your forearm and elbow puts a <b>360° twist</b> into the structural casing "
								"with every single loop. The twist does not go anywhere. When the line is "
								"uncoiled later it kinks, knots and chokes off the flow, and the casing has "
								"been worked in a direction it was never built for.</p>"
							),
						},
						{
							"title": "Over, under, over, under",
							"body": (
								"<p>Hold one end of the hose in your non-dominant hand. With your dominant "
								"hand, pull a straight length toward you and make a standard forward loop "
								"into your holding hand — that is the <b>over</b> loop.</p>"
								"<p>For the next loop, flip your dominant hand upside down, grab the hose "
								"and twist it away from your body as you bring it up to your holding hand — "
								"the <b>under</b> loop. Alternate over, under, over, under to the end.</p>"
								"<p>The alternating loops cancel the torsional twist instead of adding it "
								"up, so the coil lies flat and pulls apart in a straight line across a "
								"deck without a single kink.</p>"
							),
						},
						{
							"title": "Securing the coil",
							"body": (
								"<p>Use a reusable hook-and-loop strap or a dedicated cord tie. "
								"<b>Never tie the hose into a knot to secure it</b> — the knot is a "
								"permanent crush point in the casing, and it is exactly where the line "
								"will fail.</p>"
								"<p>Coiled line is also the trip hazard that gets somebody hurt on a "
								"finished deck. A coil that lies flat and gets hung up is the whole "
								"point.</p>"
							),
						},
					],
				},
				ask_block(
					"Depth, compaction and thrust restraint are still specified",
					"<p>Sapphire's own figures cover the bedding and the shading: 4 to 6 inches of "
					"clean washed sand or pea gravel under the pipe, 6 inches over it once the test "
					"has passed. What they do not cover is the rest of the trench detail.</p>"
					"<p>Trench depth comes from the local code and the frost line. Lift thickness and "
					"required compaction come from the project's civil drawings. So does <b>thrust "
					"restraint</b> at bends and tees on a pressurised line — thrust blocks and "
					"restrained joints are an engineered detail with a size on it. If the drawing "
					"does not say, that is a question for the project manager before the trench is "
					"open, not after.</p>",
				),
				{
					"block_type": "Checklist",
					"heading": "Before backfill goes in",
					"items": [
						"Utility locates are current, and anything in the tolerance zone was exposed by hand",
						"Trench protection is in place and a competent person decided what it is",
						"The trench bottom is continuous and free of rock, shale, roots and debris",
						"4 to 6 inches of clean washed sand or pea gravel is down along the whole floor",
						"No pipe crosses over or rests tight against another pipe",
						"Bell holes are filled and the haunches are worked in on both sides",
						"Every joint has passed its cure time",
						"The run has passed its pressure test",
						"6 inches of sand or pea gravel is shaded over the top and sides before native dirt",
						"Thrust restraint at bends and tees matches the detail on the drawing",
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Module 1 sign-off: demonstrate to a Lead Installer",
					"items": [
						"The hand-over-hand over-under coiling technique on a 50 foot heavy-duty wash hose",
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
						"question": "What goes on the trench floor before the pipe does?",
						"type": "Single Choice",
						"explanation": (
							"A minimum of 4 to 6 inches of clean, washed sand or pea gravel along the entire "
							"floor, to give a level and forgiving base. PVC never goes straight onto native rock "
							"or dirt."
						),
						"options": [
							{
								"text": "At least 4 to 6 inches of clean, washed sand or pea gravel",
								"is_correct": True,
							},
							{
								"text": "Nothing, as long as the native soil has been raked level",
								"is_correct": False,
							},
							{
								"text": "The spoil from the excavation, screened and put back",
								"is_correct": False,
							},
							{"text": "A skim of lean concrete to hold the grade", "is_correct": False},
						],
					},
					{
						"question": "When does the 6 inches of sand or pea gravel over the top and sides of the pipe go in?",
						"type": "Single Choice",
						"explanation": (
							"Shading goes on once the pressure test has passed and before native backfill. It is "
							"the cushion that stops a sharp stone puncturing the PVC when the heavy dirt above is "
							"compacted."
						),
						"options": [
							{
								"text": "After the pressure test passes, before the native dirt goes back",
								"is_correct": True,
							},
							{
								"text": "Before the pressure test, to hold the pipe on grade while it is filled",
								"is_correct": False,
							},
							{
								"text": "It is the same layer as the bedding, placed in one lift",
								"is_correct": False,
							},
							{
								"text": "Only where the pipe runs under a driveway or deck",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The schedule is tight and the crew wants to backfill a run welded an hour ago. What is the objection?",
						"type": "Single Choice",
						"explanation": (
							"Backfill loads the pipe. Loading a joint before its cure time is loading a joint that "
							"is still soft — and then it is buried. The order Sapphire sets is bed, lay, test, "
							"shade, backfill."
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
			"estimated_minutes": 15,
			"summary": "Sapphire's minimum slopes, the drop arithmetic, and the mistake that puts a laser's error into the pipe.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Gravity lines have nothing pushing them",
					"content": (
						"<p>A pressure line has a pump behind it. A gravity-return loop, an overflow "
						"and a deck drain have nothing but the drop you built into them.</p>"
						"<p>If a drain line is perfectly flat or back-sloped, water backs up, air gets "
						"trapped against the high spots — an <b>air-lock</b> — and the basin or the "
						"splash pad deck floods. A line that is level in places also holds water in "
						"those places, and held water freezes and splits a line, grows biofilm, and "
						"makes winterisation impossible to finish.</p>"
						"<p>Almost everything Module 7 does at the end of a season depends on the "
						"system emptying where it is supposed to empty.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Sapphire's minimum slopes",
					"content": (
						"<p>On non-pressurised gravity-return, overflow and deck drain piping:</p>"
						"<ul>"
						"<li><b>Pipe under 4 inch diameter</b> — a minimum downward slope of "
						"<b>1/4 inch of drop per linear foot</b> of run. That is a 2% grade.</li>"
						"<li><b>Pipe 4 inch diameter and larger</b> — a minimum downward slope of "
						"<b>1/8 inch of drop per linear foot</b>. That is a 1% grade.</li>"
						"</ul>"
						"<p>These are <b>minimums</b>. A drawing or a code that asks for more wins; "
						"nothing allows less. And the usual reason a line ends up with a belly in it "
						"is not that somebody chose the wrong grade — it is that the grade moved "
						"between setting it and backfilling it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Setting up the laser and shooting a benchmark",
					"content": (
						"<p>A laser gives you one thing: a <b>reference plane</b>. Everything else "
						"is arithmetic you do with a receiver on a grade rod.</p>"
						"<ol>"
						"<li>Set the rotary laser transit on a sturdy tripod, somewhere central with "
						"an unobstructed line of sight to the whole length of the trench, and let it "
						"self-level.</li>"
						"<li>Establish your <b>master benchmark</b> elevation. On a fountain that is "
						"typically the finished concrete coping, or the absolute top edge of the weir "
						"wall.</li>"
						"<li>Attach the digital receiver to a graded measuring rod, stand the rod on "
						"the benchmark, and read your baseline height.</li>"
						"<li>That reading plus the known benchmark elevation gives you the height of "
						"the laser plane. Every other point is then the plane height minus your rod "
						"reading there.</li>"
						"</ol>"
						"<p>Read the pipe at the <b>invert</b> — the inside bottom — unless the "
						"drawing says otherwise, and be consistent. Mixing invert and top-of-pipe "
						"readings on one run is how a fall becomes a rise.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The drop calculation, and walking the line",
					"content": (
						"<p>Work out the total drop before you set anything. On a line under 4 inch:</p>"
						"<p><b>total length of the run in feet × 0.25 inches = the required vertical "
						"drop</b></p>"
						"<p>So a 40 foot run of 3 inch drain needs 10 inches of fall from one end to "
						"the other. On 4 inch and larger the same arithmetic uses 0.125 inches per "
						"foot.</p>"
						"<p>Then walk it. Move down the trench line checking elevations every 5 to 10 "
						"feet, and adjust the pipe hangers or the gravel bedding height until the "
						"receiver confirms the pipe is dropping at the calculated rate the whole way. "
						"Two ends at the right elevation with a belly in the middle reads as correct "
						"on a two-point check and drains like a trap.</p>"
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
						"Support the pipe on the compacted bedding itself, not on loose spoil and not "
						"on blocks you intend to pull out.</p>"
						"<p>Check the grade again <b>after</b> the haunches are worked in, and again "
						"before the trench closes. The cheapest time to find a belly is while the "
						"pipe is still visible; the most expensive is on commissioning day in front "
						"of the client.</p>"
					),
				},
				ask_block(
					"The minimums are the floor, not the answer to every line",
					"<p>The 1/4 inch and 1/8 inch per foot rules above are Sapphire's minimums for "
					"gravity-return, overflow and deck drain piping. They are not a universal number "
					"for every pipe on a site — a sanitary drain, a suction line and a conduit run "
					"are three different answers, and the plumbing code governs some of them.</p>"
					"<p>Where the plan, the specification or the code names a slope or an invert "
					"elevation, that is the number. Read it off the drawing for <i>this</i> job; a "
					"number you remember from the last one is a number you are about to bury.</p>",
				),
				{
					"block_type": "Checklist",
					"heading": "Setting a gravity line",
					"items": [
						"The required slope for this line type is off the drawing, and it is at or above the minimum",
						"The laser has been field-checked against something that is not the laser",
						"The transit is on firm ground with sight of the whole run",
						"The benchmark is a real known elevation — coping or weir top — not a guess",
						"The total drop is calculated before any pipe is set",
						"Elevations are checked every 5 to 10 feet along the run, not just at the ends",
						"Grade is re-checked after haunching and again before the trench closes",
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Module 1 sign-off: demonstrate to a Lead Installer",
					"items": [
						"Set up a rotary laser level, establish a benchmark, and stake a trench line at a true 2% gravity slope",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a flat or back-sloped section in a gravity drain line a real problem rather than just imperfect?",
						"type": "Single Choice",
						"explanation": (
							"Water backs up and air gets trapped against the high spot — an air-lock — and the "
							"basin or deck floods. The water that stays behind also freezes, splits lines and "
							"defeats winterisation."
						),
						"options": [
							{
								"text": "Water backs up and air-locks the line, and the held water freezes and fouls",
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
						"question": "What is Sapphire's minimum slope on a 3 inch overflow line?",
						"type": "Single Choice",
						"explanation": (
							"Under 4 inch diameter the minimum is 1/4 inch of drop per linear foot, a 2% grade. "
							"The 1/8 inch per foot (1%) figure is for pipe 4 inch and larger."
						),
						"options": [
							{"text": "1/4 inch of drop per linear foot, a 2% grade", "is_correct": True},
							{"text": "1/8 inch of drop per linear foot, a 1% grade", "is_correct": False},
							{"text": "1/2 inch of drop per linear foot, a 4% grade", "is_correct": False},
							{
								"text": "Level is acceptable on a short run if both ends are at the right elevation",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A 40 foot run of 3 inch gravity drain. How much total vertical drop does it need?",
						"type": "Single Choice",
						"explanation": (
							"Length in feet × 0.25 inches. 40 × 0.25 = 10 inches. The 5 inch answer is the same "
							"run worked at the 1/8 inch per foot rate, which applies to 4 inch and larger pipe."
						),
						"options": [
							{"text": "10 inches", "is_correct": True},
							{"text": "5 inches", "is_correct": False},
							{"text": "20 inches", "is_correct": False},
							{"text": "2.5 inches", "is_correct": False},
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
				]
			},
		},
		{
			"lesson_title": "Hydrostatic pressure testing",
			"chapter": 2,
			"estimated_minutes": 18,
			"summary": "Water never air, Sapphire's 50 PSI two-hour test with a needle that does not move, and the shockwave the system sees afterwards.",
			"blocks": [
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Never pressure test PVC with compressed air",
					"content": (
						"<p>This is the most important sentence in this module.</p>"
						"<p>Water is effectively <b>incompressible</b>. A water-filled line at "
						"pressure holds almost no stored energy — when it fails it squirts and the "
						"pressure is gone. Air is compressible, and a line full of compressed air is "
						"a <b>spring</b>. PVC is an amorphous plastic: under air it does not split, "
						"it <b>explodes</b> into razor-sharp shrapnel.</p>"
						"<p>People have been killed doing this. Not injured — killed. If somebody "
						"suggests putting a compressor on a PVC line to find a leak faster, the "
						"answer is no, and it is not a negotiation.</p>"
						"<p>There is exactly one place compressed air belongs on this pipe: "
						"<b>under 15 PSI</b>, strictly for blowing lines out at seasonal "
						"winterisation. That is clearing water, not testing, and it is nowhere near "
						"a test pressure.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What the test is for, and Sapphire's numbers",
					"content": (
						"<p>A hydrostatic test asks one question: <i>does this system hold "
						"pressure?</i> It does not tell you whether the system was designed right, "
						"and it is not a substitute for having built it correctly. It is the check "
						"that certifies the network is free of micro-leaks while the trench is still "
						"open and while a bad joint is still an hour's work rather than a day and a "
						"machine.</p>"
						"<p>Sapphire's figures for it:</p>"
						"<ul>"
						"<li><b>Test pressure</b> — 50 PSI, or 1.5 times the maximum operating "
						"pressure of the fountain, <b>whichever is greater</b>.</li>"
						"<li><b>Hold</b> — a minimum of 2 hours, watching the gauge.</li>"
						"<li><b>Acceptance</b> — the needle must remain completely fixed.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Isolating the run and building the manifold",
					"content": (
						"<p><b>Isolate what is being tested</b> with mechanical test plugs or "
						"temporary glued-on PVC caps. Do <b>not</b> test against mechanical "
						"equipment — pumps, filters and electronic control valves are rated far below "
						"typical test pressures and testing through them destroys them.</p>"
						"<p><b>Put the test manifold at the highest physical point</b> of the piping "
						"system. It needs three things on it:</p>"
						"<ul>"
						"<li>An accurate <b>pressure gauge</b>, chosen so the target test pressure "
						"sits in the <b>middle 50% of the dial</b>. A needle down at the bottom of "
						"its range cannot show you a small drop, which is the only thing this test "
						"is looking for.</li>"
						"<li>A <b>ball valve</b> for the water intake.</li>"
						"<li>A dedicated <b>air-bleed valve</b>.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Running it",
					"content": (
						"<ol>"
						"<li><b>Every joint has passed its cure time</b>, at the temperature it "
						"actually cured at.</li>"
						"<li><b>Fill slowly from the lowest point</b> with the air-bleed valve at the "
						"top wide open. Leave it open until a steady, unbroken stream of water shoots "
						"out — that is the confirmation that the air pockets are gone.</li>"
						"<li><b>Connect a manual hydrostatic pressure pump</b> to the manifold and "
						"bring the pressure up gradually to 50 PSI or 1.5 times maximum operating "
						"pressure, whichever is greater. Keep people clear while it rises.</li>"
						"<li><b>Close the intake isolation valve</b>, disconnect the pump, and write "
						"down the starting pressure and the time.</li>"
						"<li><b>Monitor for a minimum of 2 hours</b>, and record the end pressure and "
						"the ambient temperature with it.</li>"
						"</ol>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Any drop is a fail",
					"content": (
						"<p>Sapphire's acceptance is blunt: <b>the needle must not move</b>. Any drop "
						"at all, however small, means a leaking joint, a failing plug or a cracked "
						"pipe, and that has to be cut out and replaced before anything is covered.</p>"
						"<p>That is stricter than the way a pressure drop is often explained, so be "
						"clear about why. Three things move a needle:</p>"
						"<ul>"
						"<li><b>Trapped air</b> compressing and dissolving — which is exactly what "
						"the bleed-until-a-solid-stream step exists to eliminate. If the needle moves "
						"because of air, the fill was not finished.</li>"
						"<li><b>Temperature.</b> Water expands and contracts and so does the pipe. A "
						"line pressurised in the sun and read in the shade will have moved. That is "
						"why the ambient temperature is recorded with the readings.</li>"
						"<li><b>An actual leak.</b></li>"
						"</ul>"
						"<p>None of those three is a reason to sign off a test that lost pressure. "
						"Bleed it properly, let conditions settle, and run it again. And a drop on "
						"the gauge with <b>no water visible anywhere</b> is worth taking more "
						"seriously, not less — on a buried line, the water has somewhere to go.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "After the test: the shockwave the system makes itself",
					"content": (
						"<p>The test proves the network at whichever was the greater of 50 PSI and 1.5 "
						"times working pressure. In service it can see far more than that, and it "
						"makes the extra itself.</p>"
						"<p>Water is dense and non-compressible. When a high-velocity stream through "
						"a manifold is stopped instantly — a direct-action solenoid snapping shut in "
						"milliseconds — the kinetic energy of the moving water has nowhere to go. It "
						"becomes a pressure shockwave of <b>4 to 5 times normal operating "
						"pressure</b> travelling back up the pipe. That is <b>water hammer</b>: the "
						"loud banging, the cracked PVC fittings, the shattered valve diaphragms.</p>"
						"<p>It is a design and programming problem more than a field one, which is "
						"why it belongs next to the test: a system that passed its hydrostatic test is "
						"not thereby safe from something that makes 4 to 5 times its own working "
						"pressure on every cycle.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Three ways to keep the shockwave small",
					"panels": [
						{
							"title": "Calibrate the actuation profile",
							"body": (
								"<p>When programming electronic control valves or variable frequency drives "
								"through the Splash Wizard platform, avoid instant 0% flow steps wherever you "
								"can. Program a small ramp-down profile — even a fraction of a second of "
								"deceleration — so the velocity change is eased rather than slammed.</p>"
							),
						},
						{
							"title": "Install a mechanical surge suppressor",
							"body": (
								"<p>On manifolds feeding fast-cycling interactive nozzles, fit a "
								"commercial-grade water hammer arrestor or a closed nitrogen-charged surge "
								"bladder. Both give the shockwave a compressible chamber to push into, which "
								"is what absorbs the energy safely instead of the fittings doing it.</p>"
							),
						},
						{
							"title": "Keep the velocity down in the first place",
							"body": (
								"<p>At layout and design, size pipe generously enough to keep water velocity "
								"<b>below 5 feet per second on suction lines</b> and <b>below 7 feet per "
								"second on discharge lines</b>. The energy available to make a shockwave comes "
								"from the moving water, so a low baseline velocity means there is less of it "
								"to release.</p>"
							),
						},
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Before the pressure goes up",
					"items": [
						"Water, never air",
						"Every joint is past cure time for its size and the temperature it cured at",
						"The run is isolated with test plugs or temporary caps",
						"Pumps, filters and electronic control valves are out of the test",
						"The manifold is at the highest point, with gauge, intake ball valve and air-bleed valve",
						"The target pressure sits in the middle 50% of the gauge dial",
						"Filling started at the low point and the bleed ran until the stream was solid",
						"Nobody is standing over a joint, a plug, a cap or a thrust point",
						"You have somewhere to write down start pressure, time, end pressure and ambient temperature",
					],
				},
				ask_block(
					"Where a specification asks for more",
					"<p>50 PSI or 1.5 times working pressure, held two hours with no movement, is "
					"Sapphire's standard. It is a floor, not a ceiling: a project specification, a "
					"governing code or an inspector can require a higher pressure, a longer hold, a "
					"witnessed test or a written acceptance record, and on a fire line or a potable "
					"connection they will.</p>"
					"<p>Read the documents for this job before you set the gauge. And the maximum "
					"operating pressure the 1.5 multiplier applies to is the designer's number — get "
					"it from the drawing, not from what the pump happens to read.</p>",
				),
				{
					"block_type": "Checklist",
					"heading": "Module 1 sign-off: demonstrate to a Lead Installer",
					"items": [
						"Safely assemble a hydrostatic testing manifold, purge all air pockets, and execute a stable 50 PSI pressure test",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Why must PVC never be pressure tested with compressed air?",
						"type": "Single Choice",
						"explanation": (
							"Compressed air stores energy and PVC is amorphous — under air it does not split, it "
							"explodes into razor-sharp shrapnel. Water is incompressible and simply squirts. The "
							"only compressed air allowed on this pipe is under 15 PSI for winterisation blowing, "
							"which is not a test."
						),
						"options": [
							{
								"text": "Compressed air stores energy, so the pipe explodes into shrapnel instead of splitting",
								"is_correct": True,
							},
							{
								"text": "Air leaks past joints that would hold water, so the test fails falsely",
								"is_correct": False,
							},
							{"text": "Air dries the solvent weld and weakens it", "is_correct": False},
							{
								"text": "It is acceptable on a buried line, because the soil contains the failure",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What pressure does Sapphire's hydrostatic test run at?",
						"type": "Single Choice",
						"explanation": (
							"50 PSI or 1.5 times the maximum operating pressure of the fountain, whichever is "
							"greater — so 50 PSI is the floor, not the cap."
						),
						"options": [
							{
								"text": "50 PSI or 1.5 times maximum operating pressure, whichever is greater",
								"is_correct": True,
							},
							{
								"text": "1.5 times maximum operating pressure, capped at 50 PSI",
								"is_correct": False,
							},
							{"text": "Whatever the system runs at in normal service", "is_correct": False},
							{
								"text": "50 PSI on every system, regardless of what it operates at",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The gauge reads 2 PSI lower at the end of the two-hour hold. Does the test pass?",
						"type": "Single Choice",
						"explanation": (
							"No. Sapphire's acceptance is a needle that does not move at all. Trapped air and "
							"temperature do move a needle, but neither is a reason to sign off a test that lost "
							"pressure — they are reasons to bleed properly, let conditions settle and run it "
							"again."
						),
						"options": [
							{
								"text": "No — the needle must stay fixed, so something is leaking, plugged badly or cracked",
								"is_correct": True,
							},
							{"text": "Yes — a small drop is within normal tolerance", "is_correct": False},
							{
								"text": "Yes, provided the temperature changed during the hold",
								"is_correct": False,
							},
							{
								"text": "Yes, provided no water is visible anywhere along the run",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these reduce water hammer in a fountain manifold?",
						"type": "Multiple Choice",
						"explanation": (
							"A ramped actuation profile eases the velocity change, an arrestor or nitrogen-charged "
							"bladder gives the wave a compressible chamber to push into, and generous pipe sizing "
							"keeps velocity below 5 ft/s on suction and 7 ft/s on discharge so there is less "
							"energy to release. Closing valves faster is the cause, not the cure."
						),
						"options": [
							{
								"text": "Programming a ramp-down profile instead of an instant 0% flow step",
								"is_correct": True,
							},
							{
								"text": "Fitting a water hammer arrestor or nitrogen-charged surge bladder",
								"is_correct": True,
							},
							{
								"text": "Sizing pipe to keep velocity below 5 ft/s on suction and 7 ft/s on discharge",
								"is_correct": True,
							},
							{
								"text": "Closing the solenoid faster, so the water has less time to build momentum",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Pipe penetration sealing (Link-Seals)",
			"chapter": 2,
			"estimated_minutes": 13,
			"summary": "How a modular mechanical seal works, why the annular space is a measurement, and why an impact gun ruins one.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "What it is, and what it is keeping out",
					"content": (
						"<p>A Link-Seal is a modular mechanical sealing system: a belt of heavy-duty "
						"interlocking rubber segments joined by corrosion-resistant bolts. Tightening "
						"the bolts compresses the rubber segments <b>longitudinally</b>, which makes "
						"them expand <b>radially</b> — and that expansion is what creates a gas- and "
						"water-tight pressure seal between the outside diameter of the pipe and the "
						"inside of the wall sleeve or cored hole.</p>"
						"<p>It has two jobs on a fountain, in opposite directions: keep groundwater "
						"out of a dry subterranean equipment vault, and keep feature water from "
						"escaping past a tank wall penetration.</p>"
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
						"naming pipe sizes. A 4-inch pipe is not 4 inches across the outside, and "
						"the outside diameter belongs to the sizing standard the pipe was made to "
						"rather than to the material — steel and PVC of the same nominal size share "
						"one outside diameter, and the schedule sets the wall thickness rather than "
						"that outside diameter, so <b>Schedule 40 and Schedule 80 measure the same "
						"on the outside</b>. Ductile iron and copper are each on a different "
						"standard, and PVC is made in both the steel and the ductile-iron sizes. A "
						"cored hole is not the same diameter as a sleeve of the same nominal size. "
						"Measure both, then select.</p>"
						"<p>The manufacturer's table turns those two diameters into a model and a "
						"number of links. That table is the authority.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Clean it, centre it, and point the bolt heads at the dry side",
					"content": (
						"<p><b>Clean both surfaces.</b> The inside of the sleeve or cored hole and "
						"the outside of the pipe, free of concrete dust, grit and burrs. A ragged "
						"lip, concrete splatter or a peeling coating all give the rubber somewhere "
						"to bypass.</p>"
						"<p><b>Centre the pipe.</b> Check the penetration before you start: if the "
						"pipe is sagging or badly off-centre in the opening, the links expand "
						"unevenly and the seal eventually leaks. Support the pipe load with plastic "
						"pipe spacers if you need to.</p>"
						"<p><b>Assemble and position.</b> Connect the rubber links into a continuous "
						"belt around the pipe, slide the loose belt into the annular space, and set "
						"it so the <b>bolt heads face the interior of the dry equipment vault</b> — "
						"the side somebody can get to when it needs servicing later.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Tightening: by hand, in a star pattern",
					"content": (
						"<p>Use a manual socket wrench or a hand torque wrench.</p>"
						"<p>Work the bolts in a <b>star pattern</b> — exactly like the lug nuts on a "
						"truck wheel. Turn each bolt <b>2 to 3 full rotations</b>, then move to the "
						"bolt opposite it, and keep going round.</p>"
						"<p>You are finished when the rubber links <b>visibly bulge uniformly</b> "
						"around the entire circumference. Check that against the manufacturer's "
						"specified torque rating: that is what tells you the seal is positive without "
						"being over-compressed.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Never an impact gun, and never past the torque figure",
					"content": (
						"<p><b>No electric or pneumatic impact gun. Ever.</b> An impact gun tightens "
						"far too rapidly, which loads the belt unevenly, warps the rubber links and "
						"snaps the composite or stainless hardware. This is a hand-tool job and the "
						"module document calls it a mandatory tool choice.</p>"
						"<p>Over-tightening by hand does its own damage: it ruins the rubber, "
						"distorts the pressure plates, and in thin-walled pipe it can deform the pipe "
						"itself — and the result can look tight while still failing. Under-tighten "
						"and it weeps instead. Both failure directions look finished from the dry "
						"side, which is why the torque figure exists.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Installing a modular seal, in order",
					"items": [
						"Sleeve or cored hole and pipe exterior cleaned of dust, grit and burrs",
						"Both diameters measured and the model and link count taken from the manufacturer's table",
						"Pipe centred in the opening, supported on plastic spacers if it was sagging",
						"Links assembled into a continuous belt around the pipe",
						"Belt slid into the annular space with the bolt heads facing the dry vault interior",
						"Bolts run by hand in a star pattern, 2 to 3 turns at a time, opposite to opposite",
						"Links bulging uniformly all the way round, checked against the published torque",
						"Re-torqued after the settling period where the manufacturer calls for it",
					],
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
							"front": "Star pattern",
							"back": "Tightening opposite to opposite, 2 to 3 turns at a time, like truck lug nuts. It is what makes the rubber bulge evenly all the way round.",
						},
						{
							"front": "Re-torque",
							"back": "Tightening again after an initial settling period, where the manufacturer calls for it. Skipping it produces a slow weep.",
						},
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Module 1 sign-off: demonstrate to a Lead Installer",
					"items": [
						"Install and torque a modular Link-Seal assembly through a mock vault wall sleeve, by hand, in the star pattern",
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
							"Nominal sizes do not answer it — outside diameter follows the sizing standard "
							"the pipe was made to."
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
						"question": "How are the bolts on a Link-Seal brought up?",
						"type": "Single Choice",
						"explanation": (
							"By hand — socket or torque wrench — in a star pattern, 2 to 3 full turns at a time, "
							"moving to the opposite bolt, until the links bulge uniformly all the way round. An "
							"impact gun tightens too fast, warps the links and snaps the hardware."
						),
						"options": [
							{
								"text": "By hand, in a star pattern, 2 to 3 turns at a time, until the links bulge uniformly",
								"is_correct": True,
							},
							{
								"text": "With an impact gun on a low setting, following the same star pattern",
								"is_correct": False,
							},
							{
								"text": "One bolt fully tightened at a time, working around the ring",
								"is_correct": False,
							},
							{
								"text": "By hand until each bolt head sits flush against its plate",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The pipe sags and sits off-centre in the sleeve, but the links still reach all the way round. That is fine.",
						"type": "True-False",
						"explanation": (
							"False. An off-centre pipe makes the links expand unevenly, and that seal leaks "
							"eventually even though it looked complete on the day. Centre the pipe first, on "
							"plastic pipe spacers if it needs supporting."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
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
