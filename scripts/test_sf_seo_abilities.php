<?php
/**
 * Tests for docs/website-seo/sf-seo-abilities.php, the read-only SEO abilities
 * mu-plugin for the marketing site.
 *
 * The plugin runs on WP Engine, not here, so this is the only place its rules
 * are checked before somebody uploads it. Run with: php scripts/test_sf_seo_abilities.php
 *
 * What is guarded, each because getting it wrong is silent or takes the site down:
 *  - the file loads twice without a fatal (a second copy in mu-plugins/ would
 *    otherwise break every request, and recovery mode cannot pause it), and
 *    starts with `<?php` and no byte-order mark;
 *  - the flag rules at their boundaries (a threshold off by one reports a
 *    clean site, or flags every page);
 *  - every ability's contract: edit_posts, listed on the MCP server, annotated
 *    read-only, and a `type` on every schema node (core's validator needs one);
 *  - each ability's callback, run against a stubbed WordPress and Slim SEO
 *    (scripts/fixtures/sf_seo_fake_wordpress.php), with its result checked
 *    against its own output schema. Core validates the output after every
 *    call, and a mismatch fails the call, so a schema typo would make the
 *    ability fail on every request. There is no WordPress in CI; this is the
 *    nearest check to one. Denied callers and password-protected pages
 *    included;
 *  - the read-only promise, on tokens so a comment cannot fool it: no write
 *    function but the one read-cache transient (called, or named in a string
 *    as a callback), no ->query/insert/update/delete/replace on any object, no
 *    SQL read but a prepared SELECT, no dynamic calls, and no rendering call of
 *    the plugin's own (do_blocks, do_shortcode, hooks, Bricks render_data).
 *    What Slim SEO runs inside the calls the plugin makes to it is out of
 *    reach here; the README says what that is.
 */

define( 'ABSPATH', __DIR__ . '/' );

require __DIR__ . '/fixtures/sf_seo_fake_wordpress.php';

$plugin = dirname( __DIR__ ) . '/docs/website-seo/sf-seo-abilities.php';
require $plugin;
$hooks_after_first_load = $GLOBALS['sf_test_hooks'];

// A second copy of the file, as "sf-seo-abilities (1).php" would be. Without the
// guard this line is a fatal "Cannot redeclare", and the test stops here.
require $plugin;

$failures = 0;
$passes   = 0;

function check( $condition, $message ) {
	global $failures, $passes;
	if ( $condition ) {
		++$passes;
		return;
	}
	++$failures;
	fwrite( STDERR, "FAIL: $message\n" );
}

function check_same( $expected, $actual, $message ) {
	check( $expected === $actual, $message . "\n  expected: " . var_export( $expected, true ) . "\n  actual:   " . var_export( $actual, true ) );
}

/** A record shaped like sf_seo_record()'s, with only what the rules read. */
function record( array $over = array() ) {
	$base = array(
		'id'          => 1,
		'type'        => 'page',
		'title'       => array(
			'stored'    => 'A perfectly reasonable page title for us',
			'effective' => 'A perfectly reasonable page title for us',
		),
		'description' => array(
			'stored'    => str_repeat( 'd', 100 ),
			'effective' => str_repeat( 'd', 100 ),
		),
		'noindex'     => false,
		'canonical'   => array( 'points_elsewhere' => false ),
	);
	return array_replace_recursive( $base, $over );
}

function flags_for( array $over, $dup_title = false, $dup_desc = false ) {
	return SF_SEO_Rules::flags( record( $over ), $dup_title, $dup_desc );
}

/* --- Loading ---------------------------------------------------------------- */

check_same( $hooks_after_first_load, $GLOBALS['sf_test_hooks'], 'a second copy of the file loads without a fatal and hooks nothing more' );
check_same( "<?php\n", substr( file_get_contents( $plugin ), 0, 6 ), 'the file starts with <?php: no byte-order mark, no blank line (either is output before headers)' );

/* --- Registration contract ------------------------------------------------ */

check_same( array( 'sf_seo_register_category' ), $GLOBALS['sf_test_hooks']['wp_abilities_api_categories_init'] ?? null, 'category registers on wp_abilities_api_categories_init' );
check_same( array( 'sf_seo_register_abilities' ), $GLOBALS['sf_test_hooks']['wp_abilities_api_init'] ?? null, 'abilities register on wp_abilities_api_init' );
check_same( array( 'wp_abilities_api_categories_init', 'wp_abilities_api_init' ), array_keys( $GLOBALS['sf_test_hooks'] ), 'nothing else is hooked at load' );

sf_seo_register_category();
sf_seo_register_abilities();

check( isset( $GLOBALS['sf_test_categories']['sapphire-seo'] ), 'the sapphire-seo category is registered' );
check( preg_match( '/^[a-z0-9]+(?:-[a-z0-9]+)*$/', 'sapphire-seo' ) === 1, 'category slug satisfies core\'s pattern' );
foreach ( array( 'label', 'description' ) as $key ) {
	check( ! empty( $GLOBALS['sf_test_categories']['sapphire-seo'][ $key ] ), "category has a $key (core throws without one)" );
}

check_same(
	array( 'sapphire-seo/audit-content', 'sapphire-seo/page-detail', 'sapphire-seo/site-summary' ),
	array_keys( $GLOBALS['sf_test_abilities'] ),
	'exactly the three abilities are registered'
);

/**
 * Every schema node needs a `type`: core's rest_validate_value_from_schema()
 * flags one without it, and validates nothing below it.
 */
function untyped_nodes( $schema, $path ) {
	$bad = array();
	if ( ! is_array( $schema ) ) {
		return array( $path );
	}
	if ( ! isset( $schema['type'] ) ) {
		$bad[] = $path;
	}
	foreach ( $schema['properties'] ?? array() as $key => $child ) {
		$bad = array_merge( $bad, untyped_nodes( $child, "$path.$key" ) );
	}
	if ( isset( $schema['items'] ) ) {
		$bad = array_merge( $bad, untyped_nodes( $schema['items'], "$path []" ) );
	}
	if ( isset( $schema['additionalProperties'] ) && is_array( $schema['additionalProperties'] ) ) {
		$bad = array_merge( $bad, untyped_nodes( $schema['additionalProperties'], "$path {*}" ) );
	}
	return $bad;
}

foreach ( $GLOBALS['sf_test_abilities'] as $name => $args ) {
	check( preg_match( '/^[a-z0-9-]+\/[a-z0-9-]+$/', $name ) === 1, "$name satisfies core's name pattern" );
	check( 0 === strpos( $name, 'sapphire-seo/' ), "$name is in our namespace" );
	check_same( 'sapphire-seo', $args['category'] ?? null, "$name is in the sapphire-seo category" );
	check( ! empty( $args['label'] ) && ! empty( $args['description'] ), "$name has a label and a description" );

	check_same( 'sf_seo_can_audit', $args['permission_callback'] ?? null, "$name uses sf_seo_can_audit" );
	check( is_string( $args['execute_callback'] ?? null ) && function_exists( $args['execute_callback'] ), "$name has an execute callback that exists" );

	$meta = $args['meta'] ?? array();
	check_same( true, $meta['mcp']['public'] ?? null, "$name sets meta.mcp.public = true (the MCP Adapter lists nothing else)" );
	check_same( true, $meta['show_in_rest'] ?? null, "$name is listed in REST" );
	check_same( true, $meta['annotations']['readonly'] ?? null, "$name is annotated readonly" );
	check_same( false, $meta['annotations']['destructive'] ?? null, "$name is annotated non-destructive" );

	foreach ( array( 'input_schema', 'output_schema' ) as $which ) {
		check( is_array( $args[ $which ] ?? null ), "$name has an $which" );
		check_same( array(), untyped_nodes( $args[ $which ] ?? null, "$name.$which" ), "every node of $name.$which has a type" );
	}
}

$GLOBALS['sf_fake']['caps'] = array();
check_same( true, sf_seo_can_audit( array() ), 'a user with edit_posts may audit' );
check_same( array( 'edit_posts' ), $GLOBALS['sf_fake']['caps'], 'the permission check asks for edit_posts, nothing weaker' );
$GLOBALS['sf_fake']['deny'] = array( 'edit_posts' );
check_same( false, sf_seo_can_audit( array() ), 'a user without edit_posts (a Subscriber) may not' );
$GLOBALS['sf_fake']['deny'] = array();

$audit_input = $GLOBALS['sf_test_abilities']['sapphire-seo/audit-content']['input_schema'];
check_same( 25, $audit_input['properties']['per_page']['maximum'] ?? null, 'the schema caps per_page at 25 (100 rows overran Claude Code\'s 25k-token result cap)' );
check_same( SF_SEO_Rules::FLAGS, $audit_input['properties']['flag']['enum'] ?? null, 'the flag filter accepts exactly the documented flags' );

/* --- Read-only: no write call anywhere in the plugin ------------------------ */

// Comments are dropped first: the file's own docblocks name the calls it must not make.
$significant = array();
foreach ( token_get_all( file_get_contents( $plugin ) ) as $token ) {
	if ( is_array( $token ) && in_array( $token[0], array( T_WHITESPACE, T_COMMENT, T_DOC_COMMENT ), true ) ) {
		continue;
	}
	$significant[] = $token;
}
$code = implode(
	' ',
	array_map(
		function ( $token ) {
			return is_array( $token ) ? $token[1] : $token;
		},
		$significant
	)
);

// PHP 8 reads `\update_post_meta` and `Foo\bar` as one name token, not T_STRING,
// so a leading backslash would otherwise walk a write call straight past this.
$name_tokens = array( T_STRING );
foreach ( array( 'T_NAME_FULLY_QUALIFIED', 'T_NAME_QUALIFIED', 'T_NAME_RELATIVE' ) as $constant ) {
	if ( defined( $constant ) ) {
		$name_tokens[] = constant( $constant );
	}
}
$arrow = array( T_OBJECT_OPERATOR, T_DOUBLE_COLON );
if ( defined( 'T_NULLSAFE_OBJECT_OPERATOR' ) ) {
	$arrow[] = T_NULLSAFE_OBJECT_OPERATOR;
}
$type_of = function ( $token ) {
	return is_array( $token ) ? $token[0] : $token;
};

$called   = array(); // function name => token index
$methods  = array(); // [ method name, token index ], on any object
$dynamic  = array();
$literals = array(); // every single-quoted or plain string literal, unquoted, lower case
foreach ( $significant as $i => $token ) {
	$next = $significant[ $i + 1 ] ?? null;
	$prev = $significant[ $i - 1 ] ?? null;

	if ( T_CONSTANT_ENCAPSED_STRING === $type_of( $token ) ) {
		$literals[] = strtolower( ltrim( substr( $token[1], 1, -1 ), '\\' ) );
	}
	// $fn( ... ) or $object->$method( ... ): a call nobody can read off the page.
	if ( T_VARIABLE === $type_of( $token ) && '(' === $next ) {
		$dynamic[] = $token[1];
	}
	if ( ! in_array( $type_of( $token ), $name_tokens, true ) || '(' !== $next ) {
		continue;
	}
	$name = strtolower( ltrim( $token[1], '\\' ) );
	if ( in_array( $type_of( $prev ), $arrow, true ) ) {
		// Whatever the object: $wpdb, $GLOBALS['wpdb'], or a copy of it in $db.
		$methods[] = array( $name, $i );
	} elseif ( ! in_array( $type_of( $prev ), array( T_FUNCTION, T_NEW ), true ) ) {
		$called[ $i ] = substr( $name, (int) strrpos( '\\' . $name, '\\' ) );
	}
}
$method_names = array_column( $methods, 0 );

$forbidden = array(
	// Writes.
	'update_post_meta', 'add_post_meta', 'delete_post_meta',
	'update_metadata', 'add_metadata', 'delete_metadata',
	'update_option', 'add_option', 'delete_option',
	'update_site_option', 'add_site_option', 'delete_site_option',
	'set_site_transient', 'delete_transient', 'delete_site_transient',
	'update_user_meta', 'add_user_meta', 'update_user_option', 'update_term_meta', 'add_term_meta',
	'wp_update_post', 'wp_insert_post', 'wp_delete_post', 'wp_trash_post', 'wp_publish_post',
	'wp_insert_attachment', 'wp_update_attachment_metadata',
	'wp_set_object_terms', 'wp_set_post_terms', 'wp_insert_term', 'wp_update_term',
	'wp_insert_user', 'wp_update_user', 'wp_remote_post', 'wp_safe_remote_post', 'wp_remote_request',
	'wp_cache_set', 'wp_cache_add', 'wp_cache_replace', 'wp_cache_delete',
	'wp_schedule_event', 'wp_schedule_single_event', 'wp_mail', 'mail', 'setcookie',
	'file_put_contents', 'fopen', 'fwrite', 'unlink', 'rename',
	'exec', 'shell_exec', 'system', 'passthru', 'proc_open', 'popen',
	// Calls whose target is decided at run time.
	'call_user_func', 'call_user_func_array', 'forward_static_call', 'forward_static_call_array',
	// Rendering that runs other code: dynamic blocks, shortcodes, hooks.
	'do_blocks', 'render_block', 'do_shortcode', 'apply_filters', 'do_action',
);
check_same( array(), array_values( array_unique( array_intersect( $called, $forbidden ) ) ), 'the plugin calls no write, dynamic-dispatch or rendering function' );
check_same( array(), $dynamic, 'the plugin makes no variable function or method calls' );

// A function name in a string is a call waiting to happen: array_map( 'update_option', … ),
// add_action( 'init', 'wp_update_post' ). The cache write is one call site, never a callback.
check_same( array(), array_values( array_unique( array_intersect( $literals, array_merge( $forbidden, array( 'set_transient' ) ) ) ) ), 'no forbidden function is named in a string (a callback is a call)' );

// The database's write methods, on any object. Checking only $wpdb by name would let
// $GLOBALS['wpdb']->query() or `$db = $wpdb; $db->query()` through.
check_same( array(), array_values( array_unique( array_intersect( $method_names, array( 'query', 'insert', 'update', 'delete', 'replace', 'render_data' ) ) ) ), 'no ->query/insert/update/delete/replace on any object, and no Bricks render_data' );

$transient_calls = array_keys( $called, 'set_transient', true );
check_same( 1, count( $transient_calls ), 'the read cache is the one write, made in one place' );
check_same( 'SF_SEO_CACHE_KEY', $significant[ $transient_calls[0] + 2 ][1] ?? null, 'and it writes only the cache key' );

// Every SQL read is a prepared SELECT, on any object: get_row/get_var/get_results/get_col
// run whatever they are given, so each must take a ->prepare() call, and every ->prepare()
// must start its SQL with SELECT.
$sql_problems = array();
$prepared     = 0;
foreach ( $methods as list( $method, $i ) ) {
	if ( 'prepare' === $method ) {
		$first = $significant[ $i + 2 ] ?? null;
		if ( '"' === $first ) {
			$first = $significant[ $i + 3 ] ?? null;
		}
		$sql = is_array( $first ) ? ltrim( $first[1], "'\"" ) : '';
		if ( ! preg_match( '/^\s*SELECT\b/i', $sql ) ) {
			$sql_problems[] = '->prepare() SQL does not start with SELECT: ' . substr( $sql, 0, 40 );
		}
		++$prepared;
	} elseif ( in_array( $method, array( 'get_row', 'get_var', 'get_results', 'get_col' ), true ) ) {
		$arg = array_slice( $significant, $i + 2, 3 );
		if ( ! ( T_VARIABLE === $type_of( $arg[0] ?? null ) && in_array( $type_of( $arg[1] ?? null ), $arrow, true ) && 'prepare' === strtolower( $arg[2][1] ?? '' ) ) ) {
			$sql_problems[] = "->$method() is not given a ->prepare() query";
		}
	}
}
check_same( array(), $sql_problems, 'every SQL read is a prepared SELECT' );
check( $prepared >= 2, 'the SQL check saw the fingerprint queries (it is not checking nothing)' );
check( false === stripos( $code, '__return_true' ), 'no permission is __return_true' );
check(
	array() === array_filter(
		$significant,
		function ( $token ) {
			return is_array( $token ) && T_EVAL === $token[0];
		}
	),
	'no eval'
);

/* --- Text measurement ------------------------------------------------------- */

check_same( 5, SF_SEO_Rules::length( 'Agua!' ), 'length counts characters' );
check_same( 7, SF_SEO_Rules::length( 'Diseño—' ), 'length counts multibyte characters once' );
check_same( 5, SF_SEO_Rules::length( 'A &amp; B' ), 'entities count as the character they show' );
check_same( 'A B', SF_SEO_Rules::clean_text( "  A\xC2\xA0\n\t B " ), 'non-breaking spaces and runs of whitespace collapse' );
check_same( 'Hello world', SF_SEO_Rules::clean_text( '<b>Hello</b> world' ), 'tags are dropped' );
check( '' !== SF_SEO_Rules::utf8( "bad \xC3\x28 byte" ) && 1 === preg_match( '//u', SF_SEO_Rules::utf8( "bad \xC3\x28 byte" ) ), 'invalid UTF-8 is repaired, not lost (it would break the JSON response)' );

/* --- Title rules ------------------------------------------------------------- */

$t = function ( $length ) {
	return array( 'title' => array( 'stored' => 'x', 'effective' => str_repeat( 't', $length ) ) );
};
check_same( array( 'title_too_short' ), flags_for( $t( 29 ) ), 'a 29-character title is too short' );
check_same( array(), flags_for( $t( 30 ) ), 'a 30-character title is fine' );
check_same( array(), flags_for( $t( 60 ) ), 'a 60-character title is fine (Slim SEO warns only above 60)' );
check_same( array( 'title_too_long' ), flags_for( $t( 61 ) ), 'a 61-character title is too long' );
check_same( 30, SF_SEO_Rules::TITLE_MIN, 'title minimum is 30' );
check_same( 60, SF_SEO_Rules::TITLE_MAX, 'title maximum is 60, as in Slim SEO\'s editor' );

check_same(
	array( 'title_missing' ),
	flags_for( array( 'title' => array( 'stored' => '', 'effective' => 'Fountain Design and Build - Sapphire Fountains' ) ) ),
	'no stored title is "missing" even though a template title is output'
);
check_same(
	array( 'title_missing', 'title_too_short' ),
	flags_for( array( 'title' => array( 'stored' => '   ', 'effective' => 'Home - Sapphire' ) ) ),
	'a whitespace-only stored title is missing, and the short output is measured'
);
check_same(
	array( 'title_too_long' ),
	flags_for( array( 'title' => array( 'stored' => str_repeat( 's', 70 ), 'effective' => null ) ) ),
	'with no renderer, the stored title is measured'
);

/* --- Description rules ------------------------------------------------------ */

$d = function ( $length ) {
	return array( 'description' => array( 'stored' => 'x', 'effective' => str_repeat( 'd', $length ) ) );
};
check_same( array( 'description_too_short' ), flags_for( $d( 49 ) ), 'a 49-character description is too short' );
check_same( array(), flags_for( $d( 50 ) ), 'a 50-character description is fine' );
check_same( array(), flags_for( $d( 160 ) ), 'a 160-character description is fine' );
check_same( array( 'description_too_long' ), flags_for( $d( 161 ) ), 'a 161-character description is too long' );

check_same(
	array( 'description_missing', 'description_not_output' ),
	flags_for( array( 'description' => array( 'stored' => '', 'effective' => '' ) ) ),
	'no stored description and nothing output: both flags'
);
check_same(
	array( 'description_missing' ),
	flags_for( array( 'description' => array( 'stored' => '', 'effective' => str_repeat( 'a', 120 ) ) ) ),
	'an automatic description counts as missing, not as not-output'
);
check_same(
	array( 'description_missing' ),
	flags_for( array( 'description' => array( 'stored' => '', 'effective' => null ) ) ),
	'unknown output is never reported as not-output'
);

/* --- Noindex, canonical, order ------------------------------------------- */

check_same( array( 'noindex' ), flags_for( array( 'noindex' => true ) ), 'a noindexed published page is flagged' );

// A password-protected page is judged on what a visitor gets, so its flags say nothing
// about the description stored behind the password: present, absent, short or long.
foreach ( array( '', 'Short.', str_repeat( 'd', 100 ), str_repeat( 'd', 200 ) ) as $hidden ) {
	check_same(
		array( 'description_not_output' ),
		flags_for( array( 'protected' => true, 'description' => array( 'stored' => $hidden, 'effective' => '' ) ) ),
		'password page: the same flags whatever is stored (' . strlen( $hidden ) . ' characters)'
	);
}
check_same( array( 'canonical_elsewhere' ), flags_for( array( 'canonical' => array( 'points_elsewhere' => true ) ) ), 'a canonical elsewhere is flagged' );

$all = flags_for(
	array(
		'title'       => array( 'stored' => '', 'effective' => str_repeat( 't', 70 ) ),
		'description' => array( 'stored' => '', 'effective' => '' ),
		'noindex'     => true,
		'canonical'   => array( 'points_elsewhere' => true ),
	),
	true,
	true
);
check_same( array_values( array_intersect( SF_SEO_Rules::FLAGS, $all ) ), $all, 'flags come back in the documented order' );

$canonical_cases = array(
	array( '', 'https://www.example.com/a/', false, 'a blank canonical means the page itself' ),
	array( 'https://www.example.com/a', 'https://www.example.com/a/', false, 'a trailing slash is the same page' ),
	array( 'http://WWW.example.com/a/', 'https://www.example.com/a/', false, 'scheme and host case are the same page' ),
	array( '/b/', 'https://www.example.com/a/', false, 'a relative canonical is never output by Slim SEO, so it points nowhere' ),
	array( 'not a url', 'https://www.example.com/a/', false, 'neither is a malformed one' ),
	array( 'https://www.example.com/b/', 'https://www.example.com/a/', true, 'another path is elsewhere' ),
	array( 'https://example.com/a/', 'https://www.example.com/a/', true, 'another host is elsewhere' ),
	array( 'https://www.example.com/a/?ref=x', 'https://www.example.com/a/', true, 'a query string is another URL' ),
);
foreach ( $canonical_cases as $case ) {
	check_same( $case[2], SF_SEO_Rules::canonical_elsewhere( $case[0], $case[1] ), 'canonical: ' . $case[3] );
}
check_same( array( true, false, true ), array( SF_SEO_Rules::canonical_is_output( 'https://x.com/a/' ), SF_SEO_Rules::canonical_is_output( '/a/' ), SF_SEO_Rules::canonical_is_output( 'ftp://x.com/a' ) ), 'a canonical is output exactly when it passes FILTER_VALIDATE_URL, as in Slim SEO (ftp:// passes)' );

check_same( true, SF_SEO_Rules::same_site_host( 'sapphirefountains.com', 'www.sapphirefountains.com' ), 'the apex host is this site' );
check_same( true, SF_SEO_Rules::same_site_host( 'WWW.SapphireFountains.com', 'www.sapphirefountains.com' ), 'host case does not matter' );
check_same( false, SF_SEO_Rules::same_site_host( 'evil-sapphirefountains.com', 'www.sapphirefountains.com' ), 'a look-alike host is not' );
check_same( false, SF_SEO_Rules::same_site_host( '', 'www.sapphirefountains.com' ), 'no host is not' );

/* --- Duplicates ------------------------------------------------------------- */

check_same(
	array( 1 => array( 3 ), 3 => array( 1 ) ),
	SF_SEO_Rules::duplicates( array( 1 => 'Fountain Repair', 2 => 'Fountain Design', 3 => '  fountain   REPAIR ' ) ),
	'duplicates ignore case and spacing'
);
check_same( array(), SF_SEO_Rules::duplicates( array( 1 => '', 2 => '  ', 3 => 'x' ) ), 'empty texts are never duplicates of each other' );

$records = array(
	10 => record( array( 'id' => 10, 'title' => array( 'effective' => 'Same Title For Two Pages Here And There' ) ) ),
	11 => record( array( 'id' => 11, 'title' => array( 'effective' => 'same title for two pages here and there' ) ) ),
	12 => record( array( 'id' => 12, 'noindex' => true, 'title' => array( 'effective' => 'Same Title For Two Pages Here And There' ) ) ),
	13 => record( array( 'id' => 13, 'title' => array( 'effective' => 'A Different Title That Is Long Enough' ), 'description' => array( 'effective' => null, 'stored' => str_repeat( 'd', 100 ) ) ) ),
);
$flagged = SF_SEO_Rules::apply_flags( $records );
check_same( array( 11 ), $flagged[10]['duplicate_title_of'], 'duplicates name the other item' );
check( in_array( 'duplicate_title', $flagged[10]['flags'], true ), 'a duplicated title is flagged' );
check_same( array( 'noindex' ), $flagged[12]['flags'], 'a noindexed page is not counted as a duplicate (it cannot compete in search)' );
check_same( array( 10, 11 ), $flagged[13]['duplicate_description_of'], 'an unknown effective description falls back to the stored one for duplicates' );
check( in_array( 'duplicate_description', $flagged[10]['flags'], true ), 'shared descriptions are flagged' );

$records[13]['protected'] = true;
check_same( array(), SF_SEO_Rules::apply_flags( $records )[13]['duplicate_description_of'], 'a password-protected page\'s description is never compared (it would reveal which public page it matches)' );

/* --- Filtering, paging, summary ------------------------------------------- */

$list = array(
	array( 'id' => 1, 'type' => 'page', 'flags' => array() ),
	array( 'id' => 2, 'type' => 'page', 'flags' => array( 'noindex' ) ),
	array( 'id' => 3, 'type' => 'post', 'flags' => array( 'title_too_long', 'noindex' ) ),
	array( 'id' => 4, 'type' => 'product', 'flags' => array( 'description_missing' ) ),
);
$ids = function ( $rows ) {
	return array_map( function ( $row ) { return $row['id']; }, $rows );
};
check_same( array( 1, 2, 3, 4 ), $ids( SF_SEO_Rules::filter( $list ) ), 'no filter returns everything' );
check_same( array( 1, 2 ), $ids( SF_SEO_Rules::filter( $list, 'page' ) ), 'post type filter' );
check_same( array( 2, 3, 4 ), $ids( SF_SEO_Rules::filter( $list, 'any', true ) ), 'flagged only' );
check_same( array( 2, 3 ), $ids( SF_SEO_Rules::filter( $list, 'any', false, 'noindex' ) ), 'one flag' );
check_same( array( 3 ), $ids( SF_SEO_Rules::filter( $list, 'post', true, 'noindex' ) ), 'filters combine' );

check_same( 25, SF_SEO_Rules::paging( 1, 500, 1000 )['per_page'], 'per_page is capped at 25 whatever is asked' );
check_same( 25, SF_SEO_Rules::paging( 1, 26, 1000 )['per_page'], 'one over the cap is the cap' );
check_same( 25, SF_SEO_Rules::paging( 1, 0, 10 )['per_page'], 'a zero per_page falls back to 25' );
check_same( 1, SF_SEO_Rules::paging( 0, 10, 10 )['page'], 'page is at least 1' );
check_same( 6, SF_SEO_Rules::paging( 1, 20, 101 )['total_pages'], '101 items at 20 a page is 6 pages' );
check_same( 40, SF_SEO_Rules::paging( 3, 20, 101 )['offset'], 'page 3 at 20 starts at 40' );
check_same( 0, SF_SEO_Rules::paging( 1, 20, 0 )['total_pages'], 'no items is no pages' );

$summary = SF_SEO_Rules::summarize( $list );
check_same( 4, $summary['total'], 'summary counts every item' );
check_same( 3, $summary['flagged'], 'summary counts flagged items' );
check_same( 2, $summary['by_flag']['noindex'], 'summary counts per flag' );
check_same( SF_SEO_Rules::FLAGS, array_keys( $summary['by_flag'] ), 'every flag appears in the summary, zero or not' );
check_same( array( 'total' => 2, 'flagged' => 1 ), array_slice( $summary['by_type']['page'], 0, 2 ), 'summary counts per post type' );
check_same( 1, $summary['by_type']['post']['by_flag']['title_too_long'], 'summary counts per flag within a post type' );
check_same( array( 2, 3 ), $summary['examples']['noindex'], 'summary names example items per flag' );

$many = array();
for ( $i = 1; $i <= 9; $i++ ) {
	$many[] = array( 'id' => $i, 'type' => 'page', 'flags' => array( 'noindex' ) );
}
check_same( 5, count( SF_SEO_Rules::summarize( $many )['examples']['noindex'] ), 'at most five examples per flag' );

/* --- Markup parsing (headings, served tags, JSON-LD, builder images) -------- */

$html = <<<'HTML'
<html><head>
<title>Fountain Design &amp; Build - Sapphire Fountains</title>
<meta content="We design, build, service and rent fountains." name="description">
<meta name='robots' content='noindex, follow'>
<link href="https://www.example.com/design/" rel="canonical">
<script type="application/ld+json" id="slim-seo-schema">{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":["Organization","LocalBusiness"]}]}</script>
<script type="application/ld+json">{ not json</script>
<script>var x = "<h1>not a heading</h1>";</script>
</head><body>
<svg><title>icon</title></svg>
<!-- <h2>commented out</h2> -->
<h1 class="hero">Fountain <em>Design</em></h1>
<template><h2>template</h2></template>
<h2>Our &amp; process</h2>
<header>not a heading</header>
<h3
  id="x">Step one</h3>
</body></html>
HTML;

check_same(
	array(
		array( 'level' => 1, 'text' => 'Fountain Design' ),
		array( 'level' => 2, 'text' => 'Our & process' ),
		array( 'level' => 3, 'text' => 'Step one' ),
	),
	SF_SEO_Rules::headings( $html ),
	'headings: in order, cleaned, skipping scripts, templates and comments'
);

$head = SF_SEO_Rules::head_tags( $html );
check_same( 'Fountain Design & Build - Sapphire Fountains', $head['title'], 'served title is the document title, not an SVG title' );
check_same( 'We design, build, service and rent fountains.', $head['description'], 'served description, attributes in either order' );
check_same( 'https://www.example.com/design/', $head['canonical'], 'served canonical' );
check_same( true, $head['noindex'], 'served robots noindex is detected' );
check_same( false, SF_SEO_Rules::head_tags( '<meta name="robots" content="index, follow">' )['noindex'], 'index, follow is not noindex' );

$blocks = SF_SEO_Rules::json_ld_blocks( $html );
check_same( 2, count( $blocks ), 'both JSON-LD blocks are found, the plain script is not' );
check_same( 'slim-seo-schema', $blocks[0]['id'], 'the JSON-LD block id is read' );
check_same( array( 'WebPage', 'Organization', 'LocalBusiness' ), $blocks[0]['types'], 'types are collected from @graph, arrays included' );
check_same( false, $blocks[1]['valid'], 'broken JSON-LD is reported invalid, not skipped' );

$bricks = array(
	array(
		'id'       => 'abcdef',
		'name'     => 'image',
		'settings' => array( 'image' => array( 'id' => 41, 'filename' => 'a.jpg', 'size' => 'large', 'url' => 'https://x/a.jpg' ) ),
	),
	array(
		'id'       => 'ghijkl',
		'name'     => 'image-gallery',
		'settings' => array(
			'items' => array(
				'images' => array(
					array( 'id' => '42', 'url' => 'https://x/b.jpg' ),
					array( 'id' => 41, 'url' => 'https://x/a.jpg' ),
				),
			),
		),
	),
	array( 'id' => 'mnopqr', 'name' => 'heading', 'settings' => array( 'text' => 'Hi', 'link' => array( 'url' => 'https://x' ) ) ),
);
check_same( array( 41, 42 ), SF_SEO_Rules::image_ids_in_builder_data( $bricks ), 'builder image IDs: nested, de-duplicated, element IDs ignored' );
check_same( array(), SF_SEO_Rules::image_ids_in_builder_data( 'not an array' ), 'builder data that is not an array yields nothing' );

/* --- The abilities' callbacks, against the stubbed site -------------------- */

/**
 * The part of core's rest_validate_value_from_schema() these schemas use: type
 * (one or a list), properties, items, additionalProperties, enum, minimum and
 * maximum. Stricter than core in one way that matters for JSON: an `array`
 * must be a list, so that it encodes as [] and not as {}.
 */
function value_is( $value, $type ) {
	switch ( $type ) {
		case 'null':
			return null === $value;
		case 'boolean':
			return is_bool( $value );
		case 'integer':
			return is_int( $value );
		case 'number':
			return is_int( $value ) || is_float( $value );
		case 'string':
			return is_string( $value );
		case 'array':
			return is_array( $value ) && array_values( $value ) === $value;
		case 'object':
			return is_array( $value ) && ( array() === $value || array_values( $value ) !== $value );
	}
	return false;
}

function schema_errors( $value, array $schema, $path = '$' ) {
	$types = (array) ( $schema['type'] ?? array() );
	$type  = null;
	foreach ( $types as $candidate ) {
		if ( value_is( $value, $candidate ) ) {
			$type = $candidate;
			break;
		}
	}
	if ( null === $type ) {
		return array( "$path is " . gettype( $value ) . ' ' . var_export( $value, true ) . ', schema says ' . implode( '|', $types ) );
	}

	$errors = array();
	if ( isset( $schema['enum'] ) && ! in_array( $value, $schema['enum'], true ) ) {
		$errors[] = "$path " . var_export( $value, true ) . ' is not in the enum';
	}
	if ( 'integer' === $type && isset( $schema['minimum'] ) && $value < $schema['minimum'] ) {
		$errors[] = "$path is below its minimum";
	}
	if ( 'integer' === $type && isset( $schema['maximum'] ) && $value > $schema['maximum'] ) {
		$errors[] = "$path is above its maximum";
	}
	if ( 'object' === $type ) {
		foreach ( $value as $key => $child ) {
			if ( isset( $schema['properties'][ $key ] ) ) {
				$errors = array_merge( $errors, schema_errors( $child, $schema['properties'][ $key ], "$path.$key" ) );
			} elseif ( isset( $schema['additionalProperties'] ) && is_array( $schema['additionalProperties'] ) ) {
				$errors = array_merge( $errors, schema_errors( $child, $schema['additionalProperties'], "$path.$key" ) );
			} elseif ( isset( $schema['additionalProperties'] ) && false === $schema['additionalProperties'] ) {
				$errors[] = "$path.$key is not allowed";
			}
		}
	}
	if ( 'array' === $type && isset( $schema['items'] ) ) {
		foreach ( $value as $i => $child ) {
			$errors = array_merge( $errors, schema_errors( $child, $schema['items'], "$path[$i]" ) );
		}
	}
	return $errors;
}

/**
 * Runs an ability's execute callback the way core does, with the input it was
 * given, then checks what came back against the ability's output schema.
 */
function run_ability( $name, $input, $validate_input = true ) {
	$args = $GLOBALS['sf_test_abilities'][ $name ];
	if ( $validate_input ) {
		check_same( array(), schema_errors( $input, $args['input_schema'] ), "$name accepts " . json_encode( $input ) );
	}
	$result = call_user_func( $args['execute_callback'], $input );
	if ( $result instanceof WP_Error ) {
		return $result;
	}
	check_same( array(), schema_errors( $result, $args['output_schema'] ), "$name output matches its output schema (input " . json_encode( $input ) . ')' );
	$json = json_encode( $result );
	check( false !== $json, "$name output encodes as JSON" );
	check( false === strpos( (string) $json, SF_FAKE_AI_KEY ), "$name never returns the AI key Slim SEO stores in its settings" );
	return $result;
}

function by_id( array $items ) {
	$out = array();
	foreach ( $items as $item ) {
		$out[ $item['id'] ] = $item;
	}
	return $out;
}

sf_fake_reset();

// The whole audit.
$audit = run_ability( 'sapphire-seo/audit-content', null );
$items = by_id( $audit['items'] );
check_same( array( 10, 11, 12, 13, 14 ), array_keys( $items ), 'the audit lists the published page, post and product, not the draft' );
check_same( 5, $audit['total'], 'total counts what matched' );
check_same( array(), $audit['notes'], 'no caveats on a healthy site' );

check_same( array( 'title_too_long' ), $items[10]['flags'], 'home: its 62-character title is too long' );
check_same( 'post', $items[10]['title']['source'], 'home: its title is its own' );
check_same( 62, $items[10]['title']['length'], 'home: the title length is measured' );

check_same( array( 'title_missing', 'description_missing', 'duplicate_description' ), $items[11]['flags'], 'design page: template title, automatic description shared with the product' );
check_same( 'post_type_template', $items[11]['title']['source'], 'design page: the title comes from the page template' );
check_same( 'Fountain Design - Sapphire Fountains', $items[11]['title']['effective'], 'design page: the effective title is Slim SEO\'s' );
check_same( '', $items[11]['title']['stored'], 'design page: nothing is stored' );
check_same( 'auto', $items[11]['description']['source'], 'design page: the description is automatic' );
check_same( array( 13 ), $items[11]['duplicate_description_of'], 'design page: names the product it shares a description with' );
check_same( 'https://www.example.com/fountain-design/', $items[11]['canonical']['effective'], 'design page: no canonical set means its own URL' );

check_same( array( 'description_too_short', 'noindex' ), $items[12]['flags'], 'blog post: a short description, and noindexed' );
check_same( 'post', $items[12]['noindex_source'], 'blog post: noindexed by its own setting' );
check_same( true, $items[12]['noindex'], 'blog post: noindex is reported' );
check_same( null, $items[12]['canonical']['effective'], 'blog post: a noindexed page outputs no canonical (Slim SEO removes it)' );

check_same( array( 'title_missing', 'duplicate_description', 'canonical_elsewhere' ), $items[13]['flags'], 'product: no title of its own, shared description, canonical elsewhere' );
check_same( 'slim_seo_default', $items[13]['title']['source'], 'product: no product template, so Slim SEO\'s default title' );
check_same( 'https://www.example.com/shop/three-tier/', $items[13]['canonical']['effective'], 'product: the canonical\'s {{ site.url }} is rendered' );
check_same( '{{ site.url }}/shop/three-tier/', $items[13]['canonical']['stored'], 'product: the stored canonical is shown as stored' );

check_same( array( 'title_missing', 'description_not_output' ), $items[14]['flags'], 'password page: a description is stored, but none is output' );
check_same( 'password_protected', $items[14]['description']['source'], 'password page: says why there is no description' );
check_same( array( true, false ), array( $items[14]['protected'], $items[14]['withheld'] ), 'password page: protected, and shown in full to someone who can edit it' );
check_same( SF_FAKE_PRIVATE_DESCRIPTION, $items[14]['description']['stored'], 'password page: its editor sees the stored description' );

check_same( false, $audit['from_cache'], 'the first audit is built, not cached' );
check_same( 1, $GLOBALS['sf_fake']['set_transient_calls'], 'building the audit writes the read cache once' );

// The read cache.
$again = run_ability( 'sapphire-seo/audit-content', array() );
check_same( true, $again['from_cache'], 'a second call is served from the read cache' );
check_same( $audit['items'], $again['items'], 'the cached audit is the same audit' );
check_same( 1, $GLOBALS['sf_fake']['set_transient_calls'], 'serving from cache writes nothing' );

$GLOBALS['sf_fake']['content_version'] = 2;
check_same( false, run_ability( 'sapphire-seo/audit-content', array() )['from_cache'], 'an edit changes the fingerprint, so the audit is rebuilt rather than served stale' );
check_same( false, run_ability( 'sapphire-seo/audit-content', array( 'refresh' => true ) )['from_cache'], 'refresh rebuilds' );

// Filters and paging.
$ids_of = function ( $result ) {
	return array_map(
		function ( $item ) {
			return $item['id'];
		},
		$result['items']
	);
};
check_same( array( 13 ), $ids_of( run_ability( 'sapphire-seo/audit-content', array( 'post_type' => 'product' ) ) ), 'post_type filter' );
check_same( array( 11, 13 ), $ids_of( run_ability( 'sapphire-seo/audit-content', array( 'flag' => 'duplicate_description' ) ) ), 'one-flag filter' );
check_same( array( 12 ), $ids_of( run_ability( 'sapphire-seo/audit-content', array( 'post_type' => 'post', 'flagged_only' => true ) ) ), 'filters combine' );

$page2 = run_ability( 'sapphire-seo/audit-content', array( 'per_page' => 2, 'page' => 2 ) );
check_same( array( 12, 13 ), $ids_of( $page2 ), 'page 2 of 2-per-page' );
check_same( 3, $page2['total_pages'], 'five items at two a page is three pages' );
check_same( 0, count( run_ability( 'sapphire-seo/audit-content', array( 'page' => 9 ) )['items'] ), 'a page past the end is empty, not an error' );

check_same( 25, sf_seo_audit_content( array( 'per_page' => 500 ) )['per_page'], 'the callback caps per_page at 25 even if the schema were bypassed' );
check( schema_errors( array( 'per_page' => 26 ), $GLOBALS['sf_test_abilities']['sapphire-seo/audit-content']['input_schema'] ), 'and the schema refuses 26' );

$from_get = run_ability( 'sapphire-seo/audit-content', array( 'flagged_only' => 'false', 'per_page' => '2', 'page' => '1' ), false );
check_same( array( false, 2 ), array( $from_get['filters']['flagged_only'], $from_get['per_page'] ), 'string input, as a REST GET delivers it before WordPress 7.1, is read correctly' );

$junk = sf_seo_audit_content( array( 'post_type' => 'attachment', 'flag' => 'drop table' ) );
check_same( array( 'any', null ), array( $junk['filters']['post_type'], $junk['filters']['flag'] ), 'unknown post types and flags are ignored, not passed on' );

// A password-protected page, seen by someone who cannot edit it (a Contributor).
$GLOBALS['sf_fake']['deny'] = array( 'edit_post' );
$redacted                   = by_id( run_ability( 'sapphire-seo/audit-content', array() )['items'] )[14];
check_same( array( true, null ), array( $redacted['withheld'], $redacted['description']['stored'] ), 'password page: its stored description is withheld from someone who cannot edit it' );
$withheld = run_ability( 'sapphire-seo/page-detail', array( 'id' => 14 ) )['item'];
check_same( array( true, true, array(), array() ), array( $withheld['images']['withheld'], $withheld['headings']['withheld'], $withheld['images']['missing_alt'], $withheld['headings']['items'] ), 'password page: its images and headings are withheld too' );
check_same( null, $withheld['description']['stored'], 'password page: page detail withholds the description as well' );
$GLOBALS['sf_fake']['deny'] = array();
check_same( SF_FAKE_PRIVATE_DESCRIPTION, $GLOBALS['sf_fake']['transients'][ SF_SEO_CACHE_KEY ]['items'][14]['description']['stored'] ?? null, 'the cache is shared: withholding happens on the way out, never in the cache' );
check_same( SF_FAKE_PRIVATE_DESCRIPTION, by_id( run_ability( 'sapphire-seo/audit-content', array() )['items'] )[14]['description']['stored'], 'so the next caller who can edit it still sees it' );

// A request carrying the page's wp-postpass_ cookie makes Slim SEO render the real
// description. Whoever rebuilds the cache, it must never be cached as "effective".
$hidden_render                                   = 'The real description from behind the password, as a cookie-carrying request renders it.';
$GLOBALS['sf_fake']['effective_description'][14] = $hidden_render;
$GLOBALS['sf_fake']['deny']                      = array( 'edit_post' );
$rebuilt                                         = run_ability( 'sapphire-seo/audit-content', array( 'refresh' => true ) );
$leak_check                                      = by_id( $rebuilt['items'] )[14];
check_same( array( '', 0 ), array( $leak_check['description']['effective'], $leak_check['description']['length'] ), 'password page: effective description is what visitors get (nothing), even if Slim SEO would render more' );
check( false === strpos( json_encode( $rebuilt ), 'from behind the password' ), 'password page: the rendered text appears nowhere in the response' );

// And a caller who cannot edit it sees exactly the same record whether or not a
// description is stored behind the password.
$with_stored = $leak_check;
unset( $GLOBALS['sf_fake']['meta'][14]['slim_seo'] );
++$GLOBALS['sf_fake']['content_version'];
$without_stored = by_id( run_ability( 'sapphire-seo/audit-content', array() )['items'] )[14];
check_same( $with_stored, $without_stored, 'password page: a Contributor cannot tell whether a description is stored' );
$GLOBALS['sf_fake']['deny'] = array();
sf_fake_reset();

$open = run_ability( 'sapphire-seo/page-detail', array( 'id' => 14 ) )['item'];
check_same( array( array( 'level' => 2, 'text' => 'Pricing tiers' ) ), $open['headings']['items'], 'password page: its editor gets its headings' );
check_same( array( 24 ), array_column( $open['images']['missing_alt'], 'id' ), 'password page: and its images' );

// WooCommerce's own noindexed pages.
$GLOBALS['sf_fake']['wc_pages'] = array( 'cart' => 11 );
$woo                            = by_id( run_ability( 'sapphire-seo/audit-content', array() )['items'] );
check_same( array( true, 'woocommerce' ), array( $woo[11]['noindex'], $woo[11]['noindex_source'] ), 'WooCommerce\'s cart page is reported noindexed, by WooCommerce' );
check( ! in_array( 'duplicate_description', $woo[13]['flags'], true ), 'and no longer counts as a duplicate of the product' );
check_same( 'https://www.example.com/fountain-design/', $woo[11]['canonical']['effective'], 'WooCommerce\'s noindex leaves the canonical in place (only Slim SEO\'s own noindex removes it)' );
$GLOBALS['sf_fake']['wc_pages'] = array();

// One page.
$GLOBALS['sf_fake']['caps'] = array();
$detail                     = run_ability( 'sapphire-seo/page-detail', array( 'id' => 11 ) );
$item                       = $detail['item'];
check( in_array( 'read_post', $GLOBALS['sf_fake']['caps'], true ), 'page detail checks the caller may read that page' );
check_same( $items[11]['flags'], $item['flags'], 'page detail flags agree with the list' );
check_same( 3, $item['images']['checked'], 'images: featured, attached and the one in the Bricks data are checked' );
check_same(
	array(
		array( 20, array( 'featured' ) ),
		array( 22, array( 'bricks' ) ),
	),
	array_map(
		function ( $image ) {
			return array( $image['id'], $image['found_in'] );
		},
		$item['images']['missing_alt']
	),
	'images: the two without alt text, and where each was found'
);
check_same( 'IMG_2034', $item['images']['missing_alt'][0]['file_title'], 'images: the file title is given (Slim SEO falls back to it)' );

check_same( 'slim_seo', $item['headings']['source'], 'headings: a Bricks page is read through Slim SEO\'s filtered content, not rendered here' );
check_same(
	array(
		array( 'level' => 1, 'text' => 'Fountain Design' ),
		array( 'level' => 2, 'text' => 'Our process' ),
	),
	$item['headings']['items'],
	'headings: the ones in the content Slim SEO reads'
);
check_same( 1, $item['headings']['h1_count'], 'headings: h1 count' );

$GLOBALS['sf_fake']['slim_content_throws'] = true;
$fallback                                  = run_ability( 'sapphire-seo/page-detail', array( 'id' => 11 ) )['item']['headings'];
check_same( array( 'post_content', false ), array( $fallback['source'], $fallback['available'] ), 'headings: if Slim SEO\'s reader fails, a Bricks page says so instead of rendering Bricks itself' );
check( false !== strpos( $fallback['note'], 'live_check' ), 'headings: and points at live_check' );
$GLOBALS['sf_fake']['slim_content_throws'] = false;

check_same( true, $item['schema']['slim_seo_builtin']['active'], 'schema: Slim SEO\'s built-in schema is on' );
check( in_array( 'WebPage', $item['schema']['slim_seo_builtin']['types'], true ) && ! in_array( 'Article', $item['schema']['slim_seo_builtin']['types'], true ), 'schema: a page gets WebPage, not Article' );
check_same( array( 'slim-seo-schema/slim-seo-schema.php' ), $item['schema']['schema_addon']['active_plugins'], 'schema: the Schema add-on is detected' );
check_same( null, $item['schema']['schema_addon']['outputs_here'], 'schema: the add-on\'s per-page answer is never guessed' );
check_same( null, $item['live_check'], 'no live check unless asked' );
check_same( 0, $GLOBALS['sf_fake']['http_calls'], 'and no request was made' );

$GLOBALS['sf_fake']['features_off'] = array( 'schema' );
check_same( array(), run_ability( 'sapphire-seo/page-detail', array( 'id' => 11 ) )['item']['schema']['slim_seo_builtin']['types'], 'schema: no built-in types when the feature is off' );
$GLOBALS['sf_fake']['features_off'] = array();

$product = run_ability( 'sapphire-seo/page-detail', array( 'url' => '/product/three-tier-fountain/' ) )['item'];
check_same( 13, $product['id'], 'a root-relative URL finds the product' );
check_same( array( 'slim_seo', array( array( 'level' => 2, 'text' => 'Specifications' ) ) ), array( $product['headings']['source'], $product['headings']['items'] ), 'a page without Bricks data is read from its saved content' );

check_same( 12, run_ability( 'sapphire-seo/page-detail', array( 'url' => 'https://www.example.com/blog/winterizing/' ) )['item']['id'], 'an absolute URL finds the post' );
check_same( 11, run_ability( 'sapphire-seo/page-detail', array( 'url' => 'https://example.com/fountain-design/' ) )['item']['id'], 'the apex host is this site too' );
check_same( 10, run_ability( 'sapphire-seo/page-detail', array( 'url' => 'https://www.example.com/' ) )['item']['id'], 'the home URL finds the static front page' );

$GLOBALS['sf_fake']['deny'] = array( 'read_post' );
$forbidden_page             = run_ability( 'sapphire-seo/page-detail', array( 'id' => 11 ) );
check( $forbidden_page instanceof WP_Error && 'sf_seo_forbidden' === $forbidden_page->get_error_code(), 'page detail refuses a page the caller cannot read' );
$GLOBALS['sf_fake']['deny'] = array();

$errors = array(
	array( array(), 'sf_seo_missing_target' ),
	array( array( 'url' => 'https://evil.example.org/fountain-design/' ), 'sf_seo_foreign_url' ),
	array( array( 'id' => 15 ), 'sf_seo_not_audited' ),
	array( array( 'id' => 999 ), 'sf_seo_not_found' ),
	array( array( 'url' => '/no-such-page/' ), 'sf_seo_not_found' ),
);
foreach ( $errors as $case ) {
	$result = run_ability( 'sapphire-seo/page-detail', $case[0] );
	check( $result instanceof WP_Error && $case[1] === $result->get_error_code(), 'page detail refuses ' . json_encode( $case[0] ) . ' with ' . $case[1] );
}

// Live check.
$served = <<<'HTML'
<html><head><title>Winterizing an outdoor fountain - Sapphire Fountains</title>
<meta name="description" content="Short.">
<meta name="robots" content="noindex, follow">
<link rel="canonical" href="https://www.example.com/blog/winterizing/">
<script type="application/ld+json" id="slim-seo-schema">{"@graph":[{"@type":"WebPage"},{"@type":"Article"}]}</script>
</head><body><h1>Winterizing your fountain</h1><h2>Why winterize</h2></body></html>
HTML;
$GLOBALS['sf_fake']['http'] = array(
	'response' => array( 'code' => 200 ),
	'headers'  => array(),
	'body'     => $served,
);
$live = run_ability( 'sapphire-seo/page-detail', array( 'url' => 'https://www.example.com/blog/winterizing/?utm_source=x', 'live_check' => true ) )['item']['live_check'];
check_same( 'https://www.example.com/blog/winterizing/', $GLOBALS['sf_fake']['http_url'], 'live check fetches the page\'s own permalink, never the URL it was given' );
check_same( true, $live['fetched'], 'live check: fetched' );
check_same( 'Winterizing an outdoor fountain - Sapphire Fountains', $live['head']['title'], 'live check: the served title' );
check_same( true, $live['head']['noindex'], 'live check: the served noindex' );
check_same( array( 'WebPage', 'Article' ), $live['schema_types'], 'live check: the served schema types' );
check_same( 1, $live['h1_count'], 'live check: served headings' );

$GLOBALS['sf_fake']['http'] = array(
	'response' => array( 'code' => 403 ),
	'headers'  => array( 'cf-mitigated' => 'challenge' ),
	'body'     => '<html><title>Just a moment...</title></html>',
);
$blocked = run_ability( 'sapphire-seo/page-detail', array( 'id' => 12, 'live_check' => true ) )['item']['live_check'];
check( false === $blocked['fetched'] && 403 === $blocked['status'] && false !== strpos( (string) $blocked['error'], 'Cloudflare' ), 'live check: a Cloudflare challenge is reported as one' );

$GLOBALS['sf_fake']['http'] = new WP_Error( 'http_request_failed', 'cURL error 28: Operation timed out after 15001 milliseconds' );
$timeout                    = run_ability( 'sapphire-seo/page-detail', array( 'id' => 12, 'live_check' => true ) )['item']['live_check'];
check_same( array( false, null, 'cURL error 28: Operation timed out after 15001 milliseconds' ), array( $timeout['fetched'], $timeout['status'], $timeout['error'] ), 'live check: a failed request is reported with its reason' );

$calls  = $GLOBALS['sf_fake']['http_calls'];
$locked = run_ability( 'sapphire-seo/page-detail', array( 'id' => 14, 'live_check' => true ) )['item']['live_check'];
check( false === $locked['fetched'] && $calls === $GLOBALS['sf_fake']['http_calls'], 'live check: a password-protected page is not fetched' );

// The site summary.
$summary = run_ability( 'sapphire-seo/site-summary', null );
check_same( array( 5, 5 ), array( $summary['total'], $summary['flagged'] ), 'summary: totals' );
check_same(
	array(
		'title_missing'          => 3,
		'title_too_short'        => 0,
		'title_too_long'         => 1,
		'duplicate_title'        => 0,
		'description_missing'    => 1,
		'description_not_output' => 1,
		'description_too_short'  => 1,
		'description_too_long'   => 0,
		'duplicate_description'  => 2,
		'noindex'                => 1,
		'canonical_elsewhere'    => 1,
	),
	$summary['by_flag'],
	'summary: every flag counted'
);
check_same( array( 'page', 'post', 'product' ), array_keys( $summary['by_type'] ), 'summary: per post type' );
check_same( 3, $summary['by_type']['page']['total'], 'summary: three pages' );
check_same( array( 11, 13, 14 ), array_column( $summary['examples']['title_missing'], 'id' ), 'summary: examples name the pages' );
check_same( 'https://www.example.com/product/three-tier-fountain/', $summary['examples']['canonical_elsewhere'][0]['url'], 'summary: examples carry the URL' );
check_same( SF_SEO_Rules::flag_rules(), $summary['flag_rules'], 'summary: the rules travel with the results' );
check_same( false, $summary['site']['search_engines_discouraged'], 'summary: search engines allowed' );
check_same( array_fill_keys( SF_SEO_SLIM_FEATURES, true ), $summary['site']['slim_seo']['features'], 'summary: Slim SEO feature switches' );
check_same( array( 'slim-seo-schema/slim-seo-schema.php' ), $summary['site']['slim_seo']['other_slim_seo_plugins'], 'summary: Slim SEO add-ons' );

$GLOBALS['sf_fake']['options']['blog_public'] = '0';
$GLOBALS['sf_fake']['features_off']           = array( 'meta_title' );
$staging                                      = run_ability( 'sapphire-seo/site-summary', null );
check_same( true, $staging['site']['search_engines_discouraged'], 'summary: "Discourage search engines" is reported' );
check( 2 === count( $staging['notes'] ) && false !== strpos( $staging['notes'][0], 'Discourage search engines' ) && false !== strpos( $staging['notes'][1], 'meta_title' ), 'summary: the notes explain both' );

/* --- Done ------------------------------------------------------------------- */

echo "sf-seo-abilities: $passes passed, $failures failed\n";
exit( $failures ? 1 : 0 );
