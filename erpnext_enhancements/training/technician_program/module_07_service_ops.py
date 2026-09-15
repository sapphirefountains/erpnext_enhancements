# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 7 — Service Operations.

Built from Sapphire's own document, *Module 7: Service Operations & Safety Protocols*. Its
lesson 7.1 (vacuuming) is the first lesson here and its lesson 7.4 splits into the other two,
winterization and spring commissioning. Its lesson 7.3 — confined space entry and
lock-out/tag-out — belongs to Module 9 and is written there; its lesson 7.2 on reading P&IDs
and structural drawings has no lesson in this module's frozen shape, and the schematic-reading
material lives in Module 10.

Where that document gives a figure it is used as written: 9% expansion and up to 114,000 PSI of
bursting force, the blow-out at under 15 PSI with high CFM, the plugs stored in the pump basket,
marine-grade propylene glycol into the sub-grade p-traps. Those replace what this course
previously deferred on or guessed at.

The document's closing *Technical Performance Checklist* — six field skills demonstrated to a
Lead Installer before a technician graduates Module 7 — is carried whole at the end of the last
lesson, including the three whose lessons live in Modules 9 and 10. It is Module 7's requirement
wherever the teaching sits, and dropping the three items this module happens not to teach would
lose a rule the document states.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 7 — Service Operations",
		"summary": (
			"Prime and run the vacuum rigs that lift fine sediment out of a basin without clouding "
			"the water you came to clear, close a system down so that nothing anywhere is holding "
			"water when it freezes, and open it again in spring with every plug back in, the air "
			"bled out, the chemistry balanced and a clean set of baseline readings written down."
		),
		"category": "Service & Maintenance",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Pool vacuuming operations",
			"estimated_minutes": 16,
			"summary": "Priming a portable pump the way Sapphire primes one, when to reach for a venturi hydro-vac instead, and why the technique is slowness.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "Whatever is pulling, your hose is a suction line",
					"content": (
						"<p>Three things can supply the suction when you vacuum a commercial basin, and "
						"Sapphire's document is mostly about the first two.</p>"
						"<ul>"
						"<li><b>A portable external vacuum pump</b> standing on the deck, with its own motor "
						"and its own discharge. This is the workhorse.</li>"
						"<li><b>A venturi hydro-vac</b> — a 'Jet Vac' — which has no motor at all and is "
						"driven by a high-pressure garden hose.</li>"
						"<li><b>The feature's own circulation pump</b>, through a vacuum point or a skimmer, "
						"on systems plumbed for it.</li>"
						"</ul>"
						"<p>Where a pump is doing the pulling, <b>your hose is part of its suction line</b>. "
						"Everything that pump suffers from on the suction side it now suffers from through "
						"however many feet of flexible hose you are dragging around a basin. That is the fact "
						"the whole priming procedure exists to serve.</p>"
						"<p>Empty the pump basket first, and the skimmer basket, and look at both again part "
						"way through. A vacuum moves more debris in ten minutes than the system sees in a "
						"week, and a full basket starves the pump exactly when you have given it the most "
						"work to do.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Priming a portable vacuum pump, in order",
					"content": (
						"<p>The rule under all of it: <b>the suction hose must be completely filled with "
						"water before the motor is turned on.</b> Sapphire's procedure gets it there in five "
						"steps, and the order is the whole of it.</p>"
						"<ol>"
						"<li>Attach the vacuum head to the telescopic pole and connect the vacuum hose.</li>"
						"<li>Submerge the vacuum head into the fountain basin.</li>"
						"<li>Take the free end of the hose and <b>hold it tightly against a return wall inlet "
						"nozzle</b>, forcing water through the hose and purging the air out of it. Keep it "
						"there until <b>the bubbles stop coming out of the vacuum head</b> — that is the "
						"signal, not a count of seconds.</li>"
						"<li><b>Quickly</b> plug the primed hose end into the portable pump's suction intake. "
						"Whatever air you let back in during that handover is air you will be chasing.</li>"
						"<li>Start the pump.</li>"
						"</ol>"
						"<p>The bubbles are the instrument here. While air is still coming out of the head, "
						"there is still air in the hose, and a hose that goes onto the intake with air in it "
						"hands the pump a slug of it the length of the run.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Air is what damages the pump, not dirt",
					"content": (
						"<p>If air enters the line the pump loses prime, and then <b>the impeller spins "
						"uselessly and risks heat damage</b>. A centrifugal pump is cooled and lubricated by "
						"the water going through it; with nothing going through it, the mechanical seal is "
						"running dry against itself and does not survive that for long. A dry seal can be "
						"ruined well before you have finished sorting out whatever caused it.</p>"
						"<p>So: do not lift the vacuum head out of the water while the pump is drawing on it, "
						"do not let the hose end break the surface, and if you hear the pump change note or "
						"see the basket go half empty, <b>stop the pump first</b> and sort it out after. The "
						"instinct is to keep working and fix it as you go. That instinct costs a seal.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The venturi hydro-vac, for the ones that clog a pump",
					"content": (
						"<p>Deep architectural vaults and features carrying heavy sediment clog standard "
						"vacuum pumps. For those, use a <b>venturi vacuum</b> — a 'Jet Vac'. It connects to a "
						"high-pressure garden hose and has no moving parts.</p>"
						"<p>The incoming water stream creates a localised low-pressure vacuum vortex at the "
						"base of the vacuum head. That is the <b>Venturi effect</b>, and it is doing the same "
						"job an impeller does, without being one. Leaves and sand are pulled straight up into "
						"an attached <b>mesh silt bag</b>.</p>"
						"<p>Note what that changes. The debris <b>never passes through a mechanical pump "
						"impeller</b> — so there is nothing for a stone to jam, nothing for a leaf mat to "
						"block, and no prime to lose. It also never reaches the feature's filter, because it "
						"leaves in the bag. Heavy sediment that would stop the portable rig in a minute is "
						"the exact case this tool is for.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "To waste, or through the filter",
					"content": (
						"<p>When the feature's own circulation pump is doing the vacuuming, you have a "
						"further choice to make, and it is about where the dirt ends up.</p>"
						"<p>Vacuuming through the filter is the normal case. The debris is caught, the water "
						"goes back to the basin, and nothing is lost except a little filter capacity.</p>"
						"<p><b>Fine material changes the answer.</b> Silt and the grey cloud left behind after "
						"an algae treatment are made of particles small enough to pass straight through a sand "
						"bed and come back to the basin, or fine enough to blind a cartridge so completely "
						"that you have bought yourself a cartridge clean instead of a vacuum. Either way the "
						"dirt is still in the system. Those go <b>to waste</b>, where the water and everything "
						"in it leaves the system entirely and never touches the filter — or they go up into a "
						"hydro-vac's silt bag, which is the same idea by a different route.</p>"
						"<p>Waste is a position on a multiport valve, or a dedicated drain valve. <b>Stop the "
						"pump before you move a multiport handle.</b> The handle drives a diverter against a "
						"gasket, and turning it against full pump pressure is how that gasket gets torn — "
						"after which the valve leaks internally in every position and the next person spends "
						"a morning chasing a filter that will not hold pressure.</p>"
						"<p>Not every system can vacuum to waste. A cartridge filter with no multiport has "
						"nowhere to send it. Then it is either through the filter with a clean afterwards, or "
						"one of the two portable rigs, which discharge outside the system by design.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "To waste means water is leaving the site",
					"content": (
						"<p>Two consequences, and both of them catch people out.</p>"
						"<p><b>The level falls while you work.</b> Keep an eye on it. If it drops to the mouth "
						"of the skimmer, the skimmer starts swallowing air and the pump loses prime — you have "
						"caused the exact failure the lesson above is about, by doing something unrelated. Top "
						"up as you go, or work in passes and refill between them.</p>"
						"<p><b>The water has to go somewhere it is allowed to go.</b> Treated water carries "
						"sanitiser, pH and whatever you just killed, and where it may be discharged — sanitary "
						"sewer, a specific drain, never a storm drain or a planting bed in many jurisdictions "
						"— is set by local rule and by the site. Find out before you open the valve, not "
						"after.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Slow is the technique, and the gauges are the feedback",
					"content": (
						"<p>The job is to clear the fine sediment <b>without clouding the water column</b>, "
						"and speed is what stops you doing that. Everything on the floor of a basin worth "
						"vacuuming is there because it settled. Move the head quickly and you do not pick it "
						"up, you <b>launch it</b> — it goes back into suspension, hangs there while you work, "
						"and settles again on the part of the floor you already cleaned. Then the water is "
						"cloudy, so you cannot see what is left, and you vacuum the same square foot "
						"twice.</p>"
						"<p>Long, slow, overlapping passes. Work so that you are retreating from the clean "
						"area rather than walking back through it, and keep the head on the floor rather than "
						"skipping it along.</p>"
						"<p>While you work, the gauges Module 2 covers are telling you what the system is "
						"experiencing. A <b>rising vacuum reading</b> on the suction side is a restriction "
						"ahead of the pump: a loading basket, a blocked hose, or the head sitting on a leaf. A "
						"<b>rising filter pressure</b> is the filter itself loading up with what you are "
						"sending it. They mean different things and they call for different actions — clear "
						"the restriction, or stop and clean the filter.</p>"
					),
				},
				ask_block(
					"How often, and where the waste water goes, are site answers",
					"<p>How frequently a given feature is vacuumed, whether that is part of a "
					"maintenance contract or a separate visit, and what the client expects to see "
					"afterwards are all commercial decisions rather than technical ones.</p>"
					"<p>So is the discharge point. Every site has one legal answer and several "
					"convenient wrong ones, and it depends on the jurisdiction and the plumbing that "
					"was installed. Get it from the site file or the supervisor before the first time "
					"you vacuum a feature to waste, and if nobody knows, that is the question to "
					"ask.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "You are priming a portable vacuum pump on the deck. When is the hose ready to plug into the pump's suction intake?",
						"type": "Single Choice",
						"explanation": (
							"The suction hose must be completely filled with water before the motor is turned on. "
							"Holding the free end against a return wall inlet nozzle forces water through it, and "
							"the bubbles stopping at the vacuum head is the signal that the air is out. Then plug "
							"it in quickly and start the pump — air in the line means lost prime, and the impeller "
							"spins uselessly and risks heat damage."
						),
						"options": [
							{
								"text": "When the free end has been held against a return wall inlet nozzle long enough that the bubbles stop coming out of the vacuum head",
								"is_correct": True,
							},
							{
								"text": "As soon as the vacuum head is submerged and the pole is extended",
								"is_correct": False,
							},
							{
								"text": "Once the pump has been started and has pulled the water through the hose itself",
								"is_correct": False,
							},
							{
								"text": "When the hose has been coiled on the deck and drained of the last job's dirt",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A deep architectural vault is full of leaves and sand, and standard vacuum pumps keep clogging on it. What does a venturi hydro-vac do differently?",
						"type": "Single Choice",
						"explanation": (
							"A high-pressure garden hose feeds the head, and the incoming stream creates a "
							"localised low-pressure vortex at its base — the Venturi effect. Leaves and sand go "
							"straight up into an attached mesh silt bag without passing through a mechanical pump "
							"impeller, which is why heavy sediment that stops a pump does not stop this."
						),
						"options": [
							{
								"text": "A high-pressure hose supply creates a low-pressure vortex at the head, lifting debris into a mesh silt bag without it passing through any impeller",
								"is_correct": True,
							},
							{
								"text": "It runs a larger impeller, so bigger debris passes through the pump without jamming it",
								"is_correct": False,
							},
							{
								"text": "It grinds leaves and sand fine enough for the filter to catch them",
								"is_correct": False,
							},
							{
								"text": "It reverses the circulation pump so the debris is pushed out through the return lines",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A basin has a grey settled film of fine silt left from an algae treatment. Which routes keep those fines out of the filter?",
						"type": "Multiple Choice",
						"explanation": (
							"Particles that fine pass straight through a sand bed and come back to the basin, or "
							"blind a cartridge. Waste sends the water and everything in it out of the system; a "
							"hydro-vac's mesh silt bag holds them without their touching a pump or a filter at all."
						),
						"options": [
							{
								"text": "To waste, where the water and everything in it leaves the system without touching the filter",
								"is_correct": True,
							},
							{
								"text": "A venturi hydro-vac, which lifts the fines into its own mesh silt bag",
								"is_correct": True,
							},
							{
								"text": "Through the filter — catching material is what the filter is there for",
								"is_correct": False,
							},
							{
								"text": "Through the filter, then backwash straight afterwards",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why are vacuum passes made slowly and with an overlap?",
						"type": "Single Choice",
						"explanation": (
							"The objective is to lift fine sediment without clouding the water column. Settled "
							"material is picked up by slow suction and simply relaunched by a fast head: it "
							"re-suspends, clouds the water so you cannot see what is left, and settles again on "
							"floor you already cleaned."
						),
						"options": [
							{
								"text": "Moving quickly stirs settled material back into suspension, where it clouds the water and resettles behind you",
								"is_correct": True,
							},
							{
								"text": "It reduces wear on the brushes of the vacuum head",
								"is_correct": False,
							},
							{
								"text": "Moving quickly can exceed the pump's rated flow and damage the impeller",
								"is_correct": False,
							},
							{
								"text": "It stops the hose kinking against the wall of the basin",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Seasonal winterization",
			"estimated_minutes": 17,
			"summary": "One fact drives the whole job: trapped water splits whatever is holding it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The whole job is one fact",
					"content": (
						"<p>Water expands by roughly <b>9% in volume</b> when it freezes, and inside a locked "
						"plumbing circuit that expansion exerts up to <b>114,000 PSI</b> of bursting force. "
						"Nothing about a pipe wall, a pump volute, a heater coil or a filter body is going to "
						"talk it out of that.</p>"
						"<p>So freezing is only a problem where water is <b>trapped</b> — held in a closed "
						"space with nowhere to expand into. Ice in an open basin at least has a free surface "
						"to rise into, though it also pushes outward against walls, coping and tile as it "
						"grows, which is why a basin gets drained rather than left full. Water in a capped "
						"line, a drained-looking pump with its plug still in, or the low spot of a sagged run "
						"has nowhere to go at all, and the thing around it splits.</p>"
						"<p>That reframes the job. Winterizing is not a list of products you add. It is one "
						"goal — <b>there is no trapped water anywhere in this system</b> — and every item on "
						"the list exists to serve it. Judge any step you are unsure of against that "
						"sentence.</p>"
						"<p>It is also the definition of work whose mistakes are invisible. A missed low-point "
						"drain looks exactly like a done one in November. You find out in April, when the "
						"system is filled and something that was whole in the autumn is now pouring water into "
						"the ground.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The purge, and where water hides",
					"content": (
						"<p><b>Drain everything that holds water, all the way down.</b> The fountain basin, "
						"the splash pad deck lines and the balance holding tanks all go down into the "
						"municipal waste system — completely, not to a level that looks low.</p>"
						"<p>Then <b>open all the low-point manual drain plugs</b>: pump strainers, filter "
						"bodies, heater coils and UV chambers. Leave them open and leave them out — a drain "
						"valve closed for the winter is a trap, not a drain — and open valves generally, so "
						"that any water left behind has somewhere to expand to rather than being shut into a "
						"length of pipe between two closed valves.</p>"
						"<p><b>And a line with a belly in it cannot be gravity drained at all.</b> This is "
						"Module 1's pitch lesson arriving with a bill. Gravity empties a line that falls "
						"continuously to its drain point; it does not empty a line that falls, rises, and "
						"falls again. The water in that sag stays there no matter how long you leave the valve "
						"open, and the sag is precisely where the split will be. If the same run splits in the "
						"same place two winters running, that is a grade problem being reported as a "
						"winterizing problem. Say so, so somebody can fix the actual thing.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Blowing the lines out: volume, not pressure",
					"content": (
						"<p>Gravity does not finish the job, so the lines get blown clear. Hook a high-volume "
						"industrial air compressor or blower to the <b>main manifold test ports</b>, and blow "
						"air through the lines at <b>low pressure — under 15 PSI — but high CFM volume</b>.</p>"
						"<p>Read that pairing carefully, because it is the whole method. <b>Volume moves the "
						"water; pressure only stores energy in the pipe.</b> A big compressor turned up is not "
						"a better blow-out, it is a more dangerous one.</p>"
						"<p>The finish line is not a time and not a gauge. Keep going until <b>a clean, "
						"mist-free stream of air blows out of every field nozzle tip</b>. Mist in the stream "
						"means water is still being carried out of that line, and a line still carrying water "
						"is a line you have not finished.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Blowing a line clear is not a pressure test",
					"content": (
						"<p>Module 1 says never pressure test plastic pipe with air, and that rule has not "
						"moved. Clearing a line for winter looks similar and is a different operation, and the "
						"difference is what keeps it safe.</p>"
						"<p>You are <b>moving water out of an open line</b>, not building and holding a "
						"pressure in a closed one. The far end stays open. Nothing is capped. Pressure cannot "
						"be built up and held the way a capped line holds it — it bleeds out of the open end, "
						"and the under-15-PSI setting means there was never much of it to store. It is not "
						"zero: air is compressed behind the water until the line clears, and that stored "
						"energy is the entire reason compressed air in plastic pipe is lethal. Stay out of the "
						"line of that open end all the same — it throws water, grit and whatever else was in "
						"the pipe.</p>"
						"<p>The moment somebody caps the end to 'push harder', it has become a pneumatic "
						"pressure test on plastic pipe. Stop.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Pull the plugs, and put them in the pump basket",
					"content": (
						"<p>Pumps, filters, heaters and UV chambers all carry drain plugs, and the volute of a "
						"pump holds water below the level of any pipe connected to it. A plug left in is a "
						"full housing, and a full housing is a split housing. Get them all out — the pump, the "
						"strainer pot, the filter body, the heater coil, the UV chamber, sight glasses, any "
						"low-point fitting with a plug in it.</p>"
						"<p>Then think about April. <b>A drain plug that has been lost is a start-up that "
						"cannot happen</b>, and the person opening the site may well not be the person who "
						"closed it. Sapphire's answer is specific and it is not 'wherever this site does it': "
						"<b>store the plugs safely inside the pump basket</b>. They are then at the equipment "
						"they belong to, in the one container the spring crew opens first. Write down that you "
						"did it. A plug in your truck is a plug that is gone.</p>"
						"<p>Anything that cannot be drained comes off the system entirely and gets stored "
						"where it will not freeze: nozzles and jets that hold water, chemical feeders, probes "
						"and sensors, and whatever the design says is removable. Most of that stores dry — but "
						"a <b>digital pH or ORP probe is the exception</b>. Those come indoors for the winter "
						"in <b>wet storage caps filled with reference solution</b>, because a dry probe is "
						"instantly ruined: the glass membrane of a pH probe and the reference junction of an "
						"ORP probe have to stay wet. Not a bucket, not a toolbox, not a rinse and a box.</p>"
						"<p>Covers and protection go on <b>last</b>, once the draining is genuinely finished — "
						"a cover over an undrained basin is a lid on the problem, not a solution to it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Propylene glycol, and never the automotive one",
					"content": (
						"<p>Draining and blowing out cannot reach a p-trap. So pour <b>marine-grade, non-toxic "
						"propylene glycol antifreeze</b> down into all sub-grade p-traps and perimeter drains, "
						"to stop the residual puddles that live there from freezing.</p>"
						"<p><b>Never use automotive ethylene glycol.</b> It is poisonous, and in the spring it "
						"contaminates the aquatic ecosystem you are about to fill and hand back. There is no "
						"version of this where the jug in the truck is close enough.</p>"
						"<p>Antifreeze is for the places that cannot be fully drained. It is never a substitute "
						"for draining what can be drained, and pouring it into a line you did not blow out "
						"buys you nothing.</p>"
					),
				},
				ask_block(
					"How far a site is closed is Sapphire's call, site by site",
					"<p>Whether a given feature is fully closed, left running through the winter, or "
					"something in between is a decision about that site and that client, and it changes "
					"which of these steps apply. Get the site's winterizing scope before you start "
					"pulling plugs.</p>"
					"<p>The same goes for any quantity: how much glycol a particular trap or drain "
					"takes, and any place beyond the p-traps and perimeter drains the design accepts "
					"cannot be drained, belong to the system designer and the manufacturer's "
					"instructions rather than to habit.</p>",
				),
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Isolate the power before you leave",
					"content": (
						"<p>A drained system with live power is a destroyed system waiting for a timer to come "
						"round. A pump that starts against an empty volute is running dry from the first "
						"revolution. A heater that fires with no water in it is worse than that, and on a gas "
						"appliance it is a safety event rather than a repair bill.</p>"
						"<p>Kill the power at the breaker for everything that must not run, and where the work "
						"requires it, apply <b>lock-out/tag-out</b> under the employer's own written program "
						"rather than an informal agreement that nobody will touch it. Module 9 carries the "
						"procedure itself. Anything beyond opening a breaker you are authorised to open — "
						"pulling fuses, working inside a panel, disconnecting equipment — is qualified "
						"electrical work, and that is a person, not a task.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "The winterizing pass",
					"items": [
						"The site's winterizing scope is known before anything is opened",
						"Basin, splash pad deck lines and balance holding tanks drained completely to the municipal waste system",
						"Low-point manual drain plugs opened on pump strainers, filter bodies, heater coils and UV chambers",
						"Plugs left out, valves left open, nothing shut between two closed valves",
						"Compressor or blower on the main manifold test ports, under 15 PSI and high CFM",
						"Blow-out run until a clean, mist-free stream of air leaves every field nozzle tip",
						"Known sags and bellies given extra attention, and reported if they recur",
						"Plugs stored safely inside the pump basket, and the fact recorded",
						"Nozzles, feeders and anything undrainable removed and stored out of the frost",
						"pH and ORP probes indoors in wet storage caps filled with reference solution",
						"Marine-grade non-toxic propylene glycol in all sub-grade p-traps and perimeter drains",
						"No automotive ethylene glycol anywhere near the feature",
						"Power isolated, and locked out where the program requires it",
						"Covers and protection fitted last, after the draining is finished",
						"What you did — and anything you could not do — written on the record",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A drain plug is left in the pump over the winter. What is the consequence?",
						"type": "Single Choice",
						"explanation": (
							"The volute holds water below the level of the pipes connected to it, so that water is "
							"trapped. It expands roughly 9% in volume as it freezes, exerting up to 114,000 PSI of "
							"bursting force inside a locked circuit, and the housing splits."
						),
						"options": [
							{
								"text": "The volute stays full, and the trapped water splits the housing when it freezes",
								"is_correct": True,
							},
							{
								"text": "Nothing physical — the pump simply takes longer to prime in spring",
								"is_correct": False,
							},
							{
								"text": "The volute drains back through the suction pipe as the basin empties, so only the strainer pot is at risk",
								"is_correct": False,
							},
							{
								"text": "The seal dries out, which is a wear item rather than damage",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The winter blow-out on the main manifold test ports. Which of these describe how it is done?",
						"type": "Multiple Choice",
						"explanation": (
							"Low pressure — under 15 PSI — with high CFM volume doing the work, until a clean, "
							"mist-free stream of air leaves every field nozzle tip. Volume moves water; pressure only "
							"stores energy in the pipe. The far end stays open and nothing is capped, which is what "
							"keeps this from being the pneumatic pressure test Module 1 forbids."
						),
						"options": [
							{
								"text": "Low pressure — under 15 PSI — with a high-CFM compressor or blower doing the work by volume",
								"is_correct": True,
							},
							{
								"text": "It is finished when a clean, mist-free stream of air comes out of every field nozzle tip",
								"is_correct": True,
							},
							{
								"text": "The far end is capped so the air pushes the water out harder",
								"is_correct": False,
							},
							{
								"text": "The line is held at pressure and the gauge watched, the way a hydrostatic test is read",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Where do the drain plugs go once they are out?",
						"type": "Single Choice",
						"explanation": (
							"Sapphire's answer is to store them safely inside the pump basket. They stay out all "
							"winter, and they sit at the equipment they belong to, in the container the spring crew "
							"opens first. A plug nobody can find stops a start-up dead; a plug put back finger-tight "
							"leaves a full housing to freeze."
						),
						"options": [
							{
								"text": "Inside the pump basket, where the spring crew will find them",
								"is_correct": True,
							},
							{
								"text": "In the technician's truck, where they cannot be knocked into the pit",
								"is_correct": False,
							},
							{
								"text": "Back in their housings finger-tight, so they cannot be lost",
								"is_correct": False,
							},
							{
								"text": "They are consumables; new ones get fitted at start-up",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which antifreeze goes into a feature's sub-grade p-traps and perimeter drains?",
						"type": "Single Choice",
						"explanation": (
							"Marine-grade, non-toxic propylene glycol, poured into all sub-grade p-traps and "
							"perimeter drains so residual puddles cannot freeze. Automotive antifreeze is ethylene "
							"glycol: poisonous, and it contaminates the aquatic ecosystem in the spring."
						),
						"options": [
							{
								"text": "Marine-grade non-toxic propylene glycol, so residual puddles cannot freeze without poisoning the water in spring",
								"is_correct": True,
							},
							{
								"text": "Automotive ethylene glycol, because it has the lowest freezing point",
								"is_correct": False,
							},
							{
								"text": "Whatever antifreeze is on the truck, as long as the lines were drained first",
								"is_correct": False,
							},
							{
								"text": "None — a trap that has been blown out with air does not need any",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Seasonal start-up and commissioning",
			"estimated_minutes": 20,
			"summary": "Sapphire's spring workflow in order, and the baselines the rest of the year is measured against.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "It is not the system that was running in October",
					"content": (
						"<p>Six months passed and nobody was watching. Something froze, or something nearly "
						"did. A gasket took a set and dried out. Rodents got into an enclosure. Sunlight "
						"worked on anything exposed. A fastener let go. Somebody used the empty basin as a "
						"place to put things. And there is a handful of drain plugs sitting in the pump "
						"basket that have to go back in before water arrives.</p>"
						"<p>So a start-up does not begin at a switch. It begins with a walk, with nothing "
						"energised and nothing filled, looking for what changed. Freeze damage hides in the "
						"same handful of places: the pump volute and strainer pot, the heater coil, the filter "
						"body, the UV chamber, valve bodies, and the low spot of any run that was known to "
						"sag.</p>"
						"<p>Cross-check the plugs in the basket against what the winterizing record says was "
						"pulled. That record is the only reliable inventory of what is currently missing from "
						"this system, and a filter body filled with its drain plug still in the basket is a "
						"wet pump room.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Nothing runs dry, and nothing fires without flow",
					"content": (
						"<p><b>Fill, and prime by hand, before any pump runs.</b> The hair-and-lint pots get "
						"primed manually and every isolation valve gets opened before the start-up sequence is "
						"executed. A pump started against an empty or half-empty casing is running its "
						"mechanical seal dry, and that is a repair, not an inconvenience.</p>"
						"<p><b>Prove flow before a heater is allowed to fire.</b> Heat going into an exchanger "
						"with no water moving through it has nowhere to go: it boils what is standing in there, "
						"and it cracks the exchanger. On a gas appliance that is a safety event and not just an "
						"expensive one — and the gas side itself, the gas train, the combustion setup, a pilot "
						"that will not stay lit, is not yours to adjust. That is a qualified gas "
						"technician.</p>"
						"<p>The heater's flow switch is a <b>backstop</b>, not your proof. Field-bodged or "
						"stuck flow switches are common enough that assuming one works is assuming the thing "
						"most likely to have been defeated. You prove flow; the switch is there for the day you "
						"are wrong.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The spring commissioning workflow, in order",
					"panels": [
						{
							"title": "1. Walk it dry, then put the plugs back",
							"body": (
								"<p>Nothing energised, nothing filled. Look for splits and weeping at the pump, "
								"heater coil, filter, UV chamber, valve bodies and known low spots, and check "
								"gaskets and O-rings — they take a set and dry out over a winter, and a lid that "
								"sealed in October may not seal now.</p>"
								"<p>Then retrieve all the winter storage drain plugs from the pump basket, "
								"<b>coat their threads with silicone lubricant</b>, and re-torque them securely "
								"back into their respective equipment blocks. Check them off against the "
								"winterizing record as they go in.</p>"
							),
						},
						{
							"title": "2. Scrub the shell, clear the tanks",
							"body": (
								"<p>Thoroughly scrub the basin shell walls and clear the winter debris out of all "
								"holding tanks. Do it now, while the basin is empty and you can see the floor and "
								"reach the corners. Everything left in there is load for a filter you are about "
								"to bring up clean.</p>"
							),
						},
						{
							"title": "3. Probes back on, straight out of solution",
							"body": (
								"<p>Reconnect the digital pH and ORP probes. They have wintered indoors in wet "
								"storage caps filled with reference solution; take them out of the caps, fit them, "
								"and treat a probe that was found dry as a dead probe rather than one to try — a "
								"dry probe is instantly ruined, and a ruined probe reads plausibly enough to "
								"mislead a whole controller.</p>"
							),
						},
						{
							"title": "4. Fill to the target operating level",
							"body": (
								"<p>Fill the holding reservoir or basin back to its target operating level. Fill "
								"from a low point so the water pushes air ahead of it rather than trapping it. A "
								"system that looks full and is carrying a pocket of air will lose prime the first "
								"time the level moves.</p>"
							),
						},
						{
							"title": "5. Prime the pots, open the valves, start the pumps",
							"body": (
								"<p>Manually prime the hair-and-lint pots, open all the isolation plumbing valves, "
								"and execute the pump startup sequence. Stand and watch it rather than walking "
								"away.</p>"
								"<p>On a three-phase motor, prove the direction of rotation — and understand why "
								"this one is a trap. A centrifugal pump running backwards <b>still moves water and "
								"still builds some pressure</b>. It does not sit there doing nothing, and it does "
								"not announce itself. It just underperforms, quietly, all season, while every "
								"reading you take looks vaguely plausible. Any electrical work done over the "
								"winter can have swapped two legs.</p>"
							),
						},
						{
							"title": "6. Pressure drops, and the air that is still in there",
							"body": (
								"<p>With the pumps running, check for pressure drops across the system and bleed "
								"the trapped air loops from the top filter relief points. Air that stayed in a high "
								"spot through the fill comes out here, and until it does, every gauge on the system "
								"is describing a mixture rather than water.</p>"
							),
						},
						{
							"title": "7. Flow before heat",
							"body": (
								"<p>Circulation established and proved, with flow you have actually observed, "
								"before the heater is allowed to fire. Then bring heat on and watch the first cycle "
								"through.</p>"
							),
						},
						{
							"title": "8. Adjust the chemical profiles to the baseline LSI",
							"body": (
								"<p>Test the fill water as well as the basin, then balance from what you measure "
								"today until the baseline LSI is where it should be. Last season's numbers describe "
								"water that is no longer there.</p>"
							),
						},
						{
							"title": "9. Safety devices, every one of them",
							"body": (
								"<p>GFCI protection tested. Suction covers present, intact and correctly fastened. "
								"Bonding intact. Guards, grates and access covers back where they belong. None of "
								"this is a formality and none of it is somebody else's item.</p>"
							),
						},
						{
							"title": "10. Verify the sequence, then write the baselines down",
							"body": (
								"<p>Verify the sequence triggers on the Splash Wizard controller panel, and run the "
								"whole program: every zone, every effect, the lighting scenes, the autofill through "
								"a cycle, the wind sensor if there is one. Not a spot check — the sequence, start to "
								"finish, with you standing there. Then record the baselines.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "The water is not the water you left",
					"content": (
						"<p>Everything that could have changed it over the winter did. Rain and snowmelt "
						"diluted it. Evaporation concentrated what was left. Sanitiser is long gone. The basin "
						"was drained entirely and is now being refilled from a source whose own hardness and "
						"alkalinity have moved since you last looked at them. <b>Test the fill water too</b>, "
						"because on a spring fill most of what is in the basin came out of a hose an hour "
						"ago.</p>"
						"<p>Balance it the way Module 3 teaches, in order, rather than chasing one number at a "
						"time. The Langelier Saturation Index is the sum that tells you whether the water is "
						"in balance overall: pH plus a temperature factor plus a calcium hardness factor plus "
						"an alkalinity factor, minus a total dissolved solids constant. Near zero is balanced. "
						"Negative water is <b>aggressive</b> and goes looking for calcium in the plaster, the "
						"grout and the stone. Positive water <b>scales</b>. A fresh spring fill is very often "
						"on the aggressive side, which is exactly when a feature's finish gets quietly damaged "
						"and nobody connects it to start-up day.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Test every GFCI, and look at every suction cover",
					"content": (
						"<p><b>GFCI.</b> A Class A ground-fault circuit interrupter is designed to trip at "
						"roughly <b>4 to 6 mA</b> of ground-fault current — far below what it takes to stop a "
						"heart, which is the entire point of it existing around water. Test every one with its "
						"own test button and confirm the load actually dies, not just that the button clicks. "
						"A device that will not reset, or that trips the moment it is loaded, is telling you "
						"something true about a wet winter: water in a fixture, a damaged cable, a failing "
						"light. Treat it as a finding, not as a faulty GFCI.</p>"
						"<p>Bonding around water is <b>NEC Article 680</b>, and it exists so that everything a "
						"person can touch sits at the same potential. It is not earthing and it is not "
						"optional. If the bonding looks disturbed, corroded or absent, that is a qualified "
						"electrician's job.</p>"
						"<p><b>Suction covers.</b> An anti-entrapment cover is a life-safety device with a "
						"rating and a service life on it. Cracked, missing, loose or substituted means the "
						"feature does not run. There is no version of this where you open for the season and "
						"deal with it later.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Commissioning is where the baselines come from",
					"content": (
						"<p>Everything Modules 2 and 8 do with a gauge depends on somebody having written down "
						"what that gauge reads when the system is <b>clean and right</b>. 'The filter pressure "
						"is high' is not a statement about anything until there is a clean number to be high "
						"against. Start-up is the one visit where the whole system is clean and right at the "
						"same moment, so it is the natural place to take the whole set — and Module 2's rule "
						"carries it through the rest of the year: record the gauges again after every filter "
						"clean.</p>"
						"<p>Record them: filter pressure with a clean filter, suction vacuum with a clean "
						"basket, the normal operating water level, the balanced LSI figures, the sequence "
						"timings as the controller is actually running them, and anything else the site's form "
						"asks for. Write them wherever this site's records actually live rather than in your "
						"own notebook — a reading the next technician cannot find is a reading nobody "
						"took.</p>"
						"<p>And note what both seasonal jobs have in common. They are checklist work, the "
						"individual steps are not difficult, and <b>the cost of a missed one is paid months "
						"later by somebody else</b> — a plug nobody can find, a split in a line that was never "
						"blown clear, a heater that fired dry, a season measured against a baseline that was "
						"never taken. That delay is exactly why the checklist exists and exactly why it feels "
						"like it does not need to.</p>"
					),
				},
				ask_block(
					"What counts as commissioned here is Sapphire's call",
					"<p>Which baselines get recorded, on which form, and what has to be signed before a "
					"feature is handed back to a client are process decisions this course cannot make for "
					"you. So is the running sequence itself — the zones, the timings and the scenes belong "
					"to the design and to the controller program for that feature, not to a general "
					"rule.</p>"
					"<p>If the site has a commissioning sheet, it is the authority for this visit. If it "
					"does not, ask before you start rather than inventing one on the day, because a "
					"baseline that only exists in your memory is a baseline nobody else has.</p>",
				),
				{
					"block_type": "Rich Text",
					"heading": "Reading the module is not passing it",
					"content": (
						"<p>Sapphire's document ends Module 7 with a list, and the list is not a summary. "
						"<b>Before graduating from Module 7, a technician must successfully demonstrate six "
						"field skills to a Lead Installer.</b> Every one of them is something observable "
						"with the equipment in your hands — a rig that held its prime, a manifold that blew "
						"clear, a line array that came back up — rather than an opinion about whether you "
						"followed the lesson.</p>"
						"<p>Three of the six are taught outside this module and are demonstrated all the "
						"same. The confined space gas test and the lock-out/tag-out isolation belong to "
						"<b>Module 9</b>; reading a commercial fountain P&amp;ID belongs to <b>Module 10</b>. "
						"The requirement is still Module 7's.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Module 7 sign-off — demonstrate these to a Lead Installer",
					"items": [
						"Securely prime and sweep a mock basin floor using a portable high-flow vacuum rig, without losing prime",
						"Identify five distinct component symbols and accurately plot a field flow loop using a commercial fountain P&ID engineering drawing (Module 10)",
						"Execute a true pre-entry confined space gas test, correctly reading out safe oxygen, LEL and toxic gas baselines (Module 9)",
						"Deploy a multi-lock scissor hasp, padlock and danger tag to successfully isolate an electric motor panel following LOTO rules (Module 9)",
						"Execute a complete high-volume line purge on a mock manifold run using winterization blow-out connections",
						"Securely close, lubricate and commission an equipment line array during a mock spring start-up validation check",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "The winter drain plugs come out of the pump basket in spring. What happens before they go back into their equipment blocks?",
						"type": "Single Choice",
						"explanation": (
							"Coat the threads with silicone lubricant and re-torque them securely back into their "
							"respective equipment blocks, checking them off against the winterizing record as they "
							"go. That record is the only inventory of what is currently missing, and a filter body "
							"filled with its plug still in the basket is a wet pump room."
						),
						"options": [
							{
								"text": "Their threads are coated with silicone lubricant, and they are re-torqued securely into place",
								"is_correct": True,
							},
							{
								"text": "They are fitted dry and left finger-tight so the housings are not stressed",
								"is_correct": False,
							},
							{
								"text": "They are thrown away and replaced, since a used plug no longer seals",
								"is_correct": False,
							},
							{
								"text": "They are left out until the system is filled, so trapped air can escape through the ports",
								"is_correct": False,
							},
						],
					},
					{
						"question": "How does a digital pH or ORP probe get through the winter?",
						"type": "Single Choice",
						"explanation": (
							"Indoors, in a wet storage cap filled with reference solution, and reconnected at spring "
							"start-up. A dry probe is instantly ruined: the glass membrane and the reference junction "
							"have to stay wet."
						),
						"options": [
							{
								"text": "Indoors, in a wet storage cap filled with reference solution",
								"is_correct": True,
							},
							{
								"text": "Indoors, rinsed and dried thoroughly, in its original box",
								"is_correct": False,
							},
							{
								"text": "Left in the basin, where the water protects it from frost",
								"is_correct": False,
							},
							{
								"text": "Indoors in a sealed bag with a desiccant pack to keep condensation off it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A three-phase pump was rewired over the winter and is now running backwards. What do you observe?",
						"type": "Single Choice",
						"explanation": (
							"A centrifugal pump running backwards still moves water and still builds some pressure. "
							"It looks like it is working while it underperforms all season, which is why rotation is "
							"proved at the first run rather than assumed."
						),
						"options": [
							{
								"text": "It still moves water and builds some pressure, so it looks like it is working",
								"is_correct": True,
							},
							{
								"text": "It will not start at all until the rotation is corrected",
								"is_correct": False,
							},
							{"text": "It runs normally but produces no flow whatsoever", "is_correct": False},
							{
								"text": "It trips its breaker immediately, which is how you find out",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The pumps are running on a spring start-up. What does the workflow do from there?",
						"type": "Multiple Choice",
						"explanation": (
							"Check for pressure drops, bleed the trapped air loops from the top filter relief points, "
							"adjust the chemical profiles to balance the baseline LSI, and verify the sequence "
							"triggers on the Splash Wizard controller panel. Last season's balance describes water "
							"that is no longer in the basin, so it is not a starting point for anything."
						),
						"options": [
							{"text": "Check for pressure drops across the system", "is_correct": True},
							{
								"text": "Bleed the trapped air loops from the top filter relief points",
								"is_correct": True,
							},
							{
								"text": "Adjust the chemical profiles to balance the baseline LSI",
								"is_correct": True,
							},
							{
								"text": "Verify the sequence triggers on the Splash Wizard controller panel",
								"is_correct": True,
							},
							{
								"text": "Carry last season's balance figures forward as this season's starting point",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
