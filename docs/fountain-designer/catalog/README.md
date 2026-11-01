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
