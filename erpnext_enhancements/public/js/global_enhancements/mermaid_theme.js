/*
 * Sapphire Fountains Mermaid theme — single source of truth for how every
 * Mermaid.js diagram on the desk is styled: the Process Document form
 * preview, the Process Document visual builder, and the Triton widget's
 * fenced-code diagrams.
 *
 * Branding extracted from sapphirefountains.com (Bricks theme globals):
 * Lato plus the sapphire/teal palette. Exposed as window.sf_mermaid (classic
 * script shipped in erpnext_enhancements.bundle.js, loaded on every desk
 * page before any consumer renders).
 *
 * Diagrams always render on a LIGHT canvas, even under Timeless Night. The
 * seeded Process Documents class their nodes with literal pastel fills, and
 * Mermaid would pair those fills with the theme's (light) text color under a
 * dark theme — unreadable. Treat diagrams like print: literal colors on a
 * light surface (see the dark-theme convention in the repo docs); the
 * surrounding UI chrome is what follows the desk theme.
 */

(function () {
	// sapphirefountains.com Bricks global colors
	const BRAND = {
		sapphire: "#00609C", // primary brand blue
		sky: "#00A0DF", // bright accent blue
		teal: "#62CBC9", // aqua accent
		navy: "#00263E", // dark heading/ink blue
		ink: "#00111C", // darkest background
		pale_blue: "#99BFD7",
		deep_teal: "#005779",
		pine: "#316564",
		orchid: "#B14FC5",
		plum: "#55265F",
		mist: "#F8F8F8", // off-white section background
	};

	const FONT_FAMILY = 'Lato, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';

	// Mermaid "base" theme variables — brand-light canvas in both desk themes.
	const THEME_VARIABLES = {
		fontFamily: FONT_FAMILY,
		// default (unclassed) nodes
		primaryColor: "#E6EFF5", // sapphire tint
		primaryTextColor: BRAND.navy,
		primaryBorderColor: BRAND.sapphire,
		secondaryColor: "#E7F7F6", // teal tint
		secondaryBorderColor: BRAND.teal,
		tertiaryColor: BRAND.mist,
		tertiaryBorderColor: BRAND.pale_blue,
		// edges + labels
		lineColor: BRAND.sapphire,
		textColor: BRAND.navy,
		edgeLabelBackground: "#FFFFFF",
		// subgraphs
		clusterBkg: "#F3F7FA",
		clusterBorder: BRAND.pale_blue,
		titleColor: BRAND.navy,
	};

	// The canonical classDef palette for authoring charts ("SF style pack").
	// Explicit `color:` keeps classed nodes readable no matter the theme.
	const CLASS_PACK = [
		"%% Sapphire Fountains style pack",
		"classDef sapphire fill:#E6EFF5,stroke:#00609C,stroke-width:2px,color:#00263E;",
		"classDef sky fill:#E5F5FC,stroke:#00A0DF,stroke-width:2px,color:#00263E;",
		"classDef teal fill:#E7F7F6,stroke:#62CBC9,stroke-width:2px,color:#00263E;",
		"classDef pine fill:#EAF1F1,stroke:#316564,stroke-width:2px,color:#00263E;",
		"classDef deepteal fill:#E6F0F4,stroke:#005779,stroke-width:2px,color:#00263E;",
		"classDef navy fill:#E9EDF0,stroke:#00263E,stroke-width:2px,color:#00263E;",
		"classDef orchid fill:#F6EBF9,stroke:#B14FC5,stroke-width:2px,color:#00263E;",
		"classDef plum fill:#EFE9F1,stroke:#55265F,stroke-width:2px,color:#00263E,stroke-dasharray: 5 5;",
	].join("\n    ");

	function config() {
		return {
			startOnLoad: false,
			theme: "base",
			themeVariables: Object.assign({}, THEME_VARIABLES),
		};
	}

	function init(mermaid_lib) {
		const lib = mermaid_lib || window.mermaid;
		if (!lib) return;
		lib.initialize(config());
	}

	window.sf_mermaid = {
		BRAND,
		FONT_FAMILY,
		CLASS_PACK,
		config,
		init,
	};
})();
