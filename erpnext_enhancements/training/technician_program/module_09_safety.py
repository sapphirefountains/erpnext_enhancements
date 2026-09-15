# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 9 — Jobsite Safety, Best Practices & Code Requirements.

Sapphire has no Module 9 document of its own. The safety material is lesson 7.3 of *Module 7:
Service Operations & Safety Protocols* — confined space entry and lock-out/tag-out — and it is
written here rather than in Module 7, which says so in its own docstring. Where that lesson
gives a figure, a sequence or a rule it is used as written: permit-required as Sapphire's own
classification of a below-ground vault, the calibrated four-gas monitor lowered in before the
hatch is cracked completely, O2 19.5-23.5%, H2S 0 ppm, CO below 25 ppm, LEL 0%, the
explosion-proof blower purging with fresh outdoor air for a minimum of 15 minutes before a
re-test, and the six lock-out steps ending at 0 volts on the terminal block with the key in
your pocket. The two field demonstrations its checklist requires in front of a Lead Installer
are named in the lesson's ask_block().

The other eight lessons -- loading, hoses and cords, tools, electrical, codes, first aid, PPE,
fatigue -- are outside what that document covers. They keep their general-practice content and
the rule that goes with it: where a number belongs to a product, a study or the employer, the
lesson says where to read it instead of printing one. Hence sourced_notice_block(), whose
second paragraph is what tells a learner which half they are reading.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 9 — Jobsite Safety, Best Practices & Code Requirements",
		"summary": (
			"Load a truck so nothing moves in a hard stop, lay out a site the public walks "
			"through, recognise a permit-required confined space and a live conductor before you "
			"touch either, know which document actually governs a job, and spot heat illness in "
			"somebody else while it is still reversible."
		),
		"category": "Safety",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Loading vehicles",
			"estimated_minutes": 12,
			"summary": "What a truck is rated to carry, where the weight has to sit, and why a load that shifts is a crash.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "The truck has a number, and it is not a suggestion",
					"content": (
						"<p>Every vehicle and every trailer carries ratings set by the manufacturer. "
						"<b>GVWR</b> is the most the whole loaded vehicle may weigh. <b>GAWR</b> is the "
						"most each axle may carry. A hitch and a ball each have their own rating, and "
						"they are frequently not the same as each other.</p>"
						"<p><b>Payload is not GVWR.</b> Payload is GVWR minus the truck's curb weight — "
						"the 'combined weight of occupants and cargo' figure on the door-jamb placard. It "
						"counts the crew, and it knows nothing about the rack, the ladder or the toolboxes "
						"somebody bolted on after the truck left the factory. So what a built-out service "
						"truck has <i>left</i> is that payload figure minus the crew, the rack, the ladder, "
						"the toolboxes and whatever has been living in the bed since spring: a service "
						"truck built out for this trade has spent a large part of its payload before "
						"anybody puts a pump in it.</p>"
						"<p>Water is the thing that catches people out, because it does not look like "
						"weight. Water is about <b>8.34 lb per gallon</b> and about <b>62.4 lb per cubic "
						"foot</b>. A drum, a tote or a half-full tank is heavier than the same volume of "
						"almost anything else you carry, and that weight does not go down as it sloshes "
						"— it just moves.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Where the weight sits decides whether the trailer tracks",
					"content": (
						"<p>A trailer is stable because some of its load presses down on the hitch. "
						"<b>Too little tongue weight</b> — load piled behind the axle — and the trailer "
						"starts to sway, the sway feeds itself, and it gets worse with speed rather than "
						"better. <b>Too much tongue weight</b> and the back of the tow vehicle squats, "
						"which lifts weight off the steer axle and takes away your steering and braking "
						"at the same moment you need them.</p>"
						"<p>The rule that follows: <b>heavy low, and slightly forward of the trailer "
						"axle</b>, balanced left to right, and tied so it cannot migrate rearward on the "
						"first hill. In a truck bed the same logic applies — weight over or ahead of the "
						"rear axle, not hanging off the tailgate.</p>"
						"<p>Then walk around it. A load that was balanced when you built it is not "
						"necessarily balanced after two stops and a partial unload, and the second site "
						"of the day is the one where it is wrong.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A load that shifts is a crash",
					"content": (
						"<p>In a hard stop the truck decelerates and the load does not. Anything not "
						"restrained keeps travelling at the speed you were doing, and it stops against "
						"whatever is in front of it — the cab, the bulkhead, or the back of your head. A "
						"length of pipe lying loose in a bed is a spear.</p>"
						"<p>Secure against all four directions: forward, rearward, sideways and "
						"<b>upward</b>, because a load that lifts off the deck over a bump is a load "
						"that is no longer tied to anything. Use rated tie-downs at rated anchor points "
						"— every strap and every anchor has a working load limit stamped on it, and a "
						"strap with a cut edge or a frayed web has lost it.</p>"
						"<p>And a shifting load is not only a load problem. Weight moving sideways in a "
						"corner is how a trailer ends up on its side with nothing else having gone "
						"wrong.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Chemicals ride differently from everything else",
					"content": (
						"<p><b>Upright, closed, and restrained so they stay upright.</b> A container on "
						"its side leaks past a cap that was never designed to hold liquid, and you will "
						"not know until you open the doors.</p>"
						"<p><b>Segregated.</b> Acid and chlorine products must not be able to reach each "
						"other — mixing them produces chlorine gas, and a spill in a closed vehicle is a "
						"confined space with you in it. Keep them apart physically, not just by "
						"intention, and carry them in secondary containment so a leak has somewhere to "
						"go that is not the deck.</p>"
						"<p><b>Never in the passenger space.</b> Not on the back seat, not in the "
						'footwell, not "just for this trip". The cab is the one place on the vehicle '
						"you cannot walk away from.</p>"
						"<p><b>Compressed gas cylinders are secured upright with the valve protection "
						"cap on</b>, and they do not travel loose or in an enclosed cab. A cylinder that "
						"falls and snaps its valve off becomes a rocket, and that is a literal "
						"description rather than a figure of speech.</p>"
						"<p>The safety data sheet for anything you carry travels with it, and it is the "
						"document a paramedic or a fire crew will ask you for.</p>"
					),
				},
				ask_block(
					"What your vehicle is actually rated for",
					"<p>GVWR, GAWR, tongue weight and payload are on the vehicle: the door-jamb "
					"placard, the trailer plate, the hitch and the ball. Read <i>this</i> vehicle "
					"rather than remembering the last one — two trucks that look identical are often "
					"rated differently.</p>"
					"<p>Which vehicles Sapphire allows to tow what, who is authorised to drive them, "
					"and which transport rules apply once chemicals are aboard in quantity are "
					"company and regulatory questions with real consequences attached. If the load "
					"in front of you looks close to the rating, that is a call to a supervisor, not "
					"an estimate.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "How much can a built-out service truck actually still carry?",
						"type": "Single Choice",
						"explanation": (
							"Payload is GVWR minus the truck's curb weight: the occupants-and-cargo figure on "
							"the door-jamb placard. The crew counts against it, and so does every rack, toolbox "
							"and ladder added after the factory — so a built-out service truck has spent much of "
							"it before the first pump goes on."
						),
						"options": [
							{
								"text": "Its payload rating — GVWR minus curb weight — less the crew, the racks and everything already aboard",
								"is_correct": True,
							},
							{"text": "The GVWR printed on the door jamb", "is_correct": False},
							{"text": "The combined rating of the two axles", "is_correct": False},
							{
								"text": "Whatever fits in the bed without hanging over the tailgate",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A loaded trailer begins to sway at highway speed and the sway gets worse rather than damping out. What is the most likely loading error?",
						"type": "Single Choice",
						"explanation": (
							"Too little tongue weight — load piled behind the axle — lets the trailer sway, and "
							"the sway feeds itself and grows with speed. Too much tongue weight squats the tow "
							"vehicle and unloads the steer axle instead."
						),
						"options": [
							{
								"text": "Too much of the load is behind the trailer axle, leaving too little tongue weight",
								"is_correct": True,
							},
							{
								"text": "Too much weight on the tongue, pushing the nose down",
								"is_correct": False,
							},
							{
								"text": "The load is too heavy overall, regardless of where it sits",
								"is_correct": False,
							},
							{"text": "The load is too low in the trailer", "is_correct": False},
						],
					},
					{
						"question": "Which of these are true about carrying chemicals on a service vehicle?",
						"type": "Multiple Choice",
						"explanation": (
							"Containers ride upright, closed and restrained; acid and chlorine products are kept "
							"physically apart because mixing them makes chlorine gas; and nothing chemical travels "
							"in the passenger space, which is the one place on the vehicle you cannot leave."
						),
						"options": [
							{
								"text": "They ride upright, closed, and restrained so they stay upright",
								"is_correct": True,
							},
							{
								"text": "Acid and chlorine products are separated so they cannot reach each other in a spill",
								"is_correct": True,
							},
							{
								"text": "Nothing chemical goes in the passenger space, however short the trip",
								"is_correct": True,
							},
							{
								"text": "A sealed container may ride on the back seat if it is strapped down",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Jobsite hose and cord management",
			"estimated_minutes": 10,
			"summary": "The most common way somebody gets hurt on our sites, and how temporary power is run near water.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The likeliest injury on this job is a trip",
					"content": (
						"<p>Not the pump, not the chemical, not the trench. A hose or a cord across a "
						"walking surface is the hazard this trade creates more often than any other, "
						"and the person who goes down over it is usually not on the crew.</p>"
						"<p>That is the part worth sitting with. A fountain sits in a plaza, a lobby, a "
						"courtyard or a garden, and the public keeps using it while we work. A member "
						"of the public is not watching their feet, is not expecting a cord, may be a "
						"child running, and has no idea that the thing they just caught their toe on "
						"was ours. A technician trips and swears. A visitor trips and falls onto "
						"concrete or into water.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Route at the edges, cross at right angles, cover the crossing",
					"content": (
						"<p><b>Run along the edges.</b> Hug building lines, kerbs and planter edges "
						"rather than cutting diagonally across the open space, because a diagonal "
						"crosses every path anybody might take.</p>"
						"<p><b>Cross where you must, and cross square.</b> Pick the narrowest point, go "
						"straight across it, and keep the crossing short so there is less of it to trip "
						"over.</p>"
						"<p><b>Cover or ramp the crossing.</b> A cable ramp or a proper mat. Duct tape "
						"over a cord changes its colour and nothing else — the lump is still there, and "
						"now it is also stuck to the ground when somebody catches it. Better again, get "
						"it overhead where the route allows, high enough that nobody walks into it and "
						"supported so it cannot sag into the walkway later.</p>"
						"<p><b>Make the site read as a site.</b> Cones, tape or barrier around the work "
						"area tell people to go round. A run nobody expects is a hazard; a run inside a "
						"visibly closed area is just a run.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Temporary power, standing on wet ground",
					"content": (
						"<p>Everything about temporary power gets more serious when the site is a water "
						"feature. Wet skin and wet footing drop your body's resistance, so a fault that "
						"would be a jolt on a dry floor can be the one that stops your heart.</p>"
						"<p><b>Cords must be rated for outdoor and wet use</b> — a flexible, "
						"hard-service type with an intact jacket, not an indoor extension lead borrowed "
						"from a house. <b>Temporary power gets GFCI protection.</b> A Class A GFCI trips "
						"on roughly <b>4 to 6 mA</b> of ground-fault current, which is far below the "
						"current that injures a person and far above ordinary leakage. It is there to "
						"protect the human being, not the tool.</p>"
						"<p><b>Never run a cord through standing water, a puddle, or into a basin.</b> "
						"Connections belong up, dry, and out of the splash. And test the GFCI before you "
						"rely on it — a device that has failed looks exactly like one that works.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": 'What "damaged" means, and why tape is not a repair',
					"content": (
						"<p>A cord is damaged when the jacket is cut or split, when any conductor or "
						"insulation is visible, when the grounding pin is bent or missing, when the "
						"strain relief has pulled out of the plug so the wires take the tension, when it "
						"has been crushed by a vehicle, or when the plug or connector is scorched, "
						"melted or loose in the socket.</p>"
						"<p>Tape restores the appearance of a cord and none of its function. It does not "
						"reinstate the insulation rating, it does not restore the strain relief, and it "
						"hides a fault that will keep growing underneath it until somebody is holding a "
						"metal tool at the wrong moment.</p>"
						"<p><b>A damaged cord comes out of service.</b> Tag it so nobody picks it back "
						"up, and get it repaired by somebody qualified or discarded. Cutting the ground "
						"pin off to make a plug fit is the same decision as removing a guard from a "
						"saw.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before a temporary cord run is energised",
					"items": [
						"Every cord is rated for outdoor and wet use and the jacket is intact end to end",
						"Grounding pins are present and straight; plugs are tight in their sockets",
						"The supply has GFCI protection, and the GFCI was tested at this setup before anything was plugged into it",
						"No part of the run passes through a puddle, a basin or standing water",
						"Connections are up off the ground and out of the splash zone",
						"The route follows edges; any crossing is square, short, and covered or ramped",
						"Nothing is taped down as a substitute for a ramp",
						"The work area is coned or barriered so the public goes around it",
						"Damaged cords are tagged and off the site rather than in the pile",
					],
				},
				ask_block(
					"Who decides the barricading, the permit and the power source",
					"<p>How a public site is fenced or signed, whether the client requires a "
					"particular arrangement for their visitors, whether temporary power needs a "
					"permit, an inspection or a licensed electrician, and whether you may use a "
					"client's outlet at all are job, client and jurisdiction questions.</p>"
					"<p>They are answered by the project manager and the authority having "
					"jurisdiction before the cord comes off the truck, not by whoever is holding "
					"it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What does a Class A GFCI actually do?",
						"type": "Single Choice",
						"explanation": (
							"It detects a small imbalance between the conductors — roughly 4 to 6 mA leaking to "
							"ground — and opens. That threshold is chosen to protect a person, not the tool on the "
							"end of the cord. Overload is the "
							"breaker's job, and a GFCI does not prove anything about the equipment ground."
						),
						"options": [
							{
								"text": "It opens on a ground-fault current of roughly 4 to 6 mA, a level chosen to protect a person",
								"is_correct": True,
							},
							{
								"text": "It opens when the circuit is drawing more current than it is rated for",
								"is_correct": False,
							},
							{
								"text": "It confirms that the tool plugged into it is properly grounded",
								"is_correct": False,
							},
							{
								"text": "It protects the tool on the end of the cord from damage during a fault",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A cord in the truck has had its grounding pin snapped off. What do you do with it?",
						"type": "Single Choice",
						"explanation": (
							"A missing grounding pin is damage. It removes the fault path the tool relies on, and no "
							"amount of tape or careful use puts it back. Tag it and take it out of service."
						),
						"options": [
							{
								"text": "Tag it and take it out of service until it is repaired or discarded",
								"is_correct": True,
							},
							{
								"text": "Use it, since the GFCI will protect anybody who gets a shock",
								"is_correct": False,
							},
							{
								"text": "Tape over the end and keep it for double-insulated tools only",
								"is_correct": False,
							},
							{
								"text": "Keep it on the truck as a spare for non-critical work",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are correct ways to deal with a hose or cord that has to cross a path the public uses?",
						"type": "Multiple Choice",
						"explanation": (
							"Cross at the narrowest point, square to the path, and put a ramp or mat over it — or "
							"get the run overhead entirely. Tape changes the colour of a trip hazard and nothing "
							"else, and a diagonal run crosses every route somebody might take."
						),
						"options": [
							{"text": "Cross at right angles at the narrowest point", "is_correct": True},
							{
								"text": "Put a cable ramp or a proper mat over the crossing",
								"is_correct": True,
							},
							{
								"text": "Suspend the run overhead, high enough that nobody walks into it",
								"is_correct": True,
							},
							{
								"text": "Tape the cord flat to the paving along the route it already takes",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Tool usage and storage best practices",
			"estimated_minutes": 11,
			"summary": "Guards, sharpness, inspection, and putting a tool away in the state you would want to find it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The guard is part of the tool",
					"content": (
						"<p>A blade guard, a wheel guard, a chuck key retainer and a dead-man trigger "
						"are not accessories the manufacturer added to slow you down. They are the parts "
						"that stand between a moving edge and your hand, and they are the parts people "
						"take off because they are in the way. Being in the way is the mechanism.</p>"
						"<p>The guard on a circular saw retracts as the cut starts and springs back the "
						"instant the blade leaves the work; wedging it open means the blade is exposed "
						"while the saw is still spinning down in your hand. A grinder's guard is "
						"positioned between the wheel and the operator so that if the wheel comes apart "
						"the pieces go somewhere other than your face.</p>"
						"<p><b>A tool with a defeated guard does not go back in the truck.</b> It goes "
						"out of service with a tag on it, the same as a damaged cord.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A dull blade is more dangerous than a sharp one",
					"content": (
						"<p>This sounds backwards and it is not. A sharp edge cuts at the pressure it "
						"was designed for. A dull one does not, so you push harder — and now there is "
						"stored force in your arms, the tool is more likely to bind in the cut, and when "
						"it binds it releases all of that at once. <b>Kickback</b> is the tool being "
						"driven back along the line of the cut, and the reason it hurts people is that "
						"they were already leaning into it.</p>"
						"<p>The same goes for a worn bit that wanders instead of biting, a chipped "
						"masonry bit, a stretched chain and a rounded wrench. The tool skips, and your "
						"knuckles arrive at whatever it skipped off.</p>"
						"<p>Related, and equally boring to say: <b>use the tool for the job it is</b>. A "
						"chisel is not a pry bar, a wrench is not a hammer, a screwdriver is not a "
						"chisel, and a grinding wheel rated for grinding is not a cutting wheel. Each of "
						"those substitutions fails by breaking a piece off and throwing it.</p>"
						"<p>Keep your hands out of the line of the cut, and support the work so it "
						"cannot pinch the blade. Hold the workpiece with a clamp instead of a hand "
						"wherever that is possible at all.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Inspect before use, not after the incident",
					"content": (
						"<p>It is a thirty-second look and it is the only chance you get. <b>Cord and "
						"plug</b> — jacket intact, grounding pin present, strain relief holding. "
						"<b>Housing</b> — no cracks, no missing screws, nothing rattling. <b>Guard</b> — "
						"moves freely and returns on its own. <b>Trigger</b> — returns to off and the "
						"lock-on is not jammed. <b>Blade, bit or wheel</b> — sharp, sound, no cracks or "
						"chips, and correctly seated and tightened.</p>"
						"<p>On an abrasive wheel, the wheel's own rated speed must be <b>at or above</b> "
						"the tool's speed, never below, and a wheel that has been dropped is suspect "
						"whatever it looks like. Let it run up to speed clear of the work before it "
						"touches anything, and stand out of the plane of the wheel while it does.</p>"
						"<p><b>Battery packs.</b> A pack that is swollen, cracked, has been soaked, or "
						"is hot after a charge comes out of service — lithium cells fail energetically "
						"and they do not give much warning. Use the charger made for the pack, and do "
						"not leave packs cooking on a dashboard in the sun.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Storing a tool is handing it to the next person",
					"content": (
						"<p>Whoever opens that case next is reaching in without looking, probably in a "
						"hurry, possibly in the dark at the end of a long day. Everything about how you "
						"put a tool away is a message to them.</p>"
						"<p>Edges sheathed and blades retracted. Guards free rather than jammed shut by "
						"whatever was packed on top. Cords coiled loosely rather than wrapped tight "
						"around the tool, which is how the conductors break inside a jacket that still "
						"looks perfect. Batteries out of the heat and the wet. Tools dry, because rust "
						"seizes a guard as effectively as damage does. And everything restrained in the "
						"vehicle, because an unsecured tool is the projectile from the loading "
						"lesson.</p>"
						"<p><b>Anything out of service is tagged and physically separated.</b> A broken "
						"tool returned to the same shelf as the good ones will be used by somebody who "
						"trusted the shelf.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Terms worth having straight",
					"cards": [
						{
							"front": "Kickback",
							"back": "The tool or the workpiece being thrown back along the cut when the blade binds. Worst when you are pushing hard, which is why a dull blade causes it.",
						},
						{
							"front": "Pinch point",
							"back": "Anywhere two parts close on each other — a belt and pulley, a coupling, a clamp. It does not need to be fast to take a finger.",
						},
						{
							"front": "Out-of-service tag",
							"back": "A tag that says this tool is not to be used and who removed it from service. It goes on the tool, and the tool goes somewhere separate.",
						},
						{
							"front": "Double insulated",
							"back": "A tool built with two independent layers of insulation instead of an equipment ground, so it has a two-pin plug by design. Not the same as a tool whose ground pin someone cut off.",
						},
						{
							"front": "Rated wheel speed",
							"back": "The maximum speed marked on an abrasive wheel. It must be at or above the tool's speed — a wheel spun past its rating comes apart.",
						},
					],
				},
				ask_block(
					"Some tools need documented training before you touch them",
					"<p>Powder-actuated tools, chainsaws, aerial and scissor lifts, forklifts, "
					"trenching equipment and hot work all carry their own training, "
					"authorisation and in several cases certification requirements. Owning one and "
					"being permitted to operate one are different things.</p>"
					"<p>Which tools Sapphire authorises which people to use, and who holds the "
					"current cards, is a company record. If you are not sure you are on it for the "
					"tool in front of you, ask before you start rather than after.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a dull blade more dangerous than a sharp one?",
						"type": "Single Choice",
						"explanation": (
							"A dull edge does not cut at the pressure it was designed for, so the operator pushes "
							"harder. The tool is then more likely to bind, and when it binds it releases all of that "
							"force at once into an operator who was already leaning in."
						),
						"options": [
							{
								"text": "It makes you push harder, so the tool binds and kicks back into somebody already leaning into it",
								"is_correct": True,
							},
							{"text": "It heats the workpiece, which is the real hazard", "is_correct": False},
							{
								"text": "It is not — a sharp blade cuts deeper and is therefore worse",
								"is_correct": False,
							},
							{
								"text": "It wears the motor out, and a failing motor is the danger",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A crew member has wedged the lower guard open on a circular saw because it keeps catching on the work. What is the correct response?",
						"type": "Single Choice",
						"explanation": (
							"The guard returning is what covers the blade while the saw spins down in somebody's "
							"hand. A defeated guard puts the tool out of service; it is not a technique and it is not "
							"a preference."
						),
						"options": [
							{
								"text": "Take the saw out of service and tag it until the guard works as designed",
								"is_correct": True,
							},
							{
								"text": "Leave it, as long as the person using it knows it is wedged",
								"is_correct": False,
							},
							{
								"text": "Leave it for this cut only and unwedge it afterwards",
								"is_correct": False,
							},
							{
								"text": "Swap to a different saw and leave that one in the truck for whoever needs it next",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these put a cordless tool or its battery out of service?",
						"type": "Multiple Choice",
						"explanation": (
							"A swollen or cracked pack, a soaked pack, and a cracked housing are all failures that "
							"get worse rather than better — lithium cells in particular fail energetically with "
							"little warning. A pack that is simply flat is just flat."
						),
						"options": [
							{"text": "A battery pack that is swollen or cracked", "is_correct": True},
							{"text": "A pack that has been soaked or submerged", "is_correct": True},
							{
								"text": "A cracked tool housing or a trigger that does not return to off",
								"is_correct": True,
							},
							{"text": "A pack that has run flat during the job", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Confined space entry and lock-out/tag-out",
			"estimated_minutes": 20,
			"summary": "Why Sapphire classes a below-ground vault as permit-required, the four readings taken before the hatch opens, why rescuers die, and the lock-out sequence that ends in proof.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "What makes a space permit-required",
					"content": (
						"<p>A <b>confined space</b> is one that is large enough to get into and work in, "
						"has <b>limited or restricted means of entry or exit</b>, and is <b>not designed "
						"for continuous occupancy</b>. A valve vault, a sump, a filter pit, a wet well, a "
						"drained basin with one ladder, a surge tank — this trade is full of them.</p>"
						"<p>It becomes <b>permit-required</b> when it is a confined space and it also "
						"has one or more of these: a hazardous atmosphere, or the potential for one; "
						"material that could engulf somebody; an internal shape that could trap or "
						"asphyxiate — converging walls, a floor sloping to a smaller cross-section; or "
						"any other recognised serious safety or health hazard.</p>"
						"<p>Notice what that list is made of. Most of it is about what the space "
						"<i>could</i> do, not what it is doing while you look at it. A vault that is "
						"perfectly pleasant on a breezy morning is the same vault that fills with vapour "
						"from a solvent weld, or has its oxygen consumed by rust, or takes water from a "
						"line somebody opens elsewhere on the site.</p>"
						"<p>Which is why the determination is made <b>in writing, by the employer, "
						"before anybody goes near it</b>. It is not a judgement a technician makes at "
						"the lip of the hole.</p>"
						"<p><b>Sapphire has already made that determination for the commonest case.</b> "
						"Its own training document states that many commercial fountain mechanical "
						"systems are located in below-ground concrete vaults, and that <b>these areas "
						"are classified as permit-required confined spaces</b> — on the grounds of "
						"toxic gas accumulation and oxygen depletion. So on our work a subterranean "
						"equipment vault is not a space you assess and form a view about. It starts "
						"permit-required, and it stays that way unless somebody with the authority to "
						"reclassify it does so in writing.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Test before entry, monitor during, and in that order",
					"content": (
						"<p>Sapphire's document puts this in one line: <b>never drop into a vault "
						"without testing the air first.</b> <b>Lower a calibrated four-gas "
						"atmospheric monitor down into the vault before cracking the hatch "
						"completely</b> — the instrument goes in ahead of you, through a hatch that "
						"is not yet fully open. The reading you want is of the air as it has been "
						"sitting, not of the air after you have stood over an open hatch stirring it "
						"about with your own head in the worst place to have it.</p>"
						"<p>The atmosphere is tested <b>before</b> anybody enters and monitored "
						"<b>while</b> they are in there, because the space changes — the work itself "
						"changes it. Grinding, hot work, solvent cement, a running engine outside the "
						"hatch and a purge that stops all move the numbers while you are down there.</p>"
						"<p>The order is <b>oxygen first, then flammable, then toxic</b>, and the order "
						"is not arbitrary. Oxygen comes first because it decides whether anybody can be "
						"in there at all, and because the combustible sensor in most meters needs oxygen "
						"to work — in an oxygen-deficient atmosphere it reads <b>low</b>, which is the "
						"worst possible direction for an instrument to be wrong. Sapphire's document "
						"lists the four gases in a different order from this on its page, but that is "
						"the order of a table rather than the order of a test; oxygen is read first "
						"either way.</p>"
						"<p><b>Sapphire's safe operational range for oxygen is 19.5% to 23.5%.</b> "
						"Below that range you are in an oxygen-deficient atmosphere; above it, the "
						"space is oxygen-enriched, which is not a bonus — enriched oxygen makes "
						"materials that normally smoulder burn fiercely, clothing included.</p>"
						"<p>Test at the <b>top, the middle and the bottom</b> of the space, lowering the "
						"probe slowly. Gases stratify: solvent vapour and many fuel vapours are heavier "
						"than air and sit in the bottom where you will be kneeling, while others "
						"accumulate at the ceiling.</p>"
						"<p>And the meter is an instrument, not an oracle. Bump test and calibrate it on "
						"the schedule the manufacturer sets. <b>An uncalibrated meter does not read "
						"nothing — it reads a plausible number</b>, and a plausible number is exactly "
						"what talks somebody into the hole.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The four gases, and the numbers Sapphire sets for them",
					"content": (
						"<p>Four-gas means four metrics, and Sapphire's document gives a safe "
						"operational range for each. The meter must <b>actively verify</b> all four "
						"before anybody goes in — a meter that has not been looked at is not a "
						"test.</p>"
						"<ul>"
						"<li><b>Oxygen (O₂) — between 19.5% and 23.5%.</b> The one that decides "
						"whether anybody can be in there at all.</li>"
						"<li><b>Hydrogen sulfide (H₂S) — 0 ppm.</b> Not 'low'. Zero. It is the gas "
						"of stagnant organic decomposition, which is an exact description of the "
						"bottom of a fountain vault that has been shut since autumn. It smells of "
						"rotten eggs at small concentrations and then <b>deadens your sense of smell "
						"at larger ones</b>, so the smell going away is the wrong kind of good "
						"news.</li>"
						"<li><b>Carbon monoxide (CO) — below 25 ppm.</b> Colourless, odourless, and "
						"produced by anything burning: a generator, a compressor, a pressure washer "
						"or a vehicle idling near the hatch you are about to open.</li>"
						"<li><b>Lower explosive limit (LEL) — 0%.</b> Combustible methane or a fuel "
						"leak. The LEL reading is a percentage of the concentration at which the "
						"atmosphere will burn, so a small number here is not a small problem.</li>"
						"</ul>"
						"<p><b>If the monitor alarms, the vault gets purged, not entered.</b> Deploy "
						"an industrial <b>explosion-proof</b> ventilation blower — explosion-proof "
						"because the thing you are blowing out may be flammable and a blower motor "
						"makes sparks — and purge the space with <b>fresh outdoor air for a minimum "
						"of 15 minutes</b> before re-testing. Fresh <i>outdoor</i> air: a blower "
						"drawing from beside a running generator feeds the vault the carbon monoxide "
						"you are trying to clear out of it.</p>"
						"<p>Then test again. A purge that ran is not a purge that worked, and the "
						"only thing that says it worked is the meter.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before anybody goes into a vault",
					"items": [
						"The space has been evaluated and the permit is in front of you, filled in and authorised",
						"The four-gas monitor is in calibration and has been bump tested",
						"The monitor went down into the vault before the hatch was cracked completely",
						"Oxygen reads between 19.5% and 23.5%",
						"Hydrogen sulfide reads 0 ppm",
						"Carbon monoxide reads below 25 ppm",
						"LEL reads 0%",
						"If anything alarmed: an explosion-proof blower purged the space with fresh outdoor air for at least 15 minutes, and it was re-tested afterwards",
						"Readings were taken at the top, the middle and the bottom, not just at the hatch",
						"An attendant is outside, knows who is in, and is staying",
						"The rescue arrangements are in place now, not being worked out now",
						"Monitoring continues the whole time anybody is inside",
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A large share of the people who die in confined spaces went in to help",
					"content": (
						"<p>This is the single most important paragraph in this module. When somebody "
						"collapses inside a space, the instinct to go in after them is overwhelming, "
						"immediate, and it is what kills the second person and often the third.</p>"
						"<p>Understand why. The thing that dropped your colleague is still in there, it "
						"is invisible, and in an oxygen-deficient or toxic atmosphere it acts in "
						"<b>seconds</b> — not long enough to grab somebody and get back out, and holding "
						"your breath does not buy the time people imagine it does. Rescuers are found "
						"inside, next to the person they went in for.</p>"
						"<p><b>If somebody collapses inside a space: do not enter. Call for rescue, get "
						"everyone else back, and do what you can from outside.</b> That is not "
						"cowardice and it is not a technicality. It is the difference between one "
						"casualty and three, and it is why the attendant's job is defined the way it "
						"is.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Who does what on an entry",
					"panels": [
						{
							"title": "The entrant",
							"body": "<p>The person going in. They know the hazards, use the equipment the permit specifies, stay in contact with the attendant, and <b>evacuate immediately</b> when told to, when the monitor alarms, or when anything feels wrong — without waiting to be convinced.</p>",
						},
						{
							"title": "The attendant",
							"body": "<p>Stays outside for the whole entry. Knows who is in there and how many. Maintains contact, watches conditions inside and outside the space, and orders evacuation. <b>The attendant does not leave, and the attendant does not enter</b> — that is the whole point of the role, and it is the rule that breaks first under stress.</p>",
						},
						{
							"title": "The entry supervisor",
							"body": "<p>Authorises the entry, verifies that the permit's conditions are actually in place before it starts, and terminates the entry when the work is done or the conditions change. A signature here is a statement that somebody checked.</p>",
						},
						{
							"title": "The rescue arrangements",
							"body": "<p>Decided <b>before</b> the entry, not during. Retrieval equipment — harness and line to a mechanical device — where its use is feasible, and rescue services that have been contacted and are able to respond. <i>Calling for an ambulance at the moment somebody collapses is not a rescue plan.</i></p>",
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Lock-out/tag-out: Sapphire's sequence, and it ends in proof",
					"content": (
						"<p>Lock-out/tag-out exists because equipment that is switched off is not "
						"equipment that is safe. Someone else flips a breaker, a timer fires, a level "
						"switch calls for a pump, a control system resumes after a fault. The lock is "
						"the physical statement that none of those can happen.</p>"
						"<p>Sapphire's document names the trigger plainly: <b>before replacing a pump "
						"motor, clearing a jammed valve actuator, or replacing a broken Splash Wizard "
						"relay, you isolate the power source.</b> These are its steps.</p>"
						"<ol>"
						"<li>Turn the equipment switch <b>off at the local panel</b>.</li>"
						"<li>Locate the <b>main circuit breaker upstream</b>.</li>"
						"<li>Flip that breaker to the <b>OFF</b> position.</li>"
						"<li>Snap a physical <b>LOTO scissor-hasp</b> over the breaker switch "
						"toggle.</li>"
						"<li>Affix <b>your personal padlock</b> to the hasp, alongside a signed, dated "
						'warning tag reading <b>"DANGER: DO NOT OPERATE."</b></li>'
						"<li><b>Verify.</b> Attempt to turn the breaker back on — then take your "
						"multi-meter and prove the target terminal block reads <b>0 volts</b>.</li>"
						"</ol>"
						"<p>And the rule that runs through all six: <b>the key stays in your pocket "
						"until the repair is fully finished.</b> A lock whose key is hanging on the "
						"panel, or sitting in the van, or in somebody else's hand, is a sign rather "
						"than a lock.</p>"
						"<p>Notice where those steps end. They do not end at the lock — they end at a "
						"<b>measurement</b>. A breaker labelled off, a hasp, a padlock and a tag are "
						"four things that all look like proof and none of which are; the meter on the "
						"terminal block is the only step in the list that tells you something you did "
						"not already believe.</p>"
						"<p>Four things the six steps assume rather than state, and all four matter "
						"on a water feature:</p>"
						"<ul>"
						"<li><b>Tell everybody affected</b> before it goes down, and shut down by the "
						"normal means first — which is what step 1 is doing.</li>"
						"<li><b>Electricity is not the only energy source.</b> The valves, the air "
						"supply and the chemical feed isolate too, each isolating device carries its "
						"own lock, and stored energy is released or restrained before work starts. "
						"The next callout is the list of what a fountain is still holding after the "
						"breaker is open.</li>"
						"<li><b>Each person working on the equipment applies their own lock.</b> That "
						"is what a scissor-hasp is for — it takes several padlocks, and it cannot be "
						"removed until the last one comes off. A group lock box does the same job for "
						"a bigger crew.</li>"
						"<li><b>Step 6 proves one point, once.</b> Whatever you moved to test it goes "
						"back to off — the breaker you just tried, and the local switch from step 1 — "
						"or the equipment starts itself the moment somebody restores power. Prove "
						"<b>every</b> conductor you are going to touch rather than one of three. And "
						"prove the meter itself, on a known live source, before and after: a meter "
						"with a blown fuse reads 0 volts on a live terminal block. That last one is "
						"the next lesson, and it is the reason this one ends at a measurement.</li>"
						"</ul>"
						"<p>And <b>only the person who applied a lock removes it</b>. If somebody has "
						"gone home with their lock on, there is a written procedure for that case and "
						"it belongs to the employer; cutting a colleague's lock off because the job is "
						"waiting is how somebody who is still inside the equipment gets started "
						"up.</p>"
						"<p>What you follow on the day is the <b>equipment-specific</b> procedure "
						"written for that pump, that panel, that feature, which names the actual "
						"isolating devices and the actual stored energy. The six steps above are the "
						"shape — here so you can tell whether the one in front of you is complete, and "
						"notice when there isn't one.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "What counts as stored energy in a fountain",
					"content": (
						"<p>The disconnect handles electricity arriving. It does nothing at all about "
						"energy already in the system, and a water feature is full of it.</p>"
						"<p><b>Water head and gravity drain-back.</b> Water standing in a riser, a "
						"basin, or a header above the equipment will come back through whatever you open "
						"— and a basin above a vault can empty into that vault with you in it. <b>Air "
						"pressure</b> in a bladder tank, an air-blown display, or a pneumatic actuator. "
						"<b>Chemical under pressure</b> in a feed line downstream of an injector. "
						"<b>Springs</b> in check valves, actuators and closers. <b>Capacitors, and the "
						"DC bus in a VFD, which stays charged after the disconnect is open</b> — the "
						"drive's own instructions give the wait time before it is safe to open, and that "
						"figure belongs to the drive in front of you rather than to this course.</p>"
						"<p>And the one that catches people who are only reaching in for a second: a "
						"<b>float switch or a timer can start a pump with nobody near the panel.</b> "
						"Nothing about that requires a person to make a mistake.</p>"
					),
				},
				ask_block(
					"The written program is the operative document, and this lesson is not it",
					"<p>Permit-required confined space entry and lock-out/tag-out each require a "
					"<b>written program</b> from the employer: the permit form, the hazard "
					"determination for each space, who is trained and authorised as entrant, "
					"attendant and entry supervisor, the atmospheric testing and monitoring "
					"arrangements, the equipment-specific lock-out procedures, and the rescue "
					"arrangements including how somebody is actually retrieved.</p>"
					"<p>This lesson tells you what those documents are for and what the roles mean, "
					"and it now carries Sapphire's own figures for the pre-entry gas test and its own "
					"lock-out sequence. <b>It still does not train you, and it does not authorise you "
					"to enter anything.</b> If Sapphire's program and permit are not in front of you "
					"and the space has not been evaluated, the correct move is to stop and ask — not "
					"to look in and form an opinion.</p>"
					"<p>Sapphire's document is explicit that these two are <b>demonstrated, not "
					"read</b>: a technician executes a true pre-entry confined space gas test, "
					"reading out safe oxygen, LEL and toxic gas baselines, and deploys a multi-lock "
					"scissor hasp, padlock and danger tag to isolate an electric motor panel, in "
					"front of a Lead Installer. Having read the steps here is not that, and it is not "
					"a substitute for it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "You arrive at a valve vault and find a colleague collapsed at the bottom, not moving. What do you do?",
						"type": "Single Choice",
						"explanation": (
							"Whatever dropped them is still in there and it acts in seconds. A large share of the "
							"people who die in confined spaces are would-be rescuers found beside the person they "
							"went in for. Call for rescue, keep everyone out, and help from outside. Purge and "
							"re-test is the sequence for making an entry — it is not a rescue, and a collapse is "
							"not the moment to start one."
						),
						"options": [
							{
								"text": "Do not enter. Call for rescue, keep everybody else out, and do what you can from outside",
								"is_correct": True,
							},
							{
								"text": "Go in immediately holding your breath and drag them to the ladder",
								"is_correct": False,
							},
							{
								"text": "Go in with a second person on the surface holding a rope",
								"is_correct": False,
							},
							{
								"text": "Purge with the blower, re-test the atmosphere, and then go in and get them out",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Sapphire's document sets out the pre-entry test for a below-ground vault. Which of these are part of it?",
						"type": "Multiple Choice",
						"explanation": (
							"The calibrated four-gas monitor goes down into the vault before the hatch is cracked "
							"completely; the safe ranges are oxygen 19.5% to 23.5%, hydrogen sulfide 0 ppm, carbon "
							"monoxide below 25 ppm and LEL 0%; and an alarm means an explosion-proof blower purges "
							"the space with fresh outdoor air for at least 15 minutes before re-testing. A reading "
							"taken at an open hatch is a reading of the air you are standing in."
						),
						"options": [
							{
								"text": "The monitor goes down into the vault before the hatch is cracked completely",
								"is_correct": True,
							},
							{
								"text": "Oxygen 19.5% to 23.5%, hydrogen sulfide 0 ppm, carbon monoxide below 25 ppm, LEL 0%",
								"is_correct": True,
							},
							{
								"text": "On an alarm, an explosion-proof blower purges with fresh outdoor air for at least 15 minutes, then it is re-tested",
								"is_correct": True,
							},
							{
								"text": "A reading taken at the open hatch is enough, because the vault air mixes with it",
								"is_correct": False,
							},
							{
								"text": "Carbon monoxide below 100 ppm is acceptable as long as the entry is short",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is oxygen tested before the flammable reading?",
						"type": "Single Choice",
						"explanation": (
							"Oxygen decides whether anyone can be in the space at all, and the combustible sensor in "
							"most meters needs oxygen to work — in an oxygen-deficient atmosphere it reads low, which "
							"is the worst direction for that instrument to be wrong."
						),
						"options": [
							{
								"text": "Oxygen decides whether entry is possible at all, and a low-oxygen atmosphere makes the flammable sensor read falsely low",
								"is_correct": True,
							},
							{
								"text": "The oxygen sensor warms up faster than the others",
								"is_correct": False,
							},
							{
								"text": "Flammable gas cannot be present unless oxygen is already normal",
								"is_correct": False,
							},
							{
								"text": "It is a convention with no technical reason behind it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of lock-out/tag-out?",
						"type": "Multiple Choice",
						"explanation": (
							"Each person applies their own lock — which is what a scissor-hasp is for — and only "
							"that person removes it, with the key in their own pocket until the repair is finished. "
							"Sapphire's sequence ends in a measurement: attempt to turn the breaker back on, then "
							"prove 0 volts at the terminal block with a multi-meter. Stored energy is released or "
							"restrained first. A tag records who and why; the lock is what stops it."
						),
						"options": [
							{
								"text": "Each person working on the equipment applies their own lock and tag, which is what a scissor-hasp is for",
								"is_correct": True,
							},
							{
								"text": "Only the person who applied a lock removes it, and the key stays in their pocket until the repair is finished",
								"is_correct": True,
							},
							{
								"text": "Verification means attempting to turn the breaker back on, then proving 0 volts at the terminal block with a meter",
								"is_correct": True,
							},
							{
								"text": "Stored energy — water head, air pressure, springs, capacitors — is released or restrained before work starts",
								"is_correct": True,
							},
							{
								"text": "A tag on its own is as good as a lock when the crew is small and everybody knows",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Electrical safety",
			"estimated_minutes": 14,
			"summary": "Why water changes the numbers, what 'qualified' actually means, and proving a conductor is dead yourself.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Water removes the margin you did not know you were relying on",
					"content": (
						"<p>What injures a person is <b>current through the body</b>, and how much "
						"current flows depends on the voltage and on your resistance. Dry skin is a "
						"reasonable insulator. Wet skin is not, and a person standing in a basin in wet "
						"boots with wet hands has very little resistance left and an excellent path to "
						"earth through both feet.</p>"
						"<p>So the same contact that would be an unpleasant jolt on a dry shop floor can "
						"be the one that stops a heart on a fountain deck. <b>Low voltage is not the "
						"same thing as safe voltage</b>, and a circuit does not become harmless because "
						"the number on the label is small.</p>"
						"<p>The path matters as much as the current. Hand to hand, or hand to feet, "
						"takes the current across the chest. That is the reason for the old habit of "
						"keeping one hand in a pocket around energised equipment, and the better reason "
						"for not being near energised equipment at all.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": '"Qualified" is a defined word, not a compliment',
					"content": (
						"<p>A <b>qualified person</b> has been trained on the construction and operation "
						"of the specific equipment, has been trained to recognise and avoid the "
						"electrical hazards involved, and is <b>authorised</b> to do the work. All three "
						'parts. It is not "has done it before", it is not "is comfortable around '
						'panels", and it is not "is the most senior person on site today".</p>'
						"<p>The default for any electrical work is <b>de-energise, lock out, and "
						"verify</b>. Working on something while it is live is exceptional: it has to be "
						"justified — genuinely infeasible to de-energise, or de-energising creates a "
						"greater hazard — and it runs under a written procedure with specific protective "
						'equipment. "It is quicker" is not a justification, and neither is "the client '
						'is watching".</p>'
						"<p>If you are not qualified for the task in front of you, the correct action is "
						"to leave the cover on. That is a complete and professional answer.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Live, dead, live — and the reason for the third step",
					"content": (
						"<p><b>Treat every conductor as energised until you have proven otherwise "
						"yourself.</b> Not until somebody told you, not because a breaker is labelled "
						"off, not because a lamp is out, not because the equipment is not running.</p>"
						"<p>The proof has three steps and the order matters. <b>Test your meter on a "
						"known live source.</b> <b>Test the conductor.</b> <b>Test your meter on the "
						"known live source again.</b></p>"
						"<p>That last step is the one people skip, and it is the one that catches the "
						"failure nobody sees. A meter with a blown fuse, a broken lead or a dead battery "
						"does not announce itself — it reads <b>zero</b>, on everything, forever, and "
						"zero is exactly what a safely dead conductor reads. Re-testing on the known "
						"source is how you tell a dead circuit from a dead instrument.</p>"
						"<p>This is where Sapphire's lock-out sequence ends, and it ends here on "
						"purpose. Having flipped the breaker, hasped it, padlocked it, tagged it and "
						"tried to turn it back on, the last step is to <b>prove the target terminal "
						"block reads 0 volts with your multi-meter</b>. Live-dead-live is how you earn "
						"the right to believe that reading.</p>"
						"<p>And a circuit can be fed from somewhere you did not look: a generator, a "
						"transfer switch, a control transformer, a UPS, a second panel, or a back-fed "
						"circuit somebody added years ago. One open breaker proves one open "
						"breaker.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Two hazards, two boundaries",
					"content": (
						"<p>Electricity has a second way of hurting you that has nothing to do with "
						"touching anything. An <b>arc flash</b> is a fault that turns into an arc "
						"through the air: an intense burst of heat, light and pressure that can ignite "
						"clothing and throw a person, and it does not require contact.</p>"
						"<p>So there are two families of boundary around energised equipment. The "
						"<b>shock approach boundaries</b> limit how close an unqualified or qualified "
						"person may get to exposed energised parts. The <b>arc flash boundary</b> is the "
						"distance within which someone would receive a serious burn if a fault happened "
						"while they were standing there.</p>"
						"<p>Where those distances have been worked out — by an arc-flash study — they "
						"go on a label on the equipment itself. Which equipment is required to carry "
						"which label is its own question, and the NEC, NFPA 70E and what a particular "
						"jurisdiction enforces do not all answer it the same way. <b>Where there is a "
						"label, that label is the instruction for that piece of equipment</b>, and no "
						"general figure printed in a training course could replace it. If a panel carries "
						"no label, that is a finding to report rather than a reason to guess at a "
						"distance.</p>"
						"<p>The cheap habit that costs nothing: when operating a disconnect or a "
						"breaker, stand to the side of the door rather than square in front of it, and "
						"turn your face away.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Bonding around water is a code matter, and it fails silently",
					"content": (
						"<p><b>NEC Article 680</b> covers electrical installations in and around pools, "
						"spas, fountains and other bodies of water: what may be installed where, "
						"clearances, junction boxes and luminaires, GFCI requirements, grounding, and "
						"<b>equipotential bonding</b>.</p>"
						"<p>Equipotential bonding is worth understanding rather than memorising, because "
						"people confuse it with grounding and they do different jobs. Grounding gives "
						"fault current a path back so a protective device operates. <b>Bonding ties "
						"everything a person can touch together so that it is all at the same "
						"potential.</b> A shock needs a <i>difference</i> in potential across the body; "
						"if the water, the shell, the handrail, the ladder, the deck reinforcement and "
						"the equipment are all bonded into one mass, there is no difference for a "
						"current to follow.</p>"
						"<p>And here is the reason it belongs in a safety course rather than a code "
						"class: <b>a missing or broken bond passes every check anybody thinks to "
						"make.</b> The lights work, the pumps run, the GFCI tests fine, the client sees "
						"a working fountain. The only symptom is a tingle somebody mentions and nobody "
						"chases — until the conditions line up and it stops being a tingle.</p>"
					),
				},
				ask_block(
					"Who is allowed to do electrical work here",
					"<p>Which electrical tasks a technician may perform, which require a licensed "
					"electrician, which require a permit and an inspection, and whether Sapphire's "
					"program permits any energised work at all are decisions made by the employer, "
					"the licensing rules of the state, and the authority having jurisdiction.</p>"
					"<p>The same goes for arc-flash study results, the protective equipment they "
					"call for, and who is designated qualified for which equipment. If you cannot "
					"point at the document that says you may, you may not.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "After testing a conductor and reading zero, why do you go back and test your meter on a known live source again?",
						"type": "Single Choice",
						"explanation": (
							"A meter with a blown fuse, a broken lead or a flat battery reads zero on everything — "
							"the same reading a safely dead conductor gives. Re-testing on a known source is how you "
							"tell a dead circuit from a dead instrument."
						),
						"options": [
							{
								"text": "Because a failed meter reads zero on everything, which looks identical to a dead circuit",
								"is_correct": True,
							},
							{"text": "To discharge any static built up in the leads", "is_correct": False},
							{
								"text": "To confirm the breaker did not close again while you were testing",
								"is_correct": False,
							},
							{
								"text": "It is a paperwork requirement rather than a technical one",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What is equipotential bonding around a fountain for?",
						"type": "Single Choice",
						"explanation": (
							"Bonding ties everything a person can touch to the same potential, so there is no voltage "
							"difference across the body. Providing a fault path so a device operates is grounding — a "
							"different job, done by a different conductor."
						),
						"options": [
							{
								"text": "To hold everything a person can touch at the same potential, so no voltage appears across their body",
								"is_correct": True,
							},
							{
								"text": "To give fault current a path back to the panel so the breaker trips",
								"is_correct": False,
							},
							{
								"text": "To remove the need for GFCI protection on the circuits near the water",
								"is_correct": False,
							},
							{
								"text": "To drain static charge out of the circulating water",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A circuit has been switched off at its panel, so it cannot be energised from anywhere else and testing it is a formality.",
						"type": "True-False",
						"explanation": (
							"False. Generators, transfer switches, control transformers, a UPS, a second panel or an "
							"old back-fed circuit can all energise conductors downstream of an open breaker. One open "
							"breaker proves one open breaker."
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
			"lesson_title": "Basic OSHA and ISPSC safety codes",
			"estimated_minutes": 12,
			"summary": "Which document governs which question, and why 'the code says' is only ever half a sentence.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "OSHA sets the floor",
					"content": (
						"<p>OSHA is federal law about worker safety. Two things come out of it. There "
						"are <b>specific standards</b> — the construction standards and the general "
						"industry standards, which is where confined space, lock-out/tag-out, fall "
						"protection, excavation, hazard communication and personal protective equipment "
						"all live. And there is the <b>General Duty Clause</b>, which requires an "
						"employer to provide a workplace free of recognised hazards likely to cause "
						"death or serious harm even where no specific standard covers the situation.</p>"
						"<p>The word to hold onto is <b>minimum</b>. An OSHA standard is a floor, never "
						"a ceiling. A manufacturer's instruction, a project specification, a state plan "
						"with stricter rules of its own, or Sapphire's written program can all require "
						"more — and where two requirements differ, <b>the stricter one governs</b>. "
						'"OSHA does not require it" is not an argument against doing it.</p>'
						"<p>It also gives you rights that are worth knowing you have: to see the safety "
						"data sheets and the written programs for the hazards you work around, to be "
						"trained in a language you understand, and to raise a hazard or make a complaint "
						"<b>without retaliation</b>. The right to refuse a task outright is narrower than "
						"people assume — it turns on a danger serious enough that there is no time to get "
						"it put right the ordinary way — so the move that is always available and always "
						"protected is to say it, at the time, to a supervisor.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The ISPSC is a model code, and that changes what it means",
					"content": (
						"<p>The <b>International Swimming Pool and Spa Code</b> is a model code from the "
						"International Code Council. It covers pools, spas and aquatic vessels — the ones "
						"people are meant to get into — and its subjects are barriers and access, suction "
						"entrapment protection, circulation and filtration, water quality, and how these "
						"things are built and maintained. Whether it reaches a given decorative fountain "
						"depends on how the jurisdiction classified that feature and on what it amended "
						"in.</p>"
						"<p>A <b>model</b> code has no legal force anywhere by itself. It is a document "
						"offered for adoption. A state or a local jurisdiction adopts it — <b>a "
						"particular edition of it</b> — and very commonly amends it on the way in, "
						"striking sections and adding their own.</p>"
						'<p>So "the ISPSC says" is half a sentence. The other half is <i>which edition, '
						"adopted where, amended how</i>. Two towns an hour apart can be on different "
						"editions with different amendments, and the answer that was right on last "
						"month's job is not automatically right on this one. The same is true of the "
						"NEC, the plumbing code and the building code.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The authority having jurisdiction is who actually decides",
					"content": (
						"<p>The <b>AHJ</b> — usually a building department, sometimes a health "
						"department or a state agency — adopts the code, interprets it, issues the "
						"permit and inspects the work. <b>Their interpretation is the operative one on "
						"this job</b>, including where you believe the model text reads differently. "
						"Arguing the printed code at an inspector is not a strategy.</p>"
						"<p>One item from these codes is worth naming because it is a life-safety "
						"matter rather than a paperwork one: <b>suction entrapment</b>. A single "
						"suction outlet that a body can seal against can hold a person under water with "
						"a force nobody can pull against, and it has killed children. Outlet covers "
						"are certified, they have an expiry, and the arrangement of outlets and the "
						"provisions against entrapment are prescribed. A missing, broken or "
						"uncertified cover on a suction outlet is reported the moment you see it, and the "
						"feature does not run until it has been put right. It is not a punch-list "
						"item.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Which document answers which question",
					"panels": [
						{
							"title": "OSHA standards",
							"body": "<p>Worker safety. What the employer must provide, assess, train and document — confined space, lock-out/tag-out, excavation, fall protection, hazard communication, protective equipment. Answers <i>how do the people doing this work stay safe</i>.</p>",
						},
						{
							"title": "The ISPSC, as adopted and amended locally",
							"body": "<p>How the vessel itself is built, protected and operated: barriers, entrapment protection, circulation, water quality. Answers <i>is this feature legal where it stands</i>. Always check which edition the jurisdiction adopted.</p>",
						},
						{
							"title": "The NEC, as adopted — Article 680 near water",
							"body": "<p>Electrical installation in and around water: bonding, grounding, GFCI, luminaires, junction boxes, clearances. Answers <i>is the electrical work legal and safe here</i>.</p>",
						},
						{
							"title": "Manufacturer instructions and listings",
							"body": "<p>How this specific product may be installed and used. Not merely advice — a code generally requires listed equipment to be installed according to its listing, so the instruction sheet is enforceable. It is also where every number this course refuses to print actually lives.</p>",
						},
						{
							"title": "The project specification and drawings",
							"body": "<p>What this client bought and what the engineer of record required. Frequently stricter than code, and where it is, it wins.</p>",
						},
						{
							"title": "Sapphire's own written programs",
							"body": "<p>How it is done here, wherever a program exists: the confined space program and permit, the lock-out procedures, the hazard assessment and the protective equipment it selects, the injury reporting route. Where one of these exists it is the operative document on our own crews, and it can only be stricter than the law, never looser. Where you cannot find one for the task in front of you, that absence is itself the thing to raise before you start.</p>",
						},
					],
				},
				ask_block(
					"Which edition, adopted where, and is this feature even a 'pool'",
					"<p>Whether a given water feature is regulated as a pool, a spa, or a decorative "
					"feature changes which chapters apply to it — and with them whether barriers, "
					"entrapment provisions and water-quality requirements are mandatory. "
					"Jurisdictions differ on that classification, and so do the amendments they "
					"make.</p>"
					"<p>That question is settled by reading the jurisdiction's adopted code and by "
					"asking the AHJ, and it is owned by the project manager and the engineer before "
					"the work is priced. If you are on site and something seems to be missing a "
					"barrier, a cover or a permit, report it — do not resolve it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What does it mean that the ISPSC is a model code?",
						"type": "Single Choice",
						"explanation": (
							"A model code has no force until a jurisdiction adopts it, and jurisdictions adopt a "
							"specific edition and commonly amend it. So the requirement depends on which edition was "
							"adopted where, and how it was amended."
						),
						"options": [
							{
								"text": "It has no legal force until a jurisdiction adopts it, by edition, usually with local amendments",
								"is_correct": True,
							},
							{
								"text": "It applies nationally, and local rules may only add to it",
								"is_correct": False,
							},
							{
								"text": "It is guidance that nobody is ever required to follow",
								"is_correct": False,
							},
							{
								"text": "It applies only to competition pools and not to decorative features",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A project specification requires protective equipment for a task where the OSHA standard would not. Which applies?",
						"type": "Single Choice",
						"explanation": (
							"An OSHA standard is a minimum, not a ceiling. Where a specification, a manufacturer's "
							"instruction, a state plan or the employer's own program is stricter, the stricter "
							"requirement governs."
						),
						"options": [
							{
								"text": "The specification — the stricter requirement governs, because the standard is a floor",
								"is_correct": True,
							},
							{
								"text": "The OSHA standard, because federal law overrides a private contract",
								"is_correct": False,
							},
							{
								"text": "Whichever the crew judges more practical on the day",
								"is_correct": False,
							},
							{"text": "Neither, until the AHJ rules on the conflict", "is_correct": False},
						],
					},
					{
						"question": "Which of these are true of the authority having jurisdiction?",
						"type": "Multiple Choice",
						"explanation": (
							"The AHJ adopts and interprets the code for its area, issues permits and inspects, and "
							"can genuinely reach a different conclusion from the next jurisdiction. It does not write "
							"the model code — that is the code council that publishes it."
						),
						"options": [
							{
								"text": "Its interpretation of the adopted code is the operative one on the job",
								"is_correct": True,
							},
							{"text": "It issues the permit and inspects the work", "is_correct": True},
							{
								"text": "It can differ from the jurisdiction an hour down the road",
								"is_correct": True,
							},
							{
								"text": "It writes the model code that everybody else adopts",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Basic first aid",
			"estimated_minutes": 14,
			"summary": "Recognise it, call early, control what you can safely control — and know what this trade actually produces.",
			"blocks": [
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "This lesson is not first aid training, and cannot be",
					"content": (
						"<p>Certified first aid and CPR training is a course with an instructor in the "
						"room, hands on a manikin, and an assessment at the end. <b>None of that can "
						"happen on a page</b>, and a page that pretended otherwise would be worse than "
						"nothing because it would leave somebody feeling prepared.</p>"
						"<p>What this lesson does is narrower and still useful: help you <b>recognise</b> "
						"the emergencies this trade actually produces, know <b>when to call</b>, know "
						"what you can safely do while help is coming, and know what not to do. It "
						"prints no dose, no drug and no procedure that needs training to perform "
						"safely.</p>"
						"<p>Get certified. It is a short course, it is available everywhere, and the "
						"person it is most likely to matter for is somebody you work beside.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The order that does not change",
					"content": (
						"<p><b>Scene safety first.</b> The thing that hurt them can hurt you. That is "
						"not a caveat on this page, it is the same rule as the confined space lesson and "
						"the electrical lesson, and it is the rule that turns one casualty into two. "
						"Before you go to somebody, know what dropped them.</p>"
						"<p><b>Call early.</b> Calling for help and being wrong costs nothing. Not "
						"calling and being wrong costs everything, and the hesitation is nearly always "
						"about not wanting to over-react. Over-react.</p>"
						'<p><b>Send a specific person.</b> "Someone call 911" produces a crowd all '
						"assuming someone else did it. Point at a person, give them the job, and give "
						"them the site address and how to get a vehicle in. Send a second person to meet "
						"the ambulance at the gate, and to bring the safety data sheet if a chemical is "
						"involved — the paramedics will ask.</p>"
						"<p><b>Do what you are trained to do, and no more.</b> Do not move a casualty "
						"unless leaving them where they are is more dangerous than moving them. Stay "
						"with them, keep them warm and still, and hand over clearly when help "
						"arrives.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The ones this trade actually produces",
					"content": (
						"<p><b>Chemical splash.</b> Flushing beats everything, and the first seconds are "
						"the ones that decide how much damage there is. Get to the eyewash or the "
						"nearest clean running water <b>immediately</b> and keep flushing while somebody "
						"else makes the call — do not stop to look up the product, do not go and find "
						"the safety data sheet first, and <b>never try to neutralise a chemical on "
						"skin or in an eye</b>, because that reaction generates heat in the burn. Get "
						"contaminated clothing off while flushing. Send the safety data sheet with the "
						"casualty.</p>"
						"<p><b>Chlorine gas or chemical vapour.</b> Usually the acid-and-hypochlorite "
						"mistake, and often in a pump room or a vault. Get out and upwind into fresh "
						"air, get everybody else out with you, and <b>do not go back in for anything "
						"or anybody</b> — that is the confined space rule again. Then get medical "
						"attention <b>even if the person says they feel fine</b>: respiratory injury "
						"from an irritant gas can develop hours later, and feeling all right early is "
						"not reassurance.</p>"
						"<p><b>Electrical contact.</b> <b>Do not touch the casualty.</b> If they are "
						"still in contact with a live conductor, current is flowing through them and it "
						"will flow through you — including through clothing and through anything damp. "
						"Isolate the power and confirm it is off, then help. An electrical casualty "
						"needs medical assessment even when they look unhurt, because the damage is "
						"internal and the heart rhythm is the concern.</p>"
						"<p><b>Submersion.</b> It is a real hazard on our sites and it does not need "
						"deep water. Drowning is quiet — there is no shouting and no splashing — and a "
						"person who was pulled out and is now coughing or breathless still needs medical "
						"assessment. Do not enter the water to reach somebody unless you are trained "
						"for it; reach or throw instead.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Recognise it, and the first correct move",
					"panels": [
						{
							"title": "Chemical splash to eyes or skin",
							"body": "<p>Pain, redness, burning that keeps developing. <b>Flush immediately at an eyewash or clean running water and keep flushing</b> while someone else calls. Remove contaminated clothing. Do not neutralise. The safety data sheet travels with the casualty.</p>",
						},
						{
							"title": "Irritant gas exposure",
							"body": "<p>Coughing, burning eyes and throat, tightness, a sharp smell in a pump room or vault. <b>Get out, upwind, and stay out.</b> Evacuate everybody. Get medical attention even if symptoms ease — they can return and worsen later.</p>",
						},
						{
							"title": "Electrical contact",
							"body": "<p>Person collapsed at or near equipment, possibly still gripping it. <b>Do not touch them. Isolate the power and confirm it is off first.</b> Then call and help. Medical assessment is needed even for someone who gets up and says they are fine.</p>",
						},
						{
							"title": "Submersion",
							"body": "<p>Quiet, not dramatic. <b>Reach or throw — do not go in unless trained.</b> Call. Anyone who was submerged needs medical assessment even if they seem recovered.</p>",
						},
						{
							"title": "Severe bleeding",
							"body": "<p>Blood that keeps coming rather than oozing. <b>Firm, direct pressure</b> with whatever clean material is to hand, and call. A tourniquet is a real tool with a right way to use it and a wrong way — that is trained-skill territory, and the training is the course rather than this page.</p>",
						},
						{
							"title": "Any change in how somebody is thinking",
							"body": "<p>Confusion, slurred speech, not making sense, not responding properly. Whatever caused it — heat, a head injury, a chemical, a medical event — <b>this is the sign that turns a bad situation into an emergency.</b> Call.</p>",
						},
					],
				},
				{
					"block_type": "Checklist",
					"heading": "Know these before the work starts, not during",
					"items": [
						"The site address you would give a dispatcher, and how an ambulance gets in",
						"Where the first aid kit is, on this site, today",
						"Where the nearest eyewash or clean running water is, from where you will be working",
						"Who on site currently holds first aid and CPR training",
						"Where the safety data sheets for what you are using are kept",
						"Whether there is an AED on the property and where",
						"How somebody would actually be got out of the vault or basin you are about to enter",
						"Who you report an injury to, and how",
					],
				},
				ask_block(
					"What is on the truck, and who is trained",
					"<p>First aid kit contents and where they live, eyewash provision on a site "
					"without plumbed water, which technicians hold current first aid and CPR "
					"certificates, how an injury is reported and to whom, and whether an AED is "
					"carried are all Sapphire's to decide, to provide and to publish.</p>"
					"<p>This course cannot tell you any of them, and the wrong moment to find out is "
					"while somebody is on the ground. Ask now, when it is a boring question.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A colleague is slumped against a control panel and may still be in contact with a live conductor. What is the first thing you do?",
						"type": "Single Choice",
						"explanation": (
							"Current flowing through them will flow through you, including through clothing and "
							"anything damp. Isolating and confirming the power is off is the first action — going to "
							"them first is how the rescuer becomes the second casualty."
						),
						"options": [
							{
								"text": "Isolate the power and confirm it is off, then go to them",
								"is_correct": True,
							},
							{
								"text": "Pull them clear by their clothing, which does not conduct",
								"is_correct": False,
							},
							{
								"text": "Check whether they are breathing before doing anything else",
								"is_correct": False,
							},
							{"text": "Throw water over them to break the contact", "is_correct": False},
						],
					},
					{
						"question": "Somebody takes a chemical splash in the eye. What happens first?",
						"type": "Single Choice",
						"explanation": (
							"Flushing immediately and continuously is what limits the damage, and the first seconds "
							"matter most. Someone else calls and fetches the safety data sheet; neutralising a "
							"chemical in an eye generates heat and makes the injury worse."
						),
						"options": [
							{
								"text": "Start flushing at once with clean water and keep flushing while somebody else calls",
								"is_correct": True,
							},
							{
								"text": "Find the safety data sheet first so you know what you are dealing with",
								"is_correct": False,
							},
							{
								"text": "Flush with something that neutralises the chemical",
								"is_correct": False,
							},
							{
								"text": "Rinse briefly, then drive them to an urgent care clinic",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Somebody who breathed chlorine gas got out to fresh air, feels fine now, and so does not need medical attention.",
						"type": "True-False",
						"explanation": (
							"False. Respiratory injury from an irritant gas can develop hours after the exposure, and "
							"feeling well early is not reassurance. They get medical attention regardless."
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
			"lesson_title": "Jobsite clothing and personal protective equipment (PPE)",
			"estimated_minutes": 12,
			"summary": "Why PPE is last on the list, how each type is actually selected, and the two we skip on a fountain.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "PPE is the last line, and that is not a slogan",
					"content": (
						"<p>Controls are ranked, and the ranking is about how much they depend on a "
						"person doing the right thing. <b>Eliminate</b> the hazard. <b>Substitute</b> "
						"something less dangerous. Put in an <b>engineering control</b> — a guard, a "
						"ventilation system, a barrier — that works whether anybody is paying attention "
						"or not. Use <b>administrative controls</b>: procedure, training, rotation. "
						"Then, last, <b>PPE</b>.</p>"
						"<p>PPE is last because it is the weakest. It protects exactly one person; it "
						"works only while it is worn, worn correctly, and undamaged; it does nothing to "
						"the hazard itself, which is still there waiting for the next person who is not "
						"wearing any; and <b>it fails silently</b> — a glove permeated by a chemical and "
						"a glove doing its job look and feel identical.</p>"
						'<p>So when the answer to a hazard is "wear more PPE", the honest question is '
						"whether anybody asked the first four. Sometimes they did and PPE is genuinely "
						"what is left. Often nobody asked.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Each kind of protection is selected against a different thing",
					"content": (
						"<p><b>Eyes.</b> Impact and splash are different protections. Safety glasses "
						"with side shields stop a fragment and do nothing about a liquid arriving from "
						"below or the side; <b>chemical splash goggles seal</b>, and that is the whole "
						"point of them. A face shield protects the face — it is worn <b>over</b> primary "
						"eye protection, never instead of it.</p>"
						"<p><b>Hands.</b> The glove has to match the chemical. Glove materials are "
						"selected against a specific product using the breakthrough data the glove "
						"manufacturer and the safety data sheet provide, and a glove that is right for "
						"one product can be permeated in minutes by another — with no visible change to "
						"warn you. Separately: <b>cut-resistant</b> gloves for sheet metal, glass and "
						"blades, and <b>no gloves at all</b> near rotating machinery, where a glove is "
						"something for the machine to grab.</p>"
						"<p><b>Feet.</b> Protective toe, and puncture-resistant soles wherever there is "
						"demolition debris or an open trench. Slip resistance matters more on this job "
						"than most — wet coping stone and a worn smooth sole is a fall into or beside "
						"hard edges and water.</p>"
						"<p><b>Hearing.</b> The damage is permanent, painless and cumulative, which is "
						"why people skip it: nothing hurts today. Concrete saws, core drills, blowers "
						"and a shop vacuum running in a vault all justify protection.</p>"
						"<p><b>Respiratory.</b> A tight-fitting respirator is not equipment you simply "
						"put on. It requires a written program, medical clearance, training and "
						"<b>fit testing</b>, and facial hair at the sealing surface defeats it "
						"regardless of how well it seems to fit. A dust mask is not protection against "
						"vapour or gas, and believing it is has killed people in exactly the rooms we "
						"work in.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The two that get skipped on a fountain",
					"content": (
						"<p><b>Flotation.</b> Where water is deep enough to drown in, where a current or "
						"a suction outlet could hold somebody, or where a fall would put you in water "
						"you could not climb out of, a life jacket stops being optional. Nobody enters "
						"water alone, and somebody on the bank knows you are in it.</p>"
						"<p><b>Fall protection.</b> Basin edges, open vaults and hatches, scaffolds, "
						"ladders and lifts. The open hatch you left behind you while you fetched a tool "
						"is the classic one, and the answer is a cover or a guard, every time, not "
						"memory. A harness is only protection if it is attached to an anchor rated for "
						"it — a harness clipped to nothing, or to a handrail somebody assumed was strong "
						"enough, is costume.</p>"
						"<p>Both of these feel unnecessary right up to the single moment they are the "
						"only thing between an ordinary day and a fatality.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Clothing is protective equipment too",
					"content": (
						"<p><b>Nothing loose near anything turning.</b> Sleeves, cords, drawstrings, "
						"lanyards, jewellery, watches, long hair. A coupling guard exists because a "
						"rotating shaft takes a sleeve and then an arm, and it does it faster than a "
						"person can react.</p>"
						"<p><b>Sun is an occupational exposure.</b> This work is outdoors for hours on "
						"reflective water and pale stone. Long sleeves, a brimmed hat and sunscreen are "
						"not vanity; skin cancer is the most common occupational cancer in outdoor "
						"trades.</p>"
						"<p><b>High visibility</b> wherever vehicles, equipment or public traffic move "
						"near the work. <b>Layers</b> for cold and wet, because a soaked technician "
						"loses heat fast even on a mild day.</p>"
						"<p><b>Contaminated clothing comes off at work.</b> Chemical on a shirt or in "
						"boots does not stop being chemical in the truck, on the sofa, or in a family "
						"wash. Change, bag it, and follow the product's instructions for "
						"decontamination or disposal.</p>"
					),
				},
				ask_block(
					"The hazard assessment names the PPE, and the data sheet names the glove",
					"<p>The employer is required to assess the hazards of a task and select the "
					"protective equipment for it — that assessment, not a technician's preference, "
					"is what decides goggles versus glasses, or which glove material. For chemical "
					"gloves the specific answer lives in the safety data sheet and the glove "
					"manufacturer's compatibility chart for the product in your hand.</p>"
					"<p>What Sapphire requires, who provides and pays for it, where it is stored, how "
					"it is inspected and when it is replaced are company decisions. If the "
					"assessment for a task you have been given does not exist yet, that is the thing "
					"to raise before starting.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does PPE sit at the bottom of the hierarchy of controls?",
						"type": "Single Choice",
						"explanation": (
							"PPE leaves the hazard in place and protects only the wearer, only while it is worn "
							"correctly and undamaged — and it fails without any visible sign. Elimination and "
							"engineering controls work whether anyone is paying attention or not."
						),
						"options": [
							{
								"text": "It leaves the hazard in place, protects only the wearer, and fails silently",
								"is_correct": True,
							},
							{
								"text": "It is the most expensive control, so it is used last",
								"is_correct": False,
							},
							{
								"text": "It is uncomfortable, so people are less likely to comply",
								"is_correct": False,
							},
							{
								"text": "It is only intended for emergencies rather than routine work",
								"is_correct": False,
							},
						],
					},
					{
						"question": "How should a face shield be used when handling chemicals?",
						"type": "Single Choice",
						"explanation": (
							"A face shield protects the face but does not seal around the eyes. It is worn over "
							"primary eye protection — splash goggles for a chemical — never as a replacement for it."
						),
						"options": [
							{"text": "Over splash goggles, never instead of them", "is_correct": True},
							{
								"text": "Instead of goggles, since it covers more of the face",
								"is_correct": False,
							},
							{"text": "Only when goggles are unavailable", "is_correct": False},
							{
								"text": "Over safety glasses, which are equivalent to goggles for splash",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true about gloves on this job?",
						"type": "Multiple Choice",
						"explanation": (
							"Chemical gloves are selected against the specific product using breakthrough data, no "
							"single glove is right for everything, and gloves come off around rotating machinery "
							"because a glove gives the machine something to grab."
						),
						"options": [
							{
								"text": "A chemical glove is selected against the specific product, using the manufacturer's data",
								"is_correct": True,
							},
							{
								"text": "A glove that is right for one chemical can be permeated quickly by another, with nothing visible to warn you",
								"is_correct": True,
							},
							{"text": "Gloves come off around rotating machinery", "is_correct": True},
							{
								"text": "A nitrile glove is adequate chemical protection against any liquid on the truck",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Physical fatigue and wellness",
			"estimated_minutes": 12,
			"summary": "Heat, cold, lifting and tiredness as jobsite hazards — including the sign that turns heat illness into an emergency.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Fatigue is a jobsite hazard, not a character flaw",
					"content": (
						"<p>Read back through this module and notice what every failure has in common. "
						"The guard that got wedged open. The meter that did not get re-checked on a known "
						"source. The hand that went into the sump because it was only for a second. The "
						"cord that got taped instead of tagged.</p>"
						"<p>Those are not knowledge failures. Everybody involved knew better. They are "
						"what a tired, hot, dehydrated brain does at the end of a long day — it takes "
						"the short version, and the short version is the one without the safety step in "
						"it. Long hours and short sleep produce impairment that is measurable and looks "
						"a lot like other kinds of impairment, including behind the wheel on the way "
						"home.</p>"
						"<p>So saying you are running out is <b>information the crew needs</b>, the same "
						"as saying a fitting is the wrong size. It is not a complaint and it is not "
						"weakness. The person who says it at four o'clock saves somebody else the "
						"incident at five.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Heat: what is actually happening",
					"content": (
						"<p>Your body sheds heat mainly by sweating and letting the sweat evaporate. "
						"Anything that interferes with evaporation takes that away: <b>humidity</b>, "
						"still air, and especially <b>impermeable clothing</b> — chemical suits, "
						"waders, rain gear. Direct sun and radiant heat off concrete, stone and water "
						"add load on top. A sealed pump room in August is a heat hazard with no weather "
						"in it at all.</p>"
						"<p>Dehydration makes it worse in two directions: less fluid to sweat with, and "
						"less blood volume to carry heat to the skin. <b>Thirst lags behind "
						"dehydration</b>, so drinking when you are thirsty means drinking late. Drink "
						"through the day rather than at it.</p>"
						"<p><b>Acclimatisation is real and it is not fitness.</b> The body adapts to "
						"working in heat over days of graded exposure, and it loses that adaptation "
						"after time away. The people most at risk on any crew are therefore the "
						"<b>newest person and the person back from leave or illness</b> — not the "
						"weakest, the newest. A disproportionate share of heat fatalities happen in "
						"somebody's first days on a job.</p>"
						"<p>Water, rest and shade are controls, and taking the rest in the shade rather "
						"than in the truck cab is part of the control working.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Heat exhaustion versus heat stroke, and the sign that separates them",
					"content": (
						"<p><b>Heat exhaustion</b> looks like: heavy sweating, skin that is cool, pale "
						"and clammy, weakness, dizziness, headache, nausea, a fast weak pulse, muscle "
						"cramps. Get them into shade or air conditioning, loosen clothing, cool them, "
						"give fluids if they are fully alert, and <b>they are done working for the "
						"day</b>. If it does not improve, treat it as the next paragraph.</p>"
						"<p><b>Heat stroke</b> is a medical emergency and it kills. The signs are "
						"confusion, slurred speech, agitation or unusual behaviour, staggering, "
						"unconsciousness or seizures, very hot skin, and a body temperature that has "
						"gone out of control. <b>Call 911 immediately</b>, get them out of the heat, and "
						"begin cooling with whatever is available while help is coming. Do not give "
						"anything by mouth to somebody who is confused or not fully awake.</p>"
						"<p><b>The thing that separates them is a change in how the person is "
						"thinking.</b> Not whether they are sweating — the old rule that heat stroke "
						"means dry skin is unreliable, and somebody working hard in heat can be soaked "
						"and in heat stroke at the same time. <b>If you are standing there debating "
						"which one it is, treat it as heat stroke and call.</b> Nobody has ever been "
						"harmed by an ambulance that turned out not to be needed.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cold is the same problem from the other end",
					"content": (
						"<p>Hypothermia does not need snow. <b>Wet plus wind</b> does it at temperatures "
						"well above freezing, and standing in a basin in March doing a startup is wet, "
						"windy and stationary — the three conditions together. Wet clothing pulls heat "
						"out of a person many times faster than dry.</p>"
						"<p>Early on: shivering, cold hands, clumsiness. Then the giveaway — "
						"<b>shivering that stops while the person is still cold is not improvement</b>, "
						"it is the body losing the ability to do it. And again, <b>confusion and slurred "
						"speech are the emergency marker</b>, exactly as with heat. Get them warm and "
						"dry, out of the wind, and call for help.</p>"
						"<p>The everyday version matters too: cold hands lose grip and fine control, "
						"which puts you back in the tool lesson holding something sharp.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Backs, shoulders, and the same movement ten thousand times",
					"content": (
						"<p>Most of what hurts people in this trade is <b>awkward</b> rather than heavy: "
						"a pump lifted out of a wet pit at arm's length, a length of pipe carried "
						"one-handed up steps, a drum on a tailgate. And water is deceptive — at about "
						"<b>8.34 lb per gallon</b>, a bucket, a sump full of debris and water, or a "
						"partly full container weighs far more than the same volume of almost anything "
						"else you handle.</p>"
						"<p>Plan the lift and the route <b>before</b> you pick it up: where it is going, "
						"what is in the way, where you will set it down. Keep the load close to your "
						"body, let your legs do the work, and <b>do not twist while loaded</b> — turn "
						"your feet. The honest answer is very often that it is a two-person lift, or a "
						"cart, or a hoist, and the cost of saying so is a minute.</p>"
						"<p>Repetitive strain builds without an incident to point at: overhead work, "
						"vibration from a hammer drill, kneeling on concrete, hours of the same grip. "
						"Rotate the task, use a kneeling pad, put the work at a sensible height where "
						"you can. <b>Report the ache while it is still an ache</b> — that is the stage "
						"at which it is reversible, and the reason people do not is that it does not "
						"feel like an injury yet.</p>"
					),
				},
				ask_block(
					"Rest, water, shade and when to stop are decisions Sapphire makes",
					"<p>How often a crew breaks, what conditions trigger a heat plan, who makes the "
					"call to stop for weather or for darkness, how long a shift runs, and how an "
					"injury or a near miss gets reported are company decisions that belong in a "
					"written program — not numbers this course can supply.</p>"
					"<p>One thing is yours, though. <b>If you are unsure whether you are safe to keep "
					"working, or safe to drive home, that is a conversation with a supervisor.</b> It "
					"is the cheapest conversation available on any jobsite, and it is the one nobody "
					"regrets having had.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Which sign tells you a heat casualty is a medical emergency rather than heat exhaustion?",
						"type": "Single Choice",
						"explanation": (
							"A change in mental state — confusion, slurred speech, strange behaviour, "
							"unresponsiveness — is what marks heat stroke. Dry skin is unreliable: somebody working "
							"hard can be soaked with sweat and in heat stroke at the same time."
						),
						"options": [
							{
								"text": "Confusion, slurred speech or any change in how they are thinking",
								"is_correct": True,
							},
							{
								"text": "Their skin has gone dry, which is the definitive sign",
								"is_correct": False,
							},
							{"text": "They have muscle cramps in the legs", "is_correct": False},
							{"text": "They feel dizzy when they stand up", "is_correct": False},
						],
					},
					{
						"question": "Who on a crew is at the highest risk of heat illness?",
						"type": "Single Choice",
						"explanation": (
							"Acclimatisation is built over days of graded exposure and lost after time away, so the "
							"newest person and the one back from leave are the least adapted. A disproportionate share "
							"of heat fatalities happen in somebody's first days on a job."
						),
						"options": [
							{
								"text": "The newest person, and anyone back after time away",
								"is_correct": True,
							},
							{
								"text": "The most experienced technician, because they push hardest",
								"is_correct": False,
							},
							{
								"text": "Nobody in particular — heat risk is much the same for everyone",
								"is_correct": False,
							},
							{
								"text": "The oldest person on the crew, because age is what decides it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Somebody working in the cold has stopped shivering while still cold and wet. That means they are warming up.",
						"type": "True-False",
						"explanation": (
							"False. Shivering stopping while a person is still cold means the body is losing the "
							"ability to generate heat — hypothermia is progressing, not improving. Get them warm and "
							"dry and call for help, particularly if they are confused."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
				]
			},
		},
	],
}
