# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 7 — Service Operations."""

from erpnext_enhancements.training.technician_program._common import ask_block, notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 7 — Service Operations",
		"summary": (
			"Vacuum a basin without losing the pump its prime or clouding the water you just "
			"cleared, close a system down so that nothing anywhere is holding water when it "
			"freezes, and open it again in spring with every plug back in, every safety device "
			"tested, and a clean set of baseline readings written down."
		),
		"category": "Service & Maintenance",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{
			"title": "Cleaning a basin that is in service",
			"description": "Vacuuming without damaging the pump, loading the filter, or stirring up what you came to remove.",
		},
		{
			"title": "The two ends of the season",
			"description": "Closing a system down so nothing freezes, and bringing it back with everything proved rather than assumed.",
		},
	],
	"lessons": [
		{
			"lesson_title": "Pool vacuuming operations",
			"chapter": 0,
			"estimated_minutes": 14,
			"summary": "Priming the hose, choosing waste or filter, and why the technique is slowness.",
			"blocks": [
				notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "The pump is doing the vacuuming",
					"content": (
						"<p>A manual vacuum head has no motor in it. The suction comes from the "
						"circulation pump, which means that while you are vacuuming, <b>your hose is "
						"part of the suction line</b>. Everything the pump suffers from on the suction "
						"side it now suffers from through however many feet of flexible hose you are "
						"dragging around a basin.</p>"
						"<p>That is why the hose is filled with water before it is connected. Sink the "
						"head, feed the hose in a coil until water comes out the free end, or hold the "
						"free end over a return until the flow has pushed every bubble out. Only then "
						"does it go onto the suction port. A hose connected dry hands the pump a slug "
						"of air the length of the hose.</p>"
						"<p>Empty the pump basket first, and the skimmer basket, and look at both again "
						"part way through. A vacuum moves more debris in ten minutes than the system "
						"sees in a week, and a full basket starves the pump exactly when you have "
						"given it the most work to do.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Air is what damages the pump, not dirt",
					"content": (
						"<p>A centrifugal pump is cooled and lubricated by the water going through it. "
						"Lose prime and the mechanical seal is running dry against itself, and it does "
						"not survive that for long. A dry seal can be ruined well before you have "
						"finished sorting out whatever caused it.</p>"
						"<p>So: do not lift the vacuum head out of the water while the pump is drawing "
						"on it, do not let the hose end break the surface, and if you hear the pump "
						"change note or see the basket go half empty, <b>stop the pump first</b> and "
						"sort it out after. The instinct is to keep working and fix it as you go. That "
						"instinct costs a seal.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "To waste, or through the filter",
					"content": (
						"<p>Vacuuming through the filter is the normal case. The debris is caught, the "
						"water goes back to the basin, and nothing is lost except a little filter "
						"capacity.</p>"
						"<p><b>Fine material changes the answer.</b> Silt, sand, and the grey cloud "
						"left behind after an algae treatment are made of particles small enough to "
						"pass straight through a sand bed and come back to the basin, or fine enough "
						"to blind a cartridge so completely that you have bought yourself a cartridge "
						"clean instead of a vacuum. Either way the dirt is still in the system. Those "
						"go <b>to waste</b>, where the water and everything in it leaves the system "
						"entirely and never touches the filter.</p>"
						"<p>Waste is a position on a multiport valve, or a dedicated drain valve. "
						"<b>Stop the pump before you move a multiport handle.</b> The handle drives a "
						"diverter against a gasket, and turning it against full pump pressure is how "
						"that gasket gets torn — after which the valve leaks internally in every "
						"position and the next person spends a morning chasing a filter that will not "
						"hold pressure.</p>"
						"<p>Not every system can vacuum to waste. A cartridge filter with no multiport "
						"has nowhere to send it. Then it is either through the filter with a clean "
						"afterwards, or a portable vacuum that discharges outside the system.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "To waste means water is leaving the site",
					"content": (
						"<p>Two consequences, and both of them catch people out.</p>"
						"<p><b>The level falls while you work.</b> Keep an eye on it. If it drops to "
						"the mouth of the skimmer, the skimmer starts swallowing air and the pump "
						"loses prime — you have caused the exact failure the lesson above is about, "
						"by doing something unrelated. Top up as you go, or work in passes and refill "
						"between them.</p>"
						"<p><b>The water has to go somewhere it is allowed to go.</b> Treated water "
						"carries sanitiser, pH and whatever you just killed, and where it may be "
						"discharged — sanitary sewer, a specific drain, never a storm drain or a "
						"planting bed in many jurisdictions — is set by local rule and by the site. "
						"Find out before you open the valve, not after.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Slow is the technique, and the gauges are the feedback",
					"content": (
						"<p>Everything on the floor of a basin that is worth vacuuming is there because "
						"it settled. Move the head quickly and you do not pick it up, you <b>launch "
						"it</b> — it goes back into suspension, hangs there while you work, and settles "
						"again on the part of the floor you already cleaned. Then the water is cloudy, "
						"so you cannot see what is left, and you vacuum the same square foot twice.</p>"
						"<p>Long, slow, overlapping passes. Work so that you are retreating from the "
						"clean area rather than walking back through it, and keep the head on the "
						"floor rather than skipping it along.</p>"
						"<p>While you work, the gauges Module 2 covers are telling you what the system "
						"is experiencing. A <b>rising vacuum reading</b> on the suction side is a "
						"restriction ahead of the pump: a loading basket, a blocked hose, or the head "
						"sitting on a leaf. A <b>rising filter pressure</b> is the filter itself "
						"loading up with what you are sending it. They mean different things and they "
						"call for different actions — clear the restriction, or stop and clean the "
						"filter.</p>"
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
						"question": "Why is the vacuum hose filled with water before it is connected to the suction port?",
						"type": "Single Choice",
						"explanation": (
							"While you are vacuuming, the hose is part of the suction line. A hose connected dry "
							"delivers a slug of air the length of the hose straight to the pump."
						),
						"options": [
							{
								"text": "Because the hose is part of the suction line, and the air in a dry hose goes straight to the pump",
								"is_correct": True,
							},
							{
								"text": "To make the hose sink so it is easier to handle in the water",
								"is_correct": False,
							},
							{
								"text": "To rinse loose dirt out of the hose before it reaches the filter",
								"is_correct": False,
							},
							{
								"text": "To weight the vacuum head down so it stays on the floor",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A basin has a grey settled film left over from an algae treatment. Waste, or through the filter?",
						"type": "Single Choice",
						"explanation": (
							"Particles that fine pass through a sand bed and return to the basin, or blind a "
							"cartridge. Sending them to waste is the only route that gets them out of the system."
						),
						"options": [
							{
								"text": "To waste, so the fines leave the system instead of passing through or blinding the filter",
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
							{
								"text": "Either one; the choice only affects how long the job takes",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Vacuuming to waste has consequences beyond removing the dirt. Which of these apply?",
						"type": "Multiple Choice",
						"explanation": (
							"The water leaves the system, so the level falls — far enough and the skimmer draws air "
							"and the pump loses prime. And discharged water has to go to a point that is legally "
							"allowed to receive it."
						),
						"options": [
							{
								"text": "The water level drops, and if it reaches the skimmer mouth the pump loses prime",
								"is_correct": True,
							},
							{
								"text": "The discharged water has to go to a point that is allowed to receive it",
								"is_correct": True,
							},
							{
								"text": "The filter loads faster than it would on a normal vacuum",
								"is_correct": False,
							},
							{
								"text": "The pump has to work against significantly more head",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why are vacuum passes made slowly and with an overlap?",
						"type": "Single Choice",
						"explanation": (
							"Settled material is picked up by slow suction and simply relaunched by a fast head. "
							"It re-suspends, clouds the water so you cannot see what is left, and settles again on "
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
			"chapter": 1,
			"estimated_minutes": 16,
			"summary": "One fact drives the whole job: trapped water splits whatever is holding it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "The whole job is one fact",
					"content": (
						"<p>Water expands by about <b>9%</b> when it freezes. Ice takes up more room "
						"than the water it came from, and nothing about a pipe wall, a pump volute, a "
						"heat exchanger or a filter body is going to talk it out of that.</p>"
						"<p>So freezing is only a problem where water is <b>trapped</b> — held in a "
						"closed space with nowhere to expand into. Ice in an open basin at least has "
						"a free surface to rise into — though it also pushes outward against walls, "
						"coping and tile as it grows, which is why a basin gets drained or lowered "
						"rather than left full. Water in a capped line, a drained-looking pump with "
						"its plug still in, or the low spot of a sagged run has nowhere to go at "
						"all, and the thing around it splits.</p>"
						"<p>That reframes the job. Winterizing is not a list of products you add. It "
						"is one goal — <b>there is no trapped water anywhere in this system</b> — and "
						"every item on the list exists to serve it. Judge any step you are unsure of "
						"against that sentence.</p>"
						"<p>It is also the definition of work whose mistakes are invisible. A missed "
						"low-point drain looks exactly like a done one in November. You find out in "
						"April, when the system is filled and something that was whole in the autumn "
						"is now pouring water into the ground.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Draining, and where water hides",
					"content": (
						"<p>Drain the basin. Drain every line. Open every low-point drain and leave "
						"them open — a drain valve closed for the winter is a trap, not a drain — and "
						"open valves generally, so that any water left behind has somewhere to expand "
						"to rather than being shut into a length of pipe between two closed "
						"valves.</p>"
						"<p>Then blow the lines out, because gravity on its own does not finish the "
						"job. Air pushed through a line carries out the film and the puddles that "
						"draining leaves, and it is the only way to clear the sections gravity cannot "
						"reach.</p>"
						"<p><b>And a line with a belly in it cannot be gravity drained at all.</b> "
						"This is Module 1's pitch lesson arriving with a bill. Gravity empties a line "
						"that falls continuously to its drain point; it does not empty a line that "
						"falls, rises, and falls again. The water in that sag stays there no matter "
						"how long you leave the valve open, and the sag is precisely where the split "
						"will be. If the same run splits in the same place two winters running, that "
						"is a grade problem being reported as a winterizing problem. Say so, so "
						"somebody can fix the actual thing.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Blowing a line clear is not a pressure test",
					"content": (
						"<p>Module 1 says never pressure test plastic pipe with air, and that rule has "
						"not moved. Clearing a line for winter looks similar and is a different "
						"operation, and the difference is what keeps it safe.</p>"
						"<p>You are <b>moving water out of an open line</b>, not building and holding "
						"a pressure in a closed one. The far end stays open. Nothing is capped. There "
						"is nowhere for stored energy to accumulate, which is the entire reason "
						"compressed air in plastic pipe is lethal. Stay out of the line of that open "
						"end all the same — it throws water, grit and whatever else was in the "
						"pipe.</p>"
						"<p>The moment somebody caps the end to 'push harder', it has become a "
						"pneumatic pressure test on plastic pipe. Stop. The pressure you use, and the "
						"equipment that supplies it, come from the system's own documentation — a "
						"compressor set to whatever it happens to be set to is not a plan.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Pull the plugs, and put them where they will be found",
					"content": (
						"<p>Pumps, filters and heaters all carry drain plugs, and the volute of a pump "
						"holds water below the level of any pipe connected to it. A plug left in is a "
						"full housing, and a full housing is a split housing. Get them all out — the "
						"pump, the strainer pot, the filter body, the heater, sight glasses, any "
						"low-point fitting with a plug in it.</p>"
						"<p>Then think about April. <b>A drain plug that has been lost is a start-up "
						"that cannot happen</b>, and the person opening the site may well not be the "
						"person who closed it. Put them somewhere deliberate and somewhere obvious: "
						"in the pump basket, or in a labelled bag taped to the equipment, or in the "
						"site's own container — whatever this site does, do that, and write down that "
						"you did it. A plug in your truck is a plug that is gone.</p>"
						"<p>Anything that cannot be drained comes off the system entirely and gets "
						"stored where it will not freeze: nozzles and jets that hold water, chemical "
						"feeders, probes and sensors, and whatever the design says is removable. Most "
						"of that stores dry — but a <b>pH or ORP probe is the exception</b>, because "
						"letting the glass dry out ruins it. Those go back into the storage solution "
						"the manufacturer's instructions call for, not into a bucket and not into a "
						"toolbox. Covers and protection go "
						"on <b>last</b>, once the draining is genuinely finished — a cover over an "
						"undrained basin is a lid on the problem, not a solution to it.</p>"
					),
				},
				ask_block(
					"Antifreeze is a design decision, and so is how far a site is closed",
					"<p>The only antifreeze that goes near a water feature is a <b>non-toxic, "
					"pool-rated</b> one. Automotive antifreeze is ethylene glycol, it is poisonous, "
					"and it has no place here at all. That part is not negotiable.</p>"
					"<p>Everything else about it is. Antifreeze is for the places the design accepts "
					"cannot be fully drained, it is never a substitute for draining what can be "
					"drained, and where it goes and how much belongs to the system designer and the "
					"manufacturer's instructions rather than to habit.</p>"
					"<p>The same goes for the scope. Whether a given feature is fully closed, left "
					"running through the winter, or something in between is a decision about that "
					"site and that client. Get the site's winterizing scope before you start pulling "
					"plugs.</p>",
				),
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Isolate the power before you leave",
					"content": (
						"<p>A drained system with live power is a destroyed system waiting for a timer "
						"to come round. A pump that starts against an empty volute is running dry from "
						"the first revolution. A heater that fires with no water in it is worse than "
						"that, and on a gas appliance it is a safety event rather than a repair "
						"bill.</p>"
						"<p>Kill the power at the breaker for everything that must not run, and where "
						"the work requires it, apply <b>lock-out/tag-out</b> under the employer's own "
						"written program rather than an informal agreement that nobody will touch it. "
						"Anything beyond opening a breaker you are authorised to open — pulling "
						"fuses, working inside a panel, disconnecting equipment — is qualified "
						"electrical work, and that is a person, not a task.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "The winterizing pass",
					"items": [
						"The site's winterizing scope is known before anything is opened",
						"Basin drained to the level the scope calls for",
						"Every line drained, and every low-point drain opened and left open",
						"Valves left open so nothing is shut between two closed valves",
						"Lines blown clear, far end open, nothing capped",
						"Known sags and bellies given extra attention, and reported if they recur",
						"Drain plugs out of the pump, strainer pot, filter and heater",
						"Plugs labelled, left where the spring crew will find them, and recorded",
						"Nozzles, feeders and anything undrainable removed and stored out of the frost",
						"Probes removed and kept in their storage solution, not dried out",
						"Non-toxic antifreeze only, and only where the design calls for it",
						"Power isolated, and locked out where the program requires it",
						"Covers and protection fitted last, after the draining is finished",
						"What you did — and anything you could not do — written on the record",
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A buried line has a sag in it. Left open to drain by gravity for a full day, it will be empty.",
						"type": "True-False",
						"explanation": (
							"Gravity empties a line that falls continuously to its drain point. A sag holds water at "
							"the low spot however long the valve is open, and that low spot is where it splits."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
					{
						"question": "A drain plug is left in the pump over the winter. What is the consequence?",
						"type": "Single Choice",
						"explanation": (
							"The volute holds water below the level of the pipes connected to it. That water is "
							"trapped, it expands about 9% as it freezes, and the housing splits."
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
						"question": "Blowing lines clear for winter uses compressed air on plastic pipe. What keeps that from being the pneumatic pressure test Module 1 forbids?",
						"type": "Multiple Choice",
						"explanation": (
							"Stored energy is what makes air lethal in plastic pipe. An open far end means pressure "
							"cannot accumulate, and the operation is moving water out rather than holding a pressure "
							"and reading it."
						),
						"options": [
							{
								"text": "The far end of the line is left open, so there is nowhere for stored energy to accumulate",
								"is_correct": True,
							},
							{
								"text": "You are pushing water out of an open line, not holding a pressure in a closed one",
								"is_correct": True,
							},
							{
								"text": "The far end is capped so the air pushes the water harder",
								"is_correct": False,
							},
							{
								"text": "Plastic pipe is safe under compressed air as long as the weather is cold",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Where should the drain plugs go once they are out?",
						"type": "Single Choice",
						"explanation": (
							"The person opening the site in spring may not be the person who closed it, and a plug "
							"nobody can find stops a start-up dead. Leave them somewhere deliberate at the equipment, "
							"labelled, and write down where."
						),
						"options": [
							{
								"text": "Somewhere deliberate at the equipment, labelled and recorded, so the spring crew finds them",
								"is_correct": True,
							},
							{
								"text": "In the technician's truck, where they will not be stolen or knocked into the pit",
								"is_correct": False,
							},
							{
								"text": "Loose in the pump pit — anyone opening the site will see them there",
								"is_correct": False,
							},
							{
								"text": "They are consumables; new ones get fitted at start-up",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Seasonal start-up and commissioning",
			"chapter": 1,
			"estimated_minutes": 18,
			"summary": "Proving a system that has sat all winter, and recording the baselines the rest of the year is measured against.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "It is not the system that was running in October",
					"content": (
						"<p>Six months passed and nobody was watching. Something froze, or something "
						"nearly did. A gasket took a set and dried out. Rodents got into an enclosure. "
						"Sunlight worked on anything exposed. A fastener let go. Somebody used the "
						"empty basin as a place to put things. And there is at least one drain plug "
						"lying in a bag that has to go back in before water arrives.</p>"
						"<p>So a start-up does not begin at a switch. It begins with a walk, with "
						"nothing energised and nothing filled, looking for what changed. Freeze damage "
						"hides in the same handful of places: the pump volute and strainer pot, the "
						"heat exchanger, the filter body, valve bodies, and the low spot of any run "
						"that was known to sag.</p>"
						"<p>Cross-check the plugs against what the winterizing record says was pulled. "
						"That record is the only reliable inventory of what is currently missing from "
						"this system, and a filter body filled with its drain plug still in a bag is "
						"a wet pump room.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Nothing runs dry, and nothing fires without flow",
					"content": (
						"<p><b>Fill and purge the air before any pump runs.</b> A pump started against "
						"an empty or half-empty casing is running its mechanical seal dry, and that is "
						"a repair, not an inconvenience.</p>"
						"<p><b>Prove flow before a heater is allowed to fire.</b> Heat going into an "
						"exchanger with no water moving through it has nowhere to go: it boils what is "
						"standing in there, and it cracks the exchanger. On a gas appliance that is a "
						"safety event and not just an expensive one — and the gas side itself, the gas "
						"train, the combustion setup, a pilot that will not stay lit, is not yours to "
						"adjust. That is a qualified gas technician.</p>"
						"<p>The heater's flow switch is a <b>backstop</b>, not your proof. Field-bodged "
						"or stuck flow switches are common enough that assuming one works is assuming "
						"the thing most likely to have been defeated. You prove flow; the switch is "
						"there for the day you are wrong.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The order, and why it is an order",
					"panels": [
						{
							"title": "1. Inspect, with nothing energised and nothing filled",
							"body": (
								"<p>Walk the whole system dry. Every drain plug back in, checked against the "
								"winterizing record. Look for splits and weeping at the pump, heater, filter, "
								"valve bodies and known low spots. Check gaskets and O-rings — they take a set "
								"and dry out over a winter, and a lid that sealed in October may not seal now. "
								"Reinstall what was removed for storage.</p>"
							),
						},
						{
							"title": "2. Fill, and purge the air out",
							"body": (
								"<p>Fill from a low point so the water pushes air ahead of it rather than "
								"trapping it, and let the air out at the high points and at the filter's own "
								"air relief. A system that looks full and is carrying a pocket of air will "
								"lose prime the first time the level moves.</p>"
							),
						},
						{
							"title": "3. First run of the pump, and prove rotation",
							"body": (
								"<p>Start it and watch it rather than walking away. On a three-phase motor, "
								"prove the direction of rotation — and understand why this one is a trap. A "
								"centrifugal pump running backwards <b>still moves water and still builds some "
								"pressure</b>. It does not sit there doing nothing, and it does not announce "
								"itself. It just underperforms, quietly, all season, while every reading you "
								"take looks vaguely plausible. Any electrical work done over the winter can "
								"have swapped two legs.</p>"
							),
						},
						{
							"title": "4. Flow before heat",
							"body": (
								"<p>Circulation established and proved, with flow you have actually observed, "
								"before the heater is allowed to fire. Then bring heat on and watch the first "
								"cycle through.</p>"
							),
						},
						{
							"title": "5. Water, from scratch",
							"body": (
								"<p>Test the fill water as well as the basin, then balance from what you "
								"measure today. Last season's numbers describe water that is no longer "
								"there.</p>"
							),
						},
						{
							"title": "6. Safety devices, every one of them",
							"body": (
								"<p>GFCI protection tested. Suction covers present, intact and correctly "
								"fastened. Bonding intact. Guards, grates and access covers back where they "
								"belong. None of this is a formality and none of it is somebody else's "
								"item.</p>"
							),
						},
						{
							"title": "7. Run the full sequence, and stay to watch it",
							"body": (
								"<p>Every zone, every effect, the controller's whole program, the lighting "
								"scenes, the autofill through a cycle, the wind sensor if there is one. Not a "
								"spot check — the sequence, start to finish, with you standing there. Then "
								"record the baselines.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "The water is not the water you left",
					"content": (
						"<p>Everything that could have changed it over the winter did. "
						"Rain and snowmelt diluted it.Evaporation concentrated what was left. "
						"Sanitiser is long gone. The basin may have been drained entirely and is now "
						"being refilled from a source whose own hardness and alkalinity have moved "
						"since you last looked at them. <b>Test the fill water too</b>, because on a "
						"spring fill most of what is in the basin came out of a hose an hour ago.</p>"
						"<p>Balance it the way Module 3 teaches, in order, rather than chasing one "
						"number at a time. The Langelier Saturation Index is the sum that tells you "
						"whether the water is in balance overall: pH plus a temperature factor plus a "
						"calcium hardness factor plus an alkalinity factor, minus a total dissolved "
						"solids constant. Near zero is balanced. Negative water is <b>aggressive</b> "
						"and goes looking for calcium in the plaster, the grout and the stone. "
						"Positive water <b>scales</b>. A fresh spring fill is very often on the "
						"aggressive side, which is exactly when a feature's finish gets quietly "
						"damaged and nobody connects it to start-up day.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Test every GFCI, and look at every suction cover",
					"content": (
						"<p><b>GFCI.</b> A Class A ground-fault circuit interrupter is designed to "
						"trip at roughly <b>4 to 6 mA</b> of ground-fault current — far below what it "
						"takes to stop a heart, which is the entire point of it existing around water. "
						"Test every one with its own test button and confirm the load actually dies, "
						"not just that the button clicks. A device that will not reset, or that trips "
						"the moment it is loaded, is telling you something true about a wet winter: "
						"water in a fixture, a damaged cable, a failing light. Treat it as a finding, "
						"not as a faulty GFCI.</p>"
						"<p>Bonding around water is <b>NEC Article 680</b>, and it exists so that "
						"everything a person can touch sits at the same potential. It is not earthing "
						"and it is not optional. If the bonding looks disturbed, corroded or absent, "
						"that is a qualified electrician's job.</p>"
						"<p><b>Suction covers.</b> An anti-entrapment cover is a life-safety device "
						"with a rating and a service life on it. Cracked, missing, loose or "
						"substituted means the feature does not run. There is no version of this where "
						"you open for the season and deal with it later.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Commissioning is where the baselines come from",
					"content": (
						"<p>Everything Modules 2 and 8 do with a gauge depends on somebody having "
						"written down what that gauge reads when the system is <b>clean and right</b>. "
						"'The filter pressure is high' is not a statement about anything until there "
						"is a clean number to be high against. Start-up is the one visit where the "
						"whole system is clean and right at the same moment, so it is the natural "
						"place to take the whole set — and Module 2's rule carries it through the "
						"rest of the year: record the gauges again after every filter clean.</p>"
						"<p>Record them: filter pressure with a clean filter, suction vacuum with a "
						"clean basket, the normal operating water level, run times as set, and "
						"anything else the site's form asks for. Write them wherever this site's "
						"records actually live rather than in your own notebook — a reading the next "
						"technician cannot find is a reading nobody took.</p>"
						"<p>And note what both seasonal jobs have in common. They are checklist work, "
						"the individual steps are not difficult, and <b>the cost of a missed one is "
						"paid months later by somebody else</b> — a plug nobody can find, a split in a "
						"line that was never blown clear, a heater that fired dry, a season measured "
						"against a baseline that was never taken. That delay is exactly why the "
						"checklist exists and exactly why it feels like it does not need to.</p>"
					),
				},
				ask_block(
					"What counts as commissioned here is Sapphire's call",
					"<p>Which baselines get recorded, on which form, and what has to be signed before "
					"a feature is handed back to a client are process decisions this course cannot "
					"make for you. So is the running sequence itself — the zones, the timings and the "
					"scenes belong to the design and the controller program for that feature, not to "
					"a general rule.</p>"
					"<p>If the site has a commissioning sheet, it is the authority for this visit. If "
					"it does not, ask before you start rather than inventing one on the day, because "
					"a baseline that only exists in your memory is a baseline nobody else has.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why must flow be proved before a heater is allowed to fire at start-up?",
						"type": "Single Choice",
						"explanation": (
							"Heat put into an exchanger with nothing moving through it boils the standing water and "
							"cracks the exchanger, and on a gas appliance that is a safety event. The flow switch is "
							"a backstop for when you are wrong, not the proof itself."
						),
						"options": [
							{
								"text": "Heat with no flow boils the water standing in the exchanger and cracks it",
								"is_correct": True,
							},
							{
								"text": "So the heater reaches its set temperature in a reasonable time",
								"is_correct": False,
							},
							{
								"text": "To avoid nuisance trips of the high-limit, which is awkward to reset",
								"is_correct": False,
							},
							{
								"text": "Because cold water entering a hot exchanger is what damages it",
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
							"proved rather than assumed."
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
						"question": "Why is last season's water balance not a starting point for a spring fill?",
						"type": "Multiple Choice",
						"explanation": (
							"The water itself has changed: diluted by rain and snowmelt, concentrated by evaporation, "
							"with no sanitiser left, and possibly replaced entirely from a fill source whose own "
							"chemistry has moved. Test what is in front of you, including the fill water."
						),
						"options": [
							{
								"text": "Rain and snowmelt dilute it while evaporation concentrates what is left",
								"is_correct": True,
							},
							{
								"text": "The basin may have been refilled from a source with its own hardness and alkalinity",
								"is_correct": True,
							},
							{"text": "Sanitiser does not survive the off-season", "is_correct": True},
							{
								"text": "The Langelier index resets to zero whenever a system is shut down",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What does commissioning record that the rest of the year depends on?",
						"type": "Single Choice",
						"explanation": (
							"The clean baselines. A later reading only means something measured against what the "
							"system read when it was clean and right, and start-up is when that number is available."
						),
						"options": [
							{
								"text": "The clean baselines — filter pressure clean, suction vacuum with a clean basket, normal water level",
								"is_correct": True,
							},
							{
								"text": "The date the system was started and who started it",
								"is_correct": False,
							},
							{
								"text": "The manufacturer's maximum ratings for each piece of equipment",
								"is_correct": False,
							},
							{
								"text": "The final water balance, which is the only figure worth keeping",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
