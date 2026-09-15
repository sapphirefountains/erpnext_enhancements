# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 5 — Waterproofing.

Rewritten against Sapphire's own document, *Module 4: Structural Masonry, Waterproofing, &
Tile*. That document spans two courses: its lesson 4.1, the membrane and the sand broadcast, is
the first lesson here, and the masonry, mortar, tile, coping and scale-removal material is used
in Module 4. Where it states a figure, the figure is Sapphire's -- the 28-day minimum on a new
pour, saturated surface dry, typically 30 to 40 mils a coat, the second coat crossed at 90
degrees, oven-dried silica sand into the second coat while it is wet.

Its rule against bridging a structural expansion joint with tile is carried in the movement-joint
lesson here as well as in Module 4, because that is where this course details the joint.

The other two lessons -- submersible enclosures and potting, and structural vessels -- go beyond
what the document reaches. They keep the general-practice content they had, and the rule that
went with it: no invented figure where a label, a drawing or an engineer owns the answer.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 5 — Waterproofing",
		"summary": (
			"Install a membrane that is continuous, thick enough and actually bonded, broadcast "
			"sand so the mortar bed has something to hold on to, close up a submersible enclosure "
			"without throwing away its rating, and know what a structural vessel has to prove "
			"before anybody covers it."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Submerged waterproof membranes and sand broadcasting",
			"estimated_minutes": 20,
			"summary": "What is actually holding the water, the Rule of Clean the shell has to pass first, the two crossed coats, and the sand that decides whether tile stays on.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "The membrane is the thing holding the water",
					"content": (
						"<p>Tile is not waterproof. Grout is not waterproof. Concrete is not "
						"waterproof — it is a porous material, and water moves through a concrete "
						"wall under standing pressure whether or not there is a crack in it.</p>"
						"<p>In a submerged assembly the <b>membrane</b> is the barrier. Everything on "
						"top of it — the mortar bed, the tile, the grout, the stone — is a "
						"<b>finish</b>. It is there to be looked at, walked on and cleaned. It is not "
						"there to keep water in the basin, and a job that treats it as though it were "
						"has no waterproofing at all.</p>"
						"<p>There are two common ways to make that barrier. A <b>liquid-applied</b> "
						"membrane is rolled, brushed or trowelled on and cures into a seamless film; "
						"it goes round drains, steps and odd shapes without a joint, but its thickness "
						"is entirely whatever you put on. A <b>sheet</b> membrane arrives at a "
						"thickness the factory controlled, so it cannot be applied thin — but every "
						"overlap, every corner and every penetration is a seam <i>you</i> make, and "
						"the seam is the part that leaks.</p>"
						"<p>Neither one is more waterproof than the other. They just move the risk to "
						"a different place.</p>"
						"<p><b>Sapphire's document works in the first of the two.</b> It specifies a "
						"fluid-applied waterproofing membrane — it names <b>Basecrete</b> and "
						"<b>Thoroseal</b> as the kind of material — mixed on site and brushed or "
						"rolled onto the shell. Everything below is written for that, and the fact "
						"that its thickness is entirely whatever you put on is exactly why the rest "
						"of this lesson keeps coming back to measurement.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A membrane fails where it stops",
					"content": (
						"<p>Water does not need a big hole and it does not need much pressure. It "
						"needs one place the barrier is not, and it will find it.</p>"
						"<p><b>It has to be continuous.</b> The membrane runs across the floor, up "
						"every wall, and <b>turns up past the water line</b> — a membrane that stops "
						"at the level somebody thinks the water will sit at is a membrane that stops "
						"below the level it will actually sit at the first time it rains. It wraps "
						"every penetration: drains, returns, jets, light niches, sleeves, anchors. It "
						"carries into every corner, and it terminates where the detail says, not "
						"where the roller ran out.</p>"
						"<p><b>It needs reinforcement at every change of plane.</b> Inside corners, "
						"floor-to-wall, step nosings and the lip of a drain are where the structure "
						"moves, where a coating is asked to bridge a crack, and where it is pulled "
						"thin over an edge. A liquid system carries a reinforcing fabric bedded into "
						"the coating at those lines, and a sheet system has pre-formed corner pieces "
						"for the same reason. Both are part of the system, not an upgrade.</p>"
						"<p><b>And it fails at the cold joint.</b> The line where the floor pour meets "
						"the wall pour is the one place in a basin where two separate placements of "
						"concrete are asked to be one wall. Sapphire's document singles it out: when the "
						"membrane goes on, that joint gets extra attention rather than the same pass as "
						"everything else.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The Rule of Clean, and the condition the concrete has to be in",
					"content": (
						"<p>Sapphire's document calls this <b>the Rule of Clean</b>. A membrane is only "
						"as good as the surface under it, and none of this is optional.</p>"
						"<p><b>Cured.</b> A new pour gets a <b>minimum of 28 days</b> before anything "
						"is applied to it. Green concrete is still shrinking, and a membrane put on it "
						"is a film stretched over a surface that has not finished moving.</p>"
						"<p><b>Structurally sound.</b> If the top skin of the slab can be pulled off, "
						"the membrane will pull it off. Honeycombing at the base of a wall is the same "
						"problem with a different name.</p>"
						"<p><b>Clean.</b> Free of <b>laitance</b> — the weak, dusty layer that floats "
						"to the surface of concrete — and of form-release agents, curing compounds, "
						"oil, dirt and dust. Every one of those sits between the membrane and the "
						"concrete and stops the bond happening there, and most of them are invisible "
						"on a grey slab.</p>"
						"<p><b>Cracks chased and filled.</b> Any structural crack is <b>chased out</b>, "
						"filled with an <b>engineering-grade waterproof hydraulic cement</b>, and "
						"allowed to cure before the membrane goes over it. A crack that is simply "
						"coated over is a crack with a film stretched across it, and the film is the "
						"part that tears.</p>"
						"<p><b>Profiled.</b> This one is not on Sapphire's list; it is on most "
						"membrane systems' data sheets. A steel-trowelled surface is polished, and "
						"where the system calls for a surface profile it is produced by grinding or "
						"shot blasting, so there is texture for the coating to key into.</p>"
						"<p><b>Damp, not dry.</b> The concrete goes into a <b>saturated surface dry "
						"(SSD)</b> condition before application: damp, with <b>no standing pooling "
						"water</b> on the floor. Read that twice, because the instinct on any other "
						"coating job is to get the substrate as dry as possible, and here that "
						"instinct is wrong — Sapphire's document says SSD and SSD is what we do. A "
						"bone-dry slab drinks the mix water straight out of a cementitious membrane "
						"before it has finished hydrating, and you get a chalky, under-cured coat that "
						"looks fine. Standing water does the opposite and thins the material where it "
						"has pooled. Damp, no puddles.</p>"
						"<p>When the substrate was not sound this does not look like a bonding "
						"failure. It looks like the membrane failed — until you turn the piece over "
						"and the top few millimetres of concrete are stuck to the back of it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Mixing it, and the two coats",
					"content": (
						"<p><b>Mix it with a heavy-duty drill mixer</b>, to a smooth, lump-free "
						"consistency. A lump is a place where the film is a different thickness and a "
						"different material from everything around it, and it is still a lump after it "
						"cures. A light-duty drill stalls in this material and leaves you mixing by "
						"hope.</p>"
						"<p><b>First coat.</b> Apply it with a <b>heavy masonry brush</b> or a "
						"specialised roller, at the manufacturer's specified thickness — Sapphire's "
						"document puts that at <b>typically 30 to 40 mils per coat</b>. A mil is a "
						"thousandth of an inch, so that is not a thickness anybody judges by eye: check "
						"it with a <b>wet film gauge while the coating is still wet</b>, because once "
						"it cures there is nothing left to measure without cutting it. Work the "
						"material uniformly across the floor and <b>wrap it up the basin walls</b>, "
						"paying extra attention to the <b>cold joints where the floor meets the "
						"wall</b>.</p>"
						"<p><b>Second coat, at 90° to the first.</b> Once the first coat is <b>dry to "
						"the touch</b>, apply the second <b>perpendicular</b> to the direction you ran "
						"the first. That cross-hatching is the whole point of two coats: a streak, a "
						"holiday or a line the brush missed running one way is covered by a coat "
						"running the other, and that is what guarantees complete coverage without "
						"pinholes. Two coats brushed in the same direction repeat the same misses "
						"twice.</p>"
						"<p>A membrane that looks continuous and is half thickness over the high spots "
						"of an uneven slab is thin exactly where it can least afford to be, and thin is "
						"where pinholes are. It passes a casual look every time.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Sand in the second wet coat is what the mortar grips",
					"content": (
						"<p>A cured waterproofing membrane is smooth, and smooth is the problem. "
						"<b>Smooth membranes repel standard tile thinsets and mortars.</b> The bed sits "
						"on the film rather than gripping it, and under water pressure it delaminates — "
						"which is a tile job coming off a wall that was, in every other respect, "
						"correct.</p>"
						"<p>So while the <b>second coat is still wet and tacky</b>, aggressively "
						"broadcast <b>oven-dried silica sand</b> over the entire surface until it is "
						"fully saturated. Throw it so it lands and settles rather than pouring it in a "
						"heap, and keep going until the surface will not take any more. The coating "
						"cures around the lower half of each grain and leaves the upper half standing "
						"proud. That is a <b>mechanical keyway</b>: a rough, sandpaper-like texture the "
						"mud bed or the stone mortar can grip permanently, instead of a slick face it "
						"can only sit against.</p>"
						"<p><b>The sand is oven-dried for a reason.</b> Damp sand clumps, does not "
						"broadcast evenly, and carries whatever it picked up into the coating. Dirty "
						"sand is a bond breaker in its own right.</p>"
						"<p><b>Then the excess has to come off.</b> Once the membrane has cured, sweep "
						"away all loose, unbonded sand before any tile is laid. Every grain that is not "
						"locked into the coat is loose, and loose sand under a mortar bed is exactly "
						"the same failure as dust: the mortar bonds to the sand, and the sand bonds to "
						"nothing.</p>"
						"<p>Some tile-industry membranes are sold as taking thinset directly on the "
						"cured film. Sapphire's document does not offer that as a choice on a submerged "
						"basin — the broadcast goes in. It is a five-minute step at the end of a long "
						"day, and it is the step that decides whether the tile is still on the wall "
						"years from now.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A flood test is the only honest proof",
					"content": (
						"<p>Everything above is process. A flood test is <b>evidence</b>. It is the "
						"only point in the job where the membrane is asked the only question that "
						"matters — does it hold water — under the conditions it will actually live "
						"in.</p>"
						"<p>Plug the outlets, fill it, mark the water level against a fixed reference "
						"rather than a pencil line on something that moves, and leave it. Then read it "
						"again, and look underneath: the slab soffit, the equipment vault, the "
						"penetrations, the ceiling below. Staining and damp are as much a result as a "
						"number on a tape.</p>"
						"<p>Two things will fool you. Water evaporates from an open surface, so a drop "
						"is not automatically a leak. Evaporation follows the weather — sun, wind, dry "
						"air and the gap between water and air temperature all drive it, and it slows "
						"at night without stopping, because water warmer than the air goes on "
						"evaporating in the dark. So a drop cannot be read against the clock. The way "
						"to separate them is to float a container of water in the vessel and mark that "
						"too: it loses evaporation and nothing else, so the gap between the two marks "
						"is the leak. And a slow leak into free-draining ground or a sealed void "
						"produces no visible water anywhere, which makes it easier to talk yourself "
						"out of.</p>"
						"<p><b>It happens before anything covers it.</b> A membrane that fails a test "
						"on bare concrete is a morning's repair. The same failure found after the "
						"mortar bed, the tile, the grout and the coping are on is demolition — and "
						"under a finish the water tracks sideways, so the wet patch is nowhere near "
						"the hole.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Demonstrated to a Lead Installer before you are signed off",
					"items": [
						"Prepare a mock concrete profile to a proper saturated surface dry (SSD) condition",
						"Mix and apply a two-coat fluid waterproofing membrane layout with zero visible pinholes or holidays",
						"Execute a uniform sand-broadcast keyway across a wet membrane coat, achieving an even sandpaper finish",
					],
				},
				ask_block(
					"Sapphire's document gives the shape and the figures; the pail gives the rest",
					"<p>The numbers above are Sapphire's own: 28 days on a new pour, saturated surface "
					"dry before you start, two coats crossed at 90°, oven-dried silica sand into the "
					"second while it is still wet, and a typical 30 to 40 mils a coat. Use them.</p>"
					"<p>What the document cannot give you is the rest of the data sheet for the "
					"product in front of you — starting with the thickness. Sapphire's own wording is "
					"<b>the manufacturer's specified thickness</b>, with 30 to 40 mils given as what "
					"that typically is; where the data sheet in front of you says something else, the "
					"data sheet is the figure and 30 to 40 mils is the sanity check. How long between "
					"coats, how long before it can be flooded, whether it needs a primer, how much "
					"water goes into the mix, which grade of silica sand, and what surface profile the "
					"substrate has to be brought to belong there too, and they differ between products "
					"that look identical in the bucket. Basecrete and Thoroseal are named as the kind "
					"of material; the pail on this job is the one that governs.</p>"
					"<p>Mixing products across systems — one maker's primer under another's membrane, "
					"a fabric that is not the one it was tested with — voids the warranty and is a "
					"genuine incompatibility, not a paperwork problem. How long the flood test is "
					"held, and what loss is acceptable, come from the specification.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "A basin is finished in dense porcelain tile with epoxy grout. What is actually keeping the water out of the structure?",
						"type": "Single Choice",
						"explanation": (
							"The membrane under the mortar bed is the barrier. Tile, grout and concrete are all "
							"permeable to standing water; the finish is there to be looked at and walked on."
						),
						"options": [
							{"text": "The waterproof membrane beneath the mortar bed", "is_correct": True},
							{
								"text": "The epoxy grout, which is the waterproof part of a tiled finish",
								"is_correct": False,
							},
							{
								"text": "The tile itself, because dense porcelain absorbs almost nothing",
								"is_correct": False,
							},
							{"text": "The concrete shell, once it has fully cured", "is_correct": False},
						],
					},
					{
						"question": "A new concrete basin is ready for its membrane. Which of these does Sapphire's Rule of Clean require first?",
						"type": "Multiple Choice",
						"explanation": (
							"Sapphire's document is specific: a minimum 28-day cure on a new pour, the surface free "
							"of laitance, form release, curing compounds, dirt and dust, structural cracks chased "
							"out and filled with an engineering-grade waterproof hydraulic cement and allowed to "
							"cure, and the concrete at saturated surface dry — damp, with no standing pooling "
							"water. Drying the slab right out is the instinct from other coating work and it is the "
							"wrong one here: a bone-dry slab pulls the mix water out of the membrane before it has "
							"hydrated."
						),
						"options": [
							{
								"text": "A minimum 28-day cure on a new pour before anything is applied",
								"is_correct": True,
							},
							{
								"text": "Laitance, form-release agents and curing compounds removed from the surface",
								"is_correct": True,
							},
							{
								"text": "Structural cracks chased out, filled with an engineering-grade waterproof hydraulic cement and left to cure",
								"is_correct": True,
							},
							{
								"text": "The slab dried out as far as possible, so there is no moisture left in the concrete",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The second coat of membrane goes on perpendicular — at 90° to the first — once the first is dry to the touch. What does that cross-hatching achieve?",
						"type": "Single Choice",
						"explanation": (
							"A streak, a holiday or a line the brush missed running one way is covered by a coat "
							"running the other. Two coats brushed in the same direction repeat the same misses "
							"twice, which is how a membrane ends up continuous to look at and pinholed in fact."
						),
						"options": [
							{
								"text": "Complete coverage without pinholes — a miss in one direction is covered by the coat crossing it",
								"is_correct": True,
							},
							{
								"text": "The required film thickness in one pass, so the wet film gauge is not needed",
								"is_correct": False,
							},
							{
								"text": "A mechanical key between the two coats, which is what the sand broadcast would otherwise provide",
								"is_correct": False,
							},
							{
								"text": "A faster cure, because the first coat is still releasing water when the second goes on",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is oven-dried silica sand broadcast into the second coat while it is still wet and tacky?",
						"type": "Single Choice",
						"explanation": (
							"A smooth cured membrane repels standard tile thinsets and mortars, and a bed that is "
							"only sitting on the film delaminates under water pressure. Sand half-locked into the "
							"coat leaves a sandpaper-like keyway the mud bed can grip. Once it has cured, the loose "
							"unbonded sand is swept off — the mortar would bond to it, and it bonds to nothing."
						),
						"options": [
							{
								"text": "To leave a rough mechanical keyway the mortar bed can grip, because a smooth membrane repels thinset",
								"is_correct": True,
							},
							{
								"text": "To build the membrane up to its required thickness more cheaply",
								"is_correct": False,
							},
							{
								"text": "To absorb the excess water so the coat cures faster",
								"is_correct": False,
							},
							{
								"text": "To protect the cured membrane from being walked on",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Submersible enclosures and potting",
			"estimated_minutes": 16,
			"summary": "What an IP or NEMA rating actually promises, why the rating is yours to lose, and what potting buys and costs.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "What a rating actually promises",
					"content": (
						"<p>Everything in this lesson sits in water with power going to it, so start "
						"with the part that is not negotiable: an enclosure under water is what stands "
						"between energised conductors and the water people put their hands in. In the "
						"United States that installation is governed by <b>NEC Article 680</b>, which "
						"covers pools, fountains and similar installations — bonding, grounding and "
						"ground-fault protection around water. It is <b>qualified</b> electrical work, "
						"and qualified is a defined word rather than a compliment: if you have not "
						"been trained and designated for the equipment in front of you, that part of "
						"the job is not yours to do.</p>"
						"<p>Nothing comes apart while it can still be energised, and that applies to "
						"low-voltage lighting too. Lock-out/tag-out is not a step you can pick up from "
						"a paragraph here — it is a written program with its own training, its own "
						"equipment-specific procedures and its own authorised people, and it ends in a "
						"verified dead test rather than a lock on a handle. Module 9 covers what it is "
						"for. If you are not the person authorised to lock that source out, the answer "
						"is to stop and go and get them.</p>"
						"<p>Now the ratings. <b>IP</b> — Ingress Protection — is two digits. The first "
						"is protection against solids, the second against water. <b>IPX7</b> means the "
						"enclosure survived immersion at a stated shallow depth for a stated short "
						"time. <b>IPX8</b> means continuous immersion, and the depth and duration are "
						"whatever the <i>manufacturer</i> declares — IPX8 on its own is not a number, "
						"it is a pointer to the manufacturer's statement.</p>"
						"<p><b>NEMA</b> types describe the same idea plus the things IP ignores — "
						"corrosion, ice, gasket ageing. Type 6 covers occasional temporary submersion; "
						"<b>Type 6P</b> is the one that means prolonged submersion.</p>"
						"<p>Two traps in that. First, the numbers are not a ladder: an enclosure tested "
						"for immersion has <i>not</i> automatically been tested against pressure "
						"jetting, so it may carry IPX7 and not IPX5. A device has to be tested for "
						"each claim it makes. Second, and more important, <b>submersion is a different "
						"duty from splashing</b>. A splashed box sees water arrive and leave. A "
						"submerged box sees constant pressure pushing on every seal, day and night, "
						"plus a heat cycle that makes the air inside expand and contract — so it "
						"breathes, and what it breathes in at the end of a hot afternoon is water.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The rating is for an enclosure assembled correctly, and you assembled it",
					"content": (
						"<p>A rating is not a property of the box on the shelf. It describes an "
						"enclosure <b>as tested</b>: with its own gaskets, its own hardware, every "
						"entry made up the way the maker intended. What you hand over is only rated if "
						"it is still that enclosure.</p>"
						"<p>One unused gland opening with no blanking plug in it. One gasket pinched "
						"under a lid, twisted in its groove, or left off because it stuck to the "
						"bench. One cover screw missed, or a lid pulled down one corner at a time so "
						"the gasket is compressed on one side and open on the other. One gland tightened "
						"onto a cable it was never sized for, so the sealing insert cannot close around "
						"it. A hole drilled in the field for a cable somebody forgot.</p>"
						"<p>Any one of those and the rating is gone — not degraded, gone. And the box "
						"looks exactly the same as one that is right, which is why this is a "
						"checked-before-it-goes-under job rather than a careful-person job.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cable entries are where the water gets in, and sometimes it is inside the cable",
					"content": (
						"<p>Almost every wet enclosure that fails, fails at an entry. The body is a "
						"moulding or a casting with no way through it; the lid has one large, "
						"well-understood gasket; the entries are small, numerous, and made up by hand "
						"on site.</p>"
						"<p>So: a gland sized to the <b>actual measured outside diameter</b> of the "
						"cable, not its nominal size and not the hole it happens to fit. One cable per "
						"gland unless the gland is specifically made for more. Blanking plugs in every "
						"unused entry. Strain relief, so a tug on the cable pulls against the gland "
						"body rather than dragging the jacket through the seal. And where the cable "
						"approaches from above, a drip loop, so water running down the jacket falls off "
						"the bottom of the loop instead of arriving at the entry.</p>"
						"<p>Conduit is a pipe, and it does what pipes do. A conduit run that ends in an "
						"enclosure will deliver to that enclosure any water that gets into it anywhere "
						"along its length, which is why conduit entries get sealed rather than "
						"trusted.</p>"
						"<p>Then there is the one that makes people doubt their own work. Water travels "
						"<b>inside</b> a cable — along the interstices between conductors and under the "
						"jacket — by capillary action, for a long way, from a nicked jacket or a wet "
						"splice somewhere else entirely. It arrives inside an enclosure whose lid has "
						"never been off and whose glands are perfect, and it fills it. If a box keeps "
						"making water and every seal is sound, suspect the cable: condensation leaves a "
						"box damp, but a wicking cable fills it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Potting leaves water nowhere to be",
					"content": (
						"<p>Potting is filling a cavity — around a splice, a sensor, a driver — with a "
						"resin that cures solid. It does not seal water out by holding it back at a "
						"boundary the way a gasket does. It works by <b>occupying the volume</b>: "
						"there is no void for water to collect in and no air gap for it to travel "
						"along. It also supports the joint mechanically and takes the vibration and "
						"the flex out of a connection that would otherwise work-harden and break.</p>"
						"<p>Most potting compounds are two parts mixed by ratio. The ratio is not "
						"advisory — it is the chemistry, and off-ratio resin cures soft, cures slowly, "
						"or does not cure. <b>Mix it thoroughly, including the sides and the bottom of "
						"the cup</b>: an unmixed streak stays liquid forever and leaves a soft channel "
						"running straight through the middle of a pot that looks perfect from "
						"outside.</p>"
						"<p>And the rule that catches people: <b>whatever is in the cavity when you "
						"pour stays there.</b> A damp splice, condensation on a cold component, flux "
						"residue, a wet glove print, water in the strands of a conductor. Potting over "
						"moisture does not dry it out; it seals it permanently against copper, in the "
						"warm. Dry it and clean it first, and if you cannot be sure it is dry, do not "
						"pour.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Potting is a one-way door",
					"content": (
						"<p>A potted assembly is, for practical purposes, <b>not repairable</b>. You "
						"cannot get a probe onto a conductor, you cannot reterminate, and digging "
						"cured resin out of a cavity damages the thing you were trying to save. A "
						"fault under cured resin turns a repair into a replacement, and on a fixture "
						"that is grouted into a niche under water, that is a much bigger day than it "
						"sounds.</p>"
						"<p>Which moves all the work to <b>before</b> the pour. Every connection made "
						"up and pulled on. Continuity and insulation resistance measured. Polarity and "
						"conductor identification confirmed against the drawing. The assembly oriented "
						"the way it has to sit, and the cable routed the way it has to leave. Function "
						"tested if it can be. Once you are satisfied, pour — and understand that you "
						"have just made your last decision about that assembly.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "Terms worth having straight",
					"cards": [
						{
							"front": "IP rating",
							"back": "Ingress Protection: two digits, the first for solids, the second for water. A rating is earned by test, and it describes the enclosure as assembled at the factory.",
						},
						{
							"front": "IPX7 vs IPX8",
							"back": "IPX7 is temporary immersion at a stated shallow depth for a stated short time. IPX8 is continuous immersion, at a depth and duration the manufacturer declares. Neither implies resistance to pressure jetting, which is tested separately.",
						},
						{
							"front": "NEMA Type 6P",
							"back": "The NEMA type meaning prolonged submersion, as opposed to Type 6, which covers occasional temporary submersion. NEMA also accounts for corrosion and ice, which IP does not.",
						},
						{
							"front": "Cable gland",
							"back": "The fitting that seals a cable where it enters an enclosure, sized to the cable's actual outside diameter. Also the part that takes the strain, so the jacket is never dragged through the seal.",
						},
						{
							"front": "Capillary wicking",
							"back": "Water travelling inside a cable, under the jacket and between the strands, from a wet splice or damaged jacket elsewhere. It fills an enclosure whose seals are all sound.",
						},
						{
							"front": "Pot life",
							"back": "How long a mixed two-part resin stays pourable. It is a property of the product and it shortens as the temperature rises — it is not the same number as cure time.",
						},
						{
							"front": "Equipotential bonding",
							"back": "Tying the metal parts around a body of water together so there is no voltage difference between anything a person can touch. It is about removing differences, not about providing a path to earth, and it is required work under NEC Article 680.",
						},
						{
							"front": "Class A GFCI",
							"back": "A ground-fault circuit interrupter that trips at roughly 4 to 6 mA of ground-fault current — set just below the level at which a person's muscles lock and they cannot let go, and far below what it takes to stop a heart, which is why it is required around water.",
						},
					],
				},
				ask_block(
					"The gland, the resin and the rating all belong to the product",
					"<p>Which gland suits which cable, which resin is compatible with the components "
					"and the water it will sit in, the mix ratio, the pot life, the cure time and the "
					"temperature it needs to cure at, the torque on the cover fasteners, and the depth "
					"and duration the submersion rating was declared for — every one of those is on "
					"the manufacturer's documentation for the part in your hand.</p>"
					"<p>One more that gets assumed: some submersible fixtures are <b>factory sealed "
					"and not field-openable at all</b>, and opening one ends both its rating and its "
					"warranty. Whether a given fixture may be opened, potted or re-terminated in the "
					"field is a question for the submittal and the supervisor before the lid comes "
					"off, not after.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "An enclosure is marked for continuous submersion. On site a spare hole was drilled in it for a cable that was never run, and the hole is bare. What is its rating now?",
						"type": "Single Choice",
						"explanation": (
							"A rating describes the enclosure as tested and assembled. An open entry is an open "
							"entry; there is no partial credit, and the box looks the same as one that is right."
						),
						"options": [
							{
								"text": "It has none — the rating describes the enclosure as assembled, and an open entry ends it",
								"is_correct": True,
							},
							{
								"text": "It keeps the rating as long as the hole ends up above the water line",
								"is_correct": False,
							},
							{"text": "It drops one step down the rating scale", "is_correct": False},
							{
								"text": "The rating is restored by taping or siliconing over the hole",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A submersible junction box keeps filling with water over a season. The lid gasket is sound, the glands are correct, and the box has never been opened on site. What should you suspect first?",
						"type": "Single Choice",
						"explanation": (
							"Water wicks along the inside of a cable from a damaged jacket or a wet splice "
							"elsewhere and arrives inside a perfectly sealed box. Condensation leaves a box damp; "
							"a wicking cable fills it."
						),
						"options": [
							{
								"text": "Water travelling inside the cable itself, from a wet splice or nicked jacket elsewhere",
								"is_correct": True,
							},
							{
								"text": "Condensation from day-to-night temperature swings",
								"is_correct": False,
							},
							{
								"text": "Pressure at depth forcing water straight through the enclosure wall",
								"is_correct": False,
							},
							{
								"text": "The lid gasket having taken a permanent set over the season",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why must an assembly be fully tested and verified before it is potted?",
						"type": "Single Choice",
						"explanation": (
							"Cured resin cannot be dug out without destroying what it surrounds, so a fault found "
							"afterwards is a replacement rather than a repair. The pour is the last decision you "
							"get to make about that assembly."
						),
						"options": [
							{
								"text": "Potting is effectively irreversible, so a fault found afterwards means replacing the assembly",
								"is_correct": True,
							},
							{
								"text": "Test readings are inaccurate once resin is present, but the assembly can still be repaired",
								"is_correct": False,
							},
							{
								"text": "Cured resin can be cut out and the pot remade, but it wastes material",
								"is_correct": False,
							},
							{
								"text": "The resin cures faster when the circuit has been proved live",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of a two-part potting compound?",
						"type": "Multiple Choice",
						"explanation": (
							"Ratio and cure belong to the product, an unmixed streak never cures and leaves a soft "
							"channel through the pot, and anything damp in the cavity is sealed in against the "
							"conductors. Potting is not a serviceable finish."
						),
						"options": [
							{
								"text": "The mix ratio and cure time come from the product documentation, not from experience",
								"is_correct": True,
							},
							{
								"text": "An unmixed streak can stay liquid and leave a soft channel through the middle of the pot",
								"is_correct": True,
							},
							{
								"text": "Moisture in the cavity when you pour is sealed in permanently",
								"is_correct": True,
							},
							{
								"text": "The cured resin can be peeled back later to service the connection",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Waterproofing structural vessels",
			"estimated_minutes": 18,
			"summary": "Holding water in versus keeping groundwater out, why an empty basin can float, and when to prove it holds.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Two different jobs that both get called waterproofing",
					"content": (
						"<p><b>Keeping water in</b> is the job on a vessel above the water table. The "
						"pressure comes from inside, and the membrane goes on the inside face — the "
						"wet side, the <b>positive side</b>. That is where it belongs, because the "
						"water is then pressing the membrane <i>onto</i> the substrate. A defect stays "
						"local and the pressure is working with you.</p>"
						"<p><b>Keeping groundwater out</b> is a different problem, and it starts the "
						"moment any part of the structure sits below the water table or in ground that "
						"holds water after rain. Now there is pressure from outside as well. A "
						"membrane on the inside face is then on the <b>negative side</b> of that "
						"pressure, and groundwater is trying to push it off the wall rather than onto "
						"it. Negative-side waterproofing is a specific, engineered choice with "
						"specific products — not the same tub of membrane applied to the other "
						"face.</p>"
						"<p>Which one you are doing is decided by the water table and by the drawings, "
						"and plenty of fountains are both: a basin holding water in, sitting in ground "
						"that is trying to push water back through it.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The concrete is not the barrier, and the joints are the leak",
					"content": (
						"<p>A concrete vessel is a container in the way a wicker basket is a container. "
						"It has the shape. It is not, by itself, watertight — water passes through the "
						"paste under sustained pressure, and long before that it finds the features "
						"the pour left behind.</p>"
						"<p>Those features are predictable, which is the useful part. <b>Construction "
						"joints</b> between pours, where two placements meet and did not bond. "
						"<b>Form-tie holes</b> straight through the wall. <b>Honeycombing</b> at the "
						"base of a wall where the concrete did not consolidate. <b>Cracks</b> from "
						"shrinkage and restraint. <b>Penetrations</b>, where a sleeve or a pipe passes "
						"through and the concrete was cast around something that moves differently "
						"from it.</p>"
						"<p>The repairs have a method, and it is not caulk. Sapphire's document is "
						"explicit about cracks: <b>chase them out</b>, fill them with an "
						"<b>engineering-grade waterproof hydraulic cement</b>, and let them cure before "
						"anything is coated over them. The same document holds a new pour to a "
						"<b>minimum 28-day cure</b> before the waterproofing starts, and puts the "
						"concrete at <b>saturated surface dry</b> — damp, with no standing pooling "
						"water — when it does.</p>"
						"<p>Some of the defences are cast in and cannot be added afterwards — a "
						"waterstop at a construction joint, a hydrophilic strip, a crystalline "
						"admixture in the mix. If it was not in the pour it is not in the wall, and "
						"the recovery is a surface system and a repair detail rather than the thing "
						"that was designed. That is worth saying out loud at the time rather than "
						"discovering at the flood test.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A drained basin in wet ground can float",
					"content": (
						"<p>This one is genuinely counter-intuitive and it injures people. An "
						"in-ground vessel displaces groundwater, and displaced water pushes back. "
						"Water weighs about <b>62.4 pounds per cubic foot</b>, so every foot the water "
						"table stands above the underside of the slab puts about 62 pounds of uplift "
						"on <b>every square foot</b> of that slab. Across a basin floor that adds up "
						"to tons.</p>"
						"<p>Full of water, the vessel is heavy enough to stay where it is. <b>The "
						"water you are about to pump out is part of what is holding it down.</b> "
						"Drained, the same structure can lift, tilt, crack its floor, or shear at the "
						"wall-to-slab joint — and it can do it with people standing in it.</p>"
						"<p>So draining an in-ground vessel is not a housekeeping decision. Some are "
						"built with a hydrostatic relief valve in the floor that lets groundwater in "
						"to equalise, and a seized one offers no protection while looking exactly like "
						"a working one. Others must be dewatered from outside before they are emptied. "
						"Before a below-grade basin is drained, someone has to know where the water "
						"table is and what the structure was designed to survive empty. If nobody "
						"knows, that is a stop, not a guess.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A movement joint has to be carried through every layer",
					"content": (
						"<p>Structures move — thermally, as concrete shrinks, as ground settles, and "
						"in some places seismically. A movement joint exists so that the movement "
						"happens <b>where somebody chose</b>, in a gap designed to take it, instead of "
						"wherever the structure happens to be weakest.</p>"
						"<p>Run a membrane straight across a joint like that and you have handed all "
						"of that movement to a film you could barely measure. It tears, in a "
						"line, exactly along the joint — and it is the one crack you can predict "
						"before the job starts.</p>"
						"<p>The detail is always the same idea: the membrane is <b>deliberately not "
						"bonded</b> across the joint, and there is extra material there — a loop, a "
						"bond breaker, a joint tape or a preformed profile — so the movement is taken "
						"up by slack instead of by stretch. More coats of membrane over the joint is "
						"not that detail, and it is the substitution people reach for.</p>"
						"<p>And the joint has to line up <b>all the way through</b>: through the "
						"structure, through the waterproofing, through the mortar bed, through the "
						"tile and the grout, with a compressible sealant at the surface. Sapphire's "
						"document states that last part as a flat prohibition — <b>never bridge a "
						"structural concrete expansion joint with tile</b> — and names what goes "
						"across it instead: an approved <b>underwater-grade polyurethane or silicone "
						"expansion sealant</b>, so the shell can flex safely without shattering the "
						"tile lines. A structural joint that gets tiled over reappears as a crack "
						"through the finish in the same line, and the repair is the whole run.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Before a vessel is handed over to the finish trades",
					"items": [
						"A new pour has had its minimum 28-day cure before anything was applied to it",
						"The substrate is sound, clean, profiled, and at saturated surface dry — damp, with no standing pooling water",
						"Honeycombing and tie holes are repaired by the specified method, and structural cracks were chased out, filled with an engineering-grade waterproof hydraulic cement and left to cure",
						"Every penetration, sleeve and embedment is sealed and detailed",
						"Reinforcement is bedded in at every inside corner and change of plane",
						"Movement joints are detailed with slack, and their positions are marked so the finish trades can carry them through",
						"The membrane turns up above the working water level everywhere, including behind coping and at overflows",
						"Wet film thickness was checked while the coat was wet — the specified thickness, typically 30 to 40 mils a coat — and the second coat crossed the first at 90°",
						"Oven-dried silica sand was broadcast into the second coat while it was still wet, and the loose unbonded excess has been swept off",
						"The vessel has been filled and held, with the level read against a fixed reference and evaporation accounted for",
						"Anything found by that test has been repaired and the test repeated",
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Find the leak while it is still cheap",
					"content": (
						"<p>There is one moment in a vessel's life when a leak costs almost nothing: "
						"when the structure and the waterproofing are finished and nothing is on top "
						"of them. At that point the whole membrane is visible, the outside of the wall "
						"is often still reachable, and a repair is a patch.</p>"
						"<p>Every hour after that the same defect gets more expensive, and not "
						"linearly. Once the mortar bed and tile are on, the same repair is demolition "
						"of the finish. Once the coping is set, the equipment is plumbed, the ground "
						"is landscaped and the client is standing there for the handover, it is all of "
						"that plus a programme, plus the conversation.</p>"
						"<p>Read the test the way the first lesson set out — level marked against "
						"something fixed, evaporation separated out with a floating reference before a "
						"drop is called a leak. What a whole vessel adds is everywhere else to look: "
						"the outside of the wall, the slab soffit, the equipment vault, the ground "
						"beside it. And if somebody has to go into a vault or a below-grade chamber to "
						"look, that is a confined-space question before it is a waterproofing one — "
						"Module 9 covers it, and it is not a decision the person holding the torch "
						"makes on their own.</p>"
						"<p>If it fails, say so. A vessel that failed its test and got covered anyway "
						"is the single most expensive thing in this module.</p>"
					),
				},
				ask_block(
					"The groundwater, the details and the test regime are engineering",
					"<p>Where the water table sits, whether the structure may be emptied and under "
					"what conditions, which waterproofing system is specified and on which face, where "
					"the movement joints are and what detail carries them through, and how the vessel "
					"is filled and tested — all of that comes from the <b>geotechnical report, the "
					"structural drawings, the waterproofing submittal and the engineer</b>.</p>"
					"<p>How long a test is held and what loss counts as a pass come from the "
					"specification for this job. If the drawings are silent, that is a question for "
					"the project manager before the vessel is filled — and definitely before anything "
					"is set on top of it.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a membrane normally applied to the water side of a vessel wall rather than the outside face?",
						"type": "Single Choice",
						"explanation": (
							"On the positive, wet side the water pressure presses the membrane onto the substrate. "
							"On the negative side the pressure is trying to push it off the wall, which is a "
							"different and engineered system."
						),
						"options": [
							{
								"text": "Water pressure then presses the membrane onto the substrate instead of trying to push it off",
								"is_correct": True,
							},
							{
								"text": "It keeps the concrete permanently dry, which is what stops it cracking",
								"is_correct": False,
							},
							{
								"text": "The outside face is the negative side, and no system can be applied there",
								"is_correct": False,
							},
							{
								"text": "The mortar bed will only bond over a membrane, never over bare concrete",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A crew wants to drain an in-ground basin in ground that is known to be wet, to chase a repair. What is the hazard?",
						"type": "Single Choice",
						"explanation": (
							"Groundwater exerts uplift on the underside of the structure — about 62.4 pounds per "
							"square foot for every foot the water table stands above it. Full, the vessel's own "
							"water holds it down; empty, it can lift, tilt or crack, with people inside."
						),
						"options": [
							{
								"text": "Hydrostatic uplift — the empty vessel can float, tilt or crack",
								"is_correct": True,
							},
							{
								"text": "The membrane drying out and losing its bond to the concrete",
								"is_correct": False,
							},
							{
								"text": "Nothing structural; an empty basin is the safest state it can be in",
								"is_correct": False,
							},
							{"text": "The tile debonding as the mortar bed dries out", "is_correct": False},
						],
					},
					{
						"question": "Which of these are true about a movement joint in a waterproofed vessel?",
						"type": "Multiple Choice",
						"explanation": (
							"A joint exists so movement happens where it was designed to, the waterproofing crosses "
							"it with slack and a bond breaker rather than bonded film, and the joint must line up "
							"through the mortar bed and the finish too. Extra coats are the substitution that fails."
						),
						"options": [
							{
								"text": "The joint exists so that movement happens where somebody chose it to",
								"is_correct": True,
							},
							{
								"text": "The waterproofing crosses it with a specific detail that leaves slack, not bonded film",
								"is_correct": True,
							},
							{
								"text": "The joint has to be carried up through the mortar bed and the finish in the same line",
								"is_correct": True,
							},
							{
								"text": "Extra coats of membrane over the joint are an acceptable substitute for the detail",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A vessel under a static water test has lost measurable depth over a hot, dry, windy day. What does that tell you on its own?",
						"type": "Single Choice",
						"explanation": (
							"An open water surface evaporates, and in hot dry windy weather it loses real depth "
							"with nothing wrong. Evaporation has to be measured and taken off before a drop is "
							"called a leak, and a floating reference container is the usual way."
						),
						"options": [
							{
								"text": "Not enough on its own — evaporation has to be accounted for before it counts as a leak",
								"is_correct": True,
							},
							{
								"text": "That there is a leak, since a sealed vessel cannot lose water any other way",
								"is_correct": False,
							},
							{
								"text": "That the concrete is still absorbing water and the test should simply be repeated later",
								"is_correct": False,
							},
							{
								"text": "That the membrane has failed somewhere below the new water level",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
	],
}
