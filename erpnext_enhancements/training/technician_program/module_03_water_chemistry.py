# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 3 — Water Chemistry.

Rebuilt from Sapphire's own module document, *Module 3: Aquatic Chemistry, Sanitation, & Water
Quality*. Where that document states a figure it is used as written and it outranks what general
practice would have said here: the ORP band, the DPD-1 reagent rule, the bleach-out dilution, the
cold-water threshold, the probe soak and buffer order, the breakpoint dose, the UV lamp hours and
the LSI band are all Sapphire's.

Two places where the document and the previous draft disagreed, resolved in the document's favour
and said so in the lesson text rather than split. First, the draft refused to print any chemistry
target at all; the document prints two -- the ORP band and the saturation index band -- so those
are printed here and attributed. Second, the draft wrote the saturation index with a
total-dissolved-solids term; the document works it from five site tests, with cyanuric acid
subtracted from total alkalinity instead.

Topics the document does not reach — biofilm, nutrient load, Legionella, sequestrants, evaporative
concentration — keep the draft's content and the draft's discipline about numbers.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 3 — Water Chemistry",
		"summary": (
			"Take a reading you can defend and recognise the times the test itself is lying, keep "
			"the probes and the chemical feeders honest, run a breakpoint shock without hurting "
			"anybody or the finish, clear algae and biofilm, and balance the water so it neither "
			"scales the equipment nor eats the stone."
		),
		"category": "Water Chemistry",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{
			"title": "What the readings actually mean",
			"description": "pH, alkalinity, hardness, stabiliser, ORP, and the three chlorines.",
		},
		{
			"title": "Getting a number you can trust",
			"description": "Sampling, the core tests, the probes, and the honest limits of each.",
		},
		{
			"title": "Correcting the water",
			"description": "Shocking and feeders, algae, UV, and the scaling-versus-corrosive balance.",
		},
	],
	"lessons": [
		{
			"lesson_title": "Basic water chemistry",
			"chapter": 0,
			"estimated_minutes": 20,
			"summary": "What each reading controls, why they are not independent, and the two numbers Sapphire's document does put in writing.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "pH is a ratio, and the scale is logarithmic",
					"content": (
						"<p>pH measures the <b>hydrogen ion concentration</b> — how acidic or basic "
						"the water is. It runs from 0 to 14, and each whole number is a "
						"<b>tenfold</b> change. A reading one unit off target is not slightly off. "
						"It is ten times off. Two units is a hundred times.</p>"
						"<p>That is why pH is the reading that moves everything else, and why a "
						"small correction can overshoot badly. Sapphire's document calls it the "
						"most dominant factor in the water balance, and four things depend on "
						"it:</p>"
						"<ul>"
						"<li><b>Sanitiser effectiveness.</b> Chlorine in water exists as two forms "
						"in a balance: hypochlorous acid, which is the strong killer, and the "
						"hypochlorite ion, which is far weaker. <b>The balance between them is set "
						"by pH</b>, and it shifts toward the weak form as pH rises. So the same "
						"free chlorine reading does substantially less work in high-pH water. The "
						"test reads concentration; it does not read strength.</li>"
						"<li><b>Scaling.</b> High pH causes rapid scaling — minerals come out of "
						"solution and deposit on stone, tile, nozzles and quartz sleeves.</li>"
						"<li><b>Corrosion.</b> Low pH causes rapid corrosion — it attacks metal, "
						"grout and cementitious finishes. Scaling and corrosion are the two ends of "
						"the last lesson in this module.</li>"
						"<li><b>Comfort and irritation.</b> Water well away from the range the body "
						"is comfortable in stings eyes and dries skin. On a feature people touch "
						"that is one source of a complaint — though combined chlorine, later in "
						"this lesson, is more often the real one.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Alkalinity is the buffer, and that is why you fix it first",
					"content": (
						"<p>Total alkalinity is not a measure of how basic the water is. It is the "
						"water's <b>buffering capacity to stop pH spikes</b> — its ability to "
						"absorb acid or base without the pH moving, mostly carbonate and "
						"bicarbonate sitting in reserve.</p>"
						"<p>Get it wrong in either direction and pH stops behaving:</p>"
						"<ul>"
						"<li><b>Too little buffer</b> and pH bounces. Every dose swings it, rain "
						"swings it, the aeration from the display itself swings it. You correct "
						"it, come back, and it has moved again. It is not that somebody keeps "
						"changing it — there is nothing holding it.</li>"
						"<li><b>Too much buffer</b> and pH locks. It sits high and resists "
						"correction, you add more and more acid to shift it, and it drifts back "
						"up. You are fighting the reserve rather than the reading.</li>"
						"</ul>"
						"<p>So <b>correct alkalinity first, then pH</b>. Chasing pH in water with "
						"no buffer is writing on water, and chasing it in over-buffered water is "
						"pouring chemicals at a number that was never going to move.</p>"
						"<p>Note that the two are coupled: adjusting alkalinity moves pH, and "
						"muriatic acid depresses both at once. That coupling is the lever the "
						"balancing protocol in the last lesson uses. Expect to come back and "
						"re-read.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Soft water goes and gets its own calcium",
					"content": (
						"<p>Water is a solvent, and water that is short of dissolved calcium "
						"carbonate does not stay short of it. Sapphire's document calls this "
						"<b>hungry water</b>, and it is exact: the water dissolves calcium out of "
						"whatever it is touching — <b>masonry grout, stone mortars, plaster "
						"linings and copper fittings</b>.</p>"
						"<p>What that looks like on site: plaster that goes rough and then pitted, "
						"grout washing out of joints until the tile is loose, a concrete basin "
						"that gets progressively more porous, and metal being attacked at the same "
						"time. Once a finish has gone, it does not come back; it gets "
						"replaced.</p>"
						"<p><b>Note the failure direction.</b> Soft water tests as <i>almost "
						"nothing in it</i>, which reads as clean. There is no cloudiness, no "
						"smell, no deposit, and nothing on a strip that looks alarming — while the "
						"feature is quietly being consumed. Hardness that is too <i>high</i> is "
						"the opposite problem and announces itself as scale. The hungry side is "
						"the one that is expensive precisely because it is invisible.</p>"
						"<p>Filling a soft-water feature with softened water makes this worse, not "
						"better: a domestic softener removes exactly the calcium the surface needs "
						"the water to already have.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "ORP is the effectiveness; the chlorine test is only the quantity",
					"content": (
						"<p>Oxidation-reduction potential measures the <b>sanitising work "
						"potential</b> of the water, in millivolts. It is a different question "
						"from the one a chlorine test answers, and the difference is the whole "
						"point of measuring it:</p>"
						"<ul>"
						"<li><b>Free chlorine tells you how much sanitiser is present.</b></li>"
						"<li><b>ORP tells you how hard that sanitiser is actually working.</b></li>"
						"</ul>"
						"<p>Sapphire's document sets the band: <b>a healthy commercial fountain "
						"should maintain an ORP between 650 mV and 750 mV</b>. That figure is "
						"Sapphire's and it is the one to work to.</p>"
						"<p>The two readings coming apart is information rather than a fault. A "
						"respectable free chlorine number sitting alongside a low ORP means the "
						"sanitiser that is present is not doing much — high pH pushing it into the "
						"weak form, stabiliser holding it in reserve, or an organic load consuming "
						"it as fast as it arrives. ORP also moves with pH on its own, which is why "
						"a controller steering on ORP can spend a week chasing what is really a pH "
						"problem.</p>"
					),
				},
				ask_block(
					"A decorative feature is not a pool — and two of these numbers are now Sapphire's",
					"<p>The previous draft of this course refused to print any chemistry target at "
					"all. Sapphire's own module document prints two, so this course now prints "
					"them and says whose they are: the <b>ORP band of 650 to 750 mV</b> in this "
					"lesson, and the <b>saturation index band of -0.3 to +0.3</b> in the last one. "
					"Where the document speaks, it wins.</p>"
					"<p>It does not speak about the rest. pH, total alkalinity, calcium hardness, "
					"stabiliser and the free chlorine residual in parts per million still belong "
					"to <b>a particular feature</b> — its water treatment design, the finish and "
					"equipment manufacturers, and the health authority with jurisdiction over "
					"it.</p>"
					"<p>And the jurisdiction question is a real one. An interactive feature people "
					"stand in — a splash pad, a wading basin, anything designed for contact — is "
					"commonly regulated as an aquatic venue under the local adoption of the "
					"International Swimming Pool and Spa Code or a state pool code, with mandated "
					"chemistry, mandated testing and an inspected log. A display fountain nobody "
					"touches may be under none of that, and may be run on a completely different "
					"treatment program.</p>"
					"<p>Find out which one you are standing in front of <b>before</b> you dose "
					"anything.</p>",
				),
				{
					"block_type": "Rich Text",
					"heading": "Cyanuric acid is chlorine's sunscreen, and it has two catches",
					"content": (
						"<p>Ultraviolet light destroys free chlorine. Outdoors in sun, an "
						"unstabilised residual disappears fast — you dose in the morning and by "
						"afternoon there is nothing left. Cyanuric acid binds to free chlorine and "
						"shields it from sunlight, which is why outdoor features use it.</p>"
						"<p>The first catch is the mechanism itself. <b>The bound chlorine is in "
						"reserve, not at work.</b> As the stabiliser level climbs, a larger share "
						"of your free chlorine is parked rather than sanitising, so the same test "
						"reading does less. Push it far enough and you get water that reads a "
						"perfectly respectable free chlorine number and will not hold against "
						"anything — the over-stabilisation trap. The test is not lying; it is "
						"answering a different question than the one you are asking. ORP is "
						"usually where you see it first.</p>"
						"<p>The second catch is arithmetic, and it belongs to the last lesson. "
						"<b>Cyanuric acid has to be mathematically subtracted from the total "
						"alkalinity reading when you calculate the saturation index.</b> "
						"Stabiliser registers as alkalinity on the test and does not buffer like "
						"it, so leaving it in makes the water look better balanced than it "
						"is.</p>"
						"<p>Two things make it creep up without anybody deciding it should:</p>"
						"<ul>"
						"<li><b>Stabilised chlorine products add it every time.</b> Dichlor and "
						"trichlor carry cyanuric acid with them, so a feature dosed on those "
						"accumulates stabiliser as a side effect of routine sanitising.</li>"
						"<li><b>It does not leave.</b> Cyanuric acid does not evaporate, does not "
						"burn off in sun and is not consumed by chlorine. Practically, it leaves "
						"with the water — dilution, a partial drain and refill — or through a "
						"specialist process. Waiting is not a strategy.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Free, combined, total — and the smell everybody blames on chlorine",
					"content": (
						"<p>Three numbers, and the relationship between them is the diagnosis:</p>"
						"<ul>"
						"<li><b>Free chlorine</b> is the active sanitiser available to kill "
						"pathogens and algae. This is the working number.</li>"
						"<li><b>Combined chlorine</b> is chlorine that has already reacted with "
						"ammonia and nitrogen compounds — sweat, urine, skin, leaves, birds, dust "
						"— and formed chloramines. It is spent. It is a weak sanitiser, it is a "
						"strong irritant, and it is the byproduct, not the product.</li>"
						"<li><b>Total chlorine</b> is the two added together, which is why "
						"<b>combined equals total minus free</b>. That subtraction is the number "
						"that tells you a feature needs shocking, and Sapphire's breakpoint dose "
						"in the shocking lesson is calculated <i>directly from it</i> — which is "
						"why a test that reports only total chlorine cannot tell you what to "
						"do.</li>"
						"</ul>"
						"<p>Here is the part worth carrying off site. <b>The sharp chemical smell "
						"and the stinging eyes are combined chlorine.</b> Free chlorine has very "
						"little odour. So when somebody says there is too much chlorine in the "
						"water because of the smell, the water is very often telling you the "
						"opposite — there has not been enough free chlorine to finish the job, and "
						"the half-finished products of that are what they can smell.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "The vocabulary, straight",
					"cards": [
						{
							"front": "pH",
							"back": "Hydrogen ion concentration — how acidic or basic the water is, on a logarithmic 0-14 scale. One whole unit is a tenfold change. The most dominant factor in the balance: high pH scales rapidly, low pH corrodes rapidly.",
						},
						{
							"front": "Total alkalinity",
							"back": "The water's buffering capacity to stop pH spikes. Too little and pH bounces; too much and pH locks. Correct it before pH.",
						},
						{
							"front": "Calcium hardness",
							"back": "Dissolved calcium. Too little and the water goes hungry, dissolving calcium out of grout, mortar, plaster and concrete. Too much and it deposits as scale.",
						},
						{
							"front": "Cyanuric acid (stabiliser)",
							"back": "Shields free chlorine from sunlight by binding it. The bound share is held in reserve rather than working, it only leaves with the water, and it must be subtracted from total alkalinity when calculating the saturation index.",
						},
						{
							"front": "Free chlorine",
							"back": "The active sanitiser still available to kill pathogens and algae. The working number. Tested with DPD-1.",
						},
						{
							"front": "Combined chlorine (chloramines)",
							"back": "Chlorine already reacted with nitrogen compounds. Spent, weakly sanitising, strongly irritating. Total minus free — and the number the breakpoint dose is calculated from.",
						},
						{
							"front": "DPD-1 versus DPD-3",
							"back": "DPD-1 reagent, tablet or powder pillow, is the free chlorine test and is what you reach for. DPD-3 is for combined chlorine only. Using DPD-3 when you wanted free chlorine gives you a number that answers a different question.",
						},
						{
							"front": "ORP (oxidation-reduction potential)",
							"back": "The sanitising work potential of the water in millivolts — how hard the sanitiser is working, rather than how much of it is there. Sapphire's document sets a healthy commercial fountain at 650 to 750 mV. It moves with pH, so an ORP controller can chase a pH problem.",
						},
						{
							"front": "Breakpoint",
							"back": "The dose at which added chlorine has destroyed all the combined chlorine and starts staying in the water as free chlorine. Sapphire's target: ten times the combined chlorine, plus 5 ppm.",
						},
						{
							"front": "TDS (total dissolved solids)",
							"back": "Everything dissolved in the water added together. It climbs as water evaporates and the make-up keeps arriving. Sapphire's saturation index method does not carry a TDS term — it works from five readings instead.",
						},
					],
				},
			],
			"quiz": {
				"questions": [
					{
						"question": "A feature's pH reads a full unit above where the design wants it. How far off is that?",
						"type": "Single Choice",
						"explanation": (
							"The pH scale is logarithmic, so one whole unit is a tenfold change. A reading that "
							"looks like a small miss on the strip is not a small miss in the water."
						),
						"options": [
							{
								"text": "Ten times off — the scale is logarithmic, so each whole unit is a factor of ten",
								"is_correct": True,
							},
							{
								"text": "About seven percent off, since one unit of the fourteen has moved",
								"is_correct": False,
							},
							{"text": "Twice off — each unit doubles", "is_correct": False},
							{
								"text": "It cannot be judged without the alkalinity reading",
								"is_correct": False,
							},
						],
					},
					{
						"question": "What ORP band does Sapphire's module document set for a healthy commercial fountain, and what is ORP telling you?",
						"type": "Single Choice",
						"explanation": (
							"Sapphire's document states 650 to 750 mV. ORP measures the sanitising work potential "
							"of the water — how effective the sanitiser is — while the chlorine test measures how "
							"much of it is present. The two can disagree, and that disagreement is information."
						),
						"options": [
							{
								"text": "650 to 750 mV, and it measures how effectively the sanitiser is working rather than how much is present",
								"is_correct": True,
							},
							{
								"text": "650 to 750 mV, and it is another way of reading the free chlorine concentration",
								"is_correct": False,
							},
							{
								"text": "200 to 300 mV, and it measures the water's buffering capacity",
								"is_correct": False,
							},
							{
								"text": "There is no band — ORP is specific to each feature's treatment design",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A visitor complains that the fountain smells strongly of chlorine and the smell is making their eyes sting. What is the most likely chemistry behind it?",
						"type": "Single Choice",
						"explanation": (
							"Free chlorine has little odour. The sharp smell and the stinging are combined "
							"chlorine — chloramines — which are the spent byproduct of not having had enough free "
							"chlorine to finish the reaction."
						),
						"options": [
							{
								"text": "Combined chlorine, which usually means there has not been enough free chlorine",
								"is_correct": True,
							},
							{
								"text": "Too much free chlorine, which needs to be allowed to fall",
								"is_correct": False,
							},
							{"text": "Cyanuric acid being released as the water warms", "is_correct": False},
							{"text": "Low pH, which is what people smell as chlorine", "is_correct": False},
						],
					},
					{
						"question": "Which of these are true of cyanuric acid?",
						"type": "Multiple Choice",
						"explanation": (
							"It shields free chlorine from sunlight by binding it, which also holds that share in "
							"reserve rather than at work; it has to be subtracted from total alkalinity when the "
							"saturation index is calculated; and stabilised products add more every time they are "
							"used. It does not make each unit of chlorine stronger — the opposite."
						),
						"options": [
							{
								"text": "It protects free chlorine from being destroyed by sunlight",
								"is_correct": True,
							},
							{
								"text": "It must be mathematically subtracted from total alkalinity when calculating the saturation index",
								"is_correct": True,
							},
							{
								"text": "Stabilised products such as dichlor and trichlor add more of it with every dose",
								"is_correct": True,
							},
							{
								"text": "It increases the sanitising strength of each unit of free chlorine",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Water testing",
			"chapter": 1,
			"estimated_minutes": 20,
			"summary": "Taking a sample that represents the feature, running Sapphire's core tests the way the document specifies, and recognising the times the test itself is what is wrong.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "You are testing the sample, not the feature",
					"content": (
						"<p>Every reading you take is a statement about the water in that vial. "
						"Whether it is also a statement about the feature depends entirely on "
						"where the vial was filled.</p>"
						"<p><b>Away from a return inlet.</b> Water coming out of a return is the "
						"output of the treatment equipment, not the body of water. Sample there "
						"and you are testing the feeder. Stay well away from the returns and well "
						"away from any chemical feed point.</p>"
						"<p><b>Below the surface.</b> The top film of the water is the part that "
						"has been baked by sun, concentrated by evaporation and covered by "
						"whatever blew in — oils, pollen, dust. It is not representative of "
						"anything. Reach down, roughly elbow depth, and fill the vial there with "
						"the mouth pointing down until it is under, then turn it.</p>"
						"<p><b>With the circulation running, and having run.</b> A basin that has "
						"been still overnight is stratified: warmer at the top, chemically "
						"different at depth, and sanitiser unevenly distributed. Sampling a "
						"stagnant feature reads a layer.</p>"
						"<p><b>Away from dead corners.</b> A still corner behind a weir or under a "
						"ledge is exactly where a problem hides, which makes it a useful "
						"<i>diagnostic</i> sample — but label it as one. Do not use it as the "
						"reading you dose the whole feature from.</p>"
						"<p><b>And test it where you drew it.</b> Sapphire's document is explicit "
						"that pH readings must be taken <b>immediately</b> after the sample is "
						"collected: exposure to air changes the water's carbon dioxide level and "
						"alters the true pH. A vial carried back to the truck and read on the "
						"tailgate is not the same water you dipped.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The three core tests, and the reagent rules that go with them",
					"content": (
						"<p>Sapphire's document names the three readings a technician is expected "
						"to gather, and how each is taken.</p>"
						"<ul>"
						"<li><b>pH.</b> A digital photometer or a liquid drop kit using "
						"<b>phenol red</b>. Read it immediately, for the carbon dioxide reason "
						"above.</li>"
						"<li><b>Free chlorine.</b> <b>Always DPD-1</b> — tablets or powder "
						"pillows. <b>Never DPD-3</b> unless what you are actually after is "
						"combined chlorine, the chloramines. The two reagents are easy to confuse "
						"in a kit and they answer different questions, so a DPD-3 reading logged "
						"as free chlorine is not a slightly wrong number, it is the wrong "
						"quantity.</li>"
						"<li><b>ORP.</b> Read in millivolts off the controller or a hand-held "
						"meter, against the 650 to 750 mV band from the previous lesson. An ORP "
						"reading is only as good as the probe behind it, which is the last block "
						"in this lesson.</li>"
						"</ul>"
						"<p>Combined chlorine is still total minus free, and you still need it — "
						"it is what the breakpoint dose is calculated from. That is the one place "
						"DPD-3 belongs.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Rinse the vial with the water you are about to test",
					"content": (
						"<p>These are tiny volumes. A few drops of the last sample, a smear of the "
						"last reagent, or a rinse under a tap left in the bottom of the vial is a "
						"meaningful fraction of what you are about to measure. <b>Rinse the vial "
						"two or three times with the sample water itself</b> and tip it out before "
						"the sample you keep.</p>"
						"<p>Then the mechanical part, which is where most avoidable error comes "
						"from:</p>"
						"<ul>"
						"<li>Fill to the line, reading the meniscus <b>at eye level</b>. Read "
						"it from above and the water looks higher than it is, so you stop short "
						"of the line; read it from below and you fill past it. Either way the "
						"reagent-to-sample ratio is wrong, and on a titration that is the whole "
						"result.</li>"
						"<li>Hold the reagent bottle <b>vertical</b> when counting drops. Held at "
						"an angle it delivers a different drop size, and a drop count is a "
						"measurement.</li>"
						"<li>Cap it and invert it to mix. A thumb over the top puts whatever is on "
						"your hands into the sample — and sunscreen, hand cleaner and the residue "
						"off the last chemical container all read.</li>"
						"<li>Never pipette by mouth, and never tip a sample that has reagent in it "
						"back into the feature.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Three ways to get a number, and what each is honestly good for",
					"panels": [
						{
							"title": "Test strips — fast, no skill floor, a band rather than a number",
							"body": (
								"<p>Dip, wait the time the bottle states, compare against the colour chart. Fast "
								"enough to use on every visit and cheap enough to use often.</p>"
								"<p>What they honestly give you is a <b>range</b>, not a value — and your eye "
								"matching a printed block, which is worse in poor light, worse under a coloured "
								"deck light, and worse for a colour-blind technician. Use them for <i>has "
								"anything moved since last time</i>. Do not calculate a dose from one.</p>"
								"<p>They are also the most fragile thing in the kit. The pads are hygroscopic: a "
								"lid left off, a wet hand in the bottle, or a summer in a hot truck kills the "
								"whole bottle, and a dead strip still produces a colour.</p>"
							),
						},
						{
							"title": "Drop kits — the workhorse, if you are honest with yourself",
							"body": (
								"<p>Two different things live under this heading. A <b>titration</b>, like a "
								"hardness or alkalinity test, counts drops until the colour changes; the count is "
								"the measurement and it is genuinely quantitative. A <b>colour comparator</b>, "
								"like the phenol red pH kit Sapphire's document names, asks your eye to match a "
								"block again — better resolution than a strip, same subjectivity.</p>"
								"<p>The important capability here is <b>DPD</b>: DPD-1 for free chlorine, DPD-3 "
								"for combined, and the subtraction between them is what tells you whether the "
								"feature needs shocking and how hard. A test that reports only total chlorine "
								"cannot answer either question.</p>"
								"<p>Held wrong, counted optimistically, or read in bad light, a drop kit is only "
								"as good as the hand holding it.</p>"
							),
						},
						{
							"title": "Photometers — precision is not accuracy",
							"body": (
								"<p>A photometer or colorimeter reads the developed colour by instrument and "
								"gives you a number with decimal places. That removes your eye from the loop, "
								"which is a real improvement, and it makes results repeatable between "
								"technicians. It is the first method Sapphire's document reaches for on pH and "
								"free chlorine, and the module's own performance checklist asks you to "
								"demonstrate it.</p>"
								"<p>It removes nothing else. The reagent can be expired, the sample can be "
								"unrepresentative, the vial can be scratched or fingerprinted or filled to the "
								"wrong line, the water can be too cold for the reaction, and the instrument "
								"itself drifts and needs calibrating against a standard. All of those produce a "
								"confident number to two decimal places that is simply wrong — and it is far "
								"harder to disbelieve a display than a smudgy colour.</p>"
								"<p>Precision is how repeatable the number is. Accuracy is whether it is true. An "
								"instrument gives you the first one for free and the second one never.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Three ways a test reads low on water that is not low",
					"content": (
						"<p><b>The chlorine bleach-out.</b> This one catches people, and Sapphire's "
						"document gives both the threshold and the fix. Where free chlorine is "
						"exceptionally high — <b>above 10 to 15 ppm</b> — it instantly bleaches out "
						"the DPD-1 indicator dye and the sample turns clear, which reads as "
						"<b>zero chlorine</b>. The instinct is to add more. If you suspect a "
						"bleach-out, <b>dilute the sample with 50% distilled water, re-test, and "
						"multiply the result by 2</b>.</p>"
						"<p><b>Cold water.</b> Below <b>60&deg;F</b> the reaction between the water "
						"and the test reagents slows down, and a slow reaction reads as a low "
						"result. On early spring commissions, <b>warm the testing vial in your hand "
						"for a minute</b> before adding the reagent.</p>"
						"<p><b>Expired or cooked reagents.</b> Reagents carry an expiry date, and "
						"that date assumes storage in the dark at sane temperatures — not a season "
						"on a dashboard. DPD degrades, phenol red drifts. The characteristic "
						"failure is <b>reading low or not developing at all</b>, so the water "
						"looks like it needs more of everything.</p>"
						"<p>All three fail in the same direction: they under-report, and the "
						"correction they invite is to add chemical the water did not need.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cleaning and calibrating the probes",
					"content": (
						"<p>The pH and ORP probes in a controller's inline flow cell are glass "
						"sensing bulbs sitting in moving water, and they <b>gather an organic "
						"microfilm</b>. The film slows their response and drifts their calibration, "
						"so the controller keeps dosing accurately against a number that is no "
						"longer true.</p>"
						"<p>Sapphire's document makes this a monthly job, and specifies it:</p>"
						"<ul>"
						"<li>Remove the probes from the inline flow cell.</li>"
						"<li>Soak them in a <b>5% muriatic acid solution for five minutes</b>.</li>"
						"<li>Scrub gently with a <b>soft toothbrush</b>. Gently — the bulb is "
						"glass and it is the sensor.</li>"
						"<li>Rinse with clean water.</li>"
						"</ul>"
						"<p><b>Then calibrate, in order.</b> Place the probes into certified "
						"reference buffer solutions: <b>pH 7.0 first, then pH 4.0 or 10.0</b>. "
						"Adjust the controller's offset calibration until the digital screen "
						"exactly matches the value printed on the reference bottle. Use fresh "
						"buffer — an open bottle that has been contaminated by a dirty probe is a "
						"standard that is no longer a standard, and it will calibrate the "
						"controller to be wrong with great confidence.</p>"
						"<p>The acid solution is still acid: mix it and handle it under the rules "
						"in the next lesson, acid into water and never near a chlorine "
						"product.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "One reading is a position; a series is a direction",
					"content": (
						"<p>A single number tells you where the water is right now. A run of them "
						"tells you which way it is heading and how fast, which is the only thing "
						"that lets you act before there is a problem instead of after.</p>"
						"<p>Write down the value, the date and time, the method you used, the "
						"water temperature and anything you added. With a rate of change in front "
						"of you, a chlorine residual that is falling steadily every visit reads as "
						"a growing demand somewhere in the system — that is the biofilm "
						"conversation in the algae lesson — while one that fell off a cliff once "
						"reads as an event.</p>"
						"<p>And when a reading is surprising, <b>re-test before you dose</b>. The "
						"most common cause of an alarming result is the test: a contaminated vial, "
						"a bad strip, a reagent past its date, a cold sample, a drifted probe, a "
						"sample taken in the wrong place. Confirming costs a couple of minutes. "
						"Dosing a feature on a false reading costs considerably more than "
						"that.</p>"
					),
				},
				ask_block(
					"Which parameters, how often, and against what",
					"<p>Sapphire's document sets the method — DPD-1 for free chlorine, phenol red "
					"or a photometer for pH, a monthly probe clean and calibration — and the ORP "
					"band to read against. Those are settled.</p>"
					"<p>What is not settled here is the rest: which parameters a given feature is "
					"logged on, how often, and the target range for each. Those are set by the "
					"feature's water treatment design and by the health authority over it — and "
					"on a regulated interactive feature the frequency and the log are often "
					"mandated and inspected.</p>"
					"<p>If you do not know what the schedule is for the feature in front of you, "
					"ask your supervisor rather than inventing a routine.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Where should a routine sample be taken from?",
						"type": "Single Choice",
						"explanation": (
							"A return is the equipment's output, not the body of water, and the surface film is "
							"concentrated and contaminated. Below the surface, away from returns and feed points, "
							"with the circulation running is the only sample that represents the feature — and pH "
							"is read immediately, before air exposure shifts the carbon dioxide."
						),
						"options": [
							{
								"text": "Below the surface, away from returns and chemical feed points, with the circulation running",
								"is_correct": True,
							},
							{
								"text": "Right at a return inlet, where the treated water is freshest",
								"is_correct": False,
							},
							{
								"text": "Skimmed off the surface, which is the water people actually contact",
								"is_correct": False,
							},
							{
								"text": "From the skimmer basket, because everything in the feature passes through it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You are testing free chlorine. Which reagent does Sapphire's document tell you to use, and when is the other one correct?",
						"type": "Single Choice",
						"explanation": (
							"Always DPD-1, as a tablet or a powder pillow, for free chlorine. DPD-3 is only for "
							"checking combined chlorine — the chloramines. They sit next to each other in the kit "
							"and they answer different questions."
						),
						"options": [
							{
								"text": "DPD-1 always; DPD-3 only when you are checking combined chlorine",
								"is_correct": True,
							},
							{
								"text": "DPD-3 always; DPD-1 only when you are checking combined chlorine",
								"is_correct": False,
							},
							{
								"text": "Either one — they read the same chlorine and differ only in form",
								"is_correct": False,
							},
							{
								"text": "Phenol red, which reads free chlorine and pH from the same vial",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You shock a feature heavily, test it shortly afterwards, and the DPD-1 free chlorine test reads almost zero. What has happened and what does Sapphire's document tell you to do?",
						"type": "Single Choice",
						"explanation": (
							"Above roughly 10 to 15 ppm the chlorine bleaches the DPD-1 dye out as fast as it "
							"develops, so an enormously over-chlorinated sample reads clear. Dilute the sample "
							"with 50% distilled water, re-test, and multiply the result by 2."
						),
						"options": [
							{
								"text": "The chlorine bleached the indicator — dilute the sample with 50% distilled water, re-test, and multiply by 2",
								"is_correct": True,
							},
							{
								"text": "The shock was consumed instantly, so more is needed",
								"is_correct": False,
							},
							{
								"text": "The stabiliser has bound all of the free chlorine",
								"is_correct": False,
							},
							{"text": "The reagent is expired and should be replaced", "is_correct": False},
						],
					},
					{
						"question": "Which of these can make a test report less than the water actually holds?",
						"type": "Multiple Choice",
						"explanation": (
							"Cold water slows the reagent reaction, a cooked or expired reagent under-develops or "
							"does not develop at all, and very high chlorine bleaches the DPD-1 dye clear. All "
							"three under-report, and all three invite a dose the water did not need. Rinsing the "
							"vial with the sample water is what prevents carryover error, not what causes it."
						),
						"options": [
							{
								"text": "Water below 60 degrees Fahrenheit slowing the reaction with the reagent",
								"is_correct": True,
							},
							{
								"text": "A reagent past its date or cooked on a truck dashboard",
								"is_correct": True,
							},
							{
								"text": "Free chlorine high enough to bleach the DPD-1 indicator clear",
								"is_correct": True,
							},
							{
								"text": "Rinsing the vial two or three times with the sample water before filling it",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Chemical shocking",
			"chapter": 2,
			"estimated_minutes": 20,
			"summary": "What a breakpoint shock is actually doing, Sapphire's dose for it, and the feeder and interlock rules that exist because people have been gassed.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Shocking is oxidation, not just more sanitiser",
					"content": (
						"<p>The purpose of a shock is to <b>destroy what is already in the water</b> "
						"— combined chlorine, and the organic load that keeps making it. Raising the "
						"free chlorine number is the means, not the goal.</p>"
						"<p>The chemistry is <b>breakpoint chlorination</b>, and the shape of it is "
						"what matters. As you add chlorine to water carrying ammonia and nitrogen "
						"compounds, the first thing it does is form <i>more</i> chloramines. Keep "
						"adding, and at a certain point the reaction flips and the chlorine starts "
						"destroying them instead. That flip is not yet the breakpoint. Keep going "
						"and the combined chlorine falls away to nothing; the dose at which it is "
						"gone and the chlorine you add starts staying in the water as free "
						"chlorine is the <b>breakpoint</b>. There is no credit for getting close "
						"to it.</p>"
						"<p>The consequence is the practical lesson: <b>an under-dose is worse than "
						"doing nothing</b>. Stop short of breakpoint and you have manufactured more "
						"combined chlorine than you started with — the feature smells worse, irritates "
						"more, and the technician concludes the shock did not work and that the answer "
						"is a bit less next time. It is the same trap in both directions.</p>"
						"<p>Where the breakpoint sits depends on how much combined chlorine is in the "
						"water, which is why the combined reading is what drives the decision — and "
						"unlike almost every other target in this course, <b>Sapphire's document "
						"gives you the dose</b>. It is in the next block, and it is the one to "
						"use.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Sapphire's breakpoint protocol, in order",
					"content": (
						"<p>This is the document's own sequence for shock dosing a bloom out of a "
						"fountain. The order is part of the instruction.</p>"
						"<ol>"
						"<li><b>Brush the algae first, aggressively.</b> A wire brush on concrete, "
						"a nylon brush on tile and stone. Brushing cracks open the protective "
						"cellular barriers so the chlorine can reach the living cell. Chemistry "
						"cannot kill what it cannot touch.</li>"
						"<li><b>Calculate the total water volume of the system.</b> The whole "
						"system, not the basin you can see — the surge tank, the vault and the "
						"pipework are all carrying water you are dosing.</li>"
						"<li><b>Measure the baseline combined chlorine.</b> Total minus free. "
						"Everything below depends on this number being real, so it is the one to "
						"re-test rather than assume.</li>"
						"<li><b>Calculate the target.</b> Free chlorine must reach <b>ten times the "
						"combined chlorine, plus 5 ppm</b>. That is Sapphire's figure. Ten times a "
						"combined reading you guessed at is ten times a guess.</li>"
						"<li><b>Broadcast the product uniformly, at night.</b> Pre-dissolved "
						"calcium hypochlorite or liquid sodium hypochlorite, spread evenly into "
						"the basin rather than tipped in one place. At night because sunlight "
						"breaks unstabilised chlorine down rapidly — a daytime shock spends a "
						"share of the dose on the sky.</li>"
						"<li><b>Keep the circulation running continuously for 24 hours.</b> The "
						"point is not the basin. It is to scrub the plumbing lines and clear "
						"biological films out of the pump impellers, which is where the reinfection "
						"comes from.</li>"
						"</ol>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Liquid chlorine and muriatic acid make a lethal gas",
					"content": (
						"<p>Sapphire's document flags this as the critical error to avoid, and it is "
						"the one that kills people in fountain plant rooms. <b>Never allow liquid "
						"chlorine and muriatic acid to mix directly.</b> The two together produce "
						"<b>lethal, greenish-yellow chlorine gas</b>.</p>"
						"<p>The mixing rarely looks like mixing. It looks like a shared measuring "
						"jug, a residue left in a re-used container, two feed lines injecting into "
						"the same dead section of pipe, or two drums stored where one can leak into "
						"the bund of the other. <b>One product, one clean dry scoop, one "
						"container</b> — and never re-use a chemical container for another "
						"chemical.</p>"
						"<p>The same discipline covers the rest of the shelf. These products are "
						"oxidisers, acids and organics: calcium hypochlorite and trichlor combined "
						"in a bucket, a feeder or even on a damp scoop can <b>ignite</b>. Sheds "
						"have burned down from this. Read the safety data sheet for what is "
						"actually in your hand, wear what it tells you to wear, and never stack an "
						"oxidiser and an acid on the same shelf.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "The interlock rule: no circulation, no dosing",
					"content": (
						"<p><b>Chemical controllers must be electrically interlocked with the main "
						"circulation pump.</b> If the circulation pump loses prime or shuts down, "
						"power to the chemical dosing controllers and the dosing pumps must cut out "
						"<b>instantly</b>.</p>"
						"<p>The failure this prevents is worth carrying in your head, because a "
						"feature in this state looks completely normal. If the dosing pumps keep "
						"injecting acid and chlorine into <b>stagnant</b> water, nothing carries "
						"the chemical away — so a highly concentrated chemical pocket builds at the "
						"injection point. When the circulation pump restarts, that toxic pocket is "
						"<b>shot into the fountain basin</b> in one slug: dangerous to guests in "
						"and around the water, and corrosive enough to eat copper nozzles.</p>"
						"<p>So an interlock is not an optional refinement on a controller install, "
						"and a bypassed or failed one is a finding to report rather than something "
						"to work around for the afternoon. If you have found a controller dosing "
						"while the pump is off, <b>do not simply restart the pump</b> — that is the "
						"event you are trying to prevent.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The feeder is a maintenance item, not a fitting",
					"content": (
						"<p>An automated loop has two mechanical parts that wear out quietly, and "
						"both of them fail by <b>dosing less than the controller thinks it is "
						"dosing</b>.</p>"
						"<p><b>Peristaltic squeeze tubes.</b> A peristaltic dosing pump works by "
						"rollers squeezing a rubber tube; the tube is the wearing part. Sapphire's "
						"document sets rebuild or replacement of the internal squeeze tubes on a "
						"<b>six-month cycle</b>. A failing tube loses elasticity and the flow "
						"output drops, so the controller calls for a dose the feature never "
						"receives and the chemistry drifts while the screen says everything is "
						"being done. A tube that splits rather than fades is worse: it sprays "
						"concentrated acid or chlorine inside the equipment room.</p>"
						"<p><b>Injection check valves.</b> These are the spring-loaded valves where "
						"the chemical lines thread into the main fountain plumbing. Inspect them. "
						"<b>Acid injection lines frequently clog with scale deposits</b>, which have "
						"to be cleared to prevent chemical backing up under pressure in the feed "
						"line. A partly blocked injection point is the same failure as a tired "
						"squeeze tube: the controller calls, the pump runs, and less arrives than "
						"anybody thinks.</p>"
						"<p>The module's performance checklist asks you to rebuild a peristaltic "
						"drive block and replace a worn squeeze tube, so this is a hands-on "
						"competency rather than a reading.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Non-chlorine shock does a different job",
					"content": (
						"<p>Potassium monopersulfate products oxidise organic contamination without "
						"adding chlorine. They are genuinely useful — on indoor features, on features "
						"where a chlorine spike is not tolerable, and where you want the water back "
						"quickly.</p>"
						"<p>Two honest limits. <b>It does not sanitise.</b> It oxidises. It does not "
						"replace a sanitiser residual and it does not kill algae, so it is not a "
						"substitute for the breakpoint protocol above. And <b>it interferes "
						"with DPD combined-chlorine testing</b>: for a period after dosing, the "
						"monopersulfate itself registers as combined chlorine, so the test tells you "
						"the water is full of chloramines when it is not. Technicians have chased that "
						"false reading with more shock — and since the breakpoint dose is ten times "
						"the combined reading, a false combined reading is a large false dose. Know "
						"what you dosed, and know what your test will do about it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Acid into water. Never water into acid.",
					"content": (
						"<p><b>Acid into water, always, slowly.</b> Acid meeting water releases heat. "
						"Pour a small amount of water into a volume of concentrated acid and the water "
						"flashes to steam at the contact point and throws boiling acid out of the "
						"container, into your face. Adding acid slowly into a large volume of water "
						"spreads that heat through the water instead.</p>"
						"<p>This is not a preference and there is no situation on a fountain where "
						"the other order is correct — not mixing the 5% probe-cleaning solution, not "
						"making up a feeder batch, not adjusting a basin.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Pre-dissolve where the label calls for it, and put it where the water moves",
					"content": (
						"<p>Granular product thrown into a basin sinks. It lands as a concentrated, "
						"hot, chemically extreme pile sitting directly on the finish, and what is "
						"under it gets bleached, etched or stained — permanently, on plaster, on "
						"paint, on a liner, on coloured aggregate. A ring of white marks on a dark "
						"basin floor is somebody's shortcut from two seasons ago. It is also why "
						"Sapphire's protocol says <i>pre-dissolved</i> and <i>uniformly</i>.</p>"
						"<p>How: a clean bucket, filled with water first, then <b>product added "
						"slowly into the water</b> — the same direction as acid, and for the same "
						"reason. Stir with something dedicated to the job. Then pour it in slowly, "
						"around the perimeter, with the circulation running so it is carried and "
						"diluted rather than dropped in one place.</p>"
						"<p>Some products say explicitly <i>not</i> to pre-dissolve, and some are "
						"designed to be fed through equipment rather than broadcast at all. The label "
						"and the treatment design decide that. What does not change is that the "
						"circulation should be moving the water, and that you do not stand downwind "
						"of what you are pouring.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before the product goes in",
					"items": [
						"You know which product it is and you have read its label and safety data sheet",
						"The combined chlorine baseline is freshly measured, because the breakpoint dose is calculated from it",
						"The total system volume is calculated, not estimated from the basin you can see",
						"You are wearing the protective equipment the safety data sheet specifies",
						"The feature is out of the public's reach — barriers and signs, and the public kept clear",
						"Nothing can start the feature mid-treatment — an automatic sequence or a controller call is locked out under the site's energy-control (lock-out/tag-out) procedure, by somebody authorised under it",
						"The circulation is running, or is set the way the product and the design require",
						"Nothing else is being dosed at the same time, and no container or scoop is shared",
						"You are upwind of the container and not leaning over it",
						"You have somewhere to record what went in, how much, and when",
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Re-test on the way back down, before anybody touches the water",
					"content": (
						"<p>The feature goes back into service on <b>a measurement</b>, not on a "
						"clock. Test the free chlorine back down into the range the design specifies, "
						"and re-check the combined figure to confirm the shock actually did its job "
						"rather than stalling short of breakpoint.</p>"
						"<p>Remember the bleaching trap from the testing lesson: immediately after a "
						"heavy shock, a DPD-1 test can read near zero because the chlorine is "
						"destroying the indicator. A near-zero result on freshly shocked water is a "
						"reason to dilute with 50% distilled water, re-test and multiply by 2 — never "
						"a reason to add more.</p>"
						"<p>Check pH afterwards as well, because almost everything you might have "
						"added moved it. Calcium hypochlorite and liquid sodium hypochlorite are "
						"alkaline and push pH up; dichlor and trichlor are acidic and pull it down. A "
						"feature that was balanced before the shock is not balanced after it.</p>"
					),
				},
				ask_block(
					"The product, the tolerance, and when the water is back in service",
					"<p>Sapphire's document settles the <b>dose</b>: ten times the combined "
					"chlorine plus 5 ppm, pre-dissolved calcium hypochlorite or liquid sodium "
					"hypochlorite, broadcast at night, with a continuous 24-hour circulation run "
					"behind it. Use it.</p>"
					"<p>What it does not settle is whether the feature in front of you can take "
					"that treatment. Whether the finish, the metals and the equipment in that "
					"system tolerate a shock at all, which product the design specifies, whether "
					"it is broadcast or fed, and the reading at which the water is safe for people "
					"again all come from the label, the safety data sheet, the water treatment "
					"design and the health authority.</p>"
					"<p>On a regulated interactive feature the re-entry criterion is very often "
					"written down by that authority and is not negotiable. A figure remembered "
					"from another feature is not one. Ask.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why can a shock that stops short of breakpoint leave the water worse than before?",
						"type": "Single Choice",
						"explanation": (
							"Below breakpoint, added chlorine forms more chloramines rather than destroying them. "
							"An under-dose therefore manufactures more combined chlorine, so the smell and the "
							"irritation get worse — which is exactly why the dose is calculated rather than "
							"guessed."
						),
						"options": [
							{
								"text": "Below breakpoint the added chlorine forms more chloramines instead of destroying them",
								"is_correct": True,
							},
							{
								"text": "A partial dose raises pH so far that the sanitiser stops working entirely",
								"is_correct": False,
							},
							{
								"text": "It strips the stabiliser out of the water, so the residual burns off in sun",
								"is_correct": False,
							},
							{
								"text": "It does nothing at all, which is why it has to be repeated",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A fountain tests at 1.5 ppm combined chlorine. What free chlorine target does Sapphire's breakpoint calculation give you?",
						"type": "Single Choice",
						"explanation": (
							"The document's rule is ten times the combined chlorine plus 5 ppm. Ten times 1.5 is "
							"15, plus 5 gives a target of 20 ppm. The whole calculation rests on the combined "
							"reading, which is why it is measured fresh rather than assumed."
						),
						"options": [
							{"text": "20 ppm — ten times the combined reading, plus 5 ppm", "is_correct": True},
							{"text": "15 ppm — ten times the combined reading", "is_correct": False},
							{"text": "6.5 ppm — the combined reading plus 5 ppm", "is_correct": False},
							{"text": "1.5 ppm — matching the combined reading", "is_correct": False},
						],
					},
					{
						"question": "Why must a chemical dosing controller be electrically interlocked with the main circulation pump?",
						"type": "Single Choice",
						"explanation": (
							"With no circulation, nothing carries the chemical away, so continued dosing builds a "
							"concentrated pocket of acid and chlorine at the injection point. When the pump "
							"restarts, that pocket is shot into the basin in one slug — dangerous to guests and "
							"corrosive to copper nozzles."
						),
						"options": [
							{
								"text": "Dosing into stagnant water builds a concentrated pocket that is fired into the basin when the pump restarts",
								"is_correct": True,
							},
							{
								"text": "The controller draws its sensor power from the pump circuit and would otherwise read zero",
								"is_correct": False,
							},
							{
								"text": "It stops the dosing pumps running dry and burning out their motors",
								"is_correct": False,
							},
							{
								"text": "It is a convenience so that one switch shuts the plant room down",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these statements about mixing fountain chemicals are true?",
						"type": "Multiple Choice",
						"explanation": (
							"Liquid chlorine meeting muriatic acid produces lethal greenish-yellow chlorine gas — "
							"the critical error Sapphire's document calls out. Calcium hypochlorite and trichlor "
							"combined in a container or feeder can ignite, and residue left in a re-used container "
							"or on a shared scoop is enough to start either reaction."
						),
						"options": [
							{
								"text": "Liquid chlorine and muriatic acid mixing directly produce lethal chlorine gas",
								"is_correct": True,
							},
							{
								"text": "Calcium hypochlorite and trichlor combined in a container or feeder can ignite",
								"is_correct": True,
							},
							{
								"text": "Residue left on a shared scoop or in a re-used container is enough to react",
								"is_correct": True,
							},
							{
								"text": "Two chlorine-based products are safe together because they are the same chemistry",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Algae eradication and biological remediation",
			"chapter": 2,
			"estimated_minutes": 22,
			"summary": "Telling Sapphire's three kinds apart, brushing before chemistry, running the UV loop without blinding yourself, and recognising when the problem is not in the water at all.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Algae is a plant, and something is feeding it",
					"content": (
						"<p>Algae needs four things: light, warmth, water and nutrients — chiefly "
						"phosphate and nitrate. Three of those are what a fountain is.</p>"
						"<p>It is also arriving constantly and there is nothing to be done about "
						"that. Spores come in on wind and rain, on birds, on people, and on "
						"equipment carried between sites. Exposure is not what decides whether a "
						"feature blooms.</p>"
						"<p>What decides it is whether the sanitiser is winning. So <b>a bloom is a "
						"symptom, not the disease</b>. Something is short: free chlorine, or "
						"circulation reaching that part of the basin, or filtration, or the "
						"chlorine is present on the test but parked behind too much stabiliser. "
						"Treating the green without answering that question buys you the interval "
						"between visits.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Green, mustard and black are three different problems",
					"panels": [
						{
							"title": "Green — floating or clinging, fast, and the most honest about itself",
							"body": (
								"<p>Floating or clinging organic matter that turns the water cloudy green and "
								"makes stone surfaces slippery. It grows quickly, it suspends in the water "
								"column, and Sapphire's document is blunt about the prognosis: fast-growing but "
								"<b>easy to treat</b>. It responds to chemistry and filtration.</p>"
								"<p>Green is usually a straightforward failure of sanitiser or circulation, and "
								"it tells you so. Find out which, or you will be treating it again.</p>"
							),
						},
						{
							"title": "Mustard — a yellow-brown film in the shade, tolerant of your normal residual",
							"body": (
								"<p>A <b>yellow-brown powdery film</b>, usually on the <b>shaded walls</b> of a "
								"basin. It brushes away very easily and then comes back in exactly the same "
								"place.</p>"
								"<p>The reason it comes back is that it is <b>resistant to normal chlorine "
								"baselines</b>. A residual that holds green will not hold mustard, and Sapphire's "
								"document is explicit that it requires <b>high shock levels</b> — the breakpoint "
								"protocol, not a top-up.</p>"
								"<p>And here is the part that catches people. Mustard algae survives on "
								"<b>anything that touched the water</b> — nets, brushes, hoses, vacuum heads, "
								"wetsuits, swimwear, the test kit. Treat the feature perfectly, then put an "
								"untreated brush back in it, and you have re-inoculated it yourself. The "
								"treatment worked; the tool undid it. Disinfect the equipment as part of the "
								"job, not afterwards.</p>"
							),
						},
						{
							"title": "Black — shelled and rooted, and chemistry never reaches the part that matters",
							"body": (
								"<p>It appears as <b>dark dots or streaks</b>, and it behaves differently from "
								"both of the others in two ways that both defeat chemistry.</p>"
								"<p>It grows a <b>hard, protective outer shell</b> over itself, so sanitiser "
								"reaches the outside and nothing else. And it embeds <b>root-like structures into "
								"porous concrete or grout</b>, so the living part is inside the finish rather "
								"than on it. That combination is why it returns in the same spots after a "
								"treatment that looked successful: the living part was never touched.</p>"
								"<p>Sapphire's document says it plainly — exceptionally difficult to kill "
								"<b>without mechanical scraping</b>. It has to be physically broken open before "
								"chemistry can do anything, with a brush the finish can survive. Spots that keep "
								"returning in the same place are usually telling you the surface there is damaged "
								"or porous enough to hold roots, which makes it a repair question as much as a "
								"chemistry one.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Brush, treat, filter, recheck — and the order is the whole method",
					"content": (
						"<p><b>Brush first.</b> It is step one of Sapphire's breakpoint protocol for "
						"a reason: brushing cracks open the protective cellular barriers, lifts "
						"settled growth into suspension where the sanitiser and the filter can get "
						"at it, and on black algae it is the step without which nothing else "
						"matters. <b>Wire brush on concrete, nylon brush on tile and stone.</b> Get "
						"the walls, the floor, the corners, behind the weirs, and the steps and "
						"ledges everybody skips.</p>"
						"<p><b>Then treat</b> — the breakpoint dose from the shocking lesson, "
						"broadcast pre-dissolved and at night.</p>"
						"<p><b>Then filter, continuously, and clean the filter as it loads.</b> This "
						"is the step that gets abandoned. Killing algae does not remove it — it turns "
						"a living green problem into a suspended dead one that the filtration has to "
						"physically take out of the water. The water typically goes grey or milky "
						"first, which looks like the treatment failed and is actually the sign that "
						"it worked. Filter pressure will climb as it loads; clean or backwash it "
						"according to the equipment's instructions and keep going. The continuous "
						"24-hour circulation run in the protocol is doing the same work inside the "
						"pipework and the pump impellers.</p>"
						"<p><b>Then re-test and re-brush.</b> Whatever survived regrows from the "
						"spots the brush missed, and it regrows fastest where the water moves least. "
						"Repeat until a brushed spot stays clean between visits — that, and not the "
						"colour of the water on the day, is the finish line.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Algaecides in a fountain have to be non-foaming",
					"content": (
						"<p>Sapphire's document gives one clear rule here. When you use a "
						"copper-based or quaternary ammonium algaecide in an architectural "
						"fountain, <b>verify it is a non-foaming formulary type</b>.</p>"
						"<p>The mechanism is the display itself. Standard low-cost pool algaecides "
						"contain <b>surfactants</b>. A pool agitates them gently; a fountain drives "
						"them through high-pressure nozzles, which is an excellent way to make "
						"foam. The result is mounds of soap-like foam spilling over the weir "
						"borders and down the stonework — on a feature whose entire job is to look "
						"deliberate, in front of whoever is paying for it.</p>"
						"<p>It is not only cosmetic. Foam carries the product out of the basin, so "
						"the dose leaves with it, and a foaming feature is usually shut off while "
						"somebody works out what happened.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "UV is a second sanitation loop, not a replacement for the first",
					"content": (
						"<p>A UV system is a <b>secondary</b> sanitation circuit. Water passes "
						"through an engineered stainless steel chamber where a high-intensity "
						"ultraviolet lamp emits at <b>254 nm</b>. That wavelength penetrates the "
						"cells of bacteria, viruses and chlorine-resistant pathogens — "
						"<b>Cryptosporidium</b> is the one that matters, because chlorine at normal "
						"levels barely touches it — and destroys their DNA so they cannot reproduce "
						"or infect guests.</p>"
						"<p>Note what that does and does not give you. It kills what passes through "
						"the chamber, and it leaves <b>no residual behind it</b>. The basin is still "
						"protected by the free chlorine in the water, not by the lamp. A UV system "
						"is a reason for confidence about pathogens, never a reason to let the "
						"residual fall.</p>"
						"<p><b>The quartz sleeve is the part that fails quietly.</b> The lamp lives "
						"inside a protective glass quartz sleeve, and calcium scale attaches to that "
						"sleeve over time as a chalky white layer. It is <b>insulation</b>: it "
						"blocks the UV light from reaching the water while the lamp goes on looking "
						"exactly as lit as it did on day one. Sapphire's document makes descaling a "
						"monthly job — isolate the chamber, drain it, carefully extract the sleeve, "
						"and wipe it clean with a <b>scale-dissolving gel or denatured alcohol</b>. "
						"Extracting, descaling, inspecting and safely reinstalling a sleeve is one "
						"of the module's performance checks.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Never look directly at an active UV lamp",
					"content": (
						"<p><b>It can cause permanent blindness and severe skin burns within "
						"seconds.</b> There is no safe glance and there is no warning sensation "
						"while the damage is being done.</p>"
						"<p><b>Turn the power off at the breaker before opening the housing.</b> Not "
						"the controller, not the switch on the panel — the breaker. A UV housing is "
						"designed to be opened with the lamp dead.</p>"
						"<p><b>Handle new lamps with clean latex gloves.</b> Touching the bulb "
						"quartz with bare skin leaves finger oils on the glass; those oils cook on "
						"under the operating temperature and crack the bulb prematurely. The lamp "
						"that fails early is usually the one somebody installed barehanded.</p>"
						"<p><b>Lamps age out before they burn out.</b> Intensity falls off after "
						"roughly <b>9,000 to 12,000 operational hours</b>, so a lamp that still "
						"lights can be doing very little — which is the same failure mode as the "
						"scaled sleeve and is just as invisible. Track the hours; do not judge it "
						"by the glow.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Phosphate and nitrate are the food supply",
					"content": (
						"<p>If a feature keeps blooming against a sanitiser residual that ought to "
						"hold, look at what is feeding it. Phosphate and nitrate arrive from decaying "
						"leaves and organic debris, from lawn fertiliser and irrigation runoff "
						"draining into an outdoor basin, from some source water, from birds, and from "
						"certain treatment products themselves.</p>"
						"<p>Chlorinating hard against a feature that is being fed continuously is a "
						"losing arrangement — you are paying for chemicals to treat a symptom on "
						"every visit while the cause keeps arriving. Keeping leaves and debris out, "
						"finding out where the site's runoff goes, knowing what the make-up water "
						"carries, and considering nutrient removal where the treatment design allows "
						"it are what make the chemistry stick.</p>"
						"<p>That is a site and design conversation to raise, not a bottle to pour. "
						"Bring it back with the observation that prompted it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Biofilm lives inside the pipework, and chemistry alone never reaches it",
					"content": (
						"<p>Bacteria on a wet surface build themselves a community and then glue a "
						"protective layer over the top of it. That is biofilm, and it is the "
						"slipperiness on a basin wall, the slime behind a weir, the film inside a "
						"filter, and — the part you cannot see — the coating on the inside of the "
						"pipework and on the pump impellers the protocol's 24-hour run is there to "
						"scrub.</p>"
						"<p>Three consequences, and they explain a lot of otherwise confusing "
						"features:</p>"
						"<ul>"
						"<li><b>It eats sanitiser continuously.</b> The residual crashes between "
						"visits and the dose gets blamed.</li>"
						"<li><b>It protects what lives inside it</b> from the sanitiser in the "
						"water.</li>"
						"<li><b>It re-seeds the water</b> as fast as you treat it, from a place your "
						"chemistry is barely reaching.</li>"
						"</ul>"
						"<p>It lives where flow is poor: dead legs, capped branches, lines that only "
						"run on a schedule, and anything left standing wet between seasons. A feature "
						"you find yourself re-treating on every single visit is usually telling you "
						"the problem is not in the basin. Mechanical cleaning, a system-wide "
						"disinfection carried out to the treatment design's own procedure, and "
						"sometimes replacing a fouled component are the real answers — and "
						"eliminating dead legs is a design fix rather than a service one.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A feature that makes aerosol is a public health question",
					"content": (
						"<p>Warm stagnant water, biofilm and anything that throws water into the air "
						"as fine droplets — spray, misters, splash, a waterfall into a basin — is the "
						"combination associated with <b>Legionella</b>. People are infected by "
						"breathing it in, or by water going down the wrong way into the lungs — "
						"not through the skin — and decorative fountains have caused documented "
						"outbreaks.</p>"
						"<p>This is not a technician's judgement call and it is not something to "
						"treat your way out of on the day. ASHRAE Standard 188 sets out what a water "
						"management program for a building water system is and what belongs in one, "
						"and public health authorities publish guidance for ornamental water "
						"features specifically. Whether the feature in front of you needs such a "
						"program, and who writes it, is a question for the design and for the "
						"authority.</p>"
						"<p>What is yours to do: report heavy biofilm, a feature that has stood "
						"stagnant and warm, or a system that will not hold a residual, <b>as a "
						"finding</b>, and stop rather than improvise.</p>"
					),
				},
				ask_block(
					"Which algaecide, and whether the finish will survive the brush",
					"<p>Sapphire's document settles two things here: an algaecide used in an "
					"architectural fountain must be a <b>non-foaming</b> formulary type, and the "
					"brush is matched to the substrate — <b>wire on concrete, nylon on tile and "
					"stone</b>.</p>"
					"<p>Beyond that, algaecides are not interchangeable. Copper-based products "
					"stain and carry a real upper limit on how much copper a body of water may "
					"hold, quaternary ammonium products behave differently again, and whether "
					"either is compatible with the sanitiser already in that water is a label and "
					"treatment-design question.</p>"
					"<p>The brush has the same edge. A wire brush that takes black algae off "
					"concrete will destroy a vinyl liner, a painted finish, an acrylic panel or a "
					"coloured aggregate, and a nylon brush does effectively nothing to rooted "
					"growth in concrete. If you do not know what the finish is, find out before "
					"you touch it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does black algae come back in the same spots after a treatment that appeared to work?",
						"type": "Single Choice",
						"explanation": (
							"It grows a hard protective outer shell and embeds root-like structures into porous "
							"concrete or grout, so sanitiser only ever reaches the outside. Sapphire's document is "
							"explicit that it is exceptionally difficult to kill without mechanical scraping."
						),
						"options": [
							{
								"text": "It is shelled and rooted into the finish, so chemistry never reaches the living part without mechanical scraping",
								"is_correct": True,
							},
							{
								"text": "Its spores are heavier than water and settle back into the same places",
								"is_correct": False,
							},
							{
								"text": "It is a chlorine-resistant strain that needs a stronger dose of the same product",
								"is_correct": False,
							},
							{
								"text": "It is regrowing from the filter, which needs replacing",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of a UV disinfection system on a fountain?",
						"type": "Multiple Choice",
						"explanation": (
							"The 254 nm lamp destroys the DNA of bacteria, viruses and chlorine-resistant "
							"pathogens such as Cryptosporidium. Calcium scale on the quartz sleeve insulates it "
							"and blocks the light while the lamp still looks lit, and finger oils left on a new "
							"bulb cook on and crack it. What it does not do is leave a residual — it is a "
							"secondary loop, and the basin is still protected by free chlorine."
						),
						"options": [
							{
								"text": "It destroys the DNA of chlorine-resistant pathogens such as Cryptosporidium",
								"is_correct": True,
							},
							{
								"text": "Scale on the quartz sleeve blocks the light while the lamp still appears to be working",
								"is_correct": True,
							},
							{
								"text": "A new lamp handled with bare skin can crack prematurely from cooked-on finger oils",
								"is_correct": True,
							},
							{
								"text": "It leaves a sanitiser residual in the basin, so the free chlorine level can be relaxed",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why does Sapphire's document require a non-foaming algaecide in an architectural fountain?",
						"type": "Single Choice",
						"explanation": (
							"Standard low-cost pool algaecides contain surfactants. Agitated by high-pressure "
							"fountain nozzles they produce mounds of soap-like foam that spill over the weir "
							"borders — and the foam carries the product out of the basin with it."
						),
						"options": [
							{
								"text": "The surfactants in standard pool algaecides foam up under high-pressure nozzles and spill over the weirs",
								"is_correct": True,
							},
							{
								"text": "Foaming formulas are the only ones that stain copper-based fittings",
								"is_correct": False,
							},
							{
								"text": "Foam blocks the ultraviolet lamp from reaching the water",
								"is_correct": False,
							},
							{
								"text": "Non-foaming products are the only ones that work on mustard algae",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Brushing before treating a bloom matters mainly because it removes the visible growth so the feature looks better sooner.",
						"type": "True-False",
						"explanation": (
							"Brushing is not cosmetic. It cracks open the protective cellular barriers and lifts "
							"settled growth into suspension so the sanitiser and the filter can reach it — which "
							"is why it is step one of the breakpoint protocol, with a wire brush on concrete and "
							"a nylon brush on tile and stone."
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
			"lesson_title": "Scale and mineral deposit mitigation",
			"chapter": 2,
			"estimated_minutes": 20,
			"summary": "Why no single reading tells you whether water will scale or corrode, the five variables Sapphire's document balances, and why suppression is not removal.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Scale and corrosion are one problem with two signs",
					"content": (
						"<p>Water is either holding calcium carbonate in solution, dropping it out, "
						"or dissolving more of it. Those are not three unrelated conditions; they are "
						"three positions on one scale, and every body of water is somewhere on "
						"it.</p>"
						"<p>Drop it out and you get <b>scale-forming water</b>: white calcium "
						"deposits across stone facings, dark tile work, nozzles, heat exchangers and "
						"the quartz glass sleeves from the UV lesson. Dissolve more and you have "
						"<b>corrosive, hungry water</b>: it actively dissolves calcium out of "
						"masonry grout, stone mortars, plaster linings and copper fittings.</p>"
						"<p>The trap is that <b>no single reading tells you which way you are "
						"going</b>. Not hardness on its own, not pH on its own, not alkalinity. Two "
						"features can show an identical pH and be on opposite sides of the line. This "
						"is the reading that has to be calculated rather than measured.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The index, the band, and where this course changed its mind",
					"content": (
						"<p>The <b>Langelier Saturation Index</b> is the calculation that answers "
						"it. Water wants to be perfectly balanced at an index value of <b>0.0</b>, "
						"and Sapphire's document gives the working band around it:</p>"
						"<ul>"
						"<li><b>Below -0.3 — corrosive, hungry water.</b> It lacks minerals and "
						"will take them out of the masonry grout, the stone mortars, the plaster "
						"lining and the copper fittings.</li>"
						"<li><b>Above +0.3 — scale-forming water.</b> It is oversaturated, rejects "
						"the minerals it is carrying, and deposits them on the stone, the tile, the "
						"nozzles and the quartz sleeves.</li>"
						"<li><b>Between the two — balanced</b>, and that is the target state the "
						"module's own performance check asks you to prescribe your way to.</li>"
						"</ul>"
						"<p><b>Two things here are corrections to what this course used to "
						"say</b>, and they are worth flagging rather than quietly swapping. The "
						"previous draft printed no band at all, on the grounds that a target belongs "
						"to a feature's design — Sapphire's document prints one, so the band above "
						"is the band. And the previous draft wrote the index with a "
						"total-dissolved-solids term in it. <b>Sapphire's method does not use "
						"one.</b> It works from five live site readings, with cyanuric acid "
						"subtracted from total alkalinity instead. Where the document and general "
						"practice disagree about whose arithmetic to use, use the document's.</p>"
						"<p>You are not deriving anything by hand. Use a <b>slider rule app or a "
						"matrix chart</b> to turn each measured value into its factor, and add them "
						"up. The skill is knowing which five to measure and what each one does to "
						"the answer.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "The five variables, and which of them you can actually move",
					"panels": [
						{
							"title": "pH — the most dominant factor, and the one that drifts",
							"body": (
								"<p>Sapphire's document calls it the dominant term outright: <b>high pH causes "
								"rapid scaling, low pH causes rapid corrosion</b>. It is also the fastest lever "
								"you have, which is why people reach for it first.</p>"
								"<p>It is also the term that moves on its own — aeration from the display, rain, "
								"bather load and every dose you make all push it. A correction made only with "
								"acid or base is a correction you will be making again.</p>"
							),
						},
						{
							"title": "Water temperature — a real term you usually cannot move",
							"body": (
								"<p>As water temperature rises, the water becomes <b>more scale-forming</b>. "
								"That is the whole reason equipment scales before the tile does, and it is the "
								"subject of the warning below.</p>"
								"<p>On most features you cannot set it. What you can do is remember it is in the "
								"equation, so a calculation done on a cold spring commission does not describe the "
								"same feature in August.</p>"
							),
						},
						{
							"title": "Calcium hardness — dissolved calcium, and it stays where you put it",
							"body": (
								"<p>The measure of dissolved calcium minerals. Low hardness is what makes water "
								"hungry; high hardness is what feeds a deposit.</p>"
								"<p>Unlike pH it does not wander, so moving hardness moves the index and the "
								"change holds. It is the slower lever and often the more honest one. It also only "
								"goes up on its own — evaporation concentrates it, and bringing it down means "
								"dilution.</p>"
							),
						},
						{
							"title": "Total alkalinity — the buffering capacity that stops pH spikes",
							"body": (
								"<p>It appears in the index in its own right, and it is also what decides whether "
								"your pH correction will hold at all. That is why it is corrected before pH, as "
								"the first lesson said.</p>"
								"<p>Note that muriatic acid depresses alkalinity <i>and</i> pH together, so one "
								"dose moves two terms of the index at once. Re-read both.</p>"
							),
						},
						{
							"title": "Cyanuric acid — the correction people forget",
							"body": (
								"<p>The stabiliser used to shield chlorine from sunlight. For the index it has "
								"one job: it <b>must be mathematically subtracted from the total alkalinity "
								"reading</b> before the alkalinity factor is looked up.</p>"
								"<p>Skip the subtraction and you overstate alkalinity, which overstates the "
								"index, which makes water that is quietly corrosive calculate out as balanced. On "
								"an outdoor feature running high stabiliser the error is not small — and it fails "
								"in the direction where nothing looks wrong until the grout goes.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The same water is scaling in one place and corrosive in another",
					"content": (
						"<p>Temperature is a term in the index, and a fountain is not all one "
						"temperature.</p>"
						"<p>A heat exchanger, a submerged luminaire, a pump seal and a UV quartz "
						"sleeve all run hotter than the basin around them. Water that calculates out "
						"balanced at basin temperature can be firmly on the scaling side at those "
						"hot surfaces — which is exactly why scale shows up inside the equipment "
						"first while the tile still looks perfect, why a UV sleeve chalks over "
						"between services, and why a heater fails on a feature whose test results "
						"have been filed as fine all season.</p>"
						"<p>It runs the other way too. An unheated outdoor basin in cold weather, or "
						"a feature running largely on cold make-up water, can be corrosive at the "
						"surface everybody is looking at.</p>"
						"<p>Flow matters alongside it: deposition is fastest where the water is hot "
						"<i>and</i> slow. <b>A single calculation from a single sample in the middle "
						"of the basin is not a statement about the whole system.</b></p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Bringing a bad index back into the band",
					"content": (
						"<p>Sapphire's document works one case through, and it is the common one. A "
						"feature calculates out with a scaling profile at <b>+0.45</b>. The "
						"technician lowers the index by adding <b>small, calculated dosages of "
						"muriatic acid</b>, which depress the pH and the alkalinity together and "
						"bring the value down into the balanced zone. Then <b>note the update on "
						"the daily job log</b> — the next person's starting point is your "
						"record.</p>"
						"<p>Three things about that are worth saying out loud:</p>"
						"<ul>"
						"<li><b>Small and calculated.</b> pH is logarithmic and acid moves two "
						"terms at once, so the dose that looks decisive is usually the dose that "
						"overshoots into the corrosive side. You are aiming for a band, not a "
						"point.</li>"
						"<li><b>Re-test after it has circulated</b>, then recalculate. An index is "
						"only as current as the five readings under it.</li>"
						"<li><b>Acid handling rules still apply.</b> Acid into water, never the "
						"reverse, and never anywhere near a chlorine product or its feed "
						"line.</li>"
						"</ul>"
						"<p>The other direction — an index that is too low — is not the mirror "
						"image. You raise it by adding what the water is short of, which is a "
						"hardness or alkalinity decision rather than a squirt of acid, and it is "
						"the direction that is quietly eating the finish while you decide.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why this is expensive rather than merely untidy",
					"content": (
						"<p><b>On the scaling side.</b> Scale insulates, so a scaled heat exchanger "
						"makes the heater work harder, run hotter and eventually fail. It narrows "
						"pipework and blocks nozzles, so a display that was commissioned symmetrical "
						"goes lopsided and nobody can find a mechanical reason. It chalks over a UV "
						"quartz sleeve and takes the disinfection with it. It clouds glass and "
						"builds a bonded band along the tile line, it gets harder to remove the "
						"longer it sits, and it roughens surfaces — which gives algae exactly the "
						"porous foothold the previous lesson described.</p>"
						"<p><b>On the corrosive side.</b> There is no cleaning this one off. Plaster "
						"that has been etched away is gone, grout that has been dissolved out has to "
						"be replaced, and the metal that corroded alongside it is generally a part. "
						"The bill is a refinish rather than a service call.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Suppression, removal, and the difference between them",
					"panels": [
						{
							"title": "Sequestrants — suppression, and they are consumed",
							"body": (
								"<p>A sequestrant or chelant binds calcium and metal ions so the water can carry "
								"more of them without depositing. It is a genuinely useful tool, especially where "
								"the source water is hard or carries iron or copper.</p>"
								"<p>Two things to be honest about. <b>It suppresses deposition; it does not "
								"remove scale that is already there</b> — nothing in a bottle un-deposits a "
								"scaled heat exchanger. And <b>it is consumed</b>: chlorine and UV break it down, "
								"so it is a repeating program rather than a one-time dose. When it runs out, "
								"everything it was holding in solution can come out at once, which on a feature "
								"carrying iron or copper means staining the whole basin in a single "
								"episode.</p>"
							),
						},
						{
							"title": "Mechanical and spot cleaning — surface-dependent, every time",
							"body": (
								"<p>Brushing, a scale stone on a tile line, a scale-dissolving gel on a quartz "
								"sleeve, or a proprietary cleaner applied to a deposit. This is the everyday "
								"answer for a waterline band and light deposition.</p>"
								"<p>What is safe depends entirely on the finish. A stone that cleans glazed tile "
								"will scratch glass, acrylic, polished stone and a painted surface permanently. A "
								"cleaner formulated for tile can dull or etch a natural stone coping. Test "
								"somewhere invisible before you work somewhere visible, and check what the finish "
								"manufacturer allows.</p>"
							),
						},
						{
							"title": "Acid washing — a last resort that spends the surface",
							"body": (
								"<p>An acid wash removes scale by removing a thin layer of the surface it is "
								"stuck to. That is the mechanism, not a side effect. A plaster finish can only "
								"take a limited number of them in its life before there is no finish left, and "
								"that number belongs to the finish, not to the schedule.</p>"
								"<p>It is also a serious hazard job in its own right: acid handling, fumes in "
								"what may be a poorly ventilated basin, respiratory and burn protection, and "
								"effluent that has to be neutralised and disposed of as a regulated discharge "
								"rather than pushed into a storm drain.</p>"
								"<p>It is planned work for people equipped to do it, and the decision to do it "
								"at all sits above a service visit.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "A fountain is an evaporator, and that is what breaks the balance",
					"content": (
						"<p>Water leaves a feature as vapour. <b>Everything dissolved in it stays "
						"behind.</b> Make-up water arrives to hold the level, carrying its own "
						"minerals with it, and leaves those behind too when it evaporates in turn.</p>"
						"<p>So hardness, alkalinity, stabiliser and total dissolved solids all "
						"concentrate over a season while the water level looks constant and nobody "
						"changed anything. That is the mechanism behind a feature that slowly "
						"becomes scale-prone with no event to point at — and it is why knowing what "
						"the make-up water carries matters: you are concentrating it, continuously, "
						"all summer. It is also how a stabiliser level climbs into the range where "
						"the cyanuric acid correction stops being a rounding error.</p>"
						"<p>It also sets the limit of what chemistry can do. You can add things to "
						"water. You cannot subtract most of them. Past a certain point the only "
						"correction available is <b>dilution</b> — a partial drain and refill — and "
						"that is a water-use, discharge and design decision rather than something to "
						"start with a hose.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Sapphire's Module 3 performance checks, demonstrated to a Lead Installer",
					"items": [
						"Execute precise pH and DPD-1 photometer tests, identifying and correcting a chlorine bleach-out scenario",
						"Safely clean and calibrate digital pH and ORP sensory probes using fresh 7.0 and 4.0 reference buffer solutions",
						"Rebuild a peristaltic chemical pump drive block, replacing a worn internal squeeze tube",
						"Extract, descale, inspect and safely reinstall a glass quartz sleeve inside a commercial UV reactor chamber",
						"Perform a full LSI calculation using live water test parameters and correctly prescribe the chemical adjustments that bring it to a stable balance between -0.3 and +0.3",
					],
				},
				ask_block(
					"The band is Sapphire's; the rest of the decisions are still the site's",
					"<p>The index band of -0.3 to +0.3, the five variables and the muriatic acid "
					"correction come from Sapphire's document and are used as written.</p>"
					"<p>What still belongs to the feature is everything around them: the target "
					"for each individual term, which sequestrant on what program, whether a "
					"partial drain is acceptable and where that water may legally be discharged, "
					"and whether a given finish may be acid washed at all. Those sit with the "
					"water treatment design, the finish and equipment manufacturers, and the "
					"authority over the site.</p>"
					"<p>This lesson teaches you to read the balance and to know which lever moves "
					"it. A partial drain in particular is a decision to bring back rather than to "
					"make at the feature.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Which of these tells you whether a feature's water will scale or corrode?",
						"type": "Single Choice",
						"explanation": (
							"No single reading answers it. The Langelier Saturation Index combines five live site "
							"readings — pH, water temperature, calcium hardness, total alkalinity and cyanuric "
							"acid — and two features with identical pH can sit on opposite sides of balance."
						),
						"options": [
							{
								"text": "The saturation index, calculated from five site readings together",
								"is_correct": True,
							},
							{"text": "The calcium hardness reading on its own", "is_correct": False},
							{"text": "The pH reading on its own", "is_correct": False},
							{"text": "The total alkalinity reading on its own", "is_correct": False},
						],
					},
					{
						"question": "A feature's saturation index calculates out at -0.45. What is the water doing?",
						"type": "Single Choice",
						"explanation": (
							"Below -0.3 the water is corrosive — hungry. It lacks minerals and dissolves calcium "
							"out of masonry grout, stone mortars, plaster linings and copper fittings. Above +0.3 "
							"is the scale-forming side."
						),
						"options": [
							{
								"text": "It is corrosive, hungry water — dissolving calcium out of grout, mortar, plaster and copper",
								"is_correct": True,
							},
							{
								"text": "It is depositing scale on the heater and the tile line",
								"is_correct": False,
							},
							{
								"text": "It is balanced, since a negative number means no deposition",
								"is_correct": False,
							},
							{
								"text": "It is too soft to sanitise and needs more chlorine",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are among the five variables Sapphire's document uses to calculate the saturation index?",
						"type": "Multiple Choice",
						"explanation": (
							"The five are pH, water temperature, calcium hardness, total alkalinity and cyanuric "
							"acid — the last of which is subtracted from the alkalinity reading rather than added "
							"as a factor of its own. The free chlorine residual is not one of them; it tells you "
							"about sanitation, not about saturation."
						),
						"options": [
							{"text": "Water temperature", "is_correct": True},
							{"text": "Calcium hardness", "is_correct": True},
							{"text": "Cyanuric acid, subtracted from total alkalinity", "is_correct": True},
							{"text": "The free chlorine residual in parts per million", "is_correct": False},
						],
					},
					{
						"question": "Water that calculates out balanced at basin temperature cannot be scaling inside the heater.",
						"type": "True-False",
						"explanation": (
							"Water temperature is one of the five variables, and warmer water is more "
							"scale-forming. A heat exchanger runs hotter than the basin, so the same water can be "
							"firmly on the scaling side at that surface — which is why equipment and UV sleeves "
							"scale while the tile still looks fine."
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
