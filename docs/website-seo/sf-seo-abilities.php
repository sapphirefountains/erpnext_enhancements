<?php
/**
 * Plugin Name: Sapphire Fountains — SEO Abilities (read-only)
 * Description: Read-only SEO audit abilities for the WordPress Abilities API and MCP Adapter: a content audit list, a single-page detail and a site summary, built on what Slim SEO stores and outputs.
 * Version:     1.0.0
 * Author:      Sapphire Fountains
 * Requires at least: 6.9
 * Requires PHP: 7.4
 *
 * Install as a must-use plugin: drop this one file into wp-content/mu-plugins/.
 * The install guide is docs/website-seo/README.md in the erpnext_enhancements
 * repository, which is the source of truth for this file. Edit it there, not on
 * WP Engine and not as a Code Snippets entry, or the next upload overwrites the fix.
 *
 * Phase 1 of AI-assisted SEO (2026-10-27): an AI client can audit the site over
 * MCP and cannot change anything through these abilities. Every ability is
 * read-only. The only thing this file ever stores is a five-minute read cache
 * of the audit (one transient), so that paging through the list does not
 * re-render every page. tests: scripts/test_sf_seo_abilities.php fails the
 * build on any other write call.
 *
 * Contracts this relies on, read from source rather than guessed:
 *  - Abilities API (core since 6.9): abilities register on `wp_abilities_api_init`,
 *    categories on `wp_abilities_api_categories_init`; a name is `namespace/name`.
 *    https://developer.wordpress.org/apis/abilities-api/
 *  - MCP Adapter: the default server lists an ability only when `meta.mcp.public`
 *    is true (McpAbilityExposure, v0.6.0+), and maps `meta.annotations.readonly`
 *    to MCP's readOnlyHint.
 *  - Slim SEO 4.11: a post's own settings are the post meta `slim_seo`, an array
 *    with `title`, `description`, `canonical`, `noindex`, `facebook_image`,
 *    `twitter_image`. Blank means "use the post type's setting from the `slim_seo`
 *    option, else Slim SEO's default". The effective values are computed with
 *    Slim SEO's own MetaTags classes, so the audit and the page agree.
 *
 * @package sapphire-fountains
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

if ( ! defined( 'SF_SEO_ABILITIES_VERSION' ) ) {
	define( 'SF_SEO_ABILITIES_VERSION', '1.0.0' );
}

/**
 * The audit rules, as pure functions.
 *
 * Nothing in this class calls WordPress, so the CLI test in the repo can load
 * this file and check every rule without a WordPress install. Keep it that way:
 * a rule that needs WordPress belongs in the sf_seo_* functions below.
 */
final class SF_SEO_Rules {

	/** Slim SEO's own editor turns its title counter amber above 60 characters (PostTitle.js). */
	const TITLE_MAX = 60;

	/** Ours, not Slim SEO's, which sets no minimum: under 30 characters wastes the line Google shows. */
	const TITLE_MIN = 30;

	/**
	 * Slim SEO's editor recommends 50–160 characters (PostDescription.js), and
	 * cuts the description it writes itself at 160.
	 */
	const DESCRIPTION_MIN = 50;
	const DESCRIPTION_MAX = 160;

	/** The hard cap on one page of the audit list, whatever the caller asks for. */
	const PER_PAGE_MAX     = 100;
	const PER_PAGE_DEFAULT = 50;

	/** How many example pages the site summary names per flag. */
	const EXAMPLES_PER_FLAG = 5;

	/** Every flag, in the order they are reported. */
	const FLAGS = array(
		'title_missing',
		'title_too_short',
		'title_too_long',
		'duplicate_title',
		'description_missing',
		'description_not_output',
		'description_too_short',
		'description_too_long',
		'duplicate_description',
		'noindex',
		'canonical_elsewhere',
	);

	/**
	 * What each flag means, in words an editor can act on. Returned by the
	 * abilities so the rules travel with the results.
	 *
	 * @return array<string, string>
	 */
	public static function flag_rules() {
		return array(
			'title_missing'          => 'No Slim SEO title of its own. The page shows the post type\'s title template, or Slim SEO\'s default "Page title - Site name".',
			'title_too_short'        => 'The title as output is under ' . self::TITLE_MIN . ' characters.',
			'title_too_long'         => 'The title as output is over ' . self::TITLE_MAX . ' characters. Slim SEO\'s editor warns above ' . self::TITLE_MAX . ', and Google cuts long titles.',
			'duplicate_title'        => 'Another published, indexable page, post or product outputs the same title (case and spacing ignored).',
			'description_missing'    => 'No Slim SEO description of its own. Slim SEO writes one from the excerpt or the first 160 characters of the content.',
			'description_not_output' => 'No meta description is output at all.',
			'description_too_short'  => 'The description as output is under ' . self::DESCRIPTION_MIN . ' characters.',
			'description_too_long'   => 'The description as output is over ' . self::DESCRIPTION_MAX . ' characters.',
			'duplicate_description'  => 'Another published, indexable page, post or product outputs the same description (case and spacing ignored).',
			'noindex'                => 'Published, but noindexed by its own Slim SEO setting or by its post type\'s.',
			'canonical_elsewhere'    => 'Its canonical URL is set to a different page, so search engines are told to index that page instead.',
		);
	}

	/**
	 * The length thresholds, for the abilities to report alongside the flags.
	 *
	 * @return array<string, array<string, mixed>>
	 */
	public static function thresholds() {
		return array(
			'title'       => array(
				'min'    => self::TITLE_MIN,
				'max'    => self::TITLE_MAX,
				'source' => 'Max is Slim SEO\'s editor warning (over 60). Min is ours; Slim SEO sets none.',
			),
			'description' => array(
				'min'    => self::DESCRIPTION_MIN,
				'max'    => self::DESCRIPTION_MAX,
				'source' => 'Slim SEO\'s editor recommendation (50-160 characters).',
			),
		);
	}

	/**
	 * Valid UTF-8, or JSON encoding of the whole response fails and the client
	 * gets nothing at all.
	 *
	 * @param mixed $text Anything stringable.
	 * @return string
	 */
	public static function utf8( $text ) {
		$text = (string) $text;
		if ( '' === $text || preg_match( '//u', $text ) ) {
			return $text;
		}
		if ( function_exists( 'mb_convert_encoding' ) ) {
			return (string) mb_convert_encoding( $text, 'UTF-8', 'UTF-8' );
		}
		return (string) iconv( 'UTF-8', 'UTF-8//IGNORE', $text );
	}

	/**
	 * Text as a reader sees it: entities decoded, tags dropped, runs of
	 * whitespace (non-breaking spaces included) collapsed, ends trimmed.
	 *
	 * @param mixed $text Raw text.
	 * @return string
	 */
	public static function clean_text( $text ) {
		$text = self::utf8( $text );
		$text = html_entity_decode( $text, ENT_QUOTES | ENT_HTML5, 'UTF-8' );
		$text = (string) preg_replace( '/<[^>]*>/u', ' ', $text );
		$text = (string) preg_replace( '/[\s\x{00A0}]+/u', ' ', $text );
		return trim( $text );
	}

	/**
	 * Length in characters (not bytes) of the text a reader sees.
	 *
	 * @param mixed $text Raw text.
	 * @return int
	 */
	public static function length( $text ) {
		$text = self::clean_text( $text );
		if ( function_exists( 'mb_strlen' ) ) {
			return (int) mb_strlen( $text, 'UTF-8' );
		}
		return (int) preg_match_all( '/./us', $text );
	}

	/**
	 * The key two texts are compared on for duplicates: clean, lower case.
	 * Empty text never counts as a duplicate.
	 *
	 * @param mixed $text Raw text.
	 * @return string
	 */
	public static function duplicate_key( $text ) {
		$text = self::clean_text( $text );
		return function_exists( 'mb_strtolower' ) ? mb_strtolower( $text, 'UTF-8' ) : strtolower( $text );
	}

	/**
	 * Which items share a text with another.
	 *
	 * @param array<int, string> $texts Item ID => text.
	 * @return array<int, int[]> Item ID => the other IDs with the same text. Unique and empty texts are absent.
	 */
	public static function duplicates( array $texts ) {
		$groups = array();
		foreach ( $texts as $id => $text ) {
			$key = self::duplicate_key( $text );
			if ( '' === $key ) {
				continue;
			}
			$groups[ $key ][] = (int) $id;
		}

		$out = array();
		foreach ( $groups as $ids ) {
			if ( count( $ids ) < 2 ) {
				continue;
			}
			foreach ( $ids as $id ) {
				$out[ $id ] = array_values( array_diff( $ids, array( $id ) ) );
			}
		}
		return $out;
	}

	/**
	 * A URL reduced to what decides whether two URLs are the same page: host
	 * (lower case) and path without its trailing slash, plus the query. The
	 * scheme is ignored, so http and https of one page compare equal.
	 *
	 * @param string $url Absolute or root-relative URL.
	 * @return array{0: string, 1: string} Host ('' when relative) and path with query.
	 */
	private static function url_parts( $url ) {
		$parts = parse_url( trim( (string) $url ) );
		if ( false === $parts ) {
			return array( '', rtrim( trim( (string) $url ), '/' ) );
		}
		$host  = isset( $parts['host'] ) ? strtolower( $parts['host'] ) : '';
		$path  = isset( $parts['path'] ) ? rtrim( $parts['path'], '/' ) : '';
		$query = isset( $parts['query'] ) && '' !== $parts['query'] ? '?' . $parts['query'] : '';
		return array( $host, $path . $query );
	}

	/**
	 * Whether a canonical URL points somewhere other than the page itself.
	 * A blank canonical means "this page", so it never does. A relative
	 * canonical is compared on its path alone.
	 *
	 * @param string $canonical The canonical URL Slim SEO outputs from the post's setting.
	 * @param string $url       The page's own permalink.
	 * @return bool
	 */
	public static function canonical_elsewhere( $canonical, $url ) {
		if ( '' === trim( (string) $canonical ) ) {
			return false;
		}
		list( $canonical_host, $canonical_path ) = self::url_parts( $canonical );
		list( $own_host, $own_path )             = self::url_parts( $url );

		if ( '' !== $canonical_host && '' !== $own_host && $canonical_host !== $own_host ) {
			return true;
		}
		return $canonical_path !== $own_path;
	}

	/**
	 * The flags for one audit record.
	 *
	 * Length flags measure the effective text, the one the page outputs. When
	 * Slim SEO's renderer was unavailable the effective text is null, and the
	 * stored text is measured instead.
	 *
	 * @param array $record                An audit record (see sf_seo_record()).
	 * @param bool  $duplicate_title       Whether another indexable item outputs the same title.
	 * @param bool  $duplicate_description Whether another indexable item outputs the same description.
	 * @return string[]
	 */
	public static function flags( array $record, $duplicate_title = false, $duplicate_description = false ) {
		$flags = array();

		$stored_title = isset( $record['title']['stored'] ) ? $record['title']['stored'] : '';
		$title        = isset( $record['title']['effective'] ) ? $record['title']['effective'] : null;
		$stored_desc  = isset( $record['description']['stored'] ) ? $record['description']['stored'] : '';
		$desc         = isset( $record['description']['effective'] ) ? $record['description']['effective'] : null;

		if ( '' === self::clean_text( $stored_title ) ) {
			$flags[] = 'title_missing';
		}
		$title_length = self::length( null !== $title ? $title : $stored_title );
		if ( $title_length > self::TITLE_MAX ) {
			$flags[] = 'title_too_long';
		} elseif ( $title_length > 0 && $title_length < self::TITLE_MIN ) {
			$flags[] = 'title_too_short';
		}
		if ( $duplicate_title ) {
			$flags[] = 'duplicate_title';
		}

		if ( '' === self::clean_text( $stored_desc ) ) {
			$flags[] = 'description_missing';
		}
		$desc_length = self::length( null !== $desc ? $desc : $stored_desc );
		if ( 0 === $desc_length ) {
			// Only a known-empty output is "not output"; an unknown one is just missing.
			if ( null !== $desc ) {
				$flags[] = 'description_not_output';
			}
		} elseif ( $desc_length > self::DESCRIPTION_MAX ) {
			$flags[] = 'description_too_long';
		} elseif ( $desc_length < self::DESCRIPTION_MIN ) {
			$flags[] = 'description_too_short';
		}
		if ( $duplicate_description ) {
			$flags[] = 'duplicate_description';
		}

		if ( ! empty( $record['noindex'] ) ) {
			$flags[] = 'noindex';
		}
		if ( ! empty( $record['canonical']['points_elsewhere'] ) ) {
			$flags[] = 'canonical_elsewhere';
		}

		// Report in the documented order, whatever order they were found in.
		return array_values( array_intersect( self::FLAGS, $flags ) );
	}

	/**
	 * Sets `flags` and the duplicate lists on every record of an index.
	 *
	 * Duplicates are counted among indexable records only. A noindexed page
	 * cannot compete with anything in search, and it is already flagged.
	 *
	 * @param array<int, array> $records Item ID => audit record.
	 * @return array<int, array>
	 */
	public static function apply_flags( array $records ) {
		$titles = array();
		$descs  = array();
		foreach ( $records as $id => $record ) {
			if ( ! empty( $record['noindex'] ) ) {
				continue;
			}
			$titles[ $id ] = self::effective_or_stored( $record, 'title' );
			$descs[ $id ]  = self::effective_or_stored( $record, 'description' );
		}
		$duplicate_titles = self::duplicates( $titles );
		$duplicate_descs  = self::duplicates( $descs );

		foreach ( $records as $id => $record ) {
			$records[ $id ]['duplicate_title_of']       = isset( $duplicate_titles[ $id ] ) ? $duplicate_titles[ $id ] : array();
			$records[ $id ]['duplicate_description_of'] = isset( $duplicate_descs[ $id ] ) ? $duplicate_descs[ $id ] : array();
			$records[ $id ]['flags']                    = self::flags(
				$records[ $id ],
				! empty( $records[ $id ]['duplicate_title_of'] ),
				! empty( $records[ $id ]['duplicate_description_of'] )
			);
		}
		return $records;
	}

	/**
	 * The effective text of a record's field, or the stored one when the
	 * effective text is unknown.
	 *
	 * @param array  $record An audit record.
	 * @param string $field  'title' or 'description'.
	 * @return string
	 */
	public static function effective_or_stored( array $record, $field ) {
		if ( isset( $record[ $field ]['effective'] ) ) {
			return (string) $record[ $field ]['effective'];
		}
		return isset( $record[ $field ]['stored'] ) ? (string) $record[ $field ]['stored'] : '';
	}

	/**
	 * Records matching the list filters, in index order.
	 *
	 * @param array  $records      Audit records.
	 * @param string $post_type    'any' or one post type.
	 * @param bool   $flagged_only Only records with at least one flag.
	 * @param string $flag         '' or one flag: only records carrying it.
	 * @return array[]
	 */
	public static function filter( array $records, $post_type = 'any', $flagged_only = false, $flag = '' ) {
		$out = array();
		foreach ( $records as $record ) {
			if ( 'any' !== $post_type && ( ! isset( $record['type'] ) || $record['type'] !== $post_type ) ) {
				continue;
			}
			$flags = isset( $record['flags'] ) ? (array) $record['flags'] : array();
			if ( $flagged_only && empty( $flags ) ) {
				continue;
			}
			if ( '' !== $flag && ! in_array( $flag, $flags, true ) ) {
				continue;
			}
			$out[] = $record;
		}
		return $out;
	}

	/**
	 * Clamped paging. `per_page` never exceeds PER_PAGE_MAX, whatever arrives.
	 *
	 * @param mixed $page     Requested page, 1-based.
	 * @param mixed $per_page Requested page size.
	 * @param int   $total    Number of matching records.
	 * @return array{page: int, per_page: int, total: int, total_pages: int, offset: int}
	 */
	public static function paging( $page, $per_page, $total ) {
		$per_page = (int) $per_page;
		if ( $per_page < 1 ) {
			$per_page = self::PER_PAGE_DEFAULT;
		}
		$per_page = min( $per_page, self::PER_PAGE_MAX );
		$page     = max( 1, (int) $page );
		$total    = max( 0, (int) $total );

		return array(
			'page'        => $page,
			'per_page'    => $per_page,
			'total'       => $total,
			'total_pages' => (int) ceil( $total / $per_page ),
			'offset'      => ( $page - 1 ) * $per_page,
		);
	}

	/**
	 * Counts per flag and per post type, plus a few example IDs per flag.
	 * Every flag is present in every count, at zero when nothing carries it,
	 * so a reader can tell "none" from "not checked".
	 *
	 * @param array[] $records Audit records.
	 * @return array
	 */
	public static function summarize( array $records ) {
		$zero     = array_fill_keys( self::FLAGS, 0 );
		$by_flag  = $zero;
		$by_type  = array();
		$examples = array_fill_keys( self::FLAGS, array() );
		$flagged  = 0;

		foreach ( $records as $record ) {
			$type  = isset( $record['type'] ) ? (string) $record['type'] : 'unknown';
			$flags = isset( $record['flags'] ) ? (array) $record['flags'] : array();

			if ( ! isset( $by_type[ $type ] ) ) {
				$by_type[ $type ] = array(
					'total'   => 0,
					'flagged' => 0,
					'by_flag' => $zero,
				);
			}
			++$by_type[ $type ]['total'];
			if ( $flags ) {
				++$flagged;
				++$by_type[ $type ]['flagged'];
			}
			foreach ( $flags as $flag ) {
				if ( ! isset( $by_flag[ $flag ] ) ) {
					continue;
				}
				++$by_flag[ $flag ];
				++$by_type[ $type ]['by_flag'][ $flag ];
				if ( count( $examples[ $flag ] ) < self::EXAMPLES_PER_FLAG && isset( $record['id'] ) ) {
					$examples[ $flag ][] = (int) $record['id'];
				}
			}
		}

		return array(
			'total'    => count( $records ),
			'flagged'  => $flagged,
			'by_flag'  => $by_flag,
			'by_type'  => $by_type,
			'examples' => $examples,
		);
	}

	/**
	 * Strips what a browser never renders as page text, so a heading inside a
	 * script, a style, a template or a comment is not reported as one.
	 *
	 * @param string $html Markup.
	 * @return string
	 */
	public static function visible_markup( $html ) {
		$html = self::utf8( $html );
		$html = (string) preg_replace( '/<!--.*?-->/s', ' ', $html );
		return (string) preg_replace( '#<(script|style|template|noscript)\b[^>]*>.*?</\1\s*>#is', ' ', $html );
	}

	/**
	 * The h1–h6 headings in markup, in document order.
	 *
	 * @param string $html Markup.
	 * @return array<int, array{level: int, text: string}>
	 */
	public static function headings( $html ) {
		$html = self::visible_markup( $html );
		if ( ! preg_match_all( '#<h([1-6])\b[^>]*>(.*?)</h\1\s*>#is', $html, $matches, PREG_SET_ORDER ) ) {
			return array();
		}
		$out = array();
		foreach ( $matches as $match ) {
			$out[] = array(
				'level' => (int) $match[1],
				'text'  => self::clean_text( $match[2] ),
			);
		}
		return $out;
	}

	/**
	 * The value of one attribute of one HTML tag, or null when absent.
	 *
	 * @param string $tag  The tag's markup, e.g. `<meta name="x" content="y">`.
	 * @param string $name Attribute name.
	 * @return string|null
	 */
	public static function attribute( $tag, $name ) {
		$pattern = '/\s' . preg_quote( $name, '/' ) . '\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'>]+))/i';
		if ( ! preg_match( $pattern, $tag, $match ) ) {
			return null;
		}
		$value = '';
		foreach ( array( 1, 2, 3 ) as $group ) {
			if ( isset( $match[ $group ] ) && '' !== $match[ $group ] ) {
				$value = $match[ $group ];
				break;
			}
		}
		return html_entity_decode( $value, ENT_QUOTES | ENT_HTML5, 'UTF-8' );
	}

	/**
	 * The SEO tags of a served page: title, meta description, canonical and
	 * meta robots. Null for any that is absent.
	 *
	 * @param string $html The page's markup.
	 * @return array{title: string|null, description: string|null, canonical: string|null, robots: string|null, noindex: bool}
	 */
	public static function head_tags( $html ) {
		$html = self::visible_markup( $html );
		$out  = array(
			'title'       => null,
			'description' => null,
			'canonical'   => null,
			'robots'      => null,
			'noindex'     => false,
		);

		// The document <title>, not one inside an inline SVG.
		if ( preg_match( '#<head\b.*?<title\b[^>]*>(.*?)</title\s*>#is', $html, $match ) || preg_match( '#<title\b[^>]*>(.*?)</title\s*>#is', $html, $match ) ) {
			$out['title'] = self::clean_text( $match[1] );
		}

		if ( preg_match_all( '#<meta\b[^>]*>#i', $html, $metas ) ) {
			foreach ( $metas[0] as $tag ) {
				$name = strtolower( (string) self::attribute( $tag, 'name' ) );
				if ( 'description' === $name && null === $out['description'] ) {
					$out['description'] = self::clean_text( (string) self::attribute( $tag, 'content' ) );
				} elseif ( 'robots' === $name && null === $out['robots'] ) {
					$out['robots'] = trim( (string) self::attribute( $tag, 'content' ) );
				}
			}
		}

		if ( preg_match_all( '#<link\b[^>]*>#i', $html, $links ) ) {
			foreach ( $links[0] as $tag ) {
				$rel = strtolower( (string) self::attribute( $tag, 'rel' ) );
				if ( 'canonical' === $rel ) {
					$out['canonical'] = trim( (string) self::attribute( $tag, 'href' ) );
					break;
				}
			}
		}

		$out['noindex'] = null !== $out['robots'] && false !== stripos( $out['robots'], 'noindex' );
		return $out;
	}

	/**
	 * The JSON-LD blocks in a served page, each with the schema.org types it
	 * declares (found anywhere in it, @graph included).
	 *
	 * @param string $html The page's markup.
	 * @return array<int, array{id: string|null, valid: bool, types: string[]}>
	 */
	public static function json_ld_blocks( $html ) {
		$html = (string) preg_replace( '/<!--.*?-->/s', ' ', self::utf8( $html ) );
		if ( ! preg_match_all( '#<script\b([^>]*)>(.*?)</script\s*>#is', $html, $scripts, PREG_SET_ORDER ) ) {
			return array();
		}
		$out = array();
		foreach ( $scripts as $script ) {
			$type = strtolower( trim( (string) self::attribute( '<script ' . $script[1] . '>', 'type' ) ) );
			if ( 'application/ld+json' !== $type ) {
				continue;
			}
			$data  = json_decode( trim( $script[2] ), true );
			$types = array();
			if ( is_array( $data ) ) {
				self::collect_types( $data, $types );
			}
			$out[] = array(
				'id'    => self::attribute( '<script ' . $script[1] . '>', 'id' ),
				'valid' => is_array( $data ),
				'types' => array_values( array_unique( $types ) ),
			);
		}
		return $out;
	}

	/**
	 * Every @type value anywhere in decoded JSON-LD.
	 *
	 * @param array    $node  Decoded JSON.
	 * @param string[] $types Collected types, appended to.
	 */
	private static function collect_types( array $node, array &$types ) {
		foreach ( $node as $key => $value ) {
			if ( '@type' === $key ) {
				foreach ( (array) $value as $type ) {
					if ( is_string( $type ) && '' !== $type ) {
						$types[] = $type;
					}
				}
			} elseif ( is_array( $value ) ) {
				self::collect_types( $value, $types );
			}
		}
	}

	/**
	 * Image attachment IDs referenced anywhere in a page builder's stored data:
	 * any array carrying a numeric `id` beside a `url` string. That is the shape
	 * of an image control's value (an image, a gallery item, a background), and
	 * every candidate is checked against the media library before it is used,
	 * so a stray match costs a lookup, never a wrong answer.
	 *
	 * @param mixed $data Decoded builder data.
	 * @return int[]
	 */
	public static function image_ids_in_builder_data( $data ) {
		$ids = array();
		if ( ! is_array( $data ) ) {
			return $ids;
		}
		if ( isset( $data['id'], $data['url'] ) && is_string( $data['url'] ) && is_numeric( $data['id'] ) && (int) $data['id'] > 0 ) {
			$ids[] = (int) $data['id'];
		}
		foreach ( $data as $value ) {
			if ( is_array( $value ) ) {
				$ids = array_merge( $ids, self::image_ids_in_builder_data( $value ) );
			}
		}
		return array_values( array_unique( $ids ) );
	}
}

/* ---------------------------------------------------------------------------
 * Everything below needs WordPress. Nothing runs at load except add_action().
 * ------------------------------------------------------------------------- */

/** The read cache: one transient, five minutes, rebuilt early whenever the content changes. */
const SF_SEO_CACHE_KEY = 'sf_seo_audit_index_v1';
const SF_SEO_CACHE_TTL = 300;

/** The most published items one audit reads. The site had about 110 on 2026-10-27. */
const SF_SEO_MAX_ITEMS = 2000;

/** Slim SEO features the audit depends on, by the IDs its own settings use. */
const SF_SEO_SLIM_FEATURES = array( 'meta_title', 'meta_description', 'meta_robots', 'canonical_url', 'schema', 'images_alt', 'sitemaps', 'redirection' );

add_action( 'wp_abilities_api_categories_init', 'sf_seo_register_category' );
add_action( 'wp_abilities_api_init', 'sf_seo_register_abilities' );

/**
 * One category of our own, so the abilities group together and can be listed
 * with `?category=sapphire-seo`.
 */
function sf_seo_register_category() {
	if ( function_exists( 'wp_has_ability_category' ) && wp_has_ability_category( 'sapphire-seo' ) ) {
		return;
	}
	wp_register_ability_category(
		'sapphire-seo',
		array(
			'label'       => 'Sapphire SEO',
			'description' => 'Read-only SEO audit of the marketing site: titles, descriptions, noindex, canonicals, duplicates, image alt text and schema, as Slim SEO stores and outputs them.',
		)
	);
}

/**
 * Who may run the audit. Editors and up: the AI's own `seo-agent` user is an
 * Editor. Never `__return_true` — the audit reads unpublished settings.
 *
 * @param mixed $input Unused; core passes the input when an input schema exists.
 * @return bool
 */
function sf_seo_can_audit( $input = null ) { // phpcs:ignore Generic.CodeAnalysis.UnusedFunctionParameter
	return current_user_can( 'edit_posts' );
}

/**
 * The meta every ability shares: listed in REST, listed on the MCP Adapter's
 * default server, and annotated read-only so a client knows calling it is safe.
 *
 * @return array
 */
function sf_seo_ability_meta() {
	return array(
		'show_in_rest' => true,
		'annotations'  => array(
			'readonly'    => true,
			'destructive' => false,
			'idempotent'  => true,
		),
		'mcp'          => array(
			'public' => true,
			'type'   => 'tool',
		),
	);
}

function sf_seo_register_abilities() {
	$meta = sf_seo_ability_meta();

	wp_register_ability(
		'sapphire-seo/audit-content',
		array(
			'label'               => 'SEO audit: content list',
			'description'         => 'Lists published pages, posts and WooCommerce products with their Slim SEO title and description (as stored, and as the page outputs them), lengths, noindex, canonical and audit flags: missing, too short, too long, duplicate, noindexed, canonical elsewhere. Filter by post type, by "flagged only" or by one flag. Paged, at most 100 per page. Read-only. Start with sapphire-seo/site-summary for the totals.',
			'category'            => 'sapphire-seo',
			'input_schema'        => sf_seo_audit_input_schema(),
			'output_schema'       => sf_seo_audit_output_schema(),
			'execute_callback'    => 'sf_seo_audit_content',
			'permission_callback' => 'sf_seo_can_audit',
			'meta'                => $meta,
		)
	);

	wp_register_ability(
		'sapphire-seo/page-detail',
		array(
			'label'               => 'SEO audit: one page',
			'description'         => 'Everything the content list reports for one published page, post or product, found by ID or URL, plus its images missing alt text (featured, attached, product gallery and in its content), whether Slim SEO outputs schema for it, and its headings. Set live_check to fetch the page as served and read its actual title, description, canonical, robots, headings and JSON-LD schema types. Read-only.',
			'category'            => 'sapphire-seo',
			'input_schema'        => sf_seo_detail_input_schema(),
			'output_schema'       => sf_seo_detail_output_schema(),
			'execute_callback'    => 'sf_seo_page_detail',
			'permission_callback' => 'sf_seo_can_audit',
			'meta'                => $meta,
		)
	);

	wp_register_ability(
		'sapphire-seo/site-summary',
		array(
			'label'               => 'SEO audit: site summary',
			'description'         => 'A first audit in one call: counts of every flag, per post type and overall, a few example pages per flag, the thresholds and flag rules, and the site settings that change every page (Slim SEO features, "Discourage search engines"). Read-only.',
			'category'            => 'sapphire-seo',
			'input_schema'        => sf_seo_summary_input_schema(),
			'output_schema'       => sf_seo_summary_output_schema(),
			'execute_callback'    => 'sf_seo_site_summary',
			'permission_callback' => 'sf_seo_can_audit',
			'meta'                => $meta,
		)
	);
}

/* ---------------------------------------------------------------------------
 * Reading Slim SEO
 * ------------------------------------------------------------------------- */

/**
 * The post types the audit covers: pages, posts, and products when
 * WooCommerce is active.
 *
 * @return string[]
 */
function sf_seo_post_types() {
	$types = array( 'page', 'post' );
	if ( post_type_exists( 'product' ) ) {
		$types[] = 'product';
	}
	return $types;
}

/**
 * Slim SEO's own MetaTags objects, built once per request.
 *
 * They are internal classes, not a published API, so every use is guarded:
 * if a Slim SEO update renames one, the audit reports the stored values and
 * says the effective ones are unavailable, rather than failing.
 *
 * @return array{active: bool, version: string|null, title: object|null, description: object|null, settings: object|null}
 */
function sf_seo_slim() {
	static $slim = null;
	if ( null !== $slim ) {
		return $slim;
	}
	$slim = array(
		'active'      => defined( 'SLIM_SEO_VER' ),
		'version'     => defined( 'SLIM_SEO_VER' ) ? (string) SLIM_SEO_VER : null,
		'title'       => null,
		'description' => null,
		'settings'    => null,
	);
	try {
		if ( method_exists( 'SlimSEO\MetaTags\Title', 'get_rendered_singular_value' ) ) {
			$slim['title'] = new \SlimSEO\MetaTags\Title();
		}
		if ( method_exists( 'SlimSEO\MetaTags\Description', 'get_rendered_singular_value' ) ) {
			$slim['description'] = new \SlimSEO\MetaTags\Description();
		}
		// Slim SEO keeps its services on a global container; its settings answer "is this feature on".
		$container = isset( $GLOBALS['slim_seo'] ) ? $GLOBALS['slim_seo'] : null;
		if ( is_object( $container ) && method_exists( $container, 'get_service' ) ) {
			$settings = $container->get_service( 'settings' );
			if ( is_object( $settings ) && method_exists( $settings, 'is_feature_active' ) ) {
				$slim['settings'] = $settings;
			}
		}
	} catch ( \Throwable $e ) {
		// Keep whatever was built; the rest reads as unavailable.
		unset( $e );
	}
	return $slim;
}

/**
 * Whether a Slim SEO feature is switched on, or null when that can't be read.
 *
 * @param string $feature Slim SEO's feature ID, e.g. 'schema'.
 * @return bool|null
 */
function sf_seo_feature_active( $feature ) {
	$slim = sf_seo_slim();
	if ( ! $slim['settings'] ) {
		return null;
	}
	try {
		return (bool) $slim['settings']->is_feature_active( $feature );
	} catch ( \Throwable $e ) {
		return null;
	}
}

/**
 * Slim SEO's settings for one post type, from its `slim_seo` option. Only
 * the three keys the audit needs are returned: the option also holds the AI
 * provider's API key, and nothing from it may reach a response.
 *
 * @param string $post_type Post type.
 * @return array{title: string, description: string, noindex: bool}
 */
function sf_seo_type_settings( $post_type ) {
	$option = get_option( 'slim_seo', array() );
	$type   = is_array( $option ) && isset( $option[ $post_type ] ) && is_array( $option[ $post_type ] ) ? $option[ $post_type ] : array();
	return array(
		'title'       => isset( $type['title'] ) && is_string( $type['title'] ) ? $type['title'] : '',
		'description' => isset( $type['description'] ) && is_string( $type['description'] ) ? $type['description'] : '',
		'noindex'     => ! empty( $type['noindex'] ),
	);
}

/**
 * A string value from Slim SEO's stored post settings, trimmed, '' when absent.
 *
 * @param array  $stored The post's `slim_seo` meta.
 * @param string $key    Field.
 * @return string
 */
function sf_seo_stored( array $stored, $key ) {
	return isset( $stored[ $key ] ) && is_scalar( $stored[ $key ] ) ? trim( SF_SEO_Rules::utf8( $stored[ $key ] ) ) : '';
}

/**
 * Runs one of Slim SEO's renderers for a post, or null when it can't.
 *
 * @param object|null $renderer Slim SEO Title or Description.
 * @param int         $post_id  Post ID.
 * @return string|null
 */
function sf_seo_render_with( $renderer, $post_id ) {
	if ( ! $renderer ) {
		return null;
	}
	try {
		return SF_SEO_Rules::clean_text( $renderer->get_rendered_singular_value( (int) $post_id ) );
	} catch ( \Throwable $e ) {
		return null;
	}
}

/**
 * Renders Slim SEO's dynamic variables ({{ post.title }} and the like) in a
 * stored value, the way the page does. Plain text passes through.
 *
 * @param string $text    Stored value.
 * @param int    $post_id Post ID.
 * @return string
 */
function sf_seo_render_variables( $text, $post_id ) {
	if ( false === strpos( $text, '{{' ) || ! is_callable( array( 'SlimSEO\MetaTags\Helper', 'render' ) ) ) {
		return $text;
	}
	try {
		return SF_SEO_Rules::clean_text( \SlimSEO\MetaTags\Helper::render( $text, (int) $post_id ) );
	} catch ( \Throwable $e ) {
		return $text;
	}
}

function sf_seo_is_static_front( $post_id ) {
	return 'page' === get_option( 'show_on_front' ) && (int) get_option( 'page_on_front' ) === (int) $post_id;
}

/**
 * The audit record for one post, without flags (see SF_SEO_Rules::apply_flags()).
 *
 * Effective values come from Slim SEO's renderer, so they match the page:
 * the post's own value, else its post type's template, else Slim SEO's
 * default. For the description, that default is the excerpt or the first 160
 * characters of the content — Bricks content included, through Slim SEO's
 * Bricks integration.
 *
 * @param WP_Post $post A published post.
 * @return array
 */
function sf_seo_record( WP_Post $post ) {
	$slim     = sf_seo_slim();
	$stored   = get_post_meta( $post->ID, 'slim_seo', true );
	$stored   = is_array( $stored ) ? $stored : array();
	$settings = sf_seo_type_settings( $post->post_type );
	$url      = (string) get_permalink( $post );

	$stored_title     = sf_seo_stored( $stored, 'title' );
	$stored_desc      = sf_seo_stored( $stored, 'description' );
	$stored_canonical = sf_seo_stored( $stored, 'canonical' );

	$title       = sf_seo_render_with( $slim['title'], $post->ID );
	$description = sf_seo_render_with( $slim['description'], $post->ID );

	if ( '' !== $stored_title ) {
		$title_source = 'post';
	} elseif ( sf_seo_is_static_front( $post->ID ) ) {
		$title_source = 'site_default';
	} elseif ( '' !== $settings['title'] ) {
		$title_source = 'post_type_template';
	} else {
		$title_source = 'slim_seo_default';
	}

	if ( '' !== (string) $post->post_password ) {
		$desc_source = 'password_protected';
	} elseif ( '' !== $stored_desc ) {
		$desc_source = 'post';
	} elseif ( '' !== $settings['description'] ) {
		$desc_source = 'post_type_template';
	} else {
		$desc_source = 'auto';
	}

	$post_noindex = ! empty( $stored['noindex'] );
	if ( $post_noindex && $settings['noindex'] ) {
		$noindex_source = 'post_and_post_type';
	} elseif ( $post_noindex ) {
		$noindex_source = 'post';
	} elseif ( $settings['noindex'] ) {
		$noindex_source = 'post_type';
	} else {
		$noindex_source = null;
	}

	$canonical = '' === $stored_canonical ? '' : sf_seo_render_variables( $stored_canonical, $post->ID );

	return array(
		'id'                       => (int) $post->ID,
		'type'                     => (string) $post->post_type,
		'url'                      => $url,
		'post_title'               => SF_SEO_Rules::clean_text( $post->post_title ),
		'modified'                 => str_replace( ' ', 'T', (string) $post->post_modified_gmt ) . 'Z',
		'title'                    => array(
			'stored'    => $stored_title,
			'effective' => $title,
			'length'    => SF_SEO_Rules::length( null !== $title ? $title : $stored_title ),
			'source'    => $title_source,
		),
		'description'              => array(
			'stored'    => $stored_desc,
			'effective' => $description,
			'length'    => SF_SEO_Rules::length( null !== $description ? $description : $stored_desc ),
			'source'    => $desc_source,
		),
		'noindex'                  => $post_noindex || $settings['noindex'],
		'noindex_source'           => $noindex_source,
		'canonical'                => array(
			'stored'           => $stored_canonical,
			'effective'        => '' !== $canonical ? $canonical : $url,
			'points_elsewhere' => SF_SEO_Rules::canonical_elsewhere( $canonical, $url ),
		),
		'flags'                    => array(),
		'duplicate_title_of'       => array(),
		'duplicate_description_of' => array(),
	);
}

/**
 * What the cached audit depends on. Any change to a published item, to any
 * post's Slim SEO settings or Bricks content, or to Slim SEO's options gives
 * a new value, and the cache is rebuilt instead of served. Slim SEO's own
 * update abilities write post meta without touching `post_modified`, which is
 * why the meta is hashed too.
 *
 * @return string
 */
function sf_seo_fingerprint() {
	global $wpdb;

	$types        = sf_seo_post_types();
	$placeholders = implode( ', ', array_fill( 0, count( $types ), '%s' ) );
	// phpcs:ignore WordPress.DB.PreparedSQL.InterpolatedNotPrepared -- only placeholders are interpolated.
	$posts = $wpdb->get_row( $wpdb->prepare( "SELECT COUNT(*) AS n, MAX(post_modified_gmt) AS m, COALESCE(SUM(CRC32(CONCAT(ID, ':', post_title, ':', post_excerpt, ':', post_password))), 0) AS c FROM {$wpdb->posts} WHERE post_status = 'publish' AND post_type IN ($placeholders)", $types ), ARRAY_A );

	$meta_keys = array( 'slim_seo', '_thumbnail_id' );
	if ( defined( 'BRICKS_DB_PAGE_CONTENT' ) ) {
		$meta_keys[] = BRICKS_DB_PAGE_CONTENT;
	}
	$placeholders = implode( ', ', array_fill( 0, count( $meta_keys ), '%s' ) );
	// phpcs:ignore WordPress.DB.PreparedSQL.InterpolatedNotPrepared -- only placeholders are interpolated.
	$meta = $wpdb->get_row( $wpdb->prepare( "SELECT COUNT(*) AS n, COALESCE(SUM(CRC32(CONCAT(post_id, ':', meta_key, ':', meta_value))), 0) AS c FROM {$wpdb->postmeta} WHERE meta_key IN ($placeholders)", $meta_keys ), ARRAY_A );

	return md5(
		wp_json_encode(
			array(
				$posts,
				$meta,
				md5( maybe_serialize( get_option( 'slim_seo' ) ) ),
				get_option( 'blog_public' ),
				get_option( 'show_on_front' ),
				get_option( 'page_on_front' ),
				get_option( 'blogname' ),
				get_option( 'blogdescription' ),
				defined( 'SLIM_SEO_VER' ) ? SLIM_SEO_VER : '',
				SF_SEO_ABILITIES_VERSION,
			)
		)
	);
}

/**
 * The audit of every published item, flagged. Served from the read cache
 * when nothing has changed; rebuilt otherwise, or when asked to.
 *
 * @param bool $refresh Ignore the cache.
 * @return array{built_at: string, from_cache: bool, truncated: bool, items: array<int, array>}
 */
function sf_seo_index( $refresh = false ) {
	$fingerprint = sf_seo_fingerprint();
	if ( ! $refresh ) {
		$cached = get_transient( SF_SEO_CACHE_KEY );
		if ( is_array( $cached ) && isset( $cached['fingerprint'], $cached['items'] ) && $cached['fingerprint'] === $fingerprint ) {
			$cached['from_cache'] = true;
			return $cached;
		}
	}

	$posts = get_posts(
		array(
			'post_type'        => sf_seo_post_types(),
			'post_status'      => 'publish',
			'posts_per_page'   => SF_SEO_MAX_ITEMS,
			'orderby'          => 'ID',
			'order'            => 'ASC',
			'no_found_rows'    => true,
			'suppress_filters' => true,
		)
	);

	$records = array();
	foreach ( $posts as $post ) {
		$records[ (int) $post->ID ] = sf_seo_record( $post );
	}

	$index = array(
		'fingerprint' => $fingerprint,
		'built_at'    => gmdate( 'c' ),
		'truncated'   => count( $posts ) >= SF_SEO_MAX_ITEMS,
		'items'       => SF_SEO_Rules::apply_flags( $records ),
	);
	// The one write this plugin makes: a short read cache. See the file header.
	set_transient( SF_SEO_CACHE_KEY, $index, SF_SEO_CACHE_TTL );

	$index['from_cache'] = false;
	return $index;
}

/**
 * The site-wide settings that change every page at once.
 *
 * @return array
 */
function sf_seo_site_info() {
	$features = array();
	foreach ( SF_SEO_SLIM_FEATURES as $feature ) {
		$features[ $feature ] = sf_seo_feature_active( $feature );
	}
	$slim = sf_seo_slim();

	return array(
		'home_url'                   => home_url( '/' ),
		'search_engines_discouraged' => '0' === (string) get_option( 'blog_public' ),
		'front_page_id'              => 'page' === get_option( 'show_on_front' ) ? (int) get_option( 'page_on_front' ) : null,
		'post_types'                 => sf_seo_post_types(),
		'slim_seo'                   => array(
			'active'                 => $slim['active'],
			'version'                => $slim['version'],
			'renderer_available'     => null !== $slim['title'] && null !== $slim['description'],
			'features'               => $features,
			'other_slim_seo_plugins' => sf_seo_active_plugins( array( 'slim-seo-' ) ),
		),
		'bricks_active'              => defined( 'BRICKS_VERSION' ),
		'woocommerce_active'         => class_exists( 'WooCommerce' ),
		'plugin_version'             => SF_SEO_ABILITIES_VERSION,
	);
}

/**
 * Active plugin files whose folder starts with one of the prefixes.
 *
 * @param string[] $prefixes Folder prefixes, e.g. 'slim-seo-'.
 * @return string[]
 */
function sf_seo_active_plugins( array $prefixes ) {
	$active = (array) get_option( 'active_plugins', array() );
	if ( is_multisite() ) {
		$active = array_merge( $active, array_keys( (array) get_site_option( 'active_sitewide_plugins', array() ) ) );
	}
	$out = array();
	foreach ( $active as $file ) {
		foreach ( $prefixes as $prefix ) {
			if ( is_string( $file ) && 0 === strpos( $file, $prefix ) ) {
				$out[] = $file;
			}
		}
	}
	return array_values( array_unique( $out ) );
}

/**
 * Plain-language caveats that apply to this response.
 *
 * @param array $index The audit index.
 * @return string[]
 */
function sf_seo_notes( array $index ) {
	$notes = array();
	$site  = sf_seo_site_info();
	if ( $site['search_engines_discouraged'] ) {
		$notes[] = 'Settings > Reading > "Discourage search engines from indexing this site" is on, so WordPress asks search engines not to index ANY page, whatever each page\'s own noindex says. Expected on WP Engine staging; on production it hides the whole site.';
	}
	if ( ! $site['slim_seo']['active'] ) {
		$notes[] = 'Slim SEO is not active. Stored values are empty and effective values cannot be computed.';
	} elseif ( ! $site['slim_seo']['renderer_available'] ) {
		$notes[] = 'Slim SEO\'s renderer could not be used (an update may have renamed it), so "effective" is null and lengths measure the stored values.';
	}
	foreach ( array( 'meta_title', 'meta_description', 'meta_robots', 'canonical_url' ) as $feature ) {
		if ( false === $site['slim_seo']['features'][ $feature ] ) {
			$notes[] = 'Slim SEO\'s "' . $feature . '" feature is switched off, so the page does not output what this audit calls effective for it.';
		}
	}
	if ( ! empty( $index['truncated'] ) ) {
		$notes[] = 'Only the first ' . SF_SEO_MAX_ITEMS . ' published items (lowest IDs) were audited.';
	}
	return $notes;
}

/* ---------------------------------------------------------------------------
 * The abilities
 * ------------------------------------------------------------------------- */

/**
 * A boolean input, accepting the strings a REST GET delivers ("true", "1").
 *
 * @param array  $input Input.
 * @param string $key   Key.
 * @return bool
 */
function sf_seo_input_bool( array $input, $key ) {
	return isset( $input[ $key ] ) && rest_sanitize_boolean( $input[ $key ] );
}

/**
 * sapphire-seo/audit-content.
 *
 * @param array|null $input See sf_seo_audit_input_schema().
 * @return array
 */
function sf_seo_audit_content( $input = null ) {
	$input = is_array( $input ) ? $input : array();

	$post_type = isset( $input['post_type'] ) ? sanitize_key( $input['post_type'] ) : 'any';
	if ( 'any' !== $post_type && ! in_array( $post_type, array( 'page', 'post', 'product' ), true ) ) {
		$post_type = 'any';
	}
	$flag = isset( $input['flag'] ) ? sanitize_key( $input['flag'] ) : '';
	if ( ! in_array( $flag, SF_SEO_Rules::FLAGS, true ) ) {
		$flag = '';
	}
	$flagged_only = sf_seo_input_bool( $input, 'flagged_only' );

	$index   = sf_seo_index( sf_seo_input_bool( $input, 'refresh' ) );
	$matches = SF_SEO_Rules::filter( array_values( $index['items'] ), $post_type, $flagged_only, $flag );
	$paging  = SF_SEO_Rules::paging(
		isset( $input['page'] ) ? absint( $input['page'] ) : 1,
		isset( $input['per_page'] ) ? absint( $input['per_page'] ) : SF_SEO_Rules::PER_PAGE_DEFAULT,
		count( $matches )
	);

	$notes = sf_seo_notes( $index );
	if ( 'product' === $post_type && ! post_type_exists( 'product' ) ) {
		$notes[] = 'WooCommerce is not active, so there are no products to audit.';
	}

	return array(
		'generated_at' => $index['built_at'],
		'from_cache'   => (bool) $index['from_cache'],
		'filters'      => array(
			'post_type'    => $post_type,
			'flagged_only' => $flagged_only,
			'flag'         => '' !== $flag ? $flag : null,
		),
		'page'         => $paging['page'],
		'per_page'     => $paging['per_page'],
		'total'        => $paging['total'],
		'total_pages'  => $paging['total_pages'],
		'thresholds'   => SF_SEO_Rules::thresholds(),
		'items'        => array_slice( $matches, $paging['offset'], $paging['per_page'] ),
		'notes'        => $notes,
	);
}

/**
 * The published item an ID or a URL names, or why not.
 *
 * A URL must be on this site. The page it resolves to is fetched only by its
 * own permalink (see sf_seo_live_check()), never by the URL as given.
 *
 * @param array $input Input with `id` or `url`.
 * @return WP_Post|WP_Error
 */
function sf_seo_resolve_post( array $input ) {
	$id  = isset( $input['id'] ) ? absint( $input['id'] ) : 0;
	$url = isset( $input['url'] ) && is_string( $input['url'] ) ? esc_url_raw( trim( $input['url'] ) ) : '';

	if ( ! $id && '' === $url ) {
		return new WP_Error( 'sf_seo_missing_target', 'Give the page\'s id or its url.' );
	}

	if ( ! $id ) {
		$parts = wp_parse_url( $url );
		$home  = wp_parse_url( home_url( '/' ) );
		if ( ! is_array( $parts ) ) {
			return new WP_Error( 'sf_seo_bad_url', 'That URL could not be read.' );
		}
		if ( ! empty( $parts['host'] ) && isset( $home['host'] ) && strtolower( $parts['host'] ) !== strtolower( $home['host'] ) ) {
			return new WP_Error( 'sf_seo_foreign_url', sprintf( 'That URL is not on this site (%s).', $home['host'] ) );
		}
		$home_path = isset( $home['path'] ) ? untrailingslashit( $home['path'] ) : '';
		$path      = isset( $parts['path'] ) ? untrailingslashit( $parts['path'] ) : '';
		if ( $path === $home_path && empty( $parts['query'] ) && 'page' === get_option( 'show_on_front' ) ) {
			$id = (int) get_option( 'page_on_front' );
		} else {
			$id = url_to_postid( $url );
		}
	}

	$post = $id ? get_post( $id ) : null;
	if ( ! $post instanceof WP_Post ) {
		return new WP_Error( 'sf_seo_not_found', 'No page, post or product matches that id or url.' );
	}
	if ( 'publish' !== $post->post_status || ! in_array( $post->post_type, sf_seo_post_types(), true ) ) {
		return new WP_Error( 'sf_seo_not_audited', 'Only published pages, posts and products are audited.' );
	}
	if ( ! current_user_can( 'read_post', $post->ID ) ) {
		return new WP_Error( 'sf_seo_forbidden', 'You cannot read that item.' );
	}
	return $post;
}

/**
 * The images a page uses, with the ones missing alt text.
 *
 * Sources: the featured image, images uploaded to the page, a product's
 * gallery, `wp-image-N` images in the post content, and images referenced in
 * the page's own Bricks data. Images that only a Bricks template adds (header,
 * footer, single-post template) are not the page's and are not counted.
 *
 * @param WP_Post $post The page.
 * @return array
 */
function sf_seo_images( WP_Post $post ) {
	$sources = array();
	$add     = function ( $id, $source ) use ( &$sources ) {
		$id = (int) $id;
		if ( $id > 0 ) {
			$sources[ $id ][ $source ] = true;
		}
	};

	$add( get_post_thumbnail_id( $post ), 'featured' );

	$attached = get_posts(
		array(
			'post_type'      => 'attachment',
			'post_status'    => 'inherit',
			'post_parent'    => $post->ID,
			'post_mime_type' => 'image',
			'posts_per_page' => 500,
			'fields'         => 'ids',
			'no_found_rows'  => true,
		)
	);
	foreach ( $attached as $id ) {
		$add( $id, 'attached' );
	}

	if ( 'product' === $post->post_type && function_exists( 'wc_get_product' ) ) {
		$product = wc_get_product( $post->ID );
		if ( $product && is_callable( array( $product, 'get_gallery_image_ids' ) ) ) {
			foreach ( (array) $product->get_gallery_image_ids() as $id ) {
				$add( $id, 'product_gallery' );
			}
		}
	}

	if ( preg_match_all( '/\bwp-image-(\d+)\b/', (string) $post->post_content, $matches ) ) {
		foreach ( $matches[1] as $id ) {
			$add( $id, 'content' );
		}
	}

	if ( defined( 'BRICKS_DB_PAGE_CONTENT' ) ) {
		foreach ( SF_SEO_Rules::image_ids_in_builder_data( get_post_meta( $post->ID, BRICKS_DB_PAGE_CONTENT, true ) ) as $id ) {
			$add( $id, 'bricks' );
		}
	}

	$checked = 0;
	$missing = array();
	foreach ( $sources as $id => $found_in ) {
		if ( ! wp_attachment_is_image( $id ) ) {
			continue;
		}
		++$checked;
		$alt = trim( (string) get_post_meta( $id, '_wp_attachment_image_alt', true ) );
		if ( '' !== $alt ) {
			continue;
		}
		$missing[] = array(
			'id'         => (int) $id,
			'url'        => (string) wp_get_attachment_url( $id ),
			'file_title' => SF_SEO_Rules::clean_text( get_the_title( $id ) ),
			'found_in'   => array_keys( $found_in ),
		);
	}

	return array(
		'checked'           => $checked,
		'missing_alt_count' => count( $missing ),
		'missing_alt'       => array_slice( $missing, 0, 100 ),
		'note'              => true === sf_seo_feature_active( 'images_alt' )
			? 'Slim SEO\'s "images_alt" feature fills a missing alt from the image\'s title when the image is output through wp_get_attachment_image(), so some of these may show a title-derived alt on the page (file_title shows what it would use). A real description is still better.'
			: 'Alt text as stored in the media library. Images added only by a Bricks template are not included.',
	);
}

/**
 * Whether Slim SEO outputs schema for a page.
 *
 * The built-in schema is deterministic: when its feature is on, Slim SEO
 * 4.11's Schema\Manager outputs a graph on every page, with the types below.
 * The Slim SEO Schema add-on is not: it chooses pages by location rules,
 * which can be custom PHP ("Code" rules) and run during a real page load, so
 * it cannot be answered here without guessing. live_check answers it.
 *
 * @param WP_Post $post The page.
 * @return array
 */
function sf_seo_schema_info( WP_Post $post ) {
	$active = sf_seo_feature_active( 'schema' );
	$types  = array( 'WebSite', 'SearchAction', 'BreadcrumbList', 'WebPage', 'Organization' );
	if ( in_array( $post->post_type, array( 'post', 'page' ), true ) ) {
		$types[] = 'ReadAction';
	}
	if ( 'post' === $post->post_type ) {
		$types[] = 'Article';
		$types[] = 'Person';
	}
	$addons = sf_seo_active_plugins( array( 'slim-seo-schema/', 'slim-seo-pro/' ) );

	return array(
		'slim_seo_builtin' => array(
			'active' => $active,
			'types'  => false === $active ? array() : $types,
			'note'   => 'Per Slim SEO 4.11 Schema\\Manager. ImageObject is added for the site logo and for a page with images; other plugins can change the graph through slim_seo_schema_graph.',
		),
		'schema_addon'     => array(
			'active_plugins' => $addons,
			'outputs_here'   => null,
			'note'           => $addons
				? 'The Schema add-on chooses pages by location rules (Singular, Archive, or custom PHP "Code" rules) evaluated during a real page load, so whether it outputs here cannot be determined without guessing. Run with live_check: true to read the JSON-LD the page actually serves.'
				: 'No Slim SEO Schema or Slim SEO Pro plugin is active.',
		),
	);
}

/**
 * Renders a page's own Bricks data with Bricks' renderer, as Slim SEO does for
 * the automatic description. Global $post points at the page while it runs,
 * so dynamic data such as {post_title} resolves to it, and is put back after.
 *
 * @param WP_Post $target The page.
 * @param array   $data   Its Bricks data.
 * @return string|null Markup, or null if Bricks failed.
 */
function sf_seo_render_bricks( WP_Post $target, array $data ) {
	$previous        = isset( $GLOBALS['post'] ) ? $GLOBALS['post'] : null;
	$GLOBALS['post'] = $target; // phpcs:ignore WordPress.WP.GlobalVariablesOverride.Prohibited -- restored below.
	ob_start();
	try {
		$html = \Bricks\Frontend::render_data( $data );
	} catch ( \Throwable $e ) {
		$html = null;
	}
	$echoed          = (string) ob_get_clean();
	$GLOBALS['post'] = $previous; // phpcs:ignore WordPress.WP.GlobalVariablesOverride.Prohibited
	return is_string( $html ) ? $html . $echoed : null;
}

/**
 * A page's headings, from its own content.
 *
 * Bricks pages are rendered with Bricks' own renderer, so every heading tag
 * is the one Bricks writes, defaults included: nothing is inferred from the
 * raw element settings. What this cannot see is said in `note`, and
 * live_check reads the page as served when that matters.
 *
 * @param WP_Post $post The page.
 * @return array
 */
function sf_seo_headings( WP_Post $post ) {
	$html   = null;
	$source = null;

	if ( defined( 'BRICKS_DB_PAGE_CONTENT' ) && is_callable( array( 'Bricks\Frontend', 'render_data' ) ) ) {
		$data = get_post_meta( $post->ID, BRICKS_DB_PAGE_CONTENT, true );
		if ( is_array( $data ) && $data ) {
			$html   = sf_seo_render_bricks( $post, $data );
			$source = null !== $html ? 'bricks' : null;
		}
	}
	if ( null === $html && '' !== trim( (string) $post->post_content ) ) {
		$html   = do_blocks( (string) $post->post_content );
		$source = 'post_content';
	}

	if ( null === $html ) {
		return array(
			'available' => false,
			'source'    => null,
			'items'     => array(),
			'h1_count'  => 0,
			'note'      => 'This page has no Bricks data and no post content to read headings from, or Bricks failed to render it. Use live_check to read the page as served.',
		);
	}

	$items    = SF_SEO_Rules::headings( $html );
	$h1_count = 0;
	foreach ( $items as $heading ) {
		$h1_count += 1 === $heading['level'] ? 1 : 0;
	}

	if ( 'bricks' === $source ) {
		$note = 'Rendered with Bricks\' own renderer from this page\'s Bricks data. Not included: headings that Bricks templates add around the page (header, footer, a single post or product template). Dynamic data that depends on the front-end query may render differently. live_check reads the page as served.';
	} elseif ( 'product' === $post->post_type ) {
		$note = 'From the product description only. The product page\'s own headings (its name, tabs) come from its template. live_check reads the page as served.';
	} else {
		$note = 'From the post content, rendered as blocks. Headings from the theme or Bricks templates are not included. live_check reads the page as served.';
	}

	return array(
		'available' => true,
		'source'    => $source,
		'items'     => array_slice( $items, 0, 200 ),
		'h1_count'  => $h1_count,
		'note'      => $note,
	);
}

/**
 * Fetches the page as a visitor gets it and reads its SEO tags, headings and
 * JSON-LD. A GET of the page's own permalink, nothing else: the URL is never
 * taken from the caller. If Cloudflare challenges the request, that is
 * reported rather than retried.
 *
 * @param WP_Post $post The page.
 * @return array
 */
function sf_seo_live_check( WP_Post $post ) {
	$url  = (string) get_permalink( $post );
	$base = array(
		'url'          => $url,
		'fetched'      => false,
		'status'       => null,
		'error'        => null,
		'head'         => null,
		'headings'     => array(),
		'h1_count'     => 0,
		'json_ld'      => array(),
		'schema_types' => array(),
	);

	if ( '' !== (string) $post->post_password ) {
		$base['error'] = 'Password-protected: a visitor sees the password form, not the page.';
		return $base;
	}

	$response = wp_safe_remote_get(
		$url,
		array(
			'timeout'             => 15,
			'redirection'         => 3,
			'limit_response_size' => 3 * MB_IN_BYTES,
			'user-agent'          => 'Mozilla/5.0 (compatible; SapphireSEOAudit/' . SF_SEO_ABILITIES_VERSION . '; +' . home_url( '/' ) . ')',
			'headers'             => array( 'Accept' => 'text/html' ),
		)
	);
	if ( is_wp_error( $response ) ) {
		$base['error'] = $response->get_error_message();
		return $base;
	}

	$base['status'] = (int) wp_remote_retrieve_response_code( $response );
	if ( 200 !== $base['status'] ) {
		$mitigated     = (string) wp_remote_retrieve_header( $response, 'cf-mitigated' );
		$base['error'] = '' !== $mitigated
			? 'Cloudflare answered with a "' . $mitigated . '" instead of the page. See "If Cloudflare blocks it" in the install guide.'
			: 'The page answered HTTP ' . $base['status'] . '.';
		return $base;
	}

	$html     = (string) wp_remote_retrieve_body( $response );
	$headings = SF_SEO_Rules::headings( $html );
	$json_ld  = SF_SEO_Rules::json_ld_blocks( $html );
	$types    = array();
	foreach ( $json_ld as $block ) {
		$types = array_merge( $types, $block['types'] );
	}
	$h1_count = 0;
	foreach ( $headings as $heading ) {
		$h1_count += 1 === $heading['level'] ? 1 : 0;
	}

	$base['fetched']      = true;
	$base['head']         = SF_SEO_Rules::head_tags( $html );
	$base['headings']     = array_slice( $headings, 0, 200 );
	$base['h1_count']     = $h1_count;
	$base['json_ld']      = $json_ld;
	$base['schema_types'] = array_values( array_unique( $types ) );
	return $base;
}

/**
 * sapphire-seo/page-detail.
 *
 * The record is computed fresh for this page; duplicates are judged against
 * the (possibly cached) audit of the rest of the site.
 *
 * @param array|null $input See sf_seo_detail_input_schema().
 * @return array|WP_Error
 */
function sf_seo_page_detail( $input = null ) {
	$input = is_array( $input ) ? $input : array();
	$post  = sf_seo_resolve_post( $input );
	if ( is_wp_error( $post ) ) {
		return $post;
	}

	$index                = sf_seo_index( false );
	$records              = $index['items'];
	$records[ $post->ID ] = sf_seo_record( $post );
	$records              = SF_SEO_Rules::apply_flags( $records );
	$record               = $records[ $post->ID ];

	$record['images']     = sf_seo_images( $post );
	$record['schema']     = sf_seo_schema_info( $post );
	$record['headings']   = sf_seo_headings( $post );
	$record['live_check'] = sf_seo_input_bool( $input, 'live_check' ) ? sf_seo_live_check( $post ) : null;

	return array(
		'generated_at' => gmdate( 'c' ),
		'thresholds'   => SF_SEO_Rules::thresholds(),
		'item'         => $record,
		'notes'        => sf_seo_notes( $index ),
	);
}

/**
 * sapphire-seo/site-summary.
 *
 * @param array|null $input See sf_seo_summary_input_schema().
 * @return array
 */
function sf_seo_site_summary( $input = null ) {
	$input   = is_array( $input ) ? $input : array();
	$index   = sf_seo_index( sf_seo_input_bool( $input, 'refresh' ) );
	$summary = SF_SEO_Rules::summarize( array_values( $index['items'] ) );

	$examples = array();
	foreach ( $summary['examples'] as $flag => $ids ) {
		$examples[ $flag ] = array();
		foreach ( $ids as $id ) {
			$item                = $index['items'][ $id ];
			$examples[ $flag ][] = array(
				'id'    => $item['id'],
				'type'  => $item['type'],
				'title' => $item['post_title'],
				'url'   => $item['url'],
			);
		}
	}

	return array(
		'generated_at' => $index['built_at'],
		'from_cache'   => (bool) $index['from_cache'],
		'site'         => sf_seo_site_info(),
		'thresholds'   => SF_SEO_Rules::thresholds(),
		'flag_rules'   => SF_SEO_Rules::flag_rules(),
		'total'        => $summary['total'],
		'flagged'      => $summary['flagged'],
		'by_flag'      => $summary['by_flag'],
		'by_type'      => $summary['by_type'],
		'examples'     => $examples,
		'notes'        => sf_seo_notes( $index ),
	);
}

/* ---------------------------------------------------------------------------
 * Schemas
 *
 * Core validates input against these before the permission check, and output
 * after the callback: an output that does not match fails the call. Every
 * node carries a `type`, which core's validator requires.
 * ------------------------------------------------------------------------- */

function sf_seo_schema_nullable_string( $description ) {
	return array(
		'type'        => array( 'string', 'null' ),
		'description' => $description,
	);
}

function sf_seo_schema_int_list( $description ) {
	return array(
		'type'        => 'array',
		'items'       => array( 'type' => 'integer' ),
		'description' => $description,
	);
}

function sf_seo_schema_counts( $description ) {
	return array(
		'type'                 => 'object',
		'description'          => $description,
		'additionalProperties' => array( 'type' => 'integer' ),
	);
}

function sf_seo_schema_thresholds() {
	$range = array(
		'type'       => 'object',
		'properties' => array(
			'min'    => array( 'type' => 'integer' ),
			'max'    => array( 'type' => 'integer' ),
			'source' => array( 'type' => 'string' ),
		),
	);
	return array(
		'type'        => 'object',
		'description' => 'Length thresholds in characters.',
		'properties'  => array(
			'title'       => $range,
			'description' => $range,
		),
	);
}

function sf_seo_schema_text_field( $what ) {
	return array(
		'type'       => 'object',
		'properties' => array(
			'stored'    => array(
				'type'        => 'string',
				'description' => 'The ' . $what . ' saved in Slim SEO for this item; empty when none was written.',
			),
			'effective' => sf_seo_schema_nullable_string( 'The ' . $what . ' the page outputs, rendered by Slim SEO. Null when Slim SEO\'s renderer is unavailable.' ),
			'length'    => array(
				'type'        => 'integer',
				'description' => 'Characters in the effective ' . $what . ' (the stored one when effective is null).',
			),
			'source'    => array(
				'type'        => 'string',
				'description' => 'Where the effective ' . $what . ' comes from: post, post_type_template, slim_seo_default / auto, site_default (static front page) or password_protected.',
			),
		),
	);
}

function sf_seo_schema_record() {
	return array(
		'type'       => 'object',
		'properties' => array(
			'id'                       => array( 'type' => 'integer' ),
			'type'                     => array(
				'type'        => 'string',
				'description' => 'Post type: page, post or product.',
			),
			'url'                      => array( 'type' => 'string' ),
			'post_title'               => array( 'type' => 'string' ),
			'modified'                 => array(
				'type'        => 'string',
				'description' => 'Last modified, UTC, ISO 8601.',
			),
			'title'                    => sf_seo_schema_text_field( 'title' ),
			'description'              => sf_seo_schema_text_field( 'description' ),
			'noindex'                  => array(
				'type'        => 'boolean',
				'description' => 'Noindexed by Slim SEO (its own setting or its post type\'s). Does not include the site-wide "Discourage search engines" setting; see notes.',
			),
			'noindex_source'           => sf_seo_schema_nullable_string( 'post, post_type, post_and_post_type, or null.' ),
			'canonical'                => array(
				'type'       => 'object',
				'properties' => array(
					'stored'           => array(
						'type'        => 'string',
						'description' => 'Canonical saved in Slim SEO; empty means the page\'s own URL.',
					),
					'effective'        => array( 'type' => 'string' ),
					'points_elsewhere' => array( 'type' => 'boolean' ),
				),
			),
			'flags'                    => array(
				'type'  => 'array',
				'items' => array(
					'type' => 'string',
					'enum' => SF_SEO_Rules::FLAGS,
				),
			),
			'duplicate_title_of'       => sf_seo_schema_int_list( 'IDs of other indexable items that output the same title.' ),
			'duplicate_description_of' => sf_seo_schema_int_list( 'IDs of other indexable items that output the same description.' ),
		),
	);
}

function sf_seo_schema_notes() {
	return array(
		'type'        => 'array',
		'items'       => array( 'type' => 'string' ),
		'description' => 'Caveats that apply to this response. Read them.',
	);
}

function sf_seo_schema_headings() {
	return array(
		'type'  => 'array',
		'items' => array(
			'type'       => 'object',
			'properties' => array(
				'level' => array( 'type' => 'integer' ),
				'text'  => array( 'type' => 'string' ),
			),
		),
	);
}

function sf_seo_audit_input_schema() {
	return array(
		'type'                 => array( 'object', 'null' ),
		'properties'           => array(
			'post_type'    => array(
				'type'        => 'string',
				'enum'        => array( 'any', 'page', 'post', 'product' ),
				'default'     => 'any',
				'description' => 'Only this post type. Default any.',
			),
			'flagged_only' => array(
				'type'        => 'boolean',
				'default'     => false,
				'description' => 'Only items with at least one flag.',
			),
			'flag'         => array(
				'type'        => 'string',
				'enum'        => SF_SEO_Rules::FLAGS,
				'description' => 'Only items carrying this flag.',
			),
			'page'         => array(
				'type'        => 'integer',
				'minimum'     => 1,
				'default'     => 1,
				'description' => 'Page number, from 1.',
			),
			'per_page'     => array(
				'type'        => 'integer',
				'minimum'     => 1,
				'maximum'     => SF_SEO_Rules::PER_PAGE_MAX,
				'default'     => SF_SEO_Rules::PER_PAGE_DEFAULT,
				'description' => 'Items per page, at most ' . SF_SEO_Rules::PER_PAGE_MAX . '.',
			),
			'refresh'      => array(
				'type'        => 'boolean',
				'default'     => false,
				'description' => 'Rebuild the audit instead of using the five-minute cache. It already rebuilds whenever content changes.',
			),
		),
		'additionalProperties' => false,
	);
}

function sf_seo_audit_output_schema() {
	return array(
		'type'       => 'object',
		'properties' => array(
			'generated_at' => array( 'type' => 'string' ),
			'from_cache'   => array( 'type' => 'boolean' ),
			'filters'      => array(
				'type'       => 'object',
				'properties' => array(
					'post_type'    => array( 'type' => 'string' ),
					'flagged_only' => array( 'type' => 'boolean' ),
					'flag'         => sf_seo_schema_nullable_string( 'The flag filtered on, if any.' ),
				),
			),
			'page'         => array( 'type' => 'integer' ),
			'per_page'     => array( 'type' => 'integer' ),
			'total'        => array( 'type' => 'integer' ),
			'total_pages'  => array( 'type' => 'integer' ),
			'thresholds'   => sf_seo_schema_thresholds(),
			'items'        => array(
				'type'  => 'array',
				'items' => sf_seo_schema_record(),
			),
			'notes'        => sf_seo_schema_notes(),
		),
	);
}

function sf_seo_detail_input_schema() {
	return array(
		'type'                 => 'object',
		'properties'           => array(
			'id'         => array(
				'type'        => 'integer',
				'minimum'     => 1,
				'description' => 'Post ID. Takes precedence over url.',
			),
			'url'        => array(
				'type'        => 'string',
				'maxLength'   => 2048,
				'description' => 'The page\'s URL on this site, absolute or starting with /.',
			),
			'live_check' => array(
				'type'        => 'boolean',
				'default'     => false,
				'description' => 'Also fetch the page as served and read its title, description, canonical, robots, headings and JSON-LD. Slower; may be challenged by Cloudflare.',
			),
		),
		'additionalProperties' => false,
	);
}

function sf_seo_detail_output_schema() {
	$record = sf_seo_schema_record();

	$record['properties']['images'] = array(
		'type'       => 'object',
		'properties' => array(
			'checked'           => array( 'type' => 'integer' ),
			'missing_alt_count' => array( 'type' => 'integer' ),
			'missing_alt'       => array(
				'type'  => 'array',
				'items' => array(
					'type'       => 'object',
					'properties' => array(
						'id'         => array( 'type' => 'integer' ),
						'url'        => array( 'type' => 'string' ),
						'file_title' => array( 'type' => 'string' ),
						'found_in'   => array(
							'type'  => 'array',
							'items' => array( 'type' => 'string' ),
						),
					),
				),
			),
			'note'              => array( 'type' => 'string' ),
		),
	);

	$record['properties']['schema'] = array(
		'type'       => 'object',
		'properties' => array(
			'slim_seo_builtin' => array(
				'type'       => 'object',
				'properties' => array(
					'active' => array( 'type' => array( 'boolean', 'null' ) ),
					'types'  => array(
						'type'  => 'array',
						'items' => array( 'type' => 'string' ),
					),
					'note'   => array( 'type' => 'string' ),
				),
			),
			'schema_addon'     => array(
				'type'       => 'object',
				'properties' => array(
					'active_plugins' => array(
						'type'  => 'array',
						'items' => array( 'type' => 'string' ),
					),
					'outputs_here'   => array(
						'type'        => array( 'boolean', 'null' ),
						'description' => 'Always null: see note.',
					),
					'note'           => array( 'type' => 'string' ),
				),
			),
		),
	);

	$record['properties']['headings'] = array(
		'type'       => 'object',
		'properties' => array(
			'available' => array( 'type' => 'boolean' ),
			'source'    => sf_seo_schema_nullable_string( 'bricks, post_content, or null.' ),
			'items'     => sf_seo_schema_headings(),
			'h1_count'  => array( 'type' => 'integer' ),
			'note'      => array( 'type' => 'string' ),
		),
	);

	$record['properties']['live_check'] = array(
		'type'        => array( 'object', 'null' ),
		'description' => 'Null unless live_check was requested.',
		'properties'  => array(
			'url'          => array( 'type' => 'string' ),
			'fetched'      => array( 'type' => 'boolean' ),
			'status'       => array( 'type' => array( 'integer', 'null' ) ),
			'error'        => sf_seo_schema_nullable_string( 'Why the page could not be read.' ),
			'head'         => array(
				'type'       => array( 'object', 'null' ),
				'properties' => array(
					'title'       => sf_seo_schema_nullable_string( 'The <title> served.' ),
					'description' => sf_seo_schema_nullable_string( 'The meta description served.' ),
					'canonical'   => sf_seo_schema_nullable_string( 'The canonical link served.' ),
					'robots'      => sf_seo_schema_nullable_string( 'The meta robots served.' ),
					'noindex'     => array( 'type' => 'boolean' ),
				),
			),
			'headings'     => sf_seo_schema_headings(),
			'h1_count'     => array( 'type' => 'integer' ),
			'json_ld'      => array(
				'type'  => 'array',
				'items' => array(
					'type'       => 'object',
					'properties' => array(
						'id'    => sf_seo_schema_nullable_string( 'The script tag\'s id, e.g. slim-seo-schema.' ),
						'valid' => array( 'type' => 'boolean' ),
						'types' => array(
							'type'  => 'array',
							'items' => array( 'type' => 'string' ),
						),
					),
				),
			),
			'schema_types' => array(
				'type'  => 'array',
				'items' => array( 'type' => 'string' ),
			),
		),
	);

	return array(
		'type'       => 'object',
		'properties' => array(
			'generated_at' => array( 'type' => 'string' ),
			'thresholds'   => sf_seo_schema_thresholds(),
			'item'         => $record,
			'notes'        => sf_seo_schema_notes(),
		),
	);
}

function sf_seo_summary_input_schema() {
	return array(
		'type'                 => array( 'object', 'null' ),
		'properties'           => array(
			'refresh' => array(
				'type'        => 'boolean',
				'default'     => false,
				'description' => 'Rebuild the audit instead of using the five-minute cache. It already rebuilds whenever content changes.',
			),
		),
		'additionalProperties' => false,
	);
}

function sf_seo_summary_output_schema() {
	$example = array(
		'type'  => 'array',
		'items' => array(
			'type'       => 'object',
			'properties' => array(
				'id'    => array( 'type' => 'integer' ),
				'type'  => array( 'type' => 'string' ),
				'title' => array( 'type' => 'string' ),
				'url'   => array( 'type' => 'string' ),
			),
		),
	);

	return array(
		'type'       => 'object',
		'properties' => array(
			'generated_at' => array( 'type' => 'string' ),
			'from_cache'   => array( 'type' => 'boolean' ),
			'site'         => array(
				'type'       => 'object',
				'properties' => array(
					'home_url'                   => array( 'type' => 'string' ),
					'search_engines_discouraged' => array( 'type' => 'boolean' ),
					'front_page_id'              => array( 'type' => array( 'integer', 'null' ) ),
					'post_types'                 => array(
						'type'  => 'array',
						'items' => array( 'type' => 'string' ),
					),
					'slim_seo'                   => array(
						'type'       => 'object',
						'properties' => array(
							'active'                 => array( 'type' => 'boolean' ),
							'version'                => sf_seo_schema_nullable_string( 'Slim SEO version.' ),
							'renderer_available'     => array( 'type' => 'boolean' ),
							'features'               => array(
								'type'                 => 'object',
								'description'          => 'Slim SEO feature switches; null when they could not be read.',
								'additionalProperties' => array( 'type' => array( 'boolean', 'null' ) ),
							),
							'other_slim_seo_plugins' => array(
								'type'  => 'array',
								'items' => array( 'type' => 'string' ),
							),
						),
					),
					'bricks_active'              => array( 'type' => 'boolean' ),
					'woocommerce_active'         => array( 'type' => 'boolean' ),
					'plugin_version'             => array( 'type' => 'string' ),
				),
			),
			'thresholds'   => sf_seo_schema_thresholds(),
			'flag_rules'   => array(
				'type'                 => 'object',
				'additionalProperties' => array( 'type' => 'string' ),
			),
			'total'        => array( 'type' => 'integer' ),
			'flagged'      => array( 'type' => 'integer' ),
			'by_flag'      => sf_seo_schema_counts( 'Items carrying each flag.' ),
			'by_type'      => array(
				'type'                 => 'object',
				'description'          => 'Per post type: total, flagged and by_flag.',
				'additionalProperties' => array(
					'type'       => 'object',
					'properties' => array(
						'total'   => array( 'type' => 'integer' ),
						'flagged' => array( 'type' => 'integer' ),
						'by_flag' => sf_seo_schema_counts( 'Items of this type carrying each flag.' ),
					),
				),
			),
			'examples'     => array(
				'type'                 => 'object',
				'description'          => 'Up to ' . SF_SEO_Rules::EXAMPLES_PER_FLAG . ' items per flag.',
				'additionalProperties' => $example,
			),
			'notes'        => sf_seo_schema_notes(),
		),
	);
}
