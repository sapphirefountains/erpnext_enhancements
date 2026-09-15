# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Module 4 — Materials of Water Features.

Rewritten against Sapphire's own document, *Module 4: Structural Masonry, Waterproofing, & Tile*.
That document spans two courses: the masonry, concrete, mortar, tile and coping material is used
here, and the membrane application belongs to Module 5. Where it states a figure, the figure is
Sapphire's and it replaces whatever general practice had put here — the 28-day minimum on a new
pour, the 1:4 muriatic dilution, 95% to 100% coverage behind a submerged tile, 1/16 inch across a
weir. Topics it does not reach (metals, galvanic corrosion, stainless fasteners) keep the content
they had, and keep the rule that went with it: no invented figure where a label owns the answer.
"""

from erpnext_enhancements.training.technician_program._common import ask_block, sourced_notice_block

COURSE = {
	"course": {
		"course_title": "Technician Module 4 — Materials of Water Features",
		"summary": (
			"Tell which masonry, stone, concrete and metal survive permanent immersion and which "
			"quietly do not, mix a submerged bed and set a tile bond to Sapphire's own figures, "
			"level a coping weir to 1/16 inch, take scale off a stone without destroying it, and "
			"set a stainless fastener without galling it."
		),
		"category": "Installation",
		"weight": "Required",
		"audience": "Internal Staff",
	},
	"lessons": [
		{
			"lesson_title": "Structural masonry, tile and stone",
			"estimated_minutes": 18,
			"summary": "Which units survive permanent water and freeze-thaw, the scale the water builds on them, and the ways a stone is ruined by the products meant to clean or protect it.",
			"blocks": [
				sourced_notice_block(),
				{
					"block_type": "Rich Text",
					"heading": "Immersion is not weather",
					"content": (
						"<p>A material in a wall gets wet and then dries. A material in a fountain "
						"<b>never dries</b>. Saturation is its normal condition, not an event it "
						"recovers from, and that one difference is why a unit with fifty good years "
						"in a garden wall can fail in a single season at a waterline.</p>"
						"<p>Two mechanisms do most of the damage:</p>"
						"<ul>"
						"<li><b>Freeze-thaw.</b> Water expands by about 9% when it freezes. A pore "
						"that is half full has somewhere to put that. A pore that is full does not, "
						"so the ice pushes the material apart from the inside. Saturated masonry and "
						"saturated stone are the two things a freeze is hardest on.</li>"
						"<li><b>Transport.</b> A porous material does not just hold water, it "
						"<i>moves</i> it, and everything dissolved in the water rides along — "
						"chlorides, sulphates, whatever the treatment regime puts in and whatever the "
						"fill water brought with it. The water evaporates at the face. The salts stay "
						"behind, in the pore, and crystallise there.</li>"
						"</ul>"
						"<p>The worst place on the whole feature is not the bottom of the basin, which "
						"at least stays at one condition. It is the <b>waterline and the splash "
						"zone</b> — wet, dry, wet, dry, freeze, thaw, and evaporating all day.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A masonry unit is rated, and the rating is the whole question",
					"content": (
						"<p>Brick is fired clay, and how hard it was fired decides how much water it "
						"takes up and how it behaves frozen. Units are graded for exposure, and a "
						"brick made for an interior or a protected veneer is a different product from "
						"one graded for severe weathering — it looks identical on a pallet.</p>"
						"<p>Concrete block is porous through its whole body and its cores are voids "
						"that fill. Manufactured units of every kind — pavers, cast stone, thin "
						"veneer — carry an absorption figure and a statement about freeze-thaw and "
						"about submerged service, and it is common for that statement to be "
						"<i>no</i>.</p>"
						"<p>You cannot tell any of this by looking, by tapping it, or by what the last "
						"job used. It is on the submittal, and a substitution is a decision somebody "
						"with the specification in front of them makes.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Natural stone is a variable, not a product",
					"content": (
						"<p>Stone is quarried, not manufactured. Two pieces off the same pallet differ "
						"in porosity, in veining, in how they run, and in strength. Treating a stone "
						"selection as a single known material is the first mistake.</p>"
						"<p><b>Absorption varies enormously.</b> Some granites are nearly closed. Some "
						"limestones and sandstones drink, and a slab that has been sitting in a basin "
						"for a week weighs noticeably more than the one you unloaded.</p>"
						"<p><b>Some stone is chemically vulnerable.</b> Limestone, travertine and "
						"marble are calcium carbonate, and carbonate dissolves in acid. Muriatic acid, "
						"most efflorescence removers, many scale removers and a good number of "
						"supermarket tile cleaners are acidic. Used on a carbonate stone they "
						"<b>etch</b> it — and an etch is not a stain sitting on the surface, it is "
						"stone that is gone. Nothing cleans it off.</p>"
						"<p><b>Some stone rusts by itself.</b> Iron-bearing minerals in the stone "
						"oxidise once the piece is kept wet, and the stain comes <i>through</i> the "
						"face from inside. There is no surface cleaning answer to that either.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Scale is the water handing the calcium back",
					"content": (
						"<p>When fountain water goes scale-forming — the balance drifts and the water "
						"can no longer hold what is dissolved in it — it <b>rejects calcium out of "
						"solution</b>. That calcium binds with carbon dioxide and forms <b>calcium "
						"carbonate</b>: a hard, white, crystalline crust that runs down water walls "
						"and cascades and shows worst of all on dark tile.</p>"
						"<p>Two things follow from where it came from. The crust is a <b>report on the "
						"chemistry</b>, so taking it off without correcting the balance buys you a "
						"clean wall and the same crust again. And the crust is alkaline, which is why "
						"acid is what removes it — the fizzing you see is the acid chemically breaking "
						"the carbonate shell down.</p>"
						"<p>It is <i>not</i> efflorescence, however similar the two look. "
						"Efflorescence arrives from behind, through the assembly. Scale arrives from "
						"the water, onto the face. Same colour, opposite direction, different "
						"fix.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Which acid goes on which surface",
					"content": (
						"<p>This is Sapphire's rule, and it decides whether a cleaning job ends with a "
						"clean surface or a ruined one.</p>"
						"<p><b>Granite, glass tile and dense concrete.</b> Diluted muriatic acid, "
						"<b>1 part acid to 4 parts water</b>, applied with a chemical-resistant pump "
						"sprayer or a thick fibre brush.</p>"
						"<p><b>Limestone, travertine and other porous natural stone: never muriatic "
						"acid.</b> The stone is carbonate too, so the acid does not stop politely at "
						"the scale — it eats aggressively into the stone underneath and permanently "
						"etches the finish somebody paid a great deal for. On those surfaces use a "
						"specialised <b>sulfamic or phosphoric acid gel</b>, which loosens the scale "
						"without dissolving the soft stone beneath it.</p>"
						"<p>Then dislodge the softened crust with a heavy masonry scraper or a stiff "
						"nylon brush, rinse the area thoroughly with clean water, and <b>capture the "
						"runoff with an industrial wet-vac</b> before it spills into the main basin. "
						"Acidic runoff reaching the pool throws the water chemistry out of balance, "
						"and you have traded a cosmetic problem for a chemical one.</p>"
						"<p>Acid goes into water, never water into acid, and full chemical PPE applies "
						"the moment the container is open.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "The sealer is often the thing that ruins the stone",
					"content": (
						"<p>A sealer reduces how much water goes in. On a fountain, water is also "
						"arriving from <b>behind</b> — through the bed, through the substrate, through "
						"the joints — and a sealer that slows water going in slows it coming out just "
						"as well.</p>"
						"<p>A film-forming sealer on a stone that is wet from the back traps the "
						"moisture under the film. You get clouding and blushing, the film lifts and "
						"peels in patches, salts crystallise under it, and a freeze works on a layer "
						"of trapped water. Some impregnators and nearly every colour enhancer darken "
						"the stone <b>permanently</b>, and applying one over a stone that has not "
						"fully dried locks that moisture in for good.</p>"
						"<p>Test on an offcut or a hidden area and let it dry before you judge it. "
						"Never let the finished face be the test panel, and never assume a sealer is "
						"approved for submerged service — most are not.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Five things that look like dirt and are not",
					"panels": [
						{
							"title": "Efflorescence",
							"body": "A white salt deposit at the face. Water moved through the assembly, dissolved salts on the way, reached the surface and evaporated, leaving them behind. It is a <b>symptom of water movement</b>, not a cleanliness problem — scrub it off and it returns until the water path changes.",
						},
						{
							"title": "Calcium carbonate scale",
							"body": "A hard white crystalline crust down a water wall, a cascade or a run of dark tile. It came out of the <b>water</b>, not out of the assembly: scale-forming water rejecting calcium, which binds with carbon dioxide and sets on the face. That is why it answers to acid, and why it comes straight back if the chemistry is not corrected.",
						},
						{
							"title": "Spalling",
							"body": "A flake or a crater where the face has come away. Freeze-thaw does it, and so does salt crystallising inside the pore rather than at the surface. Both are pressure generated <i>within</i> the material, which is why it comes off in sheets rather than wearing down evenly.",
						},
						{
							"title": "Rust staining",
							"body": "Orange-brown bleeding out of the stone or running down from a fixing. Either iron minerals in the stone itself oxidising, or a carbon steel anchor, tie, tool or grinding dust that should never have touched it. Where it came from decides whether there is anything to do about it.",
						},
						{
							"title": "Etching",
							"body": "A dull, lighter patch on polished limestone, travertine or marble, often exactly the shape of a splash or a wipe. Acid dissolved the carbonate. The material is missing, so cleaning does nothing — it is refinished or it is replaced.",
						},
					],
				},
				ask_block(
					"Which unit, which stone, which sealer",
					"<p>Absorption limits, freeze-thaw grades and whether a given stone or "
					"manufactured unit is approved for <b>continuous immersion</b> are product and "
					"project answers. They live on the submittal, the stone supplier's own data, and "
					"the manufacturer's written statement.</p>"
					"<p>Sapphire's document settles the cleaning question for the surfaces it names — "
					"diluted muriatic on granite, glass tile and dense concrete, a sulfamic or "
					"phosphoric gel on porous stone. What no document can settle for you is "
					"<b>which stone you are standing in front of</b>. A pale slab is limestone or it "
					"is a dense manufactured unit, the two take opposite treatments, and the wrong "
					"guess is not reversible. If nobody can say which it is, that is a "
					"stop-and-ask.</p>"
					"<p>Sealers stay open. Most are not approved for submerged service, the only "
					"proof is the manufacturer's written statement, and the test panel is an offcut — "
					"never the finished face. A cleaner or a sealer is the cheapest thing on the job "
					"and the fastest way to write off the most expensive.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Why is a fountain harder on a masonry unit than an exposed garden wall?",
						"type": "Single Choice",
						"explanation": (
							"A wall wets and dries; a fountain unit stays saturated. A full pore has nowhere to put "
							"the roughly 9% expansion when the water in it freezes."
						),
						"options": [
							{
								"text": "It stays saturated, so its pores are full when a freeze arrives",
								"is_correct": True,
							},
							{
								"text": "Fountain water is warmer, and heat degrades fired clay",
								"is_correct": False,
							},
							{
								"text": "Moving water abrades the face faster than wind-driven rain",
								"is_correct": False,
							},
							{
								"text": "It is not harder; immersion protects a unit from weather",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Calcium scale has built up on a travertine coping. Which product goes on it?",
						"type": "Single Choice",
						"explanation": (
							"Travertine is calcium carbonate itself, so muriatic acid does not stop at the scale — it "
							"eats into the stone underneath and etches the finish permanently, and an etch is missing "
							"material that no cleaning brings back. Sapphire's rule for porous natural stone is a "
							"specialised sulfamic or phosphoric acid gel, which loosens the crust without dissolving "
							"what is under it."
						),
						"options": [
							{
								"text": "A specialised sulfamic or phosphoric acid gel",
								"is_correct": True,
							},
							{
								"text": "Muriatic acid diluted 1 part acid to 4 parts water",
								"is_correct": False,
							},
							{
								"text": "Muriatic acid at full strength, rinsed off quickly",
								"is_correct": False,
							},
							{
								"text": "Any acid, as long as the surface is neutralised afterwards",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true of taking calcium scale off a granite water wall?",
						"type": "Multiple Choice",
						"explanation": (
							"Sapphire's dilution for granite, glass tile and dense concrete is 1 part muriatic acid to "
							"4 parts water. The fizzing is the acid breaking down the alkaline carbonate shell, and the "
							"runoff is captured with a wet-vac so it never reaches the basin and unbalances the water. "
							"The same solution on a limestone coping would etch it."
						),
						"options": [
							{
								"text": "The muriatic acid is diluted 1 part acid to 4 parts water",
								"is_correct": True,
							},
							{
								"text": "It will fizz, which is the acid breaking the alkaline crust down",
								"is_correct": True,
							},
							{
								"text": "The acidic runoff is wet-vacced up before it can reach the basin",
								"is_correct": True,
							},
							{
								"text": "The same solution can go straight onto a limestone coping above it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Calcium scale on a water wall and efflorescence at a masonry joint are the same deposit arriving the same way.",
						"type": "True-False",
						"explanation": (
							"Both are white and neither is dirt, but scale precipitates out of scale-forming water onto "
							"the face, while efflorescence is salt carried through the assembly from behind and left at "
							"the surface when the water evaporates. Same colour, opposite direction, different fix."
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
			"lesson_title": "Basics of concrete and rebar",
			"estimated_minutes": 18,
			"summary": "What the steel is actually doing, why cover decides whether it lasts, Sapphire's 28-day floor on a new pour, and the fact that a concrete basin does not hold water by itself.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Strong squeezed, weak pulled",
					"content": (
						"<p>Concrete carries compression extremely well and tension very badly — its "
						"tensile strength is a small fraction of its compressive strength, on the "
						"order of a tenth. So nobody designs concrete to be pulled. Where a section "
						"will be pulled, steel goes there.</p>"
						"<p>Every structure in this trade has a tension side somewhere, and which side "
						"it is does not follow from where the water is. A basin wall cantilevered off "
						"its footing bends outward under the water behind it, and the tension at the "
						"base is on the <b>wet</b> face, which is where the steel goes. A slab "
						"spanning between supports is in tension along the bottom, and over a support "
						"it is in tension along the <b>top</b>. A cantilevered edge is in tension on "
						"top all the way out.</p>"
						"<p>That is why rebar is not distributed evenly through a section like a "
						"filler. It is placed where the tension is. <b>Where the bar sits is the "
						"design</b>, not a detail of it — a bar in the wrong half of a slab is a bar "
						"that is not in the structure.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Cover is not a gap, it is the protection",
					"content": (
						"<p>Cover is the thickness of concrete between the steel and the surface, and "
						"it does two jobs at once. Concrete is strongly alkaline, and steel sitting in "
						"that alkalinity forms a passive film and does not rust. Cover also keeps "
						"water and chlorides from reaching the bar in the first place.</p>"
						"<p>Lose the cover and both jobs fail together. The bar rusts, and <b>rust "
						"occupies several times the volume of the steel it came from</b>. That "
						"expansion has to go somewhere, and it goes outward, cracking the concrete off "
						"the bar from the inside. The sequence on a wall is always the same: a rust "
						"stain, then a hairline crack following the line of a bar, then a flake, then "
						"a piece of the face on the floor with the bar visible behind it.</p>"
						"<p>This is a fountain problem more than a building problem, because the water "
						"is the delivery system. Treated water carries chlorides, and chlorides are "
						"what break the passive film on steel. That is the entire reason bars sit on "
						"chairs and spacers and get tied off the form, and the reason a bar that has "
						"been walked down or pulled to the face during a pour is not a cosmetic issue "
						"to be patched over.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Cutting or moving a bar is the engineer's decision, not yours",
					"content": (
						"<p>A sleeve does not line up, a drain lands on a bar, a core comes out in the "
						"wrong place. The tempting fix is to cut the bar, bend it aside, or core "
						"straight through it.</p>"
						"<p><b>Nobody in the field knows what that bar is carrying.</b> It may be "
						"crack control, or it may be the tension steel holding a cantilever up. The "
						"consequence of getting it wrong is not a leak, it is a collapse, and it can "
						"arrive years later under a load nobody was thinking about.</p>"
						"<p>Cutting, bending, relocating, drilling through, or coring a reinforced "
						"section is a question for the engineer of record. So is discovering after the "
						"fact that somebody already did it — that gets reported, not buried under the "
						"patch.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Why bars overlap instead of meeting",
					"content": (
						"<p>Bar comes in lengths, so a long run has joints in it. Two bars that simply "
						"butt together transfer nothing. Force moves from one bar into the concrete "
						"around it and back out into the next bar, and that takes <b>length</b> — the "
						"bars run past each other far enough for the bond between steel and concrete "
						"to carry the load across. That overlap is a lap splice.</p>"
						"<p>How long the lap has to be depends on the bar size, the concrete strength, "
						"whether the bar is coated, how far it is from its neighbours and the surface, "
						"and whether the bar is in tension there. So it is a figure off the drawing "
						"and never a habit carried from the last job.</p>"
						"<p><b>Where</b> the laps fall matters as much. The drawing staggers them and "
						"keeps them away from the points of highest stress, so moving a lap to suit a "
						"convenient bar length moves the design. And a splice only works if the "
						"concrete is around it: two bars laid tight against the form, or a lap sitting "
						"in a honeycombed void, transfer nothing at all.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Curing is keeping water in, not letting it dry out",
					"content": (
						"<p>This is the single most misunderstood thing about concrete. Cement does "
						"not dry, it <b>hydrates</b> — a chemical reaction with water that keeps "
						"producing strength for as long as there is water available to react. Take the "
						"water away and the reaction stops.</p>"
						"<p>And it does not simply pick up where it left off. Re-wetting can restart "
						"the reaction, but by then the pore structure has formed around the strength "
						"that was never gained, and concrete dried early does not <i>catch back "
						"up</i> — treat what it lost as gone. Curing is therefore an active job: "
						"keeping the water in, by wet cure, by covering, or by a curing compound, for "
						"as long as the specification says.</p>"
						"<p>The surface is what dries first, and the surface is what you will be "
						"looking at forever. A slab cured badly can test acceptably through its body "
						"and still dust, craze, scale and wear through at the top, because the skin "
						"never finished hydrating. Sun, wind and low humidity all pull water out "
						"faster, and wind is the one people underestimate.</p>"
						"<p><b>Sapphire's floor for a new pour is a minimum of 28 days.</b> Until "
						"that has passed the shell is not treated as fully cured, and nothing that "
						"has to bond to it — waterproofing, a mortar bed, a tile assembly — goes on "
						"top. That is a floor and not a target: where the product data sheet or the "
						"specification asks for longer, longer wins.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What has to come off before anything goes on",
					"content": (
						"<p>A cured shell is not automatically a surface anything will bond to. "
						"Sapphire's document is specific about what has to be gone first: "
						"<b>laitance</b> — the weak, dusty layer of fines that rises to the top of a "
						"pour — along with form-release agents, curing compounds, dirt and dust. The "
						"concrete also has to be structurally sound, which is a separate question "
						"from whether it is clean.</p>"
						"<p>There is a trap in that list. The <b>curing compound</b> is the product "
						"that protected the slab while it cured, and it is also the film that will "
						"stop the next layer sticking. Doing the right thing at the pour leaves you "
						"something to remove later, and nobody removes it by accident.</p>"
						"<p><b>Structural cracks are repaired, not covered.</b> They are chased out, "
						"filled with an engineering-grade waterproof hydraulic cement, and allowed to "
						"cure before anything goes over the top. A crack bridged by a coating is a "
						"crack that will keep moving under the coating and split it — and the coating "
						"is what everyone will blame.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Concrete passes water, and a cold joint passes more",
					"content": (
						"<p>A <b>cold joint</b> is where fresh concrete was placed against concrete "
						"that had already stiffened too far to blend with it. The two do not become "
						"one piece; there is a plane in there, and a plane is a path. Planned "
						"construction joints exist and are fine — they are detailed with keyways, "
						"waterstops and continuous reinforcement. A cold joint is the unplanned "
						"version, produced by a pour that stalled, and it has none of that.</p>"
						"<p>The larger point: <b>even flawless concrete is porous.</b> It passes "
						"water. A basin does not hold water because the wall is thick — it holds water "
						"because it is <b>waterproofed</b>, with a membrane, a coating or a liner. The "
						"concrete is the structure and the waterproofing is the barrier, and they are "
						"two different things doing two different jobs.</p>"
						"<p>Which is why a basin can lose water steadily through a wall with no visible "
						"crack in it, and why anything that penetrates or damages the waterproofing "
						"matters even where the concrete behind it is perfectly sound.</p>"
					),
				},
				ask_block(
					"The mix, the cover, the lap and the cure are all specified",
					"<p>Concrete strength and mix design, admixtures, the required cover to each face, "
					"bar sizes and spacings, lap lengths and their locations, the curing method and "
					"duration, and every waterproofing detail come from the structural drawings, the "
					"specification and the product data — not from this course and not from the last "
					"job.</p>"
					"<p>Where something on site will not fit what is drawn, the answer is a question "
					"before the pour. After the pour it becomes an engineering investigation and a "
					"demolition estimate.</p>"
					"<p>Sapphire's 28-day minimum on a new pour sits under all of that as a floor, "
					"not as a substitute for it. It says when the concrete stops being the thing you "
					"are waiting on. It does not say what goes on top of it, or how thick, or "
					"how.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What is rebar in a basin wall actually doing?",
						"type": "Single Choice",
						"explanation": (
							"Concrete is weak in tension and strong in compression. The steel is placed where the "
							"section is being pulled, and it controls cracking there."
						),
						"options": [
							{
								"text": "Carrying the tension the concrete cannot, and controlling cracking there",
								"is_correct": True,
							},
							{
								"text": "Spreading the compressive load evenly through the section",
								"is_correct": False,
							},
							{
								"text": "Making the concrete waterproof by tying it together",
								"is_correct": False,
							},
							{"text": "Holding the formwork in position during the pour", "is_correct": False},
						],
					},
					{
						"question": "A bar was walked down during the pour and ended up close to the face. Why does the wall later flake off along that line?",
						"type": "Single Choice",
						"explanation": (
							"With cover lost, water and chlorides reach the steel and it rusts. Rust takes up several "
							"times the volume of the steel, and that expansion breaks the cover off from inside."
						),
						"options": [
							{
								"text": "The bar rusts, and rust expands enough to push the cover off from inside",
								"is_correct": True,
							},
							{
								"text": "The bar conducts cold and the concrete freezes against it first",
								"is_correct": False,
							},
							{
								"text": "The bar vibrates under water flow and works itself loose",
								"is_correct": False,
							},
							{
								"text": "The concrete cured faster near the bar and shrank away from it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these are true about curing concrete?",
						"type": "Multiple Choice",
						"explanation": (
							"Cement hydrates rather than dries, so curing means retaining water. Strength not gained "
							"before it dried is not made back up later, and sun, wind and dry air all accelerate the "
							"loss. Sapphire's floor for a new pour is a minimum of 28 days before the shell counts as "
							"cured at all."
						),
						"options": [
							{
								"text": "It means keeping water in the concrete so hydration continues",
								"is_correct": True,
							},
							{
								"text": "Strength lost to early drying is not recovered later",
								"is_correct": True,
							},
							{
								"text": "Wind and low humidity make it harder, not just heat",
								"is_correct": True,
							},
							{
								"text": "A new pour is not treated as cured until a minimum of 28 days have passed",
								"is_correct": True,
							},
							{
								"text": "It is finished as soon as the surface is hard enough to walk on",
								"is_correct": False,
							},
							{
								"text": "It means waiting for the concrete to dry out completely",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A concrete basin holds water because well-made concrete is watertight.",
						"type": "True-False",
						"explanation": (
							"Concrete is porous and passes water even when it is sound. The basin holds water because a "
							"membrane, coating or liner was installed to do that job."
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
			"lesson_title": "Metals",
			"estimated_minutes": 16,
			"summary": "Why treated water turns two metals into a battery, and why stainless still pits, crevices and rusts.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Treated water is an electrolyte, so the system is a battery",
					"content": (
						"<p>Galvanic corrosion needs exactly three things, all at once:</p>"
						"<ol>"
						"<li><b>Two different metals.</b></li>"
						"<li><b>An electrical connection between them</b> — bolted, threaded, welded, "
						"or through any other metal path.</li>"
						"<li><b>An electrolyte bridging them</b> — a liquid that conducts.</li>"
						"</ol>"
						"<p>That is a cell. Current flows, and the less noble of the two metals "
						"corrodes to protect the other. It is not a chemical attack on the surface; "
						"the metal is being consumed as part of a circuit.</p>"
						"<p>Fountain water is an excellent electrolyte, and everything done to it "
						"makes it better at the job — sanitiser, salt, scale and pH chemicals, and the "
						"dissolved solids that concentrate as water evaporates and gets topped up. A "
						"pair of metals that coexist happily in a dry building can be a live cell in a "
						"basin.</p>"
						"<p>Break any one of the three and it stops. Design breaks the electrolyte "
						"path with coatings and gaskets, or breaks the electrical path with dielectric "
						"unions, isolation bushings and non-metallic washers. Note also the "
						"<b>area effect</b>: a small piece of the less noble metal attached to a large "
						"area of the more noble one is eaten quickly. A carbon steel bolt in a "
						"stainless frame is a bad idea for that reason; a stainless bolt in a carbon "
						"steel frame is far less of one.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Never solve a corrosion problem by cutting a bond",
					"content": (
						"<p>Metal around water is deliberately tied together. NEC Article 680 requires "
						"equipotential bonding around pools and fountains, and the reason is human: if "
						"every metal part a person can touch is at the same potential, a fault does not "
						"find a voltage difference to push current through the person standing in the "
						"water.</p>"
						"<p>That bonding is also, unavoidably, an electrical connection between "
						"dissimilar metals. Somebody eventually notices corrosion and proposes to "
						"isolate the parts by removing a bonding conductor. <b>The answer is no.</b> "
						"Galvanic isolation is a materials and design decision made with the bonding "
						"intact; it is never achieved by disconnecting the thing that keeps people "
						"alive.</p>"
						"<p>A bonded assembly that is corroding, or a system showing stray current, is "
						"a question for the engineer and a qualified electrician. It is not a field "
						"correction, and nothing in this course qualifies anybody to make one.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "What stainless actually is, and what the word does not promise",
					"content": (
						"<p>Stainless steel is iron alloyed with enough chromium — conventionally at "
						"least about 10.5% — that a very thin chromium-oxide film forms on the "
						"surface. That <b>passive film</b> is the entire corrosion resistance. It is "
						"invisible, it is a few atoms thick, and it re-forms on its own when it is "
						"scratched, provided oxygen can reach the surface.</p>"
						"<p>The 300-series is the austenitic family, and it is what most fountain "
						"hardware is made from. 304 is the common general-purpose grade. 316 adds "
						"molybdenum, which substantially improves resistance to chlorides, which is "
						"why it turns up on marine, pool and fountain work.</p>"
						"<p>Both of them are stainless. <b>Neither of them is corrosion-proof.</b> The "
						"name describes a mechanism, not a guarantee, and the mechanism has two "
						"well-known ways of being defeated.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Chlorides pit it, and it starts where the water sits still",
					"content": (
						"<p><b>Pitting.</b> A chloride ion breaks the passive film at one small spot. "
						"Inside the resulting pit the water goes stagnant and oxygen-starved, so the "
						"film cannot re-form there, and the chemistry inside the pit becomes more "
						"aggressive than the water outside it. The attack drives downward, fast, "
						"through a surface that still looks polished a millimetre away. A pinhole leak "
						"in a bright, clean-looking stainless part is the classic presentation.</p>"
						"<p><b>Crevice corrosion.</b> The same process, but it does not need a chloride "
						"to find a weak spot first — a crevice supplies the stagnant, oxygen-starved "
						"condition for free. Under a washer, under a gasket, inside a threaded joint, "
						"under a scale deposit, under a leaf, in a dead leg, behind a fitting flange.</p>"
						"<p>Two consequences worth carrying around. A feature shut down while still "
						"full of treated water is <b>harder</b> on stainless than one that is running, "
						"because circulating water is oxygenated and moving. And a deposit on a "
						"stainless surface is not cosmetic — it is a crevice being built.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "When stainless rusts, the rust is usually not the stainless",
					"content": (
						"<p>A stainless handrail streaked with orange almost never means the alloy "
						"failed. It means <b>free iron</b> got onto the surface during fabrication or "
						"installation and is rusting there.</p>"
						"<p>The sources are all ordinary: a grinding wheel or flap disc previously used "
						"on carbon steel, a carbon steel wire brush, cutting or laying the part on a "
						"steel table, sparks from steel work landing on it, mild steel clamps, even "
						"steel wool. Those particles embed in the soft austenitic surface, and then "
						"they do what steel does in water.</p>"
						"<p>This is not only ugly. The rust holds moisture against the surface and "
						"shields it from oxygen, which is exactly the condition the passive film "
						"cannot survive — so a contamination stain grows into a genuine pit "
						"underneath. That is why stainless work gets <b>dedicated tooling</b>: "
						"brushes, wheels, files and blast media used on stainless and on nothing "
						"else.</p>"
						"<p><b>Passivation</b> is the cure and the preventative: a chemical treatment "
						"that dissolves free iron off the surface and leaves the chromium oxide film "
						"to re-form clean. Pickling is more aggressive and removes some of the metal "
						"as well, including heat tint at welds. Neither is the same as polishing — "
						"mechanical cleaning can make a contaminated part look perfect and leave every "
						"embedded particle in place.</p>"
					),
				},
				{
					"block_type": "Flashcards",
					"heading": "The vocabulary this lesson runs on",
					"cards": [
						{
							"front": "Electrolyte",
							"back": "A liquid that conducts current. Fountain water qualifies, and treating it makes it better at it.",
						},
						{
							"front": "Galvanic couple",
							"back": "Two different metals electrically connected in an electrolyte. The less noble one corrodes to protect the other.",
						},
						{
							"front": "Area effect",
							"back": "A small less-noble part attached to a large more-noble one corrodes fast. Carbon steel bolt in a stainless frame: bad. Stainless bolt in a carbon steel frame: far less bad.",
						},
						{
							"front": "Passive film",
							"back": "The invisible chromium-oxide layer that makes stainless stainless. Self-healing where oxygen can reach it, defenceless where it cannot.",
						},
						{
							"front": "Pitting",
							"back": "Chloride attack that breaks the film at a point and drives downward into a surface that still looks clean around it.",
						},
						{
							"front": "Crevice corrosion",
							"back": "The same attack starting in a gap — under a washer, a gasket, a deposit or a thread — where the water is stagnant and oxygen-starved.",
						},
						{
							"front": "Free iron",
							"back": "Carbon steel particles left on a stainless surface by tooling or sparks. They rust, and the rust starts a real pit underneath.",
						},
						{
							"front": "Passivation",
							"back": "A chemical treatment that removes free iron and lets the passive film re-form. Not the same as polishing it until it looks clean.",
						},
					],
				},
				ask_block(
					"Which alloy, which isolation detail, which treatment",
					"<p>Whether a part is 304, 316, a duplex grade, bronze or a coated carbon steel; "
					"whether it is passivated or pickled after fabrication; which dielectric or "
					"isolation detail is used where two metals meet; and whether a given water "
					"chemistry is compatible with the metals already in the system — all of that is "
					"engineered and specified.</p>"
					"<p>Substituting an alloy because the right one is not on the truck is a decision "
					"with a several-year fuse on it. So is changing a sanitiser or raising a salt "
					"level without asking what the system is made of.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Which conditions have to be present together for a galvanic cell to run?",
						"type": "Multiple Choice",
						"explanation": (
							"Two dissimilar metals, an electrical path between them, and an electrolyte bridging them. "
							"Remove any one and the cell stops. Surface finish is not one of the three."
						),
						"options": [
							{"text": "Two metals that are different from each other", "is_correct": True},
							{"text": "An electrical connection between them", "is_correct": True},
							{"text": "An electrolyte in contact with both", "is_correct": True},
							{
								"text": "A difference in surface finish between the two parts",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A stainless bracket rusts in streaks a few weeks after it was cut and ground on site. What is most likely rusting?",
						"type": "Single Choice",
						"explanation": (
							"Carbon steel particles from shared tooling embed in the stainless surface and rust there. "
							"The rust then traps moisture and shields the surface, which can start a real pit underneath."
						),
						"options": [
							{
								"text": "Free iron left on the surface by tooling used on carbon steel",
								"is_correct": True,
							},
							{
								"text": "The alloy itself, meaning the wrong grade was supplied",
								"is_correct": False,
							},
							{"text": "The chromium in the alloy, which oxidises orange", "is_correct": False},
							{
								"text": "Nothing real — it is a water stain that will wash off",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Where does corrosion on a stainless part in treated water typically start?",
						"type": "Single Choice",
						"explanation": (
							"In crevices and under deposits, where water is stagnant and oxygen-starved. The passive "
							"film needs oxygen to re-form, and a crevice denies it."
						),
						"options": [
							{
								"text": "Under gaskets, washers, threads and deposits, where water sits still",
								"is_correct": True,
							},
							{
								"text": "On the most exposed, fastest-flowing, best-oxygenated surfaces",
								"is_correct": False,
							},
							{"text": "Evenly across the whole wetted surface at once", "is_correct": False},
							{"text": "Only at welds, and nowhere else", "is_correct": False},
						],
					},
					{
						"question": "Corrosion between two bonded metal parts around a fountain is a good reason to remove the bonding conductor between them.",
						"type": "True-False",
						"explanation": (
							"The bond is there so a person in the water never becomes the path between two different "
							"potentials. Galvanic isolation is a design decision, never achieved by cutting a bond."
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
			"lesson_title": "Grout, mortar and mud bed mechanics",
			"estimated_minutes": 18,
			"summary": "Sapphire's zero-slump snowball test, why every submerged mix is polymer-modified, how pitch gets built into a surface, and why a bed that cannot drain becomes a reservoir.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Four materials, and people call them all mud",
					"content": (
						"<p><b>Deck mud</b>, or dry pack, is sand and cement with very little "
						"water — <b>zero slump</b>, in Sapphire's words, and not a drop wetter. It "
						"has almost no bonding strength of its own. It works by being <b>compacted "
						"into a dense mass</b> and confined, the way a compacted road base "
						"works.</p>"
						"<p><b>Fat mud</b>, or wall mud, has lime in it, which makes it sticky "
						"enough to stay on a vertical surface. <b>Mortar</b> sets masonry units and "
						"is proportioned for bond and workability. <b>Grout</b> fills the joints "
						"between tiles once they are already stuck down — it is a filler, not an "
						"adhesive, and it is not a waterproof layer either.</p>"
						"<p>They are not interchangeable, and the commonest single error on a bed is "
						"adding water to make it easier to trowel. A wet mix slumps, will not hold "
						"the plane you screeded, shrinks as it dries, and cures weak. The mix being "
						"unpleasantly dry is the point of it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Tip",
					"heading": "Zero slump, and the test for it",
					"content": (
						"<p>Sapphire's document is exact about this, so the test is not a matter of "
						"feel developed over years. Squeeze a handful of the mix in a gloved hand. It "
						"should <b>clump together like a snowball</b>. If it crumbles apart it is too "
						"dry. If it <b>stains your glove with water</b> it is too wet.</p>"
						"<p>It is worth doing on every batch because the penalty for getting it wrong "
						"is invisible on the day. Extra water makes the mix pleasant to work and it "
						"<b>destroys the compressive strength of the cured product and dramatically "
						"increases its porosity</b> — a bed weaker and thirstier than the one that "
						"was designed, on an assembly that is going to live under water.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Polymer-modified, and rated for continuous submersion",
					"content": (
						"<p>Sapphire's rule has no exceptions written into it: <b>always use "
						"polymer-modified mortars, thinsets and grouts specifically rated for "
						"continuous submersion or pool use</b>. Not where the specification happens "
						"to call for it — always.</p>"
						"<p>The mechanism is the reason. Cement cures with a network of micro-pores "
						"running through it, and a plain cementitious bed lets water travel that "
						"network. The integrated polymers <b>seal the micro-pores</b>, so water "
						"cannot work its way through the bed and break down the structural bond over "
						"time. A bed that is holding perfectly today and has quietly let go a few "
						"years from now is usually this.</p>"
						"<p>So a product that is excellent on an interior floor is not a candidate "
						"here, however good it is. The words to look for on the bag are "
						"<i>continuous submersion</i> or <i>pool</i>. If they are not on it, it is "
						"the wrong bag.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Info",
					"heading": "What the bed is actually gripping",
					"content": (
						"<p>A waterproofed shell is smooth, and a smooth membrane <b>repels</b> "
						"thinset and mortar — the bed sits on it rather than bonding to it, and under "
						"water pressure it delaminates. That is why the second membrane coat is "
						"broadcast with oven-dried silica sand while it is still wet and tacky: the "
						"embedded sand turns the surface into a <b>mechanical keyway</b>, a "
						"sandpaper texture the mud bed and the stone mortar can grip permanently.</p>"
						"<p>Two things there are yours even though the membrane itself belongs to the "
						"waterproofing module. <b>Sweep off all the loose, unbonded sand</b> before "
						"you lay anything — sand that never keyed into the membrane is a layer of "
						"ball bearings between your bed and the wall. And if you meet a membrane with "
						"no broadcast on it at all, stop and ask, because you are about to bond a bed "
						"to a surface designed to shed water and nothing will look wrong until the "
						"basin is full.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A dry-pack bed is how pitch gets built into a surface",
					"content": (
						"<p>A flat slab does not drain, and no finish laid parallel to it will either. "
						"Fall comes from the structure or it comes from the bed, and on most of this "
						"work it comes from the bed — which is the real reason deck mud exists. A "
						"bed that varies in thickness lets you screed a true plane at whatever fall "
						"the drawing asks, off a substrate that is whatever it is.</p>"
						"<p>The sequence is always the same: establish the finished heights you have "
						"to hit — the drain, the perimeter, any fixed feature — then set screed points "
						"or rails to those heights, fill between them, compact, screed off the rails "
						"with a straightedge, then compact and refloat the surface you cut.</p>"
						"<p>Then <b>check it as a plane, not as two heights</b>. A bed can be exactly "
						"right at the perimeter and exactly right at the drain and still have a belly "
						"in the middle. Lay a straightedge across it in several directions. Every low "
						"spot you leave is a puddle somebody will be looking at for the life of the "
						"feature, and a puddle in a wet finish is where the staining, the biofilm and "
						"the freeze damage start.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A bed that cannot drain is a reservoir under the tile",
					"content": (
						"<p>Water gets past tile. Grout is porous, tile joints are not a seal, and in "
						"an immersed or permanently wet assembly the water arrives at the mortar bed "
						"and soaks into it. That is expected — it is why the waterproofing membrane is "
						"under the bed and not under the tile.</p>"
						"<p>What is not expected is the water having nowhere to go. A bed sitting in a "
						"tray with no outlet stays saturated: efflorescence bleeds through the joints, "
						"the bond between bed and tile breaks down, and the first freeze works on a "
						"sponge full of water.</p>"
						"<p>So the assembly drains <b>twice</b>. The membrane below the bed slopes to "
						"the drain as well as the finished surface above it, and the weep openings at "
						"the drain must be open and stay open. Weeps packed solid with mortar during "
						"the pour is one of the most common failures in this trade, and it is "
						"invisible the moment the tile goes down.</p>"
					),
				},
				{
					"block_type": "Accordion",
					"heading": "Bonded and unbonded beds are two different structures",
					"panels": [
						{
							"title": "A bonded bed",
							"body": "Placed onto a prepared substrate with a bond coat, so bed and substrate act as one. It can be thinner, because the slab underneath is carrying it. The trade-off is that it inherits everything the substrate does — a crack that opens in the slab travels straight up through the bed and into the tile.",
						},
						{
							"title": "An unbonded bed",
							"body": "Placed over a cleavage membrane or slip sheet, usually with reinforcement in it, so it <b>floats</b> and is not tied to what is underneath. It isolates the finish from substrate movement and cracking. The price is thickness: it has to work as a slab in its own right, so it cannot be thinned down.",
						},
						{
							"title": "Why it is not a field choice",
							"body": "The membrane, the reinforcement and the thickness are one decision, made in the assembly detail. A thin bed built unbonded is neither thing, and a bonded bed poured over a slip sheet is just an unbonded bed that is too thin. If what is drawn does not suit what is on site, that is a question upward before the mud goes in.",
						},
					],
				},
				{
					"block_type": "Rich Text",
					"heading": "Compaction is where the strength comes from",
					"content": (
						"<p>A dry-pack bed gets its strength from <b>density</b>. Under-compacted mud "
						"is crumbly at the edges, sounds hollow, and crushes locally later under "
						"anything concentrated — a coping stone, a bench leg, a ladder foot, a scaffold "
						"pad.</p>"
						"<p>Compact the same way across the whole bed, not harder near the rails where "
						"it is easy. Inconsistent compaction gives you a perfect plane on the day and "
						"a dished area a year later, and by then the tile on top is telling the story "
						"for you.</p>"
						"<p>Working time is the other discipline. Once cement has begun to "
						"hydrate, adding water to loosen a stiffening mix does not restore it — it "
						"destroys it, permanently, in a way that will not show until the bed is "
						"loaded. Sapphire writes the prohibition against the mortar that has skinned "
						"on a wall — <b>never re-temper it with extra water</b> — and a mud bed that "
						"has started to go off is the same act on the same cement. Throw it out and "
						"mix again.</p>"
						"<p>Which reduces to one habit. Mix what you can place, and place what "
						"you mixed.</p>"
					),
				},
				ask_block(
					"The mix, the thickness, the fall and the additives",
					"<p>Proportions, minimum and maximum bed thickness, whether reinforcement is "
					"required, which membrane and which bond coat, and which particular product "
					"answers all of those come from the data sheets and from the assembly method the "
					"drawing names.</p>"
					"<p>One item that used to sit on that list is <b>no longer open</b>. Sapphire's "
					"document settles whether the mix is polymer-modified: it always is, and it is "
					"always a product rated for continuous submersion or pool use. What the data "
					"sheet still tells you is which one, how it is mixed, and how long it has before "
					"it skins.</p>"
					"<p>The required fall comes from the drawing too. A pitch remembered from the "
					"last job is a pitch you are about to tile over.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "How do you know a submerged dry-pack mix has the right amount of water in it?",
						"type": "Single Choice",
						"explanation": (
							"Sapphire's test is the snowball: squeezed in a gloved hand the mix clumps together and "
							"holds. Crumbling means too dry; water staining the glove means too wet. The mix is zero "
							"slump, and the extra water that makes it pleasant to trowel destroys its compressive "
							"strength and dramatically increases its porosity."
						),
						"options": [
							{
								"text": "Squeezed in a gloved hand it clumps like a snowball — neither crumbling apart nor staining the glove with water",
								"is_correct": True,
							},
							{
								"text": "It slumps into a smooth pat when a handful is dropped on the board",
								"is_correct": False,
							},
							{
								"text": "It flows off a trowel held at an angle without being pushed",
								"is_correct": False,
							},
							{
								"text": "It holds a wet sheen on the surface after it is screeded",
								"is_correct": False,
							},
						],
					},
					{
						"question": "The weep openings at a drain get packed with mortar during the pour. What follows?",
						"type": "Single Choice",
						"explanation": (
							"Water that gets past the tile soaks into the bed and now cannot leave it. The bed stays "
							"saturated, which brings efflorescence, bond failure and freeze damage."
						),
						"options": [
							{
								"text": "The bed stays saturated, and efflorescence, bond failure and freeze damage follow",
								"is_correct": True,
							},
							{
								"text": "Nothing, because the grout and tile above keep water out of the bed",
								"is_correct": False,
							},
							{
								"text": "The drain runs slower but the assembly is unaffected",
								"is_correct": False,
							},
							{
								"text": "The bed cures stronger because moisture is retained in it",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these describe an unbonded mortar bed?",
						"type": "Multiple Choice",
						"explanation": (
							"It floats on a cleavage membrane so substrate cracking does not travel into the tile, and "
							"because it has to act as a slab in its own right it needs thickness — it cannot be thinned."
						),
						"options": [
							{
								"text": "It sits on a cleavage membrane or slip sheet rather than bonding to the substrate",
								"is_correct": True,
							},
							{
								"text": "It isolates the finish from cracking and movement in the substrate",
								"is_correct": True,
							},
							{
								"text": "It needs more thickness than a bonded bed, not less",
								"is_correct": True,
							},
							{
								"text": "It is the thinner of the two options, which is why it is used in shallow build-ups",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A mortar or grout that performs well on an interior floor is fine in a submerged basin, provided it is mixed correctly.",
						"type": "True-False",
						"explanation": (
							"Sapphire requires polymer-modified mortars, thinsets and grouts specifically rated for "
							"continuous submersion or pool use. The polymers seal the micro-pores in the cement so "
							"water cannot travel through the bed and break the bond down over time. It still does not "
							"make grout the waterproof layer — the membrane under the bed is doing that, and the bed is "
							"expected to get wet and drain."
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
			"lesson_title": "Tile adhesion and precast coping installation",
			"estimated_minutes": 21,
			"summary": "Sapphire's 95% to 100% coverage rule behind a submerged tile, levelling a coping weir to 1/16 inch, and setting a heavy stone without crushing a hand.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Under water, every void behind a tile is a problem",
					"content": (
						"<p>On a dry interior floor a tile with gaps behind it is usually fine "
						"forever. Submerged, it is not, because the gaps do not stay empty. They "
						"fill with water — and in a cold climate that trapped water freezes, "
						"expands, and <b>cracks the tiles and pops them off the wall in chunks</b>. "
						"Even where it never freezes the voids grow biofilm, dissolve salts out of "
						"the bed and carry them out through the grout, and leave the tile held by "
						"less mortar than anybody designed for.</p>"
						"<p>So Sapphire sets a number rather than a sentiment. <b>The Back-Butter "
						"Rule: 95% to 100% mortar coverage behind every single tile.</b> That is a "
						"technique, not an instruction to use more mortar.</p>"
						"<p><b>Comb the wall in one direction.</b> Apply thinset to the "
						"sand-broadcasted wall with a notched trowel, leaving straight parallel "
						"ridges. When the tile is pressed down those ridges collapse sideways and "
						"the air between them has a straight path out. Swirls and arcs trap air in "
						"the curve of every loop, and no amount of pressing gets it out — the trowel "
						"pattern is a ventilation design.</p>"
						"<p><b>Back-butter the tile.</b> Flat-trowel a layer of thinset directly "
						"onto the back of the tile panel before it goes up. It fills the keying "
						"pattern moulded into the back and wets the surface, so the mortar on the "
						"tile and the mortar on the wall join as one rather than meeting as two "
						"skins.</p>"
						"<p><b>Beat it in across the ridges.</b> Set the tile perpendicular to the "
						"combing and work it back and forth across the ridges to collapse them, "
						"rather than dropping it in place.</p>"
						"<p><b>Then check.</b> Pull a tile back up while everything is still fresh "
						"and look at the back of it. That is the only honest measurement of "
						"coverage, and whatever you see there is what the rest of the wall looks "
						"like.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "Skinned mortar makes a tile that feels perfect and is not stuck",
					"content": (
						"<p>Mortar starts losing its ability to bond the moment it is spread. When the "
						"surface <b>skins</b> — it dulls, loses its wet sheen, stops transferring to a "
						"finger touched to a ridge — it is finished, whatever the clock says.</p>"
						"<p>A tile set into skinned mortar still sticks. It feels solid, it sounds "
						"solid, it survives grouting and it passes every casual check, because it is "
						"genuinely bonded — <b>to a skin</b>, rather than into the body of the mortar. "
						"It lets go later, usually with its neighbours, usually under water, and "
						"usually in a place that costs a drain-down to reach.</p>"
						"<p>Pressing harder does not fix it. Sapphire's instruction is to "
						"<b>scrape it off, down to the sand-broadcasted base</b>, and apply fresh "
						"material — and <b>never re-temper the skinned mortar with extra water</b>. "
						"Water added to a mix that has begun to hydrate gives you something that "
						"looks workable and has lost its strength.</p>"
						"<p>Open time is shorter in direct sunlight and in wind, which between them "
						"describe most of a working day on an open site. It is shorter again in dry "
						"air and over a thirsty substrate. Spread only what you can actually "
						"set.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Spot bonding is fast, and it is a guaranteed failure in water",
					"content": (
						"<p>Five dabs of mortar under a tile will hold it on a dry wall, and it is "
						"quick. Submerged, it fails every way at once.</p>"
						"<p>It builds a <b>continuous void</b> between the dabs, and that void fills "
						"with water and connects the whole wall behind the finish, so a leak at one "
						"tile tracks sideways to somewhere else entirely. It hands freeze-thaw a "
						"reservoir immediately behind a rigid finish. It leaves the tile edges and "
						"corners unsupported, so a point load — a foot, a ladder, a dropped tool — "
						"cracks the tile rather than being carried into the bed. And it gives salts a "
						"chamber to concentrate in.</p>"
						"<p>The same objection applies to the variations: setting a tile on a mound and "
						"tapping it down, bedding only the perimeter, and the worst of them, filling "
						"the void afterwards by working grout in from the front.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "A movement joint that stops partway through is not a movement joint",
					"content": (
						"<p>Everything in the assembly moves — the structure, the slab, the bed, the "
						"tile — at different rates, with temperature, with wetting, and with the load "
						"of the water itself. Movement joints are the designed place for that to "
						"happen without breaking something.</p>"
						"<p>Two rules get broken constantly. First, <b>never bridge a structural "
						"concrete expansion joint with tile</b>. The joint has to run through "
						"<b>every layer</b>: the grout joint, the tile, the bed, down to and lining "
						"up with the joint in the structure it is following. A joint that is "
						"honoured in the tile and bridged by the bed underneath is decorative, and "
						"the shell flexing under it shatters the tile line anyway.</p>"
						"<p>Second, it is filled with a <b>flexible sealant</b>, never with grout — "
						"and Sapphire names the family: an approved <b>underwater-grade polyurethane "
						"or silicone</b> expansion sealant, so the shell can flex safely. Grout in a "
						"movement joint is a rigid strut across the one gap that was supposed to "
						"close: either it cracks, or the tiles on both sides lift into a tented "
						"ridge and come "
						"off.</p>"
						"<p>Joints also belong where the assembly changes — at a perimeter, where a "
						"floor turns up into a wall, at a change of plane or of material, and over "
						"every structural joint. Their spacing and position are on the drawing.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "Coping sits on a full bed, and it is anchored on purpose",
					"content": (
						"<p>Precast coping stones form the visible architectural edge of the "
						"fountain, and on a negative-edge feature they frequently <b>are the "
						"weir</b> — the spillover edge the whole effect is built on.</p>"
						"<p>They are heavy, and Sapphire sets them on a <b>thick, non-sag mortar "
						"bed</b> — full and continuous, not dabs, not a mound at each end. Non-sag "
						"is what holds a heavy stone at the height you set it instead of letting it "
						"settle out of line while you work down the run. The bearing has to be even "
						"along the whole piece, for two reasons: a stone that rocks will keep "
						"rocking and will break its joints open, and a piece bearing on two high "
						"points has all of its load, and all of the load of anybody who sits on it, "
						"concentrated there. That is how a corner cracks off a coping that nobody "
						"dropped.</p>"
						"<p>Anchorage is a detail, not a habit. Dowels, anchors, adhesive, slip "
						"sheets, expansion allowance at joints — whichever the drawing shows is there "
						"because somebody decided what this edge has to resist. A coping detailed "
						"with anchors is not a piece to set loose and grout later.</p>"
						"<p>And the anchor is a metal sitting in water, so everything from the metals "
						"lesson applies to it: its alloy is specified, and a substituted anchor is a "
						"galvanic couple buried in a stone you will have to break to reach.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "1/16 inch, across the entire length",
					"content": (
						"<p>The top overflow edge of a negative-edge weir wall must be level to "
						"<b>within 1/16 inch across its entire length</b>. That is Sapphire's "
						"tolerance. It is not a target to aim at and miss politely; it is the "
						"specification for the edge.</p>"
						"<p>The reason is that water does not average — it goes to the low point. If "
						"one side of the weir dips even slightly, <b>all</b> of the flow funnels "
						"through that dip, the sheet breaks up, and the rest of the edge beads or "
						"runs dry. The uniform glass-sheet waterfall the client paid for simply "
						"stops existing, and nothing downstream rescues it: more pump does not fix "
						"it and a bigger basin does not fix it.</p>"
						"<p>A spirit level off a bucket will not resolve 1/16 inch over a long run. "
						"Use a <b>machinist level or a digital smart level</b> alongside your "
						"<b>laser transit</b>, work the length as one edge rather than piece to "
						"piece, and check it again after the bed has taken the weight.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "A coping piece is a crush injury looking for a slip",
					"content": (
						"<p>These pieces are heavy — a long precast or stone unit runs to hundreds of "
						"pounds and is always heavier than it looks. It is carried at edge height, over "
						"a hard surface, frequently over water, and the moment it lands is the moment "
						"hands are at the joint lining it up.</p>"
						"<p>Use lifting equipment, suction cups, clamps or enough people for the piece "
						"in front of you, and plan the route and the set-down before it leaves the "
						"pallet. <b>Keep fingers out of the joint and off the bed side</b>, and close "
						"a joint with setting wedges, shims and a bar rather than a hand. Wet mortar, "
						"a wet deck and a smooth stone are a bad combination for grip.</p>"
						"<p>If the piece is beyond a safe manual lift, it is beyond a safe manual lift "
						"whatever the schedule is. A crushed hand is a permanent injury and it happens "
						"in the last six inches of the movement.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Demonstrated to a Lead Installer before you graduate Module 4",
					"items": [
						"Mix a zero-slump dry pack mortar bed that passes the handheld snowball compaction test",
						"Set a 12-inch by 12-inch section of glass tile panel using back-butter mechanics, confirming 100% mortar coverage when a tile is lifted for inspection",
						"Align two adjacent precast coping stones on a mock weir wall, matching elevations to within a strict 1/16 inch",
					],
				},
				ask_block(
					"Rated for immersion, and set to the detail",
					"<p>Sapphire's document answers two of these outright. Coverage behind a "
					"submerged tile is 95% to 100%, and an expansion joint takes an approved "
					"underwater-grade polyurethane or silicone sealant. Neither is a figure to "
					"negotiate down because the tile is going slowly.</p>"
					"<p>Which setting mortar and which grout is still a product answer. They are "
					"rated, and one that is ideal on an interior floor can soften, discolour, "
					"harbour growth or be attacked outright by treated water. Epoxy grout, an "
					"immersion-rated cementitious grout and ordinary wall grout are three different "
					"answers to three different questions.</p>"
					"<p>Movement joint spacing and location, anchorage and the anchor alloy, how "
					"long the assembly must cure before it is flood-tested and before it is filled "
					"for service, and whether the water may be chemically treated during the first "
					"fill all come from the manufacturer's data and the project specification. "
					"Ask before the tile goes down, not after the basin is full.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "Which of these does Sapphire's Back-Butter Rule require when setting submerged tile?",
						"type": "Multiple Choice",
						"explanation": (
							"95% to 100% mortar coverage behind every single tile, thinset combed onto the "
							"sand-broadcasted wall with a notched trowel, and a flat-troweled layer on the back of the "
							"tile before it is beaten in. The voids left by anything less fill with water, and in a "
							"cold climate that water freezes, expands and pops the tiles off the wall in chunks."
						),
						"options": [
							{
								"text": "95% to 100% mortar coverage behind every single tile",
								"is_correct": True,
							},
							{
								"text": "Thinset combed onto the sand-broadcasted wall with a notched trowel",
								"is_correct": True,
							},
							{
								"text": "A flat-troweled layer of thinset on the back of the tile before it is beaten in",
								"is_correct": True,
							},
							{
								"text": "Dabs of thinset at the corners and the centre, which is faster and holds just as well",
								"is_correct": False,
							},
						],
					},
					{
						"question": "How level must the top overflow edge of a negative-edge weir wall be, and why?",
						"type": "Single Choice",
						"explanation": (
							"Sapphire's tolerance is 1/16 inch across the entire length. Water does not average — it "
							"goes to the low point — so a dip at one end funnels the whole flow through it, the sheet "
							"breaks up and the rest of the edge beads or runs dry. Holding that over a long run takes a "
							"machinist or digital smart level alongside a laser transit."
						),
						"options": [
							{
								"text": "Level to 1/16 inch across its entire length, or the flow funnels through the low point and the sheet breaks up",
								"is_correct": True,
							},
							{
								"text": "Level to 1/4 inch across its entire length, which the eye cannot pick up in moving water",
								"is_correct": False,
							},
							{
								"text": "Level at each end, with the middle left to follow the coping joints",
								"is_correct": False,
							},
							{
								"text": "Pitched slightly towards the drop so the water leaves the edge cleanly",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A tile is set into mortar that had already skinned over. What is the result?",
						"type": "Single Choice",
						"explanation": (
							"It bonds to the skin rather than into the mortar. It feels and sounds solid and passes "
							"every casual check, then lets go later — usually under water and usually with its "
							"neighbours. Sapphire's instruction is to scrape it off down to the sand-broadcasted base "
							"and apply fresh material, never to re-temper it with water."
						),
						"options": [
							{
								"text": "It feels solid and passes inspection, then fails later because it is bonded only to a skin",
								"is_correct": True,
							},
							{
								"text": "It will not stick at all, so the problem is obvious immediately",
								"is_correct": False,
							},
							{
								"text": "It bonds normally as long as it is pressed down harder",
								"is_correct": False,
							},
							{
								"text": "It bonds normally; skinning only affects the colour of the mortar",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Which of these must be true of a movement joint in a tiled water feature?",
						"type": "Multiple Choice",
						"explanation": (
							"Tile never bridges a structural expansion joint. The joint runs through every layer "
							"down to the one it is following, and it is filled with something that can move — on this "
							"work an approved underwater-grade polyurethane or silicone sealant. Grout in a movement "
							"joint is a rigid strut across the one gap that was supposed to close."
						),
						"options": [
							{
								"text": "It runs through the tile, the bed and down to the joint it is following",
								"is_correct": True,
							},
							{
								"text": "It is filled with an approved underwater-grade polyurethane or silicone expansion sealant",
								"is_correct": True,
							},
							{
								"text": "It is located over the structural joint, at perimeters, and at changes of plane or material",
								"is_correct": True,
							},
							{
								"text": "It is filled with grout matching the field so it disappears",
								"is_correct": False,
							},
						],
					},
				]
			},
		},
		{
			"lesson_title": "Stainless steel fasteners and anti-seize protocols",
			"estimated_minutes": 14,
			"summary": "Why stainless threads cold-weld to each other, how to stop it, and why lubricating a fastener changes what a torque figure means.",
			"blocks": [
				{
					"block_type": "Rich Text",
					"heading": "Galling is not overtightening, it is welding",
					"content": (
						"<p>Everything that makes 300-series stainless good in water makes it bad in a "
						"thread. It is tough, it is ductile, it work-hardens, and it carries a thin "
						"oxide film that is the only thing keeping two clean metal surfaces apart.</p>"
						"<p>As a nut runs down a bolt, the threads slide under load and scrape that "
						"film off in patches. Clean stainless pressed hard against clean stainless with "
						"no oxide between them does what clean metal does: it <b>sticks</b>. The two "
						"surfaces cold-weld, tear, and weld again. Friction climbs, heat climbs, and "
						"heat makes the next patch worse.</p>"
						"<p>That is galling, and it ends in a fastener seized solid partway down its "
						"thread — one that will not tighten, will not come off, and has not reached "
						"its seat. Aluminium and titanium do the same thing for the same reason.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "So: by hand, slowly, clean, lubricated, and stop when it argues",
					"content": (
						"<p><b>Start it by hand.</b> Run the first few turns with your fingers so you "
						"know it is square and engaged. A cross-threaded start galls on the first "
						"turn.</p>"
						"<p><b>Keep the threads clean.</b> Sand, grit and mortar in a thread are "
						"abrasive and they strip the oxide film exactly the way galling needs.</p>"
						"<p><b>Go slowly.</b> Speed generates heat and heat accelerates the whole "
						"mechanism. Which is why an <b>impact driver is the single most reliable way "
						"to gall a stainless fastener</b> — it is fast, it hammers, and it removes the "
						"one useful instrument you have, which is feeling the resistance change.</p>"
						"<p><b>Use an anti-seize.</b> A film of compound between the threads is exactly "
						"the barrier the oxide layer is failing to be.</p>"
						"<p><b>Stop if it gets hard early.</b> A fastener getting harder to turn before "
						"it is anywhere near its seat is not tight, it is galling. Back it off, clean "
						"it, look at the threads. Forcing it through is the difference between a "
						"nuisance and a destroyed assembly.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Warning",
					"heading": "A galled fastener usually has to be destroyed",
					"content": (
						"<p>Once two stainless threads have cold-welded there is no penetrating oil, no "
						"heat cycle and no longer wrench that reliably separates them. They are not "
						"stuck together, they are <i>joined</i>. It comes out by cutting, grinding, "
						"drilling or extracting.</p>"
						"<p>Which means galling is never just a fastener problem. It is a "
						"galled anchor in a finished stone coping, a galled bolt on a submerged "
						"fixture that now needs a drain-down, a galled union on a plumbed manifold. "
						"The damage lands on the expensive thing the fastener was holding.</p>"
						"<p>There is no repair technique in this lesson because there is no reliable "
						"repair. The whole of the prevention is in the block above it.</p>"
					),
				},
				{
					"block_type": "Callout",
					"callout_tone": "Danger",
					"heading": "Anti-seize changes what a torque figure means",
					"content": (
						"<p>A bolted joint does not need torque. It needs <b>clamp load</b> — tension "
						"in the fastener pulling the parts together. Torque is only the crude way we "
						"reach it, and most of the torque applied is spent overcoming friction under "
						"the head and in the threads. Only a fraction of it becomes tension.</p>"
						"<p>Anti-seize cuts that friction. That is its job. So the <b>same torque now "
						"produces considerably more clamp load</b> — apply a dry figure to a lubricated "
						"fastener and you can stretch the bolt past yield, snap it, or crush and crack "
						"what it is clamping: a stone coping, a plastic flange, a gasket, a "
						"housing.</p>"
						"<p>The rule is simple and it is absolute: <b>a torque figure and a lubrication "
						"condition are a matched pair.</b> Use the figure written for the compound and "
						"the condition in front of you. Never take a dry figure and lubricate anyway, "
						"and never take a lubricated figure and run the fastener dry.</p>"
					),
				},
				{
					"block_type": "Rich Text",
					"heading": "The fastener is also a metal living in the water",
					"content": (
						"<p>Everything from the metals lesson lands here. A fastener is mostly "
						"<b>crevice</b> — under the head, under the washer, along the engaged thread — "
						"which is precisely where water goes stagnant, oxygen runs out and the passive "
						"film cannot re-form. Fasteners are one of the first places corrosion shows up "
						"on an otherwise healthy stainless assembly.</p>"
						"<p>Mixed alloys make it faster. A fastener of one metal into a fitting of "
						"another is a galvanic couple, and the fastener is usually the small part in "
						"the pair — so if it is also the less noble of the two, it is consumed "
						"fast.</p>"
						"<p>The anti-seize itself is part of that decision. Most compounds are loaded "
						"with metal — copper, nickel, aluminium — and a metal-loaded compound "
						"introduces a third metal into a joint that is going to sit in treated water. "
						"Metal-free formulations exist for exactly that reason. Compounds also differ "
						"in what they are approved for: potable water, plastics, elastomers and oxygen "
						"service are each their own compatibility question.</p>"
					),
				},
				{
					"block_type": "Checklist",
					"heading": "Setting a stainless fastener",
					"items": [
						"The fastener is the alloy and grade the drawing calls for, not the one in the truck bin",
						"The anti-seize is one approved for these metals and for this water",
						"Threads are clean, undamaged and free of sand and mortar",
						"Started by hand and running free for several turns before any tool touches it",
						"Driven at low speed — no impact driver on a stainless thread",
						"If it stiffens before it reaches its seat, back it off and look rather than forcing it",
						"Final tightening to the specified torque, using the figure written for the lubricated condition",
						"A torque wrench where a figure exists, rather than a feel and a guess",
					],
				},
				ask_block(
					"The torque, the compound and the alloy are all specified",
					"<p>The torque value, whether that value is a dry or a lubricated figure, which "
					"anti-seize compound, which alloy and grade of fastener, and whether the compound "
					"is approved for the water and the materials it will touch are all manufacturer and "
					"specification answers. There is no general-purpose number, and the figure that "
					"was right on the last fixture is wrong on this one.</p>"
					"<p>Where a fastener is structural, or anchors something that could fall or be "
					"stood on, it is an engineered detail on top of that — an anchor, an embedment and "
					"an edge distance are designed together.</p>",
				),
			],
			"quiz": {
				"questions": [
					{
						"question": "What is happening when a stainless nut galls on a stainless bolt?",
						"type": "Single Choice",
						"explanation": (
							"Sliding scrapes the passive oxide film off, and clean stainless pressed against clean "
							"stainless cold-welds. Friction and heat rise, which makes the next patch worse."
						),
						"options": [
							{
								"text": "The oxide film is scraped away and the two clean surfaces cold-weld to each other",
								"is_correct": True,
							},
							{
								"text": "The bolt is simply being overtightened past its rated torque",
								"is_correct": False,
							},
							{
								"text": "Rust between the threads is binding them together",
								"is_correct": False,
							},
							{
								"text": "The threads were cut to different pitches and are jamming",
								"is_correct": False,
							},
						],
					},
					{
						"question": "Why is an impact driver a bad tool for a stainless fastener?",
						"type": "Single Choice",
						"explanation": (
							"It is fast, which generates the heat that accelerates galling, it hammers, and it takes "
							"away the ability to feel the resistance change that warns you galling has started."
						),
						"options": [
							{
								"text": "Speed and hammering drive the heat that accelerates galling, and you lose all feel for it",
								"is_correct": True,
							},
							{
								"text": "It cannot generate enough torque to seat a stainless fastener",
								"is_correct": False,
							},
							{
								"text": "It magnetises the fastener, which promotes corrosion",
								"is_correct": False,
							},
							{
								"text": "It is fine as long as anti-seize has been applied",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A fastener is coated with anti-seize and then tightened to the torque figure written for a dry assembly. What is the risk?",
						"type": "Single Choice",
						"explanation": (
							"Lubrication removes friction, so more of the applied torque becomes tension. The same "
							"number now over-tensions the fastener and can yield or snap it, or crack what it clamps."
						),
						"options": [
							{
								"text": "The same torque produces far more clamp load, over-tensioning the fastener and what it clamps",
								"is_correct": True,
							},
							{
								"text": "The same torque produces less clamp load, so the joint is left loose",
								"is_correct": False,
							},
							{
								"text": "No risk — anti-seize does not affect the torque to clamp load relationship",
								"is_correct": False,
							},
							{
								"text": "The compound acts as a thread locker and the joint cannot be undone",
								"is_correct": False,
							},
						],
					},
					{
						"question": "A properly galled stainless nut can normally be freed with penetrating oil and a longer wrench.",
						"type": "True-False",
						"explanation": (
							"Galled threads are cold-welded rather than stuck, so oil has nothing to penetrate and more "
							"force twists the fastener apart. It is cut, ground, drilled or extracted."
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
