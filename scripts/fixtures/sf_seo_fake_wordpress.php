<?php
/**
 * Just enough WordPress and Slim SEO for scripts/test_sf_seo_abilities.php to
 * run the SEO abilities' callbacks without a WordPress install. Bricks is
 * present only as its constants: the plugin reads Bricks data, never renders it.
 *
 * Each stub does what the real function does for the inputs the plugin gives
 * it, and no more. The sample site in $GLOBALS['sf_fake'] is five published
 * items chosen so that most flags fire at least once, plus a draft. Tests
 * change it to steer a case (`deny` takes capabilities away from the caller);
 * sf_fake_reset() puts it back.
 */

namespace SlimSEO\MetaTags {

	/** Slim SEO's title renderer: the effective title the sample site "outputs". */
	class Title {
		public function get_rendered_singular_value( int $post_id = 0 ): string {
			return $GLOBALS['sf_fake']['effective_title'][ $post_id ] ?? '';
		}
	}

	class Description {
		public function get_rendered_singular_value( int $post_id = 0 ): string {
			return $GLOBALS['sf_fake']['effective_description'][ $post_id ] ?? '';
		}
	}

	/** Renders the one dynamic variable the sample data uses. */
	class Helper {
		public static function render( string $text, int $post_id = 0, int $term_id = 0, array $data = array() ): string {
			return str_replace( '{{ site.url }}', 'https://www.example.com', $text );
		}
	}

	/**
	 * Slim SEO's content reader: for a Bricks page, its filtered render of the
	 * page's own elements; otherwise the saved post content.
	 */
	class Data {
		public static function get_post_content( int $post_id = 0, string $content = '' ): string {
			if ( ! empty( $GLOBALS['sf_fake']['slim_content_throws'] ) ) {
				throw new \RuntimeException( 'renamed in a Slim SEO update' );
			}
			if ( isset( $GLOBALS['sf_fake']['slim_content'][ $post_id ] ) ) {
				return $GLOBALS['sf_fake']['slim_content'][ $post_id ];
			}
			$post = $GLOBALS['sf_fake']['posts'][ $post_id ] ?? null;
			return $post ? $post->post_content : '';
		}
	}
}

namespace SlimSEO {

	class FakeSettings {
		public function is_feature_active( string $feature ): bool {
			return ! in_array( $feature, $GLOBALS['sf_fake']['features_off'], true );
		}
	}

	class FakeContainer {
		public function get_service( string $id ) {
			return 'settings' === $id ? new FakeSettings() : null;
		}
	}
}

namespace {

	define( 'SLIM_SEO_VER', '4.11.4' );
	define( 'BRICKS_VERSION', '2.1' );
	define( 'BRICKS_DB_PAGE_CONTENT', '_bricks_page_content_2' );
	define( 'MB_IN_BYTES', 1048576 );
	define( 'ARRAY_A', 'ARRAY_A' );

	/** A key-shaped value that must never reach a response. Not a real key. */
	const SF_FAKE_AI_KEY = 'fixture-ai-key-must-not-leak-0000';

	/** The password page's stored description: only someone who can edit it may see it. */
	const SF_FAKE_PRIVATE_DESCRIPTION = 'Members-only pricing for 2027 service contracts, visible only behind the password.';

	class WP_Post {
		public $ID                = 0;
		public $post_type         = 'page';
		public $post_status       = 'publish';
		public $post_title        = '';
		public $post_content      = '';
		public $post_excerpt      = '';
		public $post_password     = '';
		public $post_parent       = 0;
		public $post_modified_gmt = '2026-10-27 12:00:00';

		public function __construct( array $fields ) {
			foreach ( $fields as $key => $value ) {
				$this->$key = $value;
			}
		}
	}

	class WP_Error {
		private $code;
		private $message;

		public function __construct( $code = '', $message = '' ) {
			$this->code    = $code;
			$this->message = $message;
		}
		public function get_error_code() {
			return $this->code;
		}
		public function get_error_message() {
			return $this->message;
		}
	}

	class SF_Fake_WPDB {
		public $posts    = 'wp_posts';
		public $postmeta = 'wp_postmeta';

		public function prepare( $query, ...$args ) {
			return $query . ' -- ' . wp_json_encode( $args );
		}
		public function get_row( $query, $output = null ) {
			// The content "version": tests bump it to stand for an edit.
			return array( 'n' => '5', 'm' => '2026-10-27 12:00:00', 'c' => (string) $GLOBALS['sf_fake']['content_version'] );
		}
	}

	function sf_fake_reset() {
		$GLOBALS['wpdb']     = new SF_Fake_WPDB();
		$GLOBALS['slim_seo'] = new SlimSEO\FakeContainer();
		unset( $GLOBALS['post'] );

		$auto = 'Custom fountain design for commercial plazas, hotels and homes across Utah.';

		$GLOBALS['sf_fake'] = array(
			'content_version'       => 1,
			'features_off'          => array(),
			'transients'            => array(),
			'set_transient_calls'   => 0,
			'http_calls'            => 0,
			'http'                  => null,
			'caps'                  => array(),
			'deny'                  => array(),
			'wc_pages'              => array(),
			'slim_content_throws'   => false,
			'slim_content'          => array(
				11 => '<section><h1>Fountain Design</h1><h2>Our process</h2></section>',
			),
			'options'               => array(
				'show_on_front'   => 'page',
				'page_on_front'   => 10,
				'blog_public'     => '1',
				'blogname'        => 'Sapphire Fountains',
				'blogdescription' => 'Design, Build, Service, Rent',
				'active_plugins'  => array( 'slim-seo/slim-seo.php', 'slim-seo-schema/slim-seo-schema.php', 'woocommerce/woocommerce.php' ),
				'slim_seo'        => array(
					'page'       => array( 'title' => '{{ post.title }} {{ sep }} {{ site.title }}' ),
					'post'       => array( 'noindex' => 0 ),
					'ai_api_key' => SF_FAKE_AI_KEY,
				),
			),
			'posts'                 => array(
				10 => new WP_Post( array( 'ID' => 10, 'post_type' => 'page', 'post_title' => 'Home' ) ),
				11 => new WP_Post( array( 'ID' => 11, 'post_type' => 'page', 'post_title' => 'Fountain Design' ) ),
				12 => new WP_Post(
					array(
						'ID'           => 12,
						'post_type'    => 'post',
						'post_title'   => 'Winterizing your fountain',
						'post_content' => '<!-- wp:heading --><h2>Why winterize</h2><!-- /wp:heading --><p>Drain it.</p><img class="wp-image-21" src="x.jpg">',
					)
				),
				13 => new WP_Post( array( 'ID' => 13, 'post_type' => 'product', 'post_title' => 'Three-Tier Fountain', 'post_content' => '<h2>Specifications</h2>' ) ),
				14 => new WP_Post(
					array(
						'ID'            => 14,
						'post_type'     => 'page',
						'post_title'    => 'Private Pricing',
						'post_password' => 'letmein',
						'post_content'  => '<h2>Pricing tiers</h2><p>Members only.</p><img class="wp-image-24" src="tiers.png">',
					)
				),
				15 => new WP_Post( array( 'ID' => 15, 'post_type' => 'page', 'post_title' => 'Draft', 'post_status' => 'draft' ) ),
			),
			'permalinks'            => array(
				10 => 'https://www.example.com/',
				11 => 'https://www.example.com/fountain-design/',
				12 => 'https://www.example.com/blog/winterizing/',
				13 => 'https://www.example.com/product/three-tier-fountain/',
				14 => 'https://www.example.com/private-pricing/',
				15 => 'https://www.example.com/?page_id=15',
			),
			'meta'                  => array(
				10 => array(
					'slim_seo' => array(
						'title'       => 'Sapphire Fountains: Fountain Design, Build, Service and Rental',
						'description' => 'Utah fountain design, build, service and rentals for over 30 years, for commercial and residential water features.',
					),
				),
				11 => array(
					'_bricks_page_content_2' => array(
						array(
							'id'       => 'abcdef',
							'name'     => 'image',
							'settings' => array( 'image' => array( 'id' => 22, 'url' => 'https://www.example.com/wp-content/uploads/hero.jpg' ) ),
						),
					),
				),
				12 => array(
					'slim_seo' => array(
						'title'       => 'Winterizing an outdoor fountain',
						'description' => 'Short.',
						'noindex'     => 1,
					),
				),
				13 => array(
					'slim_seo' => array(
						'description' => $auto,
						'canonical'   => '{{ site.url }}/shop/three-tier/',
					),
				),
				14 => array(
					'slim_seo' => array( 'description' => SF_FAKE_PRIVATE_DESCRIPTION ),
				),
				20 => array( '_wp_attachment_image_alt' => '' ),
				21 => array( '_wp_attachment_image_alt' => 'A fountain drained for winter' ),
				22 => array( '_wp_attachment_image_alt' => '' ),
				23 => array( '_wp_attachment_image_alt' => 'Plaza fountain at dusk' ),
				24 => array( '_wp_attachment_image_alt' => '' ),
			),
			'thumbnails'            => array( 11 => 20 ),
			'attachments'           => array(
				20 => array( 'title' => 'IMG_2034', 'parent' => 0 ),
				21 => array( 'title' => 'drained', 'parent' => 0 ),
				22 => array( 'title' => 'hero-final-v2', 'parent' => 0 ),
				23 => array( 'title' => 'plaza', 'parent' => 11 ),
				24 => array( 'title' => 'tiers', 'parent' => 0 ),
			),
			'effective_title'       => array(
				10 => 'Sapphire Fountains: Fountain Design, Build, Service and Rental',
				11 => 'Fountain Design - Sapphire Fountains',
				12 => 'Winterizing an outdoor fountain',
				13 => 'Three-Tier Fountain - Sapphire Fountains',
				14 => 'Private Pricing - Sapphire Fountains',
			),
			'effective_description' => array(
				10 => 'Utah fountain design, build, service and rentals for over 30 years, for commercial and residential water features.',
				11 => $auto,
				12 => 'Short.',
				13 => $auto,
				14 => '',
			),
			'routes'                => array(
				'/fountain-design/'              => 11,
				'/blog/winterizing/'             => 12,
				'/product/three-tier-fountain/'  => 13,
				'/private-pricing/'              => 14,
			),
		);
	}

	/* -- Hooks and the Abilities API (recorded) --------------------------- */

	$GLOBALS['sf_test_hooks']      = array();
	$GLOBALS['sf_test_abilities']  = array();
	$GLOBALS['sf_test_categories'] = array();

	function add_action( $hook, $callback ) {
		$GLOBALS['sf_test_hooks'][ $hook ][] = $callback;
	}
	function wp_has_ability_category( $slug ) {
		return isset( $GLOBALS['sf_test_categories'][ $slug ] );
	}
	function wp_register_ability_category( $slug, $args ) {
		$GLOBALS['sf_test_categories'][ $slug ] = $args;
	}
	function wp_register_ability( $name, $args ) {
		$GLOBALS['sf_test_abilities'][ $name ] = $args;
	}

	/* -- Users ------------------------------------------------------------- */

	/** Grants everything except the capabilities a test lists in sf_fake['deny']. */
	function current_user_can( $capability, ...$args ) {
		$GLOBALS['sf_fake']['caps'][] = $capability;
		return ! in_array( $capability, $GLOBALS['sf_fake']['deny'], true );
	}

	function wc_get_page_id( $page ) {
		return $GLOBALS['sf_fake']['wc_pages'][ $page ] ?? -1;
	}

	/* -- Options, transients ---------------------------------------------- */

	function get_option( $name, $default = false ) {
		return array_key_exists( $name, $GLOBALS['sf_fake']['options'] ) ? $GLOBALS['sf_fake']['options'][ $name ] : $default;
	}
	function get_site_option( $name, $default = false ) {
		return $default;
	}
	function is_multisite() {
		return false;
	}
	function get_transient( $key ) {
		return $GLOBALS['sf_fake']['transients'][ $key ] ?? false;
	}
	function set_transient( $key, $value, $ttl ) {
		++$GLOBALS['sf_fake']['set_transient_calls'];
		// Round-trip through serialize, as the options table does.
		$GLOBALS['sf_fake']['transients'][ $key ] = unserialize( serialize( $value ) );
		return true;
	}

	/* -- Posts and meta ----------------------------------------------------- */

	function post_type_exists( $type ) {
		return in_array( $type, array( 'page', 'post', 'product', 'attachment' ), true );
	}
	function get_post( $id ) {
		return $GLOBALS['sf_fake']['posts'][ (int) $id ] ?? null;
	}
	function get_posts( $args ) {
		if ( 'attachment' === ( $args['post_type'] ?? '' ) ) {
			$ids = array();
			foreach ( $GLOBALS['sf_fake']['attachments'] as $id => $attachment ) {
				if ( (int) $attachment['parent'] === (int) $args['post_parent'] ) {
					$ids[] = $id;
				}
			}
			return $ids;
		}
		$out = array();
		foreach ( $GLOBALS['sf_fake']['posts'] as $post ) {
			if ( $post->post_status === $args['post_status'] && in_array( $post->post_type, (array) $args['post_type'], true ) ) {
				$out[] = $post;
			}
		}
		usort(
			$out,
			function ( $a, $b ) {
				return $a->ID <=> $b->ID;
			}
		);
		return array_slice( $out, 0, $args['posts_per_page'] );
	}
	function get_post_meta( $id, $key, $single = false ) {
		return $GLOBALS['sf_fake']['meta'][ (int) $id ][ $key ] ?? '';
	}
	function get_permalink( $post ) {
		$id = $post instanceof WP_Post ? $post->ID : (int) $post;
		return $GLOBALS['sf_fake']['permalinks'][ $id ] ?? false;
	}
	function get_post_thumbnail_id( $post ) {
		$id = $post instanceof WP_Post ? $post->ID : (int) $post;
		return $GLOBALS['sf_fake']['thumbnails'][ $id ] ?? 0;
	}
	function wp_attachment_is_image( $id ) {
		return isset( $GLOBALS['sf_fake']['attachments'][ (int) $id ] );
	}
	function wp_get_attachment_url( $id ) {
		return 'https://www.example.com/wp-content/uploads/' . (int) $id . '.jpg';
	}
	function get_the_title( $id ) {
		return $GLOBALS['sf_fake']['attachments'][ (int) $id ]['title'] ?? '';
	}
	function url_to_postid( $url ) {
		$path = parse_url( $url, PHP_URL_PATH );
		return $GLOBALS['sf_fake']['routes'][ $path ] ?? 0;
	}
	function do_blocks( $content ) {
		return $content;
	}

	/* -- URLs, sanitizing, JSON -------------------------------------------- */

	function home_url( $path = '' ) {
		return 'https://www.example.com' . $path;
	}
	function esc_url_raw( $url ) {
		return trim( (string) $url );
	}
	function wp_parse_url( $url ) {
		return parse_url( $url );
	}
	function untrailingslashit( $value ) {
		return rtrim( (string) $value, '/\\' );
	}
	function sanitize_key( $key ) {
		return preg_replace( '/[^a-z0-9_\-]/', '', strtolower( (string) $key ) );
	}
	function absint( $value ) {
		return abs( (int) $value );
	}
	function rest_sanitize_boolean( $value ) {
		if ( is_string( $value ) ) {
			$value = strtolower( $value );
			if ( in_array( $value, array( 'false', '0' ), true ) ) {
				$value = false;
			}
		}
		return (bool) $value;
	}
	function wp_json_encode( $data ) {
		return json_encode( $data );
	}
	function maybe_serialize( $data ) {
		return is_array( $data ) || is_object( $data ) ? serialize( $data ) : $data;
	}
	function is_wp_error( $thing ) {
		return $thing instanceof WP_Error;
	}

	/* -- HTTP: what a fetch of the page "returns" --------------------------- */

	function wp_safe_remote_get( $url, $args = array() ) {
		++$GLOBALS['sf_fake']['http_calls'];
		$GLOBALS['sf_fake']['http_url'] = $url;
		return $GLOBALS['sf_fake']['http'];
	}
	function wp_remote_retrieve_response_code( $response ) {
		return $response['response']['code'] ?? '';
	}
	function wp_remote_retrieve_body( $response ) {
		return $response['body'] ?? '';
	}
	function wp_remote_retrieve_header( $response, $name ) {
		return $response['headers'][ strtolower( $name ) ] ?? '';
	}

	sf_fake_reset();
}
