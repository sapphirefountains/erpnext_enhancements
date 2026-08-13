<?php
/**
 * Plugin Name: Sapphire Fountains — Attribution Capture
 * Description: First-touch UTM/gclid capture into a first-party cookie, copied into Fluent Forms hidden fields, plus the honeypot's concealing CSS.
 * Version:     1.0.0
 * Author:      Sapphire Fountains
 *
 * Install as a must-use plugin: drop this file AND sf-attribution.js into
 * wp-content/mu-plugins/. WordPress auto-loads top-level .php from that
 * directory with no activation step, which is what we want — nobody can
 * deactivate attribution by accident from the plugins screen.
 *
 * The JS is inlined rather than enqueued by URL on purpose. WP Engine serves
 * wp-content with long cache lifetimes and its own edge cache in front, so a
 * versioned URL is one more thing to get wrong on every edit; the script is
 * under 4 KB and inlining it removes both the request and the cache question.
 *
 * @package sapphire-fountains
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * The honeypot field's concealment.
 *
 * `display:none` is the obvious approach and the wrong one — a good bot skips
 * fields it can see are hidden. Pulling it off-screen while leaving it painted
 * and focusable-by-nothing is the convention that still catches naive bots.
 *
 * The field itself must be created in the Fluent Forms builder as a text input
 * named `hp_company_url`. It cannot be injected here: the webhook serialises
 * Fluent Forms' own submission data, so a field this plugin adds to the DOM
 * would never reach ERPNext.
 */
function sf_attribution_honeypot_css() {
	?>
	<style id="sf-attr-hp">
		.ff-el-group:has(input[name="hp_company_url"]),
		input[name="hp_company_url"] {
			position: absolute !important;
			left: -9999px !important;
			width: 1px !important;
			height: 1px !important;
			overflow: hidden !important;
		}
	</style>
	<?php
}

/**
 * Inline the capture script in the footer.
 *
 * Footer rather than head: nothing above the fold depends on it, and the form
 * markup it fills has to exist first. `capture()` still runs on the first
 * pageview either way, because the cookie is written from location.search and
 * not from anything the form does.
 */
function sf_attribution_inline_script() {
	$path = __DIR__ . '/sf-attribution.js';

	if ( ! is_readable( $path ) ) {
		// Fail silently on the front end. A missing script must never take the
		// site down or print a notice to a customer; the symptom is blank
		// attribution, which the Attribution Gaps report in ERPNext surfaces.
		return;
	}

	$js = file_get_contents( $path ); // phpcs:ignore WordPress.WP.AlternativeFunctions

	if ( false === $js || '' === trim( $js ) ) {
		return;
	}

	echo "<script id=\"sf-attr\">\n" . $js . "\n</script>\n"; // phpcs:ignore WordPress.Security.EscapeOutput
}

/**
 * Front end only. There is no campaign attribution to capture in wp-admin, and
 * running there risks colliding with an editor's own form preview.
 */
function sf_attribution_boot() {
	if ( is_admin() ) {
		return;
	}
	add_action( 'wp_head', 'sf_attribution_honeypot_css', 5 );
	add_action( 'wp_footer', 'sf_attribution_inline_script', 99 );
}

add_action( 'init', 'sf_attribution_boot' );
