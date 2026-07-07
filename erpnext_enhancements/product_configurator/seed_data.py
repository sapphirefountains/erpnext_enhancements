"""PDT-0040 STILLWATER E-Stop — seed product definition (stdlib only).

Single source of truth transcribed from the three source documents ("PDT-0040
Stillwater Pricing Calculator.xlsx" Sheet1, "PDT-0040 STILLWATER E-Stop
BOM.xlsx", "Build instructions for PDT-0040 STILLWATER E-Stop.docx"). Imported
by the ``seed_pdt_0040_product`` patch (creates the Configurable Product), by
``dev_checks``, AND by the bench-free unit tests — so the pricing goldens
(1685.008 / 1512.979 from the workbook's own worked examples) validate the
actual shipped seed, not a test copy.

Notes vs the sources:
* Module-level ``parts_cost`` figures are the workbook's pricing estimates and
  deliberately do NOT equal the component-list sums (live vendor prices);
  pricing is driven by these editable module costs, the components drive the
  manufacturing BOM.
* The workbook's config-number formula multiplies the mounting digit by e-stop
  qty — a bug the engine does not replicate (see engine/partnumber.py).
* Mounting choices have no component rows: the source BOM never itemizes them
  (Surface references the Polycase SG-12 box; Pedestal is a ~$750 bollard).
* The docx QC sections are empty headings; the QC steps here are a generic
  starter checklist for the shop to refine on the Configurable Product form.
"""

PDT_0040 = {
	"product_code": "PDT-0040",
	"product_name": "STILLWATER E-Stop",
	"description": (
		"Configurable emergency-stop panel for spas, fountains, pools and other "
		"water features. 24 VDC control with e-stop button + buzzer stations, "
		"optional 15-minute timer buttons, pump motor contactors (sized for up "
		"to 15 hp pumps) and auxiliary interlock relays."
	),
	"labor_rate": 85.0,
	"markup_percent": 30.0,
	"part_number_template": "PDT-0040-{mounting}-{estop_qty}-{timer_qty}-{contactor_qty}-{relay_qty}",
	"item_group": "Configured Products",
	"component_item_group": "E-Stop Components",
	"options": [
		{
			"option_key": "base",
			"option_type": "Base",
			"option_label": "Base Unit — Housing & Label",
			"module_key": "base",
			"parts_cost": 146.50,
			"labor_hours": 1.0,
		},
		{
			"option_key": "mounting",
			"option_type": "Choice",
			"option_label": "E-Stop Mounting",
			"choice_code": "1",
			"choice_label": "Flush",
			"is_default": 1,
			"module_key": "mounting_flush",
			"parts_cost": 30.67,
			"labor_hours": 1.5,
			"qty_multiplier_option": "estop_qty",
			"notes": "Button and buzzer mounted on a stainless outlet plate.",
		},
		{
			"option_key": "mounting",
			"option_type": "Choice",
			"option_label": "E-Stop Mounting",
			"choice_code": "2",
			"choice_label": "Surface",
			"module_key": "mounting_surface",
			"parts_cost": 50.00,
			"flat_labor_cost": 175.0,
			"qty_multiplier_option": "estop_qty",
			"notes": (
				"Button and buzzer mounted on the front of the enclosure box "
				"(Polycase SG-12, https://www.polycase.com/sg-12)."
			),
		},
		{
			"option_key": "mounting",
			"option_type": "Choice",
			"option_label": "E-Stop Mounting",
			"choice_code": "3",
			"choice_label": "Pedestal",
			"module_key": "mounting_pedestal",
			"parts_cost": 750.00,
			"labor_hours": 2.0,
			"qty_multiplier_option": "estop_qty",
			"notes": "Button and buzzer mounted on the top or side of a full bollard.",
		},
		{
			"option_key": "estop_qty",
			"option_type": "Quantity",
			"option_label": "E-Stop Button Qty",
			"min_qty": 1,
			"max_qty": 2,
			"default_qty": 1,
			"module_key": "estop",
			"parts_cost": 74.50,
			"labor_hours": 1.0,
		},
		{
			"option_key": "timer_qty",
			"option_type": "Quantity",
			"option_label": "Timer & Button Qty",
			"min_qty": 0,
			"max_qty": 3,
			"default_qty": 1,
			"module_key": "timer",
			"parts_cost": 57.66,
			"labor_hours": 1.0,
		},
		{
			"option_key": "contactor_qty",
			"option_type": "Quantity",
			"option_label": "Pump Contactor Qty",
			"min_qty": 0,
			"max_qty": 3,
			"default_qty": 2,
			"module_key": "contactor",
			"parts_cost": 151.00,
			"labor_hours": 1.0,
			"warning_condition": "contactor_qty == 3",
			"warning_text": (
				"3 contactors require the enclosure to be turned 90 degrees to "
				"the side for more room."
			),
			"notes": (
				"Priced for pumps up to 15 hp (ABB AF12Z family; AFxx-30-xx-11 "
				"covers 25–50 A). Can be cheaper when pump hp is known."
			),
		},
		{
			"option_key": "relay_qty",
			"option_type": "Quantity",
			"option_label": "Auxiliary Relay Qty",
			"min_qty": 0,
			"max_qty": 3,
			"default_qty": 0,
			"module_key": "relay",
			"parts_cost": 23.00,
			"labor_hours": 0.5,
		},
	],
	"components": [
		# ---- Base / common across all configurations -------------------------
		{
			"module_key": "base",
			"component_name": "Enclosure, medium, hinged (Polycase ZH series)",
			"item_code": "ZH-100806-03",
			"qty_per_module": 1,
			"unit_cost": 66.08,
			"supplier_name": "Polycase",
			"manufacturer": "Polycase",
			"manufacturer_part_no": "ZH-100806-03",
			"notes": "ZQ line is the same without the hinge — saves ~$4. ~$10 shipping.",
		},
		{
			"module_key": "base",
			"component_name": "24 VDC power supply, 3.2 A (Mornsun LM75)",
			"item_code": "LM75-23B24",
			"qty_per_module": 1,
			"unit_cost": 8.97,
			"supplier_name": "TRC",
			"manufacturer": "Mornsun",
			"manufacturer_part_no": "LM75-23B24",
		},
		{
			"module_key": "base",
			"component_name": "Indicator light, 24 VDC (power-on)",
			"item_code": "PC-LIGHT-24VDC",
			"qty_per_module": 1,
			"unit_cost": 5.99,
			"supplier_name": "Amazon",
		},
		{
			"module_key": "base",
			"component_name": "DIN rail, 1 ft",
			"item_code": "PC-DIN-RAIL-1FT",
			"qty_per_module": 1,
			"unit_cost": 2.00,
		},
		{
			"module_key": "base",
			"component_name": "Mini cable tray (wire duct)",
			"item_code": "WDN-1015G-1",
			"qty_per_module": 1,
			"unit_cost": 13.50,
			"supplier_name": "Automation Direct",
			"manufacturer_part_no": "WDN-1015G-1",
		},
		{
			"module_key": "base",
			"component_name": "Hook-up wire, 20 ft",
			"item_code": "PC-WIRE-20FT",
			"qty_per_module": 1,
			"unit_cost": 30.00,
		},
		{
			"module_key": "base",
			"component_name": "Labels",
			"item_code": "PC-LABEL",
			"qty_per_module": 3,
			"unit_cost": 10.00,
		},
		{
			"module_key": "base",
			"component_name": "Strain relief",
			"item_code": "PC-STRAIN-RELIEF",
			"qty_per_module": 3,
			"unit_cost": 1.00,
		},
		{
			"module_key": "base",
			"component_name": "Flat head cable",
			"item_code": "PC-FLAT-CABLE",
			"qty_per_module": 1,
			"unit_cost": 22.00,
			"supplier_name": "Amazon",
		},
		# ---- E-Stop button station -------------------------------------------
		{
			"module_key": "estop",
			"component_name": "Emergency stop pushbutton, 22 mm, N.C., IP65 (ECP series)",
			"item_code": "GCX3131",
			"qty_per_module": 1,
			"unit_cost": 12.00,
			"supplier_name": "Automation Direct",
			"manufacturer": "Automation Direct",
			"manufacturer_part_no": "GCX3131",
		},
		{
			"module_key": "estop",
			"component_name": "Alarm buzzer with LED, 22 mm, 24 VAC/VDC, IP65 (ECX series)",
			"item_code": "ECX2071-24R",
			"qty_per_module": 1,
			"unit_cost": 10.50,
			"supplier_name": "Automation Direct",
			"manufacturer": "Automation Direct",
			"manufacturer_part_no": "ECX2071-24R",
		},
		{
			"module_key": "estop",
			"component_name": "Button contact block, 22 mm, N.O. (ECP series, 2-pack)",
			"item_code": "ECX1040-2",
			"qty_per_module": 1,
			"unit_cost": 8.75,
			"supplier_name": "Automation Direct",
			"manufacturer": "Automation Direct",
			"manufacturer_part_no": "ECX1040-2",
		},
		{
			"module_key": "estop",
			"component_name": "Outlet cover, double gang, stainless steel (with gasket)",
			"item_code": "PC-COVER-2G-SS",
			"qty_per_module": 1,
			"unit_cost": 12.67,
			"supplier_name": "Grainger",
			"notes": "Extra cost is for the gasket.",
		},
		{
			"module_key": "estop",
			"component_name": "Outlet gasket pack (1/2/3 gang)",
			"item_code": "PC-GASKET-PACK",
			"qty_per_module": 1,
			"unit_cost": 11.99,
			"supplier_name": "Amazon",
		},
		{
			"module_key": "estop",
			"component_name": "Terminal block, 3 pole",
			"item_code": "PC-TERM-3P",
			"qty_per_module": 3,
			"unit_cost": 11.00,
			"supplier_name": "RS Online",
		},
		# ---- Timer & button ---------------------------------------------------
		{
			"module_key": "timer",
			"component_name": "Timer relay, 24 VDC, DIN-rail mount (GRT6-M1 AC/DC 24–240 V)",
			"item_code": "GRT6-M1",
			"qty_per_module": 1,
			"unit_cost": 25.49,
			"supplier_name": "Amazon",
			"manufacturer_part_no": "GRT6 M1",
		},
		{
			"module_key": "timer",
			"component_name": "Illuminated pushbutton (timer button)",
			"item_code": "82-6651.1124",
			"qty_per_module": 1,
			"unit_cost": 28.94,
			"supplier_name": "Digikey",
			"manufacturer_part_no": "82-6651.1124",
			"notes": "~$10 shipping.",
		},
		{
			"module_key": "timer",
			"component_name": "Outlet cover, single gang, stainless steel",
			"item_code": "PC-COVER-1G-SS",
			"qty_per_module": 1,
			"unit_cost": 6.48,
			"supplier_name": "Grainger",
		},
		{
			"module_key": "timer",
			"component_name": "Terminal block, 2 pole (ABB 1SNA115271R2200)",
			"item_code": "1SNA115271R2200",
			"qty_per_module": 2,
			"unit_cost": 3.21,
			"supplier_name": "Standard Electric Supply Co",
			"manufacturer": "ABB",
			"manufacturer_part_no": "1SNA115271R2200",
		},
		# ---- Pump motor contactor ---------------------------------------------
		{
			"module_key": "contactor",
			"component_name": "Motor starter / contactor (ABB AF12Z-30-10-21, ≤15 hp)",
			"item_code": "AF12Z-30-10-21",
			"qty_per_module": 1,
			"unit_cost": 118.41,
			"supplier_name": "IFS",
			"manufacturer": "ABB",
			"manufacturer_part_no": "AF12Z-30-10-21",
			"notes": "AF09–AF38 family; AFxx-30-xx-11 covers 25–50 A.",
		},
		{
			"module_key": "contactor",
			"component_name": "Terminal block, 2 pole (ABB 1SNA115271R2200)",
			"item_code": "1SNA115271R2200",
			"qty_per_module": 1,
			"unit_cost": 3.21,
			"supplier_name": "Standard Electric Supply Co",
			"manufacturer": "ABB",
			"manufacturer_part_no": "1SNA115271R2200",
		},
		# ---- Auxiliary interlock relay ----------------------------------------
		{
			"module_key": "relay",
			"component_name": "Mechanical relay, 6 A, 24 VDC coil, with socket (IDEC RV1H)",
			"item_code": "RV1H-G-D24",
			"qty_per_module": 1,
			"unit_cost": 19.34,
			"supplier_name": "Galco",
			"manufacturer_part_no": "RV1H-G-D24",
			"notes": "~$9.99 shipping.",
		},
		{
			"module_key": "relay",
			"component_name": "Terminal block, 2 pole (ABB 1SNA115271R2200)",
			"item_code": "1SNA115271R2200",
			"qty_per_module": 1,
			"unit_cost": 3.21,
			"supplier_name": "Standard Electric Supply Co",
			"manufacturer": "ABB",
			"manufacturer_part_no": "1SNA115271R2200",
		},
	],
	"build_steps": [
		# ---- Machining — Timer Button (docx "Machining timer button") --------
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": (
				"This order includes {timer_qty} timer button(s) — machine one "
				"single-gang cover per timer."
			),
		},
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Use a vice to secure the stainless single-gang outlet cover. Leave "
				"the masking plastic on (add masking if it has been removed)."
			),
		},
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Drill a pilot hole in the center of the outlet; use a hole saw, "
				"step bit, or hole punch to size for the button. Ream the hole to "
				"remove edges."
			),
		},
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": "Apply label.",
		},
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Solder wires to the button: heat-shrink over wire and terminals; "
				"wire lengths 8 in, strip ends but leave the end on to protect the "
				"wire; small red jumper between + and C; 20 AWG minimum. Wire "
				"colors: − = white/blue stripe, + = red, NO = blue."
			),
		},
		{
			"section_title": "Machining — Timer Button",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Attach the button to the cover, attach the gasket to the cover, "
				"and wrap in plastic."
			),
		},
		# ---- Machining — E-Stop Button (docx "Machining E-stop button") ------
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": (
				"This order includes {estop_qty} e-stop button(s) — machine one "
				"double-gang cover per e-stop."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"condition": "mounting == \"1\"",
			"instruction": (
				"Flush mounting: the button and buzzer mount on the stainless "
				"outlet plate."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"condition": "mounting == \"2\"",
			"instruction": (
				"Surface mounting: the button and buzzer mount on the front of the "
				"enclosure box."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"condition": "mounting == \"3\"",
			"instruction": (
				"Pedestal mounting: the button and buzzer mount on the top or side "
				"of the full bollard."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": (
				"Use a vice to secure the stainless double-gang outlet cover. Leave "
				"the masking plastic on (add masking if it has been removed)."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": (
				"Drill pilot holes for both the buzzer (on the left) and the e-stop "
				"(on the right); holes are horizontally centered and vertically "
				"centered between the mount holes. Use a ½ in knockout, hole "
				"saw, or step bit to drill to size. Ream the holes to remove edges."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": "Apply label, then attach the button and buzzer.",
		},
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": (
				"Terminate wires to the button: wire lengths 8 in, strip ends but "
				"leave the end on to protect the wire; ferrules on wires that land "
				"in terminals; red jumper between NC-in and NO-in; red jumper "
				"between NO-out and Light X1; 20 AWG minimum. Wire colors: NC-in = "
				"red, NC-out = blue, Light X2 = white/blue stripe."
			),
		},
		{
			"section_title": "Machining — E-Stop Button",
			"instruction": "Attach the gasket to the cover and wrap in plastic.",
		},
		# ---- Panelbuilding — All Panels ---------------------------------------
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": (
				"Drill holes in the bottom, on the left side: one for the indicator "
				"light and one for the power-cable gland. Gland at 4 in high, 2 in "
				"from the outside edge (1¼ in from the angled side); light "
				"mounted 1 in to the right of the cable-gland hole center."
			),
		},
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": (
				"Cut a 7⅞ in piece of DIN rail and mount it in the center of "
				"the panel with 2 type-S screws with washers in the raised panel "
				"studs."
			),
		},
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": (
				"Cut wire tray: one 6 in piece with lid for the top, one 6 in piece "
				"with lid for the bottom, and 2× 3.5 in pieces for the left "
				"side with an 8 in piece of lid. Mount the wire tray with type-S "
				"screws."
			),
		},
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": "Mount the indicator light and cable gland.",
		},
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": (
				"3D print the power-supply DIN-rail mount, assemble, and attach it "
				"to the power supply. Attach the power supply on the far left side."
			),
		},
		{
			"section_title": "Panelbuilding — All Panels",
			"instruction": (
				"Cut and strip the female end of the power cable, insert it through "
				"the gland and wire it to the power supply."
			),
		},
		# ---- Panelbuilding — Timers -------------------------------------------
		{
			"section_title": "Panelbuilding — Timers",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Set each timer to 15 minutes: top dial to 1h, middle dial to 26% "
				"(or just over), bottom dial to F (single shot)."
			),
		},
		{
			"section_title": "Panelbuilding — Timers",
			"condition": "timer_qty >= 1",
			"instruction": "Apply a label to cover all but the middle dial.",
		},
		{
			"section_title": "Panelbuilding — Timers",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Calibrate: apply power to the timer, simultaneously start a "
				"15-minute stopwatch, and apply power to the S terminal. Wait 15 "
				"minutes; the instant the watch hits 15:00, adjust the timer down "
				"until it turns off."
			),
		},
		{
			"section_title": "Panelbuilding — Timers",
			"condition": "timer_qty >= 1",
			"instruction": (
				"To the right of the power supply, insert the {timer_qty} timer(s) "
				"included in the order."
			),
		},
		# ---- Panelbuilding — Timer Terminals ----------------------------------
		{
			"section_title": "Panelbuilding — Timer Terminals",
			"condition": "timer_qty >= 1",
			"instruction": (
				"To the right of the timers, add 1 terminal block per contactor and "
				"relay in the order ({contactor_qty + relay_qty} total)."
			),
		},
		{
			"section_title": "Panelbuilding — Timer Terminals",
			"condition": "timer_qty == 1",
			"instruction": "Only 1 timer in the order: use double-pole terminals.",
		},
		{
			"section_title": "Panelbuilding — Timer Terminals",
			"condition": "timer_qty == 2",
			"instruction": "2 timers in the order: use triple-pole terminals.",
		},
		{
			"section_title": "Panelbuilding — Timer Terminals",
			"condition": "timer_qty == 3",
			"instruction": "3 timers in the order: use 2 sets of double-pole terminals.",
		},
		{
			"section_title": "Panelbuilding — Timer Terminals",
			"condition": "timer_qty >= 1",
			"instruction": (
				"On the end terminal block, attach the end cover. Jumper-bar each "
				"pole: the top bar is straight 24 VDC power (after the e-stop, "
				"ignoring the timer); the bottom bar is timer output power."
			),
		},
		# ---- Panelbuilding — Motor Contactors ---------------------------------
		{
			"section_title": "Panelbuilding — Motor Contactors",
			"condition": "contactor_qty >= 1",
			"instruction": (
				"To the right of the timers and terminals, insert the "
				"{contactor_qty} contactor(s) included in the order."
			),
		},
		{
			"section_title": "Panelbuilding — Motor Contactors",
			"condition": "contactor_qty == 3",
			"instruction": (
				"3 contactors: turn the enclosure 90 degrees to the side for more "
				"room."
			),
		},
		# ---- Panelbuilding — Relays -------------------------------------------
		{
			"section_title": "Panelbuilding — Relays",
			"condition": "relay_qty >= 1",
			"instruction": (
				"To the right of the contactors, insert the {relay_qty} relay(s) "
				"included in the order."
			),
		},
		{
			"section_title": "Panelbuilding — Relays",
			"condition": "relay_qty >= 1",
			"instruction": (
				"Wire relays: A2 to 24 VDC neutral, A1 to the specified 6 or 8 "
				"terminal. Relay output goes to the 2-pole terminal bottom tier; "
				"relay input goes to the 2-pole terminal top tier."
			),
		},
		# ---- Panelbuilding — Sensor Terminals ---------------------------------
		{
			"section_title": "Panelbuilding — Sensor Terminals",
			"instruction": (
				"To the right of the relays, insert terminal blocks for sensors: 1 "
				"triple-tier per e-stop and timer ({estop_qty + timer_qty} total) "
				"and 1 double-tier for each relay ({relay_qty})."
			),
		},
		{
			"section_title": "Panelbuilding — Sensor Terminals",
			"instruction": "On the end terminal block, attach the end cover.",
		},
		# ---- Panelbuilding — Wiring -------------------------------------------
		{
			"section_title": "Panelbuilding — Wiring",
			"instruction": "Follow the wiring diagram to connect components together.",
		},
		{
			"section_title": "Panelbuilding — Wiring",
			"instruction": (
				"Use ferrules on any screw terminal. Use double ferrules to split "
				"outputs when a terminal cannot handle 2 wires."
			),
		},
		{
			"section_title": "Panelbuilding — Wiring",
			"instruction": "Attach wire labels as dictated by the wiring instructions.",
		},
		{
			"section_title": "Panelbuilding — Wiring",
			"instruction": (
				"Use blue wire for 24 VDC +; use white/blue-stripe wire for 24 VDC "
				"−."
			),
		},
		# ---- Panelbuilding — Extra Finishings ----------------------------------
		{
			"section_title": "Panelbuilding — Extra Finishings",
			"instruction": "Apply the panel label.",
		},
		{
			"section_title": "Panelbuilding — Extra Finishings",
			"instruction": (
				"Apply the internal terminal label, 9 pt bold font, 2 lines: "
				"\"Timer terminals\" / \"E-Stop Terminals\"."
			),
		},
		{
			"section_title": "Panelbuilding — Extra Finishings",
			"instruction": (
				"Insert extra cable glands into the panel — 1 for every e-stop and "
				"timer ({estop_qty + timer_qty} total)."
			),
		},
		{
			"section_title": "Panelbuilding — Extra Finishings",
			"instruction": "Insert the drawing package.",
		},
		# ---- QC (docx headings are empty — generic starter checklist) ---------
		{
			"section_title": "QC — Timer",
			"step_type": "QC",
			"condition": "timer_qty >= 1",
			"instruction": (
				"Each timer runs exactly 15 minutes, verified against a stopwatch."
			),
		},
		{
			"section_title": "QC — Timer",
			"step_type": "QC",
			"condition": "timer_qty >= 1",
			"instruction": "Timer label applied — only the middle dial exposed.",
		},
		{
			"section_title": "QC — E-Stop",
			"step_type": "QC",
			"instruction": "E-stop button latches when pressed and releases correctly.",
		},
		{
			"section_title": "QC — E-Stop",
			"step_type": "QC",
			"instruction": "Buzzer sounds while the e-stop is latched.",
		},
		{
			"section_title": "QC — E-Stop",
			"step_type": "QC",
			"instruction": "Jumpers and ferrules verified against the wiring tables.",
		},
		{
			"section_title": "QC — Panel",
			"step_type": "QC",
			"instruction": "Indicator light illuminates when power is applied.",
		},
		{
			"section_title": "QC — Panel",
			"step_type": "QC",
			"instruction": "All terminals torqued and labeled.",
		},
		{
			"section_title": "QC — Panel",
			"step_type": "QC",
			"instruction": "Panel label applied; drawing package included.",
		},
		{
			"section_title": "QC — Panel",
			"step_type": "QC",
			"instruction": "Enclosure sealed and cable glands tightened.",
		},
	],
}
