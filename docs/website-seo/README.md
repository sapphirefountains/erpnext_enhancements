# Website SEO: read-only audit abilities for AI

This is phase 1 of AI-assisted SEO for `www.sapphirefountains.com`, agreed with Nik on
2026-10-27. In this phase an AI client can **audit** the site's SEO over MCP and cannot change
anything through these abilities. Phase 2 lets the AI propose changes, which a Marketing Manager
approves in wp-admin, and nobody approves their own change. Phase 3 adds a Search Console tool to
ERPNext's MCP.

Nothing here is Frappe code. It installs on **WordPress (WP Engine, behind Cloudflare)**, beside
the attribution mu-plugin in [`../website-capture/`](../website-capture/). We keep **Slim SEO**,
which avoids migrating titles, descriptions, schema and redirects to another SEO plugin.

| File | What it is |
|---|---|
| `sf-seo-abilities.php` | mu-plugin: three read-only abilities for the WordPress Abilities API and MCP Adapter |
| [`claude-connection.md`](claude-connection.md) | Connecting Claude Code to the site as the `seo-agent` user, first prompts, revoking access |

`sf-seo-abilities.php` is tested in this repo's CI (`scripts/test_sf_seo_abilities.php`) even
though it never runs here. Edit it in the repo, not on WP Engine, or the next upload overwrites
the fix.

---

## Read this first: the AI's user must be a Contributor, not an Editor

**As an Editor, `seo-agent` can already change SEO settings on production.** These abilities are
read-only, but they are not the only ones on the MCP server. Slim SEO 4.11 registers **its own**
abilities and lists them there, including `slim-seo/update-post-meta-tags` and
`slim-seo/update-term-meta-tags`. They check `edit_post` and `edit_term`, which an Editor passes. So
anything connected as an Editor can rewrite any page's title, description, canonical or noindex on
the live site, with no approval step. That would undercut phase 2's approval rule before phase 2
exists.

This was checked on 2026-10-27 in two places: the live server's ability list (read with
`mcp-adapter-discover-abilities`) and Slim SEO 4.11.4's source
(`src/MetaTags/Abilities/Post.php` and `Term.php`, `check_permission()`).

**The fix is least privilege: make `seo-agent` a Contributor.** These abilities need only
`edit_posts` (to run them) and `read` (for the MCP Adapter), and a Contributor has both. A
Contributor fails every Slim SEO write check:

- `edit_post` on someone else's published post needs `edit_others_posts` and
  `edit_published_posts`;
- pages need `edit_pages`, and products need WooCommerce's product capabilities;
- `edit_term` needs `manage_categories`, or `edit_product_terms` for product categories.

Slim SEO's own read abilities make the same check, so a Contributor can't use those either. Ours
cover what they would show.

**The application password is not limited to MCP.** It signs in to the whole REST API as
`seo-agent`. Whatever the role can do in wp-admin, a script holding the password can do. That is the
real reason the role matters, and a Contributor can at most save drafts of its own.

If `seo-agent` is already an Editor, as it was on 2026-10-27, change it:
**Users → seo-agent → Role → Contributor → Update User.** Its application passwords keep working.
[`claude-connection.md`](claude-connection.md#1-the-seo-agent-user) has the same step.

A second line of defence is still open, and it is Nik's call: hide Slim SEO's `update-*` abilities
from MCP altogether. Core's `wp_register_ability_args` filter can set their `meta.mcp.public` to
`false` (MCP Adapter `McpAbilityExposure`). That matters for anyone who connects an AI as a real
Editor. It belongs in this mu-plugin, with a test.

---

## What the abilities do

All three are in the `sapphire-seo` category. They are:

- read-only: annotated `readonly: true`, `destructive: false`;
- allowed for any user with `edit_posts`: Contributor, Author, Editor, Shop Manager and
  Administrator;
- listed on the MCP Adapter's default server (`meta.mcp.public = true`).

| Ability | Use it for |
|---|---|
| `sapphire-seo/site-summary` | The first call. Counts of every flag, per post type and overall, up to five example pages per flag, the thresholds and the flag rules. Also the site-wide settings that change every page: Slim SEO's feature switches, and **Settings → Reading → Discourage search engines**. |
| `sapphire-seo/audit-content` | One row per published page, post and WooCommerce product. Filters: `post_type`, `flagged_only` and one `flag`. Pages: `page`, `per_page`, capped at **25**. |
| `sapphire-seo/page-detail` | One page by `id` or `url` (the apex and `www` host both work). Everything in its row, plus images missing alt text, schema, headings and an optional `live_check`. |

### Why 25 per page

A row is about 1.2 KB of JSON. Claude Code caps one MCP result at 25,000 tokens by default, and
100 rows ran past that. 25 rows stay well inside it. For the whole site, page through, or ask
`site-summary` first and then filter by one `flag`.

### Stored and effective

Every row reports the title and description twice:

- **stored** is what Slim SEO saved for that page, in post meta `slim_seo`, an array with `title`,
  `description`, `canonical`, `noindex`, `facebook_image` and `twitter_image`. Blank means no value
  was written.
- **effective** is what the page outputs. It comes from Slim SEO's own renderer
  (`SlimSEO\MetaTags\Title` and `Description`), not from a copy of its rules. So the audit agrees
  with the page.

When a value is blank, Slim SEO falls back in this order:

1. the post type's template in the `slim_seo` option;
2. its default. For a title that is `{{ post.title }} {{ sep }} {{ site.title }}`. For a static
   front page it is WordPress's own title. For a description it is the excerpt or the first 160
   characters of the content. Bricks content is included, through Slim SEO's Bricks integration.

`source` says which of these applied.

### The flags

Lengths are characters as a reader sees them: entities decoded, spaces collapsed. They are measured
on the **effective** text.

| Flag | Rule |
|---|---|
| `title_missing` | No Slim SEO title of its own. The page shows a template or the default. |
| `title_too_short` | Effective title under **30** characters. This minimum is ours; Slim SEO sets none. |
| `title_too_long` | Effective title over **60** characters. Slim SEO's own editor turns amber above 60. |
| `duplicate_title` | Another published, **indexable** item outputs the same title. Case and spacing are ignored. |
| `description_missing` | No Slim SEO description of its own. The automatic one is used. |
| `description_not_output` | No meta description is output at all. A password-protected page is one case. |
| `description_too_short` | Effective description under **50** characters. Slim SEO's editor recommends 50–160. |
| `description_too_long` | Effective description over **160** characters. |
| `duplicate_description` | Another published, indexable item outputs the same description. Password-protected pages are never compared. |
| `noindex` | Published, but noindexed: by its own Slim SEO setting, its post type's, or WooCommerce. WooCommerce deliberately noindexes its cart, checkout and account pages. `noindex_source` says which. |
| `canonical_elsewhere` | The canonical tag it **outputs** points to a different URL. Scheme and a trailing slash are ignored. Slim SEO outputs no canonical on a page it noindexes itself, or for a stored value that fails `FILTER_VALIDATE_URL` (a relative path does), so those never count, and `canonical.effective` is then `null`. WooCommerce's noindex leaves the canonical in place. |

Two rules are worth knowing:

- **Duplicates are counted among indexable pages only.** A noindexed page can't compete in search,
  and it is already flagged `noindex`.
- **"Discourage search engines" is reported once, at site level, not as a flag on every page.**
  WP Engine staging normally has it on. Every response then carries a note saying so. On
  production that note means the whole site is hidden from search.

### Password-protected pages

`read_post` is true for a published page with a password, so it can't be the guard. For such a page,
only someone who can **edit** it gets:

- its stored description;
- its headings;
- its images.

Anyone else gets `withheld: true` and nothing from behind the password. That includes `seo-agent`,
as a Contributor. `live_check` never fetches such a page at all.

Such a page is also judged only on what a visitor gets:

- Its effective description is always empty, with length 0. Slim SEO would render the real one for
  a request carrying the page's `wp-postpass_` cookie, and that must never reach the shared cache.
- Its only description flag is `description_not_output`. So nothing in its record shows whether a
  description is stored, or how long it is.

### Images, schema and headings (page detail)

- **Images missing alt text.** These sources are checked:
  - the featured image;
  - images uploaded to the page;
  - a product's gallery;
  - `wp-image-N` images in the post content;
  - images referenced in the page's own Bricks data. This is read as stored data and never
    rendered.

  Images that only a Bricks template adds, such as the header or footer, are not the page's. Slim
  SEO's `images_alt` feature fills a missing alt from the image's file title at output, so
  `file_title` shows what visitors get instead.
- **Schema.** Slim SEO's built-in schema is predictable. When the feature is on, every page gets
  WebSite, SearchAction, WebPage, BreadcrumbList and Organization. Posts and pages also get
  ReadAction, and posts also get Article and Person, the author. ImageObject is added for the site
  logo and for a page with images. The **Slim SEO Schema add-on is not predictable.** It picks
  pages by location rules, and those can be custom PHP ("Code" rules) that run during a real page
  load. So `outputs_here` is always `null` rather than a guess. `live_check` answers it.
- **Headings.** They come from Slim SEO's own content reader,
  `SlimSEO\MetaTags\Data::get_post_content()`. That is the same filtered path Slim SEO uses for its
  automatic descriptions, so it runs what Slim SEO already runs there, and nothing of this plugin's
  own:
  - For a Bricks page, that is Slim SEO's render of the page's own elements. It skips code and
    form elements, the media, query, WordPress and extras categories, query loops, components,
    popups and anything with a script.
  - Shortcodes that reach Slim SEO's normalizer do run, except those on its skip list (forms and
    the like), exactly as they do for every automatic description.
  - For any other page, it is the saved post content, which this plugin doesn't render.
  - **Page detail calls it even for a page with a description of its own.** Slim SEO's own
    description path wouldn't read that page's content, so for such a page this is a render that
    wouldn't otherwise happen.

  What this misses: headings added by Bricks templates, by elements Slim SEO skips, and by dynamic
  blocks. `note` says so in every response. If Slim SEO's reader isn't available, a Bricks page
  reports `available: false` and points to `live_check`. This plugin never renders Bricks itself.
- **`live_check: true`** fetches the page as a visitor gets it and reads:
  - the served `<title>`, meta description, canonical and robots;
  - its headings, templates included;
  - the schema.org types in every JSON-LD block.

  The request goes only to the page's own permalink, never to a URL taken from the caller. It may be
  answered from **WP Engine's page cache**, so a change made minutes ago may not show yet. If
  Cloudflare challenges it, the answer says so. See [below](#if-cloudflare-blocks-it).

### The one thing it stores

The full audit renders every published item. On 2026-10-27 that was about 110: 50 pages, 35 posts
and 24 products. The audit is kept in a **single five-minute transient**, `sf_seo_audit_index_v1`,
so that paging through it doesn't re-render the site each time.

The transient is rebuilt early whenever anything it depends on changes:

- a published item;
- any post's Slim SEO settings, including changes made through Slim SEO's own abilities, which
  don't touch `post_modified`;
- Bricks content;
- Slim SEO's options;
- WooCommerce's page assignments.

`refresh: true` forces a rebuild. Nothing else is ever written. The CI test fails the build on any
other write call in the file.

### Known limits

- **The audit is built in one request.** About 110 items take a few seconds. It has no time budget,
  so a site near the 2,000-item cap could run into WP Engine's 60-second request limit on the first
  call.
- **Anyone who may run it can force `refresh`.** That rebuilds the whole audit. Today that is only
  `seo-agent` and staff.
- **The cache check hashes every Bricks content row on each call.** That is cheap at this site's
  size.
- **`noindex` is read from Slim SEO's settings and WooCommerce's pages.** A plugin that changes
  robots through Slim SEO's `slim_seo_robots_index` filter or WordPress's `wp_robots` filter isn't
  seen. `live_check.head.robots` shows what is actually served.

---

## Install: WP Engine staging first

1. **Bring staging up to date.** In the WP Engine User Portal, copy production to the staging
   environment, so the audit has real content to read.
2. **Upload one file.** Over SFTP (the address and port are on the User Portal's SFTP page), or
   with the file manager if your plan has one, put the file here:

   ```
   wp-content/mu-plugins/sf-seo-abilities.php
   ```

   - Create `mu-plugins` if it doesn't exist. Nothing else is needed: no activation, no settings.
   - **A second copy doesn't break the site, but the wrong one may run.** WordPress loads
     must-use plugins in sorted order, and the first copy wins. The other is skipped.
     `sf-seo-abilities (1).php` sorts *before* `sf-seo-abilities.php`, because a space sorts before
     a dot. So the "(1)" copy is the one that runs. Delete the extra at once.
   - **Only 1.1.0 and later carry that protection.** An older copy beside a newer one can still
     stop every request with a fatal error. Upload only 1.1.0 or later, and delete any older copy
     before uploading.
3. **Not Code Snippets.** A snippet can be edited or switched off in wp-admin and drift from this
   repo, which is the source of truth. A must-use plugin has no Deactivate button.
4. **Make `seo-agent` a Contributor**, if it isn't one already. See
   [Read this first](#read-this-first-the-ais-user-must-be-a-contributor-not-an-editor).

WordPress needs the Abilities API, which has been in core since **6.9**. Production has it: on
2026-10-27 its ability list carried the `meta.public` field that core added in 7.1. The MCP
Adapter plugin must be active, which it is. Nothing breaks on an older WordPress: the plugin
registers on hooks that never fire there.

### Confirm it loaded

- **wp-admin → Plugins → Must-Use** lists *Sapphire Fountains — SEO Abilities (read-only)*.
- **The REST API lists the three abilities.** It needs a signed-in request. A browser tab doesn't
  count, because REST cookie auth also needs a nonce. Sign in as `seo-agent` with its application
  password ([how to make one](claude-connection.md#1-the-seo-agent-user)).
  - `-u seo-agent`, with no password after it, makes `curl` **ask** for the password. That keeps
    it out of your shell history.
  - On Windows:

  ```bash
  curl.exe -u seo-agent "https://<staging host>/wp-json/wp-abilities/v1/abilities?category=sapphire-seo"
  ```

  What comes back:

  | Response | Meaning |
  |---|---|
  | Three abilities: `sapphire-seo/audit-content`, `page-detail` and `site-summary` | It loaded. |
  | `401 {"code":"incorrect_password"}` | The application password is wrong or was revoked. |
  | `401 {"code":"rest_forbidden"}` | The request wasn't signed in. |
  | An empty list `[]` | The file isn't loading. Check the path and the file name. |
- **Run one.** Read-only abilities run with **GET**, which core enforces:

  ```bash
  curl.exe -u seo-agent "https://<staging host>/wp-json/wp-abilities/v1/abilities/sapphire-seo/site-summary/run"
  ```

  On staging, expect the "Discourage search engines" note. That is correct. A user without
  `edit_posts` gets `{"code":"rest_ability_cannot_execute"}` here, and `ability_invalid_permissions`
  over MCP.
- **From Claude**, once connected (see [`claude-connection.md`](claude-connection.md)): *"Discover
  the sapphire-seo abilities and run site-summary."*

### Then production

Do the same upload to production's `wp-content/mu-plugins/`. Then repeat the checks against
`https://www.sapphirefountains.com`.

### Update or remove

- **Update:** upload the new file over the old one. The version is in the file header and in
  `site.plugin_version` in the summary.
- **Remove:** delete `wp-content/mu-plugins/sf-seo-abilities.php`. That removes the three
  abilities and nothing else. There are no settings and no tables. The one transient expires within
  five minutes. Slim SEO's, WooCommerce's and core's abilities stay on the MCP server, because they
  aren't ours.

---

## If Cloudflare blocks it

`www` sits behind Cloudflare. On 2026-10-27 neither the MCP client nor `curl` was challenged, and
WordPress itself answered with `401 rest_forbidden` JSON to the unsigned request. If that changes,
first tell the two kinds of 403 apart:

| What comes back | Who sent it |
|---|---|
| An **HTML** page titled *Just a moment...* or *Attention Required! \| Cloudflare*, with a **Ray ID** at the bottom. Headers include `server: cloudflare` and often `cf-mitigated: challenge`. | **Cloudflare**, before WordPress saw the request |
| **JSON**, e.g. `{"code":"incorrect_password",...}`, `{"code":"rest_ability_cannot_execute",...}` | **WordPress**: a wrong password, or a user without `edit_posts` |

To see the raw answer (curl asks for the password):

```bash
curl.exe -s -i -u seo-agent "https://www.sapphirefountains.com/wp-json/mcp/mcp-adapter-default-server"
```

The same request also shows up in Cloudflare under **Security → Analytics → Events**. Its
*Service* field names the feature that blocked it.

**What to ask for.** Ask whoever manages the Cloudflare zone for a **custom rule with the Skip
action**, scoped to the two API paths:

```
(starts_with(http.request.uri.path, "/wp-json/mcp/") or starts_with(http.request.uri.path, "/wp-json/wp-abilities/"))
```

- **Skip:** *All Super Bot Fight Mode rules*, plus *All managed rules* if a managed rule fired.
- **For `live_check`:** the site fetching its own pages comes from WP Engine's address. Skip by that
  IP (`ip.src`), not by user agent, which anyone can copy.
- **This opens nothing.** WordPress still refuses every request without a valid application
  password.
- **One limit:** Cloudflare's **Bot Fight Mode**, the Free-plan toggle, **cannot be skipped by any
  rule**. If that is what fires, the choice is to turn it off or move to Super Bot Fight Mode (Pro
  and up). See Cloudflare's
  [skip options](https://developers.cloudflare.com/waf/custom-rules/skip/options/).

---

## Testing: what was and wasn't checked

- **CI** runs `php -l` on the plugin. It then runs `scripts/test_sf_seo_abilities.php` against a
  stubbed WordPress and Slim SEO (`scripts/fixtures/sf_seo_fake_wordpress.php`). It covers:
  - loading the file twice without a fatal, and that it starts with `<?php` and no BOM;
  - every flag at its boundaries;
  - each ability's contract: `edit_posts`, `mcp.public`, read-only, and a `type` on every schema
    node;
  - every callback's result against its own output schema, for an allowed caller and for denied
    ones. Core fails a call whose output doesn't match, so this matters most;
  - password-protected pages: withheld from a caller who can't edit them, with no trace of what
    is stored behind the password, even when Slim SEO would render the real description;
  - the live-check error paths.

  A token-level guard on the plugin source also fails the build on any of these:
  - any write function, namespaced or not, other than the one cache transient;
  - the same functions named in a string, as a callback for `array_map()` or `add_action()`;
  - `->query()`, `->insert()`, `->update()`, `->delete()` or `->replace()` on any object;
  - any SQL read on any object that isn't a `->prepare()` whose SQL starts with `SELECT`;
  - `call_user_func`, and variable function and method calls;
  - any call of the plugin's own to `do_blocks`, `do_shortcode`, `apply_filters`, `do_action` or
    Bricks' `render_data`.

  The guard can't see into Slim SEO. The Slim SEO calls the plugin makes (its title and description
  renderers and its content reader) run whatever Slim SEO runs, as described above.
- **Not run against a real WordPress.** A local wp-env site with Slim SEO, WooCommerce and the MCP
  Adapter was planned, but the building session's permission check refused to download and run
  WordPress and those plugins. Bricks is commercial and couldn't be installed anyway. **So the first
  real run is on staging.** Check these there:
  1. Run `site-summary`. The counts should be about 110 items, and the effective titles should look
     like the real `<title>`s.
  2. Run `page-detail` for a Bricks page with `live_check: true`. `item.title.effective` should
     match `live_check.head.title`, and the headings should be a subset of the served ones.
  3. Run `audit-content` with `per_page: 25` twice. The second call should come back within a few
     seconds, with `from_cache: true`.

---

## Sources

The API shapes here were read from source, not guessed.

- **Abilities API** (WordPress core since 6.9):
  - the [handbook](https://developer.wordpress.org/apis/abilities-api/);
  - [`wp_register_ability()`](https://developer.wordpress.org/reference/functions/wp_register_ability/)
    on the [`wp_abilities_api_init`](https://developer.wordpress.org/reference/hooks/wp_abilities_api_init/)
    hook;
  - [`wp_register_ability_category()`](https://developer.wordpress.org/reference/functions/wp_register_ability_category/)
    on [`wp_abilities_api_categories_init`](https://developer.wordpress.org/reference/hooks/wp_abilities_api_categories_init/).

  These details were checked in core's source at tags 6.9 and 7.1.3:
  - `wp-includes/abilities-api/class-wp-ability.php`: input and output validation, and how a
    callback receives its input;
  - `wp-includes/rest-api/endpoints/class-wp-rest-abilities-v1-run-controller.php`: read-only
    abilities run with GET, and a denial is `rest_ability_cannot_execute`;
  - `wp-includes/abilities.php`: core's site and environment info need `manage_options`.
- **MCP Adapter** v0.7.1:
  - [default server](https://github.com/WordPress/mcp-adapter/blob/v0.7.1/docs/guides/default-server.md);
  - [creating abilities](https://github.com/WordPress/mcp-adapter/blob/v0.7.1/docs/guides/creating-abilities.md);
  - `includes/Abilities/McpAbilityExposure.php`: an explicit `meta.mcp.public` wins, otherwise it
    falls back to `meta.public`;
  - `includes/Domain/Utils/McpAnnotationMapper.php`: `readonly` maps to `readOnlyHint`.
- **Slim SEO** 4.11.4 ([source](https://github.com/elightup/slim-seo/tree/4.11.4)):
  - `src/MetaTags/Title.php`, `Description.php`, `Robots.php` and `CanonicalUrl.php`: storage,
    fallbacks, noindex and canonical;
  - `src/MetaTags/Data.php` and `src/Integrations/Bricks.php`: the content reader and what it
    skips;
  - `src/Schema/Manager.php`: the built-in schema;
  - `src/MetaTags/Abilities/`: its own abilities and their permission checks;
  - `js/meta-tags/components/Fields/PostTitle.js` and `PostDescription.js`: the 60 and 50–160
    character guidance.
- **WooCommerce:** `wc_page_no_robots()` in `includes/wc-template-functions.php` noindexes the cart,
  checkout and account pages.
- **Slim SEO Schema** location rules:
  [adding schemas](https://docs.wpslimseo.com/slim-seo-schema/adding-schemas/).
