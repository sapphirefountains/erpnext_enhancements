# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 8 — General Troubleshooting."""

from erpnext_enhancements.training.technician_program._common import ask_block, notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 8 — General Troubleshooting",
		"summary": (
			"Work a fault on a water feature to its actual cause instead of replacing parts: split "
			"the system where it divides the possibilities in half, decide what to measure, and tell "
			"the ten faults on this list apart from the things that look exactly like them."
		),
		"category": "Service & Maintenance",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{
			"title": "Working a fault, and the electrical side",
			"description": "The method every lesson here uses, and finding an electrical fault by proving dead and measuring toward the load.",
		},
		{
			"title": "Where the water is going",
			"description": "A spray that has changed, a basin that keeps dropping, and pipe that leaks.",
		},
		{
			"title": "What the water carries, and what it stops",
			"description": "Scale, lost flow, debris in the basin, and chemistry that will not balance.",
		},
		{
			"title": "When the hardware itself is the fault",
			"description": "Threads that seize or gall, and a pump losing performance or cutting out.",
		},
	],
	"lessons": [
		{
			"lesson_title": "Electrical components",
			"chapter": 0,
			"estimated_minutes": 16,
			"summary": "The method this whole module uses, and why an electrical fault is found from the supply toward the load.",
			"blocks": [
				notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "Find the fault before you replace the part",
					"content": (
						"<p>Every fault in this module is worked the same way, and the method matters "
						"more than the ten lists that follow it.</p>"
						"<p><b>Reproduce it.</b> A fault you cannot make happen is a fault you cannot "
						"prove you fixed. If it only shows up at night, in wind, or an hour after "
						"start-up, that is not an inconvenience — it is the best clue you have, "
						"because it names a condition.</p>"
						"<p><b>Ask what changed.</b> A system that ran for two seasons rarely chooses "
						"the week somebody was working on it to fail on its own. Somebody cleaned "
						"something, replaced something, turned a valve, or changed a setting. Ask the "
						"person who was last there, and read the last service record before you take "
						"a lid off.</p>"
						"<p><b>Divide the system.</b> Every tool in this module is a way of cutting "
						"the possibilities in half: a gauge on each side of the pump, a valve that "
						"isolates one leg, one nozzle behaving while its nineteen identical neighbours "
						"do not. Halving beats guessing, and it beats it on the first try.</p>"
						"<p><b>Measure, do not assume.</b> <i>It looks fine</i> is not a reading. This "
						"trade carries meters, gauges and test kits precisely because the things that "
						"fail most often look normal.</p>"
						"<p><b>Change one thing at a time.</b> Replace three parts at once, watch the "
						"fault go away, and you have learned nothing: two of those parts were fine, "
						"they are on the invoice, and when the fault returns there is no shortcut "
						"back to it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Prove dead, and prove the tester",
					"content": (
						"<p>Nothing electrical gets touched until the energy is controlled and the "
						"circuit has been proved dead. That is lock-out/tag-out — an OSHA standard "
						"with a written energy-control procedure behind it, not a padlock somebody "
						"keeps in the truck.</p>"
						"<p>Proving dead is three steps, in this order: <b>test the meter on a source "
						"you know is live, test the circuit, then test the meter on the known live "
						"source again.</b> A meter that reads zero volts proves nothing on its own — a "
						"dead meter, a blown fuse in the meter, or a probe that has come adrift all "
						"read exactly the same as a dead circuit.</p>"
						"<p>And this is water. Everything metallic around a water feature is bonded "
						"for a reason, NEC Article 680 says why, and a fault to ground here reaches "
						"people standing in the basin. Module 6 — Electrical Components and "
						"Module 9 — Jobsite Safety cover the standard. If the work is beyond "
						"measuring, it belongs to a qualified person.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Work from the supply toward the load",
					"content": (
						"<p>A circuit is a chain: source, overcurrent device, disconnect, controller or "
						"contactor, conductors, load. Power is either present at a link or it is not, "
						"so you walk the chain from the supply end with a meter, and <b>the first "
						"place the voltage disappears puts the fault between there and the last good "
						"point</b>. That is one measurement per halving, and it is why guessing at the "
						"load end wastes an afternoon.</p>"
						"<p>Two readings lie to people, and both look reasonable:</p>"
						"<ul>"
						"<li><b>Voltage present, load dead.</b> Voltage is only half a circuit. The "
						"return path — neutral, or the second leg — has to be intact too, and an open "
						"neutral leaves full voltage sitting at a load that cannot do anything with "
						"it.</li>"
						"<li><b>Voltage that collapses under load.</b> A loose lug, a corroded "
						"terminal or a damaged conductor reads perfectly normal with nothing drawing "
						"through it, and falls over the moment the motor starts. <b>Measure with the "
						"load running</b> when the complaint is intermittent or heat-related.</li>"
						"</ul>"
						"<p>A high-impedance meter will also show induced voltage on a conductor that "
						"is genuinely disconnected. That reading is real and it is not a supply. It is "
						"another reason to prove dead by procedure rather than by one number.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A breaker that trips is a measurement, not a nuisance",
					"content": (
						"<p>An overcurrent device and a GFCI are instruments. When one operates it is "
						"reporting a condition, and resetting it repeatedly is not a repair — it is "
						"ignoring the only instrument that noticed.</p>"
						"<p>A Class A GFCI trips at roughly <b>4-6 mA</b> of ground-fault current. That "
						"is a tiny leak, far below what a breaker sees, and it is set there because it "
						"is below the level that holds a person. Around water, a GFCI tripping is the "
						"system doing exactly the job it was installed for. Wet junction boxes, a "
						"submersible fixture with a failing seal, and a damaged cord are all common "
						"and all real.</p>"
						"<p>Halve it the same way as anything else: disconnect the loads on that "
						"circuit one at a time and reset between each. The load that brings the trip "
						"back is the load with the fault. <b>Never fit a larger breaker, and never "
						"replace a GFCI device with one that is not.</b> That does not remove the "
						"fault; it removes the thing that was finding it.</p>"
					),
				},
				ask_block(
					"Which of this work is yours to do",
					"<p>Where the line sits between measuring and working live, and what "
					"qualification it requires, is set by state and local law, by NFPA 70E and by "
					"Sapphire's own policy — not by a conversation on site. Which panels a "
					"Sapphire technician may open, and whose written energy-control procedure governs "
					"a given client's site, are settled between Sapphire and the site before the work "
					"starts.</p>"
					"<p>This lesson teaches what the readings mean. It is not a qualification, and "
					"nothing in it authorises anybody to open a piece of equipment they have not been "
					"trained and permitted to open. If you are not sure which side of that line you "
					"are on, you are on the far side of it — stop and ask.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A GFCI on a submersible lighting circuit trips a few minutes after every reset. What is the right conclusion?",
						"type": "Single Choice",
						"explanation": (
							"A Class A GFCI operates at roughly 4-6 mA of ground-fault current. It is reporting real "
							"leakage to ground — around water, that is the fault it exists to catch. Resetting it "
							"removes the instrument, not the problem."
						),
						"options": [
							{
								"text": "There is real leakage to ground, and the faulty load has to be found",
								"is_correct": True,
							},
							{
								"text": "The GFCI is over-sensitive and should be swapped for a standard breaker",
								"is_correct": False,
							},
							{
								"text": "It is a nuisance trip; reset it and note it on the ticket",
								"is_correct": False,
							},
							{
								"text": "The circuit is overloaded and needs a larger breaker",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A meter reads zero volts on a circuit. That alone proves the circuit is dead.",
						"type": "True-False",
						"explanation": (
							"A dead meter, a blown internal fuse or a probe that has come loose all read zero as "
							"well. The meter has to be proved on a known live source before and after the test."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
					{
						"question": "A motor circuit measures correct voltage with everything at rest, but the motor struggles and drops out when it starts. What does that pattern suggest?",
						"type": "Single Choice",
						"explanation": (
							"A loose or corroded connection carries no current at rest and reads normal. Under load "
							"it drops the voltage across itself, which is why the measurement has to be taken with "
							"the load running."
						),
						"options": [
							{
								"text": "A loose or corroded connection that only shows up under load",
								"is_correct": True,
							},
							{
								"text": "The meter is faulty, since it read correctly the first time",
								"is_correct": False,
							},
							{
								"text": "The supply voltage is correct, so the fault must be inside the motor windings",
								"is_correct": False,
							},
							{"text": "Induced voltage on a disconnected conductor", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Nozzles and spray features",
			"chapter": 1,
			"estimated_minutes": 12,
			"summary": "Four causes cover nearly every spray complaint, and identical nozzles tell you which one in seconds.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "First decide whether it is this nozzle or all of them",
					"content": (
						"<p>A display that is not doing what it used to is one of the easiest faults in "
						"this module, because the display usually contains its own control group: "
						"<b>a ring of identical nozzles, fed the same way, doing the same job</b>.</p>"
						"<p>So before anything else, look along the whole feature and ask which "
						"picture you have.</p>"
						"<p><b>One nozzle wrong, the rest right.</b> The fault is at that nozzle or in "
						"the short run of pipe behind it. The pump is fine. The filter is fine. Nobody "
						"needs to look at the equipment room.</p>"
						"<p><b>All of them wrong together.</b> The nozzles are reporting on something "
						"they share — pressure, flow, or air. Now it is a system fault, and it belongs "
						"in the no-flow lesson later in this module.</p>"
						"<p>That single glance is worth more than any amount of taking nozzles apart, "
						"and it costs nothing.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The four things it nearly always is",
					"panels": [
						{
							"title": "Obstruction",
							"body": (
								"<p>Something is in the orifice or in the line just behind it. The jet goes "
								"short, splits, sprays sideways, or fans where it should be solid. This is "
								"the most common cause by a wide margin, and on a site with a debris "
								"problem it is a symptom rather than the fault — the basin is delivering "
								"more than the strainers are catching.</p>"
							),
						},
						{
							"title": "The wrong pressure at the nozzle",
							"body": (
								"<p>A nozzle was chosen for a flow and a pressure. Below it the jet "
								"dribbles and loses height; above it an aerated nozzle goes thin and loud "
								"and a smooth-bore jet breaks up early instead of holding together. "
								"Pressure that is wrong at every nozzle is a pump, filter or valve "
								"question, not a nozzle question.</p>"
							),
						},
						{
							"title": "Air in the line",
							"body": (
								"<p>Spitting, surging, a pattern that is right for two seconds and wrong "
								"for the next two. Air is not a nozzle fault at all — the nozzle is simply "
								"the place where you can see it. It came in on the suction side, or the "
								"line was never purged after work.</p>"
							),
						},
						{
							"title": "Aim",
							"body": (
								"<p>A nozzle that was knocked, stood on, vacuumed against, or adjusted by "
								"a member of the public. It is hydraulically perfect and visually wrong, "
								"which is why it reads as a mystery until somebody sights along the ring "
								"instead of looking at each nozzle on its own.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A nozzle that spits is talking about the suction side",
					"content": (
						"<p>Air has to get in somewhere, and the pressure side cannot let it in — "
						"anything downstream of the impeller is above atmospheric pressure, so a hole "
						"there pushes water out. <b>The suction side is the only part of the system "
						"that can fall below atmospheric, so it is the only part that can pull air "
						"in.</b></p>"
						"<p>Which means a surging spray sends you to the pump lid O-ring, the drain "
						"plugs, the strainer pot, the suction union and the water level — not to the "
						"nozzle you are standing next to. Module 2 — Aquatic System Equipment "
						"Installation covers the same fault from the pump end, and the leak lesson "
						"later in this module covers why it leaves no puddle.</p>"
						"<p>A low water level does it too. Drop the level to where the suction starts "
						"drawing air and every nozzle on the feature reports it at once.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Clear it — do not open it up",
					"content": (
						"<p>A nozzle orifice is a machined dimension. The shape of the bore, its edge, "
						"and the swirl or aeration passages behind it are what produce the pattern, and "
						"they were chosen to produce that pattern at the design flow.</p>"
						"<p>So a blockage is cleared by <b>removing the blockage</b>: back-flush it, "
						"soak it, pick it out with something softer than the nozzle. A drill bit, a "
						"screwdriver or a piece of wire run down the bore permanently changes the "
						"nozzle, and the result is a jet that never looks right again and a fault "
						"nobody can find afterwards because the nozzle <i>is</i> clear.</p>"
						"<p>The same goes for swapping: two nozzles that thread into the same fitting "
						"and look alike from a metre away can be different orifice sizes and different "
						"patterns. One odd nozzle in a ring is visible from across the plaza.</p>"
					),
				},
				ask_block(
					"The display was designed, and the nozzles were selected",
					"<p>Which nozzle, at which orifice size, at which height, on which pattern is on "
					"the submittal and in the design drawings. So is the pressure it was selected "
					"for.</p>"
					"<p>That means two things on site. A nozzle is replaced with the <b>same</b> "
					"nozzle, not with what fits. And a display that has never looked right since "
					"start-up is not a troubleshooting problem — it is a design or commissioning "
					"question, and it goes back to the project manager with the drawing in hand.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "One nozzle in a ring of twenty is short and off-axis. The other nineteen look correct. What has that told you?",
						"type": "Single Choice",
						"explanation": (
							"Identical nozzles fed the same way are a built-in comparison. Nineteen correct ones "
							"prove the shared supply — pump, filter, pressure — is doing its job, so the fault is "
							"local to the odd one."
						),
						"options": [
							{
								"text": "The fault is at that nozzle or the pipe just behind it, not in the equipment room",
								"is_correct": True,
							},
							{
								"text": "System pressure has dropped and that nozzle shows it first",
								"is_correct": False,
							},
							{"text": "The filter is loading up and needs cleaning", "is_correct": False},
							{
								"text": "Nothing useful until every nozzle has been removed and inspected",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Every nozzle on a feature surges and spits, two seconds on and two seconds off. Where does the fault live?",
						"type": "Single Choice",
						"explanation": (
							"That is air. Everything downstream of the impeller is above atmospheric pressure and "
							"pushes water out, so air can only be drawn in on the suction side — or over a water "
							"level that has fallen to the suction."
						),
						"options": [
							{
								"text": "On the suction side, or at a water level low enough to draw air",
								"is_correct": True,
							},
							{
								"text": "In the nozzles, which have all partially blocked at once",
								"is_correct": False,
							},
							{
								"text": "In the pressure piping between the filter and the feature",
								"is_correct": False,
							},
							{
								"text": "At the nozzle threads, which are leaking under pressure",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A nozzle is blocked with scale and will not clear by soaking. What is wrong with opening the orifice out with a drill bit?",
						"type": "Single Choice",
						"explanation": (
							"The bore and its edge are a machined dimension that produces the pattern at the design "
							"flow. Drilling changes it permanently, and the next technician finds a clear nozzle "
							"that still sprays wrong."
						),
						"options": [
							{
								"text": "It permanently changes the pattern, and leaves a fault nobody can find later",
								"is_correct": True,
							},
							{
								"text": "Nothing, as long as the bit is smaller than the original orifice",
								"is_correct": False,
							},
							{
								"text": "It risks pushing the scale further down the supply line",
								"is_correct": False,
							},
							{
								"text": "It voids the nozzle's finish but has no effect on the spray",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Water loss in basins",
			"chapter": 1,
			"estimated_minutes": 14,
			"summary": "Separating evaporation from splash-out from a real leak, and why the bucket test is the standard way to do it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Three ways water leaves a basin",
					"content": (
						"<p>Only one of them is a repair, and they are routinely confused with each "
						"other because they all look the same on the tile: the level is lower than it "
						"was.</p>"
						"<p><b>Evaporation.</b> Water leaves as vapour from the surface. It goes faster "
						"with warm water, dry air, wind, and a larger surface — and a fountain "
						"deliberately makes a huge amount of surface by throwing water into the air. A "
						"spray feature evaporates far more than a still pool of the same footprint, "
						"and that is design, not fault.</p>"
						"<p><b>Wind-blow and splash-out.</b> Water is being thrown clear of the basin "
						"and landing on the deck. It rises and falls with the wind and with the spray "
						"height, so it changes week to week, and it leaves a wet plaza and a mineral "
						"ring beyond the coping as evidence.</p>"
						"<p><b>A leak.</b> Water is leaving through the shell, a penetration, a "
						"fitting, or a pipe. It does not care about the weather. A leak through the "
						"shell or a penetration runs whenever there is water standing against it; a "
						"leak in pressurised pipework runs only while the pump runs — so a loss that "
						"stops overnight on a feature that shuts down overnight has not ruled a leak "
						"out.</p>"
						"<p>That last sentence is the whole diagnosis: <b>the first two follow the "
						"weather and the third does not.</b> Everything below is a way of using that.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The bucket test, and why the bucket is the control",
					"content": (
						"<p>The bucket test works because it removes the one variable nobody can "
						"measure directly. Put a bucket of basin water in the basin, so the bucket and "
						"the basin sit in the same sun, the same air and the same water temperature. "
						"<b>They evaporate at the same rate from a still surface</b> — which is all the "
						"bucket controls for. It cannot reproduce the extra evaporation a running "
						"display creates by throwing water into the air. Mark both levels, leave it, "
						"and come back.</p>"
						"<p>Now the arithmetic does itself:</p>"
						"<ul>"
						"<li><b>Both dropped the same.</b> Evaporation accounts for all of it. There is "
						"nothing to chase.</li>"
						"<li><b>The basin dropped more than the bucket.</b> The difference is "
						"everything still-surface evaporation does not explain — with the display "
						"running that is the display's own extra evaporation, splash-out, or a "
						"leak.</li>"
						"</ul>"
						"<p>Then split those two the same way you split everything else. Run the test "
						"again with the feature <b>off</b> and the basin still. If the extra loss "
						"disappears, the display was doing it — its own extra evaporation, water going "
						"over the edge, or both. If the extra loss is still there with the water flat "
						"and the pump idle, it is leaving through the shell, and now you know that "
						"before anybody opens a deck.</p>"
						"<p>A third run — feature off but the pump and filtration circulating — narrows "
						"it again, because it separates a leak in the vessel from one that only exists "
						"when the pipework is pressurised.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "Make the measurement worth having",
					"content": (
						"<p>A bucket test is only as good as its conditions. Put the bucket where it "
						"will not be knocked, splashed into, or shaded when the basin is in sun. Mark "
						"both levels at the same moment, on something that will not move. Weight the "
						"bucket so it does not float or tip.</p>"
						"<p>And record the weather. A still, overcast test and a windy, hot one "
						"produce different numbers from the same basin, which is exactly why the "
						"bucket is in the picture at all.</p>"
						"<p>One test is a data point. The value comes from comparing it to the same "
						"test on the same basin in similar conditions, which is an argument for "
						"writing it down rather than remembering it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "An autofill hides all of this",
					"content": (
						"<p>A working autofill keeps the level exactly where it should be no matter how "
						"much water is being lost. The symptom the client would have noticed never "
						"appears. What appears instead is a water bill, chemistry that drifts in one "
						"direction forever, and eventually scale — because every gallon of make-up "
						"brings the source water's hardness and alkalinity in with it and the "
						"evaporation leaves them behind.</p>"
						"<p>So two things follow. <b>Isolate or disable the autofill before a bucket "
						"test</b>, or the basin cannot drop and the test measures nothing. And "
						"<b>treat an unexplained make-up volume as a symptom in its own right</b> — if "
						"there is a meter on the fill, read it; if there is not, that is worth saying "
						"out loud, because it is the cheapest leak detector a fountain can have.</p>"
					),
				},
				ask_block(
					"What counts as normal loss here",
					"<p>How much a given basin loses on a given day is a property of that basin: its "
					"surface area, its spray height, its exposure, the local climate and the season. "
					"There is no figure this course can print that would be right on the next "
					"site.</p>"
					"<p>Which means the useful number is the one <i>this</i> feature produced when it "
					"was known to be sound, and the standing question of what Sapphire treats as "
					"acceptable, what triggers a leak investigation, and who pays for opening a deck "
					"belongs to the service agreement and to the project manager — not to a technician "
					"standing at the basin with a bucket.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a bucket of basin water used as the reference in a bucket test?",
						"type": "Single Choice",
						"explanation": (
							"The bucket sits in the same water, air and sun as the basin, so it evaporates at the "
							"same rate. The difference between the two drops is therefore everything that is not "
							"evaporation."
						),
						"options": [
							{
								"text": "It evaporates under identical conditions, so it subtracts evaporation from the measurement",
								"is_correct": True,
							},
							{
								"text": "It holds a known volume, so the loss can be converted to gallons",
								"is_correct": False,
							},
							{
								"text": "It keeps a sample of water at a stable temperature for testing",
								"is_correct": False,
							},
							{
								"text": "It shows whether the water is absorbing into the basin finish",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A bucket test runs with the feature in normal operation. The basin dropped noticeably more than the bucket. The test is repeated with the feature switched off and the basin still, and now both drop the same. What does that show?",
						"type": "Single Choice",
						"explanation": (
							"The extra loss exists only while the display runs and disappears when the water is "
							"still, so it is not leaving through the shell. That is all the second run proves — "
							"that the extra loss was display-dependent, not what it was. It could be the extra "
							"evaporation the display itself creates by throwing water into the air, water thrown "
							"clear of the basin, or pipework that is only pressurised while the pump runs — and the "
							"third run, feature off with the pump still circulating, separates that last one from "
							"the other two."
						),
						"options": [
							{
								"text": "It happens only while the display runs — the extra evaporation the display itself creates, splash-out, or pipework that leaks only under pressure — and it is not the shell",
								"is_correct": True,
							},
							{
								"text": "There is a leak in the shell that seals itself when the water is still",
								"is_correct": False,
							},
							{
								"text": "The first test was invalid and both should be discarded",
								"is_correct": False,
							},
							{
								"text": "Evaporation accounts for all of it, since the bucket and the basin agreed on the second test",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of a basin with a working autofill?",
						"type": "Multiple Choice",
						"explanation": (
							"The autofill holds the level, so the visible symptom never appears; the loss shows up "
							"as make-up volume and as chemistry that drifts one way. It has to be isolated before a "
							"bucket test, or the basin cannot drop."
						),
						"options": [
							{
								"text": "A significant leak can run for a long time with no visible symptom",
								"is_correct": True,
							},
							{
								"text": "It must be isolated or disabled before a bucket test means anything",
								"is_correct": True,
							},
							{
								"text": "Constant make-up water steadily adds the source water's hardness and alkalinity",
								"is_correct": True,
							},
							{
								"text": "It proves the basin is watertight, since the level never drops",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Leaks in piping",
			"chapter": 1,
			"estimated_minutes": 13,
			"summary": "Where pipe actually fails, why the wet spot is not the hole, and how to halve a system with the valves already on it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Pipe does not usually fail in the middle",
					"content": (
						"<p>A straight run of sound pipe sitting undisturbed is the least likely part "
						"of the system to be leaking. Failures happen where there is a "
						"<b>discontinuity</b> — somewhere two things were joined, or somewhere the pipe "
						"is made to change.</p>"
						"<p>So the search order is joints, fittings, penetrations, and anywhere the "
						"pipe is restrained while something around it moves:</p>"
						"<ul>"
						"<li><b>Solvent-weld joints</b>, especially any made in the cold, in the wet, "
						"or in a hurry. Module 1 — Piping &amp; Hydraulics describes the joint that "
						"passes its test and fails later, and that is exactly the one you are looking "
						"for now.</li>"
						"<li><b>Threaded fittings</b>, which are the most common leak in a system that "
						"has been serviced, because they are the parts that get taken apart.</li>"
						"<li><b>Penetrations</b> through a wall or a vessel, where the seal, the "
						"sleeve and the structure all move at different rates.</li>"
						"<li><b>Equipment connections</b> — unions, pump ports, filter clamps, valve "
						"bodies, gauge fittings, drain plugs.</li>"
						"<li><b>Anywhere the ground moved.</b> A pipe held rigid at one end and "
						"settling at the other fails at the rigid end.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A suction-side leak can leave nothing to find",
					"content": (
						"<p>A hole behaves in opposite ways on either side of the impeller, because "
						"the pressure either side of it is not the same.</p>"
						"<p><b>Downstream of the impeller</b> the pressure pushes water out of the "
						"hole. You get a wet spot, a drip, a spray, a puddle — something to find.</p>"
						"<p><b>Upstream of the impeller</b>, whenever the pump pulls that line below "
						"atmospheric pressure, the hole <i>pulls air in</i> instead. Now there is air "
						"in the strainer pot, bubbles at the returns, a spray that surges, a pump that "
						"loses prime — and <b>no water anywhere on the ground at all</b>.</p>"
						"<p>That last one depends on where the pump sits, so establish it first. A "
						"pump below the water line has a flooded suction, which stands at positive "
						"pressure whenever the pump is off — so the same hole can weep water overnight "
						"and draw air by day.</p>"
						"<p>Either way, this is the fault that sends a crew hunting a puddle that does "
						"not exist. If the symptoms are air and the ground is dry, stop looking for "
						"water and start on the suction side: the pump lid O-ring, the drain plugs, "
						"the suction union, threaded fittings, and the shaft seal.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Halve the system with the valves that are already on it",
					"content": (
						"<p>Most systems can be cut into sections with the isolation valves already "
						"installed, and a pressure gauge turns that into a real measurement rather "
						"than an opinion.</p>"
						"<p>Isolate a section, bring it to pressure, shut it in, and watch whether it "
						"holds. A section that holds is eliminated — completely, permanently, with one "
						"test. A section that drops contains the leak, and you halve it again.</p>"
						"<p>Two disciplines make that trustworthy. <b>Let the temperature and the "
						"trapped air settle before reading</b>, because both move a gauge without any "
						"water being lost. And <b>use water, never compressed air</b> — Module 1 — "
						"Piping &amp; Hydraulics explains why at length, and the short version is that "
						"plastic pipe failing under air does not leak, it shatters, and people have "
						"been killed doing it.</p>"
						"<p>If the system has no isolation valves, that is a finding worth recording. "
						"It is the difference between a one-hour leak hunt and a day with a machine on "
						"the next call as well.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Water travels before it appears",
					"content": (
						"<p>Water follows the easiest path, not the shortest one. It runs along the "
						"outside of a pipe, along the top of a membrane, down a bedding trench, and "
						"comes out at the first low point or the first crack it can reach.</p>"
						"<p>So the wet spot on the deck marks <b>where the water got out</b>, which is "
						"downhill and usually downstream of where it got in. Work uphill from it, and "
						"treat the visible damp as the last piece of evidence rather than the "
						"first.</p>"
						"<p>Buried and slab-embedded lines make this worse, because there is often no "
						"wet spot at all — the water simply joins the groundwater and nothing on the "
						"surface changes. On those, the isolate-and-hold test is not a shortcut; it is "
						"the only honest method available, and a section that will not hold pressure "
						"is the finding, even with nothing to see.</p>"
					),
				},
				ask_block(
					"Opening a deck is not a technician's decision",
					"<p>Test pressures, hold times and what counts as an acceptable loss come from the "
					"project specification and the governing code, not from memory — Module 1 — Piping "
					"&amp; Hydraulics makes the same point about the original test.</p>"
					"<p>And once a leak has been localised to something buried, under a slab, or "
					"behind finished stone, the next step is a cost, a schedule and often a client "
					"conversation. Localise it, document what you tested and what held, and hand the "
					"decision to the project manager. Cutting first and asking afterwards is how a "
					"leak repair becomes a stone repair.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A system loses prime, the strainer pot holds air, the returns blow bubbles, and there is no water anywhere on the ground. Where is the leak?",
						"type": "Single Choice",
						"explanation": (
							"A suction line the pump has pulled below atmospheric pressure draws air in through "
							"a hole instead of pushing water out. Air symptoms with a dry floor is the signature "
							"of a suction-side leak."
						),
						"options": [
							{"text": "On the suction side, upstream of the impeller", "is_correct": True},
							{
								"text": "On the pressure side, but small enough to evaporate before it is seen",
								"is_correct": False,
							},
							{"text": "In the basin shell, below the waterline", "is_correct": False},
							{
								"text": "There is no leak — air symptoms always mean a failing impeller",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why does a leak search start at joints, fittings and penetrations rather than along the pipe?",
						"type": "Single Choice",
						"explanation": (
							"Those are the discontinuities — where two things were joined, where something was "
							"taken apart, or where the pipe is held while its surroundings move. Sound, undisturbed "
							"pipe rarely fails in the middle."
						),
						"options": [
							{
								"text": "They are the discontinuities, and sound undisturbed pipe rarely fails mid-run",
								"is_correct": True,
							},
							{
								"text": "They are easier to reach, so it saves time even if it is less likely",
								"is_correct": False,
							},
							{
								"text": "Pipe walls fail gradually, so a mid-run leak would already be obvious",
								"is_correct": False,
							},
							{
								"text": "Only fittings are under pressure; the pipe between them is not",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A damp patch appears on the deck at the low end of a long buried run. What does that locate?",
						"type": "Single Choice",
						"explanation": (
							"Water follows the easiest path and surfaces at the first low point or crack it can "
							"reach. The damp marks where the water got out, not where it got in — so you work "
							"uphill from it."
						),
						"options": [
							{
								"text": "Where the water got out, which is downhill of where it got in",
								"is_correct": True,
							},
							{"text": "The failed joint, directly under the damp patch", "is_correct": False},
							{"text": "Nothing at all, since buried leaks never surface", "is_correct": False},
							{
								"text": "A failure of the deck waterproofing rather than the pipe",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Scaling and deposit buildup",
			"chapter": 2,
			"estimated_minutes": 14,
			"summary": "Why scale is diagnosed with a calculation rather than a scraper, and how to tell one white deposit from another.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Scale is a property of the water, not of the surface",
					"content": (
						"<p>Scale is calcium coming out of solution and depositing on whatever it "
						"touches. Whether the water is inclined to do that is not a judgement — it is "
						"a calculation, and it is the <b>Langelier Saturation Index</b>.</p>"
						"<p>The LSI is pH, plus a temperature factor, plus a calcium hardness factor, "
						"plus an alkalinity factor, minus a constant for total dissolved solids. "
						"Balanced water sits near zero. <b>Positive means the water tends to deposit "
						"scale. Negative means it is aggressive</b> and tends to dissolve what it is "
						"standing in.</p>"
						"<p>That is why the scraper is the wrong instrument. You can clean every "
						"surface in the feature, and if the index is still positive the water will put "
						"it all back, because nothing about the water changed. Cleaning resets the "
						"clock; the index decides how fast it runs. Module 3 — Water Chemistry is "
						"where the measuring and the correcting live.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Where it appears first tells you why",
					"content": (
						"<p>Scale does not arrive evenly, and the pattern is diagnostic.</p>"
						"<p><b>The hottest surface scales first.</b> Calcium carbonate is one of the "
						"substances that becomes <i>less</i> soluble as temperature rises, which is "
						"backwards from most things people dissolve in water. So a heater element or a "
						"heat exchanger scales before anything else on the same water, and a heater "
						"that has lost output is often a chemistry finding wearing a mechanical "
						"costume.</p>"
						"<p><b>The waterline gets a band</b>, because that is where water evaporates "
						"and leaves its minerals behind on a surface it keeps re-wetting.</p>"
						"<p><b>Nozzles and spray edges scale</b> for the same reason — the water is "
						"thrown into the air, evaporates, and concentrates. A feature that loses a lot "
						"to evaporation and is topped up constantly is concentrating hardness with "
						"every gallon of make-up, which is the link between the water-loss lesson "
						"above and this one.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Over-correcting scale produces the opposite failure",
					"content": (
						"<p>Aggressive water is the same index with the other sign, and it is not "
						"harmless. Water with a negative LSI dissolves what it is sitting in: it etches "
						"plaster, softens grout and mortar, leaches the surface of concrete, corrodes "
						"metals, and dulls polished stone. The damage is permanent and it is "
						"expensive.</p>"
						"<p>So a scale complaint is not a licence to push the chemistry as far in the "
						"other direction as it will go. <b>The target is balance, not the absence of "
						"scale.</b> Chase one number hard enough, on its own, and you trade a deposit "
						"you can remove for damage you cannot.</p>"
						"<p>Acid is also the most dangerous thing most technicians carry. It has its "
						"own handling rules, its own personal protective equipment, and a rule about "
						"never mixing products that is not negotiable. Module 3 — Water Chemistry and "
						"Module 9 — Jobsite Safety cover both.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Telling one white deposit from another",
					"cards": [
						{
							"front": "Carbonate scale",
							"back": "Hard, usually white or grey, at the waterline, on nozzles and on the hottest surfaces. A drop of acid fizzes on it, because a carbonate releases carbon dioxide when acid reaches it.",
						},
						{
							"front": "Biofilm",
							"back": "Slippery rather than hard, and it wipes off with a cloth. It is alive, it consumes sanitiser continuously, and it returns unless the conditions that grew it change.",
						},
						{
							"front": "Metal staining",
							"back": "Coloured rather than white — brown, green, grey or blue-green depending on the metal. Scrubbing does not touch it, because it is in the surface, not on it. The metal came from the water or from something in the system corroding.",
						},
						{
							"front": "Efflorescence",
							"back": "Salts carried out of concrete, grout or mortar by water moving through the material and evaporating at the face. It keeps returning while water keeps moving through, which makes it a waterproofing question rather than a chemistry one.",
						},
						{
							"front": "Aggressive water",
							"back": "A negative LSI. The same calculation, the opposite sign, and the opposite failure: the water dissolves plaster, grout, mortar and metals instead of coating them.",
						},
					],
				},
				ask_block(
					"Targets and descalers belong to the finish",
					"<p>What the index should be held at, what the hardness and alkalinity targets "
					"are, and which product is used to remove an existing deposit all depend on what "
					"the feature is made of. Natural stone, coloured plaster, tile, glass mosaic and "
					"stainless steel do not tolerate the same treatment, and the wrong descaler "
					"destroys a finish in minutes in a way no amount of scale would have.</p>"
					"<p>Read the finish schedule, the submittal, and the product label for the "
					"surface actually in front of you. If nobody can tell you what the surface is, "
					"that question goes up before anything is applied to it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What does a persistently positive Langelier Saturation Index mean?",
						"type": "Single Choice",
						"explanation": (
							"The LSI balances near zero. Positive means the water tends to deposit scale; negative "
							"means it is aggressive and tends to dissolve plaster, grout, mortar and metals."
						),
						"options": [
							{
								"text": "The water tends to deposit scale on the surfaces it touches",
								"is_correct": True,
							},
							{
								"text": "The water is aggressive and will etch plaster and grout",
								"is_correct": False,
							},
							{"text": "The sanitiser level is too high", "is_correct": False},
							{
								"text": "The water is balanced, since any positive number is above the aggressive range",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why does a heater or heat exchanger scale before anything else on the same water?",
						"type": "Single Choice",
						"explanation": (
							"Calcium carbonate becomes less soluble as temperature rises, which is the opposite of "
							"most dissolved substances. The hottest surface in the system is therefore where it "
							"comes out of solution first."
						),
						"options": [
							{
								"text": "Calcium carbonate becomes less soluble as the water gets hotter",
								"is_correct": True,
							},
							{
								"text": "The heater raises the pH of the water passing through it",
								"is_correct": False,
							},
							{
								"text": "Flow through the heater is slower, so minerals settle out",
								"is_correct": False,
							},
							{
								"text": "Heater surfaces are rougher, so deposits key into them",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A set of nozzles is descaled on every visit and is furred up again by the next one. What is the actual fix?",
						"type": "Single Choice",
						"explanation": (
							"Cleaning removes the deposit but changes nothing about the water. While the index stays "
							"positive the scale comes back — the correction has to happen in the chemistry."
						),
						"options": [
							{
								"text": "Correct the water balance, because cleaning only resets the clock",
								"is_correct": True,
							},
							{"text": "Descale more often, and with a stronger product", "is_correct": False},
							{
								"text": "Replace the nozzles with a material scale does not stick to",
								"is_correct": False,
							},
							{"text": "Raise the pH so the deposit dissolves on its own", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "No flow",
			"chapter": 2,
			"estimated_minutes": 14,
			"summary": "Two gauges divide the system at the impeller, and the pair of readings names the half the fault is in.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Two gauges cut the system in half at the impeller",
					"content": (
						"<p>A filtration system carries a pressure gauge downstream of the pump, and "
						"where the suction side is instrumented at all, a vacuum — compound — gauge on "
						"it as well. Most people read the pressure gauge on its own and learn very "
						"little. <b>Read them as a pair and they name "
						"the half of the system the fault is in</b>, in one look, before anything is "
						"opened.</p>"
						"<p>The logic is simple. Restriction shows up as pressure building against it. "
						"A restriction <i>after</i> the impeller makes the pump work against a wall, so "
						"the discharge pressure climbs. A restriction <i>before</i> the impeller starves "
						"it, so the suction vacuum climbs. And a pump that has stopped doing work "
						"cannot produce either.</p>"
						"<p>So of the three readings — pressure, vacuum, and actual water arriving at "
						"the feature — you only need the first two to know where to walk.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Reading the pair",
					"panels": [
						{
							"title": "Pressure up, vacuum normal — the restriction is downstream",
							"body": (
								"<p>The pump is getting water and cannot push it out. Look after the "
								"impeller: a loaded filter, a valve closed or partly closed, blocked "
								"returns or nozzles, a scaled or collapsed line, a heater or treatment "
								"unit full of debris.</p>"
								"<p>A filter climbing steadily over time is the ordinary version of this "
								"and is a maintenance item. A filter that jumped since the last visit is "
								"a debris event, and that has its own lesson later in this module.</p>"
							),
						},
						{
							"title": "Vacuum climbing, pressure down — the restriction is upstream",
							"body": (
								"<p>The pump cannot get enough water to move. Look before the impeller: a "
								"full skimmer basket, a packed strainer pot, a blocked suction line or "
								"intake screen, a closed or half-closed suction valve, a collapsed "
								"flexible hose, or a water level that has dropped below where the suction "
								"can reach.</p>"
								"<p>This is the reading to take seriously even when the feature still "
								"looks acceptable, because a starved pump is also a pump that is about to "
								"cavitate.</p>"
							),
						},
						{
							"title": "Both low, with the pump running — the pump is not doing work",
							"body": (
								"<p>Neither gauge shows the pump fighting anything, and the water is not "
								"moving. The pump itself is the suspect: lost prime, air on the suction "
								"side, an impeller worn or packed with debris, a motor not up to speed, or "
								"a three-phase motor that has been reconnected and is running backwards — "
								"which still moves some water and still sounds like a pump.</p>"
								"<p>Module 2 — Aquatic System Equipment Installation covers prime and "
								"rotation, and the last lesson in this module walks the pump itself in "
								"order.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A dead gauge reads a perfectly plausible number",
					"content": (
						"<p>This is the trap in the whole method. A gauge whose movement has seized, "
						"whose line is plugged with debris, or whose internals have corroded does not "
						"read zero and it does not read off the scale. <b>It sits on a believable "
						"number and never moves again</b> — and a believable number is exactly what you "
						"came to read.</p>"
						"<p>Prove the gauge before you trust it. It should fall to zero with the pump "
						"off and the system relieved, and it should visibly respond when you change "
						"something — close a valve part way, clean a basket, open an air relief. A "
						"gauge that does not move when the system does is not measuring the "
						"system.</p>"
						"<p>The same suspicion applies to a reading that contradicts what your eyes "
						"and ears are telling you. Measure again, somewhere else, with something "
						"else.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Before any of that: what changed, and is there water",
					"content": (
						"<p>Most no-flow calls are resolved before a gauge is read, by the two "
						"questions at the top of this module.</p>"
						"<p><b>Is the water where it needs to be?</b> A level below the skimmer throat "
						"or the intake starves the pump and draws air, and a basin that has been losing "
						"water all week gets there on its own.</p>"
						"<p><b>What changed?</b> A valve turned during a repair and not turned back. A "
						"filter cleaned and a valve left in the service position. A timer or controller "
						"adjusted. Power restored after an outage and something not restarted. "
						"Electrical work upstream and a three-phase motor reconnected the other way "
						"round. Ask the person who was last on site, and look at the valve positions "
						"before you look at anything else.</p>"
						"<p>None of that is beneath a technician. It is the cheapest half of the "
						"system to eliminate, and it eliminates a great deal of it.</p>"
					),
				},
				ask_block(
					"Normal pressure is this system's own number",
					"<p>There is no universal pressure a filter should run at. It depends on the pump, "
					"the pipe, the filter, the feature and the height it is lifting to. The only "
					"figure that means anything is the <b>clean baseline recorded for this system</b> "
					"when it was commissioned and known to be right, and the manufacturer's maximum "
					"working pressure for the equipment in it.</p>"
					"<p>So look for the start-up record, the equipment schedule and the data plate. "
					"And if nobody ever recorded a baseline for this site, that is itself a finding "
					"worth raising — without it, every future pressure reading here is a number with "
					"nothing to compare it to.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "The filter pressure gauge is well above its usual reading, the suction vacuum looks normal, and flow at the feature is poor. Where is the fault?",
						"type": "Single Choice",
						"explanation": (
							"The pump is getting water and cannot push it out, so the restriction is after the "
							"impeller: a loaded filter, a closed valve, blocked returns or nozzles."
						),
						"options": [
							{
								"text": "Downstream of the pump — a loaded filter, a closed valve, or blocked returns",
								"is_correct": True,
							},
							{
								"text": "Upstream of the pump — a blocked basket or suction line",
								"is_correct": False,
							},
							{"text": "In the pump itself — a worn impeller", "is_correct": False},
							{"text": "The water level has dropped below the skimmer", "is_correct": False},
						],
					},
					{
						"question": "The pump is running, both gauges read low, and no water is reaching the feature. What does that pair of readings point to?",
						"type": "Single Choice",
						"explanation": (
							"Neither gauge shows the pump working against anything. The pump is not moving water at "
							"all — lost prime, air, a worn or blocked impeller, low speed, or reversed rotation."
						),
						"options": [
							{
								"text": "The pump is not doing work — prime, air, impeller, speed or rotation",
								"is_correct": True,
							},
							{"text": "A severe downstream restriction", "is_correct": False},
							{"text": "A blocked strainer basket on the suction side", "is_correct": False},
							{"text": "The gauges are correct and the system is normal", "is_correct": False},
						],
					},
					{
						"question": "How do you know a pressure gauge is still telling the truth?",
						"type": "Single Choice",
						"explanation": (
							"A failed gauge parks on a plausible number and stops moving. It has to fall to zero "
							"with the system relieved and visibly respond when the system is changed — otherwise it "
							"is not measuring anything."
						),
						"options": [
							{
								"text": "It returns to zero when the system is relieved and moves when the system changes",
								"is_correct": True,
							},
							{
								"text": "It reads a number that is within the normal range for fountains",
								"is_correct": False,
							},
							{
								"text": "It agrees with the reading written on the last service ticket",
								"is_correct": False,
							},
							{"text": "The needle is steady rather than fluctuating", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Excessive debris in basin",
			"chapter": 2,
			"estimated_minutes": 12,
			"summary": "Debris is a rate that belongs to the site, and the fix is usually upstream of the filtration rather than inside it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Debris is a rate, and it belongs to the site",
					"content": (
						"<p>Skimming and filtration were sized for an expected load. The site delivers "
						"whatever it delivers. When those two disagree, the equipment is not faulty — "
						"it is being asked to do a job it was never specified for, and no amount of "
						"cleaning the strainer changes that.</p>"
						"<p>So the useful question is not <i>how dirty is it</i> but <b>how fast does "
						"it fill</b>. Empty a basket, note the time, look again later in the visit and "
						"on the next one. A rate can be compared between visits, between seasons and "
						"against what the system was designed to handle. An impression cannot, and an "
						"impression is what usually gets written down.</p>"
						"<p>A rate also tells you about causes. A load that spikes for two weeks in "
						"autumn is a tree. A load that appears with a wind direction is something "
						"upwind. A load that started the week the plaza was pressure-washed is the "
						"plaza.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "On a pedestal deck, the plaza is the catchment",
					"content": (
						"<p>A fountain built over a reservoir with a pedestal-paver deck above it is "
						"designed to let water fall between the pavers and return below. That is the "
						"whole point of the construction, and it works.</p>"
						"<p>What it also means is that <b>everything on the plaza ends up in the "
						"reservoir</b>. Leaves, grit, cigarette ends, coins, wrappers, drink spills, "
						"sand from winter treatment, dust from work on the building, and whatever is "
						"swept toward the feature by somebody trying to be helpful. There is no filter "
						"between the plaza and the water — the deck <i>is</i> the inlet.</p>"
						"<p>Two consequences worth carrying. The reservoir under a deck accumulates "
						"quietly, because nobody can see it, so it is the debris problem that is "
						"discovered late. And the fix is almost always on top of the deck — how the "
						"plaza is cleaned, where it drains, where it is swept to, what is planted "
						"beside it — rather than inside the equipment room.</p>"
						"<p>One thing before anybody lifts a paver. A reservoir under a deck, like a "
						"vault or a below-grade equipment pit, has restricted entry and egress and was "
						"never built for a person to work inside — which is what makes it a "
						"<b>confined space</b> rather than an awkward one. That is a formal OSHA "
						"standard, and the written entry program, the atmospheric testing, the "
						"attendant and the rescue arrangements behind it exist because people die in "
						"these and then so do the people who go in after them. Inspect it from "
						"outside. If the work needs somebody inside, it needs somebody trained and "
						"permitted to be inside, and Module 9 — Jobsite Safety is where that "
						"lives.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Debris is not a nuisance, it is the next three faults",
					"content": (
						"<p>It is easy to treat debris as a housekeeping complaint. It is not. It is "
						"the upstream cause of most of the other lessons in this module.</p>"
						"<ul>"
						"<li><b>It restricts the suction</b> — a packed basket or strainer starves the "
						"pump, which is the climbing-vacuum reading in the no-flow lesson, and a "
						"starved pump cavitates and damages its own impeller.</li>"
						"<li><b>It blocks nozzles</b>, which is the single most common spray complaint "
						"there is.</li>"
						"<li><b>It consumes sanitiser</b> — organic debris keeps reacting with whatever "
						"you dose, which is why a feature under a tree can look like a chemistry "
						"problem that will not respond.</li>"
						"<li><b>It reaches the pump</b> if a basket is missing, damaged or not seated, "
						"and then it is inside the impeller rather than in front of it.</li>"
						"</ul>"
						"<p>Which means clearing debris is a repair, not a courtesy — and a missing or "
						"cracked strainer basket is a fault in its own right.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Look here before blaming the filtration",
					"items": [
						"What overhangs or stands upwind of the basin, and what it drops, and when",
						"Which way the prevailing wind runs across the feature",
						"Whether the skimmers sit where the wind pushes debris, or on the far side from it",
						"Whether the plaza, deck or surrounding grade drains into the basin",
						"Whether anything nearby is under construction, being cleaned, or being landscaped",
						"Whether every basket and strainer is present, intact and properly seated",
						"Whether the reservoir under a pedestal deck has ever been inspected, and who is trained and permitted to enter it",
						"How fast a basket refills after you empty it, timed rather than estimated",
						"Who has been emptying the baskets between service visits, if anybody",
					],
				},
				ask_block(
					"How often, and whose job — and whether to propose a change",
					"<p>How frequently baskets are emptied, whether a reservoir sweep is inside the "
					"service scope, and who does the housekeeping between visits are contract "
					"questions. This course will not invent a frequency, because the right one depends "
					"entirely on the load this site produces.</p>"
					"<p>And when the measurement says the site genuinely delivers more than the design "
					"can handle, the answer is a recommendation — extra skimming, a screen, a change "
					"to the plaza drainage, different planting — which is a design and commercial "
					"conversation for the project manager and the client, not something to fit on a "
					"service visit.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A strainer basket is packed full on every visit. What does that actually tell you?",
						"type": "Single Choice",
						"explanation": (
							"The site is delivering more debris than the skimming and filtration were sized for "
							"between visits. That is a mismatch between the design load and the real load, and the "
							"fix is often upstream of the equipment."
						),
						"options": [
							{
								"text": "The site's debris load exceeds what the system was designed to handle between visits",
								"is_correct": True,
							},
							{
								"text": "The strainer basket is undersized and should be replaced with a larger one",
								"is_correct": False,
							},
							{
								"text": "The filter is failing to catch material it should be catching",
								"is_correct": False,
							},
							{
								"text": "Nothing in particular — a full basket is normal on any fountain",
								"is_correct": False,
							},
						],
					},
					{
						"question": "On a fountain built over a reservoir with a pedestal-paver deck, where does debris left on the plaza end up?",
						"type": "Single Choice",
						"explanation": (
							"The deck is designed to let water fall between the pavers into the reservoir below. "
							"There is nothing between the plaza and the water, so the deck is effectively the inlet."
						),
						"options": [
							{
								"text": "In the reservoir below the deck, because the deck is the inlet",
								"is_correct": True,
							},
							{
								"text": "In the skimmers, which is what they are there for",
								"is_correct": False,
							},
							{
								"text": "On the deck surface, where it can be swept up later",
								"is_correct": False,
							},
							{
								"text": "In the plaza's storm drainage, separate from the fountain",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What does an uncontrolled debris load cause downstream?",
						"type": "Multiple Choice",
						"explanation": (
							"Debris restricts the suction and starves the pump, blocks nozzles, and keeps consuming "
							"sanitiser as organic material reacts with it. It does not add calcium to the water."
						),
						"options": [
							{"text": "Suction restriction and a starved pump", "is_correct": True},
							{
								"text": "Blocked nozzles and a spray pattern that has changed",
								"is_correct": True,
							},
							{
								"text": "Sanitiser that will not hold, because organic load keeps consuming it",
								"is_correct": True,
							},
							{"text": "A steady rise in calcium hardness", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Chemicals not balancing",
			"chapter": 2,
			"estimated_minutes": 15,
			"summary": "Three reasons chemistry refuses to move: the wrong correction order, a test that is lying, or a source nobody has counted.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Alkalinity first, then pH",
					"content": (
						"<p>Water chemistry is not a set of independent dials, and treating it as one "
						"is the most common reason a feature will not balance.</p>"
						"<p><b>Total alkalinity is the buffer.</b> It is the water's resistance to "
						"having its pH changed. Get it right and pH becomes something you can set and "
						"it stays put. Get it wrong and pH stops behaving like a number you control:</p>"
						"<ul>"
						"<li><b>Alkalinity too low</b> and the pH bounces. A small addition swings it "
						"a long way, it swings back, and every correction overshoots. People describe "
						"this as the chemistry being unstable, and it is — that is precisely what a "
						"missing buffer means.</li>"
						"<li><b>Alkalinity too high</b> and the pH resists correction, then drifts "
						"upward again regardless. You dose, it moves a little, and by the next visit "
						"it is back.</li>"
						"</ul>"
						"<p>So the order of operations is alkalinity, then pH, then calcium hardness, "
						"then sanitiser and stabiliser. Correcting pH first, on water with no buffer, "
						"is work that undoes itself. Module 3 — Water Chemistry has the method; this "
						"lesson is about why it keeps not working.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A fountain is an aeration machine",
					"content": (
						"<p>This is the thing that makes fountain chemistry different from a still "
						"pool, and it catches people who learned on pools.</p>"
						"<p>Throwing water into the air drives <b>carbon dioxide out of it</b>, and "
						"losing carbon dioxide raises pH. A tall spray, a weir, a cascade and an "
						"aerated nozzle are all, chemically speaking, devices for stripping CO2 out of "
						"the water as efficiently as possible. So the pH on a spray feature climbs, "
						"you correct it, and it climbs again — not because the correction failed, but "
						"because the feature is doing what it was built to do.</p>"
						"<p>That is a fact to manage rather than a fault to fix. It is also the "
						"clearest illustration of why alkalinity comes first: the buffer is what "
						"decides how fast that climb happens and how far it goes.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Before you dose again, doubt the test",
					"content": (
						"<p>If you have dosed the same water three times against the same reading and "
						"nothing has moved, the most likely explanation is that the reading is wrong. "
						"Dosing against a bad test does not just waste product — it moves real "
						"chemistry to correct a number that was never there.</p>"
						"<p>Tests go wrong in ordinary ways:</p>"
						"<ul>"
						"<li><b>Reagents.</b> They expire, and they degrade faster in a hot truck. "
						"Dropper tips get cross-contaminated between bottles. Test strips are "
						"especially sensitive to heat and humidity in the tube.</li>"
						"<li><b>The sample.</b> Water taken at the surface, next to a return, or right "
						"at a chemical feed point is not representative of the body of water. Take it "
						"away from returns and feed points, and below the surface.</li>"
						"<li><b>The instrument.</b> A pH probe that has not been calibrated, or has "
						"dried out or fouled, produces confident numbers that are simply untrue — "
						"which is worse than no reading at all.</li>"
						"<li><b>The reading.</b> Colour matching in poor light, in coloured shade, or "
						"by somebody who does not see those colours the same way, is a real source of "
						"error and nobody should be embarrassed by it.</li>"
						"</ul>"
						"<p>Re-test with fresh reagent, from a proper sample, and where you can, "
						"confirm against a second method or a second person.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Sources nobody counted",
					"panels": [
						{
							"title": "Make-up water",
							"body": (
								"<p>Every gallon of fill brings the source water's hardness, alkalinity and "
								"everything else in it. Evaporation leaves all of that behind and takes "
								"only pure water away, so a feature that evaporates hard and is topped up "
								"constantly is concentrating minerals continuously. If the chemistry moves "
								"the same direction every single time, test what you are filling "
								"with.</p>"
							),
						},
						{
							"title": "A feeder doing something other than what you think",
							"body": (
								"<p>A chemical feed pump running when it should not, a stuck check valve, "
								"an erosion feeder left wide open, a controller with a setpoint somebody "
								"changed, or a tank filled with the wrong product. A feeder is the one "
								"part of the system that can add chemistry between your visits, which "
								"makes it the first thing to verify when the water moves without you.</p>"
							),
						},
						{
							"title": "Cyanuric acid",
							"body": (
								"<p>Stabiliser does not evaporate and it is not consumed. It only leaves by "
								"dilution, so on a feature that is topped up rather than drained it "
								"accumulates. As it climbs it reduces the effectiveness of the free "
								"chlorine you are measuring, so the test can show a chlorine reading while "
								"the water behaves as though there is very little working. If sanitiser "
								"reads present and the water still looks wrong, test the stabiliser.</p>"
							),
						},
						{
							"title": "Organic load",
							"body": (
								"<p>Leaves, pollen, birds, algae and everything else the basin collects "
								"consume sanitiser continuously. A sanitiser that will not hold is very "
								"often a debris problem in a chemistry costume, which is why the debris "
								"lesson sits immediately before this one.</p>"
							),
						},
					],
				},
				ask_block(
					"Targets, products and who may handle them",
					"<p>Every target range, every dose rate and every product choice here is set by "
					"the specification for the feature, the product label, and the local health or "
					"aquatic code where one applies. This course prints none of them, because a dose "
					"remembered from another site is the fastest way to do real damage.</p>"
					"<p>Two rules do not vary. <b>Never mix chemicals</b>, in a container, in a "
					"feeder, or by adding one to a basin immediately after another — some "
					"combinations react violently. And read the safety data sheet for what is "
					"actually in your hand. Which products a technician may carry, transport and "
					"apply, and what protective equipment is required, is Sapphire's call and Module "
					"9 — Jobsite Safety covers the handling.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "The pH on a tall spray feature is corrected on every visit and has climbed again by the next one. What is the most likely explanation?",
						"type": "Single Choice",
						"explanation": (
							"Throwing water into the air strips carbon dioxide out of it, and losing CO2 raises pH. "
							"A spray feature does that continuously by design, so the climb is a condition to manage "
							"rather than a failed correction."
						),
						"options": [
							{
								"text": "Aeration is stripping carbon dioxide from the water, which raises pH",
								"is_correct": True,
							},
							{
								"text": "The acid being used has degraded and is no longer effective",
								"is_correct": False,
							},
							{
								"text": "The feature is leaking and being diluted by make-up water",
								"is_correct": False,
							},
							{
								"text": "The pH test is reading high because the sample is taken near a return",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The pH swings a long way on small additions and will not settle. What should be corrected first?",
						"type": "Single Choice",
						"explanation": (
							"Total alkalinity is the buffer — the water's resistance to pH change. With it too low, "
							"pH bounces on any addition, so correcting pH first is work that undoes itself."
						),
						"options": [
							{
								"text": "Total alkalinity, because it is the buffer that holds pH steady",
								"is_correct": True,
							},
							{
								"text": "Calcium hardness, because it stabilises the whole balance",
								"is_correct": False,
							},
							{
								"text": "Sanitiser level, since low sanitiser lets pH drift",
								"is_correct": False,
							},
							{
								"text": "Cyanuric acid, which controls how pH responds to dosing",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You have dosed the same basin three times against the same stubborn reading and nothing has moved. What is the right next step?",
						"type": "Single Choice",
						"explanation": (
							"A reading that will not respond is often a reading that was never right. Re-test with "
							"fresh reagent from a proper sample before dosing again — dosing against a bad test "
							"moves real chemistry to correct a number that does not exist."
						),
						"options": [
							{
								"text": "Verify the test itself — fresh reagent, a proper sample, and a second method if you have one",
								"is_correct": True,
							},
							{
								"text": "Double the dose, since the previous amounts were clearly too small",
								"is_correct": False,
							},
							{"text": "Switch to a different product that acts faster", "is_correct": False},
							{
								"text": "Drain and refill the basin to start from a clean sheet",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Seized, galled and cross-threaded pipe fittings",
			"chapter": 3,
			"estimated_minutes": 12,
			"summary": "Thread faults are prevented on the first turn, not cured with a bigger wrench.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A thread is a fit, and you feel it on the first turn",
					"content": (
						"<p>Two threads either line up or they do not, and the moment that is decided "
						"is the first half turn. A correctly started thread turns <b>easily, by "
						"hand</b>, for several turns before anything gets tight. If it needs force "
						"straight away, it is not tight — it is <b>cross-threaded</b>, cutting a new "
						"path across the existing one, and every further turn does more damage.</p>"
						"<p>So start every thread by hand. The reliable way is to set the fitting "
						"square and turn it <b>backwards</b> until you feel it drop into the start of "
						"the thread, then turn forward. That drop is unmistakable once you have felt "
						"it, and it costs two seconds.</p>"
						"<p>The reason this matters more than it sounds is what a cross-threaded joint "
						"does afterwards. Forced up hard, it often seals — for a while. It passes the "
						"test, it looks finished, and it lets go later, which puts it in the same "
						"family as the solvent-weld joint in Module 1 — Piping &amp; Hydraulics that "
						"fails under a slab.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Galling is cold welding, and it is one-way",
					"content": (
						"<p>Galling is not corrosion and it is not dirt. Under pressure and friction, "
						"two metal surfaces in sliding contact can <b>weld to each other in the solid "
						"state</b> — material transfers from one face to the other, the threads seize, "
						"and the fastener is destroyed. It commonly happens in the middle of "
						"assembly, with nothing to be done about it in either direction.</p>"
						"<p>Stainless steel on stainless steel is the classic case, because the oxide "
						"film that makes it corrosion-resistant is exactly what gets scrubbed off and "
						"lets the metal underneath contact bare metal. Aluminium and some other alloys "
						"do it too.</p>"
						"<p>Everything that prevents it is about keeping the surfaces apart and the "
						"heat down: clean threads with no grit in them, a lubricant or anti-seize "
						"suited to the material and the service, <b>slow assembly by hand</b>, and no "
						"impact driver. Speed generates the heat that starts it, which is why a "
						"power tool turns a thirty-second job into a replaced component.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Force turns a cheap fitting into an expensive one",
					"content": (
						"<p>When a fitting will not move, the instinct is more leverage. It is almost "
						"always the wrong answer, because the fitting is rarely the most fragile thing "
						"in the assembly. <b>The cheater bar does not break the nipple — it breaks the "
						"pump housing, the valve body, the filter port or the female fitting cast into "
						"something you cannot replace on site.</b></p>"
						"<p>What works instead: penetrating oil and time; the correct size of wrench, "
						"fully engaged; <b>a second wrench backing up the fitting</b> so the load never "
						"reaches the equipment or twists the pipe; and heat only where the material "
						"and the location allow it, which on plastic pipe and near solvent cement it "
						"does not.</p>"
						"<p>And know when to stop. Cutting a seized fitting out deliberately is a "
						"controlled repair. Snapping a port off a pump because it nearly moved is an "
						"unplanned one, and it happens at the end of the day when everybody is tired "
						"of it.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Thread terms worth having straight",
					"cards": [
						{
							"front": "Cross-threaded",
							"back": "The threads started out of alignment and are cutting a new path across the original. Felt as resistance on the first turn, when there should be none. The cure is to back it out and start again, never to push through.",
						},
						{
							"front": "Galling",
							"back": "Two thread surfaces cold-welding to each other under friction. Most common with stainless on stainless, made worse by speed, heat, dirt and dry threads. It is not reversible — the fastener is finished.",
						},
						{
							"front": "Tapered (NPT) thread",
							"back": "Seals on the threads themselves as they wedge together, so it needs a sealant and it has a limit to how far it can be made up. Over-tightening a tapered male thread into a plastic female fitting splits it, because the taper is a wedge.",
						},
						{
							"front": "Straight thread",
							"back": "Seals on a gasket or an O-ring face, not on the threads. If it weeps, more torque will never fix it — the sealing face or the O-ring is the problem, and more force damages both.",
						},
						{
							"front": "Anti-seize",
							"back": "A compound that keeps thread faces from contacting bare metal so they cannot cold-weld. Which compound is a materials, temperature and potable-water question, not a preference — and some of them are also lubricants, which changes how tight a given torque actually is.",
						},
					],
				},
				ask_block(
					"How tight, and with what on the threads",
					"<p>Torque figures, sealant type, tape versus dope, how many turns past hand tight "
					"a plastic fitting takes, and whether a fitting may be reused are set by the "
					"manufacturer of the part in your hand. They differ between metals and plastics, "
					"between tapered and straight threads, and between potable and non-potable "
					"service — and some plastics are chemically attacked by the wrong compound.</p>"
					"<p>Read the instructions for the component. Where none are available, that is a "
					"question for the supervisor rather than a guess, because the failure mode here is "
					"a split female fitting in something that was not designed to be "
					"replaceable.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A threaded fitting needs noticeable force from the very first turn. What should you do?",
						"type": "Single Choice",
						"explanation": (
							"A correctly started thread turns freely by hand for several turns. Resistance at the "
							"start means it is cross-threading, and every further turn cuts more damage into both "
							"parts."
						),
						"options": [
							{
								"text": "Back it out completely and restart it by hand — it is cross-threading",
								"is_correct": True,
							},
							{
								"text": "Keep going; the threads will clean themselves up as it makes up",
								"is_correct": False,
							},
							{"text": "Add more sealant to take up the roughness", "is_correct": False},
							{
								"text": "Switch to a power tool so it runs past the tight spot quickly",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What is galling?",
						"type": "Single Choice",
						"explanation": (
							"Galling is solid-state cold welding: under friction the two thread faces transfer "
							"material and seize to each other. Stainless on stainless is the classic case, and it is "
							"not reversible."
						),
						"options": [
							{
								"text": "Two thread surfaces cold-welding to each other under friction",
								"is_correct": True,
							},
							{"text": "Corrosion between two dissimilar metals in water", "is_correct": False},
							{
								"text": "Threads stripping because the fitting was over-torqued",
								"is_correct": False,
							},
							{
								"text": "Sealant hardening in the threads so the joint cannot be undone",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A straight-thread fitting with an O-ring is weeping. Tightening it harder is the fix.",
						"type": "True-False",
						"explanation": (
							"A straight thread seals on its gasket or O-ring face, not on the threads. More torque "
							"cannot improve a seal it is not making, and it damages the face and the O-ring."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Pump motor performance drop or cut-out",
			"chapter": 3,
			"estimated_minutes": 15,
			"summary": "An ordered walk through prime, suction, impeller, air, voltage, overload and heat — and what a motor that restarts when cool is telling you.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A pump has only a few ways to lose performance",
					"content": (
						"<p>A pump that used to make its numbers and no longer does has a short list of "
						"explanations, and they are worth working in order — cheapest and most likely "
						"first, so that the expensive conclusion is reached by elimination rather than "
						"by guess.</p>"
						"<p><b>Prime and water level.</b> A pump that is not full of water is not "
						"pumping, and a level that has dropped to the suction lets air in "
						"continuously.</p>"
						"<p><b>Suction restriction.</b> Baskets, strainer pot, intake screen, suction "
						"valve, a collapsed flexible line. This is the most common performance "
						"complaint there is, and the vacuum gauge in the no-flow lesson names it in "
						"one reading.</p>"
						"<p><b>The impeller.</b> Debris wound into it — string, plastic, fibrous "
						"material — drops output immediately. Wear is slower: a worn impeller still "
						"spins, still sounds right, and simply does less. Nothing about the pump looks "
						"wrong.</p>"
						"<p><b>Air.</b> A suction-side leak lets air in without letting water out, so "
						"there is nothing to see. The leaks lesson above covers finding it.</p>"
						"<p><b>Electrical.</b> Low or unbalanced supply voltage, a failing start "
						"capacitor on a single-phase motor, or reversed rotation on a three-phase motor "
						"that has been reconnected. A motor running backwards still moves some water "
						"and still sounds like a pump.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A motor that restarts once it is cool has told you what is wrong",
					"content": (
						"<p>This is the single most informative symptom on the list, and it is "
						"routinely thrown away by resetting it.</p>"
						"<p>A pump motor is protected against overheating — by a thermal device inside "
						"the motor, by an overload in the starter, or by both, and some reset "
						"themselves once cool while others have to be reset by hand. If a motor runs, "
						"cuts out, and then comes back after it has cooled down, <b>it "
						"overheated</b>. That is not a mystery fault and it is not a faulty switch — it "
						"is a protective device doing its job and reporting a condition.</p>"
						"<p>So the question becomes why it is overheating. Airflow blocked by leaves, "
						"debris or an enclosure with no ventilation. A high ambient, or full sun on a "
						"pump that was specified for shade. Low or unbalanced supply voltage — a motor "
						"draws <i>more</i> current at low voltage to deliver the same power, and the "
						"extra current is heat. A mechanical load it was not chosen for. A failing "
						"bearing. A failing capacitor.</p>"
						"<p>Resetting it repeatedly cooks the windings. When they fail, the pump is "
						"replaced and the original cause is still there waiting for the new one.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cavitation sounds like gravel, and it eats the impeller",
					"content": (
						"<p>If a pump sounds as though it is pumping gravel, that is almost certainly "
						"cavitation, and it is a suction-side fault rather than a pump fault.</p>"
						"<p>The mechanism is worth knowing, because it explains both the sound and the "
						"damage. A restriction on the suction side drops the pressure at the eye of the "
						"impeller. Drop it far enough and the water reaches its vapour pressure and "
						"<b>boils at ambient temperature</b>, forming vapour bubbles. Those bubbles are "
						"carried into the higher-pressure region of the impeller, where they collapse "
						"violently against the metal. The noise is thousands of tiny collapses a "
						"second, and so is the damage — it pits and erodes the impeller from the "
						"surface inward.</p>"
						"<p>Which means it is not a noise to tolerate while you finish something else. "
						"The pump is destroying itself, and the fault is upstream: a blocked basket, a "
						"closed valve, a restricted line, a low water level, or a suction line that "
						"was never right. Module 2 — Aquatic System Equipment Installation covers why "
						"the suction side decides whether a pump works at all.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Work it in this order",
					"items": [
						"Is the pump primed, and is the water level above the suction",
						"Suction side clear: skimmer baskets, strainer pot, intake screen, suction valve",
						"Air: bubbles at the returns, air in the pot, a spray that surges",
						"Impeller: debris wound into it, and wear at the vanes",
						"Rotation against the arrow on the housing, if anything electrical has been touched",
						"Supply voltage measured with the motor running, not at rest",
						"Current draw compared with the figure on the motor's own data plate",
						"Ventilation around the motor, and what the ambient temperature actually is",
						"Downstream restriction, using the discharge pressure gauge",
						"Noise and vibration: gravel means cavitation, a rumble or squeal means bearings",
					],
				},
				ask_block(
					"The data plate and the pump curve are the authority",
					"<p>The motor's own data plate carries its voltage, its full-load current and its "
					"service factor, and the pump has a curve and a duty point recorded in the "
					"submittal. Those are the numbers a measurement is compared against. This course "
					"cannot print them, and a figure remembered from a different pump proves "
					"nothing.</p>"
					"<p>The other limit is scope. Measuring is one thing; opening a motor, working on "
					"a starter, or anything beyond proving dead and taking a reading belongs to a "
					"qualified person under Sapphire's energy-control procedure. Module 6 — Electrical "
					"Components and Module 9 — Jobsite Safety cover the standard, and a hot motor is "
					"also a burn hazard before it is a diagnostic puzzle.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A pump motor runs for a while, cuts out, and will restart once it has cooled down. What is it telling you?",
						"type": "Single Choice",
						"explanation": (
							"That is thermal overload protection operating. The motor is overheating, and the "
							"protective device is reporting it — so the question is why: airflow, ambient, voltage, "
							"load, bearings or capacitor."
						),
						"options": [
							{
								"text": "It is overheating, and the thermal overload is protecting it",
								"is_correct": True,
							},
							{
								"text": "The overload switch is faulty and should be replaced",
								"is_correct": False,
							},
							{"text": "It has lost prime and re-primes as it cools", "is_correct": False},
							{"text": "The impeller is worn and binding intermittently", "is_correct": False},
						],
					},
					{
						"question": "A pump sounds as though it is pumping gravel. What is happening, and where is the fault?",
						"type": "Single Choice",
						"explanation": (
							"That is cavitation: a suction-side restriction drops the pressure at the impeller eye "
							"until the water vaporises, and the bubbles collapse against the impeller. The cause is "
							"upstream, and the damage is to the pump."
						),
						"options": [
							{
								"text": "Cavitation from a suction-side restriction, and it is eroding the impeller",
								"is_correct": True,
							},
							{
								"text": "Debris passing through the impeller, which will clear itself",
								"is_correct": False,
							},
							{
								"text": "A downstream restriction forcing water back through the pump",
								"is_correct": False,
							},
							{"text": "Worn motor bearings, which need replacing", "is_correct": False},
						],
					},
					{
						"question": "Why is supply voltage to a struggling motor measured with the motor running rather than at rest?",
						"type": "Single Choice",
						"explanation": (
							"A loose or corroded connection carries no current at rest and reads perfectly normal. "
							"Under load it drops voltage across itself — and a motor at low voltage draws more "
							"current, which is heat."
						),
						"options": [
							{
								"text": "A loose or corroded connection reads normal at rest and collapses under load",
								"is_correct": True,
							},
							{
								"text": "Supply voltage is always higher when equipment is running",
								"is_correct": False,
							},
							{
								"text": "The meter cannot read accurately on an idle circuit",
								"is_correct": False,
							},
							{
								"text": "It is only a convenience; either reading answers the question",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
