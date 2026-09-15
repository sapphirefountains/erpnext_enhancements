# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 2 — Aquatic System Equipment Installation.

Built from Sapphire's own document, "Module 2: Equipment Installation, Flow Control, &
Filtration". Where that document states a figure it is used as written, in place of whatever
this file said before: the five-diameter straight suction run, the fifteen seconds a dry
mechanical seal survives, the 8-to-10 PSI rise that condemns a filter, the 30-second rinse, the
45-degree wand, PSI x 2.31 and inHg x 1.13, the 1/8-to-3/16-inch paver gap, and the three tank
levels the Splash Wizard controller works between.

The document covers pump installation and priming, balance and surge tanks, skimmer and
main-drain balancing, filtration, pedestal false floors and pump curves. Pipe and fittings,
chemical feed, heaters and chillers, structures and anchoring are **not** in it; those lessons
keep the general-practice content they had, and the ask_block()s they already carried, which
say where the real number lives rather than printing one. ("Fountain structures" never had one
and still does not.) The single thing added to that set is a balance-tank entry in the vessel
list of "Fountain structures", pointing at the lesson that carries the document's own account
of it.

One place the document contradicted this file outright: the void under a pedestal deck is a
drainage plenum, not the reservoir. Water drains through the open joints to a sloped sub-slab
and back to a separate holding tank, and it is that tank's level the controller watches.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 2 — Aquatic System Equipment Installation",
		"summary": (
			"Set and pipe the equipment a water feature runs on — pumps, balance tanks, filters, "
			"drains, gauges, chemical feed, heaters — and the structures and anchors that hold it "
			"all up."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{"title": "Moving the water", "description": "Pumps, pipe, and where water enters and leaves."},
		{"title": "Conditioning the water", "description": "Filters, gauges, chemical feed, temperature."},
		{"title": "Holding it all up", "description": "Structures, anchors and deck systems."},
	],
	"lessons": [
		{
			"lesson_title": "Pump installation and priming",
			"chapter": 0,
			"estimated_minutes": 16,
			"summary": "Mounting it, the five-diameter straight run, and the fifteen seconds a dry seal survives.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "A pump does not suck",
					"content": (
						"<p>A centrifugal pump throws water outward with an impeller. That creates "
						"a low-pressure region at the eye of the impeller, and then "
						"<b>atmospheric pressure pushes water up the suction line into it</b>. The "
						"pump is not pulling; the air outside is pushing.</p>"
						"<p>Which is why the arrangement of the suction side decides whether the "
						"pump works at all:</p>"
						"<ul>"
						"<li><b>Flooded suction</b> — the equipment room sits lower than the "
						"reservoir water level, so gravity keeps the pump wet. This is the easy case "
						"and the one most fountains are designed for.</li>"
						"<li><b>Suction lift</b> — the pump sits above the water level, so it has to "
						"actively pull the air out of the suction line before any water will follow "
						"it. There is a hard limit to how high that can work, and it gets worse with "
						"altitude, with warm water, and with every foot of pipe and every fitting on "
						"the suction side.</li>"
						"</ul>"
						"<p>A pump that will not hold prime is nearly always a suction-side "
						"problem, not a pump problem.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Fifteen seconds is all a dry seal gets",
					"content": (
						"<p>The mechanical seal between the wet end and the motor shaft is "
						"<b>lubricated and cooled by the water going past it</b>. Run the pump "
						"dry and there is nothing doing either job.</p>"
						"<p>Sapphire puts a number on how long that lasts: running a centrifugal "
						"pump without water <b>destroys the mechanical shaft seal within 15 "
						"seconds</b>, on friction heat alone. That is less time than it takes to "
						"notice the noise is wrong and walk back to the panel.</p>"
						"<p>So a pump is never started to 'see if it primes' on a system you have "
						"not filled and purged. And a pump that has just run dry does not get "
						"cold water thrown at a hot wet end.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A suction leak leaves no puddle",
					"content": (
						"<p>This is the detail that sends people hunting in the wrong place for "
						"an afternoon.</p>"
						"<p>Everything <b>downstream</b> of the impeller is above atmospheric "
						"pressure, so a leak there pushes water out and you can see it. "
						"Upstream of the impeller the line is usually below atmospheric while "
						"the pump runs, so a leak there pulls <i>air in</i> rather than pushing "
						"water out — air in the pot, a pump that loses prime, and bubbles at the "
						"returns, and nothing on the floor. On a flooded-suction system that same "
						"joint is under static pressure once the pump stops, so shut it down and "
						"look again: a weep that appears only with the pump off is the "
						"confirmation.</p>"
						"<p>Usual suspects: the pump lid O-ring, the drain plugs, a threaded "
						"fitting on the suction side, the union, and the shaft seal itself.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The pad, the pipe, and five diameters of straight run",
					"content": (
						"<p><b>The pad.</b> The pump chassis bolts down to a <b>level, reinforced "
						"concrete equipment pad</b> with high-grade anchor bolts. <b>Vibration "
						"isolation pads</b> go under the pump feet, to keep the machine's resonance "
						"out of the mechanical room — and vibration is also the slow way to a loose "
						"fitting and a leak.</p>"
						"<p><b>The pipe carries itself.</b> Suction and discharge plumbing are "
						"supported independently, on pipe hangers or struts. <b>Never let the pump "
						"housing bear the weight of the plumbing.</b> It distorts the casing, and a "
						"distorted casing means premature seal failure — the same seal as the "
						"callout above, killed a completely different way.</p>"
						"<p><b>Five pipe diameters of straight run into the inlet.</b> Water has to "
						"reach the eye of the impeller in an even, laminar flow, so the suction "
						"piping needs a straight run immediately before the pump inlet of <b>at "
						"least five times the pipe diameter</b>. A 3-inch suction line therefore "
						"needs 15 inches of straight pipe before the pump. An elbow hard against "
						"the suction port feeds the impeller unevenly, and that pump never makes "
						"its numbers.</p>"
						"<p><b>Unions both sides.</b> This pump will come out one day, and whether "
						"that is a ten-minute job or a saw is decided now.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Commissioning, in Sapphire's order",
					"content": (
						"<p>The order is not arbitrary. Each step removes one of the ways the next "
						"step could destroy the pump.</p>"
						"<ol>"
						"<li><b>Never run a centrifugal pump dry.</b> Everything below exists so "
						"that it does not happen.</li>"
						"<li>Open the <b>hair-and-lint strainer basket</b> lid.</li>"
						"<li>Fill the housing <b>completely, to the top lip</b>, with clean "
						"water.</li>"
						"<li>Inspect the lid O-ring for debris and cracks, lubricate it with a "
						"<b>silicone-based sealant</b>, and <b>hand-tighten</b> the lid. Not a "
						"wrench.</li>"
						"<li>Open all <b>suction and discharge valves fully</b>.</li>"
						"<li><b>Jog the motor</b> momentarily and confirm the shaft turns the way "
						"the arrow on the motor casing says it should.</li>"
						"<li>Start the pump and watch the pressure gauges. If it has not caught "
						"prime in two to three minutes, <b>shut it down immediately</b>, re-verify "
						"the water levels, and check for suction-side air leaks.</li>"
						"</ol>"
						"<p>Note what the last step is not. It is not 'give it another ten "
						"minutes'. A pump that is not catching prime is a pump turning with very "
						"little water in it, and the fifteen-second figure above is why that clock "
						"is short.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Three-phase motors will happily run backwards",
					"content": (
						"<p>Swap any two phases on a three-phase motor and it runs the other way. "
						"A centrifugal pump running backwards still moves <i>some</i> water and "
						"still sounds like a pump, so nothing looks obviously wrong — it just "
						"never makes its numbers, and everybody spends the day chasing a "
						"hydraulic problem that does not exist.</p>"
						"<p>Check rotation against the arrow on the motor casing at first start — "
						"that is what the jog step in the sequence above is for — and check it "
						"again after any electrical work upstream.</p>"
					),
				},
				ask_block(
					"The pump was selected for a duty point",
					"<p>Which pump, at which speed, against how much head, is an engineering "
					"decision recorded in the submittal and the equipment schedule — not something "
					"to substitute on site because what arrived is close enough.</p>"
					"<p>If the pump in the crate is not the pump on the schedule, that is a change "
					"for the project manager to accept, not a swap to make quietly.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What actually moves water into a centrifugal pump?",
						"type": "Single Choice",
						"explanation": (
							"The impeller creates a low-pressure region and atmospheric pressure pushes water in. "
							"That is why suction lift has a hard limit and flooded suction is easy."
						),
						"options": [
							{
								"text": "Atmospheric pressure pushing it in, once the impeller lowers the pressure",
								"is_correct": True,
							},
							{
								"text": "The impeller physically pulling water up the suction line",
								"is_correct": False,
							},
							{"text": "The motor's vacuum pump", "is_correct": False},
							{"text": "Water hammer from the return line", "is_correct": False},
						],
					},
					{
						"question": "A 3-inch suction line runs into the pump. How much straight pipe does Sapphire require immediately before the inlet?",
						"type": "Single Choice",
						"explanation": (
							"At least five times the pipe diameter — five times 3 inches is 15 inches. The straight "
							"run is what delivers an even, laminar flow into the eye of the impeller."
						),
						"options": [
							{
								"text": "At least 15 inches — five times the pipe diameter",
								"is_correct": True,
							},
							{"text": "At least 3 inches — one pipe diameter", "is_correct": False},
							{"text": "At least 30 inches — ten times the pipe diameter", "is_correct": False},
							{
								"text": "None is needed, provided the last fitting is a long-sweep 90",
								"is_correct": False,
							},
						],
					},
					{
						"question": "How long does a centrifugal pump's mechanical shaft seal survive being run dry?",
						"type": "Single Choice",
						"explanation": (
							"Sapphire's figure is 15 seconds. The seal is lubricated and cooled by the water going "
							"past it, and friction heat destroys the faces almost immediately without it."
						),
						"options": [
							{
								"text": "About 15 seconds — friction heat destroys it almost immediately",
								"is_correct": True,
							},
							{
								"text": "Several minutes, which is long enough to walk back to the panel",
								"is_correct": False,
							},
							{
								"text": "As long as the motor itself stays cool to the touch",
								"is_correct": False,
							},
							{
								"text": "Indefinitely — the seal is stressed by pressure, not by running dry",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The pot is full, the valves are open, and three minutes after start-up the pump still has not caught prime. There is no water anywhere on the floor. What does the protocol say?",
						"type": "Single Choice",
						"explanation": (
							"Shut it down immediately, re-verify the water levels, and check for suction-side air "
							"leaks. The suction side runs below atmospheric pressure, so a leak there draws air in "
							"rather than pushing water out — a dry floor is the signature, not the all-clear."
						),
						"options": [
							{
								"text": "Shut it down immediately, re-check the water levels, and hunt for a suction-side air leak",
								"is_correct": True,
							},
							{
								"text": "Give it another ten minutes — some pumps are simply slow to catch",
								"is_correct": False,
							},
							{
								"text": "Nothing is wrong: with no water on the floor, there is no leak to find",
								"is_correct": False,
							},
							{
								"text": "Throttle the suction valve to raise the vacuum so it pulls harder",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Piping and fitting types",
			"chapter": 0,
			"estimated_minutes": 12,
			"summary": "Schedules, pressure ratings, and putting unions where the next person needs them.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Schedule 40 and Schedule 80 share an outside diameter",
					"content": (
						"<p>This trips people up constantly. Schedule 40 and Schedule 80 PVC of the "
						"same nominal size have the <b>same outside diameter</b> — which is why "
						"the fittings interchange. What changes is the wall thickness: Schedule 80 "
						"has a thicker wall, so it has a <b>higher pressure rating</b> and a "
						"<b>smaller inside diameter</b>.</p>"
						"<p>Smaller inside diameter means more velocity for the same flow, and "
						"more friction loss. Swapping schedules is therefore a hydraulic change as "
						"well as a strength change, and both directions have a cost.</p>"
						"<p>Pressure ratings also <b>fall as the temperature rises</b>. A pipe "
						"rated at room temperature is rated lower on a hot equipment room wall or "
						"downstream of a heater.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "DWV is not pressure pipe",
					"content": (
						"<p>Drain, waste and vent fittings look like pressure fittings, cost less, "
						"and are <b>not rated for pressure at all</b>. They are made for gravity "
						"flow. A DWV fitting in a pressurised line is a burst waiting for a "
						"start-up.</p>"
						"<p>The markings on the pipe and the fitting say what they are. Read them "
						"rather than matching by eye — this is a mistake that is invisible once "
						"the cement is on.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Threads, and the plastic ones in particular",
					"content": (
						"<p>Tapered pipe threads seal by wedging, which means a plastic female "
						"fitting is a <b>split waiting to happen</b> if you keep turning. The "
						"usual guidance is hand tight plus a small amount with a wrench, and the "
						"failure mode is not immediate — the fitting crazes and opens up weeks "
						"later.</p>"
						"<p>Use the sealant the fitting maker calls for. Some pipe dopes attack "
						"plastic. Tape goes on in the direction the thread turns so it does not "
						"unwind as you assemble, and it goes on the <b>male</b> thread.</p>"
						"<p>Where a plastic pipe meets a metal one, the metal male into the "
						"plastic female is the combination that splits. Prefer plastic male into "
						"metal female, or use a transition designed for it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Build it so somebody can take it apart",
					"content": (
						"<p>Every pump, filter, heater, valve and controller in this system will "
						"be serviced or replaced. The difference between a 20-minute swap and a "
						"half-day of cutting and re-piping is decided by you, at installation, "
						"for the price of a union.</p>"
						"<ul>"
						"<li><b>Unions or flanges on both sides</b> of anything that comes out.</li>"
						"<li><b>Isolation valves</b> so one item can be pulled without draining "
						"the system.</li>"
						"<li><b>Room to swing a wrench</b> and to actually lift the thing out.</li>"
						"<li><b>Two 45s instead of a hard 90</b> where the space allows — less "
						"friction loss, and a gentler path.</li>"
						"</ul>"
					),
				},
				ask_block(
					"Flexible PVC, and what the specification allows",
					"<p>Flexible PVC has real uses and real limits — lower pressure ratings, "
					"different cement, and restrictions on where it may be buried or concealed "
					"that vary by jurisdiction.</p>"
					"<p>Whether it is acceptable on a given run is answered by the project "
					"specification and the local code, not by what makes the day easier.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "How do Schedule 40 and Schedule 80 PVC of the same nominal size compare?",
						"type": "Single Choice",
						"explanation": (
							"Same outside diameter — that is why the fittings interchange. Schedule 80's thicker "
							"wall means a higher pressure rating and a smaller inside diameter."
						),
						"options": [
							{
								"text": "Same outside diameter; Schedule 80 has a thicker wall, higher rating and smaller bore",
								"is_correct": True,
							},
							{
								"text": "Same inside diameter; Schedule 80 is larger on the outside",
								"is_correct": False,
							},
							{
								"text": "Schedule 80 is the same pipe in a different colour",
								"is_correct": False,
							},
							{"text": "Schedule 40 has the higher pressure rating", "is_correct": False},
						],
					},
					{
						"question": "Why can a DWV fitting not be used in a pressurised return line?",
						"type": "Single Choice",
						"explanation": (
							"DWV is made for gravity flow and carries no pressure rating. It looks like a pressure "
							"fitting, which is exactly what makes it dangerous."
						),
						"options": [
							{
								"text": "It carries no pressure rating — it is made for gravity drainage",
								"is_correct": True,
							},
							{
								"text": "It is a different outside diameter and will not fit",
								"is_correct": False,
							},
							{"text": "It cannot be solvent welded", "is_correct": False},
							{
								"text": "It can, as long as the pressure stays under 20 psi",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which combination is most likely to split a threaded joint over time?",
						"type": "Single Choice",
						"explanation": (
							"A tapered thread wedges. A metal male thread driven into a plastic female fitting "
							"keeps wedging until the plastic crazes — often weeks later."
						),
						"options": [
							{
								"text": "A metal male thread into a plastic female fitting, over-tightened",
								"is_correct": True,
							},
							{
								"text": "A plastic male thread into a metal female fitting",
								"is_correct": False,
							},
							{"text": "Two metal threads with tape", "is_correct": False},
							{"text": "Two plastic threads assembled hand tight", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Skimmers and main drains",
			"chapter": 0,
			"estimated_minutes": 14,
			"summary": "Where water leaves the basin, how the manifold is balanced, and the hazard that has killed people.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The skimmer works the surface, on purpose",
					"content": (
						"<p>Leaves, pollen, dust, sunscreen, oils and the film that makes a "
						"fountain look dull all float. A skimmer draws from the <b>top few "
						"inches</b> of water and takes that layer away before it sinks and "
						"becomes a bottom problem.</p>"
						"<p>The hinged flap at the mouth is the <b>weir door</b>, and it floats — it "
						"adjusts itself to a fluctuating water level, and it accelerates the top "
						"layer of water over its lip. That is what creates the <b>localised "
						"surface-tension draw</b> that pulls floating leaves, oils and debris off "
						"the water and into the skimmer's <b>internal collector basket</b>, before "
						"any of it can sink to the floor. A weir door that is jammed, missing or "
						"installed backwards turns a skimmer into an ordinary suction port.</p>"
						"<p>The basket catches what comes in. Full basket, no flow — and on many "
						"systems that means the pump is starved, not just that the skimmer is "
						"lazy.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Water level is the skimmer's operating range",
					"content": (
						"<p>The weir door follows the level by itself, but only across the range it "
						"was set for. Outside that range it stops working, in two different "
						"ways.</p>"
						"<p>Too <b>low</b> and the skimmer gulps air past the weir. That air goes "
						"straight to the pump, and the pump loses prime — which, as this module's "
						"first lesson explains, is how a mechanical seal dies in fifteen "
						"seconds.</p>"
						"<p>Too <b>high</b> and the weir stops working: the surface layer no longer "
						"accelerates over it, so debris drifts past instead of being caught.</p>"
						"<p>On a fountain, evaporation and wind-blow move that level every day. "
						"Auto-fill exists for exactly this reason, and an auto-fill that has "
						"failed is a common root cause behind 'the pump keeps losing prime'.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The balancing act",
					"content": (
						"<p>Most fountain basins draw from <b>both</b> — surface skimmers and "
						"submerged floor main drains — through one suction manifold. How much of "
						"the pump's draw each of them gets is set <b>by hand</b>, on the eccentric "
						"ball or butterfly valves on that manifold. It is a real adjustment with a "
						"right answer, not a set of valves to leave wide open.</p>"
						"<p>Both ways of getting it wrong announce themselves:</p>"
						"<ul>"
						"<li><b>Too much skimmer draw</b> — the skimmers <b>vortex</b>. A vortex "
						"pulls air down into the plumbing lines, and air on the suction side costs "
						"the pump its prime.</li>"
						"<li><b>Too much main drain draw</b> — the surface goes <b>stagnant</b>. "
						"Nothing is accelerating over the weirs, so a film of debris collects and "
						"sits there across the water. This is the one the client sees first.</li>"
						"</ul>"
						"<p>So the balance point sits between a vortexing skimmer and a dirty-looking "
						"surface, and you find it by watching the water rather than by counting "
						"turns on a valve.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Suction entrapment: the one that kills",
					"content": (
						"<p>A single submerged suction outlet with a broken, missing or "
						"non-compliant cover can hold a person, a limb, hair or clothing against "
						"it with a force nobody can pull against. It has drowned people, including "
						"children, and it has caused disembowelment injuries.</p>"
						"<p>The engineering answers are all about <b>never letting one outlet take "
						"the whole flow</b>: certified anti-entrapment covers, multiple suction "
						"outlets far enough apart that one cannot be fully blocked, unblockable "
						"designs, and safety vacuum release systems. Covers are certified, they "
						"are rated for a flow, and they carry a <b>life span and a date</b> — they "
						"are a wear part, not permanent hardware.</p>"
						"<p>Which is why balancing the manifold is a safety job and not only a "
						"housekeeping one. Sapphire's rule is to adjust the valves so that "
						"<b>main drain suction stays distributed across multiple grates</b>, to "
						"anti-entanglement standard. Throttling a compliant multi-outlet design "
						"until one grate is taking effectively all of the flow turns it back into a "
						"single-point suction hazard, with every cover still in place and "
						"everything still looking correct.</p>"
						"<p><b>A system with a damaged or missing suction cover does not run.</b> "
						"Not for a minute, not to finish a test, not while somebody goes for the "
						"part. Shut it down and lock it out.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What a main drain is for",
					"content": (
						"<p>Beyond circulation, a bottom outlet gives you the ability to draw from "
						"the coldest, dirtiest layer, to turn the whole volume over rather than "
						"just the top, and to empty the basin for service.</p>"
						"<p>On a fountain it is frequently also the only route that can actually "
						"drain the vessel, which links straight back to the pitch lesson in Module "
						"1 — a basin that does not fall to its outlet is a basin somebody pumps "
						"out by hand every winter.</p>"
					),
				},
				ask_block(
					"Which anti-entrapment standard applies here",
					"<p>Which rules bind a given feature — the federal pool and spa safety law, the "
					"ISPSC, a state or local health code, or the project specification — depends on "
					"what the feature is classified as and where it is.</p>"
					"<p>That classification is made by the designer and the authority having "
					"jurisdiction. If you cannot tell whether an outlet is compliant, the question "
					"goes up, and the feature stays off until it is answered.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does a skimmer draw from the surface rather than from deeper water?",
						"type": "Single Choice",
						"explanation": (
							"Leaves, pollen, oils and surface film all float. Taking that layer away before it "
							"sinks stops it becoming a bottom problem."
						),
						"options": [
							{
								"text": "Because the debris, oils and film that foul a feature float on top",
								"is_correct": True,
							},
							{
								"text": "Because surface water is warmer and easier to pump",
								"is_correct": False,
							},
							{"text": "Because deeper water is already filtered", "is_correct": False},
							{"text": "To keep the main drain from being overloaded", "is_correct": False},
						],
					},
					{
						"question": "A film of debris is sitting across the fountain surface and the skimmer weirs are barely moving any water. What does that say about the suction manifold?",
						"type": "Single Choice",
						"explanation": (
							"Too much of the draw is on the main drain, so the surface goes stagnant. The opposite "
							"error — too much skimmer draw — makes the skimmers vortex and costs the pump its "
							"prime. The balance is set by hand on the manifold valves."
						),
						"options": [
							{
								"text": "Too much of the draw is on the main drain — open the skimmers up at the manifold",
								"is_correct": True,
							},
							{
								"text": "Too much of the draw is on the skimmers, which is why they are vortexing",
								"is_correct": False,
							},
							{
								"text": "The manifold balance cannot affect the surface; the filter is dirty",
								"is_correct": False,
							},
							{
								"text": "Nothing — surface film is normal and clears itself once the pump warms up",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You find a cracked suction outlet cover on a running feature. What do you do?",
						"type": "Single Choice",
						"explanation": (
							"Entrapment has killed people. A damaged or missing cover means the system does not "
							"run — shut down and lock out until it is replaced with a compliant cover."
						),
						"options": [
							{
								"text": "Shut the system down and lock it out until a compliant cover is fitted",
								"is_correct": True,
							},
							{
								"text": "Note it for the next visit and leave the feature running",
								"is_correct": False,
							},
							{
								"text": "Reduce the pump speed and keep it running until the part arrives",
								"is_correct": False,
							},
							{"text": "Tape the crack and monitor it", "is_correct": False},
						],
					},
					{
						"question": "Which of these are genuine anti-entrapment measures?",
						"type": "Multiple Choice",
						"explanation": (
							"They all share one idea — never let a single outlet take the whole flow, and never "
							"let one outlet be fully blocked."
						),
						"options": [
							{"text": "Certified covers rated for the flow through them", "is_correct": True},
							{
								"text": "Multiple suction outlets separated so one cannot be fully blocked",
								"is_correct": True,
							},
							{
								"text": "Balancing the manifold so main drain suction stays distributed across multiple grates",
								"is_correct": True,
							},
							{"text": "A safety vacuum release system", "is_correct": True},
							{"text": "A warning sign at the edge of the basin", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Cartridge and sand filters",
			"chapter": 1,
			"estimated_minutes": 16,
			"summary": "Differential pressure, the 8-to-10 PSI service threshold, and the two cleaning procedures.",
			"blocks": [
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Relieve the air before you open a filter",
					"content": (
						"<p>A filter vessel is a <b>pressure vessel</b>, and if air is trapped in "
						"it, that air is a compressed spring — the same physics as the air-test "
						"warning in Module 1. Opening or loosening a clamp band on a vessel with "
						"trapped air under pressure can launch the lid.</p>"
						"<p>This has killed and maimed people in this trade. The sequence is "
						"always: <b>shut down all system pumps and lock them out, isolate the "
						"vessel by closing its inlet and outlet plumbing valves, open the air "
						"relief valve on the lid, and wait until nothing more comes out of it</b> — "
						"and only then does the locking ring or the lid clamp come off.</p>"
						"<p>It runs in reverse on the way back. Valves open, pump on, and the "
						"<b>air relief stays open until a solid stream of water escapes it</b>. A "
						"vessel buttoned up full of air is the same spring you just let down.</p>"
						"<p>A filter that repeatedly builds air is telling you there is a "
						"suction-side leak. Fix that; do not manage it by bleeding it every "
						"visit.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A filter takes things out. It does not kill anything.",
					"content": (
						"<p>Filtration and sanitation are two different jobs. A filter removes "
						"<b>particles</b> — it makes water clear. Sanitiser deals with what is "
						"<b>alive</b> — it makes water safe and keeps it from going green.</p>"
						"<p>Clear water is not clean water, and a customer who says 'it looks fine' "
						"is reporting on the filter, not on the chemistry. Module 3 is the other "
						"half of this.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The two types, and what maintaining each actually means",
					"panels": [
						{
							"title": "Sand",
							"body": (
								"<p>Water is pushed down through a bed of media; dirt is trapped in "
								"the top of the bed. Cleaning is <b>backwashing</b>: reverse the "
								"flow with a valve and send the dirt to waste, which means you need "
								"a legal, planned discharge point and you lose a quantity of "
								"treated water every time.</p>"
								"<p>Over the years the media rounds off, channels form so water "
								"takes a short cut, and filtration quality falls quietly. Media has "
								"a service life; a filter that has stopped performing after years "
								"is often due a media change rather than a repair.</p>"
							),
						},
						{
							"title": "Cartridge",
							"body": (
								"<p>Water passes through a pleated element with a large surface "
								"area. There is <b>no backwash</b> — you shut down, open the "
								"vessel, remove the element and clean it, which uses far less "
								"water and is why cartridges are common where discharge is "
								"restricted.</p>"
								"<p>Hosing removes debris from the pleats. It does <b>not</b> "
								"remove oils, scale or mineral deposits — those need a soak in the "
								"appropriate cleaner, and an element that is hosed and never soaked "
								"slowly loses capacity while looking perfectly clean.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Differential pressure, and the number that condemns a filter",
					"content": (
						"<p>Every filter vessel carries two gauges: an <b>influent</b> gauge on the "
						"inlet and an <b>effluent</b> gauge on the outlet. The difference between "
						"those two readings is how dirty the media inside has become. Nothing else "
						"in the equipment room tells you that directly.</p>"
						"<p>A reading still means nothing on its own. What means something is how "
						"far it has moved from <b>this vessel's clean baseline</b> — the pressure "
						"it showed with completely clean media, at normal operating speed, with the "
						"valves in their normal positions. Say this one reads <b>12 PSI</b> "
						"clean.</p>"
						"<p><b>Sapphire's service threshold is a rise of 8 to 10 PSI over the clean "
						"baseline.</b> On that 12 PSI vessel, that means roughly 20 to 22 PSI on "
						"the gauge. At that point the media is choked, the system's flow rate is "
						"suffering for it, and the filter is serviced immediately — not noted for "
						"next time.</p>"
						"<p>Which is why the baseline is recorded at commissioning and <b>recorded "
						"again after every clean</b>. Without it, nobody can tell a dirty filter "
						"from a system that has always run at that pressure. With it, it is a "
						"ten-second call.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The two cleaning procedures, step by step",
					"panels": [
						{
							"title": "Backwashing a sand filter",
							"body": (
								"<ol>"
								"<li>Shut down <b>all</b> feature and circulation pumps connected to "
								"the filter vessel.</li>"
								"<li>Depress the handle on the multi-port backwash valve, rotate it "
								"to <b>BACKWASH</b>, and lock it in place.</li>"
								"<li>Open the waste-line sight-glass valve.</li>"
								"<li>Turn the circulation pump on and watch the sight glass. It runs "
								"dark and dirty at first, as the reversed flow lifts the trapped "
								"organic matter out of the sand or glass media bed.</li>"
								"<li>Once the sight glass runs crystal clear — usually two to three "
								"minutes — turn the pump <b>off</b>.</li>"
								"<li>Rotate the handle to <b>RINSE</b> and run the pump for 30 "
								"seconds. This resettles the bed and clears the dirty water still "
								"standing in the pipe, which would otherwise shoot straight back "
								"into a clean basin.</li>"
								"<li>Pump off, handle back to <b>FILTER</b>, pump on. Note the new "
								"clean baseline pressure.</li>"
								"</ol>"
							),
						},
						{
							"title": "Cleaning cartridge elements",
							"body": (
								"<ol>"
								"<li>Turn off all system pumps and isolate the vessel by closing its "
								"inlet and outlet plumbing valves.</li>"
								"<li>Open the air relief valve on top of the lid and bleed off the "
								"stored pressure.</li>"
								"<li>Remove the heavy-duty locking ring or the lid clamps and lift "
								"the filter top off.</li>"
								"<li>Carefully extract the pleated fabric cartridge elements.</li>"
								"<li>Wash the pleats down with a filter spray wand on a garden hose, "
								"held at a <b>45-degree downward angle</b>, working thoroughly from "
								"the top to the bottom.</li>"
								"<li>Inspect the element cores for cracks. Reinstall, lubricate the "
								"main tank body O-ring, and close the lid securely.</li>"
								"<li>Open the plumbing valves and restart the system with the air "
								"relief held open until a stream of water escapes.</li>"
								"</ol>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Two 'nevers' inside those procedures",
					"content": (
						"<p><b>Never turn a multi-port valve handle while a pump is running.</b> "
						"Every stage of the backwash cycle above begins with the pump off, and that "
						"is not padding in the sequence — the running pump is what puts the vessel "
						"under pressure, and the handle is being moved across its ports.</p>"
						"<p><b>Never clean a cartridge element with a high-pressure washer.</b> It "
						"is quicker, and it looks like it works. What it actually does is tear the "
						"engineered fibres of the fabric — after which the element passes water "
						"freely, looks clean, and filters nothing.</p>"
					),
				},
				ask_block(
					"Cleaning frequency, media and discharge are still site decisions",
					"<p>Sapphire gives the trigger — a rise of 8 to 10 PSI over clean baseline — "
					"and that is the number to work to. How quickly a given feature reaches it "
					"depends on the feature, the season and the debris load, so the baseline and "
					"the gauges are what tell you, not a calendar.</p>"
					"<p>Which media, which cleaning chemical, and where backwash water is legally "
					"allowed to go are set by the equipment submittal, the product label and the "
					"local authority. A schedule carried over from another site is a guess.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What is the first thing to do before opening a filter vessel?",
						"type": "Single Choice",
						"explanation": (
							"Shut down, lock out, and open the air relief until nothing more comes out. Trapped "
							"air under pressure can launch the lid, and that has killed people."
						),
						"options": [
							{
								"text": "Shut off and lock out the pumps, isolate the vessel, then open the air relief and wait",
								"is_correct": True,
							},
							{
								"text": "Loosen the clamp band slowly to let pressure escape past it",
								"is_correct": False,
							},
							{
								"text": "Open the drain valve and start the clamp at the same time",
								"is_correct": False,
							},
							{
								"text": "Nothing special — the vessel is not under pressure once the pump stops",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A filter vessel's clean baseline is 12 PSI. At what reading does Sapphire call it choked and due for service?",
						"type": "Single Choice",
						"explanation": (
							"The threshold is a rise of 8 to 10 PSI over this vessel's clean baseline, so 12 PSI "
							"clean means service at roughly 20 to 22 PSI. The absolute number means nothing "
							"without the baseline it is being compared to."
						),
						"options": [
							{
								"text": "Around 20 to 22 PSI — a rise of 8 to 10 PSI over the clean baseline",
								"is_correct": True,
							},
							{"text": "14 PSI — any rise at all means it is dirty", "is_correct": False},
							{"text": "About 36 PSI — roughly triple the baseline", "is_correct": False},
							{
								"text": "There is no figure; you clean it when the water starts to look cloudy",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The backwash sight glass has run crystal clear and the pump is off. What happens before the handle goes back to FILTER?",
						"type": "Single Choice",
						"explanation": (
							"RINSE, with the pump on for 30 seconds. That resettles the sand bed and clears the "
							"dirty water still standing in the pipe, which would otherwise shoot straight back "
							"into a clean basin. Then the handle goes to FILTER and the new baseline is recorded."
						),
						"options": [
							{
								"text": "RINSE, pump on for 30 seconds, to resettle the bed and clear the dirty water left in the pipe",
								"is_correct": True,
							},
							{
								"text": "Nothing — return the handle straight to FILTER and restart",
								"is_correct": False,
							},
							{"text": "A second backwash cycle, to be sure", "is_correct": False},
							{
								"text": "Open the lid and inspect the media bed before restarting",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these belong to Sapphire's cartridge element procedure?",
						"type": "Multiple Choice",
						"explanation": (
							"The spray wand at a 45-degree downward angle, lubricating the tank body O-ring on "
							"reassembly, and restarting with the air relief open are all in it. A pressure washer "
							"tears the engineered fabric fibres, and a torn element passes water freely while "
							"filtering nothing."
						),
						"options": [
							{
								"text": "Wash the pleats with a filter spray wand at a 45-degree downward angle, top to bottom",
								"is_correct": True,
							},
							{
								"text": "Lubricate the main tank body O-ring before closing the lid",
								"is_correct": True,
							},
							{
								"text": "Restart with the air relief open until a stream of water escapes",
								"is_correct": True,
							},
							{
								"text": "Use a high-pressure washer on the stubborn pleats to save time",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Pressure and vacuum gauges",
			"chapter": 1,
			"estimated_minutes": 15,
			"summary": "The two-gauge split that locates a restriction, and the arithmetic that turns both gauges into a flow rate.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Two gauges, two sides of the pump",
					"content": (
						"<p>A <b>vacuum gauge on the suction side</b> measures how hard the pump is "
						"having to work to get water <i>in</i>. A <b>pressure gauge on the "
						"discharge side</b>, usually at the filter, measures how much resistance "
						"there is to pushing water <i>out</i>.</p>"
						"<p>Together they are the most useful diagnostic in the equipment room, "
						"because they separate an upstream restriction from a downstream one "
						"without opening anything.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Reading the pair",
					"content": (
						"<p>Flow is down, and the question is why. The pair answers it:</p>"
						"<ul>"
						"<li><b>Pressure up, vacuum roughly normal</b> — the restriction is "
						"<b>after</b> the pump. Dirty filter, closed or part-closed valve, blocked "
						"return line, scaled heater.</li>"
						"<li><b>Vacuum up, pressure down</b> — the restriction is <b>before</b> "
						"the pump. Full skimmer or pump basket, blocked suction line, closed "
						"suction valve, low water level.</li>"
						"<li><b>Both down</b> — the pump itself is not doing its job. Loss of "
						"prime, a failing impeller, air being drawn in, or a motor problem.</li>"
						"</ul>"
						"<p>The logic is simple once you see it: a blockage downstream <i>builds</i> "
						"pressure, a blockage upstream <i>starves</i> the pump so there is less "
						"water to pressurise.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The same two gauges give you Total Dynamic Head",
					"content": (
						"<p>Read together and converted, the pair tells you the system's <b>Total "
						"Dynamic Head</b> — the whole resistance this pump is working against, "
						"measured live, on this installation rather than on a drawing.</p>"
						"<p>Two conversions do it:</p>"
						"<ul>"
						"<li><b>Discharge pressure gauge:</b> PSI x 2.31 = feet of head.</li>"
						"<li><b>Suction vacuum gauge:</b> inches of mercury x 1.13 = feet of "
						"head.</li>"
						"</ul>"
						"<p><b>Add the two together.</b> That sum is the real-time TDH. Both halves "
						"count — the work of getting water in is as real as the work of pushing it "
						"out — and dropping the vacuum reading is the usual way people come up "
						"short.</p>"
						"<p>Worked through: 20 PSI on the discharge is 46.2 feet of head, 5 inches "
						"of mercury on the suction is 5.65 feet, and the system is running at about "
						"52 feet of head.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Plotting it on the manufacturer's curve",
					"content": (
						"<p>A pump curve is the manufacturer's statement of what that pump can do: "
						"how much water it moves against how much friction resistance. <b>The "
						"vertical axis is Total Dynamic Head</b>, in feet. <b>The horizontal axis "
						"is flow rate</b>, in gallons per minute. The line between them is the "
						"pump.</p>"
						"<p>So the two gauges give you a flow rate without a flow meter:</p>"
						"<ol>"
						"<li>Find your measured TDH on the vertical axis.</li>"
						"<li>Move horizontally across until you intersect the pump's operational "
						"line.</li>"
						"<li>Drop straight down to the horizontal axis and read the GPM.</li>"
						"</ol>"
						"<p>Then look at <i>where</i> on the curve you landed. A point sitting out "
						"at the extreme right or the extreme left means the pump is running outside "
						"the window it was designed for, and it pays for that in <b>motor "
						"overheating, cavitation pitting on the impeller, and premature bearing "
						"failure</b>. None of which stops it moving water in the meantime — which "
						"is exactly why this gets plotted rather than assumed.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "Same rule as the filter: record the clean baseline",
					"content": (
						"<p>These readings are only meaningful against what this system showed "
						"when everything was clean and correct. Record both gauges at "
						"commissioning, and after every filter clean, alongside the operating "
						"speed and the valve positions.</p>"
						"<p>That set of numbers is the single most valuable thing handed to "
						"whoever services the feature in three years' time.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Gauges fail, and they fail reading something plausible",
					"content": (
						"<p>A gauge that has been shaken, frozen, or run dry commonly sticks. A "
						"stuck gauge does not read zero and look broken — it reads a normal-looking "
						"number and stays there, and somebody troubleshoots the system around it "
						"for an hour.</p>"
						"<p>Two cheap checks: the needle should fall back when the pump stops — to "
						"zero only where the equipment sits above the water line — and it should "
						"visibly move when you throttle a valve. A needle that does neither is the "
						"fault.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Filter pressure is high and suction vacuum is normal. Where is the restriction?",
						"type": "Single Choice",
						"explanation": (
							"A blockage downstream of the pump builds pressure. Dirty filter, a part-closed valve, "
							"a blocked return, a scaled heater."
						),
						"options": [
							{
								"text": "Downstream of the pump — filter, valve, return line or heater",
								"is_correct": True,
							},
							{"text": "Upstream of the pump — skimmer or suction line", "is_correct": False},
							{"text": "In the pump itself", "is_correct": False},
							{"text": "It cannot be determined from gauges", "is_correct": False},
						],
					},
					{
						"question": "Suction vacuum is high and discharge pressure is low. What does that pattern mean?",
						"type": "Single Choice",
						"explanation": (
							"An upstream blockage starves the pump, so there is less water to pressurise. High "
							"vacuum with low pressure is the signature of a suction-side restriction."
						),
						"options": [
							{
								"text": "The pump is being starved by a restriction before it — basket, line or valve",
								"is_correct": True,
							},
							{"text": "The filter is dirty", "is_correct": False},
							{"text": "The return lines are blocked", "is_correct": False},
							{"text": "The gauges have been fitted the wrong way round", "is_correct": False},
						],
					},
					{
						"question": "The discharge gauge reads 20 PSI and the suction vacuum gauge reads 5 inches of mercury. What is the system's Total Dynamic Head?",
						"type": "Single Choice",
						"explanation": (
							"PSI x 2.31 gives 46.2 feet, inches of mercury x 1.13 gives 5.65 feet, and TDH is the "
							"sum of the two — about 52 feet. The suction side counts."
						),
						"options": [
							{"text": "About 52 feet of head", "is_correct": True},
							{
								"text": "About 46 feet of head — the vacuum reading is not part of TDH",
								"is_correct": False,
							},
							{"text": "25 feet of head — the two readings add directly", "is_correct": False},
							{"text": "About 12 feet of head", "is_correct": False},
						],
					},
					{
						"question": "A pump plots out at the extreme right-hand end of its curve. What does that cost?",
						"type": "Multiple Choice",
						"explanation": (
							"Either extreme of the curve is outside the pump's design window: the motor "
							"overheats, the impeller pits from cavitation, and the bearings fail early. It keeps "
							"moving water the whole time it is happening."
						),
						"options": [
							{"text": "Motor overheating", "is_correct": True},
							{"text": "Cavitation pitting on the impeller", "is_correct": True},
							{"text": "Premature bearing failure", "is_correct": True},
							{
								"text": "Nothing — the far right of the curve is the efficient end",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Chemical treatment systems",
			"chapter": 1,
			"estimated_minutes": 13,
			"summary": "Feeders, injection points, the flow interlock, and the two chemicals that must never meet.",
			"blocks": [
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Acid and chlorine make a poison gas",
					"content": (
						"<p>Mixing an acid with a chlorine product — in a container, in a feeder, "
						"in a puddle on the floor of a vault, or in a shared spill tray — releases "
						"<b>chlorine gas</b>. It is heavily toxic, it is heavier than air so it "
						"stays down where you are working, and a lungful in an enclosed equipment "
						"room is a life-changing injury.</p>"
						"<p>So: separate storage, separate containment, separate transfer "
						"equipment, and never a shared measuring jug. Never add water to acid — "
						"always <b>acid into water</b>. And chemicals go into the system, never "
						"into each other.</p>"
						"<p>Read the safety data sheet for every product you carry, and know where "
						"the eyewash is before you open a container.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "How chemical actually gets into the water",
					"content": (
						"<p>Three arrangements cover most of what you will meet:</p>"
						"<ul>"
						"<li><b>Erosion (tablet) feeders</b> — flow passes over solid product and "
						"dissolves it. Simple, and the feed rate is only roughly controllable.</li>"
						"<li><b>Metering pumps</b> — a small positive-displacement pump injects "
						"liquid product at a controlled rate from a day tank.</li>"
						"<li><b>Controller-driven feed</b> — a pH probe and an ORP probe measure "
						"the water continuously and switch the feeders to hold a setpoint.</li>"
						"</ul>"
						"<p>A controller is only as good as its probes. Probes foul, drift and age, "
						"and a fouled probe reports a comfortable number while the water goes "
						"wrong. They are calibrated against a hand test, not trusted.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Where it goes in, and what stops it coming back",
					"content": (
						"<p><b>Inject downstream of the filter and the heater</b>, into the return "
						"line, so concentrated product is carried away and diluted into the whole "
						"body of water. Injecting upstream sends neat chemical through equipment "
						"that will not survive it — heat exchangers in particular.</p>"
						"<p><b>Fit the check valve, and understand what it is for.</b> When the "
						"pump stops, the return line can siphon backwards. Without a working check "
						"valve at the injection point, a feed line can drain into the system or "
						"the system can push back into the day tank — either way the result is a "
						"slug of concentrated chemical somewhere it should never be.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The flow interlock is not optional",
					"content": (
						"<p>Chemical feed must be <b>interlocked with circulation</b> — a flow "
						"switch, or an interlock through the pump starter — so that nothing can be "
						"dosed into a system that is not moving water.</p>"
						"<p>Without it, a feeder that keeps running after the pump stops builds a "
						"concentrated plug of chemical in the pipe. The next start-up delivers "
						"that plug, all at once, out to the feature. That is how a swimmer or a "
						"passer-by gets chemically burned.</p>"
					),
				},
				ask_block(
					"Which products, which setpoints, which permits",
					"<p>Which sanitiser and which acid a given feature uses, what the target levels "
					"are, and what the local health authority requires for a publicly accessible "
					"water feature are all site decisions with documents behind them.</p>"
					"<p>Module 3 covers what the chemistry means. What goes in <i>this</i> feature "
					"comes from its water treatment design, the product labels, and the authority "
					"having jurisdiction.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why must acid and chlorine products never share storage or transfer equipment?",
						"type": "Single Choice",
						"explanation": (
							"Mixing them releases chlorine gas, which is toxic and heavier than air — so it stays "
							"at working height in an equipment room."
						),
						"options": [
							{
								"text": "Mixing them releases toxic chlorine gas that settles where you are standing",
								"is_correct": True,
							},
							{"text": "They neutralise each other and waste product", "is_correct": False},
							{
								"text": "The acid corrodes the chlorine container over time",
								"is_correct": False,
							},
							{"text": "It is only a labelling requirement", "is_correct": False},
						],
					},
					{
						"question": "Where should chemical be injected relative to the heater?",
						"type": "Single Choice",
						"explanation": (
							"Downstream. Neat chemical sent through a heat exchanger destroys it; injecting into "
							"the return carries the product away and dilutes it."
						),
						"options": [
							{"text": "Downstream of the heater, into the return line", "is_correct": True},
							{
								"text": "Upstream of the heater, so the heat helps it dissolve",
								"is_correct": False,
							},
							{"text": "Directly into the pump strainer pot", "is_correct": False},
							{
								"text": "It makes no difference as long as there is a check valve",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What does a flow interlock on the chemical feed prevent?",
						"type": "Single Choice",
						"explanation": (
							"It stops dosing into a system that is not circulating. Without it, a concentrated "
							"plug builds in the pipe and is delivered all at once at the next start."
						),
						"options": [
							{
								"text": "Dosing into a stopped system, which builds a concentrated plug in the pipe",
								"is_correct": True,
							},
							{"text": "Overdosing during normal circulation", "is_correct": False},
							{"text": "The metering pump running dry", "is_correct": False},
							{"text": "Probe fouling", "is_correct": False},
						],
					},
					{
						"question": "A controller reports pH and sanitiser both on setpoint, but the water is visibly deteriorating. What is the first suspicion?",
						"type": "Single Choice",
						"explanation": (
							"Probes foul, drift and age, and a fouled probe reports a comfortable number. "
							"Controllers are calibrated against a hand test, not trusted."
						),
						"options": [
							{
								"text": "The probes are fouled or out of calibration — confirm with a hand test",
								"is_correct": True,
							},
							{"text": "The controller setpoints are too tight", "is_correct": False},
							{
								"text": "The filter needs backwashing, which does not affect chemistry",
								"is_correct": False,
							},
							{
								"text": "The readings are correct, so the water must be fine",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Heaters and chillers",
			"chapter": 1,
			"estimated_minutes": 11,
			"summary": "Flow before fire, combustion air, and the circuit you do not open.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Flow first, always",
					"content": (
						"<p>Every heater has a <b>minimum flow rate</b>, and every heater has some "
						"means of proving flow before it fires — a pressure switch, a flow switch, "
						"or both. That interlock exists because firing a heat exchanger with "
						"little or no water through it destroys it, and on a gas appliance it can "
						"do considerably worse.</p>"
						"<p>So a flow switch is never jumpered 'just to test'. If a heater will "
						"not fire, the honest first question is whether it is <i>right</i> not to "
						"fire.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Where it sits in the loop",
					"content": (
						"<p>Conventionally the order is <b>pump → filter → heater → return</b>, "
						"with chemical injection <b>after</b> the heater.</p>"
						"<ul>"
						"<li><b>After the filter</b>, so debris is removed before it reaches the "
						"heat exchanger.</li>"
						"<li><b>Before the chemical injection</b>, so neat product never passes "
						"through the exchanger.</li>"
						"<li><b>With a bypass</b>, so flow can be set to what the heater wants "
						"while the rest of the system runs at its own rate, and so the heater can "
						"be isolated for service.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Gas appliances need air, and produce carbon monoxide",
					"content": (
						"<p>A gas heater needs <b>combustion air</b> and a correct <b>vent</b>. "
						"Both are engineered — clearances, vent material, termination location, "
						"and the free area of the openings supplying air.</p>"
						"<p>Putting one into a tight vault, an enclosure, or a room that was later "
						"sealed up produces carbon monoxide, and carbon monoxide has no smell. "
						"People have died in equipment rooms this way.</p>"
						"<p>Gas piping and appliance venting are licensed work in most "
						"jurisdictions. If the installation does not match the manufacturer's "
						"instructions, it stops there.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Chillers, and the line you do not cross",
					"content": (
						"<p>A chiller moves heat out of the water instead of into it, using a "
						"refrigeration circuit. Two rules:</p>"
						"<p><b>The refrigerant circuit is not yours.</b> Opening it, charging it, "
						"or recovering from it requires certification, and venting refrigerant is "
						"a federal offence with real penalties. Water side, electrical "
						"disconnects and airflow are the technician's side of the line.</p>"
						"<p><b>Airflow is a performance spec.</b> An air-cooled condenser needs "
						"clearance and unobstructed air. Fence it in, plant a shrub in front of "
						"it, or stack crates beside it, and it recirculates its own hot exhaust "
						"and loses capacity — then somebody diagnoses it as low on gas.</p>"
					),
				},
				ask_block(
					"Water chemistry decides how long a heat exchanger lasts",
					"<p>Aggressive water eats metal heat exchangers and scaling water coats them. "
					"Either way the exchanger is the first thing in the system to show it, and a "
					"warranty claim usually asks for chemistry records.</p>"
					"<p>What material this exchanger is and what water it tolerates comes from the "
					"submittal. Module 3 and Module 10 cover the chemistry side of the same "
					"problem.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A heater will not fire and the flow switch is suspected. What is the right first question?",
						"type": "Single Choice",
						"explanation": (
							"Whether it is correct not to fire. The interlock exists because firing without flow "
							"destroys the exchanger, so it is never jumpered to test."
						),
						"options": [
							{
								"text": "Whether the heater is right not to fire — is there actually adequate flow?",
								"is_correct": True,
							},
							{
								"text": "Whether the switch can be jumpered briefly to confirm the heater works",
								"is_correct": False,
							},
							{"text": "Whether the thermostat setpoint is too low", "is_correct": False},
							{"text": "Whether the gas valve has failed", "is_correct": False},
						],
					},
					{
						"question": "Why is chemical injected downstream of the heater rather than upstream?",
						"type": "Single Choice",
						"explanation": (
							"Neat chemical passing through a heat exchanger destroys it. Injecting into the return "
							"dilutes the product into the whole body of water."
						),
						"options": [
							{"text": "Neat product would attack the heat exchanger", "is_correct": True},
							{"text": "The heater would evaporate the chemical", "is_correct": False},
							{"text": "It would trip the flow switch", "is_correct": False},
							{"text": "It would not mix properly at higher temperature", "is_correct": False},
						],
					},
					{
						"question": "Which of these is outside a technician's scope on a chiller?",
						"type": "Single Choice",
						"explanation": (
							"Opening, charging or recovering refrigerant requires certification, and venting "
							"refrigerant carries federal penalties."
						),
						"options": [
							{
								"text": "Opening, charging or recovering the refrigerant circuit",
								"is_correct": True,
							},
							{"text": "Clearing obstructions from around the condenser", "is_correct": False},
							{"text": "Checking water-side isolation valves", "is_correct": False},
							{"text": "Verifying the unit's electrical disconnect", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Fountain structures",
			"chapter": 2,
			"estimated_minutes": 11,
			"summary": "Vessels, embeds and joints — and why sequencing is where fountains go wrong.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Water is much heavier than people expect",
					"content": (
						"<p>Water weighs roughly <b>8.34 pounds per gallon</b>, or about "
						"<b>62.4 pounds per cubic foot</b>. A basin two feet deep is carrying "
						"around 125 pounds on every square foot of its floor before anybody stands "
						"in it.</p>"
						"<p>That number is why a fountain is a structural element rather than a "
						"decoration, why the structural engineer cares about it, and why 'we will "
						"just make the basin a bit bigger' is never a field decision.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The vessel types you will meet",
					"content": (
						"<ul>"
						"<li><b>Cast-in-place concrete</b> — poured on site. Strong, permanent, "
						"and entirely unforgiving about anything you forgot to put in before the "
						"pour.</li>"
						"<li><b>Pre-cast</b> — delivered and set. Fast, but the penetrations were "
						"decided at the casting yard.</li>"
						"<li><b>Stainless steel</b> — fabricated vessels and troughs. Watch the "
						"alloy and the welds, and watch what dissimilar metals are touching "
						"it.</li>"
						"<li><b>Lined</b> — a membrane doing the water-holding over a structure "
						"that does the load-carrying.</li>"
						"<li><b>Balance or surge tanks</b> — the subterranean holding vessel a "
						"zero-depth feature drains into. It is a vessel like any other here, and "
						"the last lesson of this module covers what it does and how its level is "
						"controlled.</li>"
						"</ul>"
						"<p>In every case, <b>the thing holding the water and the thing holding "
						"the load may not be the same thing</b>. Knowing which is which on the "
						"feature in front of you determines what you may cut, drill or "
						"anchor into.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Embeds and sleeves go in before the pour, or they cost ten times as much",
					"content": (
						"<p>Every penetration, sleeve, anchor plate, niche, conduit and drain body "
						"that should be cast into concrete has exactly one cheap moment: before "
						"the truck arrives.</p>"
						"<p>Afterwards you are core drilling through a structural element — which "
						"means locating reinforcement first, getting engineering approval to cut "
						"anything you find, and then making a penetration watertight through a "
						"structure that was never detailed for one.</p>"
						"<p>This is why fountain work is so sequencing-heavy, and why the layout "
						"review before a pour is worth more than any day of work after it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Joints move, and the waterproofing has to move with them",
					"content": (
						"<p>Structures expand, contract, shrink as they cure and settle. Designers "
						"put <b>movement joints</b> in to control where that happens.</p>"
						"<p>A joint that is simply tiled and grouted over does not stop moving — "
						"it cracks the finish along the line of the joint, and then it leaks. "
						"Every joint has to be carried through <i>every</i> layer: the structure, "
						"the waterproofing, the setting bed and the finish, each with the detail "
						"that layer needs.</p>"
						"<p>Module 5 is about the waterproofing side of this and Module 4 about "
						"the finish side. They are the same problem seen from two trades.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "Roughly how much does water weigh?",
						"type": "Single Choice",
						"explanation": (
							"About 8.34 pounds per gallon, or about 62.4 pounds per cubic foot — which is why a "
							"basin is a structural element."
						),
						"options": [
							{
								"text": "About 8.34 pounds per gallon, or 62.4 pounds per cubic foot",
								"is_correct": True,
							},
							{"text": "About 5 pounds per gallon", "is_correct": False},
							{"text": "About 12 pounds per gallon", "is_correct": False},
							{"text": "It depends too much on temperature to generalise", "is_correct": False},
						],
					},
					{
						"question": "Why is a missed embed in a cast-in-place basin so expensive?",
						"type": "Single Choice",
						"explanation": (
							"You end up core drilling a structural element: locate reinforcement, get approval to "
							"cut anything found, then waterproof a penetration never detailed for one."
						),
						"options": [
							{
								"text": "It becomes a core drill through structure, needing reinforcement located and engineering approval",
								"is_correct": True,
							},
							{
								"text": "The concrete has to be removed and re-poured entirely",
								"is_correct": False,
							},
							{
								"text": "It only delays the schedule; the cost is the same",
								"is_correct": False,
							},
							{"text": "It voids the concrete supplier's warranty", "is_correct": False},
						],
					},
					{
						"question": "A movement joint in a basin has been tiled and grouted straight over. What happens?",
						"type": "Single Choice",
						"explanation": (
							"The joint keeps moving. The finish cracks along the joint line and then it leaks — "
							"the joint must be carried through every layer."
						),
						"options": [
							{
								"text": "The joint keeps moving, cracks the finish along its line, and leaks",
								"is_correct": True,
							},
							{"text": "Nothing, provided the grout is flexible", "is_correct": False},
							{
								"text": "The joint is locked and the structure stops moving",
								"is_correct": False,
							},
							{"text": "The waterproofing below will absorb the movement", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Concrete anchoring",
			"chapter": 2,
			"estimated_minutes": 12,
			"summary": "Mechanical versus adhesive, and the one step that causes most adhesive failures.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Two families, two failure modes",
					"content": (
						"<p><b>Mechanical anchors</b> — wedge, sleeve and screw anchors — grip by "
						"friction and by biting into the concrete. They can be loaded quickly, and "
						"the expansion types put outward force into the concrete, so edge distance "
						"and spacing matter a great deal — every type has its own edge and spacing "
						"table.</p>"
						"<p><b>Adhesive anchors</b> — threaded rod or rebar set in an injected "
						"epoxy or acrylic — bond to the walls of the hole. They put no expansive "
						"force in, they work closer to an edge, they fill irregular holes, and "
						"they need <b>cure time</b> before any load goes on.</p>"
						"<p>Which is specified is a design decision. Substituting one for the "
						"other is not a like-for-like swap.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Hole cleaning is the whole job on an adhesive anchor",
					"content": (
						"<p>Drilling concrete produces a fine dust that coats the walls of the "
						"hole. Adhesive injected onto that dust bonds to <b>dust</b>, not to "
						"concrete, and the anchor can pull straight out at a fraction of its rated "
						"load.</p>"
						"<p>This is the number one cause of adhesive anchor failure in the field. "
						"Overhead adhesive anchors under sustained load have failed and killed, "
						"which is why the adhesive, its hole-cleaning procedure and its "
						"sustained-load qualification all have to be the ones in the evaluation "
						"report for that exact product.</p>"
						"<p>The manufacturer's instructions give an <b>exact</b> procedure — a "
						"specified number of blow, brush and blow cycles, with a brush of a "
						"specified diameter. It is not 'give it a blast with the compressor'. "
						"That sequence is part of the anchor's rating, and doing three-quarters of "
						"it does not give you three-quarters of the strength.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Everything about an anchor is in a table",
					"content": (
						"<p>Hole diameter, hole depth, embedment, minimum edge distance, minimum "
						"spacing, base material thickness, cure time at a given temperature, "
						"installation torque, and whether the anchor is approved for cracked or "
						"uncracked concrete — all of these come from the manufacturer's data and "
						"the evaluation report for that exact product.</p>"
						"<p>They are not interchangeable between products that look identical, and "
						"they are not adjustable on site. <b>Too close to an edge</b> is the "
						"common field failure: a mechanical anchor set near an edge blows a cone "
						"of concrete out sideways instead of holding.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you load an anchor",
					"items": [
						"The anchor is the one specified, not a similar one from the truck",
						"The hole is the specified diameter and depth",
						"The hole was cleaned by the manufacturer's exact procedure",
						"Edge distance and spacing meet the table for this anchor",
						"Nothing structural was cut — reinforcement was located before drilling",
						"Adhesive has had its full cure time at the actual temperature",
						"Torque is the specified figure, applied with a torque wrench",
						"The anchor material suits a permanently wet, chemically treated environment",
					],
				},
				ask_block(
					"Cutting reinforcement is an engineering decision",
					"<p>If a drill hits rebar, stopping and asking is the correct move. Cutting "
					"reinforcement in a structural element is an engineer's call, and a fountain "
					"basin is very often structural.</p>"
					"<p>Scan or locate before drilling into anything that holds water or holds "
					"load, and treat a strike as a stop, not an obstacle.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What is the most common cause of adhesive anchor failure in the field?",
						"type": "Single Choice",
						"explanation": (
							"Inadequate hole cleaning. Adhesive injected onto drilling dust bonds to dust, and the "
							"anchor pulls out at a fraction of its rated load."
						),
						"options": [
							{
								"text": "The hole was not cleaned by the manufacturer's procedure",
								"is_correct": True,
							},
							{"text": "Too much adhesive was injected", "is_correct": False},
							{"text": "The threaded rod was not galvanised", "is_correct": False},
							{"text": "The anchor was torqued too soon after curing", "is_correct": False},
						],
					},
					{
						"question": "Why is edge distance more critical for a wedge anchor than for an adhesive anchor?",
						"type": "Single Choice",
						"explanation": (
							"A mechanical anchor works by expansion, putting outward force into the concrete. Near "
							"an edge that blows a cone of concrete out sideways."
						),
						"options": [
							{
								"text": "It works by expansion, and the outward force can blow out a cone of concrete near an edge",
								"is_correct": True,
							},
							{"text": "Adhesive anchors are always stronger", "is_correct": False},
							{"text": "Wedge anchors need a larger hole diameter", "is_correct": False},
							{
								"text": "Edge distance only matters for overhead installations",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A drill bit hits rebar part way into a hole in a basin wall. What is the correct response?",
						"type": "Single Choice",
						"explanation": (
							"Stop and ask. Cutting reinforcement in a structural element is an engineer's "
							"decision, and a fountain basin is very often structural."
						),
						"options": [
							{
								"text": "Stop and escalate — cutting reinforcement is an engineering decision",
								"is_correct": True,
							},
							{"text": "Switch to a rebar-cutting bit and continue", "is_correct": False},
							{
								"text": "Move over two inches and drill again without telling anyone",
								"is_correct": False,
							},
							{
								"text": "Reduce the embedment depth and set the anchor short",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Pedestal false floors",
			"chapter": 2,
			"estimated_minutes": 15,
			"summary": "A deck over a sloped sub-slab, the balance tank it drains to, and the three levels the controller works between.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "What the system is",
					"content": (
						"<p>A pedestal false floor is a walkable deck — stone pavers, tile or "
						"grating — carried on <b>heavy-duty adjustable plastic pedestals</b> that "
						"stand on a waterproofed concrete sub-slab. The jets come up through the "
						"deck, and there is no visible basin edge at all.</p>"
						"<p>The joints between the pavers are <b>entirely open</b>, and deliberately "
						"so. The pedestals carry <b>modular spacer tabs</b> that set that gap for "
						"you — consistent and uniform, <b>typically 1/8 to 3/16 inch</b> — so water "
						"sprayed from the nozzles drains instantly down through the floor grid "
						"instead of standing on it.</p>"
						"<p>Underneath, the sub-slab is <b>sloped</b>, and it carries that water "
						"back to the holding tank. Be precise about this part: the void beneath the "
						"deck is a drainage and service space, and <b>the reservoir is the balance "
						"tank</b> — a separate vessel, usually subterranean. Getting those two the "
						"wrong way round matters, because it is the tank's level, not anything "
						"under the deck, that the controller watches and the pumps depend on.</p>"
						"<p>It is how a plaza can be a dry, usable public space one minute and a "
						"water feature the next. Everything that makes that possible also makes it "
						"a service challenge, because the entire system is under a floor people "
						"walk on.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "The deck is level. The sub-slab underneath is not.",
					"content": (
						"<p>This is the detail that gets inverted in the field, and it is worth "
						"stating plainly: the <b>finished deck is set level</b> — people walk on "
						"it, and it has to look flat — while the <b>sub-slab beneath it is "
						"pitched</b>, to drain back to the holding tank.</p>"
						"<p>That is exactly what the adjustable pedestals are for. They take up "
						"the difference between a sloping structural surface and a level walking "
						"surface, and their adjustment keys are how you prove the deck is true "
						"across a run of pavers. Pitching the deck to match the slab gains nothing "
						"— the open joints already pass water straight through — and gives you a "
						"walking surface that slopes where it should be flat, and a feature that "
						"trips people.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The plumbing lives in the void",
					"content": (
						"<p>Nozzle supply lines and light conduits are routed through the open "
						"space beneath the pedestals. Two things follow from that, and both are the "
						"point of building a deck this way.</p>"
						"<p><b>Nothing is under the traffic.</b> The pavers and the pedestals carry "
						"the foot load; the pipe and conduit sit in the void below and take none of "
						"it.</p>"
						"<p><b>Service is a lift, not a demolition.</b> A failed nozzle or valve is "
						"reached by lifting out the surrounding paver tiles to expose the plumbing, "
						"and then putting them back. That is the whole maintenance model of a "
						"raised deck — and it only works if whoever set the equipment put it where "
						"pavers can actually be lifted over it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The balance tank is the reservoir",
					"content": (
						"<p>A zero-depth feature — a splash pad, a dry-deck plaza fountain — holds "
						"no standing pool of water on the play deck. Everything drains straight off "
						"the surface into a <b>subterranean holding tank</b>, and that tank is the "
						"system's hydraulic buffer.</p>"
						"<p>'Buffer' means something specific here. The tank has to hold enough "
						"volume to <b>fill every pipe and every nozzle in the system</b> at the "
						"moment the Splash Wizard controller starts the feature pumps — and enough "
						"room spare to take all of that water back when the system shuts down and "
						"the decks drain down into it. A tank sized only for the running state "
						"overflows at every shutdown.</p>"
						"<p>The level in it is watched by <b>digital ultrasonic level sensors</b> or "
						"by <b>multi-tier float switches</b> suspended inside the tank, reporting to "
						"the Splash Wizard MAX or INDUSTRIAL controller.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The three levels the controller works between",
					"panels": [
						{
							"title": "Level 1 — high-water fill / refill solenoid",
							"body": (
								"<p>Evaporation and splash-out take water out of the system "
								"continuously. When the tank volume drops below the safe "
								"operational threshold, the controller opens an automatic "
								"water-makeup valve and tops it back up.</p>"
								"<p>Worth knowing as a diagnostic: a feature that is losing water "
								"faster than it should is losing it somewhere, and a make-up "
								"solenoid will hide that indefinitely if nobody is looking at how "
								"often it runs.</p>"
							),
						},
						{
							"title": "Level 2 — operating level",
							"body": (
								"<p>The sweet spot. Enough volume in the tank to run the water "
								"features, and enough freeboard that the water draining back down "
								"from the decks at shutdown does not put it over the top.</p>"
							),
						},
						{
							"title": "Level 3 — low-water cut-off",
							"body": (
								"<p>If the level drops critically low, the controller instantly cuts "
								"power to the feature pumps and the circulation pumps. It is "
								"protecting the pumps: at that level they would be drawing in air, "
								"cavitating and running dry — and a dry mechanical seal has fifteen "
								"seconds.</p>"
								"<p>So a feature that has shut itself off on low water has done its "
								"job. The question is where the water went, not how to get past the "
								"cut-off.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Everything on the deck ends up in the water",
					"content": (
						"<p>Leaves, grit, cigarette ends, coins, food, dust, dropped litter — all "
						"of it goes through the open joints between the pavers, onto the sub-slab, "
						"and down to the holding tank with the water. That is not a defect, it is "
						"how the system works, and it defines the maintenance regime "
						"completely.</p>"
						"<p>It is why these systems need real filtration and real access, why the "
						"tank and its sump need cleaning far more often than a conventional basin, "
						"and why debris in the reservoir is Module 8's most predictable "
						"troubleshooting call.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Access is a design requirement, not an afterthought",
					"content": (
						"<p>Every pump, valve, light, nozzle, probe and junction box under that "
						"deck has to be reachable by a person lifting pavers — one at a time, by "
						"hand, with the deck around them still safe to stand on.</p>"
						"<p>So during installation, check the thing nobody checks: <b>can you "
						"actually lift the panel over each piece of equipment, and is there room "
						"to work once it is off?</b> A pump that needs four pavers removed and a "
						"structural pedestal cut out is a pump that will not be serviced properly "
						"for the rest of its life.</p>"
						"<p>The same thought covers the walking surface: pedestal spacing, paver "
						"thickness and edge restraint are engineered for the load, joints have to "
						"stay within the gap the design allows, and a rocking paver over water is "
						"a genuine hazard rather than a snag-list item.</p>"
					),
				},
				ask_block(
					"Loads, spacing and accessibility requirements are designed",
					"<p>Pedestal spacing, paver thickness, edge restraint and any accessibility "
					"requirement for the walking surface are set by the design and by code, and "
					"they depend on whether the deck carries pedestrians, a maintenance vehicle or "
					"a crowd. The spacer tabs hand you the typical 1/8 to 3/16 inch joint; whether "
					"that width is acceptable underfoot on <i>this</i> deck is an accessibility "
					"question with a code answer behind it.</p>"
					"<p>If what arrives on site does not match the drawing, that is a change to "
					"raise rather than adapt to.</p>",
				),
				{
					"block_type": "Checklist",
					"heading": "Module 2 sign-off — demonstrate these to a Lead Installer",
					"items": [
						"Align a centrifugal pump housing flush with a suction manifold and bolt it securely to an isolation pad",
						"Fill a pump strainer housing, inspect and lubricate the lid ring, and establish a prime under suction lift",
						"Perform a full backwash and rinse cycle on a commercial sand filter, using the multi-port safety steps",
						"Extract, clean, inspect and safely reinstall the pleated elements of a cartridge filter housing",
						"Assemble a four-pedestal layout grid and check true level across mock stone pavers with the adjustment keys",
						"Calculate system TDH from live manifold gauge readings and plot the flow rate on a manufacturer's pump curve",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "On a pedestal false floor, which surface is pitched to drain?",
						"type": "Single Choice",
						"explanation": (
							"The sub-slab below is pitched, back to the holding tank; the deck above is set "
							"level. Adjustable pedestals take up the difference — that is what they are for."
						),
						"options": [
							{
								"text": "The sub-slab below — the deck above is set level on adjustable pedestals",
								"is_correct": True,
							},
							{"text": "The deck above — the sub-slab below is poured level", "is_correct": False},
							{"text": "Both are pitched at the same angle", "is_correct": False},
							{"text": "Neither; the sump pump handles all drainage", "is_correct": False},
						],
					},
					{
						"question": "What sets the width of the open joints between the pavers?",
						"type": "Single Choice",
						"explanation": (
							"Modular spacer tabs on the pedestals set a consistent, uniform gap, typically 1/8 to "
							"3/16 inch. Those open joints are what let the deck drain straight through to the "
							"sloped sub-slab below."
						),
						"options": [
							{
								"text": "Modular spacer tabs on the pedestals, giving a uniform gap of typically 1/8 to 3/16 inch",
								"is_correct": True,
							},
							{
								"text": "The setter's eye, checked with a tape at each course of pavers",
								"is_correct": False,
							},
							{"text": "Grout, raked back to a consistent depth once it cures", "is_correct": False},
							{
								"text": "Nothing — the joints are closed and the deck drains at its perimeter",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The tank level falls critically low. What does the controller do, and why?",
						"type": "Single Choice",
						"explanation": (
							"Level 3 is the low-water cut-off: power to the feature and circulation pumps is cut "
							"instantly, so they cannot draw air, cavitate or run dry. The make-up solenoid is "
							"Level 1, a much earlier trip — by Level 3 it is the pumps being protected."
						),
						"options": [
							{
								"text": "Cuts power to the feature and circulation pumps, so they cannot draw air, cavitate or run dry",
								"is_correct": True,
							},
							{
								"text": "Opens the make-up valve and keeps the pumps running while it refills",
								"is_correct": False,
							},
							{
								"text": "Sounds an alarm and leaves the pumps to the technician",
								"is_correct": False,
							},
							{
								"text": "Drops the pumps to low speed until the level recovers",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why does a pedestal deck system need more frequent tank and filter attention than a conventional basin?",
						"type": "Single Choice",
						"explanation": (
							"Everything dropped or blown onto the deck falls through the open joints, onto the "
							"sub-slab and down to the holding tank with the water. That is how the system works, "
							"and it sets the maintenance regime."
						),
						"options": [
							{
								"text": "Everything dropped on the deck falls through the joints and ends up in the holding tank",
								"is_correct": True,
							},
							{
								"text": "The holding tank is shallower, so the chemistry swings faster",
								"is_correct": False,
							},
							{
								"text": "Pedestals shed material into the water as they wear",
								"is_correct": False,
							},
							{"text": "It does not; it needs less because it is covered", "is_correct": False},
						],
					},
				]
			},
		},
	],
}
