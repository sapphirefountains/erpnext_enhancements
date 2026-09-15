# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 6 — Electrical Components, Automation & Field Diagnostics.

Rewritten against Sapphire's own document, *Module 6: Electrical Automation & Field
Diagnostics*. Where that document states a figure, a sequence or a rule it is used as written,
in place of whatever this file said before: lock-out/tag-out before any sub-panel or control
enclosure is opened, a CAT III or CAT IV meter, a supply within ±10% of specification and
typically 120 VAC, the Splash Wizard's 24 VDC output to the manifold solenoids, 20 to 60 Ω on a
healthy solenoid coil against 0 Ω for melted windings and OL for a snapped one, bare solid
copper no smaller than 8 AWG, the six bonding targets and the 5-foot perimeter, copper or bronze
split-bolts below the water line with steel fasteners forbidden outright, the four-step GFCI
isolation ending on the MΩ range from hot line to structural ground, and the five-step potting
protocol for a submersible lighting junction box.

The document is three lessons — multimeter diagnostics, equipotential bonding, GFCI and potting
— and this course is nine, a shape pinned by ``tests/test_technician_training_program.py``. So
its material sits where it belongs rather than as three new lessons: the meter work is the
multimeter lesson; bonding joins the GFCI lesson, because the document's own reason for bonding
is the fault a GFCI cannot prevent; the solenoid coil and the controller's two voltages are
introduced in the automation-components lesson, which is where the gear is named; and the
potting protocol sits with junction boxes and accessibility in the electrical-equipment lesson.
Lesson one carries ``sourced_notice_block()`` rather than the generic notice for the same
reason the other sourced modules do — those figures are Sapphire's, not this course's.

The document's closing *Technical Performance Checklist* — six field skills demonstrated to a
Lead Installer before a technician graduates Module 6 — is carried whole at the end of the last
lesson.

The lessons it does not reach — conduit and pulling wire, sensors, DMX, ladder logic and program
troubleshooting — keep the general-practice content they had, and the rule that went with it: no
invented figure where a label, a drawing or an engineer owns the answer.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 6 — Electrical Components, Automation & Field Diagnostics",
		"summary": (
			"Get wire into a raceway without damaging it, name every device in a control panel and say "
			"what it protects, prove a circuit dead before you open it, measure a supply and a solenoid "
			"coil, bond the metal around the water so nobody becomes the path, seal a submerged splice, "
			"read a ladder rung and a DMX run, and find the reason a controller is not doing what "
			"somebody expected."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Running conduit and pulling wire",
			"estimated_minutes": 14,
			"summary": "What conduit is for, why fill and bend limits exist, and the pull that quietly ruins a conductor.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "Conduit is a path, not just armour",
					"content": (
						"<p>Conduit does two jobs. It <b>protects the conductors</b> from being cut, "
						"crushed, dug up or chewed, and it <b>leaves a path</b> so the wire inside it "
						"can be pulled out and replaced without breaking concrete.</p>"
						"<p>The second job is the one people forget, and it is the one that costs money "
						"later. A fountain is full of things that get changed: a pump gets bigger, a "
						"light becomes a different light, a controller is replaced by one that needs two "
						"more conductors. If the raceway is still a raceway, that is an afternoon. If it "
						"was stuffed full, kinked and buried under stone, it is a demolition job.</p>"
						"<p>Everything below is about keeping it a path.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Fill, bends, and the fact that conductors make heat",
					"content": (
						"<p><b>Fill.</b> There is a limit to how much of a conduit's inside area the "
						"conductors may occupy, and it has nothing to do with whether you can physically "
						"force them in. Two reasons. A pull that is too tight drags insulation across "
						"the wall of the conduit and across the other conductors and scrapes it. And "
						"<b>current-carrying conductors make heat</b>, which has to leave; bundle enough "
						"of them in one raceway and each one has to be derated to account for the heat "
						"of its neighbours.</p>"
						"<p><b>Bends.</b> Every bend adds friction, and friction multiplies rather than "
						"adds — the tension needed at the pulling end climbs steeply as bends "
						"accumulate. The NEC caps the total bend between pull points at the equivalent "
						"of four quarter bends, 360 degrees. The answer to a run that needs more is "
						"another pull point, a box or a conduit body — not more rope.</p>"
						"<p><b>Expansion.</b> PVC conduit moves with temperature far more than the "
						"concrete and steel it is strapped to. A long exposed run clamped hard at both "
						"ends bows, buckles, or pulls a joint apart. Expansion fittings give it "
						"somewhere to go. How much movement to expect, and therefore which fitting and "
						"how far open it is set at the temperature you install it, is a design figure "
						"for that run and that climate.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "An underground conduit is full of water",
					"content": (
						"<p>Treat every buried raceway as though it has standing water in it, because it "
						"does. Solvent-welded conduit joints are not watertight the way pressure pipe "
						"is, the ground around it is wet, and warm air drawn into a conduit condenses on "
						"the inside when it cools overnight. So:</p>"
						"<p><b>Conductors in an underground raceway are in a wet location</b> and have to "
						"be listed for one. That is not the same wire that is acceptable in a dry indoor "
						"wall.</p>"
						"<p><b>Seal the ends where the conduit enters an enclosure</b>, and drain at the "
						"low point where the design provides for it. Water and the damp air with it "
						"migrate up a conduit and condense inside the panel — which is how a dry-looking "
						"indoor panel corrodes from the inside out and how a controller dies for no "
						"visible reason.</p>"
						"<p>Water in the conduit plus a nick in the insulation from a hard pull is the "
						"ground fault that appears months later, in the rain, as a GFCI that will not "
						"reset.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The pull itself",
					"content": (
						"<p>A pull is a controlled operation between people who can hear each other, not "
						"a tug of war.</p>"
						"<p><b>Lubricant.</b> Use a listed pulling compound that is compatible with the "
						"insulation and the conduit. Soap, grease and whatever is in the truck can "
						"attack a jacket, and the damage does not show until years later.</p>"
						"<p><b>Steady tension.</b> Jerking spikes the tension at the first bend, which is "
						"where the insulation is already under the most pressure. Pull smoothly and feed "
						"the wire off the reel so it is never dragged across a sharp edge or allowed to "
						"kink — a kinked conductor is damaged whether or not you can see it.</p>"
						"<p><b>A pull that stalls is telling you something.</b> A crushed section, a "
						"coupling that came apart, gravel in the raceway, a bend past the limit, or "
						"simply more conductors than that conduit was meant to hold. More force fixes "
						"none of those. It strips insulation instead, and leaves you with a fault you "
						"cannot see and do not know you made. Stop and find out why.</p>"
					),
				},
				ask_block(
					"What wire, what conduit and what size are engineered",
					"<p>Conductor size and insulation type, conduit type and trade size, fill, derating, "
					"burial depth, expansion allowance and where the pull points go are all set by the "
					"<b>electrical design, the NEC and the authority having jurisdiction</b> for that "
					"job.</p>"
					"<p>None of those is a figure this course can hand you, and a size that was right on "
					"the last job is the wrong kind of memory. If the drawing does not say, ask before "
					"the conduit goes in the ground rather than after.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is there a limit on how much of a conduit's inside area the conductors may fill?",
						"type": "Multiple Choice",
						"explanation": (
							"Fill limits exist so the pull does not damage insulation and so the heat made by "
							"current-carrying conductors can get out of the raceway. Whether the wire physically "
							"fits is not the question being answered."
						),
						"options": [
							{
								"text": "So pulling tension stays low enough not to scrape insulation off the conductors",
								"is_correct": True,
							},
							{
								"text": "So the heat made by current-carrying conductors can leave the raceway",
								"is_correct": True,
							},
							{"text": "So water can drain freely past the conductors", "is_correct": False},
							{
								"text": "So the weight of the conductors stays within what the straps can carry",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A wire pull stalls halfway along a long underground run. What is the right response?",
						"type": "Single Choice",
						"explanation": (
							"A stall means something is wrong in the raceway — a crushed section, a parted "
							"coupling, debris, too many bends. Adding force does not fix any of them; it strips "
							"insulation and creates a fault nobody knows about."
						),
						"options": [
							{
								"text": "Stop and find out what the conduit is doing before any more tension goes on",
								"is_correct": True,
							},
							{
								"text": "Put more people on the rope and keep the tension up until it moves",
								"is_correct": False,
							},
							{
								"text": "Switch to a winch so the tension is steady rather than jerky, and continue",
								"is_correct": False,
							},
							{
								"text": "Cut the conductors and start a fresh pull from the other end",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A buried PVC conduit was assembled carefully with every joint cemented, so ordinary dry-location wire is acceptable inside it.",
						"type": "True-False",
						"explanation": (
							"Conduit joints are not watertight the way pressure pipe is, the surrounding ground is "
							"wet, and air inside condenses. An underground raceway is a wet location and the "
							"conductors must be listed for one."
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
			"lesson_title": "Basic automation components",
			"estimated_minutes": 15,
			"summary": "The devices that let a small signal switch a large load, and why a quiet control circuit proves nothing.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Something small switching something dangerous",
					"content": (
						"<p>Almost every device in a fountain control panel exists for one reason: to let "
						"something small and safe — a controller output, a float switch, a pushbutton — "
						"turn something large and dangerous on and off.</p>"
						"<p>That splits the panel into two worlds. The <b>control circuit</b> carries the "
						"decision. On a Sapphire panel that is the <b>24 VDC</b> a Splash Wizard "
						"controller puts out to cycle the manifold solenoids, plus whatever a float, an "
						"activation button or a screen terminal contributes; it carries very little "
						"current, and it is the part that has wire numbers on it and shows up on the "
						"schematic as logic. The <b>power circuit</b> carries the motor, the heater and "
						"the lighting load at line voltage — <b>120 VAC</b> at the main line input, and "
						"more where the equipment calls for it — at full current.</p>"
						"<p>They sit inside the same enclosure, frequently on the same DIN rail, and this "
						"is the fact that hurts people: <b>the control side being dead tells you nothing "
						"about the power side.</b></p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The gear, and what each piece is actually for",
					"panels": [
						{
							"title": "Relay",
							"body": (
								"<p>A small switch operated by a coil. Energise the coil from the control "
								"circuit and the contacts change state, switching a circuit that is "
								"electrically separate from the one that commanded it. That separation is "
								"the point: one low-voltage signal can switch several things, at a "
								"different voltage, without the controller ever seeing that voltage.</p>"
								"<p>The contacts have a rating. A relay is selected for what it switches "
								"and how often, not for what fits the empty space on the rail.</p>"
							),
						},
						{
							"title": "Contactor",
							"body": (
								"<p>A relay built for load current. Larger contacts, built to make and "
								"break motor and heater current thousands of times and to manage the arc "
								"that happens every time it does. Coil on the control side, contacts on "
								"the power side. A contactor that chatters, buzzes or shows burnt, pitted "
								"contacts is on its way out and will fail closed or fail to pull in.</p>"
							),
						},
						{
							"title": "Motor starter and overload",
							"body": (
								"<p>A starter is a contactor plus an <b>overload</b>. The overload watches "
								"the current the motor draws and trips when it has been too high for too "
								"long — a blocked impeller, a seizing bearing, a lost phase, a jammed "
								"shaft.</p>"
								"<p>Note what is <b>not</b> on that list. A centrifugal pump running "
								"against a closed valve draws <i>less</i> current, not more: it is moving "
								"no water, so the power it takes falls away. It cooks itself quietly "
								"while the overload stays perfectly satisfied. Dead-heading is caught by "
								"proven flow, by pressure and by temperature — never by the overload.</p>"
								"<p>This is not what the breaker does. The breaker protects the "
								"<b>conductors</b> from a short circuit or a gross overcurrent, and it "
								"acts fast. The overload protects the <b>motor</b> from slowly cooking "
								"itself at a current the breaker is perfectly happy with. Two devices, two "
								"jobs, and neither does the other's.</p>"
							),
						},
						{
							"title": "Variable frequency drive",
							"body": (
								"<p>A VFD changes the frequency and voltage going to the motor, and "
								"therefore its speed. A fountain gets two things from that: an effect that "
								"can be tuned or varied, and a <b>ramp</b> — the pump is brought up to "
								"speed and back down over seconds instead of instantly.</p>"
								"<p>A drive is also an electronic device in a wet, hot, outdoor "
								"environment with a stored charge inside it. It has ventilation "
								"requirements, it makes electrical noise that gets into signal cable run "
								"beside it, and its DC bus stays live after the power is off.</p>"
							),
						},
						{
							"title": "Timeclocks and controllers",
							"body": (
								"<p>The part that decides <i>when</i>. An astronomic timeclock tracks "
								"sunrise and sunset through the year so a lighting schedule does not have "
								"to be reset every month. A programmable controller runs a sequence. A "
								"lighting controller drives a show.</p>"
								"<p>The one you will meet most on our work is the <b>Splash Wizard</b>. "
								"Line voltage comes in at its main line input and <b>24 VDC</b> goes out "
								"of it to cycle the manifold solenoids. Both facts matter when you have "
								"the meter out: the input is diagnosed on the AC range and the output on "
								"the DC range, and mixing those up wastes an afternoon.</p>"
								"<p>All of them are the brain and none of them are the muscle. A "
								"controller output is a signal; something downstream is doing the "
								"switching.</p>"
							),
						},
						{
							"title": "Solenoid valves on the manifold",
							"body": (
								"<p>The muscle at the water end. The controller does not move water; it "
								"energises a coil, and the coil opens a valve. Both kinds turn up on our "
								"manifolds: <b>fast-acting</b> valves, for effects that have to snap, and "
								"<b>diaphragm</b> valves, which borrow line pressure to do the work.</p>"
								"<p>The coil is the part that fails. It is a few tens of ohms of fine "
								"copper wire living in a wet vault, and it has two ways to die. The "
								"windings can melt together, which makes the coil a near short across a "
								"24 VDC output and takes the controller's circuit fuse with it — so one "
								"drowned coil presents as a controller that has stopped doing anything at "
								"all. Or the wire simply snaps, the coil goes open, and nothing happens "
								"and nothing else is damaged. Lesson 5 is how you tell those apart with a "
								"meter.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A dead control circuit is not a dead panel",
					"content": (
						"<p>Control panels routinely have <b>more than one source of power</b>. The line "
						"side of the disconnect is live with the disconnect open. A control transformer "
						"is fed from somewhere. A controller, a modem or a light circuit may come in from "
						"a separate breaker in a different panel entirely, so switching off the one you "
						"found leaves parts of that enclosure energised and looking exactly like the rest "
						"of it.</p>"
						"<p>Which is why the rule is not <i>turn it off</i>. It is <b>lock it out, tag it, "
						"and verify dead at the conductors you are about to touch</b> — every source, "
						"every time, with a meter you proved on a live source first. <b>That sequence "
						"is not taught here.</b> It lives in Module 9 and in the employer's written "
						"lock-out/tag-out and electrical safety programs, and those are also what say "
						"who is authorised to work it at all; Lesson 5 covers only the meter half. "
						"Nothing in this module is a reason to skip either.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why a ramp matters to the pipe, not just the motor",
					"content": (
						"<p>Water in a pipe is heavy and it is moving. Stop it abruptly — slam a valve, "
						"drop a pump across the line — and that momentum has nowhere to go except into "
						"the pipe as a pressure spike. That is water hammer. You hear it as a bang; the "
						"system experiences it as a shock load on every fitting, joint and check valve in "
						"the run, over and over.</p>"
						"<p>A soft ramp on a drive brings the column up and down gradually, so the "
						"momentum changes over seconds rather than instantly. The motor and the starter "
						"get an easier life out of it too, and the inrush at the service is lower.</p>"
						"<p>It is not, however, a replacement for the surge control the system was "
						"designed with. Check valves, air release and any surge device are there for the "
						"power failure nobody ramps.</p>"
					),
				},
				ask_block(
					"Settings come off the nameplate and the manual",
					"<p>An overload is set from the <b>motor nameplate</b>. A drive's ramp times, current "
					"limits and speed range come from the drive manual and the sequence of operations "
					"written for that feature. Which relay, which contactor and which drive are on a "
					"given panel is on the panel drawing.</p>"
					"<p>None of those is dialled in from memory or copied off the last job, and a ramp "
					"that was right on one pump is wrong on a different one. If the drawing and the "
					"nameplate disagree, that is a question to raise, not a difference to split.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "In a motor starter, what is the difference between the breaker upstream and the overload?",
						"type": "Single Choice",
						"explanation": (
							"The breaker protects the conductors from a short circuit or gross overcurrent. The "
							"overload protects the motor from drawing more than it should for longer than it "
							"should — a current the breaker is entirely happy with."
						),
						"options": [
							{
								"text": "The breaker protects the conductors from a short or overcurrent; the overload protects the motor from drawing too much for too long",
								"is_correct": True,
							},
							{
								"text": "The overload protects the conductors and the breaker protects the motor",
								"is_correct": False,
							},
							{
								"text": "They are two names for the same protective device",
								"is_correct": False,
							},
							{
								"text": "The breaker protects against ground faults and the overload against short circuits",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What does a soft ramp on a drive do for the piping?",
						"type": "Single Choice",
						"explanation": (
							"A moving column of water has momentum. Changing its speed over seconds instead of "
							"instantly stops that momentum arriving at the fittings as a pressure spike."
						),
						"options": [
							{
								"text": "It changes the speed of the water column gradually, so its momentum does not become a pressure spike",
								"is_correct": True,
							},
							{
								"text": "It lowers the voltage so the motor runs cooler at every speed",
								"is_correct": False,
							},
							{
								"text": "It removes the need for check valves and surge control in the piping",
								"is_correct": False,
							},
							{
								"text": "It limits the pump to half its rated flow so the pipe is never fully loaded",
								"is_correct": False,
							},
						],
					},
					{
						"question": "If the control circuit in a panel is de-energised, the components inside that panel are safe to work on.",
						"type": "True-False",
						"explanation": (
							"A panel usually has several sources. The line side of the disconnect, a control "
							"transformer feed, or a circuit fed from another panel can all still be live. Only "
							"lock-out plus a verified dead test answers this."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
					{
						"question": "What does a Splash Wizard controller put out to cycle a manifold solenoid?",
						"type": "Single Choice",
						"explanation": (
							"Line voltage arrives at the controller's main line input; 24 VDC leaves it for the "
							"solenoid coils. Which is why the input is diagnosed on the meter's AC range and the "
							"output on its DC range."
						),
						"options": [
							{"text": "24 VDC", "is_correct": True},
							{"text": "120 VAC, the same as its line input", "is_correct": False},
							{"text": "24 VAC from an internal control transformer", "is_correct": False},
							{
								"text": "Nothing — the output is a dry contact and the field wiring supplies the voltage",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Sensor types and functions",
			"estimated_minutes": 13,
			"summary": "What each sensor is really measuring, and why a broken wire has to produce the safe state.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A switch reports a state; a probe reports a value",
					"content": (
						"<p>Sensors come in two flavours and they fail differently, so it is worth having "
						"the difference straight before the list.</p>"
						"<p>A <b>switch</b> changes a contact at a threshold. The controller sees on or "
						"off and nothing in between: the water is above the float or it is not, there is "
						"flow or there is not. Simple, robust, and it tells you nothing about how far "
						"past the threshold you are.</p>"
						"<p>A <b>probe or transducer</b> reports a continuous value — a level, a "
						"temperature, a pH. That is more information, and more ways to be quietly wrong, "
						"because a probe that has drifted still reports a number and the number still "
						"looks like a measurement.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "What is on a fountain, and what it is watching",
					"panels": [
						{
							"title": "Float and level switches",
							"body": (
								"<p>A float rides on the surface and flips a contact as it rises or falls. "
								"Used for make-up water and, more importantly, for the low-water cutoff "
								"that stops a pump before it runs dry.</p>"
								"<p>Mechanical, cheap and reliable until it is not: a float can foul on "
								"debris, wrap its own cable, ice up, or hang on the side of a sump. A "
								"stuck float reports the same thing forever, and forever looks a lot like "
								"a level that simply has not changed.</p>"
							),
						},
						{
							"title": "Pressure transducers used as level",
							"body": (
								"<p>Depth and pressure are the same fact in different units. Water weighs "
								"about 8.34 pounds per gallon and about 62.4 pounds per cubic foot, so a "
								"column of water of a given depth produces a predictable pressure at the "
								"bottom of it. A transducer at the bottom of a basin reads that pressure "
								"and reports a continuous level.</p>"
								"<p>It gives you a trend rather than a threshold, which is what makes "
								"leak detection possible. It also needs its port kept clear and its zero "
								"kept honest.</p>"
							),
						},
						{
							"title": "Flow switches",
							"body": (
								"<p>Proves that water is actually moving, rather than that a pump has been "
								"told to run. That distinction is the whole safety case: a heater firing "
								"into a dead line, or a chemical feeder dosing into water that is not "
								"going anywhere, is a real hazard rather than an inefficiency.</p>"
								"<p>Anything that heats, treats or doses should be interlocked to proven "
								"flow.</p>"
							),
						},
						{
							"title": "Temperature sensors",
							"body": (
								"<p>Feed heaters and chillers, and drive freeze protection. On an outdoor "
								"feature the freeze interlock is the one that matters at three in the "
								"morning, and it is only as good as the sensor's placement — a sensor in a "
								"warm equipment room is not measuring the basin.</p>"
							),
						},
						{
							"title": "pH probes",
							"body": (
								"<p>The pH scale runs from 0 to 14. Seven is neutral, below it is acidic, "
								"above it is basic, and each whole number is a tenfold change in "
								"hydrogen-ion concentration — which is why a small-looking pH move is not "
								"a small chemical move.</p>"
								"<p>Probes drift and age. They are calibrated against buffer solutions, "
								"and a probe that has never been calibrated reports a plausible number "
								"indefinitely. Module 3 covers the chemistry; the electrical point is that "
								"this is a sensor with a service life, not a fitting.</p>"
							),
						},
						{
							"title": "ORP probes",
							"body": (
								"<p>Oxidation-reduction potential, read in millivolts. It measures how "
								"strongly oxidising the water is — an indication of how <i>effective</i> "
								"the sanitiser is right now, not how much of it is in the water. pH, "
								"temperature and what else is in the basin all move it.</p>"
								"<p>So ORP is a control signal, not a test result. It does not replace a "
								"test kit and it should never be the only thing anybody looks at.</p>"
							),
						},
						{
							"title": "Wind sensors",
							"body": (
								"<p>Measure wind and tell the controller to drop the height of a feature, "
								"or shut it down, before the wind carries the water out of the basin. That "
								"is three problems at once: water loss, a wet and slippery walking "
								"surface, and a complaint from whoever was standing there.</p>"
								"<p>Like the freeze interlock, it only works if somebody proved it works. "
								"An anemometer that has seized reads a calm day in a gale.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A broken wire has to produce the safe state",
					"content": (
						"<p><b>Normally open</b> and <b>normally closed</b> describe a contact at rest, "
						"unactuated, sitting on the shelf. That is a fact about the device. It is not yet "
						"a fact about your machine, and confusing the two is how interlocks get defeated "
						"by accident.</p>"
						"<p>The question that matters on the job is this: <b>which state does the circuit "
						"have to be in for the machine to be allowed to run, and what happens to that "
						"state when the wire breaks?</b></p>"
						"<p>A break always produces an <b>open</b> circuit. A cut cable, a corroded "
						"connector, a terminal backing out, a chewed lead — all of them open. So an "
						"interlock that must <i>stop</i> something is wired so that the circuit is "
						"<b>closed while all is well</b>. Then the failure produces the stop rather than a "
						"false permission.</p>"
						"<p>Wire the same low-water cutoff the other way round and the moment its cable is "
						"damaged the controller reads water present, forever, and the pump runs dry. Same "
						"sensor, same fault, opposite outcome.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Test the sensor instead of believing it",
					"content": (
						"<p>Every sensor above has a failure mode where it keeps reporting confidently. So "
						"the habit is to make the condition happen and watch what the machine does, "
						"rather than to read the screen and be satisfied.</p>"
						"<p>Drop the level and see the cutoff act. Close a valve and see the flow switch "
						"drop out. Check the pH reading against a test kit. Disconnect a lead — where it "
						"is safe to, with the equipment in a state where it can stop — and confirm the "
						"machine goes to the safe state rather than carrying on.</p>"
						"<p>And treat a reading that has not moved at all as suspicious, even when the "
						"number is entirely plausible. A stuck sensor and a stable process look identical "
						"from the control room.</p>"
					),
				},
				ask_block(
					"Setpoints and calibration are site decisions",
					"<p>What level trips the cutoff, at what wind speed a feature drops, which chemical "
					"targets the controller holds, how often a probe is calibrated and with which buffers "
					"— all of that belongs to the <b>sequence of operations</b> for that site, the "
					"commissioning record, and the probe manufacturer's instructions.</p>"
					"<p>Changing a setpoint because a sensor keeps tripping is not a repair. It is "
					"removing the alarm that was working. Raise it instead.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does fail-safe wiring hold a low-water interlock circuit closed while the basin has water in it?",
						"type": "Single Choice",
						"explanation": (
							"Every break in the wiring — a cut cable, a corroded connector, a loose terminal — produces "
							"an open circuit. Wiring it this way means the failure produces the stop rather than a "
							"false permission to run."
						),
						"options": [
							{
								"text": "Because a broken wire always produces an open circuit, so the stop state is the one a wiring failure creates",
								"is_correct": True,
							},
							{
								"text": "Because closed contacts carry less current and therefore last longer",
								"is_correct": False,
							},
							{
								"text": "Because a controller input cannot read an open circuit reliably",
								"is_correct": False,
							},
							{
								"text": "Because a float switch is only manufactured with one contact arrangement",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What does an ORP probe tell you?",
						"type": "Single Choice",
						"explanation": (
							"ORP reads how strongly oxidising the water is, in millivolts. That is an indication of "
							"sanitiser effectiveness under current conditions, not a measurement of how much "
							"sanitiser is present, and it does not replace a test kit."
						),
						"options": [
							{
								"text": "How strongly oxidising the water is, which indicates sanitiser effectiveness rather than concentration",
								"is_correct": True,
							},
							{
								"text": "The concentration of sanitiser in the water, in parts per million",
								"is_correct": False,
							},
							{
								"text": "The pH of the water, read electrically instead of with a reagent",
								"is_correct": False,
							},
							{"text": "The total dissolved solids in the basin", "is_correct": False},
						],
					},
					{
						"question": "Which of these are good reasons to distrust a sensor reading even though the value looks plausible?",
						"type": "Multiple Choice",
						"explanation": (
							"A stuck float and a drifted probe both report confidently. A value that has not moved "
							"when it should have, and a reading that has never been checked against something "
							"independent, are exactly the conditions where a wrong number looks right."
						),
						"options": [
							{
								"text": "The value has not moved at all across a period when it should have changed",
								"is_correct": True,
							},
							{
								"text": "It has never been checked against an independent measurement or a physical look",
								"is_correct": True,
							},
							{
								"text": "The device is a switch rather than a continuous transducer",
								"is_correct": False,
							},
							{
								"text": "The sensor is wired so that a broken wire produces the safe state",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Basic electrical equipment",
			"estimated_minutes": 16,
			"summary": "What each part of the distribution system protects, the rule about junction boxes that gets broken on nearly every finished fountain, and how a submerged splice is sealed.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "From the service to the fixture",
					"content": (
						"<p><b>The panel</b> takes one supply and splits it into circuits, and holds the "
						"overcurrent device for each one.</p>"
						"<p><b>The breaker</b> protects the <i>conductor</i> downstream of it. It is sized "
						"to the wire, not to the appetite of whatever is plugged in at the far end. This "
						"is the single most misunderstood sentence in the panel.</p>"
						"<p><b>The disconnect</b> gives a means of removing power at the equipment, within "
						"sight of it, so the person working on the pump controls the power to the pump "
						"rather than trusting a panel in another room and a text message.</p>"
						"<p><b>Transformers</b> change voltage. A control transformer makes the low-voltage "
						"control circuit inside a panel. A lighting transformer feeds low-voltage "
						"fixtures.</p>"
						"<p><b>Junction boxes</b> hold splices, keep them dry, and contain the arc if one "
						"of those splices ever fails.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A breaker that trips is doing its job",
					"content": (
						"<p>A breaker that trips repeatedly is a message, and the message is that "
						"something downstream is drawing more current than that conductor is rated to "
						"carry. A motor with a failing bearing. A pump whose impeller is jammed by debris. Water "
						"where water should not be. A conductor damaged during a pull.</p>"
						"<p><b>Fitting a larger breaker to stop the tripping is a way to start a fire.</b> "
						"The breaker was sized to protect the wire in the wall, and swapping the breaker "
						"does not make the wire any bigger. What it does is remove the only thing that "
						"was going to notice the fault.</p>"
						"<p>The right move is always the same: find out what is drawing the current.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Low voltage is not automatically low risk",
					"content": (
						"<p>Low-voltage lighting reduces shock hazard. It does not remove the fire hazard, "
						"and it does not exempt anything from the rules that apply around water.</p>"
						"<p>Three things bite on fountain lighting. <b>Connections get wet</b>, so every "
						"splice and entry has to be made with the method the fixture manufacturer lists "
						"for that location — a wire nut in a puddle is not a connection. <b>Voltage drop "
						"is real</b>: over a long run the far fixtures get less voltage than the near "
						"ones — on a lamp that reads as dim and yellow, on an LED more often as a colour "
						"shift, a flicker, or a fixture that simply drops out — and it is a design "
						"problem solved by conductor size and transformer taps rather than by turning "
						"something up. And "
						"<b>bonding still applies</b>: the fact that a circuit is low voltage does not "
						"take the metal parts around the water out of the bonding requirements.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A box has to stay accessible, and this is the rule people break",
					"content": (
						"<p>Every junction box and every splice must remain <b>accessible</b> without "
						"demolishing the structure. No box under a stone coping. None mortared in, buried "
						"under soil in a planter, tiled over, or hidden behind a panel that was then "
						"caulked.</p>"
						"<p>The reason is simple: a splice is the most likely place for a circuit to fail, "
						"and the next person has to be able to reach it. On a finished fountain a box is "
						"ugly, and that is exactly why this rule gets broken during the last week of a "
						"job by somebody who is not thinking about the technician who arrives three "
						"winters later.</p>"
						"<p>If a box is in the way, it gets relocated or an accessible cover gets designed "
						"in. It does not get buried. While you are there: covers on, gaskets in, "
						"connectors listed for the location, and every unused knockout plugged — an open "
						"knockout in a wet location is a direct path to a splice.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Potting an underwater junction box",
					"content": (
						"<p>A submersible LED light has a cord that runs back to a water-tight junction "
						"box sitting just above or just below the water surface. Water under hydrostatic "
						"pressure does not need a hole to get in. It works at the entries and the seams, "
						"and once it is inside it only has to bridge a terminal strip once. So on our "
						"work that box does not merely get closed. It gets <b>potted</b>: filled with a "
						"compound that encapsulates every splice, so there is nothing left inside for "
						"water to bridge.</p>"
						"<p>The compound is a <b>two-part re-enterable polyurethane</b>, and "
						"<i>re-enterable</i> is the word that carries the weight. It chemically sets into "
						"a thick, gel-like rubber rather than a rock, so the splices are sealed against "
						"water and can still be cut away when a fixture has to come out. That is also "
						"what keeps the box honest against the rule above — a potted box is still an "
						"accessible box. A box filled with something that cannot be cut, or a potted box "
						"buried under a coping, is not.</p>"
						"<p>Two things spoil the job and both happen before the pour. Splices made with "
						"anything other than <b>water-tight crimp connectors</b> are asking the compound "
						"to do a job it was only ever meant to back up. And dust or moisture left in the "
						"shell stops the compound bonding where it lands, which is where <b>voids</b> "
						"come from. A void is a channel, and a channel is exactly the thing you poured "
						"the compound to prevent — and it is invisible once the surface skins over.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "The potting protocol",
					"items": [
						"Every splice inside the box is made with water-tight crimp connectors before any compound is mixed",
						"The interior of the junction box shell is cleaned out — no dust, no moisture, nothing loose",
						"The two-part re-enterable polyurethane compound is mixed thoroughly, to the manufacturer's own guidelines",
						"The compound is poured slowly, so it flows around the splices instead of trapping air against them",
						"The pour continues until every wire splice is encapsulated and the housing is filled to the rim",
						"The compound is left to set chemically into a thick, gel-like solid barrier before the box is handled",
						"The finished pour is void-free, and the box is still one a technician can re-enter with a knife",
					],
				},
				ask_block(
					"Who is allowed to open what",
					"<p>Which electrical work a technician may carry out, and which requires a licensed "
					"electrician, is set by <b>state and local law</b> and by Sapphire's own policy — not "
					"by confidence or by how long the drive back is. Work on or near energised equipment "
					"is governed by NFPA 70E and by the employer's written electrical safety program.</p>"
					"<p>The working boundary is short and it holds even where the law is unclear: if you "
					"cannot lock it out and verify it dead, you are not opening it. Ask your supervisor "
					"where the line sits for you, before you are standing in front of a panel deciding.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What is a circuit breaker sized to protect?",
						"type": "Single Choice",
						"explanation": (
							"The breaker protects the conductor downstream from carrying more current than it is "
							"rated for. It is not sized to the appliance and it is not primarily a personnel "
							"protection device."
						),
						"options": [
							{"text": "The conductors downstream of it", "is_correct": True},
							{"text": "The appliance or motor at the end of the circuit", "is_correct": False},
							{"text": "A person who contacts an energised part", "is_correct": False},
							{"text": "The bus and main lugs inside the panel", "is_correct": False},
						],
					},
					{
						"question": "A junction box ends up directly underneath a stone coping on a finished fountain. What has to happen?",
						"type": "Single Choice",
						"explanation": (
							"Junction boxes and splices must stay accessible without demolition, because a splice "
							"is the most likely point of failure and somebody has to be able to reach it. Good "
							"connectors and a note on the as-built do not satisfy that."
						),
						"options": [
							{
								"text": "The box is relocated, or an accessible cover is designed into the coping",
								"is_correct": True,
							},
							{
								"text": "It is acceptable if every splice inside was made with waterproof connectors",
								"is_correct": False,
							},
							{
								"text": "It is acceptable as long as the location is recorded on the as-built drawings",
								"is_correct": False,
							},
							{
								"text": "It is acceptable because the circuit is low voltage",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A breaker that keeps tripping may be replaced with a larger one, as long as the equipment it feeds is rated for the larger size.",
						"type": "True-False",
						"explanation": (
							"The breaker protects the conductor, and a larger breaker does not make the wire "
							"larger. Upsizing it removes the only device that would have noticed the fault causing "
							"the trips."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
					{
						"question": "Why is the compound used to pot an underwater lighting junction box specified as re-enterable?",
						"type": "Single Choice",
						"explanation": (
							"It sets into a thick, gel-like solid rather than a rock, so it seals the splices "
							"against water and can still be cut away when a fixture has to be replaced. Sealing a "
							"splice is not a reason to make it unreachable."
						),
						"options": [
							{
								"text": "So the set compound can be cut away later and the splices are still reachable when a fixture is replaced",
								"is_correct": True,
							},
							{
								"text": "So it cures faster than a rigid compound and the light can be energised sooner",
								"is_correct": False,
							},
							{
								"text": "So any water that does get in can drain back out through the compound",
								"is_correct": False,
							},
							{
								"text": "So the box may be buried under the coping once the pour has set",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Multimeter diagnostics (voltage, resistance, continuity)",
			"estimated_minutes": 20,
			"summary": "Which measurements are valid live, which are only valid dead, what a supply and a solenoid coil should read, and how to prove a meter is still telling the truth.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Three measurements, two different worlds",
					"content": (
						"<p><b>Voltage</b> is a difference between two points, so it is measured "
						"<b>across</b> them, in parallel, on a circuit that is <b>live</b>. The meter has "
						"a very high internal resistance and takes a tiny sample of what is there. "
						"Measuring voltage is the only one of the three that belongs on an energised "
						"circuit.</p>"
						"<p><b>Resistance</b> and <b>continuity</b> work the opposite way round. The meter "
						"supplies its own small current and measures what comes back. Which means they "
						"are valid only on a circuit that is <b>de-energised and isolated</b>. Any outside "
						"voltage corrupts the reading and can destroy the meter, and <i>isolated</i> "
						"matters even when nothing is live: a parallel path through some other part of "
						"the circuit gives you a resistance that is completely real and not the one you "
						"were asking about. Lift one end of what you are measuring.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The golden rule, and the meter that is allowed in the panel",
					"content": (
						"<p>Sapphire's rule comes first and it is one sentence: <b>lock out and tag out "
						"before you open any sub-panel or control enclosure</b>, and treat every circuit "
						"in it as live until your meter has physically proved otherwise. Not until it "
						"looks dead, not until somebody says it is off — until the meter says so, in "
						"your hand, on that conductor.</p>"
						"<p>The second rule is about the instrument. A meter used on our panels is rated "
						"<b>CAT III or CAT IV</b>. Those categories describe how much fault energy the "
						"meter is built to survive if the circuit flashes over while it is connected, and "
						"a commercial or industrial panel is a CAT III or CAT IV environment. A cheap or "
						"unrated meter in that panel is not a worse measurement — it is the thing that "
						"turns a fault into an arc flash across your hands.</p>"
						"<p>The category comes with a <b>voltage rating</b>, and both have to suit the "
						"installation in front of you rather than your confidence — a service entrance "
						"and a 24 VDC control loop are not the same environment, even though the same "
						"meter comes out of the bag for both.</p>"
						"<p>The rating covers the <b>leads</b> as well, and this is where it is usually "
						"lost. A CAT III meter with unrated, cracked or damaged leads is not a rated "
						"measurement. Cracked insulation, an exposed strand, a probe tip that has been "
						"filed — those leads get binned, not taped.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Live, dead, live — in that order",
					"content": (
						"<p>Proving a circuit dead is three steps, and the order is the entire point. "
						"What follows is the <i>measurement</i> taken inside a lock-out, not a "
						"replacement for one — Module 9 and the employer's program own the sequence "
						"around it, and say who is authorised to do it at all.</p>"
						"<p><b>1.</b> Test the meter on a source you know is live. <b>2.</b> Test the "
						"circuit you intend to work on. <b>3.</b> Test the known live source again.</p>"
						"<p>Step 3 is the one people skip and the one that saves them. A meter with a flat "
						"battery, a blown fuse, a broken lead or a dial left on the wrong function reads "
						"zero volts on a live bus and is completely convincing while it does it. If the "
						"meter died between step 1 and step 2, step 3 is what tells you.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Voltage: AC at the supply, DC in the control loop",
					"content": (
						"<p><b>The supply side.</b> Dial to <b>AC volts</b>. When you are chasing a "
						"supply problem at a pump, a filter or the main line input of a Splash Wizard "
						"controller, measure across the terminals — line to line, or line to neutral. "
						"What you are looking for is a <i>stable</i> reading <b>within 10 percent either "
						"way of the system's specification</b>, which on our work is usually 120 VAC. "
						"Outside that band, or wandering inside it, the fault is in the supply and not "
						"in the equipment everybody has been blaming.</p>"
						"<p><b>The control side.</b> Dial to <b>DC volts</b>. The Splash Wizard's primary "
						"output is <b>24 VDC</b>, and that is what cycles the manifold solenoids. When an "
						"external activation button or a digital screen terminal is misbehaving, put the "
						"leads directly across the output relays and watch whether the 24 V appears when "
						"the controller says it should. That single measurement splits the job in half: "
						"if the voltage is there and the valve does nothing, the fault is downstream in "
						"the coil or its wiring; if the voltage never arrives, it is upstream in the "
						"controller or whatever is supposed to be commanding it.</p>"
						"<p>Both of those are live measurements, which is why they come after the two "
						"rules above and not before them.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Ohms on a coil, and the buzzer on a long run",
					"content": (
						"<p><b>Testing a solenoid coil.</b> Kill all power to the system, <b>disconnect "
						"the valve leads from the controller's terminal block</b> — that is the isolation "
						"step, and without it you are measuring the controller as well — and set the "
						"meter to ohms. Three readings, three different stories:</p>"
						"<ul>"
						"<li><b>20 to 60 Ω</b>, steady: a healthy coil. The exact figure depends on the "
						"manufacturer, so read the valve's own literature before you call a number "
						"wrong.</li>"
						"<li><b>0 Ω or near zero</b>: the internal copper windings have melted together. "
						"That is a short across a 24 VDC output, which is why it usually took the "
						"controller's circuit fuse with it — replacing the fuse without replacing the "
						"coil just blows the next fuse.</li>"
						"<li><b>OL</b> — over limit: the coil wire has snapped and the circuit path is "
						"broken. Nothing is shorted, nothing blew, and nothing happens.</li>"
						"</ul>"
						"<p><b>Tracing a run.</b> Use the continuity buzzer to follow a long wire run from "
						"a field junction box back to the control panel on the equipment pad. A buzz "
						"means the path is continuous and unbroken. Silence on a conductor that should "
						"ring is a break, and now you are looking for where — which is a much smaller "
						"question than the one you started with.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Power off is not energy gone",
					"content": (
						"<p>Capacitors hold charge after the supply is removed. Motor-start capacitors, "
						"power supplies, and above all the <b>DC bus inside a variable frequency drive</b> "
						"can sit at a voltage that will kill you long after the disconnect has been "
						"opened and the display has gone dark.</p>"
						"<p>A drive's own label and manual give the time to wait before the enclosure "
						"may be opened, and that figure belongs to the drive in front of you rather than "
						"to this course. Wait it — and then <b>verify with the meter, at the terminals "
						"the manual names</b>. The lights going out is not a measurement, and a "
						"capacitor can recover some charge after being discharged.</p>"
						"<p>The same caution applies to anything back-fed from a second source. Dead "
						"means dead at the point you are about to touch, proved by a meter you proved "
						"first — and opening a drive to prove it is work for somebody <b>qualified</b> "
						"on that equipment under NFPA 70E. That is a defined word rather than a "
						"compliment, and Module 9 unpacks it. If it is not you, the half of this that "
						"still applies is the half that keeps you out: <b>off is not discharged</b>.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What a standard meter cannot tell you",
					"content": (
						"<p>Two limits are worth knowing before you draw a conclusion from a meter.</p>"
						"<p><b>It reads insulation in one direction only.</b> Sapphire's own GFCI "
						"procedure — Lesson 6 — puts the meter on its <b>megohm (MΩ)</b> range and "
						"measures from the hot line to structural ground, and <i>a low reading there is "
						"the answer</i>: water has found a pinhole in the insulation and that cable run "
						"gets replaced. Use it, because it finds real faults in the field.</p>"
						"<p>What it cannot do is clear a cable. Insulation fails under <i>voltage</i>, and "
						"a handheld meter is asking the question with a few volts from its own battery, so "
						"a clean MΩ reading is not proof the insulation would hold at line voltage. A "
						"megohmmeter applies a high test voltage on purpose and is the instrument for "
						"that — with its own hazards, its own procedure, and a habit of destroying any "
						"electronics still connected to the circuit. So <i>the motor ohms out fine</i> is "
						"not evidence that the motor is not the ground fault tripping your GFCI.</p>"
						"<p><b>Continuity answers a smaller question than you think.</b> The beeper says "
						"there is a path. It does not say the path can carry load current. A conductor "
						"down to a few surviving strands, a corroded terminal, a contact with a film on "
						"it — all of them beep happily and then drop voltage under load. If a circuit "
						"beeps but does not work, measure the voltage at the load while it is "
						"running.</p>"
					),
				},
				ask_block(
					"Which meter, and whose measurement",
					"<p>CAT III or CAT IV is the floor, and it is Sapphire's. What sits on top of it is "
					"not: which meters we issue, how and how often they are checked, what the coil "
					"resistance is for the specific valve in your hand, and what the policy is on any "
					"energised measurement at all.</p>"
					"<p>Those are questions for your supervisor and for the equipment's own literature. "
					"If you do not know the category of the panel you are about to probe, or the band a "
					"particular coil should read in, that is the moment to ask rather than the moment to "
					"find out.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why are resistance and continuity measured only on a de-energised, isolated circuit?",
						"type": "Single Choice",
						"explanation": (
							"The meter supplies its own current for those functions. Outside voltage corrupts the "
							"reading and can destroy the meter, and a parallel path through the rest of the circuit "
							"returns a real resistance that is not the one you asked for."
						),
						"options": [
							{
								"text": "The meter supplies its own current, so outside voltage ruins the reading and a parallel path measures the wrong thing",
								"is_correct": True,
							},
							{
								"text": "Because resistance readings drift once a conductor has warmed up under load",
								"is_correct": False,
							},
							{
								"text": "Because the meter cannot display resistance and voltage at the same time",
								"is_correct": False,
							},
							{
								"text": "Because continuity is only meaningful on low-voltage control circuits",
								"is_correct": False,
							},
						],
					},
					{
						"question": "In the live-dead-live sequence, what does the final step catch?",
						"type": "Single Choice",
						"explanation": (
							"It catches the meter having failed during the check. A meter with a flat battery, a "
							"blown fuse or a broken lead reads zero volts on a live circuit, so a reading of zero "
							"only means something if the meter still works afterwards."
						),
						"options": [
							{
								"text": "That the meter itself failed part-way through, which would otherwise read as a dead circuit",
								"is_correct": True,
							},
							{
								"text": "That the circuit was re-energised by somebody while you were testing",
								"is_correct": False,
							},
							{
								"text": "That the lock-out device is correctly fitted to the disconnect",
								"is_correct": False,
							},
							{
								"text": "That the leads are in the correct jacks for a voltage measurement",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You lift a valve's leads off the controller terminal block and measure the coil on the ohms range. Which reading says the windings have melted together?",
						"type": "Single Choice",
						"explanation": (
							"Near zero ohms is a short through the melted windings — the reading that usually "
							"came with a blown controller fuse. OL means the coil wire has snapped and the path "
							"is open. A steady 20 to 60 Ω is a healthy coil, subject to the manufacturer's own "
							"figure."
						),
						"options": [
							{"text": "0 Ω, or near zero", "is_correct": True},
							{"text": "OL, over limit", "is_correct": False},
							{"text": "A steady reading between 20 and 60 Ω", "is_correct": False},
							{
								"text": "A reading that climbs for a few seconds and then settles",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true about stored energy in equipment that has been switched off?",
						"type": "Multiple Choice",
						"explanation": (
							"Capacitors and the DC bus of a drive hold charge after the supply is removed, which is "
							"why the label gives a wait time — and why the wait is followed by a verification with "
							"a meter rather than by an assumption."
						),
						"options": [
							{
								"text": "A drive DC bus and motor-start capacitors can hold a lethal charge after the disconnect is opened",
								"is_correct": True,
							},
							{
								"text": "The wait time on the equipment label is followed by verifying with a meter at the terminals the manual names",
								"is_correct": True,
							},
							{
								"text": "Opening the disconnect discharges every capacitor in the enclosure immediately",
								"is_correct": False,
							},
							{
								"text": "A dark display is a reliable indication that the drive is fully discharged",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "GFCI",
			"estimated_minutes": 20,
			"summary": "What a GFCI compares, why it trips at a current the breaker cannot see, how the bonding grid is built, and how to walk a tripping circuit back to the leak.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "It compares what goes out with what comes back",
					"content": (
						"<p>A ground-fault circuit interrupter watches the current leaving on the "
						"ungrounded conductor and the current returning on the grounded conductor. On a "
						"healthy circuit those are equal — everything that went out came back.</p>"
						"<p>If they are not equal, the difference is going somewhere else. Through wet "
						"insulation into the earth. Through a damaged fixture into the water. Through "
						"wet concrete. Through a person standing in a basin. That <b>difference</b> is "
						"the only thing the device measures, and a Class A GFCI trips on roughly "
						"<b>4 to 6 milliamps</b> of it, in milliseconds.</p>"
						"<p>That number is not arbitrary. It is set deliberately <i>below</i> the "
						"current at which a person's muscles lock and they cannot let go of what is "
						"shocking them: a few milliamps is already a painful shock, a little more takes "
						"the choice away, and more again puts the heart at risk. Meanwhile the breaker "
						"feeding that circuit is rated in <i>amps</i> — thousands of times larger. A person "
						"can be electrocuted by a current the overcurrent device will never notice, while "
						"the lights stay on and nothing looks wrong. <b>That is the entire reason this "
						"device exists.</b></p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "It protects people, not equipment — and it does not replace bonding",
					"content": (
						"<p>A GFCI does nothing to prevent a fault. It detects one that already exists and "
						"disconnects fast enough to matter. It is one layer, and it does not stand in for "
						"the others.</p>"
						"<p>Around water the NEC's <b>Article 680</b> requires <b>equipotential "
						"bonding</b>: tying the metal parts, the reinforcing steel, the shell, the "
						"handrails and the water itself together so they all sit at the same potential. "
						"The purpose is not to carry fault current away. It is to make sure there is no "
						"<i>difference</i> in voltage between two things a person can touch at the same "
						"time, because a difference is what pushes current through them.</p>"
						"<p>How far the bonding has to extend on a particular job is engineered and "
						"inspected — the engineer designs it from the article and the AHJ signs it off. "
						"What it is made of, and what it has to reach, Sapphire has written down, and "
						"that is what the rest of this lesson starts with. A GFCI on a feature with no "
						"bonding is one failure mode covered on a structure that still has another.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Bonding is not grounding, and the difference is the one that drowns people",
					"content": (
						"<p>They are two different jobs and the words get used as though they were one.</p>"
						"<p><b>Grounding</b> connects an electrical path to the earth, so that fault "
						"current has a safe route to travel and the breaker upstream sees enough of it to "
						"trip. It is about clearing a fault.</p>"
						"<p><b>Equipotential bonding</b> is a continuous mesh of heavy copper linking "
						"every single piece of metal in and around the fountain. It is not there to carry "
						"fault current anywhere. It is there so that no two things a person can touch at "
						"once are at different voltages.</p>"
						"<p>That distinction is the whole safety case, and here is the mechanism. Water "
						"and electricity go wherever there is a <b>differential</b>. If a pump shorts and "
						"leaks current into the water, a person bridging two zones that sit at different "
						"electrical potentials becomes the path between them — severe shock, loss of "
						"muscle control, and then drowning, which is why it has its own name: "
						"<b>Electric Shock Drowning</b>. Bond everything together and every one of those "
						"zones sits at the same potential, <b>0 volts relative to each other</b>. There "
						"is no differential left for a person to bridge.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "What gets bonded, and with what",
					"items": [
						"Bare, solid copper wire, no smaller than 8 AWG — that is the minimum conductor, not a target to work down to",
						"All reinforcing structural steel rebar inside the concrete shell",
						"Metallic pump frames and filter bodies",
						"Stainless steel light niches",
						"Underwater pipe penetrations",
						"Metal ladders and rails within 5 feet of the water perimeter",
						"Every mechanical structural connection beneath the water line made with a solid copper or bronze split-bolt connector",
						"No steel fasteners anywhere below the water line — they are forbidden outright, because they rust out and sever the loop",
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A severed bond has no symptom",
					"content": (
						"<p>Think about what happens the day a submerged steel fastener finally rusts "
						"through and the loop opens. Nothing stops working. No breaker trips. No light "
						"goes out. No alarm is raised, nobody is called, and the feature runs exactly as "
						"it did the day before.</p>"
						"<p>The grid is a safety system that is only ever asked to do anything on the day "
						"something else has already failed — so it can sit broken for years and look "
						"perfect. That is the entire reason the material rule is absolute rather than a "
						"preference: copper and bronze below the water line, steel never, because there "
						"is no feedback loop that would tell you when you got it wrong.</p>"
						"<p>And it is why the grid is <b>proved</b> rather than assumed. Continuity around "
						"the loop is a measurement — the buzzer in Lesson 5 — and the moment to take it "
						"is while the connections are still reachable, before anything is poured or "
						"backfilled over them. When that inspection happens on a given job, and who signs "
						"it off, belongs to the AHJ and the schedule; that it happens before the "
						"connections disappear is physics.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Test buttons, and the word nuisance",
					"content": (
						"<p>The test button creates a small deliberate imbalance inside the device and "
						"trips it. That proves the mechanism still works, which is the one thing you "
						"cannot tell by looking. Devices are tested on the schedule the manufacturer and "
						"the code set, and <b>a device that will not trip on its own test button is a "
						"dead device</b> — it is replaced, not left in service because the circuit still "
						"works.</p>"
						"<p>Now the harder half. Genuinely spurious trips do exist: long wire runs have "
						"capacitive leakage, several loads sharing one device add their leakage together, "
						"and a soaked but undamaged luminaire can push a device over the edge.</p>"
						"<p>But the word <i>nuisance</i> does a lot of work, and most of it is wishful. "
						"<b>The great majority of GFCIs that will not reset are reporting a real "
						"fault</b>: water in a fixture, water in a junction box, a conductor nicked "
						"against a coupling during a hard pull, a failing pump seal that has wet the "
						"windings, a heater element gone to ground. When a <b>Splash Wizard or a pump "
						"sub-panel</b> keeps tripping its GFCI, work the sequence below and the circuit "
						"will usually tell you which one.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Isolating a GFCI that keeps tripping",
					"items": [
						"Turn off the main breaker first — the diagnosis starts de-energised, not with the circuit live and everybody guessing",
						"Disconnect the load lines from that specific GFCI breaker, so it is feeding nothing at all",
						"Turn the breaker back on with no load on it. If it trips with nothing connected, the breaker itself is the fault and gets replaced",
						"If it holds, reconnect the lines and start disconnecting components one at a time — individual submersible lights first, then the rest of the load",
						"When the trips stop following you, the last thing you removed is where the leak is",
						"Confirm it on the meter: megohm (MΩ) range, hot line to structural ground, on the run you suspect",
						"Any low resistance hot-to-ground is water entering a pinhole in the insulation. That sub-component cable run is replaced — it is not dried out and put back",
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Do not defeat it",
					"content": (
						"<p>Replacing a GFCI with a standard device, moving the circuit to an unprotected "
						"breaker until somebody has time to look at it, or fiddling with the equipment "
						"grounding conductor to make a trip go away — all of that is disabling a "
						"life-safety device on a wet installation that the public, frequently including "
						"children, will stand in.</p>"
						"<p>It is a code violation and an OSHA matter, and it is the sort of decision "
						"people are prosecuted over after somebody is hurt. It is also, quietly, the one "
						"that gets suggested most often, because the feature is meant to run tonight.</p>"
						"<p>If a feature cannot run without disabling its GFCI protection, the feature "
						"does not run. Report it, isolate it, and let the fault be somebody's repair "
						"rather than somebody's accident.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Terms worth having straight",
					"cards": [
						{
							"front": "GFCI (Class A)",
							"back": "A device that compares outgoing and returning current and opens the circuit on a difference of roughly 4 to 6 mA. Protects people.",
						},
						{
							"front": "GFPE",
							"back": "Ground-fault protection of equipment. Trips at a much higher leakage than a Class A GFCI, to protect equipment and wiring. Not personnel protection.",
						},
						{
							"front": "Equipotential bonding",
							"back": "Tying conductive parts around the water together so there is no voltage difference between two things a person can touch at once. Bare solid copper, no smaller than 8 AWG; copper or bronze split-bolts below the water line. NEC Article 680; engineered and inspected.",
						},
						{
							"front": "Electric Shock Drowning (ESD)",
							"back": "Current leaking into the water, a person bridging two zones at different potential, muscle control lost, drowning. The failure equipotential bonding exists to make impossible.",
						},
						{
							"front": "Pinhole insulation leak",
							"back": "Water entering a cable through a small break in the insulation. Reads as a low resistance from hot to structural ground on the MΩ range, and is the usual reason a GFCI will not stay in. The cable run gets replaced.",
						},
						{
							"front": "Equipment grounding conductor",
							"back": "The path that lets fault current return and the overcurrent device operate. A different job from bonding and a different job from a GFCI.",
						},
						{
							"front": "Nuisance trip",
							"back": "A trip with no fault behind it — real, but far rarer than the phrase suggests. Assume a real fault until you have isolated the loads and proved otherwise.",
						},
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What does a GFCI actually measure?",
						"type": "Single Choice",
						"explanation": (
							"It compares the current leaving on the ungrounded conductor with the current "
							"returning on the grounded conductor. Any difference is current taking another path, "
							"possibly through a person, and that difference is what it trips on."
						),
						"options": [
							{
								"text": "The difference between current leaving on the ungrounded conductor and returning on the grounded one",
								"is_correct": True,
							},
							{"text": "The total current drawn by the circuit", "is_correct": False},
							{
								"text": "The resistance of the equipment grounding conductor",
								"is_correct": False,
							},
							{
								"text": "The voltage between the grounded conductor and ground",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why does a Class A GFCI trip at a few milliamps when the breaker feeding the same circuit is rated in amps?",
						"type": "Single Choice",
						"explanation": (
							"A current far too small for a breaker to react to can still lock a person's muscles "
							"and stop their heart. The two devices protect different things: the breaker protects "
							"the conductors, the GFCI protects people."
						),
						"options": [
							{
								"text": "Because a current far below the breaker's rating can still kill a person, so the two devices protect different things",
								"is_correct": True,
							},
							{
								"text": "Because GFCIs are built from more sensitive components than breakers and trip earlier as a side effect",
								"is_correct": False,
							},
							{
								"text": "Because leakage current damages equipment long before it reaches the breaker's rating",
								"is_correct": False,
							},
							{
								"text": "Because the GFCI has to trip before the breaker so the two do not fight each other",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are rules for the bonding grid on a Sapphire fountain?",
						"type": "Multiple Choice",
						"explanation": (
							"The conductor is bare, solid copper, no smaller than 8 AWG. Mechanical connections "
							"beneath the water line are made with solid copper or bronze split-bolt connectors, "
							"because steel fasteners rust out and sever the loop without anything appearing to go "
							"wrong — stainless and galvanised included. And nothing is exempted from bonding by "
							"the presence of a GFCI: the two do different jobs, and Article 680 asks for both."
						),
						"options": [
							{
								"text": "The conductor is bare, solid copper, no smaller than 8 AWG",
								"is_correct": True,
							},
							{
								"text": "Connections beneath the water line are made with solid copper or bronze split-bolt connectors",
								"is_correct": True,
							},
							{
								"text": "Steel fasteners are acceptable below the water line provided they are stainless or galvanised",
								"is_correct": False,
							},
							{
								"text": "Metal rails and ladders near the water are exempt once the circuit is on a GFCI",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You disconnect every load line from a GFCI breaker that keeps tripping, turn it back on, and it trips with nothing connected. What does that tell you?",
						"type": "Single Choice",
						"explanation": (
							"With no load on it there is nothing downstream to leak, so the imbalance is being "
							"produced inside the device. The breaker is faulty and gets replaced. If it had held, "
							"the next step is reconnecting and removing components one at a time."
						),
						"options": [
							{
								"text": "The breaker itself is faulty and has to be replaced",
								"is_correct": True,
							},
							{
								"text": "The fault is in the submersible lights, which are the most common cause",
								"is_correct": False,
							},
							{
								"text": "The bonding grid has been severed somewhere below the water line",
								"is_correct": False,
							},
							{
								"text": "It is a nuisance trip from capacitive leakage on the long wire runs",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "DMX systems",
			"estimated_minutes": 13,
			"summary": "How a lighting universe is wired and addressed, and why the trouble always appears at the far end of the run.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "One conversation, 512 slots, nobody answering",
					"content": (
						"<p>DMX512 is a one-way digital protocol carried on an RS-485 differential pair. "
						"The controller talks and the fixtures listen. Nothing reports back, which means "
						"the console has no idea whether a fixture received anything — a fact that "
						"explains most of the troubleshooting in this lesson.</p>"
						"<p>A universe carries <b>512 channels</b>, and the controller is broadcasting all "
						"512 values over and over, very fast. A fixture is given a <b>start address</b> "
						"and reads that many consecutive channels from the stream: a single dimmer takes "
						"one, an RGBW fixture takes four, one with dimming, strobe and effects takes "
						"more. The controller is not talking to fixtures. It is shouting numbers and each "
						"fixture is picking out the ones it was told to listen to.</p>"
						"<p><b>Differential pair</b> means the signal is carried as the difference between "
						"two wires, so noise picked up equally by both cancels out. That is why DMX runs "
						"on a twisted pair with a defined impedance and not on a spare pair in the power "
						"conduit — put the data alongside a drive's output cable and you are asking the "
						"pair to carry that drive's noise as though it were signal.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Daisy chain, and the terminator at the end",
					"content": (
						"<p>Out of the controller into the first fixture, out of the first fixture into "
						"the second, and so on to the last. A chain. <b>Not a star</b> — splitting one "
						"output with a Y so it feeds three fixtures creates branches that reflect signal "
						"back into each other. When a run genuinely has to branch or go a long way, that "
						"is what a splitter or repeater is for.</p>"
						"<p>At the very end of the chain the data pair gets a <b>120 ohm terminator</b>. "
						"It matches the impedance of the cable, so the signal that arrives at the end is "
						"absorbed there instead of bouncing back down the line and colliding with data "
						"still coming the other way.</p>"
						"<p>Leave it off and the symptoms are recognisable: fixtures flicker, jump to "
						"colours nobody asked for, or freeze holding the last value they understood. The "
						"important part is <b>where</b> it shows up. Reflection damage is worst at the far "
						"end of the run and gets worse as the chain grows, so a system that behaved "
						"perfectly until two more fixtures were added is a termination and topology "
						"story, not a fixture story. Check the terminator and the wiring before you start "
						"swapping lights.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Addressing, and what an overlap looks like",
					"content": (
						"<p>Two fixtures set to the same start address do exactly the same thing, forever, "
						"and they do it perfectly. They are not faulty. They were told to.</p>"
						"<p>A partial overlap is nastier. If one fixture starts on a channel that falls "
						"inside another fixture's block, only <i>some</i> of the channels are shared — so "
						"driving a colour change on one light makes another light do something odd on one "
						"axis and behave normally on the rest. That reads as an intermittent fault and it "
						"is arithmetic.</p>"
						"<p>Two habits prevent all of it. Keep the <b>address map</b> — the commissioning "
						"record has it, and it is the first document to open, before the first fixture "
						"comes out of the water. And write the address on the fixture somewhere a person "
						"can read it, because the person who arrives next season will not have the "
						"laptop.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "It is low-voltage data bolted to something that is not",
					"content": (
						"<p>The data pair itself is harmless. The fixture it is plugged into is submerged, "
						"line-powered or transformer-fed, and sitting in water that people stand in.</p>"
						"<p>So: <b>lock out the fixture's power before you open it</b>, even when you only "
						"came to change an address. Underwater and in-ground luminaires are inside the "
						"scope of NEC Article 680 like everything else around the basin, and their "
						"bonding, grounding, entries and splices are not relaxed because the job you came "
						"to do is a data job.</p>"
						"<p>And if you open a submersible fixture, it goes back together with the seals "
						"and gaskets the manufacturer specifies, fitted the way the instructions say. A "
						"reused, pinched or omitted gasket is a leak, then a flooded fixture, then a "
						"ground fault, in that order.</p>"
					),
				},
				ask_block(
					"The address map, the cable and the fixture mode are job data",
					"<p>How many channels a fixture consumes depends on which <b>mode or personality</b> "
					"it is set to. What cable, how long a run may be, which splitter, and which addresses "
					"belong to which fixture come from the <b>fixture manual, the lighting design and the "
					"commissioning record</b> for that site.</p>"
					"<p>Do not guess an address and do not renumber a universe to make one fixture "
					"behave. If the record does not match what is in the water, that mismatch is the "
					"finding — report it and get the map corrected rather than working around it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does the last fixture on a DMX run get a 120 ohm terminator across the data pair?",
						"type": "Single Choice",
						"explanation": (
							"It matches the impedance of the cable, so the signal is absorbed at the end instead of "
							"reflecting back down the line and colliding with data still arriving."
						),
						"options": [
							{
								"text": "It matches the cable impedance so the signal is absorbed rather than reflected back down the line",
								"is_correct": True,
							},
							{
								"text": "It tells the controller where the chain ends so it stops sending further channels",
								"is_correct": False,
							},
							{
								"text": "It limits the current in the data pair so the last fixture is not overdriven",
								"is_correct": False,
							},
							{
								"text": "It provides the return path that lets fixtures report their status back",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Two fixtures in different parts of a basin always change colour together, exactly in step. What is the most likely cause?",
						"type": "Single Choice",
						"explanation": (
							"They share a start address, so they are reading the same channels out of the broadcast "
							"and doing precisely what they were told. Nothing is faulty."
						),
						"options": [
							{"text": "They are set to the same start address", "is_correct": True},
							{
								"text": "The terminator is missing from the end of the run",
								"is_correct": False,
							},
							{"text": "The universe has run out of channels", "is_correct": False},
							{
								"text": "The controller has been left in a broadcast-to-all mode",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of DMX512?",
						"type": "Multiple Choice",
						"explanation": (
							"A universe carries 512 channels, the protocol is one-way so fixtures never report "
							"back, and fixtures are wired as a daisy chain rather than a star. Fixtures are not "
							"discovered — they are addressed by hand."
						),
						"options": [
							{"text": "A universe carries 512 channels", "is_correct": True},
							{
								"text": "It is one-way — fixtures do not report anything back to the controller",
								"is_correct": True,
							},
							{
								"text": "Fixtures are wired in a daisy chain rather than branched from one output",
								"is_correct": True,
							},
							{
								"text": "Each fixture has a serial number the controller discovers automatically",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Ladder logic and basic automation",
			"estimated_minutes": 14,
			"summary": "Read a rung, and know what a controller is doing between one scan and the next.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A rung is a sentence: if all of this, then that",
					"content": (
						"<p>Ladder logic was drawn to look like relay wiring, and it reads left to right. "
						"A rail on the left, a rail on the right, and between them a row of "
						"<b>contacts</b> — the conditions — ending in a <b>coil</b>, the thing that "
						"happens.</p>"
						"<p>If there is an unbroken path of true conditions from the left rail to the "
						"coil, the coil is on. That is the whole language. Contacts in series are AND: "
						"all of them have to be true. Contacts in parallel are OR: any one of them will "
						"do. Everything else is those two ideas stacked up.</p>"
						"<p>Reading a rung out loud is a real technique and it is worth the "
						"embarrassment: <i>if the controller is in auto, and the low-water float is "
						"satisfied, and the wind interlock has not tripped, and the start has been "
						"pressed, then the pump contactor comes on.</i> Whichever of those clauses is "
						"false is your fault, and now you know which one to go and measure.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The scan cycle, and the surprises inside it",
					"content": (
						"<p>A PLC does not watch its inputs continuously. It loops. It <b>reads every "
						"input</b> into memory, <b>solves the whole program</b> against that frozen "
						"snapshot, then <b>writes every output</b> at the end, and starts again.</p>"
						"<p>Three consequences catch people who have not been told:</p>"
						"<ul>"
						"<li><b>The program never sees an input change mid-scan.</b> A contact that "
						"pulses shorter than one scan can be missed entirely, which is why a fast "
						"pushbutton or a bouncing switch sometimes does nothing at all.</li>"
						"<li><b>An output written early is visible to later rungs immediately</b>, "
						"because it is only a bit in memory until the end of the scan. The physical "
						"output has not moved yet.</li>"
						"<li><b>Order matters.</b> If two rungs write the same coil and disagree, the "
						"last one to execute is the one that survives to the output write. The earlier "
						"rung looks correct on screen and achieves nothing. This is the classic <i>the "
						"logic is right but the output never comes on</i> bug.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The pieces you will see on a rung",
					"panels": [
						{
							"title": "Normally open contact",
							"body": (
								"<p>True when the bit it refers to is on. It is a <i>question about a "
								"bit</i> — is this on? — and not a picture of the device in the field. "
								"Getting comfortable with that distinction is most of learning to read "
								"ladder.</p>"
							),
						},
						{
							"title": "Normally closed contact",
							"body": (
								"<p>True when the bit it refers to is off. Used for conditions expressed "
								"as an absence: not in fault, not in manual, not already running.</p>"
							),
						},
						{
							"title": "Coil",
							"body": (
								"<p>Energised while the rung is true, and drops out the instant the rung "
								"goes false. That is important: a plain coil has no memory. A momentary "
								"button wired to a plain coil gives you a momentary output.</p>"
							),
						},
						{
							"title": "Seal-in, or latch",
							"body": (
								"<p>The fix for the above. Put a contact of the coil's own bit in "
								"<b>parallel</b> with the momentary start condition. Press start, the "
								"rung goes true, the coil comes on — and now its own contact holds the "
								"rung true after the button is released. A stop condition in series "
								"breaks the seal and it drops out.</p>"
								"<p>That is how every start/stop station in the world works, in relays "
								"and in software, and you will recognise it on sight once you have seen "
								"it once.</p>"
							),
						},
						{
							"title": "Timers",
							"body": (
								"<p>An on-delay timer produces its output once its input has been "
								"<b>continuously</b> true for the preset. An off-delay holds its output "
								"on for the preset after the input goes false.</p>"
								"<p>The word that matters is <i>continuously</i>. A timer that never "
								"reaches its preset is usually being reset every scan by a condition that "
								"is flickering — a bouncing switch, a sensor right on its threshold, a "
								"pressure hunting around a setpoint.</p>"
							),
						},
						{
							"title": "Counters, and what survives a power cycle",
							"body": (
								"<p>Counters accumulate. Some memory in a controller is retentive and "
								"survives a power cycle; some is not and comes back zero. Which is which "
								"decides whether a runtime total, a backwash count or a latched alarm "
								"still means anything after a storm took the site down, so it is worth "
								"knowing before you rely on a number.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A contact in the program is not a contact in the field",
					"content": (
						"<p>This is the hardest idea in the lesson and the one that causes real damage, "
						"because the two conventions multiply together.</p>"
						"<p>A low-water switch wired fail-safe holds its input <b>on</b> while the basin "
						"has water. The program then uses a normally-<b>open</b> instruction on that bit "
						"to mean <i>all is well</i>. Two separate conventions that have to agree — and if you swap "
						"<i>either</i> one without the other, the machine runs happily in exactly the "
						"condition that was supposed to stop it.</p>"
						"<p>So when a field device is replaced with one of the other contact "
						"configuration, the program has to change to match. And then you <b>prove it by "
						"making the condition happen</b> and watching the machine, not by reading the "
						"rung and being satisfied with it.</p>"
						"<p>One boundary to know: a safety function that must stop machinery is generally "
						"not permitted to depend on ordinary PLC logic. Hard-wired safety circuits exist "
						"for precisely this reason. Whether a given interlock is allowed to live in "
						"software is an engineering decision, not a convenience.</p>"
					),
				},
				ask_block(
					"Which controller, whose program, and what it is supposed to do",
					"<p>Which platform a site runs, where the master copy of its program lives, who may "
					"edit it, and what the feature is <b>supposed</b> to do are all Sapphire and project "
					"questions. The last one has a document behind it — the <b>sequence of "
					"operations</b> — and it is the thing the program is measured against.</p>"
					"<p>If the sequence and the program disagree, you have found something worth raising "
					"rather than something to quietly reconcile at the keyboard.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Two rungs in the same program write the same output coil, and they disagree. What reaches the physical output?",
						"type": "Single Choice",
						"explanation": (
							"The scan solves rungs in order and writes the outputs at the end, so the last rung to "
							"execute determines the bit that gets written. The earlier rung looks correct on screen "
							"and does nothing."
						),
						"options": [
							{
								"text": "Whatever the last rung to execute wrote, because outputs are written at the end of the scan",
								"is_correct": True,
							},
							{
								"text": "Whatever the first rung wrote, because it executed first",
								"is_correct": False,
							},
							{
								"text": "The output alternates between the two on successive scans",
								"is_correct": False,
							},
							{
								"text": "The controller faults and stops, because two rungs cannot address one coil",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What is a seal-in on a rung?",
						"type": "Single Choice",
						"explanation": (
							"A contact of the output's own bit placed in parallel with the momentary start "
							"condition. Once the coil is on, its own contact keeps the rung true after the button "
							"is released, until a stop condition in series breaks it."
						),
						"options": [
							{
								"text": "A contact of the output placed in parallel with the start condition, so the rung stays true after the button is released",
								"is_correct": True,
							},
							{
								"text": "A timer that holds an output on for a preset time after the input goes false",
								"is_correct": False,
							},
							{
								"text": "A normally-closed contact in series that prevents the rung ever being true twice",
								"is_correct": False,
							},
							{
								"text": "A second coil that copies the first one so the output is not lost on a power cycle",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A normally-closed contact in a ladder program is the same thing as a normally-closed contact on the field device.",
						"type": "True-False",
						"explanation": (
							"They are separate conventions that multiply. The instruction asks a question about a "
							"bit in memory; the device describes a contact at rest. Change one without the other "
							"and the machine runs in the condition that was meant to stop it."
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
			"lesson_title": "Program troubleshooting",
			"estimated_minutes": 16,
			"summary": "Find out what the program believes before changing it, change one thing at a time, and know what the module asks you to demonstrate at the end.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The machine is telling the truth",
					"content": (
						"<p>Start from what the equipment is actually doing and work backwards to why the "
						"program thinks that is correct. Almost every call that arrives as <i>the program "
						"is broken</i> turns out to be one of four things, and only the last one is the "
						"program.</p>"
						"<ul>"
						"<li><b>An input that is not arriving.</b> The sensor, its wiring, its power "
						"supply, a blown fuse on the input card, a terminal that backed out.</li>"
						"<li><b>An output that arrives but does not act.</b> The controller turned it on; "
						"a relay did not pick up, a contactor coil is open, an overload has tripped, a "
						"disconnect is off, somebody left a selector in hand.</li>"
						"<li><b>A condition that is genuinely false</b> because something upstream is "
						"wrong — no flow, a level below setpoint, a fault bit that has not been "
						"cleared.</li>"
						"<li><b>A program that is wrong</b>, which happens, and which is far more likely "
						"when something was changed recently.</li>"
						"</ul>"
						"<p>The one worth memorising: <b>an output that will not come on is most often an "
						"input that is not arriving.</b></p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Monitor before you force, force before you edit",
					"content": (
						"<p>Go online and watch the program run. The software shows you which contacts are "
						"true and which rungs are solving, live, while the machine does whatever it is "
						"doing. That is the whole diagnosis: follow the rung until the chain of true "
						"conditions dies, and you have converted <i>it doesn't work</i> into <i>rung "
						"fourteen wants the flow switch and that bit is off</i>.</p>"
						"<p>At which point it is not a program problem at all. It is a wiring and field "
						"question with a meter-shaped answer, and you can go and measure it.</p>"
						"<p><b>Forcing</b> — overriding an input or an output — is a diagnostic instrument "
						"with a hazard attached. It makes the controller act on something that is not "
						"true. It starts pumps. It satisfies the exact interlocks that exist to stop "
						"things happening. Force with people clear and the equipment in a state where "
						"movement is safe, force one thing at a time, and <b>remove every force before "
						"you leave</b>. A forgotten force is a machine that will run with an interlock "
						"permanently satisfied, long after everyone has forgotten it is there.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Take a copy of the working program before you touch anything",
					"content": (
						"<p>Upload what is actually in the controller and save it with a name and a date "
						"<b>before the first edit</b>. Without that, undo is your memory of what the rung "
						"used to say.</p>"
						"<p>And do not assume the file on somebody's laptop is that copy. On a site that "
						"has been running for years, the version in the controller very often contains a "
						"field change that was never sent back to anybody — so the laptop file is a "
						"different program, and restoring it wipes out whatever that change was doing.</p>"
						"<p>The other half of the warning: an online edit acts on live equipment the moment "
						"it is accepted or downloaded, and staging an edit is not the same as testing it — "
						"nothing simulates what the machine will do. Before you download or "
						"accept an edit, know what is going to move.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "One change at a time, and write down what you did",
					"content": (
						"<p>Make one change. Test it. Keep it or put it back. Then make the next one.</p>"
						"<p>Two changes at once and the information is gone in every direction: if it "
						"works you do not know which change fixed it, if it still fails you do not know "
						"which one to keep, and if it now fails <i>differently</i> you have two suspects "
						"and no baseline. The discipline feels slow for about twenty minutes and then "
						"saves the afternoon.</p>"
						"<p>Write down what you changed and why — in the program as a comment, and on the "
						"job record. The person who opens this program next is very often you, a year "
						"later, in the rain, with no memory of the reasoning that seemed obvious at the "
						"time.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you leave a controller you have been in",
					"items": [
						"Every force has been removed and the forces list is empty",
						"The controller is back in its normal run mode, not in program or test",
						"Selector switches, hand-off-auto and bypass switches are back where they belong",
						"The as-found program was saved before the first edit, and the as-left program is saved too",
						"What you changed and why is written down where the next person will find it",
						"The feature has been watched through a real cycle, not just started",
						"Anything you could not resolve is reported rather than left as a surprise",
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "The end of the module, and what it actually asks you to prove",
					"content": (
						"<p>That is the last lesson. Reading it is not the qualification — the module is "
						"signed off on six things done in front of a <b>Lead Installer</b>, with the "
						"equipment in your hands, and every one of them is a task somebody has to be "
						"trusted to do alone at some point.</p>"
						"<p>Notice what they have in common. Each one is a measurement or a physical "
						"result that can be observed, not an opinion about whether you understood the "
						"lesson. A coil either reads what a healthy coil reads or it does not; a pour is "
						"either void-free or it is not.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Module 6 sign-off — demonstrate these to a Lead Installer",
					"items": [
						"Safely measure line voltage across an active 120/240 VAC supply panel, using a meter and leads of the correct CAT rating",
						"Test a dead solenoid coil and diagnose its condition from the resistance reading",
						"Isolate a broken control path along a 100-foot underground wire run using the meter's continuity chime",
						"Construct a standard multi-point bonding mesh loop on a mock rebar grid in solid 8 AWG bare copper wire",
						"Systematically isolate a simulated current leak to fix a tripping commercial GFCI breaker loop",
						"Mix, pour and achieve a flawless, void-free terminal encapsulation inside a lighting potting box",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "An output will not come on and the program looks correct. What is the most likely cause?",
						"type": "Single Choice",
						"explanation": (
							"An output that will not come on is most often an input condition that is not arriving "
							"— the sensor, its wiring, its supply or a fuse. Monitoring the rung shows which "
							"condition is false."
						),
						"options": [
							{
								"text": "An input condition that is not arriving at the controller",
								"is_correct": True,
							},
							{
								"text": "A corrupted program that needs to be restored from the laptop copy",
								"is_correct": False,
							},
							{
								"text": "A scan cycle running too slowly to energise the output",
								"is_correct": False,
							},
							{
								"text": "A controller that needs to be power cycled to clear its memory",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true about forcing an input or output while troubleshooting?",
						"type": "Multiple Choice",
						"explanation": (
							"A force makes the controller act on something that is not true, which moves real "
							"equipment and satisfies real interlocks. Forces do not clear themselves, so every one "
							"has to be removed deliberately before you leave."
						),
						"options": [
							{
								"text": "It makes the controller act on a condition that is not actually true, so equipment can move",
								"is_correct": True,
							},
							{
								"text": "Every force must be removed deliberately before leaving the site",
								"is_correct": True,
							},
							{
								"text": "It is inherently safe because it only affects software and not the outputs",
								"is_correct": False,
							},
							{
								"text": "Forces clear themselves automatically when the controller is put back into run",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The program file on the laptop can be assumed to match what is running in the controller.",
						"type": "True-False",
						"explanation": (
							"Field changes are frequently made in the controller and never sent back. Upload and "
							"save what is actually running before editing, or a restore will silently delete "
							"somebody's change."
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
