# Fountain Designer catalog seed data (research draft)

Research output, 2026-11-01. Cited pump curves, nozzle tables and underwater light data, gathered from public
manufacturer documents for the Fountain Designer rebuild in `erpnext_enhancements/water_engineering/`. Nikolas chose
on 2026-11-01 to seed the catalogs from public manufacturer data, cited per row, and to approve rows before anything
is seeded.

**Status: nothing here has been seeded or wired into the app.** These files are review material. No existing file
was changed. Each row carries the document it came from, so you can check any number against its source page.

| File | What it holds |
|---|---|
| [`pumps.json`](pumps.json) | Pump models with a head-flow curve (usually 8 to 12 points), electrical data, ports and listings |
| [`nozzles.json`](nozzles.json) | Nozzle, jet and floating-aerator performance tables (pressure or head → flow → display height) |
| [`lights.json`](lights.json) | Underwater fixtures: watts, voltage, lumens, beam, listing |
| [`jurisdictions.json`](jurisdictions.json) | Adopted codes, pool and interactive-feature regulation, design-city climate and licensing for the 48 contiguous states; see [Jurisdiction table](#jurisdiction-table-jurisdictionsjson). Checked by [`jurisdictions-validate.py`](jurisdictions-validate.py) |

**At a glance:** 169 pump records, every one with a curve. 155 were read from vector charts, 7 from published tables and 7 visually from raster charts, and 166 of them have 6 or more points. There are 176 nozzle and aerator records, 152 of them with a table of 4 or more rows, and 35 underwater light records.

## How the numbers were obtained

Three methods were used, and every row states which one in `confidence` and `method`:

1. **`table`**: the value was copied from a table the manufacturer printed (`error_band_pct: 0`). This covers all
   nozzle rows, Hayward HCP 2000, and the electrical data on every pump.
2. **`digitized` / `vector-path`**: most pump curves. Manufacturer PDFs draw their charts as vector paths, so the
   curve was read from the PDF's own drawing instructions, not from a picture. The chart was calibrated on its
   gridlines, and the drawn line was sampled at round flows (Bézier segments flattened first). The result was then
   compared with a rendered image of the chart. The error is the chart's own drawing accuracy plus calibration, about
   **±2 %** of full scale.
3. **`digitized` / `visual` or `visual-raster`**: the chart was a raster image, read either by pixel colour tracing
   on gridlines detected in the image or by eye from a high-resolution render. Pentair SuperFlo VST is ±3 %, Hayward
   HCP 2500/3000 ±5 %.

Rules followed throughout:

- **No invented values.** A field the document does not publish is `null`, and the reason is in `notes`.
  Efficiency, NPSHr and power appear only where the maker publishes them, which in practice means the industrial
  lines (Grundfos, Goulds). Pool-industry literature publishes head-flow only.
- **`eff_pct_derived`** (Grundfos only) is *computed* as Q·H/(3960·BHP) from the published power curve. It is a
  cross-check, not a published figure.
- **Variable-speed pumps:** `curve` is the full-speed curve (`curve_speed_rpm`, or 100 % for IntelliFlo3, which
  gives no rpm). The other speeds the document draws are in `extra_curves`. Use the affinity laws between them.
- **Where documents disagree**, the newer one wins and the disagreement is written in `notes`. Two examples:
  Pentair's 2024 catalog against the 2018 Max-E-ProXF brochure, and two Hayward Super Pump sheets (the second is
  kept as `alt_curves`).
- **Units:** US gpm, feet of water, psi, °F, inches. Metric tables were converted exactly: 1 m = 3.28084 ft,
  1 l/min = 0.264172 gpm, 1 m³/h = 4.40287 gpm, 1 bar = 14.5038 psi, 1 m w.c. = 1.42233 psi. The as-published values
  are kept beside them in `rows_as_published`.
- **Hosting:** many manufacturer PDFs are served from distributor CDNs (Shopify, cloudfront, poolsupplyunlimited,
  inyopools). Those were used only when the PDF was the manufacturer's own document, with its literature number on
  the page, and the source title says where it was hosted.

## Schema

### `pumps.json`: a list of pump objects

| Field | Meaning |
|---|---|
| `id` | Stable slug, unique across the file |
| `manufacturer`, `series`, `model` | As published |
| `part_no` | List of published part or SKU numbers; variants are annotated, e.g. `"340031 (EQK500 3-ph ODP)"` |
| `hp`, `total_hp`, `service_factor` | Rated HP; total HP (THP) where the maker rates that way (DOE-era pool pumps) |
| `voltage`, `phase` | Lists of supported voltages and phases |
| `fla_amps` | Object keyed by voltage, or `"voltage phase"`. Values are as printed, so ranges stay as strings |
| `rpm`, `variable_speed`, `speed_range_rpm`, `curve_speed_rpm` | Fixed speed, or VS with min/max and the speed the main curve is drawn at |
| `max_flow_gpm`, `shutoff_head_ft` | End of the published curve and head at zero flow. `null` when the chart does not reach Q = 0 |
| `curve` | `[{flow_gpm, head_ft, eff_pct?, npshr_ft?, bhp?, kw?, eff_pct_derived?}]`, ascending flow |
| `extra_curves` | VS pumps: `[{speed_rpm or speed_pct, label?, curve}]` |
| `alt_curves` | A second published curve for the same model that disagrees with the primary one |
| `impeller_dia_in` | Industrial pumps: the impeller trim the curve belongs to |
| `suction_in`, `discharge_in`, `connection_type` | Port sizes in inches, and the connection type |
| `max_water_temp_f` | Only where published |
| `listing` | Certifications exactly as the document states them (NSF/ANSI 50, UL 1081, ETL, NSF/ANSI 61 …) |
| `source` | `[{url, title, page, accessed, what}]`. `page` is the PDF page; printed catalog page numbers are in `title` |
| `confidence`, `method`, `error_band_pct` | See *How the numbers were obtained* |
| `notes` | Caveats, typos found in the source, and what was not published |
| `research_file` | Which research batch produced the row (provenance only) |

**Mapping to the existing fields** (`water_engineering/setup.py`). `curve` maps to the `custom_pump_curve` table
(`Pump Curve Point`), which stores `flow_gpm` and `head_ft` only. Efficiency, NPSHr, power, speed and every
`extra_curves` set are dropped unless that child table grows. The other fields:

- `max_flow_gpm` → `custom_rated_gpm`, which setup.py describes as "Max flow used by the … pump selector".
- `shutoff_head_ft` → `custom_rated_tdh_ft` ("Max head from the pump curve").
- `hp` → `custom_pump_hp`.

Three fields hold one value where a row may hold several, so a row that covers several variants becomes several
Items, one per part number and supply:

- `custom_pump_phase` is a Select of `1` / `3`, but `phase` is a list.
- `custom_pump_voltage` is Data, but `voltage` is a list.
- `custom_pump_fla_amps` is a single Float, but `fla_amps` is per voltage.

`part_no` already lists the variants with their supply.

### `nozzles.json`: a list of nozzle objects

| Field | Meaning |
|---|---|
| `id`, `manufacturer`, `model`, `part_no` | As published |
| `type` | `orifice jet`, `foam/aerating`, `laminar`, `cascade/fan`, `geyser`, `bubbler`, `spray ring`, `water screen` or `other` |
| `inlet_size`, `orifice_in` | Connection as printed (thread standard conflicts are noted) |
| `discharge_coefficient` | Only if published. **No maker publishes one**, so this is `null` throughout |
| `min_submergence_in`, `water_level_below_top_in` | Water-level requirement for level-dependent jets (geysers, cascades, foam), where the sheet gives one |
| `rows` | `[{pressure_psi, head_ft, flow_gpm, height_ft, spread_ft?, throw_ft?, angle_deg?}]`, converted to US units |
| `rows_as_published` | The same rows in the document's own units and labels |
| `units_as_published` | What was converted, and how |
| `source`, `confidence`, `method`, `error_band_pct`, `notes` | As for pumps |

Floating aerators (Otterbine, Kasco) are recorded as one "nozzle" per unit and spray pattern. HP, voltage and GPM are
in the rows or notes.

**Mapping.** `Nozzle Profile` holds one rated point (`rated_gpm`, `rated_head_ft`) and an optional
`discharge_coefficient`. Seeding one point per row would throw away the height column, the cheapest accuracy win the
engine audit identified. A child table of rows is the better fit.

### `lights.json`: a list of light objects

`id`, `manufacturer`, `model`, `part_no`, `watts`, `voltage`, `lumens`, `beam_deg`, `color`, `listing`,
`ip_rating`, `max_depth_ft`, `source`, `notes`. `lumens` is `null` where the maker publishes watts only, which is
common for pool lights.

## Coverage by manufacturer

| Manufacturer | Pumps | with curve | Nozzles | with table (4+ rows) | Lights | Documents cited |
|---|---:|---:|---:|---:|---:|---:|
| Pentair | 48 | 48 | 0 | 0 | 4 | 5 |
| Sta-Rite (Pentair) | 37 | 37 | 0 | 0 | 0 | 3 |
| Hayward | 28 | 25 | 0 | 0 | 4 | 14 |
| Jandy | 25 | 25 | 0 | 0 | 16 | 11 |
| Grundfos | 12 | 12 | 0 | 0 | 0 | 1 |
| Goulds (Xylem) | 19 | 19 | 0 | 0 | 0 | 2 |
| OASE | 0 | 0 | 33 | 33 | 1 | 8 |
| Safe Rain | 0 | 0 | 115 | 105 | 0 | 28 |
| Roman Fountains | 0 | 0 | 2 | 2 | 0 | 1 |
| Atlantic Water Gardens | 0 | 0 | 7 | 7 | 0 | 1 |
| Otterbine | 0 | 0 | 4 | 0 | 0 | 1 |
| Kasco | 0 | 0 | 15 | 5 | 2 | 13 |
| WIBRE | 0 | 0 | 0 | 0 | 8 | 4 |
| **Total** | **169** | **166** | **176** | **152** | **35** | |

## Pump series and how each was read

| Manufacturer | Series | Records | Method | Error band |
|---|---|---:|---|---|
| Pentair | EQ Series commercial | 4 | digitized / vector-path | 2 % |
| Pentair | EQ Series WaterFall | 2 | digitized / vector-path | 2 % |
| Pentair | WhisperFloXF | 6 | digitized / vector-path | 2 % |
| Sta-Rite (Pentair) | Max-E-ProXF | 6 | digitized / vector-path | 2 % |
| Pentair | WhisperFlo | 11 | digitized / vector-path | 2 % |
| Pentair | SuperFlo | 6 | digitized / vector-path | 2 % |
| Pentair | WhisperFloXF VS | 1 | digitized / vector-path | 2 % |
| Sta-Rite (Pentair) | Max-E-ProXF VS | 1 | digitized / vector-path | 2 % |
| Pentair | SuperFlo VST | 1 | digitized / visual-raster | 3 % |
| Pentair | IntelliFlo3 VSF | 2 | digitized / vector-path | 2 % |
| Sta-Rite (Pentair) | Max-E-Pro (P6 / 5P6R) | 16 | digitized / vector-path | 2 % |
| Pentair | C Series commercial bronze (CH high head) | 5 | digitized / vector-path | 2 % |
| Pentair | C Series commercial bronze (CM medium head) | 3 | digitized / vector-path | 2 % |
| Pentair | SuperFlo VS (discontinued) | 1 | digitized / vector-path | 2 % |
| Sta-Rite (Pentair) | CSP/CCSP commercial self-priming | 8 | digitized / vector-path | 2 % |
| Pentair | D-Series cast iron self-priming (high head) | 2 | digitized / vector-path | 2 % |
| Pentair | D-Series cast iron self-priming (medium head) | 2 | digitized / vector-path | 2, 5 % |
| Sta-Rite (Pentair) | SuperMax | 6 | digitized / vector-path | 2 % |
| Pentair | WhisperFlo VST | 1 | digitized / vector-path | 2 % |
| Pentair | IntelliFlo VS+SVRS | 1 | digitized / vector-path | 2 % |
| Hayward | HCP 3000 Series | 5 | digitized / visual | 5 % |
| Hayward | HCP 2500 Series (TriStar VS C) | 1 | digitized / visual | 5 % |
| Hayward | HCP 4000 Series | 4 | digitized / vector-path | 2 % |
| Hayward | HCP 2000 Series (TriStar 3-phase) | 7 | table / table | 0 % |
| Hayward | TriStar VS (900) | 1 | digitized / vector-path | 2 % |
| Hayward | TriStar VS 950 | 1 | digitized / vector-path | 2 % |
| Hayward | Super Pump VS 700 | 1 | digitized / vector-path | 2 % |
| Hayward | Super Pump VS | 2 | digitized / vector-path | 2 % |
| Hayward | Super Pump (SP2600, high-efficiency) | 4 | digitized / vector-path | 2 % |
| Jandy | Stealth | 7 | digitized / vector-path | 2 % |
| Jandy | FloPro (single- and two-speed) | 5 | digitized / vector-path | 2 % |
| Jandy | VS FloPro | 5 | digitized / vector-path | 2 % |
| Jandy | ePump | 3 | digitized / vector-path | 2 % |
| Hayward | Super Pump 700 (TEFC) | 2 | digitized / vector-path | 2 % |
| Jandy | PlusHP (single- and two-speed) | 5 | digitized / vector-path | 2 % |
| Grundfos | NBS (split-coupled end-suction, ANSI, fixed speed) | 12 | digitized / vector-path | 2 % |
| Goulds (Xylem) | e-SHX Smart Pump (316 SS end-suction with Hydrovar X drive) | 9 | digitized / vector-path | 2 % |
| Goulds (Xylem) | 3656LH / 3756LH low-head end suction (cast iron, bronze fitted) | 10 | digitized / vector-path | 2 % |

## Item codes

Since v1.775.0 a seeded Item's code is the manufacturer's part number, read from `part_no`: the first
entry, without its trailing note (`"340030 (EQ500 1-ph)"` gives `340030`), and the first of a ` / ` set of
alternatives. A row with no `part_no`, a range (`"7.3JF100 to 7.3JF400 (cord length variants)"`,
`"601000-601004"`), or a part number another row also has (once case and punctuation are ignored) is coded
by its `model` as written, commas dropped. So keep the real part number first in `part_no`, and put
anything else (the variant, the supply, "order no. on sheet") in the parenthetical note after it. To code
a row some other way, or to attach it to an Item Sapphire already has, give its `approved.json` entry an
`item_code`. If the loader already created its own Item for that row, `rename_catalog_items` merges that
Item into Sapphire's. Three rows are attached this way: `pentair-whisperfloxf-vs-022035`,
`pentair-superflo-vst-342002` and `pentair-superflo-348190`, to Items `022035`, `342002` and `348190`.
The full rule is in `erpnext_enhancements/water_engineering/README.md`, Seeding the catalog.

## Approval checklist

Tick a manufacturer once its rows are approved for seeding. Approving means: the cited documents are the ones you want to rely on, the spot checks below pass, and the terms note for that maker is acceptable.

### Pentair (52 rows)

- [ ] Approve Pentair rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Pentair EQ Series High Performance Commercial Pump brochure (Pentair PDF, hosted on a distributor's Shopify CDN)](https://cdn.shopify.com/s/files/1/0426/5373/files/eq-series-commercial-pump-brochure-english-min.pdf)
  - [Pentair Pool 2024 Product Catalog - Pumps section (Pentair catalog PDF, hosted on a distributor's Shopify CDN)](https://cdn.shopify.com/s/files/1/0621/5994/5981/files/2024_Pentair_Pool_Product_Catalog_-_Pumps.pdf)
  - [SuperFlo VST / SuperFlo VS / Sta-Rite SuperMax VS Installation and User's Guide P/N 356292 (Pentair PDF hosted on a distributor CDN)](https://cdn.shopify.com/s/files/1/0716/0591/1827/files/356292-superflovs-superflovst-supermaxvs-owners-manual.pdf)
  - [Pentair SuperFlo VS Variable Speed Pump brochure P1-234 10/13 (Pentair PDF hosted by inyopools.com)](https://images.inyopools.com/cloud/documents/superflo-vs-brochure.pdf)
  - [IntelliBrite Architectural Series Pool and Spa Lighting specifications grid (2024)](https://www.pentair.com/assets/intellibrite-architectural-series-specifications-grid-pdf)

### Sta-Rite (Pentair) (37 rows)

- [ ] Approve Sta-Rite (Pentair) rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Pentair Pool 2024 Product Catalog - Pumps section (Pentair catalog PDF, hosted on a distributor's Shopify CDN)](https://cdn.shopify.com/s/files/1/0621/5994/5981/files/2024_Pentair_Pool_Product_Catalog_-_Pumps.pdf)
  - [Sta-Rite Max-E-ProXF High Performance Pump brochure, P1-134 09/18 (pentair.com)](https://flowandfiltrationsolutions.pentair.com/content/dam/extranet/nam/pentair-pool/commercial/pumps/max-e-pro-xf/max-e-proxf-high-performance-pump-english.pdf)
  - [Sta-Rite Max-E-ProXF VS Commercial Variable Speed Pump - 5HP brochure (Pentair PDF hosted on a distributor CDN)](https://cdn.shopify.com/s/files/1/0716/0591/1827/files/maxeproxf-vs-pump-brochure.pdf)

### Hayward (32 rows)

- [ ] Approve Hayward rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Hayward Commercial Aquatics catalog 2024 (LITCRNG24C)](https://ca.hayward.com/media/wysiwyg/pdf/Tools/2024_LITCRNG24C_CommercialCatalog_lr.pdf)
  - [Hayward HCP 3000 Series brochure LITHCP300017C (2017)](https://cdn.shopify.com/s/files/1/0426/5373/files/LITHCP300017C_HCP_3000_Brochure.pdf)
  - [Hayward HCP 4000 Series sell sheet LITCHCP400017 (2017)](https://cdn.shopify.com/s/files/1/0426/5373/files/LITCHCP400017_HCP_4000_Sell_Sheet.pdf)
  - [Hayward TriStar VS literature LITTRISTVS22 (2022)](https://commerce.hayward-pool-assets.com/magento/akeneo/W3SP3202VSP/literature/LITTRISTVS22_1fa0.pdf)
  - [Hayward TriStar VS brochure (2017 edition), cross-check of the 3450 RPM curve](https://www.propools.com/Manuals/64521_202211162354.pdf)
  - [Hayward 1.85 THP VS pump family owner's manual IS3202VSP2B](https://commerce.hayward-pool-assets.com/magento/akeneo/SP3202VSPND/manuals/IS3202VSP2B_RevA_6773.pdf)
  - [Hayward TriStar VS 950 sell sheet eLITTSVS95020 (2020)](https://cdn.shopify.com/s/files/1/0426/5373/files/TriStar-VS-950-eLITTSVS95020.pdf)
  - [Hayward Super Pump VS 700 sell sheet eLITSPVS70020 (2020)](https://cdn.shopify.com/s/files/1/0426/5373/files/Super-Pump-VS-700-eLITSPVS70020.pdf)
  - [Hayward Super Pump VS sell sheet eLITSUPER21 (2021)](https://cdn.shopify.com/s/files/1/2473/3280/files/Hayward_SuperPumpVS_eLITSUPER21.pdf)
  - [Hayward Super Pump high-efficiency literature LITSPHE23 (2023)](https://commerce.hayward-pool-assets.com/magento/akeneo/SP2600X5/literature/LITSPHE23_ea6c.pdf)
  - [Hayward 'Super Pump Performance Curves' (product-page PDF)](https://commerce.hayward-pool-assets.com/magento/akeneo/SP2600X5/pump_performance_curves/Super_Pump_Performance_Curves_dda9.pdf)
  - [Hayward Super Pump 700 TEFC sell sheet LITSP70021C (2020)](https://cdn.shopify.com/s/files/1/0426/5373/files/LITSP70021C_SuperPump700_TEFC-SellSheet_Bilingual.pdf)
  - [Hayward ColorLogic LED pool and spa light installation and operation manual 092153A Rev B](https://commerce.hayward-pool-assets.com/magento/akeneo/W3SP0527LED100/manuals/092153ARevB_e052.pdf)
  - [Hayward ColorLogic 4.0 LED Pool Light W3SP0527LED100 product page](https://www.hayward.com/colorlogic-4-0-led-pool-light-120v-100-ft-cord-plastic-trim-open-line-w3sp0527led100.html)

### Jandy (41 rows)

- [ ] Approve Jandy rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Jandy Stealth pump spec sheet 8342_SL (c) 2021 Zodiac Pool Systems LLC](https://www.poolsupplyunlimited.com/Products/Manuals/104204_2021122116125.pdf)
  - [Jandy FloPro single- and two-speed pump spec sheet 7352_SL (c) 2021 Zodiac Pool Systems LLC](https://cdn.shopify.com/s/files/1/0028/0992/1606/files/7352_SL_FloProPump.pdf)
  - [Jandy VS FloPro 0.85 HP / 1.65 HP spec sheet 2241_SL (c) 2021 Zodiac Pool Systems LLC](https://www.poolsupplyunlimited.com/Products/Manuals/145794_20211210101629.pdf)
  - [Jandy VS FloPro 1.3 HP spec sheet 26650.1_SL (c) 2023 Zodiac Pool Systems LLC](https://www.poolsupplyunlimited.com/Products/Manuals/189434_20231189297.pdf)
  - [Jandy VS FloPro 2.7 HP spec sheet 3538_SL (c) 2021 Zodiac Pool Systems LLC](https://www.poolsupplyunlimited.com/Products/Manuals/139363_2021124131852.pdf)
  - [Fluidra 2023 Catalog excerpt: VS FloPro 3.8 HP VS Pump (VSFHP3802A(S))](https://www.poolsupplyunlimited.com/Products/Manuals/189437_2023118123449.pdf)
  - [Jandy ePump 2.2 & 2.7 HP spec sheet 1765_SL (c) 2021 Zodiac Pool Systems LLC](https://cdn.poolsupplyunlimited.com/Products/Manuals/185163_202222115547.pdf)
  - [Jandy ePump 3.8 HP brochure 82540_SL (c) 2023 Zodiac Pool Systems LLC](https://poolimages.blob.core.windows.net/insite/Product/Documents/TL/D-/10/TLD-10-0012-bro.pdf)
  - [Jandy PlusHP single- and two-speed pump spec sheet 4602_SL (c) 2021 Zodiac Pool Systems LLC](https://www.poolsupplyunlimited.com/Products/Manuals/104220_202112138311.pdf)
  - [Jandy Commercial White LED Lights sell sheet (80934 SL)](https://www.jandy.com/-/media/zodiac/global/downloads/jandy/lighting/80934_sl_led_whitelights.pdf?rev=2cc087048c844fd29829dd94f55fbfe5%3Fforce%3D1)
  - [Jandy Infinite WaterColors LED lights sell sheet (56042 SL)](https://www.jandy.com/-/media/zodiac/global/downloads/jandy/water-features/56042slinfinitewatercolors.pdf?rev=55d5513e7a12461494c4a68a61e6ed31%3Fforce%3D1)

### Grundfos (12 rows)

- [ ] Approve Grundfos rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Grundfos Data Booklet NBS, NBSE, 60 Hz ANSI (manufacturer PDF hosted by d1pkofokfruj4.cloudfront.net)](https://d1pkofokfruj4.cloudfront.net/media/upload/resource/v/Grundfos-NBS-NBSE-End-Suction-Pumps-Data-Booklet.pdf)

### Goulds (Xylem) (19 rows)

- [ ] Approve Goulds (Xylem) rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Goulds Water Technology e-SHX Series Smart Pump Technical Brochure GWTe-SHXTB 4000013 R3, Feb 2024 (manufacturer PDF hosted by d1pkofokfruj4.cloudfront.net)](https://d1pkofokfruj4.cloudfront.net/media/upload/resource/z/GOULDS-e-SHX-Smart-Pump-Technical-Brochure-gwte-shxtb--4000013-r3.pdf)
  - [Goulds Water Technology 3656/3756LH Technical Brochure B3656LH (manufacturer PDF hosted by d1pkofokfruj4.cloudfront.net)](https://d1pkofokfruj4.cloudfront.net/media/upload/resource/r/Goulds-Pump-3656-3756-LH-Performance-Curves-Datasheet.pdf)

### OASE (34 rows)

- [ ] Approve OASE rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [OASE Geyser Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Geyser_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Cascade Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Cascade_Fountain_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Frothy Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Frothy_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Comet Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Comet_Brass_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Fan Jet Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Fan_Jet_Fountain_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Vulcan Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Vulcan_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE Finger Jets Nozzle specification sheet](https://cdn.shopify.com/s/files/1/2394/4493/files/Oase_Finger_Jets_Nozzles_Specifications_Sheet_PDF.pdf)
  - [OASE ProfiLux LED 1100/01 data sheet v2.3 (01.09.2014)](https://cdn.poolsupplyunlimited.com/Products/Manuals/154737_2015916114834.pdf)

### Safe Rain (115 rows)

- [ ] Approve Safe Rain rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Safe Rain Cascade jet datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_cascade-jet.pdf)
  - [Safe Rain Snowy jet datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_snowy-jet.pdf)
  - [Safe Rain Snowy fir jet datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_snowy-fir-jet.pdf)
  - [Safe Rain Foamy column datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_foamy-column-jet.pdf)
  - [Safe Rain Bubbler datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_bubbler.pdf)
  - [Safe Rain Palm tree jet datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_palm-tree-jet.pdf)
  - [Safe Rain Willow jet datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_willow-jet.pdf)
  - [Safe Rain Willow-Palm tree datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_willow-palm-tree-jet.pdf)
  - [Safe Rain Lance Jet I datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_lance-jet-i.pdf)
  - [Safe Rain Lance Jet II datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_lance-jet-ii.pdf)
  - [Safe Rain Compact crystal jet datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_compact-crystal-jet.pdf)
  - [Safe Rain Geyser jet datasheet](https://saferain.com/wp-content/uploads/2026/10/frothy_fountain_nozzle_geyser-jet.pdf)
  - [Safe Rain Crown of jets datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_crown-of-jets.pdf)
  - [Safe Rain Fan of jets datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_fan-of-jets.pdf)
  - [Safe Rain Large fan of jets datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_large-fan-of-jets.pdf)
  - [Safe Rain Laminar fan datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_laminar-fan.pdf)
  - [Safe Rain Lance Jet III datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_lance-jet-iii.pdf)
  - [Safe Rain Hollow jet datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_hollow-jet.pdf)
  - [Safe Rain Special crowns datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_special-crowns.pdf)
  - [Safe Rain Special crowns with fixed jets datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_special-crowns-with-fixed-jets.pdf)
  - [Safe Rain Spraying jet datasheet](https://saferain.com/wp-content/uploads/2026/10/spray_fountain_nozzle_spraying-jet.pdf)
  - [Safe Rain Spraying clouds datasheet](https://saferain.com/wp-content/uploads/2026/10/spray_fountain_nozzle_spraying-clouds.pdf)
  - [Safe Rain Hibiscus rotating nozzle datasheet](https://saferain.com/wp-content/uploads/2026/10/rotatory-fountain-nozzle_hibiscus.pdf)
  - [Safe Rain Dancing rotating nozzle datasheet](https://saferain.com/wp-content/uploads/2026/10/rotatory-fountain-nozzle_dancing.pdf)
  - [Safe Rain Twister rotating nozzle datasheet](https://saferain.com/wp-content/uploads/2026/10/rotatory-fountain-nozzle_twister.pdf)
  - [Safe Rain Cyclone rotating nozzle datasheet](https://saferain.com/wp-content/uploads/2026/10/rotatory-fountain-nozzle_cyclone.pdf)
  - [Safe Rain Double big fan jets rotatory datasheet (catalogue page 110)](https://saferain.com/wp-content/uploads/2026/10/rotatory-fountain-nozzle_double_fan_rotating_jets.pdf)
  - [Safe Rain Lance Jet I stainless steel datasheet](https://saferain.com/wp-content/uploads/2026/10/crystalline_fountain_nozzle_lance-jet-i-stainless-steel.pdf)

### Roman Fountains (2 rows)

- [ ] Approve Roman Fountains rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Roman Fountains RPJ-375/RPJ-500 Adjustable Precision Jet cut sheet, drawing 1.203 (R0_08-14-18)](https://www.poolsupplyunlimited.com/Products/Manuals/87032_20191021111136.pdf)

### Atlantic Water Gardens (7 rows)

- [ ] Approve Atlantic Water Gardens rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Atlantic Water Gardens stainless steel fountain nozzles product manual (FNCC15 / FNMJ15 / FNAF10 / FNAS15 / FNHC15 / FNDF10 / FNDJ10 / FNAB15), 2019](https://cdn.shopify.com/s/files/1/0600/3635/0129/files/Nozzle_manual-2019.pdf)

### Otterbine (4 rows)

- [ ] Approve Otterbine rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Otterbine Gemini Aerating Fountain sales sheet (60 Hz)](https://www.otterbine.com/media/1059/salesheet_gemini_60hz_14.pdf)

### Kasco (17 rows)

- [ ] Approve Kasco rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [Kasco 7.3JF Aerating Fountain quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/7.3jf_quick_spec.pdf)
  - [Kasco 2.3JF quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/2.3jf_quick_spec.pdf)
  - [Kasco 3.1JF quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/3.1jf_quick_spec.pdf)
  - [Kasco 5.1JF quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/5.1jf_quick_spec.pdf)
  - [Kasco 3400JF quick spec](https://cdn.shopify.com/s/files/1/1126/4786/files/Kasco_3400jf_quick_spec_cut_sheet.pdf)
  - [Kasco 4400JF quick spec](https://cdn.shopify.com/s/files/1/1126/4786/files/Kasco_4400jf_quick_spec_cut_sheet.pdf)
  - [Kasco 2.3VFX quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/2.3vfx_quick_spec.pdf)
  - [Kasco 5.1VFX quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/5.1vfx_quick_spec.pdf)
  - [Kasco 5.3VFX quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/5.3vfx_quick_spec.pdf)
  - [Kasco 2400VFX quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/2400vfx_quick_spec.pdf)
  - [Kasco 8400VFX quick spec](https://cdn.shopify.com/s/files/1/0009/3470/7315/files/8400vfx_quick_spec.pdf)
  - [Kasco 3400VFX quick spec](https://cdn.shopify.com/s/files/1/1126/4786/files/3400vfx_quick_spec.pdf)
  - [Kasco 4400VFX quick spec](https://cdn.shopify.com/s/files/1/1126/4786/files/4400vfx_quick_spec.pdf)

### WIBRE (8 rows)

- [ ] Approve WIBRE rows for seeding
- [ ] Spot-check two curves or tables against the documents below (open the page cited in each row's `source`)
- Documents:
  - [WIBRE CENTUM catalogue (EN)](https://www.wibre.de/files/public/content/downloads/WIBRE_Catalogue_Centum_EN.pdf)
  - [WIBRE 4.0083 recessed spotlight catalogue page](https://www.wibre.de/files/public/product-downloads/Catalog_4.0083.pdf)
  - [WIBRE 4.0273 recessed spotlight (swimming pool light, saline version) catalogue page](https://www.wibre.de/files/public/product-downloads/Catalog_4.0273.pdf)
  - [WIBRE 4.0275 recessed spotlight (flush-mount swimming-pool light) catalogue page](https://www.wibre.de/files/public/product-downloads/Catalog_4.0275.pdf)

## Things to know before seeding

- **Pool pumps publish head and flow only.** None of the Pentair, Sta-Rite, Hayward or Jandy documents publishes
  efficiency, NPSHr or shaft power, and very few give a maximum water temperature. So the solver can pick these
  pumps on head and flow but cannot check NPSH margin at altitude against a published NPSHr. Grundfos NBS and Goulds
  e-SHX are the exceptions: they publish NPSHr, and Grundfos also publishes power.
- **Don't infer power from "rated HP" on pool pumps.** Pool pumps are rated with a service factor. On several
  Hayward HCP 2000/3000 rows the published table implies more water horsepower (Q·H/3960) than the printed
  "Rated H.P." Two examples: HCP20073, rated 0.75 HP, gives 71 gpm at 50 ft, which is 0.90 WHP. HCP30203, rated 2 HP,
  reaches 2.1 WHP. Where the maker publishes THP or a service factor, it is in `total_hp` / `service_factor`; use
  that for motor and circuit sizing, or better, the nameplate.
- **Several source documents have errors, and the notes flag each one.**
  - Pentair D-Series chart: the x-axis labels skip 220 gpm.
  - Max-E-ProXF catalog page: the curve keys don't match the chart.
  - Hayward HCP 2000/3000 tables: misprinted cells.
  - OASE Comet table: a non-monotonic row.
  - Safe Rain Cascade sheet: a pressure that falls as height rises.
  - Pentair EQ WaterFall chart: the box says 3 HP for a 5 HP pump.

  In every case the value is stored as published or as the drawing implies, never corrected silently.
- **Some ratings are 50 Hz or not sold in the USA.** Pentair `-INT` part numbers are "not for sale in the USA".
  They are kept because they are the only published curve at that HP, and their notes say so. 50 Hz tables were
  skipped.
- **Goulds 3656LH at 1725 rpm** shuts off at 13–18 ft, below the 20 ft fountain floor. Those rows are kept but
  probably shouldn't be seeded.
- **Hayward HCP 2000** rows are flow-at-head tables (30–90 ft) with no shutoff point. A solver needs to extrapolate
  carefully, or skip them.
- **Specs change.** Every maker reserves the right to change specifications without notice. Check the current
  document at order time, not this file.

## License and terms-of-use concerns

Not legal advice. These are the points to weigh before the numbers go into a database that other people use:

- **Pentair forbids scraping.** Its website terms forbid automated scraping
  ([terms](https://www.pentair.com/en-us/legal/terms-of-use.html)). Every Pentair and Sta-Rite document here was
  opened and read one at a time; nothing was crawled. Keep any refresh of this data manual, document by document.
- **OASE asserts reproduction rights.** The ProfiLux light data sheet states that its content is "the sole property
  of OASE GmbH" and forbids reproduction without written permission. That makes the OASE light row the clearest
  case for asking OASE first. OASE's nozzle sheets carry no such clause, but they are the same company's
  literature.
- **Everyone else gives copyright notices only.** Hayward, Jandy/Zodiac, Roman (© 2019), Grundfos (© 2022) and
  Goulds/Xylem (© 2024) carry copyright and trademark notices but no data-reuse licence. Safe Rain and Atlantic
  sheets carry no terms at all.
- **Facts versus presentation.** Performance figures are facts about a product, and storing them as cited data
  points for internal engineering use is different from republishing the sheets. A customer-facing page that prints
  these tables or charts verbatim would be closer to reproduction. Keep the citation on each row, and don't
  redistribute the PDFs.
- **Ask for the data directly.** Pentair, Hayward, Crystal and OASE reps can usually send curve data in Excel. That
  removes the digitizing error and the terms question in one step.

## How to regenerate or extend

The per-maker research files and the scripts that produced them live outside the repo, in the session scratchpad.
They are not committed. To extend the catalog, read one more document the same way and add rows with the same
fields. The vector method is:

1. Find the curve paths and their colours in the PDF.
2. Calibrate on the gridlines, not on text labels: label boxes are offset, and one Pentair chart has wrong labels.
3. Sample at round flows.
4. Compare the result with a rendered image of the page.

The chart pages left undigitized in documents already opened are listed below. They are the cheapest next rows.

## Lines with no published data, or not yet digitized

| Manufacturer | Line | Why |
|---|---|---|
| Sta-Rite (Pentair) | Dura-Glas / Dura-Glas II | Discontinued line; not in the 2024 Pentair catalog; no manufacturer curve document found. The 2024 catalog offers Max-E-Pro and SuperMax as the current Sta-Rite inground pumps. |
| Pentair | WhisperFloXF 2-speed and high-efficiency (XFE/XFDS) models | Not in the 2024 catalog's ordering table (only 022034, 022032-INT, 022033-INT and three-phase 022017-022019); older brochures (2013 XFE/XFDS) not digitized. |
| Pentair | EQ Series catalog curves (keys A-I, separate 1-ph and 3-ph curves) | The catalog's EQ curve page was not digitized in this pass; EQ records use the EQ brochure curves (one curve per HP). |
| Pentair | C Series CM/CMK curve 'I' | Curve drawn on the CM/CMK chart but no model in the catalog maps to key I. |
| Pentair | Efficiency / NPSHr / BHP for any pool-line pump | Pentair pool and commercial-pool literature publishes head-flow only (C Series refers to www.pentaircommercial.com for efficiency curves, not fetched). NPSHr is not published for self-priming pool pumps. |
| Pentair | Max water temperature (pool lines) | Only the EQ brochure states a liquid limit (104 F). Owner's manuals may state one; not read. |
| Pentair / Sta-Rite | SuperMax VS, IntelliPro3, Aurora, Berkeley B | Present in the 2024 catalog (p16-19, 26-27, 54-55) but not digitized in this pass; same method applies. |
| Hayward | TriStar VS Omni (and other Omni-branded VS pumps) | No separate Omni curve document found; the TriStar VS 900 (SP3202VSP) and 950 sheets cover the pump hydraulics and are included. Whether Omni variants share these curves was not verified. |
| Hayward | Super Pump legacy single-speed 1.5-3 HP (SP2610X15, SP2615X20 etc.) and Super Pump VS SP2602VSP / SP2603VSP (1.5 HP) | Current Hayward literature (LITSPHE23) covers only 0.60-1.10 THP and the two-speed SP2607X102S; older non-DOE models are listed as obsolete and only a 2014 sell sheet with an unreadable chart turned up. The Super Pump 700 TEFC sheet covers 1.10 and 1.50 THP. |
| Hayward | HCP 1000 / HCP 6000 | No HCP 1000 or HCP 6000 documents exist in the 2024 catalog or search; the commercial pump line there is HCP 2000, 2500, 3000, and 4000 (the HCP 4000 sell sheet is included). |
| Hayward | HCP 2000 per-model curve / shutoff head | The small raster chart at the bottom of catalog page 13 cannot be separated per model; only the published flow-at-head table (30-90 ft) is recorded, with no shutoff head. |
| Hayward | HCP 2000 single-phase SP32xxEE and 330/575V HCP32xxC | Listed with amps and weights but no separate curves; not recorded as their own pumps. |
| Hayward | Super Pump / TriStar rated amps and max water temperature | Owner's manuals refer to the motor nameplate; no per-model FLA table found in the documents opened. fla_amps left null for those records. |
| Jandy | VS PlusHP (VSPHP270DV2AS) and ePump 1.85 HP | jandy.com is behind Incapsula bot protection (not fetched); no distributor-hosted VS PlusHP spec sheet with a readable curve was found. ePump 2.2, 2.7 and 3.8 HP and VS FloPro 0.85-3.8 HP are included. |
| Jandy | Older Jandy ePump 208-230V sheet (VSSHP220AUT / VSSHP270AUT / JEP2.0SVRS) and ePump Pro Series JEP-1.5/2.0 curves | Superseded by the dual-voltage ePump sheet (included); the older 208-230V sheet (poolsupplyunlimited 145252_202112792320.pdf) was opened but not digitized. |
| Jandy | Stealth / FloPro / PlusHP motor RPM and max water temperature | Max water temperature and motor RPM are not printed on the spec sheets; owner's manuals (manualslib / manualshive) were not downloaded because those hosts are third-party copies. |
| Goulds (Xylem) | e-SH 60 Hz close-coupled end suction (2-pole 3500 rpm and 4-pole 1750 rpm) | Every download returned an HTML page (bot block / not a PDF) and no xylem.com copy of the e-SH brochure turned up in search. The e-SHX Smart Pump brochure (same S/M-group hydraulics, variable speed) was used instead. Second pass: prochiller.com lists e-SH curve images and guessed pfcestore.com / xylem.com siteassets URLs also returned HTML; no alternative PDF host found. |
| Goulds (Xylem) | NPE / NPO stainless end suction | Download returned an XML error document, not a PDF. |
| Goulds (Xylem) | e-SHX remaining models (brochure pages 14, 16, 17, 20, 24, 26, 27: 01SHX08L, 15SHX06K/N, 15SHX10K, 02SHX10N, 25SHX08J, 03SHX08K) | Curves exist and are vector, but the batch digitizer failed on their layout (different panel geometry or missing NPSH panel) and they were not hand-processed in the time budget. 9 of 16 curve pages were digitized. |
| Grundfos | NB / NBG / NK / NKG (EN 733 / ISO 2858) 60 Hz data booklet 4112587 | Opened: it is a supplement covering only the large sizes (125-400, 150-125-400, 150-315.1, 200-150-315.1), in metric units (m, m3/h), with heads well above 120 ft for these sizes. Out of the requested range, so not digitized. The ANSI NBS booklet (inch/gpm/ft) was used instead. |
| Grundfos | NBS 2-pole (3500 rpm), NBS 060 / 080 / 100 4-pole, and small 4-pole sizes 012-070, 015-070, 025-070, 030-095 etc. | 3500 rpm NBS curves reach 150-430 ft (above the 120 ft target); NBS 060-095/110 run 2,200-2,600 gpm and NBS 080/100 more than 4,000 gpm (above the 1,500 gpm target). Other 4-pole sizes are on the same pages and readable with the same method; only 12 chart panels were done. 050-095 and 050-110 (curves end near 1,550 and 1,670 gpm) were kept because they cover the 1,000-1,500 gpm range. |
| Wilo | NL / NL-HE end suction 60 Hz (also GIGA-N, IL-E) | Only product-family coverage charts and sales decks were in the downloadable PDFs; per-size submittals with individual curves sit on wilo.com pages that were not scraped (read one document at a time by hand if needed). No GIGA-N / IL-E 60 Hz US data found in search. |
| Franklin Electric | end-suction centrifugal | No public Franklin end-suction curve document found; results were FPS submersible catalogs and parts lists. |
| Crystal Fountains | all nozzles and LED fountain lights | no public performance tables found; their catalog says to contact them for detailed specifications |
| Roman Fountains | foam jets, cascade, other nozzle sheets besides RPJ-375/500 | only the RPJ precision jet sheet was found as a manufacturer PDF; other models appear only as retailer listings pointing to cut sheets that were not located |
| OASE | Water Trumpet, Lava, Bell/Schaumsprudler, Aerating, Maxi Vulcan, Spray Ring, Multi-jet sheets | only Comet, Fan Jet, Frothy, Finger Jets, Cascade, Geyser and Vulcan sheets exist at that path; the 2014 pageflip catalogue and oase-livingwater.com Comet3-5T datasheet were not read (flipbook HTML; one fetch returned HTTP 429). Not attempted further |
| OASE | Vulcan sheet PDF pages 3-19 | only pages 1-2 (Vulcan table) are readable; Maxi Vulcan not present |
| Otterbine | other aerating fountain models and patterns (Sunburst, Triton, Comet, Mystic, Super Nova, etc.) | only Gemini 60 Hz was opened; other patterns found only on retailer sites (not used per brief); Otterbine download centre not crawled |
| Atlantic Water Gardens | vertical/column nozzle model names, thread sizes, FNAB15 | manual lists only model codes; names above come from retailer listings and are flagged in notes; FNAB15 is on the cover but has no table row; no thread/orifice data in the manual |
| Fountain People, Waterline, Airmax, Scott, Delta Fountains | all | time budget; brief lists them as optional |
| Kasco | J/VFX/JF quick specs for models beyond those recorded (e.g. 3400HVFX, 884169 5HP/7HP manual, other cord/voltage variants) | time; 3400HVFX and the 5/7 HP J-series manual were seen in search results but not opened. Quick specs give no GPM for any Kasco unit |
| Safe Rain | floating fountains (saferain series PDFs), lighting and spray-ring PDFs | not downloaded or read; all nozzle datasheet PDFs linked from the nozzle product pages were transcribed |
| OASE | ProfiLux LED (all sizes), ProfiLux Garden LED RGB, other lights | oase.com and oase-livingwater.com now serve a Vercel security checkpoint (or HTTP 429) to scripted requests, and oase.su returned nothing; not bypassed. Only the ProfiLux LED 1100/01 PDF on poolsupplyunlimited.com was readable. Search-result summaries quoted figures for ProfiLux 320/DMX/02 (573 lm, 31 deg), 2200 (2683 lm, 31 deg) and 110/DMX/02 (213 lm, 25 deg) but those were not read from a document, so they are NOT recorded |
| Hydrel (Acuity Brands) | 4426 LED SWM / BM / FTN, W6300 linear, 4415, 4421C | Cloudflare challenge / HTTP 403 for scripted downloads; not bypassed. Search summaries mention 44 W and 86 W options with up to about 10,985 lm for the 4426 SWM, but nothing was read from the document, so nothing recorded |
| Pentair | IntelliBrite 5G lumens, MicroBrite, AquaLumin | Pentair publishes watts and voltage only in the grid; no lumen figure found for 5G Color; MicroBrite and AquaLumin sheets not located |
| Jandy | Infinite WaterColors niched/nicheless lumens, Jandy Pro Series WaterColors (JLU) lights | sheets give watts/voltage but no lumens; the niched sell sheet 68348_SL was not found as a PDF link. JLU 10 W / 15 W Pro Series models listed only as max watts in SL6500 and not recorded |
| Hayward | ColorLogic lumens, beam angle, IP rating | manual has watts/voltage/UL file only; only the product page gives a lumen figure (950) and it is flagged as unconfirmed |
| Crystal Fountains | LED fountain lights | no public document with lumens found |
| WIBRE | 4.0052 recessed spotlight, 4.0292/4.0291/4.0102 updated models, 4.0192 | only the .11 / main rows with unambiguous column alignment were recorded; 4.0052 table not captured (text says up to 1200 lumen) |
| Otterbine / Airmax / Scott Aerator / Delta Fountains / Fountain People / Waterline | all | time; Kasco JF/VFX series were prioritised (12 additional Kasco quick specs transcribed) |

## Jurisdiction table (`jurisdictions.json`)

Research output, 2026-11-01. Nikolas decided that day to extend the Fountain Designer from Utah to all 48 contiguous
states, at state level now (county and city overrides later), and to ship every cited row without a review pass. The
phase J loader in `water_engineering/` reads this file; until it merges, nothing in the app reads it.

**At a glance:** 48 entries, 168 `null` values (each with a cite saying why). 30 states regulate interactive
water features in their pool rule; 6 read like Utah R392-302-41 and get the Utah checklist as blockers: CA, FL, GA, NV, OR, UT.
Run the validator after any edit:

```
python docs/fountain-designer/catalog/jurisdictions-validate.py
```

It fails on a missing or extra key, a wrong type, a value without a cite, a cite with no URL behind a non-null value, a
state missing from the 48, a `winterize` that disagrees with the heating temperature, or a Utah row that disagrees with
the engine's current defaults. It prints the null count per state.

### Schema

`{"schema": 1, "generated": "2026-11-01", "jurisdictions": {"<USPS>": entry, ...}}`, 48 entries. Every value may be
`null` except `iwf_like_utah` (always a boolean) and the free-text fields. A cite is one string: `URL — what the source
says (read 2026-11-01)`, several joined with ` ; `.

| Key | Meaning |
|---|---|
| `name` | State name |
| `codes.nec` | `edition` (year), `basis` (`statewide` / `local` / `none`), `effective` (ISO date), `amendments` (short note or `""`), `cite` |
| `codes.ibc`, `codes.irc` | `edition`, `basis`, `cite`. A state amendment or effective date the agent found is in brackets at the end of the cite |
| `codes.ispsc` | `adopted` (statewide adoption of the International Swimming Pool and Spa Code), `edition`, `basis`, `cite` |
| `codes.mahc` | `status`: `adopted` (the state rule adopts the CDC Model Aquatic Health Code), `referenced` (cited as guidance or used to revise the rule), `none` (no use found); `cite` |
| `regulation.agency`, `pool_rule`, `pool_cite` | Who administers public pools and under which rule. `pool_rule` is `null` where the state has none |
| `regulation.spa_regulated` | Public spas are under the rule (cited in `pool_cite`) |
| `regulation.iwf_regulated`, `iwf_rule`, `iwf_cite` | The state regulates interactive water features (splash pads, spray grounds, interactive fountains with recirculated water) |
| `regulation.iwf_like_utah` | `true` only when the state requires all four Utah elements: turnover of about 30 minutes, a collector tank, secondary disinfection, and automated disinfectant (ORP) and pH control. The engine then runs the Utah checklist as blockers under the state's citation; otherwise as warnings |
| `regulation.fogger_potable`, `fogger_cite` | The rule requires misters and foggers to run on potable water. `false` means the rule was read and has no such requirement |
| `regulation.plan_review`, `plan_review_cite` | `required` (state or its delegate approves plans), `not required`, `local` |
| `regulation.notes` | What the designer must know, including what the state does instead of Utah's rule |
| `climate.design_city` | The state's largest metro or capital, where Sapphire would most plausibly build |
| `climate.elevation_ft` | USGS Elevation Point Query Service at the city's Census 2024 Gazetteer internal point |
| `climate.air_temp_f` | ASHRAE 1% cooling dry-bulb |
| `climate.heating_design_f` | ASHRAE 99.6% heating dry-bulb |
| `climate.water_temp_f` | Summer basin design temperature: ASHRAE 1% evaporation wet-bulb + 5 °F (see below) |
| `climate.frost_depth_in` | The design city's building-department frost line |
| `climate.hardness_ppm` | Total hardness, mg/L as CaCO3, from the city utility's latest water quality data |
| `climate.winterize` | `heating_design_f` below 28 °F |
| `climate.seismic_default` | ASCE 7-16 seismic design category, Risk Category II, Site Class D, from the USGS design-maps service. The fallback when the address lookup has no answer |
| `climate.cites` | One cite per climate value, keyed by field. `water_temp_f` and `winterize` are two keys beyond the brief's list, added so every number has a cite |
| `licensing.contractor`, `electrical`, `permits`, `cites` | License class for pool, spa and fountain work; electrical licensing; who issues permits and whether a health-department permit also applies; a list of cites |

### How the values were obtained

- **Codes, regulation, licensing, frost depth and hardness** were researched state by state from primary sources:
  statutes and administrative codes (state sites, or Cornell LII for rule text), state agency and licensing-board pages,
  city building-department criteria and amendments, and city water quality reports. Secondary sites (UpCodes, ICC-NTA,
  vendor pages) were used only to find a primary; where nothing else could be read, the cite says `[secondary]`.
- **Search excerpts.** The search budget ran out partway through, and many state sites refuse automated readers
  (maine.gov, tn.gov, azsos.gov, chicago.gov, kdhe.ks.gov and others). Where a value rests on the excerpt a search
  engine showed of an official page, rather than the page itself, the cite says `[search excerpt]` or "search excerpt".
- **Climate is scripted** (`jurisdictions-research/climate/fetch_climate.py` in the session notes):
  - ASHRAE 2025 climatic design conditions, from ASHRAE's station viewer at ashrae-meteo.info.
  - The station is the city's primary airport, the conventional design station. Where the nearest station was a harbor
    buoy or a small field, the main airport was used instead, and the cite names the station.
  - Elevation comes from USGS EPQS, and the seismic category from the USGS Seismic Design Web Service (ASCE 7-16).
  - Where Fv is site-specific (S1 ≥ 0.2 on Site Class D), the category comes from SDS per ASCE 7-16 §11.4.8. The cite
    says so.
- **Water temperature basis.**
  - A sprayed fountain basin behaves like a cooling tower: it runs toward the wet-bulb, not the dry-bulb. So
    `water_temp_f` is the ASHRAE 1% evaporation design wet-bulb plus a 5 °F approach.
  - The 5 °F is Sapphire's basis, chosen so Salt Lake City (64.9 + 5) reproduces the engine's existing Utah default of
    70 °F. It is a design assumption, not a published figure.
- **Utah keeps the engine's values, except hardness.** Elevation 4,300 ft, air 95 °F, water 70 °F and frost 30 in are the
  engine's long-standing Utah defaults. Hardness is 212 ppm (v1.772.0): the upper bound of the 2026 SLC water report's
  136–212 ppm at the three treatment plants, taken for scaling conservatism, because cited values ship and the old
  250 ppm is in no report. The scripted figures are in the cites as a cross-check: EPQS 4,223 ft, ASHRAE 2025 96.4 °F.
  Since phase J this file is the engine's default (`engine/data/jurisdictions.json` is a copy, held equal by a test).
- **Coordinator changes to the agents' rows** (all in the session notes' `assemble.py`):
  - Extra keys were folded into their cites.
  - `iwf_like_utah` was set `false` where the rule could not be read (AZ, ME, MS, NM, NY).
  - It was also set `false` for Iowa and Indiana, which lack secondary disinfection and automated pH control
    respectively.
  - Arizona's pool rule and plan review and Tennessee's contractor class were filled from search excerpts.
  - Mississippi's agency and MAHC status were nulled, because no rule text was read.
- **Values are as of 2026-11-01 and move.** Many states are mid-transition to the 2024 I-codes and 2023 or 2026 NEC. The
  cites name pending changes and their effective dates. Recheck a state's row before relying on it for a permit set.

### State summary

| State | Design city | NEC | IBC | IRC | ISPSC | MAHC | IWF regulated | Like Utah | Fogger potable | Plan review | Elev ft | 1% DB °F | 99.6% °F | Water °F | Frost in | Hardness ppm | SDC | Nulls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| AL | Birmingham | local | local | local | no | referenced | — | no | — | — | 600 | 93 | 22 | 82 | — | — | C | 13 |
| AZ | Phoenix | local | local | local | no | none | — | no | — | required | 1242 | 109 | 40 | 80 | 0 | 237 | B | 8 |
| AR | Little Rock | — | 2021 | 2021 | local | none | yes | no | no | required | 279 | 96 | 21 | 84 | — | 23 | D | 5 |
| CA | Los Angeles | 2023 | 2024 | 2024 | no | referenced | yes | yes | yes | required | 82 | 81 | 45 | 74 | 12 | 103 | D | 1 |
| CO | Denver | 2026 | local | local | no | referenced | yes | no | no | required | 5292 | 93 | 1 | 69 | 36 | 105 | B | 3 |
| CT | Hartford | 2020 | 2021 | 2021 | 2021 | none | yes | no | no | required | 33 | 89 | 5 | 80 | 42 | — | B | 1 |
| DE | Wilmington | 2020 | local | local | no | none | yes | no | no | required | 5 | 90 | 14 | 82 | 32 | 145 | B | 4 |
| FL | Miami | 2020 | 2021 | 2021 | no | referenced | yes | yes | no | required | 7 | 91 | 49 | 85 | — | — | A | 3 |
| GA | Atlanta | 2023 | 2018 | 2018 | 2018 | none | yes | yes | no | required | 956 | 92 | 23 | 81 | — | — | C | 2 |
| ID | Boise | 2023 | 2018 | 2018 | no | none | no | no | no | required | 2742 | 96 | 12 | 70 | 24 | — | C | 3 |
| IL | Chicago | local | 2018 | 2018 | no | none | yes | no | no | required | 594 | 89 | 0 | 80 | — | — | B | 5 |
| IN | Indianapolis | 2008 | 2012 | 2018 | no | none | yes | no | no | required | 719 | 89 | 3 | 81 | 30 | 317 | C | 1 |
| IA | Des Moines | 2023 | 2024 (local) | 2024 (local) | no | none | yes | no | no | required | 792 | 90 | -4 | 82 | 42 | 147 | B | 1 |
| KS | Wichita | local | local | local | no | none | no | no | — | local | 1296 | 98 | 8 | 82 | 24 | — | B | 10 |
| KY | Louisville | 2023 | 2015 | 2015 | no | none | yes | no | no | required | 520 | 92 | 12 | 82 | 24 | 128 | C | 1 |
| LA | New Orleans | 2020 | 2021 | 2021 | no | none | no | no | no | required | -5 | 93 | 34 | 85 | — | 163 | B | 3 |
| ME | Portland | — | — | — | no | — | — | no | — | required | 0 | 84 | 1 | 78 | — | 7.2 | B | 10 |
| MD | Baltimore | local | 2021 | 2021 | 2021 | none | yes | no | no | required | 39 | 91 | 14 | 82 | 30 | 94 | B | 2 |
| MA | Boston | 2026 | 2021 | 2021 | 2021 | none | no | no | no | required | 5 | 88 | 8 | 80 | 48 | 13.1 | B | 1 |
| MI | Detroit | 2023 | 2021 | 2021 | no | none | — | no | — | required | 639 | 88 | 3 | 80 | 42 | 107 | B | 5 |
| MN | Minneapolis | 2026 | 2018 | 2018 | no | none | — | no | no | required | 866 | 88 | -9 | 80 | 42 | 96 | A | 3 |
| MS | Jackson | local | local | local | no | — | — | no | — | — | 364 | 95 | 24 | 83 | — | — | B | 15 |
| MO | St. Louis | local | local | local | no | none | yes | no | — | local | 467 | 94 | 8 | 83 | 30 | 129 | D | 7 |
| MT | Billings | 2023 | 2024 | 2024 | 2024 | referenced | yes | no | no | required | 3216 | 92 | -8 | 69 | 42 | 125 | B | 0 |
| NE | Omaha | 2023 | 2018 | 2018 | no | none | yes | no | yes | required | 1164 | 92 | -2 | 83 | — | 154 | B | 2 |
| NV | Las Vegas | local | local | local | no | referenced | yes | yes | no | required | 2351 | 107 | 33 | 76 | 12 | 280 | D | 5 |
| NH | Manchester | 2023 | 2024 | 2024 | 2024 | referenced | yes | no | no | required | 255 | 89 | 3 | 79 | 48 | 17.88 | C | 1 |
| NJ | Newark | 2023 | 2024 | 2024 | 2024 | none | yes | no | no | required | 12 | 91 | 13 | 81 | 36 | 49 | B | 0 |
| NM | Albuquerque | 2020 | 2021 | 2021 | no | adopted | yes | no | — | required | 4963 | 94 | 19 | 69 | 12 | — | D | 3 |
| NY | New York City | — | 2024 | 2024 | no | none | yes | no | — | required | 55 | 88 | 13 | 80 | 48 | 32 | B | 3 |
| NC | Charlotte | 2020 | 2015 | 2015 | no | none | yes | no | no | required | 674 | 92 | 22 | 82 | — | 39 | B | 2 |
| ND | Fargo | 2023 | 2024 | 2024 | no | none | no | no | no | local | 901 | 88 | -18 | 79 | 54 | 134 | A | 2 |
| OH | Columbus | 2023 | 2021 | 2018 | — | none | yes | no | no | required | 806 | 89 | 6 | 80 | 32 | 120 | B | 2 |
| OK | Oklahoma City | 2023 | 2024 | 2024 | no | none | no | no | no | required | 1196 | 98 | 14 | 82 | 18 | — | B | 3 |
| OR | Portland | 2026 | 2024 | 2024 | no | adopted | yes | yes | no | required | 161 | 88 | 26 | 73 | 12 | 9 | D | 1 |
| PA | Philadelphia | 2020 | 2021 | 2021 | 2021 | none | no | no | no | local | 100 | 91 | 15 | 82 | 30 | 140 | B | 1 |
| RI | Providence | 2023 | 2021 | 2021 | 2021 | adopted | no | no | no | required | 42 | 87 | 9 | 80 | 40 | — | B | 2 |
| SC | Charleston | 2020 | 2021 | 2021 | no | none | yes | no | no | required | 5 | 92 | 28 | 85 | — | 58.4 | D | 2 |
| SD | Sioux Falls | 2023 | local | local | no | none | no | no | — | local | 1475 | 89 | -9 | 81 | 42 | 250 | A | 6 |
| TN | Nashville | — | — | — | — | none | — | no | no | required | 427 | 93 | 16 | 82 | 12 | 100.5 | D | 8 |
| TX | Dallas | 2023 | local | local | 2021 | none | yes | no | no | local | 483 | 100 | 25 | 83 | 6 | — | B | 3 |
| UT | Salt Lake City | 2023 | 2024 | 2021 | 2024 | none | yes | yes | yes | required | 4300 | 95 | 12 | 70 | 30 | 250 | D | 0 |
| VT | Burlington | 2023 | 2021 | none | no | referenced | no | no | no | — | 104 | 86 | -6 | 78 | 60 | 63 | C | 4 |
| VA | Virginia Beach | 2020 | 2021 | 2021 | no | none | yes | no | no | local | 13 | 91 | 22 | 83 | — | — | A | 3 |
| WA | Seattle | 2023 | 2021 | 2021 | 2021 | none | yes | no | no | required | 144 | 83 | 27 | 70 | 12 | 28 | D | 1 |
| WV | Charleston | 2020 | 2018 | 2018 | 2018 | none | yes | no | no | required | 598 | 89 | 11 | 80 | — | 79 | B | 1 |
| WI | Milwaukee | 2023 | 2021 | none | no | referenced | yes | no | no | required | 710 | 86 | 0 | 80 | 48 | 138 | B | 2 |
| WY | Cheyenne | 2026 | 2024 | local | no | none | no | no | no | required | 6041 | 88 | -3 | 67 | 36 | 60 | B | 4 |

A dash is a `null` (its cite says why). "local" means no statewide adoption: the design city's own edition, where the agent found it, is in that code's cite.

### Sources

National starting points:
- ICC code adoption map and per-state pages (https://www.iccsafe.org/advocacy/adoptions-map/).
- NFPA NEC enforcement maps (https://www.nfpa.org/education-and-research/electrical/nec-enforcement-maps).
- CDC Model Aquatic Health Code (https://www.cdc.gov/model-aquatic-health-code/).
- ASHRAE climatic design conditions 2025 (https://ashrae-meteo.info/v3.0/).
- USGS EPQS (https://epqs.nationalmap.gov/v1/json).
- USGS Seismic Design Web Service (https://earthquake.usgs.gov/ws/designmaps/asce7-16.json).
- Census 2024 Gazetteer places file (https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip).

Each row's cites name the exact pages. The per-state lists below are what each state's research read.

<details><summary>AL — Alabama (7 sources)</summary>

- https://dcm.alabama.gov/bldg_code.aspx — Alabama DCM State Building Code
- https://www.alabamapublichealth.gov/environmental/rules.html — ADPH environmental rules
- https://genconbd.alabama.gov/about/law/ — ALBGC law summary
- https://genconbd.alabama.gov/14534/new-contractor-licensing-exemptions-take-effect-october-1-2026/ — 34-8-7 exemptions
- https://aecb.alabama.gov/applicants/how-to-get-licensed/ — Alabama Electrical Contractors Board
- https://www.iccsafe.org/advocacy/adoptions-map/alabama/ — ICC Alabama page
- https://www.energycodes.gov/status/states/alabama — [secondary] energy code status

</details>

<details><summary>AZ — Arizona (10 sources)</summary>

- https://www.azleg.gov/ars/49/00104.htm — A.R.S. 49-104 (ADEQ powers incl. pools)
- https://www.azleg.gov/ars/11/00861.htm — A.R.S. 11-861 (county building codes)
- https://www.azleg.gov/ars/32/01121.htm — A.R.S. 32-1121 (contractor licensing exemptions)
- https://www.azleg.gov/ars/36/01681.htm — A.R.S. 36-1681 (residential pool barriers, 18-inch depth)
- https://www.phoenix.gov/administration/departments/pdd/tools-resources/codes-ordinance/building-code.html — Phoenix Building Construction Codes
- https://www.phoenix.gov/content/dam/phoenix/pddsite/documents/codes-ordinances/amendmentcodes/2024-irc.pdf — Phoenix 2024 IRC amendments (Table R301.2)
- https://www.phoenix.gov/content/dam/phoenix/pddsite/documents/codes-ordinances/amendmentcodes/2024-ispcs.pdf — Phoenix 2024 ISPSC amendments
- https://www.phoenix.gov/content/dam/phoenix/pddsite/documents/codes-ordinances/amendmentcodes/2023-nec.pdf — Phoenix 2023 NEC amendments
- https://www.phoenix.gov/content/dam/phoenix/waterservicessite/documents/wsdprimarywqr.pdf — Phoenix 2025 Water Quality Report
- https://www.phoenix.gov/administration/departments/pdd/residential-building.html — Phoenix residential building permits

</details>

<details><summary>AR — Arkansas (11 sources)</summary>

- https://healthy.arkansas.gov/wp-content/uploads/Swimming-Pools-Regs.pdf — ADH pool rules (2012)
- https://healthy.arkansas.gov/programs-services/licensing-military-member-licensure-permits-plan-reviews/swimming-pool-plan-review-process/ — ADH plan review
- https://healthy.arkansas.gov/resources/rules/ — ADH rules page
- https://www.littlerock.gov/media/18644/enforced-construction-codes-2023-c-sized-sheet.pdf — Little Rock enforced codes
- https://littlerock.gov/government/city-departments/planning-and-development/divisions/ — Little Rock Building Codes Division
- https://littlerock.gov/business/little-rock-business-portal/additional-requirements-and-permits/ — Little Rock contractor licensing note
- https://carkw.com/customer-service/faq/ — Central Arkansas Water FAQ (hardness)
- https://carkw.com/site/assets/files/1171/ccr25_central_arkansas_water_465.pdf — CAW 2025 CCR
- https://labor.arkansas.gov/labor/code-enforcement/electrical-inspection-and-licensing/ — electrical licensing
- https://www.labor.arkansas.gov/licensing/arkansas-contractors-licensing-board/ — Contractors Licensing Board
- https://www.iccsafe.org/advocacy/adoptions-map/arkansas/ — ICC Arkansas page

</details>

<details><summary>CA — California (15 sources)</summary>

- https://www.dgs.ca.gov/BSC/Codes — 2025 Title 24 parts list, effective 2026-01-01
- https://www.dgs.ca.gov/-/media/Divisions/BSC/06-News/Information-Bulletins/2025/BSC-Bulletin-25-01-FINAL.pdf — CBSC Information Bulletin 25-01
- https://www.dgs.ca.gov/-/media/Divisions/BSC/02-Codes/2025-CEC_rev2025-11-20_locked.docx — 2025 California Electrical Code text
- https://shop.iccsafe.org/2025-california-building-code-title-24-part-2-volumes-1-2.html — CBC based on 2024 IBC
- https://shop.iccsafe.org/2025-california-residential-code-title-24-part-2-5.html — CRC based on 2024 IRC
- https://www.dgs.ca.gov/-/media/Divisions/BSC/06-News/Information-Bulletins/BSC-Bulletin-19-06-Swmg-Pool-FINAL.pdf — CA does not adopt ISPSC
- https://deh.acgov.org/operations-assets/docs/recreationalhealth/Pool%20Code%20Book%20Title%2022%20final%20printed%20version%20effective%201.1.2023%20-%20Final.pdf — compiled Title 22 / CBC 31B / H&S Code
- https://gocodebook.com/library/us/ca/ca-building-code/chapter-31b-public-pools/section-3106b-special-requirements-for-spray-grounds — CBC 3106B [secondary]
- https://gocodebook.com/library/us/ca/ca-building-code/chapter-31b-public-pools/section-3103b-plan-review — CBC 3103B [secondary]
- https://www.Cdph.Ca.gov/Programs/OLS/CDPH%20Document%20Library/DPH-15-001-45-Day-PN.pdf — CDPH rulemaking referencing MAHC
- https://admin.publichealth.lacounty.gov/eh/docs/permit/public-pool-plan-approval-requirements.pdf — LA County DPH plan approval
- https://www.ladwp.com/sites/default/files/2026-07/2026_DWQR_Print_Rev2_Digital-remediated.pdf — LADWP 2025 water quality report
- https://www.apexpergola.com/frost-depth [secondary] — LA frost depth
- https://www.cslb.ca.gov/About_Us/Library/Licensing_Classifications/Licensing_Classifications_Detail.aspx?Class=C53 — C-53
- https://dir.ca.gov/DLSE/ECU/EleCat.html — DIR electrician certification

</details>

<details><summary>CO — Colorado (11 sources)</summary>

- https://www.coloradosos.gov/CCR/GenerateRulePdf.do?ruleVersionId=9173&fileName=5+CCR+1003-5 — 5 CCR 1003-5 Swimming Pools and Mineral Baths (eff. 12/15/2020)
- https://cdphe.colorado.gov/dehs/swimming-pools — CDPHE swimming pools regulation page
- https://www.sos.state.co.us/CCR/GenerateRulePdf.do?ruleVersionId=12606&fileName=3%20CCR%20710-1 — State Electrical Board rules, Rule 1.5 (2026 NEC)
- https://dpo.colorado.gov/Electrical — State Electrical Board page
- https://content.govdelivery.com/accounts/CODORA/bulletins/367d589 — DORA 2023 NEC bulletin
- https://www.iccsafe.org/advocacy/adoptions-map/colorado/ — ICC Colorado adoptions
- https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Building-Codes-Policies-and-Guides/Building-and-Fire-Code-Adoption-Process — Denver 2025 code adoption
- https://www.denvergov.org/files/assets/public/v/1/community-planning-and-development/documents/ds/building-codes/archive-codes/2025_denver_building_code-june_2025.pdf — 2025 Denver Building Code
- https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Public-Health-Environment/Public-Health-Investigations/Healthy-Families-Healthy-Homes/Swimming-Pools — DDPHE swimming pools
- https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Community-Planning-and-Development/Contractor-Licensing — Denver contractor licensing
- https://www.denverwater.org/sites/default/files/water-quality-report-2026.pdf — Denver Water 2026 water quality report

</details>

<details><summary>CT — Connecticut (10 sources)</summary>

- https://portal.ct.gov/das/-/media/das-beta/pdf/office-of-state-building-inspector/2022-csbc-final.pdf — 2022 Connecticut State Building Code
- https://portal.ct.gov/das/services-for-agencies-and-municipalities/safety-codes-and-inspections — DAS safety codes page, CSBC summary
- https://portal.ct.gov/dph/-/media/dph/dph-2025/environmental-engineering-program/public-pools/rules-and-design-resources/rcsa-section-19-13-b33b.pdf — RCSA 19-13-B33b public pools
- https://portal.ct.gov/dph/-/media/dph/dph-2025/environmental-engineering-program/public-pools/rules-and-design-resources/ct-dph-public-swimming-pool-design-guide-2021.pdf — CT DPH Public Swimming Pool Manual, April 2021
- https://portal.ct.gov/dph/-/media/dph/dph-2025/environmental-engineering-program/public-pools/application-packages-and-forms/design-specification-sheets/public-splash-or-spray-pad-design-specification-sheet-july-2026.pdf — splash/spray pad design specification sheet, July 2026
- https://portal.ct.gov/dph/programs/health-and-the-environment/environmental-engineering-program/public-pools — DPH public pools program page
- https://portal.ct.gov/dph/environmental-health/recreation/recreation-program — DPH Recreation Program
- https://themdc.org/wp-content/uploads/2026/06/2025-Water-Quality-Report.pdf — MDC 2025 Water Quality Report
- https://www.cga.ct.gov/current/pub/chap_393.htm — Conn. Gen. Stat. ch. 393 electrical/pool builder
- https://www.cga.ct.gov/current/pub/chap_400.htm — Conn. Gen. Stat. ch. 400 home improvement

</details>

<details><summary>DE — Delaware (6 sources)</summary>

- https://archive.regulations.delaware.gov/AdminCode/title16/Department%20of%20Health%20and%20Social%20Services/Division%20of%20Public%20Health/Health%20Systems%20Protection%20(HSP)/4464.shtml — 16 DE Admin Code 4464 public pool regulations
- https://archive.regulations.delaware.gov/AdminCode/title1/700/701.shtml — Delaware State Fire Prevention Regulation 701
- https://library.municode.com/de/wilmington/codes/code_of_ordinances — Wilmington Code of Ordinances ch. 4 and Sec. 5-96
- https://wilmingtondewater.gov/DocumentCenter/View/757 — Wilmington 2025 Water Quality Report
- https://delcode.delaware.gov/title24/c014/sc02/index.html — 24 Del.C. ch. 14 subch. II
- https://www.icc-nta.org/states/delaware/ — [secondary] ICC-NTA Delaware

</details>

<details><summary>FL — Florida (14 sources)</summary>

- https://www.floridahealth.gov/environmental-health/swimming-pools/index.html — FL DOH swimming pools program
- https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-64E-9-008 — 64E-9.008 Supervision and Safety (IWF)
- https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-64E-9-004 — 64E-9.004 Operational Requirements
- https://www.law.cornell.edu/regulations/florida/Fla-Admin-Code-Ann-R-64E-9-001 — 64E-9.001 General
- https://www.flrules.org/gateway/ruleNo.asp?id=64E-9.004 — rule history (6-11-23)
- https://up.codes/s/interactive-water-features-iwfs — [secondary] FBC 454.1.9.8
- https://www.floridabuilding.org/c/default.aspx — FBC 8th ed. effective date
- https://www.floridabuilding.org/fbc/thecode/2023_Code_Development/2023_Code_Resources/Analysis-of-Changes-8th-Ed-2023-FBCB-Final.pdf — Analysis of Changes 8th ed.
- https://www.charlottecountyfl.gov/departments/community-development/building-construction/building-codes.stml — NEC 2020 enforcement
- http://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0500-0599/0553/Sections/0553.73.html — s. 553.73
- http://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0400-0499/0489/Sections/0489.105.html — s. 489.105
- http://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0400-0499/0489/Sections/0489.505.html — s. 489.505
- https://www.neha.org/Images/resources/JEH5.18-Column-Direct-From-CDC-EHS.pdf — JEH May 2018, MAHC in Florida
- https://www.miamidade.gov/global/water/water-quality-reports.page — WASD water quality reports

</details>

<details><summary>GA — Georgia (6 sources)</summary>

- https://rules.sos.ga.gov/gac/511-3-5 — Georgia Public Swimming Pools rule
- https://dca.georgia.gov/document/document/ga-state-codes-adoption-history-effective-1-1-2025/download — DCA state codes adoption history
- https://dca.georgia.gov/announcement/2025-12-09/new-codes-jan-2027 — DCA codes effective January 1, 2027
- https://dca.georgia.gov/community-assistance/construction-codes/current-state-minimum-codes-construction — DCA current state minimum codes
- https://dca.georgia.gov/community-assistance/construction-codes/mandatory-and-permissive-state-codes — mandatory vs permissive
- https://www.atlantawatershed.org/water-quality-report/ — Atlanta DWM water quality reports

</details>

<details><summary>ID — Idaho (10 sources)</summary>

- https://dopl.idaho.gov/wp-content/uploads/2025/06/2025-Updated-Idaho-Adopted-Codes.pdf — DOPL Idaho Adopted Codes
- https://dopl.idaho.gov/wp-content/uploads/2026/08/BLD-Minutes-2026-08-11.pdf — Building Code Board minutes 8/11/2026
- https://dopl.idaho.gov/wp-content/uploads/2025/08/BLD-Minutes-2025-08-11.pdf — Building Code Board hearing 8/11/2025
- https://legislature.idaho.gov/statutesrules/idstat/Title54/T54CH10/SECT54-1001/ — Idaho Code 54-1001 (NEC 2023)
- https://admws.idaho.gov/pipermail/dopl-communication/2024-June/000028.html — DOPL 2023 NEC enforcement memo
- https://adminrules.idaho.gov/rules/2024%20Archive/16/160214.pdf — IDAPA 16.02.14 public swimming pools
- https://www.cityofboise.org/departments/planning-and-development-services/building/building-permits/general-building-information-300/301-current-codes/ — Boise current codes and frost depth
- https://legislature.idaho.gov/statutesrules/idstat/Title54/T54CH52/SECT54-5205/ — Idaho Contractor Registration Act exemptions
- https://dopl.idaho.gov/ele/ — DOPL electrical program
- https://mywater.veolia.us/idaho/water-in-my-area/water-quality-reports — Veolia Idaho water quality report lookup (no hardness obtained)

</details>

<details><summary>IL — Illinois (7 sources)</summary>

- https://www.ilga.gov/commission/jcar/admincode/077/077008200C02400R.html — 77 IAC 820.240 Spray Pools
- https://www.ilga.gov/commission/jcar/admincode/077/077008200B01000R.html — 77 IAC 820.100 Permits
- https://www.ilga.gov/commission/jcar/admincode/077/077008200C02100R.html — 77 IAC 820.210 pool design
- https://www.ilga.gov/commission/jcar/admincode/077/077008200F05000R.html — 77 IAC 820.500
- https://cdb.illinois.gov/business/codes/buildingcodesregulations — CDB building codes and regulations
- https://www.ilga.gov/ftp/Public%20Acts/103/103-0510.htm — Public Act 103-0510
- https://www.law.cornell.edu/regulations/illinois/title-77/part-820 — Part 820 index and history

</details>

<details><summary>IN — Indiana (7 sources)</summary>

- https://www.in.gov/dhs/boards-and-commissions/fpbsc-rules — IDHS adopted rules and code editions table
- https://iar.iga.in.gov/code/2026/675/20 — 675 IAC 20 Swimming Pool Code
- https://iar.iga.in.gov/code/2026/675/14 — 675 IAC 14 Indiana Residential Code
- https://iar.iga.in.gov/code/2026/410/6 — 410 IAC 6 Sanitary engineering
- https://www.in.gov/dhs/building-plan-review/building-plan-review-process — DHS building plan review
- https://www.in.gov/pla/professions/ — Indiana PLA professions
- https://info.citizensenergygroup.com/water/quality/indianapolis-and-morgan-county — Citizens 2025 water quality data

</details>

<details><summary>IA — Iowa (13 sources)</summary>

- https://www.legis.iowa.gov/docs/code/2026/135I.pdf — Iowa Code ch. 135I
- https://www.legis.iowa.gov/docs/iac/chapter/481.485.pdf — 481 IAC ch. 485
- https://www.legis.iowa.gov/docs/iac/chapter/481.301.pdf — 481 IAC ch. 301 State Building Code
- https://www.legis.iowa.gov/docs/iac/chapter/481.300.pdf — 481 IAC ch. 300
- https://www.legis.iowa.gov/docs/code/103A.10.pdf — Iowa Code 103A.10
- https://www.legis.iowa.gov/docs/iac/chapter/481.404.pdf — 481 IAC ch. 404 electrical
- https://www.legis.iowa.gov/docs/code/2026/103.pdf — Iowa Code ch. 103
- https://www.legis.iowa.gov/docs/code/2026/91C.pdf — Iowa Code ch. 91C
- https://www.legis.iowa.gov/docs/code/2026/105.pdf — Iowa Code ch. 105
- https://www.dsm.city/departments/development_services/permit_development_center/construction_codes.php — Des Moines construction codes
- https://www.dsm.city/Documents/Departments/Development%20Services/Permit%20Development%20Center/Residential/Deck%20Handout-LEGAL-2021%20IRC.pdf — Des Moines deck handout
- https://www.dmww.com/water_quality/water_quality_data.php — Des Moines Water Works data
- https://desmoineswater.revize.com/api/water-quality-data — DMWW data API

</details>

<details><summary>KS — Kansas (8 sources)</summary>

- https://www.sedgwickcounty.org/mabcd/codes-resolutions-ordinances/ — MABCD adopted codes list
- https://www.sedgwickcounty.org/media/54682/50-833-2018-international-residential-code.pdf — Wichita Ord. 50-833, 2018 IRC with frost depth amendment
- https://www.sedgwickcounty.org/mabcd/contractor-licensing/ — MABCD contractor licensing
- https://www.sedgwickcounty.org/mabcd/permits/ — MABCD permits
- https://www.law.cornell.edu/regulations/kansas/K-A-R-22-1-3 — Kansas Fire Prevention Code adoption
- https://www.law.cornell.edu/regulations/kansas/agency-28 — K.A.R. agency 28 article index
- https://www.ksrevisor.gov/statutes/ksa_ch65.html — K.S.A. chapter 65 title index
- https://www.ksrevisor.gov/statutes/chapters/ch31/031_001_0034a.html — K.S.A. 31-134a

</details>

<details><summary>KY — Kentucky (10 sources)</summary>

- https://apps.legislature.ky.gov/law/kar/titles/815/007/120/ — 815 KAR 7:120 Kentucky Building Code
- https://apps.legislature.ky.gov/law/kar/titles/815/007/125/ — 815 KAR 7:125 Kentucky Residential Code
- https://dhbc.ky.gov/Documents/2023%20NEC%20Update%20Announcement.pdf — DHBC 2023 NEC memo
- https://dhbc.ky.gov/Documents/2018%20Kentucky%20Building%20Code%204th%20Ed.pdf — 2018 KBC 4th Ed (Table 1809.5, Sec. 3109)
- https://apps.legislature.ky.gov/law/kar/titles/902/010/120/ — 902 KAR 10:120
- https://apps.legislature.ky.gov/law/kar/titles/815/035/060/ — 815 KAR 35:060
- https://apps.legislature.ky.gov/law/kar/titles/815/035/015/ — 815 KAR 35:015
- https://louisvillewater.com/WaterQualityReport — Louisville Water 2025 WQR
- https://louisvilleky.gov/government/health-wellness/services/public-health-plan-review — Louisville plan review [snippet]
- https://louisvilleky.gov/government/construction-review/electrical-permit — Louisville electrical permits [snippet]

</details>

<details><summary>LA — Louisiana (7 sources)</summary>

- https://www.law.cornell.edu/regulations/louisiana/title-51/part-XXIV — LAC 51:XXIV Swimming Pools
- https://www.law.cornell.edu/regulations/louisiana/title-17/part-I/chapter-1 — LAC 17:I Uniform Construction Code
- http://luccc.louisiana.gov/wp-content/uploads/2025/11/LSUCCC_Louisiana-Construction-Code-Information.pdf — LUCCC code editions
- https://luccc.louisiana.gov/2023-nec-2024-i-code-adoption-proposed-language-2/ — 2023 NEC/2024 I-Code notice of intent
- https://lslbc.louisiana.gov/exams-classifications/ — LSLBC classifications
- https://nola.gov/class-d-journeyman-electrical-license/ — New Orleans electrician licenses
- https://streaming.swbno.org/Media/documents/Reports/water_quality_report_2014.pdf — SWBNO 2014 water quality report

</details>

<details><summary>ME — Maine (5 sources)</summary>

- https://legislature.maine.gov/statutes/22/title22ch602.pdf — 22 MRSA ch. 602 Public Pools and Spas
- https://legislature.maine.gov/statutes/10/title10sec9722.html — 10 MRSA s.9722 Technical Building Codes and Standards Board (MUBEC contents)
- https://legislature.maine.gov/statutes/10/title10sec9724.html — 10 MRSA s.9724 Application of MUBEC
- https://legislature.maine.gov/statutes/32/title32ch17.pdf — 32 MRSA ch. 17 Electricians
- https://www.pwd.org/wp-content/uploads/2026/05/PWD-Water-Quality-Report-2026_Final.pdf — Portland Water District 2026 water quality report

</details>

<details><summary>MD — Maryland (8 sources)</summary>

- https://www.law.cornell.edu/regulations/maryland/COMAR-09-12-51-04 — COMAR 09.12.51 Maryland Building Performance Standards
- https://www.law.cornell.edu/regulations/maryland/COMAR-09-12-56-03 — COMAR 09.12.56 Maryland Swimming Pool and Spa Standards
- https://www.law.cornell.edu/regulations/maryland/COMAR-10-17-01-16 — COMAR 10.17.01 Public Swimming Pools and Spas (all regs read)
- https://health.maryland.gov/phpa/OEHFP/CHS/pages/aquaticfacility.aspx — MDH aquatic facility page
- https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gps&section=12-603&enactments=false — Public Safety 12-603 (NEC)
- https://codes.baltimorecity.gov/us/md/cities/baltimore/code/building-codes/II/index.full.html — Baltimore City Building Code Part II (also III, X, XII)
- https://www.baltimorecity.gov/publicworks/water-quality-reports — Baltimore DPW water quality reports and mineral analyses
- https://mgaleg.maryland.gov/mgawebsite/Laws/StatuteText?article=gbr&section=8-101&enactments=false — Home improvement licensing

</details>

<details><summary>MA — Massachusetts (10 sources)</summary>

- https://www.icc-nta.org/code-update/new-massachusetts-electrical-code-in-effect/ — MA electrical code 2026 effective 2026-04-24
- https://www.law.cornell.edu/regulations/massachusetts/780-CMR-CHAPTER-1-PART-1-SECTION-101-101.4 — 780 CMR 101.4.15 ISPSC 2021
- https://www.law.cornell.edu/regulations/massachusetts/780-CMR-3109-3109-1 — 780 CMR 3109.1 pools
- https://www.law.cornell.edu/regulations/massachusetts/780-CMR-110-R5 — Construction supervisor license exemptions
- https://chatham-ma.gov/DocumentCenter/View/3266/State-Pool-Regulation-PDF — 105 CMR 435.00 text
- https://www.mwra.com/your-water-system/drinking-water-quality/water-and-health — MWRA hardness 2025
- https://www.mwra.com/your-water-system/drinking-water-quality/frequently-asked-questions-about-mwra-tap-water — MWRA hardness FAQ
- https://www.mwra.com/media/file/boston2026 — MWRA 2025 Boston CCR (no hardness line)
- https://malegislature.gov/Laws/GeneralLaws/PartI/TitleXX/Chapter141/Section1 — M.G.L. c.141 s.1
- https://www.mass.gov/handbook/tenth-edition-of-the-ma-state-building-code-780 — 10th edition handbook (search excerpt only)

</details>

<details><summary>MI — Michigan (7 sources)</summary>

- https://ars.apps.lara.state.mi.us/Transaction/DownloadFile?FileName=FinalRule%28s%29.pdf&FileType=FinalRule&TransactionID=1416&EffectiveDate=4%2F9%2F2025 — Michigan Building Code Part 4 final rule
- https://ars.apps.lara.state.mi.us/Transaction/DownloadFile?FileName=FinalRule%28s%29.pdf&FileType=FinalRule&TransactionID=1375&EffectiveDate=8%2F29%2F2025 — Michigan Residential Code Part 5 final rule
- https://ars.apps.lara.state.mi.us/Transaction/DownloadFile?FileName=RFRForm_2026-17_LR.pdf&FileType=RFRForm&TransactionID=1658&EffectiveDate=Pending — Part 8 Electrical RFR
- https://www.legislature.mi.gov/Laws/MCL?objectName=mcl-333-12525 — MCL 333.12525
- https://www.law.cornell.edu/regulations/michigan/Mich-Admin-Code-R-325-2113 — R 325.2113
- https://www.legislature.mi.gov/documents/mcl/pdf/mcl-Act-407-of-2016.pdf — Skilled Trades Regulation Act
- https://detroitmi.gov/sites/detroitmi.localhost/files/2026-06/DWSD%20-%20WQR%20-%202025%20-%20Final%20-1.pdf — DWSD 2025 water quality report

</details>

<details><summary>MN — Minnesota (16 sources)</summary>

- https://www.revisor.mn.gov/rules/1315.0200/ — Minn. R. 1315.0200 Minnesota Electrical Code
- https://dli.mn.gov/about-department/rulemaking/rulemaking-docket-minnesota-rules-chapter-1315 — DLI 2026 NEC docket
- https://www.revisor.mn.gov/rules/1305.0011/ — Minn. R. 1305.0011 IBC adoption
- https://dli.mn.gov/about-department/rulemaking/rulemaking-docket-minnesota-rules-chapter-1305 — DLI IBC docket
- https://www.revisor.mn.gov/rules/1309.0010/ — Minn. R. 1309.0010 IRC adoption
- https://www.revisor.mn.gov/rules/1300.0050/ — chapters of the State Building Code
- https://www.revisor.mn.gov/rules/4717/full — Minnesota Pool Code full text
- https://www.revisor.mn.gov/rules/4717.0450/ — plan submission
- https://www.revisor.mn.gov/statutes/cite/144.1222 — public pools statute
- https://www.health.mn.gov/communities/environment/recreation/pools/poolprinsp.html — MDH plan review page
- https://www.revisor.mn.gov/rules/1303.1600/ — footing depth for frost
- https://www.minneapolismn.gov/government/departments/public-works/water-treatment-distribution/water-quality-reports/documents/ — Minneapolis water reports
- https://www.revisor.mn.gov/statutes/cite/326B.802 — residential contractor licensing
- https://www.revisor.mn.gov/statutes/cite/326B.31 — electrical licensing definitions
- https://www.revisor.mn.gov/statutes/cite/326B.36 — electrical inspection
- https://www.revisor.mn.gov/statutes/cite/326B.121 — State Building Code application

</details>

<details><summary>MS — Mississippi (6 sources)</summary>

- https://jacksonms.gov/?p=6627 — City of Jackson adopted building codes and permits
- https://www.msboc.us/classifications/ — MSBOC classifications
- https://www.msboc.us/general-info/frequently-asked-questions/ — MSBOC FAQ
- https://jxnwater.com/wp-content/uploads/2026/06/JXNWater-2025-Water-Quality-Report.pdf — JXN Water 2025 report
- https://awc.org/priorities/codes-standards/adoption/mississippi/ — [secondary] state adoption summary
- https://ezel.ai/ag-opinions/ms/2024-01-n-manley-january-9-2024-citys-ability-to-exempt-areas-from-certain-building-code — [secondary] AG opinion on Miss. Code 17-2-4

</details>

<details><summary>MO — Missouri (9 sources)</summary>

- https://health.mo.gov/prevention/environmental-factors/recreational-water-safety — Missouri DHSS recreational water safety
- https://revisor.mo.gov/main/OneSection.aspx?section=67.280 — RSMo 67.280
- https://www.stlouis-mo.gov/government/departments/public-safety/building/building-codes.cfm — City of St. Louis adopted codes
- https://www.stlouis-mo.gov/government/departments/health/environmental-health/community-sanitation/new-pool-construction.cfm — St. Louis new pool permit
- https://www.stlouis-mo.gov/government/departments/health/documents/upload/2025-New-Pool-Construction.pdf — St. Louis recreational water new construction checklist
- https://www.stlouis-mo.gov/government/departments/public-safety/building/permits/electrical-permit.cfm — St. Louis electrical permit
- https://www.stlouis-mo.gov/government/departments/public-safety/building/boards-of-examiners/index.cfm — St. Louis Boards of Examiners
- https://www.stlouis-mo.gov/government/departments/public-safety/building/documents/upload/fence.pdf — St. Louis fence plan guideline
- https://www.stlwater.com/wp-content/uploads/2026/06/CCR-2025.pdf — St. Louis Water Division 2025 CCR

</details>

<details><summary>MT — Montana (10 sources)</summary>

- https://bsd.dli.mt.gov/building-codes-permits/current-codes — Montana DLI current adopted codes
- https://www.iccsafe.org/advocacy/adoptions-map/montana/ — ICC Montana
- https://dphhs.mt.gov/publichealth/EHFS/swimmingpools/index — DPHHS Swimming Pools, Spas and other Water Features
- https://dphhs.mt.gov/assets/publichealth/FCS/PublicSwimmingPools/CircularFCS_3_2022.pdf — Circular FCS 3-2022
- https://www.billingsmt.gov/324 — Billings design criteria
- https://www.billingsmt.gov/323/Adopted-Codes — Billings adopted codes
- https://www.billingsmtpublicworks.gov/ArchiveCenter/ViewFile/Item/392 — Billings 2025 water quality report
- https://boards.bsd.dli.mt.gov/construction-contractor/construction-contractor-program-FAQ.pdf — Montana CCL FAQ
- https://boards.bsd.dli.mt.gov/electrical/ — Montana Electrical Board
- https://boards.bsd.dli.mt.gov/plumbers/ — Montana Board of Plumbers

</details>

<details><summary>NE — Nebraska (11 sources)</summary>

- https://nebraskalegislature.gov/laws/statutes.php?statute=81-2104 — State Electrical Board powers, NEC 2023
- https://electrical.nebraska.gov/licensing — NSED code adoption note
- https://nebraskalegislature.gov/laws/statutes.php?statute=71-6403 — State building code
- https://nebraskalegislature.gov/laws/statutes.php?statute=71-6404 — applicability
- https://nebraskalegislature.gov/laws/statutes.php?statute=71-6406 — local codes
- https://dwee.nebraska.gov/sites/default/files/titles/Title_196.pdf — Title 196 Nebraska Public Swimming Pool Standards
- https://dee.nebraska.gov/water-quality/water-quality-permitting/swimming-pool-program — DWEE swimming pool program
- https://www.mudomaha.com/wp-content/uploads/2026/04/CCR2025.pdf — MUD 2025 water quality report
- https://electrical.nebraska.gov/electrical-contractor — electrical contractor license
- https://electrical.nebraska.gov/municipal-county-electrical-inspection-programs — municipal inspection programs
- https://dol.nebraska.gov/conreg — contractor registration

</details>

<details><summary>NV — Nevada (12 sources)</summary>

- https://www.leg.state.nv.us/nrs/nrs-278.html — NRS 278.580, 278.583
- https://www.leg.state.nv.us/NAC/NAC-444.html — NAC 444 Sanitation (public bathing places)
- https://www.leg.state.nv.us/NAC/NAC-624.html — NAC 624 contractor classifications
- https://www.iccsafe.org/nevada/ — ICC Nevada adoption page
- https://www.lasvegasnevada.gov/Government/Departments/Building-Safety/Building-Safety-Resources?tab=1 — City of Las Vegas building codes
- https://www.lasvegasnevada.gov/Government/Departments/Building-Safety/Building-Safety-Resources?tab=2 — City of Las Vegas adopted codes and requirements
- https://sawebfilesprod001.blob.core.windows.net/building-safety/2025/2024-IRC-Amendments.pdf — Southern Nevada 2024 IRC amendments (Table R301.2)
- https://www.southernnevadahealthdistrict.org/permits-and-regulations/aquatic-health-program/regulations/ — SNHD aquatic regulations page
- https://www.southernnevadahealthdistrict.org/download/aquatic-health/Regualtions/20180705-Aquatic-Regulations.pdf — SNHD Aquatic Facility Regulations
- https://www.lvvwd.com/assets/pdf/water-quality-report-las-vegas-valley-current.pdf — LVVWD 2026 Water Quality Report
- https://www.lvvwd.com/water-quality/facts/index.html — LVVWD water quality FAQ
- https://www.nvcontractorsboard.com/ — Nevada State Contractors Board

</details>

<details><summary>NH — New Hampshire (8 sources)</summary>

- https://www.gencourt.state.nh.us/rsa/html/XII/155-A/155-A-1.htm — RSA 155-A:1 definitions (state building code editions)
- https://www.gencourt.state.nh.us/rsa/html/XII/155-A/155-A-2.htm — RSA 155-A:2 state building code and permits
- https://www.gencourt.state.nh.us/rules/state_agencies/env-wq1100.html — Env-Wq 1100 Public Bathing Places
- https://www.gencourt.state.nh.us/rsa/html/XXX/319-C/319-C-1.htm — RSA 319-C:1 electricians
- https://www.manchesternh.gov/Departments/Planning-and-Comm-Dev/Building/Building-Codes — Manchester building codes page
- https://www.manchesternh.gov/pcd/Regulations/BuildingCode.pdf — Building Code of the City of Manchester (2021)
- https://www.manchesternh.gov/Departments/Health/Environmental-Health/Recreational-Water-Quality/Open-a-New-Pool-or-Spa — pool plan approval process
- https://www.manchesternh.gov/Portals/2/Departments/water_works/Web-Ready%20CCR.pdf — Manchester Water Works 2022 report

</details>

<details><summary>NJ — New Jersey (7 sources)</summary>

- https://www.nj.gov/dca/codes/codreg/pdf_rule_adoptions/model_codes_2024_adopt.pdf — NJ DCA adoption of 2024 model codes, effective 2026-08-17
- https://www.nj.gov/dca/codes/codreg/pdf_rule_proposals/2024_prop_p1.pdf — NJ DCA proposal PRN 2025-136 with section summaries
- https://www.law.cornell.edu/regulations/new-jersey/title-8/chapter-26 — N.J.A.C. 8:26 Public Recreational Bathing
- https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-5-23-3-21 — IRC amendments incl. frost depth
- https://water.newarknj.gov/s/2020-CCR-1.pdf — Newark 2020 water quality report
- https://www.law.cornell.edu/regulations/new-jersey/N-J-A-C-13-45A-17-2 — Home improvement contractor definition
- https://pub.njleg.state.nj.us/Bills/2020/AL21/479_.HTM — P.L. 2021 c.479 electrical

</details>

<details><summary>NM — New Mexico (11 sources)</summary>

- https://www.srca.nm.gov/parts/title07/07.018.0001.html — 7.18.1 NMAC Public Aquatic Venues
- https://www.srca.nm.gov/parts/title14/14.008.0003.html — 14.8.3 NMAC 2012 NM Swimming Pool, Spa and Hot Tub Code
- https://www.rld.nm.gov/wp-content/uploads/2026/03/14.8.3-NMAC_DRAFT-RULE.pdf — April 2026 draft 14.8.3 NMAC
- https://www.rld.nm.gov/2026/03/05/notice-of-public-hearing-construction-industries-division/ — CID hearing notice
- https://www.srca.nm.gov/parts/title14/14.007.0002.html — 14.7.2 NMAC 2021 NM Commercial Building Code
- https://www.srca.nm.gov/parts/title14/14.007.0003.html — 14.7.3 NMAC 2021 NM Residential Building Code
- https://www.srca.nm.gov/parts/title14/14.010.0004.html — 14.10.4 NMAC 2020 NM Electrical Code
- https://www.srca.nm.gov/parts/title14/14.006.0006.html — 14.6.6 NMAC contractor classifications
- https://www.iccsafe.org/advocacy/adoptions-map/new-mexico/ — ICC New Mexico
- https://www.cabq.gov/planning/building-safety-division — Albuquerque Building Safety Division
- https://www.abcwua.org/your-drinking-water-faqs/ — ABCWUA drinking water FAQ

</details>

<details><summary>NY — New York (8 sources)</summary>

- https://dos.ny.gov/notice-adoption — NYS DOS notices of adoption, 2025 Uniform Code (effective 2025-12-31)
- https://dos.ny.gov/division-building-standards-and-codes-frequently-asked-questions — NYS DOS Building Standards and Codes FAQ
- https://www.law.cornell.edu/regulations/new-york/10-NYCRR-6-3.1 — 10 NYCRR Subpart 6-3 spray grounds (6-3.1 to 6-3.13)
- https://www.law.cornell.edu/regulations/new-york/10-NYCRR-6-1.2 — 10 NYCRR Subpart 6-1 swimming pools
- https://www.nyc.gov/assets/doh/downloads/pdf/about/healthcode/health-code-article165.pdf — NYC Health Code Article 165 Bathing Establishments
- https://www.nyc.gov/assets/buildings/codes-pdf/cons_codes_2022/2022BC_Chapter18_SoilsFoundationsWBwm.pdf — 2022 NYC Building Code Chapter 18
- https://www.nyc.gov/assets/dep/downloads/pdf/water/drinking-water/drinking-water-supply-quality-report/2025-drinking-water-supply-quality-report.pdf — NYC DEP 2025 water quality report
- https://www.nyc.gov/site/buildings/codes/electrical-code.page — NYC Electrical Code page

</details>

<details><summary>NC — North Carolina (9 sources)</summary>

- https://www.ncosfm.gov/codes/codes-current-and-past — NC OSFM codes, current and past
- https://www.ncosfm.gov/letter-re-2024-state-building-code-effective-date — letter on 2024 State Building Code effective date
- https://www.iccsafe.org/advocacy/adoptions-map/north-carolina/ — ICC NC adoption page
- https://ehs.dph.ncdhhs.gov/docs/rules/RulesGoverningPublicSwimmingPools.2500.pdf — Rules Governing Public Swimming Pools
- https://ehs.dph.ncdhhs.gov/faf/pti/pools.htm — NC Public Swimming Pool Program
- https://www.charlottenc.gov/Water/Water-Quality — Charlotte Water quality page
- https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_87/GS_87-1.html — G.S. 87-1
- https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/ByArticle/Chapter_87/Article_4.html — G.S. 87 Art. 4
- https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_143/GS_143-138.html — G.S. 143-138

</details>

<details><summary>ND — North Dakota (12 sources)</summary>

- https://ndlegis.gov/cencode/t54c21-3.pdf — NDCC ch. 54-21.3 State Building Code
- https://www.commerce.nd.gov/node/130 — ND Commerce Building Codes page
- https://www.medialibrary.nd.gov/assetbank-nd/assetfile/156311.pdf — 2026 ND State Building Code Book
- https://ndlegis.gov/files/rule-changes/notices/ndseb072623notice.pdf — State Electrical Board 2023 NEC rulemaking notice
- https://ndlegis.gov/information/acdata/pdf/33-29-01.pdf — NDAC ch. 33-29-01 Pool Facilities
- https://fargond.gov/city-government/departments/inspections/commercial-building-permits-fees/current-codes — Fargo adopted codes
- https://fargond.gov/city-government/departments/fargo-cass-public-health/environmental-health/pools-public-residential — Fargo Cass Public Health pools
- https://download.fargond.gov/1/attached_garage_handout.pdf — Fargo frost depth handout
- https://download.fargond.gov/0/2026-06-05_-_water_quality_report_-_approved_-_approved_single_pages.pdf — Fargo 2025 water quality report
- https://ndlegis.gov/cencode/t43c07.pdf — NDCC ch. 43-07 Contractors
- https://ndlegis.gov/cencode/t43c09.pdf — NDCC ch. 43-09 Electricians
- https://www.ndseb.com/ — ND State Electrical Board

</details>

<details><summary>OH — Ohio (11 sources)</summary>

- https://www.richlandcountyoh.gov/media/Departments/Building/Documents/BBS%20Memo%20414%202024%20Code%20Update%20Information.pdf — BBS Memo 414
- https://www.iccsafe.org/advocacy/adoptions-map/ohio/ — ICC Ohio adoption page
- https://codes.ohio.gov/ohio-administrative-code/chapter-3701-31 — OAC 3701-31
- https://codes.ohio.gov/ohio-administrative-code/rule-3701-31-05.1 — design criteria incl. spray grounds
- https://codes.ohio.gov/ohio-administrative-code/rule-4101:8-34-01 — Ohio electrical rule
- https://www.unioncountyohio.gov/media/Officials/Engineer/building-regulation/BuildingInUnionCo_UsefulInfo/Current%20Ohio%20Code%20References.pdf — county code table
- https://www.icc-nta.org/states/ohio — ICC-NTA Ohio notices [secondary]
- https://www.columbus.gov/files/sharedassets/city/v/2/building-and-zoning/cics/cic-09-climatic-geographic-design-2026.pdf — Columbus R301.2 table
- https://www.columbus.gov/files/sharedassets/city/v/1/utilities/ccr/consumer-confidence-report-2025.pdf — Columbus 2025 CCR
- https://www.columbus.gov/Services/Public-Utilities/About-Public-Utilities/The-Division-of-Water/Water-Facts/Water-Hardness — hardness page
- https://codes.ohio.gov/ohio-revised-code/chapter-4740 — ORC 4740 contractor licensing

</details>

<details><summary>OK — Oklahoma (12 sources)</summary>

- https://www.law.cornell.edu/regulations/oklahoma/title-748/chapter-20 — OAC 748:20 Adopted Codes index
- https://oklahoma.gov/content/dam/ok/en/oubcc/documents/rules/2026%2009%2014%20IBC%202024%20Permanent%20Rule.pdf — IBC 2024 rule
- https://oklahoma.gov/content/dam/ok/en/oubcc/documents/rules/2022%2009%2014%20NEC%202023%20Permanent%20Rule.pdf — NEC 2023 rule
- https://oklahoma.gov/cib/news/2026-updated-building-code.html — CIB 2026 code update notice
- https://aem-prod.oklahoma.gov/content/dam/ok/en/health/health2/documents/cpd-public-bathing-place-act-title-63-1-1013.pdf — 63 O.S. 1-1013 et seq.
- https://www.law.cornell.edu/regulations/oklahoma/OAC-310-315-3-1 — plans and specifications
- https://www.law.cornell.edu/regulations/oklahoma/OAC-310-315-7-5 — decorative fountains
- https://www.law.cornell.edu/regulations/oklahoma/OAC-310-315-7-11 — public spas
- https://oklahoma.gov/health/services/licensing-inspections/consumer-health-service/licensure/public-pools-and-spas.html — OSDH pools and spas
- https://oklahoma.gov/cib.html — CIB home, general contractor statement
- https://www.oklegislature.gov/cf_pdf/2025-26 SUPPORT DOCUMENTS/BILLSUM/House/HB3338 POLPCS3 BILLSUM.PDF — HB 3338 summary
- https://www.okc.gov/Services/Water-Trash-Recycling/Water — OKC water page, hardness contact

</details>

<details><summary>OR — Oregon (11 sources)</summary>

- https://oregon.gov/bcd/codes-stand/Pages/adopted-codes.aspx — BCD adopted codes and effective dates
- https://www.oregon.gov/bcd/laws-rules/Documents/20261001-2026oesc-adoption-pr.pdf — 2026 OESC adoption notice
- https://www.oregon.gov/bcd/laws-rules/Documents/20261001-2026orsc-adoption-pr.pdf — 2026 ORSC adoption notice
- https://www.oregon.gov/bcd/codes-stand/pages/commercial-structures.aspx — 2025 OSSC
- https://oregon.gov/bcd/codes-stand/Documents/23orsc-summaryofamendments.pdf — 2023 ORSC text incl. Table R301.2 and R327/R328
- https://www.oregon.gov/bcd/codes-stand/Documents/26orsc-ch01.pdf — 2026 ORSC Chapter 1
- https://www.law.cornell.edu/regulations/oregon/Or-Admin-Code-SS-333-060-1000 — OAR 333-060-1000
- https://www.oregon.gov/oha/PH/HEALTHYENVIRONMENTS/RECREATION/POOLSLODGING/Documents/OAR%20333-060-1000.pdf — OHA compiled rule
- https://www.portland.gov/water/drinking-water-quality/about-drinking-water-quality/2026-drinking-water-quality-report — Portland Water Bureau 2026 report
- https://www.oregonlegislature.gov/bills_laws/ors/ors701.html — ORS 701
- https://www.oregonlegislature.gov/bills_laws/ors/ors479.html — ORS 479

</details>

<details><summary>PA — Pennsylvania (8 sources)</summary>

- https://www.pacodeandbulletin.gov/Display/pacode?file=/secure/pacode/data/034/chapter403/s403.21.html&d=reduce — 34 Pa. Code 403.21 Uniform Construction Code
- https://www.phila.gov/media/20251021080549/B-2501-Implementation-of-2021-International-Codes.pdf — Philadelphia L&I Code Bulletin B-2501
- https://www.phila.gov/departments/department-of-licenses-and-inspections/ — Philadelphia L&I home (2021 code effective date)
- https://www.pacodeandbulletin.gov/secure/pacode/data/028/chapter18/028_0018.pdf — 28 Pa. Code Chapter 18
- https://www.pa.gov/agencies/health/business-registration-and-regulation/bathing-places/application — PA Department of Health bathing places permitting
- https://codelibrary.amlegal.com/codes/philadelphia/latest/philadelphia_pa/0-0-0-310874 — Philadelphia Code Subcode B 1809
- https://water.phila.gov/drops/2025-drinking-water-quality-report/ — PWD 2025 water quality report
- https://www.phila.gov/departments/department-of-licenses-and-inspections/licenses/trade-licenses/ — L&I trade licenses

</details>

<details><summary>RI — Rhode Island (8 sources)</summary>

- https://rules.sos.ri.gov/regulations/part/510-00-00-1 — 510-RICR-00-00-1 State Building Code (IBC 2021)
- https://rules.sos.ri.gov/regulations/part/510-00-00-2 — 510-RICR-00-00-2 One and Two-Family Dwelling Code (IRC 2021)
- https://rules.sos.ri.gov/regulations/part/510-00-00-5 — 510-RICR-00-00-5 Electrical Code (NEC 2023)
- https://rules.sos.ri.gov/regulations/part/510-00-00-14 — 510-RICR-00-00-14 Swimming Pool and Spa Code (ISPSC 2021)
- https://rules.sos.ri.gov/regulations/part/216-50-05-4 — 216-RICR-50-05-4 Licensing Aquatic Venues
- https://webserver.rilegislature.gov/Statutes/TITLE23/23-22/23-22-1.htm — R.I. Gen. Laws 23-22-1
- https://webserver.rilegislature.gov/Statutes/TITLE5/5-65/5-65-1.htm — Contractors' Registration and Licensing Board definitions
- https://webserver.rilegislature.gov/Statutes/TITLE5/5-6/5-6-2.htm — Electricians, license required

</details>

<details><summary>SC — South Carolina (9 sources)</summary>

- https://www.llr.sc.gov/BCC/PDFfiles/Building%20Codes%20in%20Effect%20for%20South%20Carolina.pdf — Building Codes in Effect for South Carolina (12/2022)
- https://llr.sc.gov/bcc/PDFfiles/2024%20Code%20Adoption%20Notice.pdf — 2024 South Carolina Code Adoptions
- https://llr.sc.gov/bcc/BCAdoption.aspx — Building Codes Council adoption page
- https://charleston-sc.gov/1466/Notice-to-Construction-Industry — City of Charleston codes
- https://www.law.cornell.edu/regulations/south-carolina/chapter-61/subchapter-61-51 — R.61-51 text
- https://des.sc.gov/programs/bureau-water/recreational-waters/water-regulations-standards-public-swimming-pools — DES recreational waters
- https://www.scstatehouse.gov/code/t40c011.php — S.C. Code Title 40 Ch. 11
- https://llr.sc.gov/clb/ — LLR Contractors' Licensing Board
- https://www.charlestonwater.com/FAQ.aspx — Charleston Water System FAQ (hardness)

</details>

<details><summary>SD — South Dakota (8 sources)</summary>

- https://sdlegislature.gov/api/Rules — ARSD article index
- https://sdlegislature.gov/api/Rules/Rule/20:44.html?all=true — ARSD article 20:44 Electricians
- https://sdlegislature.gov/api/Rules/Rule/44:02.html?all=true — ARSD article 44:02 Lodging and Food Service
- https://sdlegislature.gov/api/Rules/Rule/20:54.html?all=true — ARSD 20:54 State Plumbing Code
- https://www.siouxfalls.gov/business-permits/permits-licenses-inspections/ordinances-codes/adopted-codes — Sioux Falls adopted codes
- https://www.siouxfalls.gov/files/assets/public/v/2/planning-and-development/building-services/adopted-building-codes/local-amendments-to-the-2024-irc.pdf — Sioux Falls 2024 IRC ordinance
- https://www.siouxfalls.gov/files/assets/public/v/2/planning-and-development/building-services/adopted-building-codes/2024-ispsc-amendments-10-15.pdf — Sioux Falls 2024 ISPSC ordinance
- https://www.siouxfalls.gov/files/assets/public/v/2/resident-services/water/water-quality/pw26_034-annual-water-report-multi-fold-brochure-1.pdf — Sioux Falls 2025 water quality data

</details>

<details><summary>TN — Tennessee (8 sources)</summary>

- https://www.iccsafe.org/advocacy/adoptions-map/tennessee/ — ICC Tennessee page
- https://www.law.cornell.edu/regulations/tennessee/Tenn-Comp-R-Regs-1200-23-05-.01 — Chapter 1200-23-05 Public Swimming Pools (also .02, .03, .04, .05, .06, .07, .08)
- https://www.nashville.gov/departments/health/environmental-health/food-and-public-facilities/apply-public-pool-permit — Metro Health pool plan review
- https://www.nashville.gov/departments/codes/codes-administration/adopted-codes-list — Metro adopted codes
- https://www.nashville.gov/departments/codes/news/metro-adopts-2024-international-building-codes — Metro news release 2025-07-23
- https://nashville.legistar.com/ViewReport.ashx?M=R&N=Master&GID=787&ID=7433986&GUID=B8491A2F-6B1C-4507-A310-2058064BA422&Extra=WithText&Title=Legislation+Details+(With+Text) — BL2025-898 text
- https://www.nashville.gov/sites/default/files/2026-05/2025_Consumer_Confidence_Report-FINAL.pdf — Nashville 2025 CCR
- https://www.nashville.gov/departments/codes/construction-and-permits/building-permit-process — Metro building permits

</details>

<details><summary>TX — Texas (11 sources)</summary>

- https://www.dshs.texas.gov/public-swimming-pools-spas/laws-rules-public-swimming-pools-spas — DSHS laws and rules page
- https://www.dshs.texas.gov/public-swimming-pools-spas — DSHS program page (decorative fountains not regulated)
- https://www.dshs.texas.gov/sites/default/files/CP-PoolSpa/docs/25-tac-chp-265-subchapter-l.pdf — 25 TAC 265 Subchapter L
- https://www.dshs.texas.gov/sites/default/files/CP-PoolSpa/docs/25-tac-chp-265-subchapter-m.pdf — 25 TAC 265 Subchapter M
- https://www.tdlr.texas.gov/electricians/compliance-guide.htm — TDLR electrical compliance guide (NEC 2023, effective 2023-09-01)
- https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/Pages/know_code.aspx — Dallas adopted codes
- https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/DCH%20documents/pdf/BI_Chapter%2062-Dallas%20Swimming%20Pool%20and%20Spa%20Code.pdf — Dallas Chapter 62
- https://dallascityhall.com/departments/sustainabledevelopment/DCH%20documents/adopt%202023%20National%20Electrical%20Code%20w%20edits.pdf — Dallas 2023 NEC ordinance
- https://dallascityhall.com/departments/sustainabledevelopment/buildinginspection/DCH%20documents/pdf/code_amendments/2021_codes/23-0471.pdf — Dallas Chapter 57 ordinance (Table R301.2(1))
- https://dallascityhall.com/departments/waterutilities/DCH%20Documents/pdf/water-quality-report.pdf — Dallas 2025 Water Quality Report
- https://texas.public.law/statutes/tex._occ._code_section_1305.102 [secondary] — Occupations Code 1305.102

</details>

<details><summary>UT — Utah (15 sources)</summary>

- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-41 — R392-302-41 Interactive Water Features
- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-35 — R392-302-35 cryptosporidiosis outbreak watches (subsection 8)
- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-5 — Design requirements and plan approval
- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-3 — Definitions
- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R392-302-1 — Authority and purpose
- https://le.utah.gov/xcode/Title15A/Chapter2/C15A-2-S103_2025070120250507.html — Utah Code 15A-2-103 (7/1/2025 version)
- https://le.utah.gov/xcode/Title15A/Chapter2/C15A-2-S103_2026050620260701.html — Utah Code 15A-2-103 (7/1/2026 version)
- https://le.utah.gov/Session/2025/bills/enrolled/HB0313.pdf — H.B. 313 (2025) NEC 2023
- https://le.utah.gov/Session/2026/bills/enrolled/HB0065.pdf — H.B. 65 (2026) 2024 I-codes
- https://www.slc.gov/buildingservices/wp-content/uploads/sites/9/2026/07/SLC-Design-Criteria-2024-IBC_IRC.pdf — SLC Design Criteria
- https://www.slc.gov/buildingservices/2026/04/23/design-criteria-2/ — SLC Design Criteria web page
- https://www.slc.gov/utilities/wp-content/uploads/sites/22/2026/06/2026-04_Water-Quality-Report-2026-digital-FNL.pdf — SLC Public Utilities 2026 Water Quality Report
- https://www.law.cornell.edu/regulations/utah/Utah-Admin-Code-R156-55a-301 — Utah contractor classifications
- https://commerce.utah.gov/dopl/electrical/apply-for-a-license/ — DOPL electrical licensing
- https://slco.org/health/water-quality/swimming-pools — Salt Lake County Health Department pools

</details>

<details><summary>VT — Vermont (9 sources)</summary>

- https://firesafety.vermont.gov/sites/firesafety/files/Rules/2025%20Vermont%20Fire%20%26%20Building%20Safety%20Code.pdf — Vermont Fire & Building Safety Code 2025
- https://firesafety.vermont.gov/sites/firesafety/files/Rules/2025%20Vermont%20Electrical%20Safety%20Rules.pdf — Vermont Electrical Safety Rules 2025
- https://firesafety.vermont.gov/node/149 — DFS adopted codes page
- https://legislature.vermont.gov/Documents/2026/Workgroups/House%20Energy%20and%20Digital/Bills/H.718/Witness%20Documents/H.718~Michael%20Desrochers~Energy%20Efficiency%20Division%20of%20Fire%20Safety~1-30-2026.pdf — DFS testimony on IRC non-adoption
- https://www.healthvermont.gov/sites/default/files/document/reg-lodging-establishment.pdf — Licensed Lodging Establishment Rule
- https://www.healthvermont.gov/environment/recreational-water/managing-recreational-swimming-sites — Health Dept guidance (MAHC)
- https://www.law.cornell.edu/regulations/vermont/13-027-Code-Vt-R-13-140-027-X — Public Spas and Hot Tubs rule
- https://www.law.cornell.edu/regulations/vermont/agency-13/subagency-140 — Health Dept rule chapter list
- https://www.burlingtonvt.gov/DocumentCenter/View/12198/2025-Consumer-Confidence-Report-PDF — Burlington 2025 water quality report

</details>

<details><summary>VA — Virginia (8 sources)</summary>

- https://law.lis.virginia.gov/admincode/title13/agency5/chapter63/section10/ — 13VAC5-63-10 USBC Section 101
- https://law.lis.virginia.gov/admincode/title13/agency5/chapter63/section300/ — 13VAC5-63-300 Chapter 27 Electrical
- https://law.lis.virginia.gov/admincode/title12/agency5/chapter460/ — 12VAC5-460 Tourist Establishment Swimming Pools and Other Public Pools
- https://law.lis.virginia.gov/admincode/title12/agency5/chapter460/section30/ — 12VAC5-460-30 Plan review
- https://law.lis.virginia.gov/admincode/title12/agency5/chapter460/section400/ — 12VAC5-460-400 Spray pool water supplies
- https://law.lis.virginia.gov/admincode/title18/agency50/chapter22/section30/ — 18VAC50-22-30 classifications
- https://law.lis.virginia.gov/admincode/title18/agency50/chapter30/ — 18VAC50-30
- https://law.lis.virginia.gov/vacode/title35.1/chapter1/section35.1-11/ — Va. Code 35.1-11

</details>

<details><summary>WA — Washington (11 sources)</summary>

- https://sbcc.wa.gov/ — State Building Code Council: 2021 editions and 2024 schedule
- https://app.leg.wa.gov/WAC/default.aspx?cite=51-50&full=true — WAC 51-50 (IBC 2021)
- https://app.leg.wa.gov/WAC/default.aspx?cite=51-51&full=true — WAC 51-51 (IRC 2021, R327 ISPSC)
- https://app.leg.wa.gov/WAC/default.aspx?cite=296-46B-010&full=true — WAC 296-46B-010 (NEC)
- https://app.leg.wa.gov/WAC/default.aspx?cite=296-46B-680 — WAC 296-46B-680 (Art. 680 amendments)
- https://app.leg.wa.gov/WAC/default.aspx?cite=246-260&full=true — chapter 246-260 WAC
- https://kingcounty.gov/en/dept/dph/health-safety/environmental-health/healthy-water-air-soil/water-recreation-program/plan-review-process-fees — King County plan review
- https://www.seattle.gov/documents/Departments/SDCI/Codes/SeattleResidentialCode/2021SRCChapter3.pdf — Seattle Residential Code Table R301.2
- https://www.seattle.gov/documents/Departments/SPU/Services/Water/Analyses/SPU_Quarterly-Surface-Water-Analysis_05-20-2026.pdf — SPU hardness
- https://app.leg.wa.gov/RCW/default.aspx?cite=19.28.041 — RCW 19.28.041
- https://app.leg.wa.gov/RCW/default.aspx?cite=18.27.020 — RCW 18.27.020

</details>

<details><summary>WV — West Virginia (9 sources)</summary>

- https://firemarshal.wv.gov/media/39311/download?inline= — 87CSR4 State Building Code (State Fire Commission)
- https://apps.sos.wv.gov/adlaw/csr/rule.aspx?rule=87-04 — SOS rule version list for 87-04
- https://apps.sos.wv.gov/adlaw/csr/rule.aspx?rule=64-16 — SOS rule version list for 64-16
- https://www.law.cornell.edu/regulations/west-virginia/agency-64/title-64/series-64-16 — 64CSR16 sections
- https://code.wvlegislature.gov/16-1-4/ — W. Va. Code 16-1-4
- https://code.wvlegislature.gov/30-42-3/ — Contractor licensing definitions
- https://code.wvlegislature.gov/29-3B-2/ — Electrician licensing
- https://www.charlestonwv.gov/sites/default/files/documents/2022-08/2018%20BUILDING%20CODES%20TAKE%20EFFECT%20AUGUST%201.docx — Charleston adopted codes
- https://amwater.com/resources/ccr/kanawhavalley.pdf — 2025 Kanawha Valley CCR

</details>

<details><summary>WI — Wisconsin (7 sources)</summary>

- https://docs.legis.wisconsin.gov/code/admin_code/sps/safety_and_buildings_and_environment/388/390.pdf — SPS 390
- https://docs.legis.wisconsin.gov/code/admin_code/atcp/055/76.pdf — ATCP 76
- https://docs.legis.wisconsin.gov/code/admin_code/sps/safety_and_buildings_and_environment/301_319/316.pdf — SPS 316
- https://docs.legis.wisconsin.gov/code/admin_code/sps/safety_and_buildings_and_environment/361_366/361.pdf — SPS 361
- https://docs.legis.wisconsin.gov/code/admin_code/sps/safety_and_buildings_and_environment/320_325/321.pdf — SPS 321
- https://dsps.wi.gov/Pages/Programs/Pools/Default.aspx — DSPS pools program
- https://city.milwaukee.gov/ImageLibrary/Groups/WaterWorks/Consumer-Confidence-Reports/2025-Distribution-System-Water-Quality.pdf — Milwaukee 2025 distribution water quality

</details>

<details><summary>WY — Wyoming (10 sources)</summary>

- https://wsfm.wyo.gov/electrical-safety/rules-statutes-and-jurisdictions — Wyoming State Fire Marshal: adopted codes, rules and statutes
- https://wsfm.wyo.gov/electrical-safety/electrical-safety-updates — adopted codes list (2024 I-codes, 2026 NEC)
- https://drive.google.com/file/d/1qqOeGyLuVScqf8-_218IsBcYuht3GckF/view (W.S. 35-9-101 to 35-9-130, linked from wsfm.wyo.gov)
- https://drive.google.com/file/d/1i-Olc8bQZTv4AgZ4vZmkjKjG5xpFvx_n/view (Council on Fire Prevention & Electrical Safety Rules, Ch. 1, linked from wsfm.wyo.gov)
- https://agriculture.wy.gov/corecode/uploads/document6/uploaded_pdfs/corecode/All%20Chapters%201-7%20Rules%20for%20Public%20Swimming%20Pools,%20Spas,%20&%20Aquatic%20Facilities%20_1758.pdf — Wyoming Public Swimming Pools rules, chapters 1-7
- https://agriculture.wy.gov/pools — Wyoming Dept. of Agriculture pools page
- https://www.iccsafe.org/advocacy/adoptions-map/wyoming/ — ICC Wyoming
- https://www.cheyennecity.org/Your-Government/Departments/Compliance-Department/Building-Permitting-Licensing/ICC-2024-Building-Codes — Cheyenne adopted codes and design criteria
- https://www.cheyennecity.org/Services/Building-Safety — Cheyenne Building Safety
- https://www.cheyennebopu.org/Water/Water-Quality/Hardness — Cheyenne BOPU hardness

</details>

### Open questions

Cross-cutting:
- **Utah hardness** (settled in v1.772.0). The engine's 250 ppm was above every SLC treatment plant in the 2026 report
  (136–212 ppm); only some wells and springs run higher (154–571). Nikolas's rule that cited values ship set it to 212.
- **Utah air temperature.** The engine's 95 °F is below ASHRAE 2025's 96.4 °F (2021: 95.9 °F) for SLC International.
- **Water temperature** is a Sapphire basis (wet-bulb + 5 °F), not a published figure. Confirm it with the designers.
- **`iwf_like_utah` needs all four elements.** Ohio, Kentucky, Wisconsin, North Carolina, Iowa and Indiana each meet
  three of the four. They run the Utah checklist as warnings; decide whether any should run as blockers.
- **Code-adoption gaps.** Where a state has no statewide code (`basis: local`), the design city's edition is in the cite.
  Other cities in that state differ.
- **MAHC `none`** usually means no MAHC use was found in the rule text, not that the state disclaims it.

By state, from the research (each also appears in that state's cites where it bears on a value):

**AL — Alabama**

- Whether Alabama has any statewide public pool / spa / splash-pad rule (none found on ADPH rules page); if county-based, which Jefferson County Department of Health rule applies and whether plan review is required.
- IRC edition adopted by the Alabama Home Builders Licensure Board and whether any commercial IBC/NEC applies statewide beyond DCM's scope.
- Birmingham frost depth (IRC R301.2(1)) and hardness (latest Birmingham Water Works / Central Alabama Water report) both unresolved: sites blocked or unreachable.
- Exact ALBGC classification name and thresholds (the $5,000 pool figure came from the Board's law summary page, not the statute text).

**AZ — Arizona**

- Arizona pool/IWF regulation entirely unverified: ADEQ administrative code (public and semipublic pools) and the Maricopa County Environmental Health Code were unreachable (azsos.gov, azdeq.gov, roc.az.gov 403; Cornell LII refused connections; mirrors behind bot checks). Need: whether splash/spray pads are regulated, turnover, collection tank, secondary disinfection, ORP/pH control, fogger potable-water rule, plan-review authority and MAHC use. All regulation booleans are null.
- Contractor classification: pool, spa and fountain license classes at the Arizona Registrar of Contractors not read; electrician licensing status unverified.
- Hardness 237 ppm is the midpoint of Phoenix's reported 172-302 ppm distribution-system range, not a stated typical value. Phoenix's report also lists sodium 91-183 ppm and TDS 464-716 ppm.
- Frost depth is published by Phoenix as '0' (inches); it is a design-table entry, not a recommended footing depth.
- No statewide NEC/IBC/IRC/ISPSC: editions left null; Phoenix's adoption (2024 IBC/IRC/ISPSC, 2023 NEC, effective 2025-08-01) recorded in cites. Other Arizona jurisdictions (Maricopa County unincorporated, Tucson) differ and were not checked.
- A Phoenix 2023 NEC amendment document exists; Article 680 content was searched by text and none found, but the PDF text was not read in full.
- Coordinator gap-fill used search excerpts of Cornell LII, a Mohave County page and Maricopa County's Ch. VI §10 PDF; read A.A.C. R18-5-201 to -249 and Maricopa Ch. VI §10 to settle splash-pad coverage, turnover, UV, controllers and misters.

**AR — Arkansas**

- Statewide NEC edition and effective date: not found (Little Rock enforces 2020 NEC locally per a Jan 2023 sheet). Needs the Arkansas Dept of Labor and Licensing electrical rules.
- Effective date of the 2021 Arkansas Fire Prevention Code (IBC/IRC bases) and whether a newer AFPC has been adopted since the Jan 2023 Little Rock sheet; state sites dps.arkansas.gov / arkansasfireprevention.com gave no edition.
- Little Rock frost line depth (Table R301.2(1)): not found; municode and the AFPC Vol 3 table were not readable.
- Hardness 23 mg/L is the midpoint of CAW's FAQ range 17-29 mg/L (the 2025 CCR has no hardness line); confirm with CAW if a CCR-based figure is required.
- ADH pool rule read is dated Aug 1, 2012; ADH says rules moved into the Code of Arkansas Rules effective 2025-01-01 and may have been revised. Contractor classification for pools (Contractors Licensing Board) and dollar threshold for commercial licensing not read.
- iwf_like_utah=false: Arkansas sets no fixed IWF standards (case-by-case Class C review).

**CA — California**

- Frost depth 12 in for Los Angeles is from a secondary source citing the 2020 City of LA Residential Code Table R301.2(1); could not reach LADBS's current table (the 2023 LARC). Value is a nominal footing minimum, not a real frost line.
- Hardness 103 mg/L is the LA Aqueduct source only; LADWP water blends sources by zone (MWD-fed areas are 234-236, Harbor/Eastern 140). The designer may want to pick per project location.
- Fountain contractor classification: CSLB has no distinct 'fountain' class that I could find; which class covers a purely decorative fountain is unconfirmed.
- Whether a recirculating decorative fountain that is not a play feature falls under 'public pool' (Title 22/24) in LA County, versus a once-through potable-to-waste interactive fountain: rule text read addresses spray grounds with surge basins only.
- CBC/Title 22 sections were read from third-party reproductions (gocodebook; Alameda County compiled code book dated 2023-01-01) because the official CBSC/OAL sites were not machine-readable; Title 22 may have been amended since 2023.
- Statewide ISPSC status is from CBSC Bulletin 19-06 (2019) plus absence from the 2025 Title 24 parts list; no 2025 adoption notice found either way.

**CO — Colorado**

- ibc/irc/ispsc: Colorado has no statewide code (basis local); Denver's own editions (2024 IBC/IRC, adopted 2025-06-13) are in the cite but the table has no slot for a city edition.
- NEC: the state adopted the 2026 NEC effective 2026-08-01, but municipalities have up to a year to adopt it locally; Denver's own NEC edition was not confirmed (Denver Building Code says NFPA 70 means 'the Electrical Code as adopted by the State of Colorado').
- iwf: the state rule has no interactive-feature section; 'spray pool' (water not allowed to pond) is covered as a pool. Denver DDPHE's own Pool Rules and Regulations (DRMC ch. 51) were not read for splash-pad design criteria (turnover, UV, ORP), so iwf_like_utah=false is based on the state rule only.
- mahc: marked 'referenced' because the state incorporates MAHC 3rd ed. by reference only as a local option; could be classed 'adopted' for jurisdictions that opted in.
- contractor: the absence of a state general-contractor license rests on a DORA consumer page seen through a search summary; the statute was not checked.
- hardness: 105 mg/L is Denver Water's system-wide average; the report notes the southern system is harder and the northern softer, with a seasonal range of 55-124 mg/L.

**CT — Connecticut**

- hardness_ppm null: MDC's 2025 report gives calcium (avg 3.09 ppm) but not total hardness or magnesium; need MDC lab (860-278-7850) or an older report with a hardness line.
- Whether DCP has adopted the regulations that make the CGS 20-340d swimming pool builder license operative (statutory deadline was 2012); not verified.
- CT's rule text for spray pads is in the 2021 manual (spray pool definition: no standing water) and the July 2026 forms; DPH may have a newer design guide than April 2021 that was not found. iwf_like_utah false rests on UV 'if applicable' and no ORP/pH controller requirement in the documents read.
- CSBC 2022 is still the current edition per the DAS page read 2026-11-01; a newer cycle (2024 I-codes) may be pending but none was found.

**DE — Delaware**

- State building codes: no statewide IBC/IRC/ISPSC adoption found (only county/city adoptions); a possible state-level mandate (e.g. for state-owned buildings or schools) was not checked. Wilmington's edition is 2018 for IBC/IRC/ISPSC.
- NEC: statewide 2020 NEC comes from State Fire Prevention Regulation 701; effective date not shown (last register cite 25 DE Reg. 71, 07/01/21) and whether Wilmington enforces it or a different edition was not confirmed.
- Whether 16 DE Admin Code 4464 was amended after Nov 29 2023 (Delaware moved its regulations site in 2025; the amendment history of the archive page ended 2015).
- Delaware state business license and any Division of Professional Regulation contractor registration for pool/fountain work not verified.
- Hardness: used 145 mg/L (mean of Brandywine 136 and Porter 154 plant averages from the 2025 report); actual tap value depends on which plant serves the site.

**FL — Florida**

- Miami frost depth: FBC Residential Table R301.2(1) says 'NA' statewide; no numeric published. Coordinator may decide to treat as 0.
- Miami hardness: Miami-Dade WASD 2024 and 2025 CCRs publish no hardness value; need a WASD hardness fact sheet or a direct request.
- NEC edition: 2020 NEC confirmed via a county page and a Commission analysis summary, not the FBC chapter text itself; the 9th edition (2026) is expected to move to a newer NEC.
- FBC 454.1.9.8 text was read only through up.codes [secondary]; ICC codes site blocked (403).
- Fogger/mister potable requirement: not found in 64E-9 or the IWF section as read; FBC s. 454 not read in full.
- MAHC: no Florida rule text cites the MAHC; 'referenced' rests on a 2018 JEH column by a FL DOH administrator.
- Journeyman electrician licensing in Florida: state vs local split not verified in primary text.

**GA — Georgia**

- Contractor and electrician licensing for Georgia (Construction Industry Licensing Board / Residential and General Contractors Board): sos.ga.gov returned 403/bot challenge and web-search budget ran out. Needs a follow-up read.
- Atlanta frost depth (IRC R301.2(1) via City of Atlanta amendments) and water hardness (DWM reports have no hardness line) both null; check Atlanta DWM lab or a city building-code amendment page.
- In-force editions: adoption-history PDF lists 2024 codes at 01-01-26 while the Dec 9, 2025 DCA announcement says 2027-01-01; table uses 2018 IBC/IRC/ISPSC (eff. 2020-01-01) and 2023 NEC (eff. 2025-01-01) as in force on 2026-11-01. Confirm that DCA did postpone to 2027.
- Rule 511-3-5 history note / last amendment date not obtained; ORP-specific automation in IWFs not word-for-word; mister rules read via summarised passes, so 'false' for fogger_potable should be double-checked.

**ID — Idaho**

- hardness_ppm null: Boise's latest Veolia Water Idaho Consumer Confidence Report could not be reached (zip-code form, no direct PDF). Secondary sites suggest roughly 100-150 ppm (6-9 gpg) but this is not cited as a value. Needs the Veolia CCR PDF.
- Whether Idaho regulates splash pads/spray grounds: IDAPA 16.02.14 (2024 archive text, last amended 3-15-22) has no IWF provisions; the 2025/2026 current rule text could not be opened (adminrules.idaho.gov 'current' URL returned HTML), so a later amendment is not ruled out. Ask Central District Health how it treats splash pads, and whether DEQ reuse permits apply.
- NEC effective date: used DOPL's enforcement transition of 2024-04-19 (primary memo). Statute 54-1001 was added in 2023 (ch. 244) and a secondary source (IAEI) lists 2023-07-01 as the statutory date; the actual statute effective date was not read. Idaho amendments to NEC Article 680 not checked.
- ISPSC marked basis 'none': Idaho has no statewide ISPSC; city or county adoption (e.g. Boise) was not checked.
- Idaho IBC 2018 is current but a bill (draft EAL, 8/2026) would adopt 2024 IBC/IRC effective 2028-07-01 if passed; in Feb 2026 the House Business Committee rejected the earlier IBC rule (boisedev.com report seen in search results, not read). Re-check each legislative session.
- Contractor licensing: no primary page found that confirms there is no pool-specific state class; inferred from the Idaho Contractor Registration Act text and absence on DOPL pages.

**IL — Illinois**

- Chicago frost depth: Chicago/amlegal sites blocked all fetches (403) and the web-search budget ran out; needs the Chicago Building Code (Title 14B) footing/frost provision.
- Chicago hardness: latest Department of Water Management water quality report not reachable (403); needs hardness in mg/L or gpg.
- Chicago's adopted edition of the NEC (Chicago Electrical Code, Title 14E) and IBC basis not confirmed; whether Chicago pools fall under an ordinance health department (820.610) not confirmed.
- No statewide electrician/electrical contractor licensing statement from a primary source; assumed local from absence in pages read.
- IBC/IRC edition: statute sets a rolling 10-year baseline, I recorded 2018 as the oldest edition in the window; which edition is 'least restrictive for structural design' not determined.
- Is the IDPH Part 820 text current? Last amendment recorded is 2013 (37 Ill. Reg. 16539); a later rewrite was not checked.

**IN — Indiana**

- Electrical licensing: concluded only from the Indiana PLA list (no electrician/contractor entry); local licensing rules for Indianapolis not checked.
- Indiana Electrical Code is on NEC 2008 per the DHS rule table, and an 'Electrical Code Update (2026)' docket is open; the new edition and timing are unknown.
- No pH-specific automatic controller requirement found in 675 IAC 20-5-23, so iwf_like_utah=true depends on the electronic disinfectant monitor plus UV/ozone.
- Whether IDOH or a local health department also issues a construction approval or operating permit for splash pads was not confirmed (410 IAC 6-2.1 is an operation rule).
- Frost depth is the state Table R301.2(1) residential footing depth for Marion County; the City of Indianapolis amendment, if any, was not checked.
- Art. 680 state amendments to the Indiana Electrical Code were not read.

**IA — Iowa**

- iwf_like_utah is true on a close reading: 30 min turnover, storage tank, automatic ORP and pH controllers (via 485.5(11)/(12), which say 'swimming pool' but are pulled in by 485.5(19) 'applicable provisions') but NO UV or secondary disinfection requirement. Confirm with DIAL that ORP/pH controllers are enforced on spray pads.
- Iowa NEC effective date 2025-07-01 is the effective date of the current 481 IAC 404 text (ARC 9033C); the rule previously filed 12/30/15 and the 2023 edition may have been in effect earlier.
- Iowa state building code is not applied everywhere: it binds state buildings and jurisdictions that adopt it. Des Moines adopts 2024 I-codes (2026-01-01) with local amendments; Des Moines also adopts the 2015 ISPSC locally.
- Des Moines frost depth (42 in) is quoted from a deck handout; the municipal code's IRC Table R301.2 entry could not be read (municode blocks automated fetches).
- Hardness is not in the Des Moines Water Works CCR; value is the 12-month mean of the utility's own published daily data for the Fleur plant (the Des Moines main supply), not a CCR figure.

**KS — Kansas**

- Is there any Kansas state public-pool or spray-ground program? kdhe.ks.gov blocked automated access; conclusion rests on the absence of a pool article in K.A.R. agency 28 (LII mirror) and no pool statute in K.S.A. chapter titles. Confirm with KDHE.
- Wichita hardness_ppm is null: wichita.gov (403) hosts the water-quality report. Needs a manual pull from the City of Wichita Water Utilities CCR.
- Does the City of Wichita or Sedgwick County Health license public pools or splash pads (operating permits, health inspection, plan approval)? Wichita municode and wichita.gov were not reachable.
- Statewide NEC/IBC/IRC: no state instrument beyond the State Fire Marshal's fire code found. The LII copy of K.A.R. 22-1-3 shows 2006/2008 editions and may be stale; the current rules.ks.gov text could not be rendered.
- No state source confirms the absence of statewide contractor and electrician licensing; cited only the local MABCD licensing.
- No Kansas state board for contractor or electrical licensing was located; confirm there is none.

**KY — Kentucky**

- Kentucky still enforces 2015 IBC/IRC (2018 KBC/KRC); a blog claimed 2024 I-codes were adopted late 2025 but no DHBC/KAR source supports it (815 KAR 7:120 page generated 2026-08-28 still says 2015). Re-check before release.
- Louisville Metro blocked automated fetches (403), so Metro permit and plan-review details are from search snippets only; Louisville Metro may amend the state frost depth (24 in used from the state table).
- No statewide general/pool contractor license: confirmed only via a secondary guide; plumbing/HVAC state licensing (DHBC) not verified this pass.
- iwf_like_utah=false because Kentucky has no automated ORP/pH controller requirement, though it meets turnover (30 min), holding tank and mandatory UV.
- IBC effective date used is the 2018 KBC date (2018-08-22); the rule was re-promulgated with amendments eff. 2024-12-03.

**LA — Louisiana**

- frost_depth_in: New Orleans' adopted IRC Table R301.2(1) frost line value not found; ask Safety and Permits.
- hardness_ppm: only the 2014 SWBNO figure (163 mg/L East Bank) found; recent CCRs omit hardness and the LDH CCR docx returned 403. Value is old but utility-published; SWBNO lab 504-865-0420 can give a current one.
- Code editions: LUCCC shows a Notice of Intent (Aug 20, 2026) for 2023 NEC / 2024 I-Codes effective 2027-01-01; ICC NTA (secondary) says adopted 2026-10-13. Table records editions in force on 2026-11-01 (2021 ICC, 2020 NEC); confirm final rule.
- Whether OPH regulates recirculating spray grounds under Class D or as a water park, and what criteria it applies in practice; the Cornell copy of Part XXIV may lag the official LAC.
- Commercial pool contractor license dollar threshold not confirmed.

**ME — Maine**

- www.maine.gov is blocked to automated access (HTTP 403 on every URL tried), so no DHHS pool rule text, no Electricians' Examining Board NEC rule, and no MUBEC edition rule could be read. The three code editions (NEC, IBC, IRC), the IWF and fogger answers, MAHC status, and the pool-rule number/effective date are therefore null or unconfirmed.
- MUBEC edition in force (2021 or 2024 I-codes) and its effective date; whether the board added the ISPSC by rule.
- Portland, ME frost depth (city inspections or MUBEC IRC Table R301.2 amendments) was not found.
- Whether recirculating splash pads and interactive fountains count as 'pools' under 22 MRSA s.2662 and what DHHS requires for them.
- Hardness 7.2 mg/L is the single value in the PWD 2026 report, not a range or average.
- Statewide general contractor licensing: no source read beyond the trade-license statutes.

**MD — Maryland**

- NEC edition: Maryland statute names the NEC with no edition and lets each county use its own code; Baltimore City's code says 2020 NEC (Part III, Ord. 24-341). A secondary source claims State adoption of the 2023 NEC effective 2025-03-01 that could not be confirmed on a primary State page; edition left null.
- Hardness: Baltimore's CCR omits it; 94 mg/L is derived from the 2021 plant mineral-analysis forms (Ashburton about 78, Montebello about 110) whose text layers are misaligned; verify by calling DPW or reading the form image.
- Baltimore City: whether the City Health Department or MDH reviews pool/IWF plans (MDH page says some local jurisdictions elected to review in place of MDH); Baltimore City pool licensing page not found.
- Whether MDH has since adopted a rewrite of COMAR 10.17.01 (spray ground definition, MAHC); MDH page shows revisions in workgroup only, and the notice about suspended development could not be read.
- Local building-department permit rules for a decorative fountain with no public bathing (not covered by 10.17.01) were not researched.

**MA — Massachusetts**

- Electrical code: mass.gov was unreachable (HTTP 403); NEC 2026 / 2026-04-24 rests on ICC-NTA (read directly) plus a mass.gov search excerpt. Whether 527 CMR 12.00 amends Art. 680 not checked.
- IBC/IRC 2021 and the 780 CMR 10th Edition basis rest on mass.gov handbook search excerpts; mass.gov blocked direct fetch. The 780 CMR 101.4.15 ISPSC text was read directly on law.cornell.edu.
- 105 CMR 435 text read is a 3/20/98 copy on a town site; the official mass.gov page (dated 2004) could not be fetched, so later amendments cannot be ruled out.
- Boston frost depth: 48 in is the statewide Table R301.2(1) value from a Justia search excerpt; no Boston-specific figure; confirm with Boston ISD.
- Hardness 13.1 mg/L is MWRA's 2025 Metro Boston summary on its Water and Health page, not a line in the Boston CCR PDF (no hardness row). MWRA FAQ gives 16 mg/L.
- No state standard for splash pads / interactive water features; how local boards of health classify recirculating spray pads was not found.

**MI — Michigan**

- Interactive water features / splash pads: no EGLE rule or program page reached (michigan.gov blocked; Cornell host stopped responding after R 325.2137). iwf_regulated, iwf_rule and fogger_potable are null.
- Effective date of the Michigan Electrical Code Part 8 based on the 2023 NEC not found; amendments to NEC Art. 680 not checked.
- MAHC: only R 325.2111-2137 read; MAHC use by EGLE could not be ruled out.
- Detroit-specific frost depth: used the state 42 in. value from Table R 301.2(1); Detroit's own amendment not checked.
- Pending code changes: 2024 IBC (rule set 2025-48 LR) and 2026 NEC (2026-17 LR) are in rulemaking and may replace the editions recorded.
- Whether a residential builder license is required for pool or fountain work on a dwelling was not determined from the statute text read.

**MN — Minnesota**

- IWF: whether MDH regulates standalone recirculating splash pads (and what turnover, UV, ORP/pH control it requires) is not in the rule text; it is a case-by-case 4717.0450 subp. 1 E review. iwf_regulated left null. Ask MDH (health.swimmingpools@state.mn.us).
- Minnesota is mid-cycle: DLI is rulemaking to adopt the 2024 IBC (docket opened Jan 2025, no effective date). Current IBC and IRC are 2018 editions.
- MAHC: no MDH statement found either way; recorded none.
- Minnesota amendments to NEC Article 680 not checked.
- Hardness: the Minneapolis CCR gives none; used mean of 11 monthly plant-effluent analyses (July 2026 missing). Annual 2024/2025 summary sheets are garbled.
- The 2025 city planning document claim of a pending MDH shower requirement for splash pads is unverified (secondary).

**MS — Mississippi**

- MSDH public swimming pool / spa / interactive water feature rule: citation, text, plan-review requirement, IWF and mister provisions all unresolved (no direct URL reached; web-search budget exhausted mid-run). Highest priority for Mississippi.
- Statewide NEC: sources conflict (local-only vs. a 2014 NEC 'statewide' entry); Jackson itself uses 2017 NEC. Confirm with Mississippi Insurance Department / State Fire Marshal.
- Which editions the Mississippi Building Codes Council currently has adopted (2018 per secondary source; ICC NTA notes a 2024 adoption only for modular construction, effective 2026-07-01).
- Jackson frost depth (IRC R301.2(1) in adopted ordinance) and hardness (JXN Water CCRs carry no hardness) both null.
- Whether MSBOC pool classification covers decorative fountains and whether statewide journeyman licensing exists.
- MSDH agency role and MAHC status set null by the coordinator: the agent could not read any rule text, and the only source naming MSDH was a secondary vendor page.

**MO — Missouri**

- No primary source was found stating outright that Missouri has no statewide building or electrical code; ICC and NFPA adoption maps blocked or did not load. Conclusion rests on RSMo 67.280 (local adoption by ordinance) and the DHSS recreational-water page (local programs).
- City Code ch. 11.79 text (definitions, turnover, spray pool rules, potable-water mister rules) could not be read because Municode blocks automated fetches; fogger_potable is null and only checklist excerpts were read. Obtain ch. 11.79 from the Health Department.
- St. Louis adopted-code page may be stale: it still lists 2018 IBC/IRC and the 2017 NEC (adopted 2018-08-01); a newer ordinance may exist.
- Frost depth 30 in comes from the city fence footing guideline, not from IRC Table R301.2 in Title 25 (Municode blocked).
- DHSS lodging-facility pool standard (hotel/motel pools) not read.
- St. Louis general contractor licensing not checked (only trade contractor boards found).

**MT — Montana**

- Billings's own adopted-codes page still lists 2021 IBC/IRC/ISPSC and 2020 NEC; the state's 2024 codes (NEC 2023) took effect 2026-09-26. Whether Billings is a certified local program for swimming pools (vs. DLI Building Codes Bureau reviewing) was not confirmed.
- ispsc edition: building code adoption is 2024 (eff. 2026-09-26) but the DPHHS pool rule ARM 37.115.106 still cites ISPSC 2021; the later (2024) edition is used.
- hardness: Billings's 2025 report gives only a range (53.5-197 ppm); 125 is the midpoint, not a utility-stated typical value.
- iwf_like_utah=false: splash decks need secondary disinfection and automated controllers but no stated 30-minute turnover or collector tank; the ARM rule text itself (not only the circular) was not read, since rules.mt.gov would not load.
- ARM rule numbers for pool regulation beyond 37.115.106 and the MCA chapter for public pools were not confirmed from primary text.

**NE — Nebraska**

- Omaha frost depth is null: Omaha's planning and code sites blocked automated access (403). Needs Omaha's amended IRC Table R301.2(1) from the city.
- Which IBC/IRC/NEC editions Omaha itself enforces (it may have adopted editions newer than the state's 2018 IBC/IRC) could not be read; the state values are the statewide floor.
- basis for IBC/IRC set to statewide because 71-6404 applies the state code wherever a local code is not adopted; Omaha may differ. Effective date of the current state code editions not read (last amendments in the 71-6403 source list are 2019).
- Whether Omaha (not just Nebraska) licenses pool or general contractors was not confirmed.
- Title 196 Chapter 2 (operations) was only text-searched for ORP, secondary disinfection and MAHC; iwf_like_utah false rests on that search plus the full Chapter 1 spray park section.
- Local operating-permit program for public pools in Omaha/Douglas County after LB 759 (effective 2026-07-18) is not documented.

**NV — Nevada**

- No statewide building, electrical or pool-code edition: basis is 'local', editions left null. Design-city values for Las Vegas (2024 IBC/IRC/ISPSC, 2023 NEC, effective 'January 2026') come from a City of Las Vegas web page that gives only a month; confirm the exact day and any transition rules. Other Nevada jurisdictions (Reno/Washoe, Carson City, Clark County unincorporated) were not checked.
- NEC effective date null: only 'January 2026' found for Las Vegas.
- SNHD regulation was read from the 2018 PDF (effective 2019-07-01); a later amendment or a newer consolidated version was not found. SNHD 'Regulation Updates' page returned 404.
- Whether SNHD's 'automated controllers' for disinfectant and pH count as the 'automated ORP and pH control' in Utah's rule: ORP is named only for ozone; iwf_like_utah is set true on turnover (0.5 h), collection tank, mandatory secondary disinfection and automated disinfectant/pH controllers, but it is a judgment call.
- SNHD secondary-disinfection rule applies to IWFs 'with no standing water' on new construction or substantial alteration; it does not name UV specifically (UV is one option).
- Statewide electrician (journeyman/master) licensing: none found at state level; inferred local from NAC 624 wording. Needs confirmation with Clark County / City of Las Vegas licensing.
- Hardness: LVVWD gives 280 ppm in the 2026 report but 291 ppm elsewhere on its FAQ page; 280 used (latest report). SNWA/Las Vegas area water is blended Colorado River (Lake Mead) supply.
- Frost line: IRC table says '1 ft below 5,000 ft'; the 2024 IBC amendment for commercial footings was not separately checked.

**NH — New Hampshire**

- Effective dates for NEC 2023 and the 2024 I-codes in RSA 155-A:1 IV: the live statute names them but the text gives no date; its source line shows laws eff. 2025-07-01 and 2026-07-01 (likely the latter for the 2024 codes). Manchester's page still lists the 2021 codes and NEC 2020, so the city may lag. effective is null.
- NH state amendments (Admin. Rules BCR Chapter 300, including any Art. 680 change) not read.
- Env-Wq 1100 page includes 2026 tracked-change markup: a later amendment may exist; only the 5-1-21 text was relied on.
- Manchester frost depth comes from a 2021 ordinance (4 ft); the city may have updated its Table R301.2 alongside the 2024 codes.
- Hardness 17.88 ppm is the 2022 report value; later reports omit the row. Plumber licensing (RSA 328-F) not read.
- NH DES web pages are blocked to automated fetch (Akamai); the rule text came from gencourt.state.nh.us.

**NJ — New Jersey**

- hardness_ppm 49 is from Newark's 2020 CCR (45.7-53 ppm); the 2022 and 2025 CCRs omit hardness and the 2021/2023/2024 PDFs are scanned images not read; Newark also receives some NJ District Water Supply Commission (Wanaque) water, not separately measured.
- iwf_like_utah is false because 8:26 gives no spray-park turnover/surge/UV/ORP rules; confirm with the local health authority whether it applies more in practice.
- fogger_potable false means no misting rule found in the sections read, not a full-text search of N.J.A.C. 8:26.
- 2024 codes took effect 2026-08-17 with a six-month grace (to about 2027-02-17) under which applicants may still elect the prior codes; the exact grace end date was not found.

**NM — New Mexico**

- hardness_ppm: ABCWUA's CCR/mailer states no hardness number; the utility's per-zone hardness page is an ArcGIS app that could not be read. Needs a manual lookup of the zone data or a utility contact.
- iwf_like_utah / fogger_potable: both depend on the CDC MAHC 1st Edition text incorporated by 7.18.1.9 NMAC, which could not be read (CDC blocks automated download; PDF too large for the fetch tool). NM omits MAHC 4.7.3.2.8.1.1 (automated controllers), so ORP/pH automation is not required via that section.
- frost_depth_in: 12 in. is the NM Residential Code minimum footing depth (statewide); no Albuquerque frost-line value or R301.2 table was found. The 2021 NM Commercial Code (14.7.2) has no frost amendment.
- nec: state rule 14.10.4 NMAC still shows the 2020 NEC (eff. 2023-03-28); a newer edition may be in rulemaking and was not checked beyond the SRCA text and the April 2026 CID hearing notice (which lists plumbing, pool, mechanical rules only).
- ispsc: NM uses the IAPMO USPSHTC (2012; 2024 edition proposed in an April 2026 draft rule, effective date blank), so ISPSC is marked not adopted. Whether the 2024 pool code has since been finalized was not confirmed.
- Albuquerque is a home-rule city with its own Uniform Administrative Code; its adoption dates (Dec. 14, 2023 building; Sept. 28, 2023 electrical) are as stated on the city page, whose tooltips conflicted on edition years.

**NY — New York**

- nec.edition is null: the NFPA 70 edition referenced by the 2025 Building/Residential Codes of NYS could not be read (ICC digital codes return 403 for automated access) and the amended adoption notice says some referenced standards reverted to 2020 editions. NYC uses its own 2025 Electrical Code based on the 2020 NEC.
- iwf_like_utah and fogger_potable are null: the spray-ground design standards (10 NYCRR 6-3.24, turnover/recirculation rate) were not readable once law.cornell.edu stopped answering and health.ny.gov returned 403. Sections 6-3.1 to 6-3.13 show a treatment tank, UV, and an automatic free-residual and pH controller, so the rule is probably Utah-like; turnover and ORP wording still need to be read.
- State Subpart 6-1 (pools) was only partly read (6-1.1 to 6-1.7, 6-1.31), and MAHC use was not searched across it; NYSDOH may have revised Part 6 more recently than the Cornell text.
- NYC specifics for the design city (NYC DOB electrician/home improvement licensing; Article 165 versus state Subpart 6-3 for spray grounds) were not fully verified; NYC DOB master electrician licensing was not read.

**NC — North Carolina**

- Which NC code edition is in force today: the OSFM page and ICC page still show the 2018 NC codes (2015 I-codes) with the 2024 codes (2021 I-codes) delayed; check whether the State Fire Marshal has since certified the 2024 code (effective 12 months later).
- Charlotte frost depth: no published value reached (ICC viewer blocked, Mecklenburg pages 404).
- Charlotte hardness: 39 ppm is one Franklin plant monthly reading; the CCR has no hardness row; Vest and Dukes plants not checked.
- Mecklenburg County Code Enforcement as the permit authority for Charlotte was not verified in this pass.
- NC general contractor pool classification not confirmed; G.S. 87-1 text does not name swimming pools.
- NEC 2020 effective date rests on a single line on the OSFM page whose older paragraph still says 2017.

**ND — North Dakota**

- Fargo frost depth: city handouts disagree (54 in garage/addition handout, 52 in deck handout, 4-ft and 5-ft elsewhere); 54 used. No IRC Table R301.2 entry for Fargo was found.
- Local pool and splash-pad rules (Fargo Municipal Code 13-10, Cass County Aquatic Code 225.501-.512) could not be read (ecode360 and Cass County sites blocked automated fetches), so IWF turnover, UV, ORP/pH, surge tank and potable-mister requirements are unknown; local rules may differ from the state's silence.
- ND NEC Article 680 state amendments not checked; the NDSEB notice says the 2023 NEC is adopted except where articles are specifically amended.
- IBC 105.2 'Swimming pools' permit-exemption wording in the 2026 state code book is ambiguous in extracted text; confirm with Fargo Inspections whether public pools need a building permit.
- Fargo adopted the 2024 I-codes locally on 2025-07-02, earlier than the state effective date of 2026-01-01.

**OH — Ohio**

- ISPSC: no state-level adoption confirmed; one county sheet lists a '2024 ISPSC' for residential from 2024-04-15. Need RCO amendment rule text (OAC 4101:8) to confirm.
- IRC edition: Ohio RCO is 2019 (2018 IRC base) with March 2024 amendments; no 2021-based RCO found. Confirm none adopted after 2024.
- ODH chapter 3701-31-05.1 text read is effective 2011-04-01 (rule 3701-31-03 amended 2024-07-25); iwf_like_utah=false but Ohio meets 3 of 4 Utah traits (tank, 30-min tank turnover, automatic pH/ORP controller); only UV/secondary disinfection is not required - coordinator may judge.
- Columbus frost depth 32 in comes from a CIC dated 2006/2012 still on the city site; no newer table seen. NEC 2024-03-01 effective date is the commercial (OBC) date; residential is 2024-04-15.
- State journeyman electrician licensing: ORC 4740 read via summarized fetch only; confirm there is no state journeyman license.

**OK — Oklahoma**

- hardness_ppm null: OKC's own CCR (2022 copy read) lists no hardness and the 2025 CCR PDF could not be downloaded; call (405) 297-3056 or open okc.gov/ccr in a browser and look for hardness / alkalinity.
- frost_depth_in 18 comes from an OKC criteria sheet seen only through a search excerpt (cites the 2020 OKC Municipal Code); OKC likely re-adopts the 2024 IRC after the state's 2026-09-14 date, so recheck Table R301.2(1).
- Is HB 3338 (2026 pool contractor licensing) enacted? Only the House committee summary was found. If enacted, CIB must set requirements by Nov 1, 2026.
- Splash pads: the 63 O.S. 1-1013 carve-out defines spray grounds as water 'not allowed to accumulate above ground'; unclear whether a recirculating splash pad with a collection tank counts. 2024 HB 4035 (pool/spa rules) status not confirmed.
- NEC 2023 OUBCC rule says 'commercial electrical construction'; which NEC governs residential electrical was not checked. NEC 2023 effective date 2024-09-14 rests on a secondary (IAEI) plus the rule's 9-14-24 amendment note.
- IRC 2024 adoption read through search summary and the CIB notice; the IRC permanent-rule PDF itself was not opened.
- OAC 310:320 (operations) section text was not read; fogger_potable false and mahc none are based on 310:315 only.

**OR — Oregon**

- Which CCB endorsement a pool, spa or fountain builder needs: ORS 701 lists no pool-specific endorsement in the text read (only first ~100k chars of chapter 701 were read).
- Whether Multnomah County (rather than OHA directly) performs plan review and licensing of public pools in Portland: OHA materials say 'OHA or jurisdiction-having county' but I did not confirm Multnomah's delegation.
- 2026 ORSC Table R301.2 frost depth for Multnomah (12 in) is taken from the 2023 table; the 2026 table was not read.
- OAR 333-060-1000 was read from the OHA compiled PDF (MAHC text plus Oregon amendments), not the Secretary of State's attachment; Table 4.7.1.10 shown in that PDF may differ from a later amendment.
- Hardness 9 ppm is the midpoint of Bull Run's 7-11 ppm; the Columbia South Shore wellfield supplement is about 80 ppm and can raise local hardness in summer.
- ISPSC: the state ORSC references ISPSC Section 305 for residential barriers and full ISPSC only where a city opts in; I recorded adopted=false/basis=local. Public-pool design follows MAHC/OAR 333-060, not the ISPSC.

**PA — Pennsylvania**

- Whether Philadelphia's own health department (rather than the state Department of Health) permits and inspects public pools and spray grounds in the city; neither phila.gov health pages nor the state pages say. Plan-review basis ('local') rests on the DOH statement that the UCC governs construction.
- Any Department of Health guidance on spray grounds/splash pads (e.g. in the Public Bathing Place Manual) was not found; iwf_regulated=false means Chapter 18 text has no such rule, not that PA ignores them.
- Statewide electrician and pool-contractor licensing: no statewide license found, but no primary statute was read (only Philadelphia L&I pages); PA Home Improvement Consumer Protection Act registration scope for pools not verified.
- Philadelphia's own effective date for the 2021 I-Codes is July 1, 2026 (L&I page, with a transition grace period); the state UCC took effect Jan 1, 2026. Local amendments proposed May 2026 were not read.
- Hardness: 140 mg/L is the mean of three plant averages (101, 151, 169); the figure at a site depends on the serving plant.

**RI — Rhode Island**

- hardness_ppm null: Providence Water's website (provwater.com) blocks automated access (HTTP 403); need the latest Consumer Confidence Report hardness line.
- iwf_regulated false: 216-RICR-50-05-4 never mentions splash pads/spray grounds; confirm with RIDOH whether a recirculating spray pad is licensed as a 'non-traditional pool' and which provisions of the adopted MAHC Sec. 4.0 (not read) then apply.
- NEC 2023 effective date 2025-12-01 is inferred from the regulation history (amendment effective 12/01/2025); the page does not state which edition took effect on which date.
- RI agencies' own sites (ribcc.ri.gov, dbr.ri.gov, health.ri.gov) returned 403, so adoption dates rest on rules.sos.ri.gov regulation history; the rule defines MAHC as the 2016 edition in 4.2 but incorporates the 2018 MAHC in 4.1.3.

**SC — South Carolina**

- Charleston frost depth: no published local value found (state sets none); needs the City of Charleston or Charleston County adopted R301.2(1) table.
- Charleston hardness: 58.4 ppm comes from the utility FAQ, not the dated CCR table (2025 CCR is an image-only PDF).
- ISPSC: statewide it is 'permissive' (local ordinance); whether City of Charleston or Charleston County adopted it is unknown.
- Agency: R.61-51 text still names DHEC; DES administers recreational waters now. Confirm whether R.61-51 has been re-codified under DES or DPH.
- State journeyman electrician licensing not verified.
- NEC and I-codes move to 2023 NEC and 2024 I-codes on 2027-01-01; table reflects codes in force on 2026-11-01.

**SD — South Dakota**

- No South Dakota statewide building code found; conclusion rests on the ARSD article list and Sioux Falls local adoption. Confirm there is no state minimum code (e.g. SDCL title 11) and what the 61:15 fire-safety rule requires.
- fogger_potable null: the 1996 Recommended Standards for Swimming Pool Design and Operation that ARSD 44:02:02:22 incorporates were not read.
- Sioux Falls 2024 IBC, IRC and ISPSC ordinance copies (Ord. 0034/0035) show blank adoption dates; the city's adopted-codes page lists 2024 as in effect and the text says effective July 1, 2026, applied to permits issued after October 15, 2026. IBC effective date and any ISPSC local amendments on spray features were not read.
- Whether Sioux Falls (or Minnehaha County) runs its own health licensing of public pools or splash pads was not found.
- Hardness 250 ppm is the Sioux Falls system average; the Lewis & Clark Regional Water System share averages 168 ppm, and the report gives no single design value.
- Statewide contractor licensing absence not confirmed from a state source.

**TN — Tennessee**

- Statewide adopted editions of IBC, IRC, NEC, ISPSC (and effective dates): tn.gov State Fire Marshal 'State Codes Adoption & History' page and the Secretary of State rule 0780-02 were 403/unreachable, web search budget exhausted. Needs a follow-up read of https://www.tn.gov/commerce/fire/codes-enforcement.html.
- State contractor license class for pools/spas/fountains (Board for Licensing Contractors, classification list and dollar threshold) and state electrician licensing: not read, same access block.
- Does Chapter 1200-23-05 apply to recirculating splash pads / interactive fountains? Rule text has no explicit provision; Metro Health plan-reviews 'water features'. Confirm with Tennessee Dept of Health.
- Nashville frost depth 12 in is read from a column-scrambled table in the Legistar printout of BL2025-898; re-verify against the adopted code PDF.
- Hardness 100.5 mg/L is labeled '2023 data' in the 2025 CCR; the utility did not refresh it.
- Confirm the pool contractor classification (reported as BC-26) on the Board for Licensing Contractors' own outline.

**TX — Texas**

- Dallas hardness: no total hardness figure in the 2025 Water Quality Report or the DWU FAQ list; need DWU's hardness statement (the web search tool was exhausted so I could not look for it).
- Subchapter M (interactive water features) was read from a DSHS PDF headed 'As in effect on May 20, 2010'; I could not confirm against the current Texas SOS rule text (the SOS site is a JavaScript portal) whether it was amended after 2010.
- Dallas's current IBC/IRC edition: Dallas's own code page says 2021 codes effective 2023-05-12 but the page notes it has moved to a new 'City Codes & Ordinances' page I could not locate; a 2024 adoption cannot be excluded. Dallas's 2023 NEC ordinance read is dated 4-22-25 and its effective date was not confirmed.
- Frost depth 6 in for Dallas comes from OCR of a scanned ordinance (Ordinance 23-0471); worth a visual check.
- ISPSC Section 612 (interactive water play features) is adopted for commercial facilities by 25 TAC 265.181 but I could not read the ISPSC text (ICC site returned 403); its turnover, tank and disinfection requirements could change the iwf_like_utah answer.
- Which plumbing/pool contractor license Texas requires: no statewide pool contractor license found, but I did not verify the plumbing board (TSBPE) scope for pool plumbing.

**UT — Utah**

- hardness_ppm: engine value 250 disagrees with the SLC 2026 Water Quality Report (2025 data): treatment plants 136-212 mg/L (Big Cottonwood 136, Parleys 175, City Creek 212), wells and springs 154-571, JVWCD 184.1. 250 is above every SLC surface-water plant; kept at 250 per instruction (conservative for scale). A figure near 175-185 would match the report.
- Code editions disagree between sources: SLC's design-criteria PDF (effective 7/1/2024, uploaded 2026/07) lists 2024 IPC/IMC/ISPSC and 2021 IFC, while its web page lists 2021 IBC/IRC/IPC. State law moved IBC/IPC/IMC/IFGC/IECC/IEBC/ISPSC to 2024 only on 7/1/2026 (H.B. 65). This row uses the state statute. Designs started before 7/1/2026 may still be under 2021 editions (transition rules not checked).
- ISPSC: Utah adopts only the 'residential provisions' of the ISPSC; the brief says adopted=true for statewide adoption. Check whether the engine should treat commercial pools/spas as ISPSC-exempt (they follow R392-302 instead).
- NEC: no state amendment to Article 680 was found in H.B. 313 (searched for 680/pool); not checked against Utah Code 15A-3 chapters, so amendments is left empty.
- MAHC: no Utah reference found in the R392-302 sections read; the full rule text was not searched exhaustively (PDF on epi.health.utah.gov would not download), so 'none' is absence of evidence.
- R392-302-35(8) secondary-disinfection text: R392-302-41(6)(b) points to it; (8) applies to countermeasures in 35(7)(b)-(e) including UV with 99.9% Cryptosporidium inactivation, but 35 is an outbreak-watch section, so whether UV is required for every IWF outside an outbreak watch is not confirmed from the text read.
- The engine's stated NEC value (2023) is confirmed; effective date is 2025-07-01 (H.B. 313, 2025).

**VT — Vermont**

- Is there any state plan review or permit for a freestanding public pool, fountain or splash pad outside lodging? No Health Dept public-pool chapter exists in the Vermont Administrative Code list (Cornell), but the DFS 'public building' definition (20 V.S.A. ch. 173) was not read; plan_review is null.
- The lodging rule copy read is dated 'Effective Date 1/1/2018'; the Health Dept fact sheet is dated September 2026, so a newer rule revision may exist.
- Burlington frost line: 60 in is the state minimum for public buildings; no Burlington city figure found (Burlington code enforcement page not located). Residential one- and two-family work is not under the IBC or IRC.
- Plumber licensing details (Plumbers' Examining Board) not read; only the 2025 Vermont Plumbing Rules link was seen.
- Hardness 63 mg/L is the city CCR's single sample (5/28/2025), not an annual average.

**VA — Virginia**

- Virginia Beach frost depth: city site unreachable; null.
- Virginia Beach hardness: city utility report unreachable; null.
- No statewide rule found for non-lodging public pools or splash pads; confirm with VDH or the Virginia Beach Health Department whether any local requirement applies.
- Whether a 2024-edition USBC has been adopted with a future effective date was not checked (DHCD pages returned 404).
- MAHC: only part of 12VAC5-460 read; part II design sections 40-390 not individually checked.
- Virginia journeyman/master electrician classes under 18VAC50-30 not confirmed in text.

**WA — Washington**

- Effective date of Washington's 2023 NEC adoption not stated on the WAC page; left null.
- Hardness (28 mg/L) comes from SPU's non-regulatory quarterly analysis (Cedar water), not a Consumer Confidence Report; the 2025 CCR gives no hardness. Tolt water is similar to slightly softer.
- Seattle's electrical permit authority and any Seattle code amendments (Seattle Building Code, Seattle Electrical Code) were not read directly; only the state WAC and Seattle Residential Code Table R301.2 were.
- Whether King County applies any fogger/mister conditions administratively: none in WAC 246-260; the potable-supply language in 246-260-081(2)(c) is generic.
- Whether WAC 246-260 will be rewritten around MAHC: no sign of it in the 2025 amendment, but DOH rulemaking pages were not found.

**WV — West Virginia**

- Charleston frost depth: not published anywhere reachable (Administrative Manual not online; municode search found nothing); call Charleston Building Dept. Secondary sources say 24 or 30 in.
- WV building code basis: 87CSR4 applies 'throughout this state' but enforcement exists only where a county or city adopts it; confirm for non-Charleston projects. Rule sunsets 2027-08-01 and an 'In Progress' 87-04 revision (edition unknown) is pending, so IBC/IRC/ISPSC/NEC editions may rise.
- Hardness: used 79 mg/L (top of the 19-79 range) from the 2025 Kanawha Valley CCR; an older utility typical-quality sheet said 38 mg/L (secondary report, not opened).
- Whether the Contractor Licensing Board requires General Engineering for a spray-ground/fountain job (no explicit class); board classification list not reachable on the official site.
- 64CSR16 text read via Cornell LII; SOS lists no amendment since 2007, but the 2019 HB2490 change to 16-1-4 (pool-rule enforcement limits) was only partly checked; subsection (b) cited above.

**WI — Wisconsin**

- Pool contractor licensing statement is secondary (contractorlicenserequirements.com); no primary DSPS statement that no pool contractor license exists was found.
- Milwaukee hardness (138 mg/L median) was read from a search-tool excerpt of the 2025 city PDF because city.milwaukee.gov blocked direct fetch (403).
- Frost depth: used the state UDC 48 in minimum; City of Milwaukee commercial footing depth or local amendment not confirmed (site blocked).
- Whether ATCP 76 requires automated pH control for interactive play attractions was not resolved; iwf_like_utah=false rests mainly on UV/secondary disinfection being optional.
- NEC 2023 effective 2026-09-01 comes from SPS 316 (CR 26-016); scope for 1-2 family dwellings (SPS 320 side) not separately confirmed.

**WY — Wyoming**

- nec effective date: the State Fire Marshal page lists the 2026 NEC but gives no effective date or rule citation; the electrical board rules PDF is an image scan with no text. An earlier secondary summary said 2023 NEC effective 2023-07-01. Cheyenne currently accepts 2023 or 2026 NEC.
- ibc basis: ICC says Wyoming has no single statewide building code; the Council rule (2024 IBC) sets the state minimum for state/public buildings (W.S. 35-9-108), with cities and counties enforcing locally. Marked 'statewide' with that scope caveat.
- irc: no statewide IRC; edition null. Cheyenne uses the 2024 IRC (city edition).
- iwf: Wyoming pool rules do not mention splash pads or interactive water features; whether Consumer Health Services treats them as 'similar installations' was not found. The Wyoming rule PDF on agriculture.wy.gov was generated 05/30/2025; later amendments (if any) were not checked.
- contractor: no statewide general/pool contractor license was found, but no authoritative statement of absence was read (the pool rule oddly refers to a 'state contractors' board'); verify.
- hardness: 60 mg/L comes from the Cheyenne BOPU hardness web page (50-70 mg/L), not the CCR, and the page has no date.
- ispsc/mahc: neither appears in state rules; Cheyenne's ICC list has no ISPSC.
