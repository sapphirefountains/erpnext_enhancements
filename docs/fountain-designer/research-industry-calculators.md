# Fountain Designer — what the trade calculates, what can be automated, and what catalog data exists

Research note, 2026-11-01. Read-only research for the Fountain Designer rebuild in
`erpnext_enhancements/water_engineering/`. Nothing in the app was changed.

**How to read the sources.** Every number carries a URL. Many manufacturers keep their full
design guides behind a login: Crystal Fountains, Roman Fountains and Fountain People among
them. So a lot of the trade's rules of thumb come from trade press (WaterShapes, Pool & Spa
Marketing), from cut sheets, and from Crystal's public slide decks. The tags mean:

- **[snippet]**: read only in a search-result excerpt.
- **[weak]**: the source is low quality or unattributed.
- **[gap]**: no public source found.

Where sources disagree, the table gives both values. Treat every rule here as a starting
default the designer can override, never as code.

---

## 0. Findings that matter most

1. **The engine's jet-height default is optimistic.** `engine/constants.py` sets
   `JET_EFFICIENCY["smooth"] = 0.90`, and `jet_trajectory` computes height as 0.90 × head.
   The sources disagree with it:
   - A smooth-bore rule of thumb needs head of about **1.22 × height** (k ≈ 0.82)
     ([Scribd, unattributed](https://www.scribd.com/document/462312854/elementry-pdf) [weak]).
   - A real decorative column nozzle needs about **2.5–3 × height**. Atlantic's column nozzle
     makes 2 ft at 6.5 ft of head and 12 ft at 31 ft
     ([Atlantic via retailer](https://practicalgardenponds.com/products/atlantic-water-gardens-vertical-nozzles)).
   - Nozzle performance tables (height → flow, head) should replace the constant. This is the
     cheapest accuracy win on the list.
2. **No manufacturer publishes machine-readable catalog data.** Pump curves, nozzle tables and
   light specs are all PDFs, web pages or selectors behind a login. Hand-entering a curated
   catalog of a few hundred SKUs is realistic. Scraping is not: Pentair's terms forbid it
   outright ([Pentair terms](https://www.pentair.com/en-us/legal/terms-of-use.html)). Detail
   in §4.
3. **The existing catalog doctypes are too thin to auto-pick equipment.**
   - `Pump Curve Point` stores only flow and head: no efficiency, NPSHr or speed.
   - `Nozzle Profile` stores one rated GPM at one rated head, and no display height at all.
   - Both need a points table (§4.3).
4. **The rain-curtain app already has the automation pattern to copy.** It uses presets, kits,
   a fixed order of auto-sized sections, a full-model pump ranking with reasons for each
   failure, and a verdict written in plain language. §2.2 maps it onto the Python engine.
5. **Utah regulates interactive water features precisely**
   ([R392-302-41](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41)):
   - 30-minute turnover, with at least 4 inlets
   - collector tank of at least **3 minutes** of combined pump flow
   - nozzle velocity of at most **20 ft/s**
   - ground-level openings of at most ½"
   - a 3 ft deck sloped at 2%
   - ORP/pH control and secondary disinfection
   - a circulation loop separate from the feature pump

   Every one of these can be checked automatically.
6. **Public $/sq ft figures for fountains are not usable.** What turned up was residential
   SEO content ([homeguide](https://homeguide.com/costs/water-fountain-cost),
   [countbricks](https://www.countbricks.com/post/water-fountain-installation-cost)). Budget
   pricing should be BOM × Sapphire's own ERPNext Item prices plus labour assemblies taken
   from past projects.

---

## 1. Feature families: inputs, rules of thumb, checks

"Engine today" says what `water_engineering/engine/` already covers, judged from the file
headers and `README.md`.

### 1.1 Sheet falls, weirs, vanishing edges, waterwalls

**Inputs:**
- weir length
- lip shape (sharp, square or rounded)
- edge tolerance
- target sheet thickness or head over the lip
- fall height and catch-basin width
- upper-pool surface area (for surge)
- whether it is a spa or a raised element

**Engine today:** Francis weir (`feature.py`) and the weir-edge wind bands in
`WEIR_EDGE_BANDS`. Pool surge exists only for pools (`drainage.surge_basin_volume`).

| Rule | Value | Source |
|---|---|---|
| Francis weir | Q (gpm) = 36·L·h^1.5 − 0.3·n·h^2.5 (L ft, h in): ¼" ≈ 4.5 gpm/ft; 1" ≈ 36 gpm/ft | [WaterShapes, Over the Edge](https://watershapes.com/over-the-edge/) |
| Planning flow, square edge | 7–10 gpm/ft at ¼"; 36 gpm/ft at 1". Higher than Francis: use Francis as the floor | [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/) |
| Vanishing edge / slot overflow | about 5 gpm/ft; slot overflows down to about 2 gpm/ft. "The sharper the edge, the lower the flow" | [WaterShapes](https://watershapes.com/over-the-edge/) |
| Edge tolerance → head | 1/16" of tolerance needs 1/8" of head; ¼" of tolerance needs ½"; a rounded crest needs ≥ 1" | [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/) |
| Edge build tolerance | ±1/16"; 20 ft treated as the minimum vanishing-edge length | [WaterShapes, Water in Transit](https://watershapes.com/water-in-transit/) |
| Sheet-fall products | Jandy Sheer Descent about 12 gpm/ft (range 5–15); Pentair MagicFalls 8–10 gpm/ft | [epoolsupply summary](https://www.epoolsupply.com/blogs/default-blog/jandy-sheer-descent-sheer-rain-water-features-comprehensive-guide) [snippet] |
| Sheet breakup | Blue Thumb formal falls: at most 24" of drop before the sheet "breaks apart"; MagicFalls sheet at most 1 m, rain at most 2 m | [Blue Thumb](https://pondprodirect.com/products/blue-thumb-stainless-medium-formal-falls-28); [Sta-Rite manual](https://manualmachine.com/starite/magicfalls/5275295-installation--manual) [snippet] |
| Landing distance | 1 m fall: a 6 mm sheet lands about 0.3 m out; a 25 mm sheet about 0.7 m out | [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/) |
| Catch-basin width | about equal to the fall height (1:1) "when circumstances allow" | [WaterShapes 2024](https://watershapes.com/?p=49059) |
| Vanishing-edge surge | 2" of displacement over the upper surface (area × 7.5 / 6 gal); 3" for small, busy pools. Add any raised spa's volume and ≥ 1 ft of vortex reserve | [WaterShapes](https://watershapes.com/water-in-transit/); [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/) |
| Line velocity | ≤ 7 ft/s on suction and discharge for edge systems. The engine's PVC limits are stricter: suction 4.5, discharge 6.5 | [WaterShapes](https://watershapes.com/water-in-transit/) |

**Checks to flag:**
- gpm/ft below what the edge tolerance needs
- sheet drop over 24"–3 ft without a rain or textured lip
- catch basin narrower than the fall
- trough or surge volume below the 2" rule (plus spa volume)
- vanishing edge under 20 ft
- tolerance worse than ±1/16"
- sheets above about 3 ft with no wind control

### 1.2 Nozzle arrays, jet rings, single orifice jets, geysers

**Inputs:**
- nozzle model, or type and size
- target display height
- count and spacing (ring diameter, row pitch)
- water depth over the nozzle (for aerating types)
- distance to the nearest basin edge

**Engine today:** `nozzle_flow` (Cd·A·√2gh, or rated GPM × √(h/h₀)), `nozzle_array_flow`,
and `jet_trajectory` (height = k × head; see finding 1).

| Rule | Value | Source |
|---|---|---|
| Physics | h ≈ k·v²/2g; nozzle efficiency k about 0.85–0.95 for laminar or smooth bore | [Firgelli](https://www.firgelliauto.com/en-mx/blogs/mechanisms/luminous-fountain) [weak] |
| Smooth bore | head ≈ 1.22 × height, up to about 140–150 ft; above that, ask the factory | [Scribd](https://www.scribd.com/document/462312854/elementry-pdf) [weak] |
| Real decorative nozzle | column nozzle head ≈ 2.5–3 × height (2 ft at 2.8 psi; 12 ft at 13.5 psi) | [Atlantic](https://practicalgardenponds.com/products/atlantic-water-gardens-vertical-nozzles) |
| Manufacturer tables | OASE Geyser and Vulcan: per size, height in ft/m → GPM, TDH ft, LPM, bar | [OASE Geyser PDF](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Geyser_Nozzles_Specifications_Sheet_PDF.pdf); [Vulcan PDF](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Vulcan_Nozzles_Specifications_Sheet_PDF.pdf) |
| Equal heights | size the branch piping for equal head loss to every nozzle | [Safe-Rain part 2](https://saferain.com/en/blog/tips-of-fountain-design-part-2/) |
| Entry angle | jets entering at 30–90°; nearer vertical makes fewer ripples | [Safe-Rain part 3](https://saferain.com/en/blog/tips-of-fountain-design-part-3/) |

**Checks to flag:**
- height assumed from a constant rather than a table
- manifold pressure spread above ±2–5% across an array (the rain curtain uses ±2%)
- velocity in the nozzle supply piping
- any jet at a body-contact venue with orifice velocity above 20 ft/s (Utah IWF)

### 1.3 Foam / aerating jets, geysers

These behave like 1.2, with one extra input that matters: **water depth over the nozzle**. OASE
Cascade and Geyser injector nozzles are sensitive to water level. Below normal level the jet
is slender and taller; above it, the jet is fuller and lower. The Schaumsprudler model is not
sensitive to level.
([OASE Cascade](https://www.oase.com/cs-CZ/fountain-technology/cascade-50-t-npt);
[OASE catalog p.56](https://www.oase-livingwater.com/fileadmin/pageflip/32610_FT-Katalog_GB_2014_lowres.pdf/files/assets/basic-html/page56.html) [snippet].)

Crystal's WMO foam jet is also level-dependent
([Pool360](https://www.pool360.com/Product/1-wmo-series-shallow-water-foam-jet-pool-cft-27-210)).

**Checks to flag:**
- a check valve on each level-sensitive jet, or the basin drains down to injector height at
  shutdown
- the autofill holding level within the nozzle's band
- no published submergence figure in inches **[gap]**: take it from each cut sheet

### 1.4 Laminar jets

**Inputs:** flow, launch angle, arc length and height, lighting.

| Product | Performance | Source |
|---|---|---|
| CMP deck jet | 2.5 gpm @ 3.8 psi → 2 ft high, 3 ft out; 3.5 gpm @ 6.5 psi → 4 ft high, 6 ft out | [CMP](https://www.poolsupplyunlimited.com/Products/Manuals/169670_20187311590.pdf) [snippet] |
| Fiberstars mini laminar | ≥ 5 gpm each; lit up to 3 ft high, 5 ft out | [Fiberstars](https://images.inyopools.com/cloud/documents/fiberstars-light-streams-lsml.pdf) |
| SRSmith | up to 6 ft up and 6 ft out | [SRSmith](https://srsmith.com/media/176437/79-15152-00-rev-e2-914.pdf) |

A ballistic arc computed from the table's flow and pressure is the right calculator here.

**Checks to flag:** landing point inside the basin, with a wind margin; filtration ahead of the
jet, since laminar jets clog easily.

### 1.5 Interactive dry-deck plazas and splash pads (sequenced valves)

**Inputs:**
- feature list (GPM each)
- the sequence: the maximum number of features open at once
- deck area
- manifold-to-feature distances
- whether the venue is regulated (in Utah it is)

**Engine today:** `aquatic.py` covers regulated pools and spas. It has no interactive-feature
rules.

| Rule | Value | Source |
|---|---|---|
| Utah turnover | whole volume filtered and treated "within 30 minutes"; ≥ 4 equally spaced inlets | [R392-302-41](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41) |
| Utah collector tank | ≥ 3 minutes of the combined flow of every feature pump plus the filter pump | same |
| Utah nozzle velocity | ≤ 20 ft/s | same |
| Utah openings | ground nozzles ≤ ½"; drains must not pass a ½" sphere | same |
| Utah deck and drainage | continuous deck ≥ 3 ft; surrounding ground sloped away ≥ 2% | same |
| Utah treatment | automated ORP + pH controller; secondary disinfection per R392-302-35(8); circulation on a loop separate from the feature pump; automated level controller; foggers fed with potable water; 5 fc of light at night | same |
| MAHC | 30-minute turnover plus secondary disinfection (UV or ozone), with third-party-validated UV and a strainer after the UV; 10 sq ft per person | [Waterline, MAHC summary](https://waterline.com/?p=1085) (2014 edition) |
| MAHC secondary disinfection | 3-log Cryptosporidium reduction, placed after the feature pump; feature-to-filter flow ≤ 3:1 | [CDC/NACCHO](https://stacks.cdc.gov/view/cdc/153389/cdc_153389_DS1.pdf) [snippet] |
| Feature flows | spouts 5–20 gpm, tents 15–30, posts 10–60 | [Waterplay](https://issuu.com/makrgroup/docs/waterplay_water_management_brochure) [snippet] |
| Sequencing saving | 30–50% lower total GPM | [Vortex](https://www.vortex-intl.com/blog/how-to-design-low-impact-splashpad) |
| Line length | Vortex sizes lines assuming ≤ 30 ft from manifold to the farthest feature | [Vortex](https://www.vortex-intl.com/blog/water-play-design-mistakes-to-avoid/) [snippet] |

**Checks to flag:** every Utah item above, as a pass/fail list. Sizing the feature pump to the
worst sequence step, not to the sum of all features, is where automation saves money. The
engine's existing VGB/suction-outlet gate applies to the collector tank suction.

### 1.6 Cascades, tiers, ponds, naturalistic streams

**Inputs:**
- spillway width per tier
- desired look (light, average or heavy)
- stream length × width × depth
- pond surface and volume
- fall heights

**Engine today:** `tiered_fountain_flow` and `open_channel_flow` (Manning, for runnels).

| Rule | Value | Source |
|---|---|---|
| Spillway flow | residential minimum 1,500 GPH per ft of width; commercial 3,000; about 125 GPH per inch (width measured at the spillway, not the frame rocks) | [Aquascape support](https://support.aquascapeinc.com/hc/en-us/articles/48506913796244-Waterfall-What-flow-rate-is-needed-for-a-waterfall); [Aquascape pump guide](https://www.azponds.com/wp-content/uploads/pdf/aquascape%20Pump-Selection-Guide.pdf) |
| By sheet depth | about 50 / 100 / 200 GPH per inch for ¼" / ½" / 1" over the lip | [Pond Trade](https://www.pondtrademag.com/?p=17369) [snippet] |
| Pond turnover | at least once every 2 h (Aquascape) vs once per hour (koi guidance) | [Aquascape manual](https://manualslib.fr/manual/724172/Aquascape-Aquaforce-1000-Gph.html?page=4) [snippet]; [koi guide](https://playitkoi.com/pages/pond-pump-sizing-guide) [snippet] |
| Rill width | Crystal limits rills to 12" wide | [Crystal slide deck](https://www.slideshare.net/slideshow/water-feature-types/5145948) |

**Checks to flag:**
- each tier's flow below its look band
- the tightest tier governs the pump flow
- drain-back at shutdown overflowing the lowest basin
  ([WaterShapes](https://watershapes.com/water-in-transit-pond-problem/))
- liner sizing has no public rule **[gap]**: use L + 2·depth + 2 ft overlap as a Sapphire
  standard, if the designers agree

### 1.7 Pondless, urn and bubbler kits

**Inputs:** stream or urn flow, plus the reservoir type (matrix blocks or gravel).

| Rule | Value | Source |
|---|---|---|
| Stream volume | W × L × D (ft) × 7.5 gal; reservoir ≥ 3× that | [Best Planet](https://best-planet.com/pages/pump-sizing-for-pondless-systems) |
| Reservoir by fill | ≥ 4× the dynamic volume with water-matrix blocks; ≥ 12× if gravel-filled | [Pond Trade](https://www.pondtrademag.com/sizing-gravel-filled-and-water-matrix-reservoirs-for-pondless-features/) |
| Small pump data | Aquascape AquaSurge publishes GPH at 0, 5, … 30 ft per model | [Aquascape](https://www.aquascapeinc.com/products/aquasurge-5000-pond-pump) |

Kits are a good fit for **BOM templates**, with almost no engineering in them.

**Check to flag:** reservoir multiplier against the fill type.

### 1.8 Rain curtains and water screens

The Digital Rain Curtain app covers the digital, valve-driven type end to end. Passive rain and
lace curtains:

| Rule | Value | Source |
|---|---|---|
| Strand flow | about 0.33 gpm per strand | [WaterShapes](https://watershapes.com/?p=27303) |
| Mylar lace | 0.5 L/min per strand, strands ≥ 35 mm apart | [water-garden.co.uk](https://www.water-garden.co.uk/prod/mylar-lace) |
| Stream screens | 48 streams/ft at 0.5 gpm/ft; double row 1 gpm/ft | [WaterShapes](https://watershapes.com/?p=32451) |
| Lace angle | can hang up to 20° from vertical | [Crystal slide deck](https://www.slideshare.net/slideshow/water-feature-types/5145948) |

**Checks to flag:** hard-water scaling, a real problem on Wasatch Front supplies. Recommend
softened or RO make-up water and a descaling schedule.

### 1.9 Misting and fog

High-pressure (about 1,000 psi) systems:
- a 0.008" orifice passes about 0.020 gpm, a 0.012" orifice about 0.028 gpm
- use 0.012" in dry climates
- ([Advanced Misting](https://www.advancedmistingsystems.com/stainless-steel-10-24-anti-drip-cleanable-nozzle/);
  pump capacity by nozzle count: [Cool-Off](https://www.cool-off.com/high-pressure-misting-system-pumps))

These are patio-cooling sources **[weak fit]**.

Utah requires fogger water to come from a potable source, never the reservoir
([R392-302-41](https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41)). The
calculator is simple: nozzle count × gpm per nozzle → pump size, plus line pressure rating.

### 1.10 Floating aerators and fountains (ponds)

**Inputs:** surface acres, shape, depth, purpose (aeration or display), voltage available,
and the distance to shore power.

| Rule | Value | Source |
|---|---|---|
| Kasco | AF series 1 HP per surface acre; JF display about 2 HP/acre; CF ¾ HP/acre | [Kasco sizing chart](https://cdn.shopify.com/s/files/1/2394/4493/files/Kasco_VFX_AF_JF_Fountain_Sizing_Chart_PDF.pdf) |
| Otterbine | 2 HP per surface acre for water quality | [Sweets/Otterbine](https://sweets.construction.com/swts_content_files/49654/P302633.htm) [snippet] |
| Safe-Rain | about 1 HP per 4,000 m² (≈ 1 HP/acre) | [Safe-Rain FAQ](https://saferain.com/en/faq) |
| Shape | for long, narrow ponds, use several smaller units rather than one large one | [MSU archive](https://archive.lib.msu.edu/tic/wetrt/article/1986jul54.pdf) |
| Spec tables | Otterbine Gemini 60 Hz: 1 HP (5 ft × 10 ft, 555 gpm) to 5 HP (15 × 34 ft, 1,125 gpm) | [Otterbine PDF](https://www.otterbine.com/media/1059/salesheet_gemini_60hz_14.pdf) |

**Checks to flag:**
- HP/acre below the purpose's band
- pattern diameter against the pond and shoreline setback
- cable voltage drop over the run to shore (reuse the rain-curtain electrical module's
  voltage-drop rule)

### 1.11 Lighting

**Inputs:** effect heights, nozzle positions, weir lengths, voltage system, DMX or switched.

**Engine today:** `controls.calc_lighting` (60 W per 12 VDC relay, from DOC-0126).

| Rule | Value | Source |
|---|---|---|
| Count | ≥ 2 lights on streams over 4 ft | [Crystal lighting deck](https://www.slideshare.net/slideshow/lighting-5145934/5145934) |
| Beam | narrow spot (about 13°) for sprays over 10 ft; wide flood (about 44°) under 10 ft | same |
| Depth | "no more than 2 in" below water (Crystal) vs 10 cm (SYA) | same; [SYA](https://www.syalighting.com/news/lighting-design-of-landscape-fountain-95434.html) |
| Illuminance | light a 10 m jet to ≥ 300 lux; a 75 W spot gives about 600 lux at 10 m | [Safe-Rain FAQ](https://saferain.com/en/faq) |
| Transformer | VA = total watts × 1.25 | [Crystal lighting deck](https://www.slideshare.net/slideshow/lighting-5145934/5145934) |
| NEC 680 Part V | GFCI on fountain luminaires and submersible equipment unless listed at or below the low-voltage contact limit (15 V AC / 30 V DC) and supplied per 680.23(A)(2); luminaire circuits ≤ 150 V; lens below water unless listed otherwise; removable for relamping | [IAEI](https://iaeimagazine.org/2013/septemberoctober-2013/fountains-and-reflection-pools/); [EC&M](https://www.ecmweb.com/code-basics/pools-and-spas-part-3-3). Verify against the NEC edition Utah has adopted |
| Fixture data | OASE ProfiLux publishes lumens, beam, W and 24 VDC (e.g. 735 lm, 11°); Pentair and Hayward publish watts only, no lumens | [OASE](https://www.oase.com/it-IT/fountain-technology/profilux-basic-led-l-w-spot-01); [Hayward](https://hayward.com/ucl-standard-switched-color-spa-light-35w-12v-100-ft-cord-lscus11100.html) |

There is no watts-per-foot-of-jet or fixtures-per-foot-of-weir rule **[gap]**. Suggest a
Sapphire house rule that is calibrated on past jobs.

**Checks to flag:**
- 24 VDC fixtures still need GFCI (forum dispute; confirm with the inspector)
- transformer VA under 1.25× load
- voltage drop on long low-voltage runs
- relay count (existing)

### 1.12 Winterization (Utah)

**Inputs:** site frost depth, pipe routing low points, equipment location (vault or room), and
feature type.

| Rule | Value | Source |
|---|---|---|
| Frost depth | 30" in Salt Lake City | [SLC design criteria](https://www.slc.gov/buildingservices/wp-content/uploads/sites/9/2026/07/SLC-Design-Criteria-2024-IBC_IRC.pdf) |
| When | wherever freeze-thaw happens or temperatures fall below 32 °F; disconnect the autofill | [Commercial winterization instructions](https://cdn.shopify.com/s/files/1/0566/4492/7571/files/Commercial_Fountain_Winterization_Instructions.pdf) |
| Blow-out | irrigation practice: ≤ 50 psi, ≤ 2 min per circuit, never through a pump or backflow preventer. Conflicts with a pool maker allowing < 5 psi blowers only | [Rain Bird](https://rainbird.com/media/1332); [Lawn & Landscape](https://lawnandlandscape.com/news/the-don-ts-of-blow-out-winterization); [TFP forum](https://www.troublefreepool.com/goto/post?id=1710978) [weak] |
| Splash pads | pull upward-facing nozzles; fit winter caps | [Vortex](https://www.vortex-intl.com/blog/winterize-water-play-system/) |

**Checks to flag:**
- every line has a drain point or slopes to drain
- lines not drained are buried below the frost depth
- equipment rooms heated or drained
- level-sensitive nozzles and lights pulled or protected

There is no heat-trace rule **[gap]**.

This is a strong service-business output: a per-site winterization and spring start-up
checklist, generated from the design.

### 1.13 Cross-cutting rules (all families)

| Topic | Rule | Source |
|---|---|---|
| Splash / wind setback | Safe-Rain: splash radius ≈ jet height in still air. Delta: height ≤ distance to the nearest edge. Crystal: 1:1 for ground pools, 1:1.5 for raised tables. Rose: falling height × 0.75 | [Safe-Rain](https://saferain.com/en/blog/tips-of-fountain-design-part-2/); [Delta](https://www.deltafountains.com/?p=772); [Crystal deck](https://www.slideshare.net/slideshow/water-feature-types/5145948); [Rose](https://www.landscapingnetwork.com/industry-leaders/bill-rose-fountains-unique.html) [snippet] |
| Wind control | 3-cup anemometer; 0–25 mph adjustable; two stages (reduce, then stop). OASE offers three stages. No standard setpoint | [Roman RWC](https://landscapearchitect.com/ladetails/landscape-product/fountains-equipment/roman-fountains-corp./rwc-sm-dul-wind-control); [OASE](https://www.oase.com/en-US/fountain-technology/wind-dependent-fountain-control-3-k-int); [WindRanger guide](https://pub-bcff8178dbe34049ab0f0c9ac449d964.r2.dev/docs/WindRanger%20Selection%20Guide.docx) |
| Basin contents | the basin must hold water in transit plus the pump's minimum level, with automatic make-up | [Safe-Rain](https://saferain.com/en/blog/tips-of-fountain-design-part-2/) |
| Basin depth | 18" maximum depth "standard in the industry" (often a code trigger) | [Delta](https://www.deltafountains.com/?p=772) |
| Suction submergence | HI 9.8: S = D(1 + 2.3·F_D), with F_D = V/√(gD) | [pumps.org datatool](https://datatool.pumps.org/pump-fundamentals/submergence); [Eng-Tips](https://www.eng-tips.com/threads/pumping-station-design.223825) |
| Suction pipe | 1.5–2× the pump inlet diameter, eccentric reducer; economic velocity 1–1.8 m/s | [Safe-Rain](https://saferain.com/en/blog/tips-of-fountain-design-part-2/) |
| Vortex reserve | ≥ 1 ft of water over the suction (edge systems) | [Pool & Spa Marketing](https://www.poolspamarketing.com/trade/water-feature-hydraulics/2/) |
| Freeboard | no public rule **[gap]**. The rain-curtain app uses 1.3 × the computed drain-back level rise, which is a good model | `digital-rain-curtain/src/hydro/presets.js` `autoBasin` |
| Evaporation | ASHRAE pool method (already in `treatment.py`), with activity factors 0.5 unoccupied / 1.0 occupied. Field studies show ±37% error, so calibrate | [HPAC](https://www.hpac.com/humidity-control/simplified-method-calculating-evaporation-from-swimming-pools); [E3S study](https://www.e3s-conferences.org/10.1051/e3sconf/202236214004) |
| Utah evaporation scale | about 74.6 in/yr Class A pan at Saltair (lakeshore; overstates the city) | [USGS OFR 82-91](https://pubs.usgs.gov/of/1982/0091/report.pdf) |
| Altitude | the Wasatch Front sits at about 4,200–5,000 ft. Atmospheric pressure falls (standard-atmosphere formula), which cuts NPSH available. `safety.npsh_available` should take site elevation, not sea level | standard atmosphere (textbook) |

Spray and splash loss on outdoor fountains (beyond evaporation) has no good public figure
**[gap]**. Sapphire's service visits and autofill meters are the better source. Calibrate a
per-family loss factor from them.

---

## 2. The automation ladder

### 2.1 From a handful of inputs to a full design

**Minimum inputs:**
- feature type (one of the families)
- site address, giving elevation, climate, frost depth and the governing code
- principal dimensions
- the desired effect: sheet thickness or "look", jet height, or acres of pond
- pump location and its distance from the feature

Everything below is derived in this order. Each step reads only from the steps above it, which
is what lets the result stay in step when an input changes.

| # | Derive | From | Rule / engine function | Designer still chooses |
|---|---|---|---|---|
| 0 | Site context: elevation, design air/water temp, frost depth, water hardness, $/kWh, $/gal, regulated or not | address + feature type | geocode → elevation; Utah lookup table; `aquatic.is_regulated` extended to interactive features | override any |
| 1 | Water surface, basin volume, weight | dimensions | `basin.basin_volume` | basin shape |
| 2 | Feature flow | effect + dimensions | weir: gpm/ft from look and edge tolerance (§1.1). Jets: height → nozzle table → gpm + head per nozzle. Cascades: GPH/ft. Splash pad: worst sequence step | **nozzle model / effect**, sequence |
| 3 | Code minimum flow and tank | regulated + volume | 30-min turnover; 3-min tank; 20 ft/s velocity | — |
| 4 | Setback and wind | jet heights, fall heights, basin plan | §1.13 ratios → basin radius / catch width pass-fail; wind control stage count | **accept a tighter setback with wind control** |
| 5 | Basin and surge | flows + routes | water in transit (pipe volume + troughs + water in the air) + drain-back + HI submergence + freeboard → depth / reservoir / trough size; autofill from evaporation + splash | basin vs vault vs tank |
| 6 | Pipe routes and sizes | pump location + distance + feature geometry | per-family route template (as in rain-curtain `pipingLayout`) → lengths and fittings; `pipe.py` size-walker; manifold sized for ±2% spread | **pump location**, material |
| 7 | TDH and pump | segments + feature head | `tdh.py` → rank catalog curves: smallest that passes with ≥ 8–10% head margin, NPSH margin at site elevation, duty point inside the BEP window | pump family / brand |
| 8 | Control, bypass, surge | pump vs system | none / VFD / PRV; minimum-flow bypass; arrestor if Joukowsky surge exceeds the rating (`safety.water_hammer`) | VFD for shows / wind |
| 9 | Treatment | circulation + volume | `treatment.filtration_area`, `chemistry.chlorinator_feed`, UV for interactive features | sanitizer type |
| 10 | Lighting and electrical | effects + pumps | fixtures per effect (§1.11); `controls`, `electrical` (FLC, 125%, OCPD, transformer VA) | **colour, DMX vs switched** |
| 11 | Winterization plan | routes + site | §1.12 | — |
| 12 | Outputs | all of the above | §5 | — |

**Where a person must still decide:**
- the aesthetic: nozzle model, sheet look, lighting
- pump location, and vault versus pump room
- brand preference and stock on hand
- the wind setpoints
- whether to accept a warning
- the engineer's stamp on regulated work, always

The tool should present each of these as an `options` list, which `CalcResult` already
supports, with the recommended option first.

### 2.2 What the Digital Rain Curtain app already automates, and how to generalize it

`digital-rain-curtain/src/hydro/presets.js` takes a curtain from "five choices" to a complete
plumbing model:

- **Field presets with "Custom" detection.** The catalogs are `CURTAINS`, `JETS`, `NOZZLES`,
  `VALVES` and `CLIMATES`. `matchPreset` shows the preset whose numbers all match, or "Custom"
  once the designer edits a value.
- **Whole-system kits.** `KITS` covers tabletop, event rental, lobby, stage, atrium and patio.
  Each kit is a bundle of presets plus setup choices. This is the template idea already in
  `water_feature_design.js`'s 18 `WFD_TEMPLATES`, but a kit also sets the *policy* for each
  section, not just the rows.
- **Per-section auto/custom flags with a fixed derivation order.** The sections are `SECTIONS`
  = pump, piping, manifold, control, bypass, surge, basin, and `deriveSetup` runs them in a
  commented order: pipes → manifold → pump → control → bypass → arrestor → basin. A section
  set to `custom` is never overwritten.
- **Full-model candidate ranking.** `rankPumps` runs each pump through the hydraulic model,
  keeps the smallest one that passes, and gives each failure in plain language. A fallback
  picks the least-bad pump by a "miss" score.
- **A plain-language verdict.** `verdict` returns good / warn / bad / info lines, each with
  its remedy: "Pick a stronger pump", "fit a 0.5 L arrestor".

**Generalizing to the Python engine:**
1. Add a `setup` dict to the design: per section, `auto` / a preset key / `custom`.
2. Add a `derive(design)` orchestrator, a sibling of `pipeline.run_spine`, that runs the §2.1
   order and writes only into `auto` sections.
3. Make `select_pump` a ranker over curve objects, with pass/fail reasons.
4. Let `issues.py` turn results into the verdict list. It already produces typed `DesignIssue`
   records, so the verdict is a rendering of those.

The kits become a "start from" catalog per family. One example is "Hotel lobby sheet wall,
8 ft": its kit would set the weir, the look band, a submersible pump and the lighting kit.

---

## 3. Feature-family coverage today vs needed

| Family | Engine today | Biggest missing piece |
|---|---|---|
| Weir / vanishing / waterwall | Francis, edge bands | look → gpm/ft, tolerance check, catch width, trough surge |
| Nozzle arrays / jets | orifice, array sum, k·head | nozzle performance tables; manifold balance |
| Foam / geyser | k = 0.55–0.6 | level band, check valves |
| Laminar | none | ballistic arc from table flow |
| Interactive plaza | regulated-pool path only | Utah IWF ruleset, sequence-based pump sizing, tank |
| Cascades / streams | tiers, Manning channel | GPH/ft look bands, drain-back |
| Pondless / urn | none | reservoir multiplier, kit BOM |
| Rain curtain / screen | separate JS app | strand flow (passive); link to the app (digital) |
| Misting | none | nozzle count × gpm, potable supply |
| Floating aerator | none | HP/acre + pattern selector |
| Lighting | relay sizing | fixtures-per-effect, VA ×1.25, GFCI rule |
| Winterization | none | checklist generator |

---

## 4. Catalog data

### 4.1 What is public, and in what form

| Category | Best public form | Notes |
|---|---|---|
| Commercial pool / fountain pumps | PDF curve charts (digitize 8–12 points) | Pentair EQ ([PDF](https://cdn.shopify.com/s/files/1/0426/5373/files/eq-series-commercial-pump-brochure-english-min.pdf)) and Hayward HCP ([catalog](https://ca.hayward.com/media/wysiwyg/pdf/Tools/2024_LITCRNG24C_CommercialCatalog_lr.pdf)) are charts only. Efficiency curves come "from the factory". |
| Industrial pumps | online selectors | Grundfos Product Center ("export or print" a curve; format unconfirmed) ([guide](https://www.grundfos.com/ca/support/how-to-guides/how-to-get-a-pump-curve-quickly)); Xylem Xylect ([news](https://www.xylem.com/en-ca/making-waves/water-utilities-news/new-xylect-pump-selection-tool-launches/)); Wilo-Select project export as CEF/CEFX ([Wilo](https://wilo.com/ae/en/Solutions-Finder/Selection-and-configuration/Wilo-Select)); Franklin FE Select and Pentair Encompass need a login ([FE Select](https://www.franklinwater.com/resources/fe-select), [Encompass](https://www.pentair.com/Encompass)). Jandy, Sta-Rite, Goulds pool-line formats: not verified. |
| Small fountain / pond pumps | numeric tables on product pages | Aquascape GPH at 0–30 ft ([AquaSurge](https://www.aquascapeinc.com/products/aquasurge-5000-pond-pump)); Little Giant GPH at 1/5/10 ft ([catalog](https://res.cloudinary.com/wholesale-plumbing/image/upload/Documents1/LittleGIANT_580506_Catalog.pdf)) |
| Data standard | HI 50.7 XML (free) | aimed at RFQ/bid exchange; no fountain maker publishes it ([HI](https://pumps.org/resources/electronic-data-exchange)) |
| Nozzles | PDF tables, often image-only | OASE height → GPM/TDH/LPM/bar (above); Roman RPJ height → GPM/head ([PDF](https://www.poolsupplyunlimited.com/Products/Manuals/87032_20191021111136.pdf)); Safe-Rain pressure (m.c.a) → diameter ([PDF](https://saferain.com/wp-content/uploads/2025/07/crystalline_fountain_nozzle_willow-palm-tree-jet.pdf)); Crystal: cut sheet free, CAD and WATERlab behind a login ([Crystal](https://crystalfountains.com/waterlab/)) |
| Lights | spec pages / PDFs | OASE and Wibre give lumens and beam; Pentair and Hayward give W/V only; Crystal LED020 "UL and ULC listed" ([buildings.com](https://buildings.com/products/article/10190513/led020-fountain-light-by-crystal-fountains)). Confirm UL 676 in UL Product iQ |
| Filters | best structured: area, design GPM, NSF 50 | Pentair Triton II ([PDF](https://cdn.shopify.com/s/files/1/0426/5373/files/triton-ii-sand-filter-clearpro-technology-brochure-english.pdf)); Hayward HCF ([page](https://www.hayward.com/hcf-series-36-laminated-sand-filter-2-5in-hcf236c.html)); Jandy CS commercial 0.375 GPM/ft² ([PDF](https://cdn.shopify.com/s/files/1/0716/0591/1827/files/sl6256.pdf)); cross-check in the [NSF listings](https://info.nsf.org/Certified/Pools/Listings.asp) |
| Floating aerators | PDF tables: HP, pattern H × D, GPM, V | Otterbine ([download centre](https://www.otterbine.com/resource-center/download-center/product-specifications/)); Kasco quick specs ([7.3JF](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/7.3jf_quick_spec.pdf)); Airmax ([manual](https://cdn.shopify.com/s/files/1/0723/8539/7041/files/Airmax_EcoSeries_1-2_HP_Floating_Fountain_Manual.pdf)); Scott ([DA-20](https://pondhaven.com/pages/da-20-specs)) |
| Distributor feeds | commercial fields only | POOLCORP/Pool360 API: inventory, UPC, price; no hydraulics ([Flxpoint](https://help.flxpoint.com/kb/general-supplier-integrations/poolcorp-as-a-source)). Heritage: nothing public |

### 4.2 Is a scraped or hand-entered catalog realistic?

**Hand-entered, yes. Scraped, no.**
- Sapphire's working catalog is probably a few hundred SKUs: the pumps, nozzles, lights,
  filters and aerators it actually specifies and stocks.
- Filters and aerators are already tabular. Nozzles and small pumps print their numbers.
- Commercial pump curves need about 8–12 digitized (flow, head) points, plus efficiency and
  NPSHr where published. That is roughly 10–15 minutes per curve with a plot digitizer.
- Retailer copies of specs often disagree with the manufacturer (Kasco, Scott and Triton II
  all do), so the manufacturer's sheet is the source of truth.
- Bare performance facts are not copyrightable
  ([Feist v. Rural](https://www.law.cornell.edu/supremecourt/text/499/340)). Copying the PDFs
  or bulk-scraping against site terms is the risk. (Not legal advice.)
- Two cheap shortcuts: ask the Pentair, Hayward, Crystal and OASE reps for curve data in Excel,
  and store the cut sheet as an attachment on each record.

### 4.3 Recommended catalog schema

Keep the **ERPNext Item** as the anchor, so that BOM, price, stock and purchasing all work.
Hang performance data off it in child doctypes that the engine reads as plain dicts. The
existing `Nozzle Profile`, `Pump Curve Point` and Item custom fields are a subset of this
schema, so the change is to extend them, not replace them.

```
Item (existing) + custom fields
  equipment_class    Select: Pump | Nozzle | Light | Filter | Aerator | Valve | Controller | Fitting kit
  manufacturer, model_no, cut_sheet (Attach), spec_source_url, spec_revision
  spec_verified_by (Link User), spec_verified_on (Date)
  electrical:        volts, phase, hz, fla_amps, watts, hp, requires_gfci, ul_listing (e.g. "UL 676")
  physical:          inlet_size, outlet_size, weight_lb, min_submergence_in, install (submersible/dry)

Pump Curve (new parent, 1 Item : n curves; one per impeller / speed)
  item, impeller_dia_in, rpm, speed_pct, motor_eff_pct, max_flow_gpm, shutoff_head_ft
  points (child Pump Curve Point, extended):
    flow_gpm, head_ft, efficiency_pct, npshr_ft, power_bhp
  source: "datasheet" | "digitized" | "rep-supplied" | "generic estimate"

Nozzle Performance (child of Nozzle Profile, extended)
  head_ft (or psi), flow_gpm, display_height_ft, display_diameter_ft, throw_ft,
  water_depth_over_nozzle_in (for level-sensitive types)
  + on the profile: nozzle_type, connection_size, level_sensitive (Check),
    min_max_depth_in, recommended_light_beam

Light Fixture (fields on Item)
  lumens, beam_deg, cct_or_rgb, control (switched | DMX | proprietary), input_voltage,
  max_depth_ft, ip_rating

Filter (fields on Item)  media, effective_area_sqft, design_rate_gpm_sqft, rated_gpm,
                         backwash_gpm, nsf50_listed
Aerator (fields on Item) hp, pattern_name, pattern_height_ft, pattern_diameter_ft, gpm,
                         o2_lb_per_hp_hr, min_depth_ft, cord_lengths
```

**Design rules for the schema:**
1. Curves and tables are **points, not formulas**. The engine already interpolates
   (`pump.head_at_flow`, `tdh._interp_curve`).
2. Every record carries **provenance** (source URL, revision, who verified it). That matches
   the `CalcResult.inputs[*].source` philosophy, so a design can say "pump curve digitized from
   EQ brochure rev X".
3. Use a `generic estimate` flag, like the existing `Nozzle Profile.is_generic_estimate`, so
   the tool can size a design before a model is chosen. This mirrors the rain-curtain app's
   typical-class pump curves, and the issues layer should warn whenever a generic record
   reaches a submittal.

---

## 5. Outputs the tool should produce

| Output | Content | Exists today? |
|---|---|---|
| Verdict | good / warn / bad lines with remedies, per section | partly (`issues.py` readiness) |
| Bill of materials | equipment Items with qty from the design; pipe by size and material with length plus waste; fittings from route templates; consumables; a per-family kit BOM. Push to an ERPNext Quotation or Material Request | no |
| Equipment schedule | tag, model, duty, electrical, cut-sheet link | yes (Schedules print format) |
| Piping schedule | segment, size, material, length, GPM, velocity, head loss | partly |
| Pump duty-point chart | pump curve(s) vs system curve, duty point, BEP band, NPSH margin, VFD speeds. SVG from the engine, like `drawings.py` | no |
| Plan-check calc sheet | existing "Design Calculations" print format, plus the Utah IWF checklist and NEC 680 checklist | yes for regulated pools and spas |
| Budgetary cost | BOM × Item valuation/price list + labour assemblies (hours per family, per sq ft of basin, per fixture) from Sapphire's own timesheets, ± range | no |
| O&M per year | **water:** evaporation + splash + backwash + drain/refill, priced at SLC commercial **$3.53/ccf (748 gal)** for FY26 ([SLC](https://www.slc.gov/utilities/fy26-what-new-rates-mean/)). **Power:** kW × hours × rate, using Rocky Mountain Power's proposed average non-residential **9.50¢ → 10.49¢/kWh** (2025 → 2026) ([Utah News Dispatch](https://utahnewsdispatch.com/?p=4025)) until the approved tariff is confirmed. **Chemicals:** from `chemistry.py`. **Plus** lamp, valve and filter replacements | partly (`workbook.electric_cost`) |
| Winterization / start-up checklist | per site, from the routes and equipment | no |
| Schematic | circulation and electrical one-line | yes (`drawings.py`) |

---

## 6. Prioritized backlog

Weighted for Sapphire's mix of work:
- **Design:** speed and credibility on proposals.
- **Build:** a correct BOM and procurement.
- **Service:** O&M and winterization.
- **Rental:** portable kits.
- **Utah factors:** health-department submittals, freezing winters, hard water, altitude.

Effort assumes an agent writes the code and a person reviews it:
- **S** = 1–3 days
- **M** = 1–2 weeks
- **L** = 3–6 weeks

| # | Item | Why it pays for Sapphire | Effort | Depends on |
|---|---|---|---|---|
| 1 | **Nozzle performance tables + replace the `JET_EFFICIENCY` constant** (height → gpm/head by interpolation; enter the 20–30 nozzles Sapphire uses most) | Jets are in most designs; the current k = 0.90 under-sizes pumps | S–M | schema §4.3 |
| 2 | **Pump curve catalog v2 + ranker + duty-point chart** (efficiency, NPSHr, speed; smallest passing pump with margin and NPSH at site elevation) | Pump choice is the costliest single decision and the most common design error | M | #1 not required |
| 3 | **Basin / water-in-transit / surge calculator** (transit volume, drain-back, HI submergence, freeboard rule, autofill sizing) | The most common field failure is a basin that overflows at shutdown or vortexes when running; it applies to every family | M | — |
| 4 | **Auto-derive orchestrator + per-section auto/custom flags + kits** (the §2.2 generalization) | This is what makes the designer "do the work": few inputs → complete design | L | #1–3 |
| 5 | **BOM + budgetary cost from Item prices and labour assemblies** → Quotation | Turns a design into a proposal; build and procurement use it directly | M | #4 for full value; can start from today's rows |
| 6 | **Utah interactive water feature ruleset** (R392-302-41 checklist, sequence-based pump sizing, 3-minute tank, UV) | Splash pads are regulated, high-value public work in Utah; plan-check speed matters | M | #3 |
| 7 | **Splash/wind setback check + wind-control recommendation** | Cheap; prevents the classic soaked-plaza complaint | S | #1 |
| 8 | **O&M annual figures** (water, power, chemicals, parts) on the design and on service contracts | Feeds maintenance agreements and owner conversations | S | rate settings |
| 9 | **Winterization / spring start-up plan generator** | Utah seasonal service revenue; reduces freeze damage | S | routes from #4 (manual input works first) |
| 10 | **Lighting calculator** (fixtures per effect, beam choice, VA ×1.25, NEC 680 GFCI flag, voltage drop) | Every job has lights; ties into the existing panel sizing | S–M | light fields §4.3 |
| 11 | **Weir look → gpm/ft + tolerance + catch-width checks** | Refines a family the engine already covers | S | — |
| 12 | **Floating aerator selector** (HP/acre, pattern, cable voltage drop) | HOA and commercial pond service work; fast quotes | S | aerator fields |
| 13 | **Cascade / stream / pondless kit calculators** (GPH/ft bands, reservoir multipliers, kit BOM) | Small jobs quoted quickly | S | #5 |
| 14 | **Valve manifold and sequencing** (simultaneous-open analysis, valve Cv, hammer) for plazas and digital curtains | Interactive and show work; reuse the rain-curtain manifold model | M | #2, #6 |
| 15 | **Rental kit configurator** (portable basin, generator size, set-up checklist; the rain-curtain app as the first kit) | Rental line; the app already does most of it for curtains | M | #5 |
| 16 | Laminar arc, passive rain/lace curtains, misting | Niche | S each | #1 |

**Suggested order:** 1 → 2 → 3 → 7 → 11 (accuracy on what exists), then 5 and 8 (money),
then 4 (the big automation step), then 6, 9, 10, 12 and the rest.

**The data work runs in parallel and is the long pole:** entering about 30 nozzles, 20 pump
curves, 15 lights, 10 filters and 10 aerators is roughly 3–5 person-days of careful
transcription, with verification.

---

## 7. Open questions and things to verify

- **NEC edition.** Check which NEC edition Utah currently enforces, and confirm the 680.51 and
  680.23(A)(2) wording against it. The sources above are trade articles on the 2011–2020
  editions.
- **MAHC 2023.** Get the verbatim text for interactive venues: UV dose, tank sizing, and
  whether 100% UV treatment was adopted. Utah's own rule governs in Utah, but other states
  differ.
- **R392-302-35(8) and Table 3.** These are referenced by the IWF rule but were not read.
  Read them before writing #6.
- **Missing house rules.** There are no public sources for foam-jet submergence, freeboard,
  watts per foot of jet, wind mph setpoints, liner allowance or splash loss. These should
  become **Sapphire house rules**, chosen by the designers and stored as named, editable
  constants with a citation of "Sapphire standard, <date>".
- **Rocky Mountain Power rates.** The approved commercial tariff, as opposed to the rates
  proposed in the rate case.
