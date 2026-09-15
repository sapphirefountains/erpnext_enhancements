# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 3 — Water Chemistry."""

from erpnext_enhancements.training.technician_program._common import ask_block, notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 3 — Water Chemistry",
		"summary": (
			"Read a feature's water and know what each number actually drives, take a sample and "
			"run a test you can defend, shock and clear algae without hurting anybody or the "
			"finish, and tell scaling water from aggressive water before either one eats the "
			"surface."
		),
		"category": "Water Chemistry",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"chapters": [
		{
			"title": "What the readings actually mean",
			"description": "pH, alkalinity, hardness, stabiliser, and the three chlorines.",
		},
		{
			"title": "Getting a number you can trust",
			"description": "Sampling, the three test methods, and the honest limits of each.",
		},
		{
			"title": "Correcting the water",
			"description": "Shocking, algae and biofilm, and the scaling-versus-aggressive balance.",
		},
	],
	"lessons": [
		{
			"lesson_title": "Basic water chemistry",
			"chapter": 0,
			"estimated_minutes": 18,
			"summary": "What each reading controls, why they are not independent, and why the targets are never this course's to give.",
			"blocks": [
				notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "pH is a ratio, and the scale is logarithmic",
					"content": (
						"<p>pH runs from 0 to 14, and each whole number is a <b>tenfold</b> change "
						"in acidity. A reading one unit off target is not slightly off. It is ten "
						"times off. Two units is a hundred times.</p>"
						"<p>That is why pH is the reading that moves everything else, and why a "
						"small correction can overshoot badly. Three things depend on it:</p>"
						"<ul>"
						"<li><b>Sanitiser effectiveness.</b> Chlorine in water exists as two forms "
						"in a balance: hypochlorous acid, which is the strong killer, and the "
						"hypochlorite ion, which is far weaker. <b>The balance between them is set "
						"by pH</b>, and it shifts toward the weak form as pH rises. So the same "
						"free chlorine reading does substantially less work in high-pH water. The "
						"test reads concentration; it does not read strength.</li>"
						"<li><b>Comfort and irritation.</b> Water well away from the range the "
						"body is comfortable in stings eyes and dries skin. On a feature people "
						"touch, that is one source of a complaint — though combined chlorine, "
						"later in this lesson, is more often the real one.</li>"
						"<li><b>What the water does to the things it touches.</b> Low pH is "
						"aggressive — it attacks metal, grout and cementitious finishes. High pH "
						"drives deposition. That is the subject of the last lesson in this "
						"module.</li>"
						"</ul>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Alkalinity is the buffer, and that is why you fix it first",
					"content": (
						"<p>Total alkalinity is not a measure of how basic the water is. It is a "
						"measure of the water's <b>capacity to absorb acid or base without the pH "
						"moving</b> — mostly carbonate and bicarbonate sitting in reserve.</p>"
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
						"acid lowers both at once. Expect to come back and re-read.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Soft water goes and gets its own calcium",
					"content": (
						"<p>Water is a solvent, and water that is short of dissolved calcium "
						"carbonate does not stay short of it. It dissolves it out of whatever it "
						"is touching — <b>plaster, tile grout, mortar, concrete, and natural "
						"stone</b>.</p>"
						"<p>What that looks like on site: plaster that goes rough and then pitted, "
						"grout washing out of joints until the tile is loose, a concrete basin "
						"that gets progressively more porous, and metal being attacked at the same "
						"time. Once a finish has gone, it does not come back; it gets "
						"replaced.</p>"
						"<p><b>Note the failure direction.</b> Soft water tests as <i>almost "
						"nothing in it</i>, which reads as clean. There is no cloudiness, no "
						"smell, no deposit, and nothing on a strip that looks alarming — while the "
						"feature is quietly being consumed. Hardness that is too <i>high</i> is "
						"the opposite problem and announces itself as scale. The aggressive side "
						"is the one that is expensive precisely because it is invisible.</p>"
						"<p>Filling a soft-water feature with softened water makes this worse, not "
						"better: a domestic softener removes exactly the calcium the surface needs "
						"the water to already have.</p>"
					),
				},
				ask_block(
					"A decorative feature is not a pool, and the targets are not ours to print",
					"<p>Every target range in this subject — pH, alkalinity, calcium hardness, "
					"stabiliser, sanitiser residual — belongs to <b>a particular feature</b>. It "
					"comes from that feature's water treatment design, the finish and equipment "
					"manufacturers, and the health authority with jurisdiction over it.</p>"
					"<p>And the jurisdiction question is a real one. An interactive feature people "
					"stand in — a splash pad, a wading basin, anything designed for contact — is "
					"commonly regulated as an aquatic venue under the local adoption of the "
					"International Swimming Pool and Spa Code or a state pool code, with mandated "
					"chemistry, mandated testing and an inspected log. A display fountain nobody "
					"touches may be under none of that, and may be run on a completely different "
					"treatment program.</p>"
					"<p>Find out which one you are standing in front of <b>before</b> you dose "
					"anything. A number remembered from another site is the whole reason this "
					"course does not print any.</p>",
				),
				{
					"block_type": "Rich Text",
					"heading": "Cyanuric acid is chlorine's sunscreen, and it has a catch",
					"content": (
						"<p>Ultraviolet light destroys free chlorine. Outdoors in sun, an "
						"unstabilised residual disappears fast — you dose in the morning and by "
						"afternoon there is nothing left. Cyanuric acid binds to free chlorine and "
						"shields it from UV, which is why outdoor features use it.</p>"
						"<p>The catch is the mechanism itself. <b>The bound chlorine is in "
						"reserve, not at work.</b> As the stabiliser level climbs, a larger share "
						"of your free chlorine is parked rather than sanitising, so the same test "
						"reading does less. Push it far enough and you get water that reads a "
						"perfectly respectable free chlorine number and will not hold against "
						"anything — the over-stabilisation trap. The test is not lying; it is "
						"answering a different question than the one you are asking.</p>"
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
						"<li><b>Free chlorine</b> is what is available to sanitise. This is the "
						"working number.</li>"
						"<li><b>Combined chlorine</b> is chlorine that has already reacted with "
						"ammonia and nitrogen compounds — sweat, urine, skin, leaves, birds, dust "
						"— and formed chloramines. It is spent. It is a weak sanitiser, it is a "
						"strong irritant, and it is the byproduct, not the product.</li>"
						"<li><b>Total chlorine</b> is the two added together, which is why "
						"<b>combined equals total minus free</b>. That subtraction is the number "
						"that tells you a feature needs shocking, and it is the whole reason a "
						"test that reports only total chlorine cannot tell you very much.</li>"
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
							"back": "How acidic or basic the water is, on a logarithmic 0-14 scale. One whole unit is a tenfold change. It sets how much of your chlorine is in the strong form.",
						},
						{
							"front": "Total alkalinity",
							"back": "The water's buffering reserve — its ability to take acid or base without the pH moving. Too little and pH bounces; too much and pH locks. Correct it before pH.",
						},
						{
							"front": "Calcium hardness",
							"back": "Dissolved calcium. Too little and the water dissolves calcium out of plaster, grout and concrete. Too much and it deposits as scale.",
						},
						{
							"front": "Cyanuric acid (stabiliser)",
							"back": "Shields free chlorine from UV by binding it. The bound share is held in reserve rather than working, and it only leaves with the water.",
						},
						{
							"front": "Free chlorine",
							"back": "Chlorine still available to sanitise. The working number.",
						},
						{
							"front": "Combined chlorine (chloramines)",
							"back": "Chlorine already reacted with nitrogen compounds. Spent, weakly sanitising, strongly irritating. Total minus free.",
						},
						{
							"front": "ORP (oxidation-reduction potential)",
							"back": "A millivolt reading of how oxidising the water actually is, rather than how much sanitiser is in it. It is what most automatic controllers steer on, and it moves with pH — so an ORP controller can chase a pH problem.",
						},
						{
							"front": "TDS (total dissolved solids)",
							"back": "Everything dissolved in the water added together. It climbs as water evaporates and the make-up keeps arriving, and it is a term in the saturation index.",
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
							"The pH scale is logarithmic, so one whole unit is a tenfold change in acidity. A "
							"reading that looks like a small miss on the strip is not a small miss in the water."
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
						"question": "Why is total alkalinity corrected before pH rather than after?",
						"type": "Single Choice",
						"explanation": (
							"Alkalinity is the buffer that holds pH still. With too little, every pH correction "
							"bounces straight back out; with too much, pH locks and resists you. Fixing pH first "
							"means correcting a number that nothing is holding."
						),
						"options": [
							{
								"text": "Alkalinity is the buffer that decides whether a pH correction will hold at all",
								"is_correct": True,
							},
							{
								"text": "Alkalinity takes much longer to dissolve, so it needs the head start",
								"is_correct": False,
							},
							{
								"text": "pH cannot be measured accurately until alkalinity is in range",
								"is_correct": False,
							},
							{
								"text": "It is only a convention; the order makes no practical difference",
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
							"It shields free chlorine from UV by binding it, which also holds that share in "
							"reserve rather than at work; stabilised products add more every time they are used; "
							"and it leaves essentially only with the water. It does not make each unit of "
							"chlorine stronger — the opposite."
						),
						"options": [
							{
								"text": "It protects free chlorine from being destroyed by sunlight",
								"is_correct": True,
							},
							{
								"text": "Stabilised products such as dichlor and trichlor add more of it with every dose",
								"is_correct": True,
							},
							{
								"text": "It is not consumed or burned off, so it leaves mainly through dilution",
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
			"estimated_minutes": 15,
			"summary": "Taking a sample that represents the feature, and knowing what each test method can and cannot honestly tell you.",
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
							"title": "Drop tests — the workhorse, if you are honest with yourself",
							"body": (
								"<p>Two different things live under this heading. A <b>titration</b>, like a "
								"hardness or alkalinity test, counts drops until the colour changes; the count is "
								"the measurement and it is genuinely quantitative. A <b>colour comparator</b>, "
								"like phenol red for pH, asks your eye to match a block again — better resolution "
								"than a strip, same subjectivity.</p>"
								"<p>The important capability here is <b>DPD</b>, which can separate free chlorine "
								"from total chlorine, so you can actually work out combined. A test that reports "
								"only total chlorine cannot tell you whether the feature needs shocking.</p>"
								"<p>Held wrong, counted optimistically, or read in bad light, a drop test is only "
								"as good as the hand holding it.</p>"
							),
						},
						{
							"title": "Photometers — precision is not accuracy",
							"body": (
								"<p>A photometer or colorimeter reads the developed colour by instrument and "
								"gives you a number with decimal places. That removes your eye from the loop, "
								"which is a real improvement, and it makes results repeatable between "
								"technicians.</p>"
								"<p>It removes nothing else. The reagent can be expired, the sample can be "
								"unrepresentative, the vial can be scratched or fingerprinted or filled to the "
								"wrong line, and the instrument itself drifts and needs calibrating against a "
								"standard. All of those produce a confident number to two decimal places that is "
								"simply wrong — and it is far harder to disbelieve a display than a smudgy "
								"colour.</p>"
								"<p>Precision is how repeatable the number is. Accuracy is whether it is true. An "
								"instrument gives you the first one for free and the second one never.</p>"
							),
						},
					],
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Two ways a test reads low on water that is not low",
					"content": (
						"<p><b>Expired or cooked reagents.</b> Reagents carry an expiry date, and "
						"that date assumes storage in the dark at sane temperatures — not a season "
						"on a dashboard. DPD degrades, phenol red drifts. The characteristic "
						"failure is <b>reading low or not developing at all</b>, so the water "
						"looks like it needs more of everything.</p>"
						"<p><b>DPD bleaching at high chlorine.</b> This one catches people. At "
						"very high free chlorine, the DPD indicator develops its colour and is then "
						"immediately bleached clear by the chlorine itself. A badly over-shocked "
						"feature therefore reads as <b>almost no chlorine</b>, and the instinct is "
						"to add more. If the sample flashes pink and then goes clear, or if you "
						"get a near-zero reading on water you have just shocked, <b>dilute the "
						"sample with known clean water and re-test</b>, then multiply back.</p>"
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
						"a bad strip, a reagent past its date, a sample taken in the wrong place. "
						"Confirming costs a couple of minutes. Dosing a feature on a false reading "
						"costs considerably more than that.</p>"
					),
				},
				ask_block(
					"Which parameters, how often, and against what",
					"<p>What gets tested, at what frequency, with which method, and the target "
					"range for each are set by the feature's water treatment design and by the "
					"health authority over it — and on a regulated interactive feature the "
					"frequency and the log are often mandated and inspected.</p>"
					"<p>Sapphire's own service expectations for testing and logging are a company "
					"decision that this course is not in a position to make for you. If you do "
					"not know what the schedule is for the feature in front of you, ask your "
					"supervisor rather than inventing a routine.</p>",
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
							"with the circulation running is the only sample that represents the feature."
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
						"question": "Why is the test vial rinsed with the sample water before the sample is kept?",
						"type": "Single Choice",
						"explanation": (
							"Test volumes are tiny, so a few drops of the previous sample or a trace of the last "
							"reagent is a meaningful fraction of what is being measured."
						),
						"options": [
							{
								"text": "Carryover from the last test is a large fraction of such a small volume",
								"is_correct": True,
							},
							{
								"text": "It brings the vial to the water's temperature before the reagent goes in",
								"is_correct": False,
							},
							{
								"text": "It removes air bubbles that would refract the colour",
								"is_correct": False,
							},
							{
								"text": "It is only needed when the vial has been stored wet",
								"is_correct": False,
							},
						],
					},
					{
						"question": "You shock a feature heavily, test it shortly afterwards, and the DPD free chlorine test reads almost zero. What has most likely happened?",
						"type": "Single Choice",
						"explanation": (
							"At very high chlorine the DPD indicator is bleached clear as soon as it develops, so "
							"an enormously over-chlorinated sample reads near zero. Dilute the sample with known "
							"clean water, re-test, and multiply back."
						),
						"options": [
							{
								"text": "The chlorine bleached the indicator — dilute the sample and re-test",
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
						"question": "A photometer reports free chlorine to two decimal places. Which of these does that number NOT protect you from?",
						"type": "Multiple Choice",
						"explanation": (
							"An instrument removes your eye from the loop and nothing else. An expired reagent, an "
							"unrepresentative sample and a scratched or badly filled vial all still produce a "
							"precise, confident, wrong number."
						),
						"options": [
							{"text": "An expired or heat-damaged reagent", "is_correct": True},
							{"text": "A sample taken at a return inlet", "is_correct": True},
							{"text": "A scratched vial filled past the line", "is_correct": True},
							{
								"text": "Two technicians reading the same colour differently",
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
			"estimated_minutes": 16,
			"summary": "What shocking is actually doing, why a half dose is worse than none, and the handling rules that exist because people have been hurt.",
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
						"destroying them instead. That point is the breakpoint, and it is only "
						"reached by getting past it.</p>"
						"<p>The consequence is the practical lesson: <b>an under-dose is worse than "
						"doing nothing</b>. Stop short of breakpoint and you have manufactured more "
						"combined chlorine than you started with — the feature smells worse, irritates "
						"more, and the technician concludes the shock did not work and that the answer "
						"is a bit less next time. It is the same trap in both directions.</p>"
						"<p>Where the breakpoint sits depends on how much combined chlorine is in the "
						"water, which is why the combined reading from the last lesson is what drives "
						"the decision. The dose that reaches it is a label and design calculation, not "
						"a habit.</p>"
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
						"replace a sanitiser residual and it does not kill algae. And <b>it interferes "
						"with DPD combined-chlorine testing</b>: for a period after dosing, the "
						"monopersulfate itself registers as combined chlorine, so the test tells you "
						"the water is full of chloramines when it is not. Technicians have chased that "
						"false reading with more shock. Know what you dosed, and know what your test "
						"will do about it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Acid into water. Never water into acid. Never two products together.",
					"content": (
						"<p><b>Acid into water, always, slowly.</b> Acid meeting water releases heat. "
						"Pour a small amount of water into a volume of concentrated acid and the water "
						"flashes to steam at the contact point and throws boiling acid out of the "
						"container, into your face. Adding acid slowly into a large volume of water "
						"spreads that heat through the water instead. This is not a preference and "
						"there is no situation on a fountain where the other order is correct.</p>"
						"<p><b>One product, one clean dry scoop, one container.</b> Pool and fountain "
						"chemicals are oxidisers, acids and organics, and combinations of them react "
						"violently. Calcium hypochlorite and trichlor mixed in a bucket, a feeder or "
						"even on a damp scoop can ignite. Hypochlorite plus acid releases <b>chlorine "
						"gas</b>. Residue left in a container from the last product is enough. Sheds "
						"have burned down from this and people have been gassed in plant rooms by "
						"it.</p>"
						"<p>Never re-use a chemical container for another chemical. Never stack an "
						"oxidiser and an acid on the same shelf. Read the safety data sheet for what "
						"is actually in your hand, and wear what it tells you to wear.</p>"
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
						"basin floor is somebody's shortcut from two seasons ago.</p>"
						"<p>Where the label calls for pre-dissolving: a clean bucket, filled with "
						"water first, then <b>product added slowly into the water</b> — the same "
						"direction as acid, and for the same reason. Stir with something dedicated to "
						"the job. Then pour it in slowly, around the perimeter, with the circulation "
						"running so it is carried and diluted rather than dropped in one place.</p>"
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
						"The dose came from the label and the feature's treatment design, not from memory",
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
						"heavy shock, a DPD test can read near zero because the chlorine is destroying "
						"the indicator. A near-zero result on freshly shocked water is a reason to "
						"dilute and re-test, never a reason to add more.</p>"
						"<p>Check pH afterwards as well, because almost everything you might have "
						"added moved it. Calcium hypochlorite and liquid sodium hypochlorite are "
						"alkaline and push pH up; dichlor and trichlor are acidic and pull it down. A "
						"feature that was balanced before the shock is not balanced after it.</p>"
					),
				},
				ask_block(
					"The product, the dose, and when the water is back in service",
					"<p>Which product a feature may be treated with, how much, whether it is "
					"pre-dissolved or fed, whether the finish, the metals and the equipment in that "
					"system can tolerate it at all, and the reading at which the water is safe for "
					"people again — all of that comes from the label, the safety data sheet, the "
					"water treatment design and the health authority.</p>"
					"<p>On a regulated interactive feature, the re-entry criterion is very often "
					"written down by that authority and is not negotiable. This course does not "
					"set a house dose or a house re-entry number, and a figure remembered from "
					"another feature is not one. Ask.</p>",
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
							"irritation get worse."
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
						"question": "Why is acid always added to water rather than water to acid?",
						"type": "Single Choice",
						"explanation": (
							"The reaction releases heat. Water poured into concentrated acid flashes to steam at "
							"the contact point and throws boiling acid out of the container. Acid added slowly to "
							"a large volume of water spreads that heat through the water."
						),
						"options": [
							{
								"text": "The reaction is exothermic, and water hitting concentrated acid flashes to steam and throws acid out",
								"is_correct": True,
							},
							{
								"text": "Acid poured first will not dissolve properly, leaving it stratified in the bucket",
								"is_correct": False,
							},
							{
								"text": "It is a labelling convention rather than a chemical one",
								"is_correct": False,
							},
							{
								"text": "Water added to acid neutralises it, wasting the product",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these statements about mixing fountain chemicals are true?",
						"type": "Multiple Choice",
						"explanation": (
							"These products are oxidisers, acids and organics. Combining them in a bucket, a "
							"feeder or on a scoop can ignite, and hypochlorite meeting acid releases chlorine gas. "
							"Residue left in a container is enough to start it — sharing a scoop is not a small "
							"shortcut."
						),
						"options": [
							{
								"text": "Calcium hypochlorite and trichlor combined in a container or feeder can ignite",
								"is_correct": True,
							},
							{
								"text": "Hypochlorite mixed with acid releases chlorine gas",
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
					{
						"question": "Non-chlorine shock sanitises the water as well as oxidising the organic load in it.",
						"type": "True-False",
						"explanation": (
							"It oxidises, which is a different job. It does not provide a sanitiser residual and "
							"it does not kill algae — and for a period after dosing it also registers as combined "
							"chlorine on a DPD test."
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
			"lesson_title": "Algae eradication and biological remediation",
			"chapter": 2,
			"estimated_minutes": 17,
			"summary": "Telling the three kinds apart, working a bloom out in the right order, and recognising when the problem is not in the water at all.",
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
							"title": "Green — free-floating, fast, and the most honest about itself",
							"body": (
								"<p>The common one. The water goes hazy, then cloudy, then green; the walls and "
								"floor go slippery. It grows quickly, it suspends in the water column, and it "
								"responds well to chemistry and filtration.</p>"
								"<p>Green is usually a straightforward failure of sanitiser or circulation, and "
								"it tells you so. Find out which, or you will be treating it again.</p>"
							),
						},
						{
							"title": "Mustard — chlorine-tolerant, in the shade, and living on your tools",
							"body": (
								"<p>Yellow or brownish, settles on walls and in shaded corners, brushes away very "
								"easily and then comes back in exactly the same place. It tolerates chlorine far "
								"better than green does, so a residual that controls green will not control "
								"it.</p>"
								"<p>Here is the part that catches people. Mustard algae survives on <b>anything "
								"that touched the water</b> — nets, brushes, hoses, vacuum heads, wetsuits, "
								"swimwear, the test kit. Treat the feature perfectly, then put an untreated brush "
								"back in it, and you have re-inoculated it yourself. The treatment worked; the "
								"tool undid it. Disinfect the equipment as part of the job, not afterwards.</p>"
							),
						},
						{
							"title": "Black — rooted into the surface, and chemistry never reaches the part that matters",
							"body": (
								"<p>Despite the name it behaves as cyanobacteria rather than as a green alga, and "
								"it behaves differently from both of the others. It appears as dark spots with "
								"heads that sit proud of the surface, and it <b>puts roots down into the finish "
								"itself</b> — into plaster, into grout lines, into anything porous or "
								"roughened.</p>"
								"<p>It also grows a protective layer over itself. Sanitiser therefore reaches the "
								"outside and nothing else, which is precisely why it returns in the same spots "
								"after a treatment that looked successful. The living part was never "
								"touched.</p>"
								"<p>It has to be <b>physically broken open before chemistry can do anything</b> — "
								"brushed hard, with a brush the finish can actually survive. And spots that keep "
								"returning in the same place are usually telling you that the surface there is "
								"damaged or porous enough to hold roots, which makes it a repair question as much "
								"as a chemistry one.</p>"
							),
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Brush, treat, filter, recheck — and the order is the whole method",
					"content": (
						"<p><b>Brush first.</b> Chemistry cannot kill a cell it cannot reach. "
						"Brushing breaks the protective layer open, lifts settled growth into "
						"suspension where the sanitiser and the filter can get at it, and on black "
						"algae it is the step without which nothing else matters. Get the walls, the "
						"floor, the corners, behind the weirs, and the steps and ledges everybody "
						"skips.</p>"
						"<p><b>Then treat</b>, per the label and the treatment design.</p>"
						"<p><b>Then filter, continuously, and clean the filter as it loads.</b> This "
						"is the step that gets abandoned. Killing algae does not remove it — it turns "
						"a living green problem into a suspended dead one that the filtration has to "
						"physically take out of the water. The water typically goes grey or milky "
						"first, which looks like the treatment failed and is actually the sign that "
						"it worked. Filter pressure will climb as it loads; clean or backwash it "
						"according to the equipment's instructions and keep going.</p>"
						"<p><b>Then re-test and re-brush.</b> Whatever survived regrows from the "
						"spots the brush missed, and it regrows fastest where the water moves least. "
						"Repeat until a brushed spot stays clean between visits — that, and not the "
						"colour of the water on the day, is the finish line.</p>"
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
						"pipework.</p>"
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
						"breathing aerosol, not by drinking or touching the water, and decorative "
						"fountains have caused documented outbreaks.</p>"
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
					"<p>Algaecides are not interchangeable. Quaternary ammonium products foam, "
					"copper-based products stain and have a real upper limit on the amount of copper "
					"a body of water may carry, and polymeric products behave differently again. "
					"Which one a feature may have, at what dose, and whether it is compatible with "
					"the sanitiser already in the water is a label and treatment-design "
					"question.</p>"
					"<p>The brush is the same kind of decision. A stainless brush takes black algae "
					"off plaster and destroys a vinyl liner, a painted finish, an acrylic panel or a "
					"coloured aggregate. A nylon brush is safe on those and does effectively nothing "
					"to rooted growth in plaster. Match the brush to the finish, and if you do not "
					"know what the finish is, find out before you touch it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why does black algae come back in the same spots after a treatment that appeared to work?",
						"type": "Single Choice",
						"explanation": (
							"It roots into the finish and grows a protective layer over itself, so sanitiser only "
							"ever reaches the outside. It has to be physically broken open by brushing before any "
							"chemistry can reach the living part."
						),
						"options": [
							{
								"text": "It is rooted into the surface under a protective layer, so chemistry never reaches the living part",
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
						"question": "After treating a green bloom, the water turns grey and cloudy. What is happening and what do you do?",
						"type": "Single Choice",
						"explanation": (
							"Killing algae does not remove it. The cloudiness is dead algae in suspension, which "
							"the filtration now has to take out physically — it is the sign the treatment worked. "
							"Run the filter continuously and clean it as it loads."
						),
						"options": [
							{
								"text": "It is dead algae in suspension — keep filtering continuously and clean the filter as it loads",
								"is_correct": True,
							},
							{
								"text": "The treatment failed and the feature should be re-dosed immediately",
								"is_correct": False,
							},
							{
								"text": "The shock has driven the pH out of range, and acid is needed",
								"is_correct": False,
							},
							{
								"text": "Nothing — it will settle to the floor on its own and can be left",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A feature is treated successfully and blooms again a fortnight later, repeatedly. Which of these could genuinely sustain that cycle?",
						"type": "Multiple Choice",
						"explanation": (
							"A continuing nutrient supply feeds regrowth, biofilm inside the pipework re-seeds "
							"the water and eats the residual, and mustard algae carried on undisinfected tools "
							"puts the infection straight back."
						),
						"options": [
							{
								"text": "Phosphate and nitrate arriving continuously from debris or site runoff",
								"is_correct": True,
							},
							{
								"text": "Biofilm inside the pipework consuming the residual and re-seeding the basin",
								"is_correct": True,
							},
							{
								"text": "Nets, brushes and hoses that were never disinfected after the last treatment",
								"is_correct": True,
							},
							{
								"text": "Calcium hardness sitting near the top of the design's range",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Brushing before treating a bloom matters mainly because it removes the visible growth so the feature looks better sooner.",
						"type": "True-False",
						"explanation": (
							"Brushing is not cosmetic. It breaks the protective layer open and lifts settled "
							"growth into suspension so the sanitiser and the filter can reach it — without it, "
							"the chemistry never contacts the cells that matter."
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
			"estimated_minutes": 16,
			"summary": "Why no single reading tells you whether water will scale or etch, what the saturation index combines, and why suppression is not removal.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Scale and etching are one problem with two signs",
					"content": (
						"<p>Water is either holding calcium carbonate in solution, dropping it out, "
						"or dissolving more of it. Those are not three unrelated conditions; they are "
						"three positions on one scale, and every body of water is somewhere on "
						"it.</p>"
						"<p>Drop it out and you get <b>scale</b>: on the heater, in the pipework, on "
						"the tile line, over the nozzles. Dissolve more and the water is "
						"<b>aggressive</b>: it takes calcium out of the plaster, the grout and the "
						"concrete, and it attacks metal at the same time.</p>"
						"<p>The trap is that <b>no single reading tells you which way you are "
						"going</b>. Not hardness on its own, not pH on its own, not alkalinity. Two "
						"features can show an identical pH and be on opposite sides of the line. This "
						"is the reading that has to be calculated rather than measured.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What the saturation index actually combines",
					"content": (
						"<p>The Langelier Saturation Index is the standard way of answering the "
						"question, and its shape is worth knowing because it tells you which levers "
						"you have:</p>"
						"<p><b>LSI = pH + temperature factor + calcium hardness factor + total "
						"alkalinity factor &minus; a total dissolved solids constant.</b></p>"
						"<p>It is balanced near <b>zero</b>. <b>Negative is aggressive</b> — the "
						"water is under-saturated and will dissolve calcium out of the surfaces it "
						"touches. <b>Positive is scaling</b> — it is over-saturated and will deposit. "
						"Each factor comes off a published table for the measured value; you are "
						"looking things up and adding them, not deriving anything.</p>"
						"<p>Reading it that way makes the practical point obvious. Every term is a "
						"lever. pH is the fastest one and the one people reach for, but it is also "
						"the term that drifts on its own, so a correction made only with acid or base "
						"is a correction that will need making again. Moving alkalinity or hardness "
						"shifts the index too, and those stay put. Temperature you generally cannot "
						"move at all — which is the subject of the next block.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The same water is scaling in one place and aggressive in another",
					"content": (
						"<p>Temperature is a term in the index, and a fountain is not all one "
						"temperature.</p>"
						"<p>A heat exchanger, a submerged luminaire, a pump seal and a UV sleeve all "
						"run hotter than the basin around them. Water that calculates out balanced at "
						"basin temperature can be firmly on the scaling side at those hot surfaces — "
						"which is exactly why scale shows up inside the equipment first while the "
						"tile still looks perfect, and why a heater fails on a feature whose test "
						"results have been filed as fine all season.</p>"
						"<p>It runs the other way too. An unheated outdoor basin in cold weather, or "
						"a feature running largely on cold make-up water, can be aggressive at the "
						"surface everybody is looking at.</p>"
						"<p>Flow matters alongside it: deposition is fastest where the water is hot "
						"<i>and</i> slow. <b>A single calculation from a single sample in the middle "
						"of the basin is not a statement about the whole system.</b></p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why this is expensive rather than merely untidy",
					"content": (
						"<p><b>On the scaling side.</b> Scale insulates, so a scaled heat exchanger "
						"makes the heater work harder, run hotter and eventually fail. It narrows "
						"pipework and blocks nozzles, so a display that was commissioned symmetrical "
						"goes lopsided and nobody can find a mechanical reason. It clouds glass and "
						"etches tile, it gets harder to remove the longer it sits, and it roughens "
						"surfaces — which gives algae exactly the porous foothold the previous lesson "
						"described.</p>"
						"<p><b>On the aggressive side.</b> There is no cleaning this one off. Plaster "
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
								"<p>Brushing, a scale stone on a tile line, or a proprietary cleaner applied to a "
								"deposit. This is the everyday answer for a waterline band and light "
								"deposition.</p>"
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
						"all summer.</p>"
						"<p>It also sets the limit of what chemistry can do. You can add things to "
						"water. You cannot subtract most of them. Past a certain point the only "
						"correction available is <b>dilution</b> — a partial drain and refill — and "
						"that is a water-use, discharge and design decision rather than something to "
						"start with a hose.</p>"
					),
				},
				ask_block(
					"The targets, the index range, and what may be done about a bad one",
					"<p>The acceptable index range, the target for every term in it, which "
					"sequestrant on what program, whether a partial drain is acceptable and where "
					"that water may legally be discharged, and whether a given finish may be acid "
					"washed at all — every one of those belongs to the feature's water treatment "
					"design, the finish and equipment manufacturers, and the authority over the "
					"site.</p>"
					"<p>This lesson teaches you to read the balance and to know which lever moves "
					"it. The numbers you push it to are on the job, and a partial drain in "
					"particular is a decision to bring back rather than to make at the "
					"feature.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Which of these tells you whether a feature's water will scale or etch?",
						"type": "Single Choice",
						"explanation": (
							"No single reading answers it. The saturation index combines pH, temperature, calcium "
							"hardness, alkalinity and TDS, and two features with identical pH can sit on opposite "
							"sides of balance."
						),
						"options": [
							{
								"text": "The saturation index, which combines pH, temperature, hardness, alkalinity and TDS",
								"is_correct": True,
							},
							{"text": "The calcium hardness reading on its own", "is_correct": False},
							{"text": "The pH reading on its own", "is_correct": False},
							{"text": "The total alkalinity reading on its own", "is_correct": False},
						],
					},
					{
						"question": "A feature's saturation index calculates out clearly negative. What is the water doing?",
						"type": "Single Choice",
						"explanation": (
							"Negative means under-saturated, so the water dissolves calcium out of what it "
							"touches — etching plaster, washing out grout, attacking concrete and metal. Positive "
							"is the depositing side."
						),
						"options": [
							{
								"text": "It is aggressive — dissolving calcium out of plaster, grout and concrete",
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
						"question": "Water that calculates out balanced at basin temperature cannot be scaling inside the heater.",
						"type": "True-False",
						"explanation": (
							"Temperature is a term in the index. A heat exchanger runs hotter than the basin, so "
							"the same water can be firmly on the scaling side at that surface — which is why "
							"equipment scales while the tile still looks fine."
						),
						"options": [
							{"text": "True", "is_correct": False},
							{"text": "False", "is_correct": True},
						],
					},
					{
						"question": "Which of these are true of sequestrants?",
						"type": "Multiple Choice",
						"explanation": (
							"They bind ions so the water carries more without depositing — suppression, not "
							"removal. Chlorine and UV break them down, so they are a repeating program, and when "
							"one runs out the metals it was holding can come out of solution together and stain."
						),
						"options": [
							{
								"text": "They suppress deposition rather than removing scale that has already formed",
								"is_correct": True,
							},
							{
								"text": "They are broken down by chlorine and UV, so they have to be re-dosed on a program",
								"is_correct": True,
							},
							{
								"text": "If one is allowed to run out, held metals can drop out of solution and stain at once",
								"is_correct": True,
							},
							{
								"text": "They dissolve existing scale off a heat exchanger without dismantling it",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
