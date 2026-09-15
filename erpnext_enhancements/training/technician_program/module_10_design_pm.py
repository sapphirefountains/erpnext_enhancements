# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 10 — Basic Design & Project Management Principles."""

from erpnext_enhancements.training.technician_program._common import ask_block, notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 10 — Basic Design & Project Management Principles",
		"summary": (
			"Name every part of a fountain's loop and say what it does, read a schematic and a "
			"drawing set well enough to build from them, tell bonding from grounding and say why "
			"that difference keeps somebody alive, and recognise water hammer, unbalanced water and "
			"a stripped thread before they cost anything. Then run the task and the client "
			"conversation without committing the company to something it never agreed to."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Fountain component basics",
			"estimated_minutes": 12,
			"summary": "The parts of a fountain, what each one is for, and why they only make sense as a loop.",
			"blocks": [
				notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "It is one loop, and nothing on it is independent",
					"content": (
						"<p>A fountain is not a collection of equipment. It is <b>the same water going "
						"round</b>, over and over, and every component sits on that circuit.</p>"
						"<p>Water sits in the <b>basin</b>, drains to a <b>sump</b> or surge tank at the "
						"low point, is pulled through a <b>strainer</b> by the <b>pump</b>, pushed "
						"through <b>filtration</b>, treated by <b>sanitation</b> and whatever else "
						"conditions it, carried back out through the <b>distribution piping and "
						"manifold</b>, and returned to the basin through the <b>nozzles</b>. "
						"<b>Lighting</b> and <b>controls</b> ride along with it. <b>Make-up water</b> "
						"tops up what is lost and the <b>overflow</b> sets the ceiling.</p>"
						"<p>Because it is a loop, a fault anywhere on it turns up everywhere on it. A "
						"blocked strainer is a pump problem and a nozzle problem. A leaking basin is a "
						"water-chemistry problem, because make-up water keeps arriving with fresh "
						"hardness in it. Whenever something looks wrong, ask what is upstream of it "
						"before you touch the thing that is complaining.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The components, one at a time",
					"panels": [
						{
							"title": "Basin",
							"body": (
								"<p>The visible vessel, and the thing the water is in contact with for "
								"almost all of its life. Its finish — plaster, tile, stone, coated "
								"concrete — is what unbalanced water attacks first, which is why "
								"lesson 10.9 exists.</p>"
							),
						},
						{
							"title": "Sump, surge tank and suction",
							"body": (
								"<p>The low point the pump draws from. It holds the reserve that lets a "
								"feature run while the level swings, and it is where entrapment "
								"protection lives on anything the public can reach. If the sump "
								"uncovers, the pump is the next casualty.</p>"
							),
						},
						{
							"title": "Strainer and pump",
							"body": (
								"<p>The strainer catches what would otherwise reach the impeller. The "
								"pump is the only thing on the loop adding energy — everything else "
								"spends it. A pump that has to pull through a clogged strainer is "
								"starved on its suction side, which is where pumps get damaged.</p>"
							),
						},
						{
							"title": "Filtration",
							"body": (
								"<p>Takes particles out. It makes the water <i>clear</i>. It does not "
								"make the water clean, and it kills nothing at all — see the next "
								"lesson.</p>"
							),
						},
						{
							"title": "Sanitation and chemical feed",
							"body": (
								"<p>Kills or inactivates organisms and holds a measurable residual "
								"through the whole body of water. Injection points are placed where "
								"they will mix, and downstream of anything that would be damaged by "
								"undiluted chemical.</p>"
							),
						},
						{
							"title": "Distribution, manifold and nozzles",
							"body": (
								"<p>Carries treated water back to where it does the visible work. The "
								"manifold splits it between branches, and how it splits is a balancing "
								"job rather than an accident — lesson 10.4.</p>"
							),
						},
						{
							"title": "Lighting and controls",
							"body": (
								"<p>Submersible luminaires, transformers and the controller that "
								"sequences pumps, valves, colour and effects. Everything electrical "
								"near water is governed work, and it is bonded into the grid.</p>"
							),
						},
						{
							"title": "Make-up water and overflow",
							"body": (
								"<p>Make-up replaces what evaporates, blows away, splashes out and "
								"leaves on a backwash. Overflow sets the maximum level so rain does "
								"not put the basin across the deck. They are the two ends of the same "
								"level control, and a stuck make-up valve plus a blocked overflow is a "
								"flooded plaza.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The level is what destroys pumps, and the make-up meter is what warns you",
					"content": (
						"<p>Drop the level far enough to uncover the suction and the pump pulls air. It "
						"loses prime, it runs without the water that was cooling and lubricating it, "
						"and the damage is to the pump — even though the fault was a leak, a failed "
						"fill valve or a drained basin nobody refilled.</p>"
						"<p>The make-up water meter is the cheapest diagnostic on the site. Make-up "
						"tracks losses, and losses track the weather. A consumption that climbs while "
						"the weather has not changed means the water is going somewhere else: a leak in "
						"the basin or the buried pipe, a fill valve passing, or water leaving over the "
						"overflow because the level is set too high. Read it, write it down, and "
						"compare it to last time.</p>"
					),
				},
				ask_block(
					"What is actually installed on this feature",
					"<p>No two features carry the same equipment. Whether there is a surge tank, what "
					"the filter is, whether there is a heater, what the controller runs and where the "
					"isolation valves are come from the <b>submittals, the O&amp;M manual and the "
					"as-built drawings</b> for that site.</p>"
					"<p>If you are standing in front of a feature you have not seen before and there is "
					"no document set, that is worth saying out loud before you start changing things, "
					"not after.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What does the make-up water system do?",
						"type": "Single Choice",
						"explanation": (
							"It replaces what the feature loses — evaporation, wind carry, splash-out and "
							"backwash. It is not a supply to the nozzles, and it does not treat anything."
						),
						"options": [
							{
								"text": "Replaces water lost to evaporation, wind, splash-out and backwash",
								"is_correct": True,
							},
							{"text": "Supplies the nozzles at show pressure", "is_correct": False},
							{"text": "Drains the basin down in an emergency", "is_correct": False},
							{"text": "Dilutes chemical before it reaches the basin", "is_correct": False},
						],
					},
					{
						"question": "The water level drops far enough to uncover the pump's suction. What is damaged?",
						"type": "Single Choice",
						"explanation": (
							"The pump. It loses prime and runs without the water that was cooling and "
							"lubricating it — even though the actual fault was a leak or a fill problem."
						),
						"options": [
							{"text": "The pump, which loses prime and runs dry", "is_correct": True},
							{
								"text": "The nozzles, which run too high on the thinner water",
								"is_correct": False,
							},
							{"text": "The overflow, which begins to siphon the basin", "is_correct": False},
							{
								"text": "Nothing — the low level simply reduces the display height",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Make-up water consumption has climbed steadily while the weather has not changed. What does that tell you?",
						"type": "Single Choice",
						"explanation": (
							"This is a signal, not a season. Make-up tracks losses; if evaporation has not "
							"changed, the water is going somewhere else — a leak, a passing fill valve, or "
							"water leaving over the overflow."
						),
						"options": [
							{
								"text": "Water is leaving somewhere it should not — a leak, a passing fill valve, or the level running out over the overflow",
								"is_correct": True,
							},
							{
								"text": "The sanitiser is being consumed faster than it is being fed",
								"is_correct": False,
							},
							{"text": "The filter is overdue for a backwash", "is_correct": False},
							{
								"text": "Nothing — make-up consumption wanders on its own and is not a diagnostic",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Water purification systems",
			"estimated_minutes": 13,
			"summary": "Filtration, sanitation and oxidation are three different jobs, and UV and ozone only do one of them.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Three different jobs that people call by one name",
					"content": (
						"<p><b>Filtration</b> removes what you could in principle catch in a net — grit, "
						"debris, dead algae, the fine material that makes water look hazy. It kills "
						"nothing.</p>"
						"<p><b>Sanitation</b> kills or inactivates living organisms, and keeps doing it, "
						"everywhere in the water at once.</p>"
						"<p><b>Oxidation</b> destroys dissolved organic material — the invisible load "
						"that a filter cannot catch and that makes water smell and look tired.</p>"
						"<p>The sentence worth carrying out of this lesson: <b>clear water is not clean "
						"water</b>. Clarity is a filtration result. A basin can be beautifully clear and "
						"carrying an organism load that would close it, and it can be perfectly "
						"sanitised and look like dishwater. They are separate measurements of separate "
						"things, and you need both.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "A residual is protection that travels; contact time is what makes it work",
					"content": (
						"<p>A <b>residual</b> is sanitiser still present and measurable in the body of "
						"water. That is what makes it useful: something introduced at the far corner of "
						"the basin — a bird, a hand, a leaf — meets sanitiser <i>there</i>, without "
						"having to travel to the equipment room first.</p>"
						"<p>Killing an organism takes both <b>concentration and time in contact with "
						"it</b>. A high concentration for an instant and a low one for a long while are "
						"not the same thing, and organisms differ enormously in how stubborn they are — "
						"some are dealt with almost immediately and some resist a normal residual for a "
						"very long time. That is why a target is a range held continuously, not a dose "
						"thrown in when the water looks off.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "UV and ozone work only where the water passes them",
					"content": (
						"<p>A UV system inactivates organisms as the water flows past the lamp. An ozone "
						"system oxidises as the gas is injected and mixed. Both are real, both are "
						"effective, and both are <b>point-of-treatment</b>: they act inside the "
						"equipment, on the water that is passing through at that moment.</p>"
						"<p>Two consequences follow, and they are the whole reason this is a lesson:</p>"
						"<ul>"
						"<li><b>Neither leaves a residual in the basin.</b> Water treated an hour ago is "
						"not protected now. Whatever happens in the basin happens where the UV lamp is "
						"not.</li>"
						"<li><b>Only treated water is treated.</b> Water that has not yet made it round "
						"the loop has had nothing done to it, so how much good these systems do depends "
						"on turnover.</li>"
						"</ul>"
						"<p>So they are <b>supplements</b>. They reduce the load the residual sanitiser "
						"has to carry, and on some organisms they do work the residual does badly. They "
						"do not replace it, and a system designed around a residual does not become safe "
						"because a lamp was added.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A lit lamp is not a working lamp, and ozone is a gas that hurts people",
					"content": (
						"<p>A UV lamp's output falls as it ages, and the quartz sleeve between the lamp "
						"and the water fouls with scale and film. Both happen slowly, neither shows a "
						"fault, and the lamp goes on glowing. <b>A UV system can be lit, drawing power, "
						"showing no alarm, and inactivating very little.</b> Lamp life and sleeve "
						"cleaning are maintenance items with a schedule behind them, not an "
						"as-needed job.</p>"
						"<p><b>Ozone is toxic to breathe.</b> These systems are built with contact "
						"vessels, off-gas handling and destruct units for that reason. Opening, venting or "
						"servicing one is work for somebody trained on that equipment, with the system "
						"isolated under the site's lock-out/tag-out program — Module 9 covers what that "
						"program is for and who is allowed to apply it. Treat a sharp smell in an equipment "
						"room as a reason to leave, not to investigate. "
						"Chemical storage rules apply to everything else on this list too: oxidisers and "
						"acids stored or mixed together is how equipment rooms catch fire. Read the "
						"safety data sheet for what is actually on the site.</p>"
					),
				},
				ask_block(
					"Which sanitiser, what target, how much",
					"<p>Which chemical a feature runs on, what residual it is held at, how often it is "
					"tested, what the records have to show and how much to add for the volume in front "
					"of you are set by the <b>water chemistry design, the product label and the health "
					"authority with jurisdiction</b>. They differ by feature, by chemical and by "
					"city.</p>"
					"<p>Module 3 covers testing. What this course will not do is print a dose rate — a "
					"figure carried from another site is how a basin gets over-dosed and a finish gets "
					"ruined. Read the label and the site's own design.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is “the water is clear” not evidence that the water is safe?",
						"type": "Single Choice",
						"explanation": (
							"Clarity is what filtration produces, and filtration kills nothing. Sanitation "
							"is a separate job, measured separately."
						),
						"options": [
							{
								"text": "Clarity is a filtration result, and filtration does not kill anything",
								"is_correct": True,
							},
							{
								"text": "Clarity is measured at the wrong point on the loop to be meaningful",
								"is_correct": False,
							},
							{
								"text": "Clear water always indicates a scaling saturation index",
								"is_correct": False,
							},
							{
								"text": "Clarity only matters for the appearance of the lighting",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which statements about UV and ozone in a fountain system are true?",
						"type": "Multiple Choice",
						"explanation": (
							"Both act on water as it passes through the equipment and neither leaves "
							"anything behind in the basin. That is exactly why they supplement a residual "
							"sanitiser rather than replacing it."
						),
						"options": [
							{
								"text": "They only treat the water that actually passes through them",
								"is_correct": True,
							},
							{"text": "They leave no residual behind in the basin", "is_correct": True},
							{"text": "They remove the need for a residual sanitiser", "is_correct": False},
							{
								"text": "They treat the whole body of water continuously, wherever it is",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A UV lamp is lit and the controller shows no fault. What can still be wrong?",
						"type": "Single Choice",
						"explanation": (
							"Output falls with lamp age and the quartz sleeve fouls. Both are invisible and "
							"neither raises an alarm, so the lamp keeps glowing while doing very little."
						),
						"options": [
							{
								"text": "Output has fallen with age, or the sleeve is fouled, so little is being inactivated",
								"is_correct": True,
							},
							{
								"text": "Nothing — a lit lamp with no alarm is a working lamp",
								"is_correct": False,
							},
							{
								"text": "It is over-treating, which will drive the sanitiser residual up",
								"is_correct": False,
							},
							{
								"text": "It can only fail by failing to light, which the controller would report",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Aquatic system diagrams and basics",
			"estimated_minutes": 12,
			"summary": "Reading a hydraulic schematic: following the loop, what the symbols mean, and what a schematic is not.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A schematic is a map of relationships, not a map of the room",
					"content": (
						"<p>A hydraulic schematic answers one question extremely well: <b>what is "
						"connected to what, and in which order</b>. It is authoritative about whether "
						"the filter is upstream or downstream of the chemical injection, which valve "
						"isolates which branch, and where the sample point is taken from.</p>"
						"<p>It is not a picture of the equipment room. It is not to scale, the lines are "
						"not where the pipe runs, and two items drawn touching each other may be in "
						"different rooms. Do not measure it, do not count fittings off it, and do not "
						"decide where to stand from it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Follow the loop with your finger, and account for every branch",
					"content": (
						"<p>The way to read one is to trace it. Start in the basin and go round: to the "
						"sump, through the strainer, through the pump, through the filter, past whatever "
						"treats the water, out through the manifold, to the nozzles, back to the "
						"basin.</p>"
						"<p>Then account for every line that leaves that circle. A backwash line to "
						"waste. The make-up feed coming in. The overflow going to drain. A chemical "
						"injection point. A sample line. A bypass around the heater. For each one, ask "
						"the same two questions: <b>where does it come from, and where does it go?</b></p>"
						"<p>If you find a component you cannot trace back to the basin, one of two "
						"things is true: you missed a line, or the drawing is incomplete. Both are worth "
						"saying out loud. Neither is a reason to assume.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "What the common symbols are telling you",
					"panels": [
						{
							"title": "Valves",
							"body": (
								"<p>Usually a bowtie — two triangles meeting at the stem. The "
								"<i>type</i> of valve is shown by what is drawn on the stem or inside "
								"the body, and that is what tells you whether it is meant to isolate "
								"or to throttle.</p>"
							),
						},
						{
							"title": "Check valves",
							"body": (
								"<p>Drawn with a direction, and the direction is the entire point. A "
								"check valve on a schematic is telling you which way the designer "
								"expects water to move through that line — and, by implication, that "
								"reverse flow there is a problem.</p>"
							),
						},
						{
							"title": "Pumps",
							"body": (
								"<p>A circle with the suction and discharge shown differently. Which "
								"side is which matters: most of the pump faults a drawing can help "
								"you chase — strainer, air ingress, prime, a starved suction — live "
								"on the suction side, and the schematic tells you what is on it. "
								"Mechanical and electrical faults are a different hunt and the "
								"schematic will not show them.</p>"
							),
						},
						{
							"title": "Instrument bubbles",
							"body": (
								"<p>A circle with letters in it. The first letter is what is measured "
								"and the rest say what is done with it — PI for a pressure indicator, "
								"FI for flow, TI for temperature. A bubble marks a place where "
								"somebody expected a reading to be taken.</p>"
							),
						},
						{
							"title": "Line types and continuation flags",
							"body": (
								"<p>Heavy solid lines are usually process piping and lighter, dashed or "
								"slashed lines are usually signal and instrument runs — but the "
								"convention varies between offices. A flag or arrow at the edge of the "
								"sheet means the line carries on somewhere else, and it names where.</p>"
							),
						},
						{
							"title": "The legend",
							"body": (
								"<p>Every drawing set carries one, and it is the authority for that set. "
								"Symbol conventions are not universal. If a symbol and your memory "
								"disagree, the legend wins.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The schematic is the intent; the pipe is the truth",
					"content": (
						"<p>Schematics are drawn at design. Then a valve gets added during "
						"construction, a line gets rerouted around an obstruction, a filter is swapped "
						"for a different model, and somebody ties a new feature into a spare port. "
						"Unless a proper as-built was produced, none of that is on the sheet.</p>"
						"<p>On an existing feature, treat the schematic as what was meant to be there "
						"and <b>verify by tracing actual pipe</b> before you rely on a valve to isolate "
						"anything. Then mark up what you found. A marked-up drawing handed back to the "
						"office is worth more than the clean one you were given.</p>"
					),
				},
				ask_block(
					"Valve tags and which set is the site's",
					"<p>Whether the valves on this site carry tags, whether the tags match the drawing, "
					"and whether Sapphire keeps a marked-up set for a given feature are site and "
					"company matters, not something a course can answer.</p>"
					"<p>Ask your supervisor what exists for the feature before you go looking, and ask "
					"where a markup should go afterwards. A correction nobody files is a correction "
					"that gets made again next year.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A hydraulic schematic shows the physical layout of the equipment room to scale.",
						"type": "True-False",
						"explanation": (
							"It shows connection and order. Position on the page says nothing about "
							"position in the room, and nothing on it may be measured."
						),
						"options": [
							{"text": "False", "is_correct": True},
							{"text": "True", "is_correct": False},
						],
					},
					{
						"question": "You trace a schematic and find a return line you cannot follow back to the basin. What is the right conclusion?",
						"type": "Single Choice",
						"explanation": (
							"Either you missed a line or the drawing is incomplete. Both matter, and "
							"neither is settled by assuming where it probably goes."
						),
						"options": [
							{
								"text": "Either you missed a line or the drawing is incomplete — say so",
								"is_correct": True,
							},
							{"text": "It is a dead leg and can be ignored", "is_correct": False},
							{
								"text": "It must run to the overflow, because everything eventually does",
								"is_correct": False,
							},
							{
								"text": "Draw in the connection you think is there and carry on",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You do not recognise a symbol on a drawing. What is the reliable way to find out what it means?",
						"type": "Single Choice",
						"explanation": (
							"Symbol conventions differ between offices and drawing sets. The legend on "
							"that set is the authority for that set."
						),
						"options": [
							{"text": "Read the legend on that drawing set", "is_correct": True},
							{"text": "Use the symbol set you learned on the last job", "is_correct": False},
							{"text": "Assume it is a valve, since most symbols are", "is_correct": False},
							{
								"text": "Ask the client what the previous contractor used it for",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Flow control methods",
			"estimated_minutes": 14,
			"summary": "Which valve is for which job, why throttling a gate valve ruins it, and why the far end of a manifold starves.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Isolating and throttling are two different jobs",
					"content": (
						"<p>An <b>isolation</b> valve exists to be fully open or fully shut. Open, it "
						"should be as close to a straight piece of pipe as the designer could manage; "
						"shut, it should seal. A <b>throttling</b> valve exists to sit part way and "
						"control how much gets past. Ask a valve to do the other one's job and you "
						"either get no useful control or you destroy the valve.</p>"
						"<p>The example worth remembering is the gate valve. A gate valve is a flat "
						"wedge dropped across the bore. Part way open, that wedge sits in the middle of "
						"a fast stream and the flow chatters and erodes across it. <b>The surfaces being "
						"damaged are the sealing surfaces</b> — the ones that have to mate perfectly for "
						"it to shut. So the valve is ruined as an isolation valve, and you find that out "
						"on the day you actually need to isolate something. It was never good at "
						"throttling anyway: nearly all of the flow change happens in a small part of the "
						"travel, so it is either barely doing anything or nearly shut.</p>"
						"<p>Ball valves behave the same way for the same reason. The seats get cut by "
						"the jet that squeezes past a partly open ball.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The valves you will meet and what each is for",
					"panels": [
						{
							"title": "Gate valve — isolation",
							"body": (
								"<p>Very low loss when fully open, seals when fully shut. Open or shut, "
								"nothing in between. Throttling erodes the wedge and the seat and "
								"takes away its only real ability.</p>"
							),
						},
						{
							"title": "Ball valve — isolation",
							"body": (
								"<p>Quarter turn, low loss, positive shut-off, easy to see the position "
								"of. Also easy to slam, which makes it a water-hammer source — see "
								"lesson 10.8. Partly open, the seats get cut.</p>"
							),
						},
						{
							"title": "Globe valve — throttling",
							"body": (
								"<p>A plug closing onto a seat, with the flow turning through the body. "
								"Built to sit part way: the control is smooth and spread across the "
								"travel. The price is head loss even wide open, because the water has "
								"to change direction twice.</p>"
							),
						},
						{
							"title": "Butterfly valve — both, within limits",
							"body": (
								"<p>A disc that rotates in the bore. Compact and cheap in large sizes, "
								"and it will throttle over part of its range — but near the closed end "
								"the flow past the disc becomes unstable and it can cavitate. It is "
								"not a precision control device.</p>"
							),
						},
						{
							"title": "Check valve — direction only",
							"body": (
								"<p>Allows flow one way and closes against the other. It is <b>not</b> "
								"an isolation valve: debris on the seat lets it dribble backwards, and "
								"you never rely on one to hold a line you are working on. A check "
								"valve that closes only after the flow has already reversed slams, and "
								"that slam is hydraulic shock.</p>"
							),
						},
						{
							"title": "Balancing valve — deliberate resistance",
							"body": (
								"<p>A throttling valve with a memory stop and, usually, a way of "
								"reading flow across it. It is set once during commissioning to give "
								"one branch its designed share of the water, and the stop lets it be "
								"shut for service and reopened to exactly the same position. "
								"<b>Do not move the stop.</b></p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Water takes the easy path, so the far end starves",
					"content": (
						"<p>Flow divides between parallel branches according to how much resistance each "
						"one offers. It is not divided evenly and it does not care about the drawing. "
						"The nearest branch off a manifold is short and has few fittings; the far branch "
						"has more pipe, more elbows and more loss. Left alone, the near one takes more "
						"than its share and the far one takes less.</p>"
						"<p>What you see is the near jets running high and the far jets dribbling — and "
						"here is the trap: <b>the total flow can still be exactly at design</b>. The "
						"pump is happy, the flow meter reads correctly, nothing is blocked. The water is "
						"all there; it is in the wrong branches.</p>"
						"<p>Balancing valves fix it by deliberately adding resistance to the easy "
						"branches until every branch takes its designed flow. The work is done against "
						"<b>flow readings</b>, not against how the jets look — eyeballing nozzle heights "
						"gets you a display that is even on a still day and wrong on every other one.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Changing the speed instead of fighting the valve",
					"content": (
						"<p>Throttling reduces flow by making the pump work against more resistance. The "
						"pump still turns at full speed and still draws most of its power; the energy "
						"you removed from the flow is spent as friction and ends up heating the "
						"water.</p>"
						"<p>A variable frequency drive changes the pump's speed instead. For a "
						"centrifugal pump the relationships are steep and they are universal: flow falls "
						"roughly in proportion to speed, head falls roughly with the square of speed, "
						"and <b>power falls roughly with the cube</b>. A modest reduction in speed is a "
						"large reduction in power, which is why a VFD is the efficient way to change "
						"flow, and it is also the gentle way to start and stop a pump.</p>"
						"<p>It has a floor. Below some speed the pump no longer makes the head the "
						"feature needs, the display collapses, and anything relying on flow for cooling "
						"or seal lubrication stops getting it. That minimum, and the ramp times, are "
						"commissioning settings rather than knobs to explore.</p>"
					),
				},
				ask_block(
					"Design flows, valve positions and drive settings",
					"<p>How much flow each branch is supposed to take, where a balancing valve was set, "
					"what the minimum VFD speed is and how long the ramps are all come from the "
					"<b>engineer's schedule and the commissioning record</b> for that feature.</p>"
					"<p>If a balancing valve has been moved and there is no record of where it was, "
					"that is a rebalancing job, not a guess. Ask the project manager whether a "
					"commissioning record exists before you touch it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why should a gate valve not be left part way open to reduce flow?",
						"type": "Single Choice",
						"explanation": (
							"The wedge sits in the stream and the flow erodes the wedge and the seat — the "
							"very surfaces that have to mate for it to shut. The valve loses its ability to "
							"isolate, and it was poor at throttling in the first place."
						),
						"options": [
							{
								"text": "The flow erodes the sealing surfaces, so it will no longer shut off",
								"is_correct": True,
							},
							{
								"text": "Its stem seal is only rated for fully open or fully shut",
								"is_correct": False,
							},
							{
								"text": "It will not reduce the flow at all, whatever position it is in",
								"is_correct": False,
							},
							{"text": "It causes the pump to overheat within minutes", "is_correct": False},
						],
					},
					{
						"question": "The near nozzles on a manifold run high, the far ones barely run, and the system's total flow is at design. What is happening?",
						"type": "Single Choice",
						"explanation": (
							"Flow follows the path of least resistance, and the manifold is not balanced. A "
							"blockage or an undersized pump would show as a shortfall in total flow — this "
							"one does not, because all the water is there, just in the wrong branches."
						),
						"options": [
							{
								"text": "The branches are not balanced, so the easy ones are taking more than their share",
								"is_correct": True,
							},
							{"text": "The pump is undersized for the feature", "is_correct": False},
							{"text": "The far nozzles are blocked", "is_correct": False},
							{"text": "The far branch has a leak", "is_correct": False},
						],
					},
					{
						"question": "Which are true of reducing flow with a VFD rather than by throttling a valve?",
						"type": "Multiple Choice",
						"explanation": (
							"Power falls roughly with the cube of speed, so slowing the pump saves energy "
							"that throttling merely converts to friction. But there is a floor: too slow and "
							"the pump stops making the head the feature needs."
						),
						"options": [
							{
								"text": "It reduces the power the pump draws instead of burning it as friction",
								"is_correct": True,
							},
							{
								"text": "There is a minimum speed below which the feature will not perform",
								"is_correct": True,
							},
							{"text": "It removes the need to balance the branches", "is_correct": False},
							{
								"text": "It increases the head the pump produces as the speed falls",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Architectural and engineering drawing mastery",
			"estimated_minutes": 14,
			"summary": "Plans, sections, elevations and details; why you never scale a print; and which revision is in your hand.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Four ways of looking at the same thing",
					"content": (
						"<p><b>Plan</b> — looking straight down. It is the horizontal arrangement: where "
						"things are relative to each other in the footprint.</p>"
						"<p><b>Elevation</b> — looking straight at a face from outside, with no "
						"perspective. Heights and what the thing looks like.</p>"
						"<p><b>Section</b> — a cut straight through, showing what is inside along that "
						"line. Sections exist because a plan cannot show depth, and on a fountain the "
						"section is where the real construction lives: basin build-up, waterproofing, "
						"the sump, the depth of everything.</p>"
						"<p><b>Detail</b> — one condition, drawn much larger. Where a small-scale plan "
						"and a large-scale detail disagree, the detail is usually the one that was drawn "
						"with knowledge of the condition — but which governs is stated in the documents, "
						"not decided by you.</p>"
						"<p>Section marks and detail bubbles are cross-references: they name the sheet "
						"the cut is drawn on, and the <b>arrow shows which way you are looking</b>. Read "
						"a section from the wrong side and everything on it is mirrored.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why you do not scale a drawing",
					"content": (
						"<p>Every drawing has a stated scale, and a scale rule will measure it. Do not "
						"build from that. Drawings are printed at reduced sizes, photocopied, scanned, "
						"re-issued as PDFs and printed again on paper that is not the size the office "
						"drew them at. By the time a sheet reaches a truck it may be nowhere near "
						"scale.</p>"
						"<p><b>Written dimensions govern.</b> Every set says so somewhere. A scaled "
						"measurement is a sanity check — a way of noticing that a written dimension "
						"looks wrong — and never a dimension in its own right.</p>"
						"<p>A missing dimension is not a puzzle to solve. It is a question to ask.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "What is drawn and what is specified are two different documents",
					"content": (
						"<p>The <b>drawings</b> show geometry: arrangement, location, size, how it goes "
						"together. The <b>specification</b> says what it is made of, which standard it "
						"has to meet, which products are acceptable and how the work is to be executed. "
						"A drawing may show a pump; the specification says which pump.</p>"
						"<p>They disagree more often than anyone would like. When they do, there is an "
						"<b>order of precedence written into the contract documents</b>, and it differs "
						"from job to job. On top of that, the <b>approved submittal</b> is what is "
						"actually being installed, and it can be a different model from either "
						"document.</p>"
						"<p>None of that is resolved on site by whoever is holding the wrench. Stop and "
						"ask. Installing the wrong thing correctly is still installing the wrong "
						"thing.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Revisions, clouds, and which drawing is in your hand",
					"content": (
						"<p>A <b>revision cloud</b> is a wobbly outline drawn around an area that "
						"changed. Next to it sits a numbered triangle, and that number ties the change "
						"to a row in the sheet's revision table, which says what changed and when.</p>"
						"<p>The number that actually matters is the <b>revision of the sheet you are "
						"reading</b>, checked against the current issued set. Building from a superseded "
						"sheet is one of the most expensive mistakes available on a construction site, "
						"and it gives you no warning at all: an old print looks exactly as authoritative "
						"as a new one, it is often cleaner, and it is frequently the one that has been "
						"living in the truck.</p>"
						"<p>Two habits fix it. Check the revision before you build from a sheet, and "
						"<b>destroy superseded prints</b> rather than rolling them back up. Changes also "
						"arrive as addenda and bulletins that never appear as a cloud on anything — if "
						"you are working from paper, you are working from a snapshot.</p>"
					),
				},
				ask_block(
					"Raising the question is the cheap part",
					"<p>When the documents do not answer something — a missing dimension, a conflict "
					"between a drawing and a specification, a detail that cannot physically be built — "
					"the formal route is a <b>Request for Information</b>. It puts the question to the "
					"people with the authority to answer it, and the answer becomes part of the record "
					"of the job.</p>"
					"<p>An RFI is not an admission that you do not know your trade. It is the mechanism "
					"that moves a decision to whoever owns it. A guess costs demolition and rework, and "
					"afterwards nobody can say who decided.</p>"
					"<p>Who writes it, who it goes to, and how it is tracked on a given project are "
					"Sapphire's and the contract's arrangements. Ask your project manager how an RFI "
					"is raised here, before the day you need to raise one.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A dimension you need is missing from the drawing. What is the right move?",
						"type": "Single Choice",
						"explanation": (
							"Written dimensions govern, and prints are resized, scanned and reprinted until "
							"a scale rule means nothing. A missing dimension is a question for the design "
							"team, raised before the work is built."
						),
						"options": [
							{
								"text": "Raise it as a question before building, rather than scaling the sheet",
								"is_correct": True,
							},
							{
								"text": "Measure it off the print with a scale rule and build to that",
								"is_correct": False,
							},
							{
								"text": "Use the dimension from the last similar feature you built",
								"is_correct": False,
							},
							{
								"text": "Build it and note the assumption on the punch list",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The drawing shows one pump and the specification names a different one. What do you do?",
						"type": "Single Choice",
						"explanation": (
							"The contract documents carry a written order of precedence, and it differs by "
							"job — and an approved submittal may supersede both. It is a documented "
							"resolution, not a field preference."
						),
						"options": [
							{
								"text": "Stop and ask — the documents carry a written order of precedence",
								"is_correct": True,
							},
							{
								"text": "The drawing governs, because it shows the actual installation",
								"is_correct": False,
							},
							{
								"text": "The specification always governs over the drawings",
								"is_correct": False,
							},
							{
								"text": "Install whichever one is on the truck and note it afterwards",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is building from a superseded drawing such a common and expensive mistake?",
						"type": "Single Choice",
						"explanation": (
							"Nothing about an old sheet announces that it is old. It looks as authoritative "
							"as the current one — often cleaner — which is why the revision is checked "
							"against the issued set and superseded prints are destroyed."
						),
						"options": [
							{
								"text": "An out-of-date sheet gives no warning that it is out of date",
								"is_correct": True,
							},
							{"text": "Superseded sheets are printed without dimensions", "is_correct": False},
							{
								"text": "Revision clouds are only added to the newest sheet in a set",
								"is_correct": False,
							},
							{
								"text": "It is only a problem on sheets that have no revision table",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Basic electrical and health department codes",
			"estimated_minutes": 13,
			"summary": "Who regulates a water feature, why classification decides which rules bind, and why the AHJ has the last word.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Four layers decide what a fountain has to be",
					"content": (
						"<p><b>A model code</b>, adopted by the state or the local jurisdiction. The "
						"<i>International Swimming Pool and Spa Code</i> (ISPSC) is the common one for "
						"pools, spas and many water features.</p>"
						"<p><b>The electrical code.</b> In the United States that is the NEC, and "
						"<b>Article 680</b> is the article that covers swimming pools, fountains and "
						"similar installations.</p>"
						"<p><b>The health department</b>, wherever the public can contact the water. It "
						"regulates water quality, treatment, testing, records, and sometimes who is "
						"allowed to operate the system.</p>"
						"<p><b>The authority having jurisdiction</b> — the AHJ — the inspector or "
						"official who interprets and enforces all of the above on this particular job. "
						"<b>The AHJ's reading is the one that counts.</b> Model codes are adopted "
						"selectively and amended locally, so “what the code says” always means "
						"“what your jurisdiction adopted, as amended, as this inspector reads "
						"it”.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Classification decides which rules bind you",
					"content": (
						"<p>This is the part people miss. A decorative basin nobody is meant to touch, an "
						"interactive feature children are designed to run through, and a swimming pool "
						"are three different regulatory objects, and the requirements step up sharply "
						"with public contact: treatment and residual requirements, turnover rates, "
						"testing frequency and record keeping, suction entrapment protection, barriers "
						"and signage, and in some jurisdictions operator certification.</p>"
						"<p>The same physical basin can land in a different class depending on whether "
						"people can get into it. Which means <b>changing how a feature is used changes "
						"its classification</b>. A client who decides their ornamental pool is now where "
						"the children play has made a regulatory change, not an operational one, and "
						"telling them so is part of the job.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Electrical work around water is licensed work, and a defeated GFCI looks like a working fountain",
					"content": (
						"<p>NEC Article 680 governs how power gets to and around water: ground-fault "
						"protection, equipotential bonding (the next lesson), listed luminaires, "
						"specific wiring methods, and where junction boxes may sit relative to the water "
						"line. It is licensed work. Do not do electrical work you are not qualified and "
						"permitted to do, however simple it looks.</p>"
						"<p>A <b>Class A GFCI trips at roughly 4–6 mA</b> of ground-fault current. "
						"That level is not arbitrary — it is set below the current that would stop a "
						"person being able to let go. A GFCI is a device, and devices fail, which is "
						"exactly why it has a test button on it.</p>"
						"<p>If you find a GFCI bypassed, or replaced with a plain breaker because it "
						"“kept tripping”, you have found a lethal modification that looks like "
						"a perfectly normal fountain. It kept tripping because something is faulted. The "
						"feature comes out of service and a licensed electrician gets involved.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Permits, inspections and the paper trail",
					"content": (
						"<p>Permitted work is inspected work, and some of those inspections have to "
						"happen before things are covered. Bonding, conduit and buried pipe are all "
						"invisible after the pour and the backfill, which is what rough-in inspections "
						"are for. Covering work that has not been inspected means uncovering it.</p>"
						"<p>Health authorities generally require operating records — test results, "
						"readings, logs — and the record is part of compliance rather than paperwork "
						"around it. A feature running perfect water with no log is non-compliant, and it "
						"is non-compliant in the one way that was entirely avoidable.</p>"
					),
				},
				ask_block(
					"Which code, which classification, which inspector",
					"<p>Which code edition your jurisdiction adopted and how it amended it, how a given "
					"feature is classified, what testing and records are required, who the AHJ is and "
					"what they have already required on this job — all of that is site-specific and "
					"none of it is in this course.</p>"
					"<p>It lives in the permit documents and with the project manager, and the health "
					"department will answer a direct question. The one habit that reliably causes "
					"trouble is assuming the last city's rules apply in this one.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What decides which set of health rules applies to a water feature?",
						"type": "Single Choice",
						"explanation": (
							"Its classification, which turns on how the public is intended to contact the "
							"water — and the AHJ decides how it is classified."
						),
						"options": [
							{
								"text": "How the feature is classified, which depends on public contact with the water",
								"is_correct": True,
							},
							{"text": "Its volume in gallons", "is_correct": False},
							{"text": "Whether it is indoors or outdoors", "is_correct": False},
							{"text": "The type of filtration installed", "is_correct": False},
						],
					},
					{
						"question": "Because model codes are national documents, a feature that complied in one city complies in the next.",
						"type": "True-False",
						"explanation": (
							"Model codes are adopted selectively and amended locally, and the AHJ interprets "
							"what was adopted. Compliance is always compliance somewhere specific."
						),
						"options": [
							{"text": "False", "is_correct": True},
							{"text": "True", "is_correct": False},
						],
					},
					{
						"question": "A Class A GFCI trips at roughly 4–6 mA of ground-fault current. Why that level?",
						"type": "Single Choice",
						"explanation": (
							"It is set below the current at which a person can no longer let go. A GFCI "
							"protects people; the breaker beside it protects the wiring."
						),
						"options": [
							{
								"text": "It is below the current that would stop a person being able to let go",
								"is_correct": True,
							},
							{
								"text": "It is the smallest current a breaker can reliably measure",
								"is_correct": False,
							},
							{"text": "It matches the rating of the circuit it protects", "is_correct": False},
							{
								"text": "It is whatever the installer adjusts it to on commissioning",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Bonding grids versus grounding",
			"estimated_minutes": 15,
			"summary": "The difference between grounding and bonding, precisely — and why a perfectly grounded fountain can still shock somebody.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Two words, two completely different jobs",
					"content": (
						"<p><b>Grounding</b> gives fault current a way home. It is a low-impedance path "
						"from the metal of equipment back to the source of supply, so that when an "
						"energised conductor touches a metal enclosure a <i>large</i> current flows and "
						"the overcurrent device or the GFCI sees it and disconnects. Grounding's job is "
						"to <b>make a protective device operate</b>. It is about clearing the "
						"fault.</p>"
						"<p><b>Bonding</b> ties all the conductive parts together with a conductor so "
						"that they are electrically continuous and everything a person can touch sits at "
						"the <b>same potential</b>. Bonding does not carry fault current away and it is "
						"not trying to. It removes the <i>voltage difference</i> that would otherwise "
						"drive current through a body.</p>"
						"<p>They are not two names for the same wire. They are two different safety "
						"mechanisms addressing two different failures.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A person is only shocked by a difference",
					"content": (
						"<p>Current flows through a body when one part of it is touching something at "
						"one voltage and another part is touching something at a different voltage. The "
						"body bridges the gap. <b>The absolute voltage is not what hurts somebody; the "
						"difference across them is.</b></p>"
						"<p>Now picture somebody standing in a fountain. At one moment they may be in "
						"contact with the water, the wet deck, the coping, the reinforcing steel under "
						"the finish, a handrail, and the housing of a submerged light. If every one of "
						"those is bonded together, they can all rise and fall in voltage together and "
						"the person feels nothing — there is no difference across them to drive "
						"anything.</p>"
						"<p>If one of them is <i>not</i> in the grid, it can sit at a different "
						"potential from everything else, and it becomes the other end of a circuit whose "
						"conductor is a person. That is the whole mechanism.</p>"
						"<p>Which gives the sentence this lesson exists for: <b>a perfectly grounded "
						"feature with a broken bond can still shock somebody standing in it.</b> "
						"Grounding cleared nothing, because there may be no fault large enough to trip "
						"anything — just enough of a difference to kill.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "The equipotential bonding grid is engineered and inspected, and it is never a field improvisation",
					"content": (
						"<p>Around water this is an <b>equipotential bonding grid</b>, and NEC Article "
						"680 governs it. It ties together the structure's reinforcing steel or an "
						"equivalent conductive grid, metal parts of the shell, metal fittings and "
						"ladders and rails, pump motors and equipment, conductive surfaces within a "
						"defined distance of the water, and — where required — the water itself. The "
						"conductor sizes and the connectors are specified, and much of it is buried in "
						"concrete.</p>"
						"<p>Two consequences. It is <b>inspected before the pour</b>, because afterwards "
						"nobody can see it. And you cannot verify it by looking.</p>"
						"<p>So: never cut, extend, shorten, relocate or tidy up a bonding conductor. It "
						"is frequently bare copper and it looks like scrap. If you find one broken, "
						"corroded off at the lug, or disconnected — or a light niche replaced without "
						"its bond reconnected — the feature comes out of service, and putting it right "
						"is an electrician's work with the AHJ involved.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "The terms, kept straight",
					"cards": [
						{
							"front": "Equipment grounding conductor",
							"back": "The path that carries fault current back to the source so an overcurrent device or GFCI operates. Its job is to clear the fault — not to make things safe to touch.",
						},
						{
							"front": "Bonding",
							"back": "Connecting conductive parts together so they sit at the same potential. It removes the voltage difference a person would otherwise bridge. It does not clear a fault.",
						},
						{
							"front": "Equipotential bonding grid",
							"back": "The engineered network around water — reinforcing, shell metal, fittings, rails, equipment, nearby surfaces and the water where required — all tied together. Governed by NEC Article 680, inspected before the pour.",
						},
						{
							"front": "Ground fault",
							"back": "An unintended path from an energised conductor to grounded metal, water or earth. It can be far too small to trip a breaker and still be far more than a person can survive.",
						},
						{
							"front": "GFCI",
							"back": "A device that compares the current going out with the current coming back and opens when they differ. A Class A device trips at roughly 4-6 mA. It protects people; a breaker protects wire.",
						},
						{
							"front": "Touch potential",
							"back": "The voltage difference between two things a person is touching at the same time — the water and a handrail, say. Bonding is what drives it to nearly zero.",
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "What a technician actually does about it",
					"content": (
						"<p>You do not design the grid and you do not repair it. What you can do matters "
						"anyway.</p>"
						"<p><b>Recognise a bonding conductor and leave it alone.</b> Notice a corroded "
						"lug, a conductor that has been cut, a fitting that was replaced and never "
						"reconnected. Notice when somebody has <b>added metal to a finished feature</b> "
						"— an aftermarket handrail, a sculpture, a metal grate, a new light — because "
						"new metal near water that nobody bonded is a new potential difference waiting "
						"for a person.</p>"
						"<p>And treat <b>any</b> report of a tingle as an emergency. A tingle in the "
						"water, on a rail, on the deck. It is not a curiosity and it is not something to "
						"reproduce by touching it again. Get people out, de-energise the feature, keep "
						"it out of service, and escalate it. That sensation is often the only warning "
						"anybody gets.</p>"
					),
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "What does bonding do that grounding does not?",
						"type": "Single Choice",
						"explanation": (
							"Bonding makes everything a person can touch sit at the same potential, removing "
							"the difference that would push current through a body. Grounding gives fault "
							"current a path so a protective device operates."
						),
						"options": [
							{
								"text": "It puts everything a person can touch at the same potential",
								"is_correct": True,
							},
							{"text": "It carries fault current away from the water", "is_correct": False},
							{"text": "It replaces the need for GFCI protection", "is_correct": False},
							{"text": "It drains voltage away into the earth", "is_correct": False},
						],
					},
					{
						"question": "A fountain that is correctly grounded cannot shock somebody standing in it.",
						"type": "True-False",
						"explanation": (
							"Grounding clears faults large enough to operate a device. A broken bond leaves a "
							"potential difference between things a person touches at once, and that "
							"difference is what drives current through them."
						),
						"options": [
							{"text": "False", "is_correct": True},
							{"text": "True", "is_correct": False},
						],
					},
					{
						"question": "Somebody has added a metal handrail to an existing fountain. What is the concern?",
						"type": "Single Choice",
						"explanation": (
							"New metal near the water that was never tied into the equipotential bonding grid "
							"can sit at a different potential from the water and the deck. A person touching "
							"both bridges the difference."
						),
						"options": [
							{
								"text": "If it was not tied into the bonding grid it can sit at a different potential from the water",
								"is_correct": True,
							},
							{
								"text": "It will corrode faster than the original metalwork",
								"is_correct": False,
							},
							{
								"text": "It will change the trip level of the feature's GFCI",
								"is_correct": False,
							},
							{"text": "None, provided the GFCI has been tested recently", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Hydraulic shock (water hammer)",
			"estimated_minutes": 12,
			"summary": "Where the bang comes from, why the damage arrives disguised as age, and what actually reduces it.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Moving water has momentum and it has to go somewhere",
					"content": (
						"<p>A column of water in a pipe has mass and it has speed. Stop it quickly and "
						"that momentum has to become something else. It becomes <b>pressure</b> — a "
						"spike that travels back up the pipe as a wave, reflects off the far end, and "
						"comes back, banging back and forth until friction finally kills it.</p>"
						"<p>Two things make the surge bigger: <b>how fast the water was moving</b> and "
						"<b>how fast you stopped it</b>. Length works differently: it decides what counts "
						"as fast. The pressure wave has to run to the end of the pipe and back before a "
						"closure stops being a sudden one, so on a long line a closure that felt gentle "
						"still produces the full surge — and the banging goes on longer.</p>"
						"<p>The spike can be several times the system's normal working pressure and it "
						"lasts a fraction of a second. That is why a pressure gauge on the wall almost "
						"never shows it — the needle cannot move that fast.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What causes it on a fountain",
					"content": (
						"<p><b>A fast-closing valve.</b> A solenoid valve snapping shut, or a quarter-"
						"turn ball valve closed by hand in half a second. The faster the stop, the "
						"bigger the surge — and a ball valve is the easiest thing on a site to slam.</p>"
						"<p><b>A pump starting or stopping across the line.</b> Full voltage, full "
						"speed, immediately.</p>"
						"<p><b>A check valve slamming.</b> If the disc has not closed by the time flow "
						"reverses, the reversed column slams it shut, and that is a surge every "
						"time.</p>"
						"<p><b>A pump tripping on power loss.</b> The worst case, because nothing is "
						"controlled: the column keeps going, separates, and comes back together.</p>"
						"<p><b>Level and make-up controls</b> that snap open and shut rather than "
						"modulating.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The damage is cumulative, and it does not arrive labelled",
					"content": (
						"<p>The bang is the obvious symptom. What you will actually be sent to look at is "
						"the consequences, months or years later: pipe supports worked loose, pipe "
						"chafed where it has been moving against a bracket, cracked fittings, threaded "
						"joints that have started weeping, failed pump seals, and solvent-weld joints "
						"fatigued by a load nobody designed for.</p>"
						"<p>Add gauges to that list, and notice what it means. A pressure gauge hammered "
						"repeatedly goes out of calibration, and it does not do so by going blank — "
						"<b>it settles on a plausible-looking number and stays there</b>. A system that "
						"“reads fine” on an instrument that has been beaten for two years is "
						"not evidence of anything.</p>"
						"<p>All of it gets attributed to age. The cause is a valve closing in a quarter "
						"of a second, several hundred times a day.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What actually reduces it",
					"content": (
						"<p><b>Slow the stop down.</b> Motorised valves with an adjustable travel time, "
						"slow-closing solenoids, and closing a manual valve deliberately rather than "
						"flicking it. This is the single most effective lever, because closure time is "
						"the term you can usually change.</p>"
						"<p><b>Ramp the pump.</b> A VFD that accelerates and decelerates over a set "
						"period never presents the system with a sudden change at all.</p>"
						"<p><b>Let the check valve close in time.</b> Non-slam and spring-assisted "
						"checks are built to be shut before the flow reverses, which is the entire "
						"problem with the cheap ones.</p>"
						"<p><b>Give the energy somewhere to go.</b> A surge arrestor or air chamber near "
						"the offending valve absorbs the spike — but an air chamber that has water-"
						"logged over the years is doing nothing at all, and it looks exactly like one "
						"that works.</p>"
						"<p><b>Design the velocity down.</b> The surge follows the velocity you stopped, "
						"so a system laid out at sensible velocities has less to give up in the first "
						"place.</p>"
						"<p>One case the first two do not cover: a pump tripping on a power failure. "
						"Nothing is closing slowly there, and the VFD has no power either. That one is "
						"handled in the design — non-slam check valves, a surge vessel or air chamber "
						"sized for the pump trip, and a sensible design velocity. A feature that bangs "
						"on every power blip has an engineering problem, not a maintenance one.</p>"
					),
				},
				ask_block(
					"Closure times, arrestor sizing and ramp settings are calculated",
					"<p>How fast a valve may be allowed to close, what size an arrestor needs to be, "
					"what velocity the system was designed at and how long a VFD's ramps should be are "
					"all calculated for the specific system. They are not field preferences.</p>"
					"<p>Which matters most when somebody wants a change: swapping in a faster-acting "
					"valve, or shortening a ramp to make a show look crisper, <b>increases the "
					"surge</b>. That is an engineering question for the design team through your "
					"project manager, not a setting to try.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What makes a water-hammer surge larger?",
						"type": "Single Choice",
						"explanation": (
							"The surge comes from stopping momentum. More velocity means more momentum, and "
							"a faster stop means it has less time to be absorbed."
						),
						"options": [
							{"text": "A higher flow velocity, stopped more quickly", "is_correct": True},
							{"text": "A slower valve closure", "is_correct": False},
							{"text": "A shorter pipe run with less water in motion", "is_correct": False},
							{"text": "A lower flow velocity held for a longer time", "is_correct": False},
						],
					},
					{
						"question": "Which of these reduce hydraulic shock?",
						"type": "Multiple Choice",
						"explanation": (
							"Every one of these either slows the stop down or takes the energy somewhere "
							"else. Raising the design velocity does the opposite — the surge follows the "
							"velocity you had to stop."
						),
						"options": [
							{"text": "Increasing a valve's closing time", "is_correct": True},
							{
								"text": "Ramping the pump up and down with a VFD instead of starting it across the line",
								"is_correct": True,
							},
							{
								"text": "A non-slam check valve that closes before the flow reverses",
								"is_correct": True,
							},
							{
								"text": "Designing for a higher velocity so the water clears the pipe faster",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is a wall-mounted pressure gauge poor evidence about water hammer in a system?",
						"type": "Single Choice",
						"explanation": (
							"The spike lasts a fraction of a second, so the needle never reflects it — and "
							"repeated hammering knocks the gauge out of calibration, where it settles on a "
							"plausible number rather than failing visibly."
						),
						"options": [
							{
								"text": "The spike is too brief for the needle to show, and hammering leaves the gauge reading a plausible but wrong number",
								"is_correct": True,
							},
							{"text": "Gauges are inaccurate at low pressures", "is_correct": False},
							{
								"text": "Gauges are always installed on the wrong side of the pump",
								"is_correct": False,
							},
							{
								"text": "Gauges report average pressure by design, and the average is the value that matters",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Langelier Saturation Index (LSI) engineering",
			"estimated_minutes": 14,
			"summary": "What the index is made of, what aggressive and scaling water actually do, and why one body of water can be both at once.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Unbalanced water takes what it needs, or leaves what it cannot carry",
					"content": (
						"<p>Water tends towards saturation with calcium carbonate, and it will get there "
						"one way or the other.</p>"
						"<p><b>Under-saturated water is aggressive.</b> It dissolves calcium out of "
						"whatever it can reach: plaster, grout, mortar joints, concrete, some stone. "
						"Etched plaster, grout washing out of tile joints and a basin that gets rougher "
						"every year are not wear. They are the water dismantling the building, and it "
						"goes after metal too.</p>"
						"<p><b>Over-saturated water scales.</b> It deposits on tile, at the waterline, "
						"inside heat exchangers and pipe, across nozzles — where even a little deposit "
						"changes the shape of the visible jet — and on probes and sensors, which then "
						"quietly read wrong.</p>"
						"<p>Neither is cosmetic. One takes the structure apart and the other takes the "
						"equipment out.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The five things the index is made of",
					"content": (
						"<p>The Langelier Saturation Index is a single number assembled from five "
						"things: <b>pH</b>, plus a <b>temperature</b> factor, plus a <b>calcium "
						"hardness</b> factor, plus an <b>alkalinity</b> factor, minus a <b>total "
						"dissolved solids</b> constant.</p>"
						"<p>Balanced water sits near <b>zero</b>. <b>Negative is aggressive</b> — the "
						"water is hungry and will dissolve. <b>Positive is scaling</b> — the water is "
						"full and will deposit.</p>"
						"<p>Each term behaves differently, and that is what makes it an engineering "
						"problem rather than an arithmetic one:</p>"
						"<ul>"
						"<li><b>pH</b> moves fastest and drifts on its own.</li>"
						"<li><b>Alkalinity</b> is the buffer that decides how hard pH is to move at all. "
						"Chasing pH without looking at alkalinity is a losing game — you keep adding, it "
						"keeps coming back.</li>"
						"<li><b>Calcium hardness</b> climbs on its own and there is nothing you dose "
						"to bring it down. It comes down two ways only: water removed and replaced "
						"with softer water, or calcium dropping out of solution as scale.</li>"
						"<li><b>Temperature</b> is not something you dose. It is in the equation anyway, "
						"and it changes on its own.</li>"
						"<li><b>TDS</b> climbs as the feature evaporates and everything except the water "
						"stays behind.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The same water is two different waters in the same system",
					"content": (
						"<p>Temperature is a term in the index. So the identical body of water, at the "
						"identical moment, can be <b>aggressive in a cold outdoor basin and scaling "
						"inside the heat exchanger it is being pumped through</b>.</p>"
						"<p>That is not a curiosity. Scale forms first on the hottest surface in the "
						"system, which is usually the surface you cannot see and the one whose failure "
						"costs the most. Meanwhile the cold end is quietly etching.</p>"
						"<p>Which makes “the water tests fine” an incomplete sentence. It "
						"tests fine <i>at the point and the temperature you tested it</i>. Ask what the "
						"same water is doing at the hot end and at the cold end of the loop.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why the engineer designs to a number",
					"content": (
						"<p>The LSI is a design parameter, not a diagnosis you reach for when something "
						"looks wrong. Every surface the water touches has a tolerance: natural stone, "
						"plaster, coloured concrete, tile grout, copper, stainless and the internals of "
						"the equipment all have their own preference for how saturated the water should "
						"be. The target is chosen so that the water is compatible with <i>everything</i> "
						"it contacts, and the acceptable band around it comes with the finish.</p>"
						"<p>That is also why you cannot fix one reading in isolation. Move pH to correct "
						"a number and you have moved the index; add something to bring the index up and "
						"you have moved alkalinity. Every lever moves the same result.</p>"
						"<p>And the index drifts on its own. Make-up water arrives carrying hardness, "
						"alkalinity and dissolved solids; evaporation takes pure water out and leaves "
						"all of that behind. A feature that evaporates hard concentrates its own water "
						"over a season, and the index walks — which is why it is measured and trended "
						"rather than set once.</p>"
					),
				},
				ask_block(
					"The target, the band and what goes in",
					"<p>What LSI this feature is held at, how wide the acceptable band is, which "
					"chemicals are kept for the site and what a dose should be for the volume in front "
					"of you come from the <b>water chemistry design, the finish manufacturer and the "
					"product labels</b> — and the health authority sets its own floors and ceilings on "
					"top.</p>"
					"<p>Module 3 covers testing technique. Two rules this course will state: never dose "
					"from a number you remember off another site, and never add two chemicals at "
					"once.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A basin's plaster is etching and grout is washing out of the tile joints. What does that say about the water?",
						"type": "Single Choice",
						"explanation": (
							"Under-saturated, negative-LSI water is aggressive: it dissolves calcium out of "
							"the surfaces it touches. Scaling water does the opposite — it deposits."
						),
						"options": [
							{
								"text": "It is aggressive — a negative index, dissolving calcium out of the surfaces",
								"is_correct": True,
							},
							{
								"text": "It is scaling — a positive index, and scale is abrasive",
								"is_correct": False,
							},
							{"text": "The sanitiser residual is being held too high", "is_correct": False},
							{"text": "Calcium hardness is too high for the finish", "is_correct": False},
						],
					},
					{
						"question": "Water that is balanced in the basin is balanced everywhere in the system.",
						"type": "True-False",
						"explanation": (
							"Temperature is a term in the index, so the same water can be aggressive in a "
							"cold basin and scaling inside a hot heat exchanger at the same moment."
						),
						"options": [
							{"text": "False", "is_correct": True},
							{"text": "True", "is_correct": False},
						],
					},
					{
						"question": "Which of these are terms in the Langelier Saturation Index?",
						"type": "Multiple Choice",
						"explanation": (
							"The index is pH plus temperature, calcium hardness and alkalinity factors, minus "
							"a total dissolved solids constant. Sanitiser residual and flow rate matter "
							"enormously to a fountain, but they are not in this equation."
						),
						"options": [
							{"text": "pH", "is_correct": True},
							{"text": "Temperature", "is_correct": True},
							{"text": "Calcium hardness", "is_correct": True},
							{"text": "Total alkalinity", "is_correct": True},
							{"text": "Sanitiser residual", "is_correct": False},
							{"text": "Flow rate through the filter", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Fasteners, taps and dies",
			"estimated_minutes": 13,
			"summary": "Threads, grades and clamp load: why torque is really a measure of stretch, and how to cut or rescue a thread.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A thread is a standard, and a nearly-right thread destroys the hole",
					"content": (
						"<p>A fastener is defined by three things: diameter, <b>thread pitch</b>, and "
						"which standard it belongs to. Inch fasteners come in UNC (coarse) and UNF "
						"(fine); metric fasteners are named by diameter and pitch. Both families live in "
						"the same toolbox, and some near-miss combinations will start and turn a couple "
						"of times before they bind.</p>"
						"<p>That is the moment it goes wrong. Forcing one is how a perfectly good tapped "
						"hole in an expensive casting becomes a stripped hole. <b>If a fastener does not "
						"run in freely by hand for several turns, it is the wrong fastener.</b> Identify "
						"it by measuring and by a thread gauge, never by eye.</p>"
						"<p>Coarse and fine are a real choice. Coarse threads tolerate dirt, damage and "
						"corrosion, assemble faster, and hold better in soft materials like aluminium "
						"and cast alloys. Fine threads engage more thread per length of travel, resist "
						"loosening a little better at a given tension, and adjust more precisely — but "
						"they cross-thread easily and they are unforgiving of any damage. Around water "
						"and outdoors, corrosion usually argues for coarse.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Torque is an indirect way of measuring stretch",
					"content": (
						"<p>What holds a bolted joint together is not the bolt being tight. It is the "
						"bolt being <b>stretched</b>. A tightened fastener is a very stiff spring under "
						"tension, and that tension is the <b>clamp load</b> squeezing the parts "
						"together. A joint with enough clamp load does not move, and a joint that does "
						"not move does not fatigue and does not shake loose.</p>"
						"<p>Torque is only a way of getting there, and it is a poor one: most of the "
						"torque you apply is spent overcoming friction under the head and in the "
						"threads, and only a fraction of it becomes tension. Change the friction — dry, "
						"oiled, anti-seized, plated, galvanised — and the same torque produces a "
						"<b>very</b> different clamp load. That is why a real torque figure always "
						"arrives with a condition attached.</p>"
						"<p>Both errors are real. Under-torqued, the joint moves, the fastener works "
						"loose or fatigues, and the gasket weeps. Over-torqued, you take the fastener "
						"past its yield point and it stays permanently stretched. There is almost "
						"nothing left in it — a little more turning, or the next shock load, necks "
						"it and snaps it, or tears the thread out of the casting. And it cannot be "
						"trusted again: a bolt stretched past yield will not reliably reach the "
						"specified clamp load a second time, so it is replaced, not re-used.</p>"
						"<p>Grade markings say how much the fastener can take: radial lines on the head "
						"of an inch bolt, a number on a metric one. The grade and the torque figure "
						"belong together, so substituting a same-size bolt from the bin can be a "
						"dramatic downgrade. And <b>stainless is a corrosion choice, not a strength "
						"choice</b> — plenty of stainless fasteners are weaker than a decent alloy steel "
						"bolt, and stainless running on stainless galls and seizes.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Taps, dies, and rescuing a thread",
					"content": (
						"<p>A <b>tap</b> cuts an internal thread in a hole. A <b>die</b> cuts an "
						"external thread on a rod. Both remove metal.</p>"
						"<p>An internal thread starts as a hole, and the hole has to be the right size. "
						"Too large and there is not enough thread left to hold the load. Too small and "
						"the tap has more metal to cut than it was built for: it binds, and it snaps off "
						"inside the hole — which is a far worse afternoon than starting over. The right "
						"size comes from a <b>tap drill chart</b> for that thread, and there is usually "
						"one printed inside the tap set.</p>"
						"<p>Technique is the same everywhere: cutting fluid, keep the tap square to the "
						"work, cut a little and back off to break the chip, and never force it. A tap "
						"started crooked cuts a crooked thread and that hole is not recoverable.</p>"
						"<p>A damaged-but-alive thread is a different job. A <b>thread chaser</b> or "
						"restoring file reforms the existing thread without cutting new metal. Running a "
						"<i>cutting</i> tap down a dirty thread to “clean it up” removes metal "
						"that was doing work and leaves a thread that is loose. And a hole that is "
						"genuinely stripped is repaired with a thread insert, not with more effort.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Five things people reach for, and what each is actually for",
					"panels": [
						{
							"title": "Cutting tap",
							"body": (
								"<p>Cuts a new internal thread in a correctly sized hole. Removes metal. "
								"Not a cleaning tool.</p>"
							),
						},
						{
							"title": "Thread chaser or restoring file",
							"body": (
								"<p>Reforms a damaged existing thread without cutting metal away. This is "
								"the tool for a thread that is dirty, corroded or lightly bruised.</p>"
							),
						},
						{
							"title": "Thread locker",
							"body": (
								"<p>An adhesive that cures between the threads, in the absence of air, "
								"and stops a fastener rotating loose under vibration. Grades differ "
								"mostly in how hard they are to undo afterwards, and the stronger ones "
								"need heat to break.</p>"
							),
						},
						{
							"title": "Anti-seize",
							"body": (
								"<p>A lubricant carrying solids, used to stop threads galling and "
								"seizing — stainless on stainless, and anything that lives wet. It is "
								"the opposite of thread locker: it reduces friction, so the same torque "
								"stretches the bolt further <i>and</i> the fastener backs off more "
								"easily. Never both on one fastener.</p>"
							),
						},
						{
							"title": "Thread insert",
							"body": (
								"<p>How a stripped hole is actually repaired: the hole is drilled out, "
								"tapped oversize, and an insert restores the original thread size. "
								"Often stronger than the material it replaced.</p>"
							),
						},
					],
				},
				ask_block(
					"Torque figures, and the condition they were written for",
					"<p>Every torque figure that matters belongs to a specific fastener on a specific "
					"assembly, in a specific condition — dry, lubricated or anti-seized. It comes from "
					"the <b>equipment manufacturer or the engineer</b>, and the condition is part of the "
					"figure rather than a footnote to it.</p>"
					"<p>Which grade of fastener a given assembly takes, and which material it has to be "
					"for where it lives, are the same kind of question. Module 1's modular seals are one "
					"more example of the same rule: the number lives on the product, not in a "
					"course.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What actually holds a bolted joint together?",
						"type": "Single Choice",
						"explanation": (
							"The tension in the stretched fastener — the clamp load — squeezing the parts "
							"together. Torque is only an indirect and lossy way of producing it."
						),
						"options": [
							{
								"text": "The tension in the stretched fastener, clamping the parts together",
								"is_correct": True,
							},
							{"text": "Friction between the threads gripping each other", "is_correct": False},
							{"text": "The bolt head bearing down on the surface", "is_correct": False},
							{"text": "The thread locker bonding the fastener in place", "is_correct": False},
						],
					},
					{
						"question": "Why does a torque figure come with a condition — dry, lubricated, anti-seized — attached to it?",
						"type": "Single Choice",
						"explanation": (
							"Most applied torque is spent on friction, so changing the friction changes how "
							"much of it becomes clamp load. Using a dry figure on an anti-seized fastener "
							"over-stretches it."
						),
						"options": [
							{
								"text": "Friction consumes most of the torque, so changing it changes the clamp load produced",
								"is_correct": True,
							},
							{"text": "Lubricant weakens the fastener material", "is_correct": False},
							{
								"text": "A torque wrench reads differently when the threads are wet",
								"is_correct": False,
							},
							{
								"text": "It is a documentation formality with no effect in practice",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A hole is drilled smaller than the tap drill chart calls for, and then tapped. What is the likely outcome?",
						"type": "Single Choice",
						"explanation": (
							"The tap has more metal to cut than it was designed for, so it binds and can snap "
							"off in the hole. The extra engagement adds almost nothing to the strength of the "
							"joint, because the fastener fails before that thread does."
						),
						"options": [
							{"text": "The tap binds and can break off inside the hole", "is_correct": True},
							{
								"text": "A noticeably stronger thread, because more material is engaged",
								"is_correct": False,
							},
							{"text": "A thread that is loose on the fastener", "is_correct": False},
							{"text": "Nothing — the tap sizes the hole as it goes", "is_correct": False},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Tasks, milestones and deadlines",
			"estimated_minutes": 11,
			"summary": "What a milestone is, why dependencies make your day somebody else's problem, and what a record is for.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "A task has duration; a milestone has none",
					"content": (
						"<p>A <b>task</b> is work. It takes time, it consumes somebody's day, and it can "
						"be half done — and half done is real progress.</p>"
						"<p>A <b>milestone</b> is a point in time that marks a state: basin watertight, "
						"power energised, system commissioned, feature handed over. It takes no time at "
						"all. It is either reached or it is not, and <b>half of a milestone is "
						"nothing</b>.</p>"
						"<p>Milestones exist because they are what other people are waiting on. That is "
						"the only reason somebody drew one on the schedule.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Dependencies are why your day affects somebody else's",
					"content": (
						"<p>Some work cannot start until other work has finished. You cannot pressure "
						"test until the joints have cured. You cannot backfill until the test has "
						"passed. You cannot pour until the bonding grid has been inspected. You cannot "
						"commission until power is energised and the water is in.</p>"
						"<p>That chain is the schedule. And it means the important question about your "
						"own work is not only “when is it due” — it is <b>“who is waiting "
						"on it”</b>.</p>"
						"<p>A task with nothing behind it can slip and cost very little. A task that "
						"three other trades are queued against cannot slip at all without moving every "
						"one of them, and moving a trade off a site is not the same as moving it back "
						"on. Knowing which of your tasks is which is most of what schedule awareness "
						"actually is.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A delay reported early is a schedule problem. A delay reported late is a crisis.",
					"content": (
						"<p>The moment you know the part is wrong, the trench is flooded, the cure will "
						"not be finished in time or the detail cannot be built, that knowledge has "
						"value. Crews can be resequenced, another trade can be pulled forward, a "
						"delivery can be changed, a client can be told before they turn up expecting "
						"water.</p>"
						"<p>Sit on it and every one of those options expires quietly. By the time the "
						"delay is visible to everybody, the only choices left are the expensive "
						"ones.</p>"
						"<p>Report it while it is still uncertain, too — “this might not be "
						"ready” is useful information, and it is not a promise that it won't. "
						"Who you tell and how fast Sapphire expects to hear it are below, and they are "
						"the company's to set; what this lesson can tell you is that early beats "
						"tidy.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Record what you actually did, not what you were supposed to do",
					"content": (
						"<p>The task list is a plan. The record is what happened. They are different "
						"documents, and the second one is the valuable one.</p>"
						"<p>Write down what was completed, what was not and why, what you found that "
						"nobody expected, anything you did differently from the drawing, the readings "
						"you took and the equipment you installed. Two reasons. Somebody — possibly you "
						"— comes back to this feature in three years, and the record is all they will "
						"have. And when something is disputed later, a note written at the time beats a "
						"confident memory written afterwards, every time.</p>"
						"<p>One discipline makes the rest work: <b>“done” has to mean the same "
						"thing to you and to whoever reads it</b>. A task that is finished except for "
						"one item is not done, and that one item is precisely the thing somebody else "
						"needs to know about.</p>"
					),
				},
				ask_block(
					"How Sapphire wants this tracked",
					"<p>Which system tasks live in, what each status means, what a daily report has to "
					"contain, who updates the schedule and how quickly a problem is expected to be "
					"passed up are <b>Sapphire's decisions</b>, and this course will not invent "
					"them.</p>"
					"<p>Ask your supervisor how they want it recorded, and then do it the same way every "
					"time. A record that is kept inconsistently is barely a record — its value comes "
					"from being able to trust that if something is not in it, it did not happen.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What distinguishes a milestone from a task?",
						"type": "Single Choice",
						"explanation": (
							"A task has duration and can be partly complete. A milestone takes no time and "
							"marks a state — it is reached or it is not, and half of one is nothing."
						),
						"options": [
							{
								"text": "A milestone has no duration and marks a state that other work waits on",
								"is_correct": True,
							},
							{"text": "A milestone is simply a more important task", "is_correct": False},
							{
								"text": "A milestone is a task assigned to a supervisor rather than a technician",
								"is_correct": False,
							},
							{
								"text": "A milestone is any task that has a date attached to it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You learn on Monday that a part is wrong and the work depending on it cannot start as planned. Why say so immediately rather than when it becomes visible?",
						"type": "Single Choice",
						"explanation": (
							"Early, the schedule can be resequenced, another trade pulled forward, a client "
							"warned. Those options expire, and a late report leaves only the expensive "
							"choices."
						),
						"options": [
							{
								"text": "While there is time, the work can be resequenced — those options expire",
								"is_correct": True,
							},
							{
								"text": "So the delay can be attributed to the supplier rather than the crew",
								"is_correct": False,
							},
							{
								"text": "It makes no real difference, provided the job finishes",
								"is_correct": False,
							},
							{
								"text": "So the client can be invoiced for the change sooner",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these belong in the record of a visit?",
						"type": "Multiple Choice",
						"explanation": (
							"The record is what happened, not what was planned. What did not get done and why "
							"is often the most useful line in it, and the unexpected finding is what the next "
							"person needs."
						),
						"options": [
							{
								"text": "What was completed, and what was not, with the reason",
								"is_correct": True,
							},
							{"text": "Anything found that nobody expected", "is_correct": True},
							{"text": "Readings taken and equipment installed", "is_correct": True},
							{
								"text": "Only the tasks that were finished, so the record stays clean",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Customer interactions",
			"estimated_minutes": 11,
			"summary": "Being the company on somebody's site: what you may say, what you may not commit to, and what goes up the line.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "On site, you are the company",
					"content": (
						"<p>Whatever a client thinks of Sapphire is formed almost entirely by whoever is "
						"standing in front of them. The truck, the work area, whether you said hello, "
						"whether the deck was swept when you left.</p>"
						"<p>It also means that anything you say out loud sounds like a company position, "
						"whether you meant it that way or not. “That pump's probably shot” "
						"leaves your mouth as an observation and arrives at the person holding the "
						"budget as <i>Sapphire says the pump needs replacing</i>. It will be repeated, "
						"and it will be repeated without the word “probably”.</p>"
						"<p>So say what you know, and label what you are unsure of as unsure. Those are "
						"two different sentences and the difference survives the retelling only if you "
						"made it clearly.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Explain the thing, not the jargon — and never the blame",
					"content": (
						"<p>People do not need your vocabulary. They need the consequence and the "
						"decision. “The water chemistry is aggressive, so it is slowly dissolving "
						"the grout out of the joints” is better than an index value, and better "
						"again when it is followed by what happens if nothing is done.</p>"
						"<p>Two things to keep out of it. <b>Jargon</b>, which makes people feel talked "
						"down to and stops them asking the question they actually had. And <b>blame</b> "
						"— of the installer, the last technician, another trade, or the client's own "
						"maintenance. Blame fixes nothing, it is very often wrong because you are seeing "
						"the last page of a story you did not watch, and it converts a repair "
						"conversation into a dispute.</p>"
						"<p>Describe the condition and the options. Who is at fault is a commercial "
						"question, and there are people whose job it is.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "What you must not commit to",
					"content": (
						"<p><b>Price. Scope. Dates. Warranty coverage. Cause and liability.</b> Those are "
						"commitments the company makes, not the technician on site — and they are made "
						"in writing by someone with the authority to make them.</p>"
						"<p>They slip out as kindness. “We'll take care of that for you” lands "
						"as a free scope change. “That should be under warranty” lands as a "
						"promise. A number said out loud as a rough idea becomes the number the client "
						"remembers and quotes back.</p>"
						"<p>The safe shape of the answer is the same every time: here is what I am doing "
						"today, I will pass that request on, and somebody will come back to you on it. "
						"That is not a brush-off — it is the only answer that will still be true next "
						"week.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "“I will find out” is a professional answer",
					"content": (
						"<p>Not knowing something is normal. Guessing in front of a client is what turns "
						"it into a problem, because a confident wrong answer gets repeated, acted on, "
						"and quoted back at you weeks later when it has cost somebody money.</p>"
						"<p>There is nothing weak about “I don't want to guess at that — let me "
						"check and get you a proper answer.” Clients trust it more than "
						"improvisation, and it is almost always the right answer to anything that turns "
						"out to be about money, scope or safety.</p>"
						"<p>Then close the loop. An “I'll find out” that never comes back is "
						"worse than having said nothing at all, because it was a promise. And write down "
						"what you told somebody: who you spoke to, when, what you said and what they "
						"asked for. Everyone in a conversation remembers it differently, and the note "
						"made at the time is what stops a misunderstanding turning into an "
						"argument.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before you leave a site where you spoke to the client",
					"items": [
						"You told them what you did and what you found, in plain language",
						"Anything you were not certain about was said as uncertain",
						"No price, date, warranty position or cause was committed to",
						"Requests outside today's work were written down and passed on, not absorbed",
						"Anything touching money, scope or safety went up the line rather than being settled on site",
						"You wrote down who you spoke to and what you told them",
						"They know what happens next and who will come back to them",
						"The feature is left in a known state — running, off, or isolated and tagged — and they know which",
						"The work area is clean and nothing was left where somebody can trip over it",
					],
				},
				ask_block(
					"Escalation, authority and response are Sapphire's to set",
					"<p>Who you call, what you are authorised to approve on your own, what counts as an "
					"emergency and how quickly somebody is expected to respond are decisions your "
					"supervisor and the company make. This course does not know them and will not "
					"invent them — a made-up escalation path is worse than none, because people rely on "
					"it.</p>"
					"<p>Ask for them, in writing if you can, <b>before</b> the first time you need them. "
					"The one rule this course will state on its own: anything involving money, scope or "
					"a safety concern goes up the line, and it goes up before you resolve it on "
					"site.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A client asks whether a failed pump will be covered under warranty. What is the right response?",
						"type": "Single Choice",
						"explanation": (
							"Warranty coverage is a company commitment made by somebody with the authority to "
							"make it. Even a hedged answer on site gets remembered as a promise."
						),
						"options": [
							{
								"text": "Say you will pass it on and somebody will come back to them with an answer",
								"is_correct": True,
							},
							{
								"text": "Give them your honest opinion, making clear it is only an opinion",
								"is_correct": False,
							},
							{
								"text": "Tell them it should be covered, since the equipment is fairly new",
								"is_correct": False,
							},
							{
								"text": "Tell them it will not be covered, so they are not disappointed later",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why keep blame out of an explanation to a client, even when you are fairly sure who caused the problem?",
						"type": "Multiple Choice",
						"explanation": (
							"You are seeing the end of a story you did not watch, so field judgements about "
							"cause are often wrong — and naming a culprit turns a repair conversation into a "
							"commercial dispute handled by other people."
						),
						"options": [
							{
								"text": "You are seeing the end of a story you did not watch, so you are often wrong",
								"is_correct": True,
							},
							{
								"text": "Fault is a commercial question handled by people whose job it is",
								"is_correct": True,
							},
							{
								"text": "The client is never interested in why something failed",
								"is_correct": False,
							},
							{
								"text": "It is always the previous contractor, so it goes without saying",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A client asks a question you genuinely cannot answer. What is the professional response?",
						"type": "Single Choice",
						"explanation": (
							"Say you will find out, and then actually come back. A confident guess gets "
							"repeated and acted on, and an “I'll find out” that never returns was a "
							"broken promise."
						),
						"options": [
							{"text": "Say you will find out, and then close the loop", "is_correct": True},
							{
								"text": "Give your best guess, since the client wants an answer now",
								"is_correct": False,
							},
							{
								"text": "Change the subject to what you are there to do today",
								"is_correct": False,
							},
							{
								"text": "Tell them nobody could answer that without a full survey",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
