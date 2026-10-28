# Website commerce plan: SEO, Google listings, and buying online

**Status:** a plan for Nik to decide from, written 2026-10-28. **Nothing here is built.** No
code, plugin or setting changes until Nik picks from [the decisions](#9-decisions-for-nik).

Nik asked for three things on `www.sapphirefountains.com`:

1. **Better SEO.**
2. **Our products on Google**, including Google's "Universal Cart" if we can integrate with it.
3. **Online checkout**, where a customer buys a product on our website and the order reaches
   ERPNext as a real sales record with stock tracked.

This plan fits the SEO approach agreed on 2026-10-27 ([`../website-seo/`](../website-seo/)):
AI drafts changes, a Marketing Manager approves them in wp-admin, nobody approves their own
change, and a Search Console tool comes to ERPNext's MCP later.

All research was read-only: public fetches of the live site, Google's and vendors' published
documentation, and read-only queries on production ERPNext. Every outside claim carries a
source and a date in [§10](#10-sources).

---

## Contents

1. [The short version](#1-the-short-version)
2. [What we have today](#2-what-we-have-today)
3. [Google: what exists, what it needs, and what fits](#3-google-what-exists-what-it-needs-and-what-fits)
4. [SEO audit of the live site](#4-seo-audit-of-the-live-site)
5. [Buying online: three options](#5-buying-online-three-options)
6. [Failure modes and how the design handles them](#6-failure-modes-and-how-the-design-handles-them)
7. [Phases and effort](#7-phases-and-effort)
8. [What Nik sets up himself](#8-what-nik-sets-up-himself)
9. [Decisions for Nik](#9-decisions-for-nik)
10. [Sources](#10-sources)

---

## 1. The short version

- **Universal Cart is real but is not something we can plug into yet.** Google launched it in
  the US on 2026-10-15 as a shopper's cart in AI Mode and the Gemini app. Merchants take part
  only through the Universal Commerce Protocol, which is early access, hand-picked by Google, has
  no WooCommerce support, and excludes custom goods, rentals and special shipping.
- **Google Merchant Center free listings are the right first step, and they need a real store.**
  Google requires that listed products can be bought through a working cart and checkout on our
  site. Today nobody can: WooCommerce holds 24 products as a catalog, 4 of them priced, with a
  "Contact us" button instead of "Add to cart". Services, rentals and quote-priced fountains can
  never be listed.
- **Recommended checkout: WooCommerce on the website, with ERPNext as the source of truth.**
  ERPNext owns price and stock and pushes them to WooCommerce. Every paid order comes back into
  ERPNext as a submitted Sales Order, a Customer and a Payment Entry, through a durable record
  that survives a deploy. WooCommerce's Stripe gateway charges on our existing Stripe account,
  and our Stripe module records each charge, so payouts still reconcile and no invoice can be
  paid twice. Checkout stays up while ERPNext deploys.
- **Build the sync ourselves rather than install WooCommerce Fusion**, the only v16 connector.
  Its webhook does not check signatures, its default mode loses orders when a deploy flushes the
  job queue, and its stock push can oversell.
- **Start with 20 to 50 boxed products**: replacement parts and Sapphire's own controls. The
  catalog is not ready yet: in ERPNext no Item has a photo, weight, GTIN or brand, and several
  prices disagree with WooCommerce's. No chemicals at launch.
- **SEO has six High fixes**, mostly settings: the contact page is set to `noindex`; project pages
  are uncrawlable and duplicated; the sitemap comes from a second plugin and lists cart, test and
  template pages; there is no local-business markup; and there is a 404 linked from the site. None needs an AI writing to
  the live site.
- **Nik decides 14 things** ([§9](#9-decisions-for-nik)) and sets up Merchant Center, the Business
  Profile, the Stripe gateway and the policies ([§8](#8-what-nik-sets-up-himself)).

---

## 2. What we have today

### 2.1 The website

WordPress on WP Engine behind Cloudflare, built with Bricks, with Slim SEO (plus its Schema and
Redirection add-ons), WooCommerce, Google Site Kit, Jetpack, Fluent Forms, WP Grid Builder and
Code Snippets. Audited read-only on 2026-10-28 with about 145 GET requests; the details are in
[§4](#4-seo-audit-of-the-live-site).

**WooCommerce is in use, but as a catalog, not a store.**

- **24 published products**, all simple products, every one with at least one photo. Categories:
  Controls (Splash Wizard, Pool & Fountain Center and Level Master families, Stillwater E-Stop, VFD
  Bypass), Accessories, Nozzles and Rentals.
- **Only 4 have a price**: the deck jet nozzle, the wind-speed anemometer, the fast-acting
  solenoid valve and the deck jet's winterizing plug. The other 20, including all six rental
  fountains, are priced at $0 and show "Read more".
- **Nobody can actually buy.** The product page shows the price and a **Contact us** button. The
  only add-to-cart links are in the "Related products" strip. No cart icon, and no menu link to
  cart or checkout. `/cart/`, `/checkout/` and `/my-account/` exist, plus stray `/cart-2/` and
  `/checkout-2/` copies.
- **Stock is not tracked**: every product reports "in stock".
- **The SKUs do not match ERPNext.** Three of the four priced products carry auto-incremented
  SKUs (`PDT-0016`, `PDT-0016-1`, `PDT-0016-1-1`) where ERPNext's codes are `PDT-0050`, `PDT-0202`
  and `PDT-0152`. ERPNext's own `PDT-0016` is a Pool & Fountain Center model. The anemometer's
  price also disagrees: $118.50 in WooCommerce, $175.50 in ERPNext, and $118.50 is what ERPNext
  has on `PDT-0016`. So the matching key has to be cleaned up before any sync, and each price
  confirmed by a person.
- **Google for WooCommerce is installed.** Its REST namespace (`wc/gla`) is live, and the site's
  Google tag setup references it. Whether it is linked to a Merchant Center account, and what that
  account holds, needs Nik's login to see.
- **No product structured data reaches the page.** Product pages carry only Slim SEO's WebPage and
  breadcrumb markup. WooCommerce's own Product markup is not in the HTML, so something (most likely
  the Bricks single-product template) is not emitting it.
- **A return policy page exists** (`/return-policy/`), along with terms and a rental policy, but all
  three are set to `noindex`. Google can still read a noindexed policy page; the text needs
  checking against what we decide in [D9](#9-decisions-for-nik).

### 2.2 ERPNext: is anything ready to sell online?

**Not yet. The catalog has prices and part numbers, but nothing a shopper or Google needs.**
Measured on production on 2026-10-28 with read-only queries:

| What a web store needs | What ERPNext has |
|---|---|
| Product photos | **0** enabled Items have an image |
| Weights, for shipping rates | **0** Items have a weight |
| GTINs (UPC barcodes) | **0** Item Barcodes |
| Brand and manufacturer | **0** Brand records, **0** Item Manufacturer rows; 34 supplier part numbers |
| A selling price | 114 Standard Selling prices, 110 of them on QuickBooks-era items |
| Stock to sell from | 386 Items have stock in the shop's shelf bins, mostly with no valuation rate |
| Stock reservation | Off (`Stock Settings`), 0 reservation entries |
| An order-to-cash trail | 1,325 Sales Invoices (QuickBooks history), **0** Sales Orders, **0** Delivery Notes |
| Shipping rules | 0 |
| Sales tax | 3 QuickBooks-era flat templates (4%, 6% default, 6.25%), no Tax Rules (WI-036/037, gated on the CPA's written matrix, OD-2) |

**Production versions** on 2026-10-28: frappe 16.53.0, erpnext 16.52.1, erpnext_enhancements
1.753.2, plus `payments`, `telephony`, `newsletter` and `frappe_assistant_core`. The `webshop`
app is **not** installed, and neither is any WooCommerce connector.

**What could be sold online.** Three groups stand out.

- **Sapphire's own product line**, the `PDT-` Items carried over from QuickBooks. They include
  Splash Wizard (four models), Level Master (water, wind, both), Activation Bollard (standard,
  heavy duty, replacement sensor), VFD bypass panels (5 to 20 HP), Pool & Fountain Center (three
  sizes), the Stillwater E-Stop system and Sapphire Connect hardware. All are priced and **all are
  non-stock**. Some prices look wrong next to each other: the Pool & Fountain Center Pinnacle
  (`PDT-0016`) carries the website's anemometer price, and Splash Wizard Plus is priced above Max.
  They need a price review before anything is published.
- **Replacement parts customers already buy from us:** the adjustable deck jet, its winterizing
  plug and nozzle wrench, the paddle anemometer, the float level sensor, the fast-acting solenoid
  valve. These have repeat invoice history, ship in a box, and are the natural first products.
- **Stocked parts** in the shelf bins (pumps, PVC fittings, electrical, plumbing). They have stock
  but usually no selling price, photo or weight.

**Chemicals** (acid, chlorine liquid, tablets and granular, algaecide, defoamer) are sold today
as a per-visit line on maintenance invoices. Calcium hypochlorite is a Division 5.1 oxidizer and
muriatic acid a Class 8 corrosive. Shipping them means hazmat packaging, labels and carrier
rules ([§10](#10-sources)), so this plan keeps them **off** the web store at launch
([D10](#9-decisions-for-nik)).

**Most Items are non-stock.** About 490 enabled Items are locked that way because submitted
documents already use them. PR #1378 adds a checked "Turn on Maintain Stock" button for Item
Managers. Any Item sold online with stock tracking has to be a stock Item first, so the pilot
list goes through that button.

### 2.3 Payments: the Stripe module

`stripe_payments/` is live (Live environment, connected, card and ACH on, surcharging off per
OD-7). It talks to Stripe's REST API with `requests`, on purpose, with no SDK.

What matters for web orders:

- **Money lands in `Stripe Clearing - SF`.** A customer Payment Entry debits clearing. When
  Stripe pays out, `payouts.py` posts the payout Journal Entry that moves net + fees out of
  clearing into the bank and the fee account.
- **One invoice is never paid twice** (Nik's rules of 2026-09-24). Every payment path shares
  `card_element.invoice_payment_block` and `invoice_lock`. Any new path must extend that guard,
  never route around it.
- **Events are idempotent by design.** A `Stripe Event` is named by Stripe's event id, so a
  redelivery cannot be ingested twice. `tasks.poll_pending` (hourly) re-reads anything stuck, and
  `retry_failed` re-runs events that errored, including after a deploy's `FLUSHDB`.
- **Hosted Checkout today takes a Sales Invoice or a customer + amount.** It has no idea of a
  cart, shipping or a web customer who does not exist yet.

**The reconciliation consequence:** any charge on our Stripe account that does not get a Payment
Entry into `Stripe Clearing` breaks the payout entry. Whatever takes web payments must post one
Payment Entry per charge, into clearing, keyed on the charge.

### 2.4 Public pages on ERPNext

ERPNext already serves guest pages on `erp.sapphirefountains.com`: `/rent-a-fountain` (Turnstile,
honeypot, the session's own CSRF token), `/fountain-move`, `/pay`, `/pay-card` and
`/stripe-return`. So a public page on ERPNext is a known pattern. Two facts weigh against putting
**checkout** there now:

- **Every merge to `main` restarts the bench and flushes both Redis instances.** This repo
  ships several releases a day, so a checkout on ERPNext would blink out several times a day.
- **Production's web tier is mid-change.** PR #1379 moves it to gunicorn under systemd, and that
  cutover is Nik's. A public checkout should not depend on it until that has settled.

---

## 3. Google: what exists, what it needs, and what fits

Researched 2026-10-28 from Google's own help and developer pages, Google's blog, and trade press
where Google is silent. Google moves fast here: two of the pages below were updated the week
this was written.

### At a glance

| Surface | What it is | Status (Oct 2026) | Can we use it? |
|---|---|---|---|
| **Merchant Center free listings** | Our products in the Shopping tab, Search, Images and Maps at no cost | Live, open to anyone | **Yes, once the store can actually sell.** The first Google step |
| **Product structured data (merchant listings)** | Price, stock and shipping in our own search results | Live | **Yes.** Needs Product markup we do not output today |
| **Shopping ads** | Paid product ads (Performance Max) | Live | Yes, later, and it spends money (Nik's call) |
| **Universal Cart** | A cart in AI Mode and the Gemini app that holds items from many stores | **Launched in the US 2026-10-15** | **Not as a merchant integration we can build today.** See below |
| **UCP checkout** (Universal Commerce Protocol) | Google Pay checkout inside AI Mode, Gemini and Universal Cart, run against the merchant's own API | **Early access, US, Google picks the merchants** | **No for now.** No WooCommerce support, and our freight, custom and rental items are excluded anyway |
| **Business Agent** | A branded AI chat on our brand profile in Search | Early US rollout | Later: needs **50 approved free listings** |
| **Local inventory / Business Profile products** | "In stock nearby" and products on our Maps profile | Live | Local inventory needs a walk-in store; the Profile's manual Product Editor works for anyone |
| **Agent Payments Protocol (AP2)** | A trust layer between AI agents and payment systems | Announced 2025-09-16 | Nothing for a merchant to do |

### Universal Cart: what it actually is

Universal Cart is real, and it is a **shopper feature**. A shopper in AI Mode or the Gemini app
adds products from several stores to one cart. Google tracks price drops and restocks, suggests
promo codes and the card to pay with, and offers two ways to finish:

- **pay with Google Pay right in the cart**, or
- **move the items into the store's own cart** and check out on the store's site.

Either way the store stays the seller of record. It was announced at Google I/O on 2026-05-19
and launched in the US on 2026-10-15 on Search and Gemini, with YouTube and Gmail to follow.
The launch post names Nike, Sephora, Target, Ulta Beauty, Walmart, Wayfair and "many Shopify
merchants".

**There is no Universal Cart sign-up.** Both ways of finishing run on the **Universal Commerce
Protocol (UCP)**. Paying in the cart needs UCP's checkout endpoints; moving items to the store's
cart needs a UCP profile and a cart endpoint. Google onboards merchants through a UCP hub in
Merchant Center, after they build the integration and fill in an interest form, and Google
chooses who gets in.

Whether a shopper can **save** a product from an ordinary Merchant Center store (no UCP) into
Universal Cart and get price alerts is not stated anywhere we found. If so, free listings are what
put us there, and that comes with the recommended plan anyway.

### UCP checkout: why not now

UCP is an open standard announced at NRF on 2026-01-11, co-developed by Google, Shopify, Etsy,
Wayfair, Target, Walmart, Amazon, Microsoft, Meta, Salesforce and Stripe. A merchant who joins
gets a Buy button on eligible listings in AI Mode and Gemini, paid with Google Pay, charged
through the merchant's own processor. Google's merchant page (updated 2026-10-28) sets the bar:

- **Selection:** early access, US first, Canada and Australia "next year"; build first, then apply.
- **Merchant Center prerequisites:** free listings approved; return policies with cost, window and
  a link; a support contact; a per-product `native_commerce` checkout-eligibility flag.
- **Our own API:** a profile at `/.well-known/ucp`, checkout-session endpoints (create, update
  shipping, complete) and order-status updates sent back to Google.
- **Payments:** a Google Pay & Wallet Console merchant id. Stripe documents a UCP integration
  (charging through a PaymentIntent), so our Stripe account would work. Some of Stripe's UCP
  pieces still use a preview API version.
- **Excluded products:** custom or made-to-order goods, rentals and services, final-sale items,
  and anything with **special shipping**. That rules out the fountains, the rentals and, most
  likely, freight and hazmat.
- **Platforms:** Shopify has it built in. **WooCommerce does not**: it is not on ucp.dev, Google
  for WooCommerce does not mention it, and there is only an open feature request. The one
  third-party WordPress plugin had 30-odd installs and no claim of Google approval.

**Recommendation:** not now. Get free listings live and clean first, since UCP requires them anyway.
Look again in mid-2027, or as soon as WooCommerce or Google for WooCommerce ships UCP support.
Building the endpoints ourselves for a handful of parcel-size parts is not worth it today. This is
[D11](#9-decisions-for-nik).

### Merchant Center free listings: what Google needs from us

| Requirement | What Google asks | Where we are |
|---|---|---|
| **Account** | A Merchant Center account (Merchant Center Next was renamed plain "Merchant Center" in July 2026), signed into at least once every 14 months | Google for WooCommerce is installed, so an account may exist. Nik checks |
| **Website** | Verified and claimed; contact details visible; a stated return and refund policy; **products buyable through a working add-to-cart and checkout**; SSL; full costs shown | **Fails today:** nothing can be bought. This is why checkout comes before listings |
| **Product data** | A feed, a Google Sheet, the Merchant API, or Google crawling our Product markup. Google for WooCommerce (v3.10.0, 2026-10-20) syncs through the Merchant API | Use the plugin. The old Content API for Shopping was sunset on 2026-08-18 |
| **Price** | Required and above zero; for the US, before tax | 20 of 24 WooCommerce products are at $0. Quote-only items cannot be listed at all |
| **Shipping** | **Shipping costs are required in the US**: flat, free over a threshold, weight-based, a rate table, or carrier-calculated (UPS, FedEx, USPS). Labels group oversized items | We have no shipping rules and no Item weights |
| **Return policy** | Set at account level (window, method, who pays, restocking fee, URL) | A `/return-policy/` page exists; the terms need deciding ([D9](#9-decisions-for-nik)) |
| **Tax** | **Not required in the US since 2025-07-01** | Nothing to do on Google's side |
| **Identifiers** | Our own brand: `brand` = Sapphire Fountains plus our own `mpn` (the ERPNext item code). Resold parts: the manufacturer's GTIN where one exists | No brand records and no barcodes in ERPNext |
| **Images** | No watermarks or overlays; about 1500 × 1500 recommended; **500 × 500 minimum from 2027-01-31** | WooCommerce has photos for all 24 products; ERPNext has none |
| **What is not allowed** | **Services**, including services bundled with goods (Google's examples include maintenance and repair), and anything without a price | Design, build, service and rental stay off Merchant Center. They belong on the website and the Business Profile |

**Chemicals.** Google's dangerous-products policy does not name pool chemicals either way. Carrier
hazmat rules are the real limit ([D10](#9-decisions-for-nik)).

### Product structured data and Search Console

Google's merchant-listing guide (updated 2026-09-08) wants `Product` with `name`, `image` and an
`Offer` with `price` and `priceCurrency`, plus `availability`, `sku`, `brand`, `gtin`/`mpn`,
`shippingDetails` and `hasMerchantReturnPolicy`. The offer must be ours (not an aggregate) and buyable
on the page. Since 2025-11-12, shipping and return settings can also be entered in **Search Console**
without Merchant Center, and Google now reads site-wide return and shipping policies from
`Organization` markup too. Merchant Center data wins over page markup when they disagree.

Our product pages emit none of this today ([§4](#4-seo-audit-of-the-live-site), #7). Fixing it helps
twice: rich results in ordinary search, and Merchant Center's "automatic item updates", which correct
price and availability from the page.

### Business Profile and Business Agent

- **Google Business Profile** matters more than anything above for "fountain repair near me"
  searches. The site never links to one ([§4](#4-seo-audit-of-the-live-site)).
- Its manual **Product Editor** can show a few product lines and the rental fountains on the
  profile, at no cost and with no feed.
- **Local inventory** ("in stock nearby") needs a walk-in store where the public buys. It is only
  relevant if the shop opens a parts counter.
- **Business Agent**, a chat on our brand profile that answers from Merchant Center and website
  data, needs a verified US Merchant Center account, a claimed brand profile and at least **50
  approved free listings**. The pilot catalog would have to grow first.

---

## 4. SEO audit of the live site

Read-only, 2026-10-28: about 145 GET/HEAD requests with a browser user agent, 50 pages read in
full. No form, cart, login or MCP endpoint was touched. Two gaps: **PageSpeed Insights refused
every run** (the keyless shared quota was exhausted, HTTP 429), so there are no Core Web Vitals
figures; and the web search tool used is not Google, so the ranking notes are only a hint. Search
Console is the ground truth for both, and phase 3 of the SEO plan brings it into ERPNext's MCP.

### What the site does well

- Titles are mostly well formed, under 60 characters, with the brand.
- One canonical host: `http://`, bare domain and missing trailing slashes all redirect to
  `https://www.sapphirefountains.com/…/`. Unknown URLs return a real 404.
- The name, address and phone are consistent across header, footer, contact and about pages.
- Every image has an alt attribute, even if many are filenames.
- Cached pages answer in 0.13 to 0.3 seconds from Cloudflare.
- Products, projects, rentals and the blog are all in the sitemap.

### What is wrong, by priority

**How each fix gets made.** "Setting" means a site-wide switch in a plugin or Cloudflare, which a
WordPress administrator changes by hand. "Template" means a Bricks template edit. "Page SEO" means
a per-page title, description or noindex, which is exactly what phase 2's AI-drafted,
Marketing-Manager-approved path is for. Nothing in this table is done by an AI writing to the live
site directly.

| # | Priority | Finding | Fix | How |
|---|---|---|---|---|
| 1 | High | **`/contact/` is `noindex`**, has no canonical, and its description is the single word "Contacts". It is the main lead page. | Remove noindex, write a real description, output a canonical. | Page SEO |
| 2 | High | **Project pages cannot be crawled and exist up to three times.** The `/projects/` grid opens each project in a Bricks popup, so no link points at any of the 18 `/projects/*` pages. About 12 projects are also duplicated as `/<slug>/` and `/<slug>-2/` posts, all indexable, with identical titles. | Make project cards real links. 301 the duplicate posts to the `/projects/` version (Slim SEO Redirection) and drop them. | Template + Setting |
| 3 | High | **Junk in the sitemap and index:** `/cart/`, `/checkout/`, `/test/`, `/cart-2/`, `/checkout-2/`, `/product-category/uncategorized/`, and 13 Bricks templates (`/template/header/` serves a full copy of the homepage). | Delete the test and `-2` pages; noindex cart and checkout; keep Bricks templates out of the sitemap and out of public view. | Setting + Page SEO |
| 4 | High | **The live sitemap is not Slim SEO's.** "Google XML Sitemaps" serves `/sitemap.xml` and also empties WordPress's own `/wp-sitemap.xml`. It lists noindexed pages, which is why #3 happens. | Use one generator. Slim SEO's own sitemap leaves noindexed pages out by itself. Disable the other plugin and resubmit in Search Console. | Setting |
| 5 | High | **No local-business data in structured data.** The only Organization markup is a hand-coded block with a phone number and no address; Slim SEO's own Organization uses the favicon as the logo. | One `HomeAndConstructionBusiness` (a LocalBusiness type) via the Slim SEO Schema add-on: address, geo, hours, `areaServed`, all social profiles, the real logo. Remove the hand-coded duplicate. | Setting |
| 6 | High | **`/contact-us/` is a 404 linked from `/projects/` and two blog posts.** | 301 it to `/contact/` and fix the links. | Setting + Page |
| 7 | Medium | **No Product markup on any product page** (no price, availability, SKU, brand). Google's merchant listings need it. | Emit Product + Offer for priced products, with shipping and return details ([§3](#3-google-what-exists-what-it-needs-and-what-fits)). Find what is suppressing WooCommerce's own markup first. | Template + Setting |
| 8 | Medium | **No pages for the places we work.** Nothing targets Salt Lake City, Utah County, Park City or Las Vegas; the only service-area text is "Wasatch Front" in rental copy. | Location landing pages with real project examples from each area, linked from service pages. | New pages, approved |
| 9 | Medium | **Service pages are orphaned and do not link onward.** Ten water-feature pages (`/fountain-repair/`, `/splash-pads/`, `/water-walls/`…) and all nine `/design/<audience>/` pages are linked from nowhere. Service pages link to no projects. | Add them to the menu; add "related projects" and "related rentals" blocks to service templates. | Template |
| 10 | Medium | **About a dozen weak titles or descriptions:** `/design/architects/` is titled "Architects", `/create-a-fountain/` "Sapphire Fountains", `/splash-pad-installation/` repeats itself (117 characters), `/referral-program/` has a leftover Divi shortcode as its description and an empty body, several have none. Project and policy descriptions are body text cut at 160 characters. | Rewrite each; set Slim SEO's default description template for custom post types. | Page SEO |
| 11 | Medium | **No H1** on products, projects and testimonials. | An H1 of the post title in each Bricks single template. | Template |
| 12 | Medium | **Three Google Tag Manager containers, an Ads tag, Site Kit's tag and a chat widget** load on every page. Heavy, and risks counting a visit twice. | Consolidate to one container; check Site Kit is not tagging a second time. | Setting |
| 13 | Medium | **Core Web Vitals unknown.** The homepage HTML is 283 KB with about 135 resources, and 78 of 86 images are not lazy-loaded. | Run PageSpeed Insights in a browser for `/`, `/rentals/` and one product. | Check |
| 14 | Low | Thin or duplicate archives are indexable: `/page/2/` (a copy of the homepage), `/shop/page/2/`, `/category/blogs/`, gallery and project category pages with duplicate titles. | Noindex them. | Setting |
| 15 | Low | Placeholder lorem ipsum on `/products/`; all-caps product titles; filename alt text ("Vector", "Hqdefault"); typos ("commericial", "Instgram"). | Edit. | Page |
| 16 | Low | Two redirect hops from `http://sapphirefountains.com`; HTML cached at the edge for 180 days (one homepage copy was 15 days old); no visible breadcrumbs although the markup has them. | One Cloudflare redirect; a short HTML edge lifetime with purge on publish; a Breadcrumbs element. | Setting + Template |
| 17 | Low | No HSTS or other security headers; `wp-login.php` public; `/wp-json/` lists every namespace (1.4 MB). | Headers at Cloudflare; limit login and REST exposure. | Setting |

**Local search.** The web search tool showed the company's listings on wedding-directory sites for
"fountain rental Utah", but not the site itself, and found nothing for "fountain repair Salt Lake
City" or "fountain design Utah". Treat that as a reason to look in Search Console, not as a
measurement. A **Google Business Profile** matters more than any page for local queries: the site
links to Google Maps with two different short links and never to a Business Profile. Nik should
confirm the profile exists, is verified, and lists the services and the service area
([§8](#8-what-nik-sets-up-himself)).

### Fit with the agreed SEO approval model

The 2026-10-27 plan stands, and this audit is its first worklist:

- **Settings** (#2–#7, #12, #14, #16, #17) are site-wide switches. They need an administrator, so
  an AI cannot make them through `seo-agent` as a Contributor. They are a short checklist for
  whoever administers WordPress, best done on staging first.
- **Page SEO** (#1, #3, #10) is per-page titles, descriptions and noindex: the phase 2 path, where
  the AI drafts and a Marketing Manager approves in wp-admin. #1 is urgent enough to do by hand now.
- **Templates and new pages** (#2, #8, #9, #11) are Bricks work. The AI can draft copy for the
  location pages; a person builds and publishes them.
- **Never** through Slim SEO's own `update-*` abilities, which bypass the approval.

---

## 5. Buying online: three options

All three end the same way in ERPNext: a submitted **Sales Order** with a **Customer**, paid
through a **Payment Entry** into `Stripe Clearing - SF`, then a **Delivery Note** when the box
ships (this moves the stock) and a **Sales Invoice** against the paid order. They differ in where
the shopper checks out and who owns the copy of the catalog.

### Option A — WooCommerce on the website, synced with ERPNext (recommended)

The shop lives on `www.sapphirefountains.com`, where the SEO and Google listings live too.
WooCommerce is already installed there.

- **Source of truth.** ERPNext owns the Item, its **price** (a new `Website` selling Price List)
  and its **stock** (the shop's shelf bins, minus a per-item safety buffer). WooCommerce holds a
  copy, pushed from ERPNext whenever a price or stock level changes. Product page copy, photos and
  SEO stay in WordPress, where the Marketing approval rule already applies.
- **Order to ERPNext.** The shopper pays in WooCommerce. A signed webhook (WooCommerce's
  `X-WC-Webhook-Signature`, an HMAC we verify) reaches ERPNext, which writes a `Web Order` row,
  then creates the Customer, a submitted Sales Order, and the Payment Entry for the charge.
  Shipping goes on the order as its own line. ERPNext tells WooCommerce when the order ships, with
  tracking.
- **Stock reservation.** WooCommerce holds the unit from the moment of payment. ERPNext reserves it
  against the Sales Order (needs `Stock Settings` reservation turned on) until the Delivery Note
  moves it. With reservation off, the Sales Order still shows in projected quantity, which is
  enough at our volume.
- **Customer record.** Matched by the buyer's email: an existing Contact with that email gives its
  Customer. Otherwise a new **Individual** Customer in the buyer's state Territory, with Contact
  and Address. A buyer who names a company is never merged silently into an existing company
  Customer; the order is flagged for a person to link.
- **Tax.** WooCommerce computes it at checkout (WooCommerce Tax is free and uses the shipping
  address's rate). ERPNext records it as an Actual tax row on the Sales Order, against the right
  liability account. Which streams are taxable is the CPA's call (OD-2, WI-036); Products are
  expected to be taxable to the customer.
- **Shipping.** Parts ship by parcel at weight-based rates, plus free local pickup at the shop.
  Anything too big for a parcel is **not sold in the cart**: its page shows a "Request a quote"
  button into the existing lead flow. That keeps freight off the checkout.
- **Returns.** Done in ERPNext: a Sales Return (stock back in) and a refund through the Stripe
  module, which then marks the WooCommerce order refunded. The rule is that nobody refunds from
  WooCommerce; if someone does, Stripe's `charge.refunded` still reaches ERPNext and drafts the
  reversing entry for Accounts to review, so the books cannot silently miss it.
- **Stripe reconciliation.** WooCommerce charges through its own Stripe gateway, on **our same
  Stripe account**. For each paid web order, ERPNext writes a `Stripe Payment` row for the
  order's PaymentIntent (the gateway keeps the Stripe ids on the order; which field holds which is
  checked against the installed gateway version before building) and posts it through
  the module's existing `finalize_payment`: one Payment Entry into `Stripe Clearing - SF`, keyed on
  the PaymentIntent. The payout Journal Entry then balances unchanged. Today our webhook already
  receives these charges' events and ignores them, because `_find_payment` finds no row; with the
  row in place, `payment_intent.succeeded` becomes a second route to the same Payment Entry and
  `charge.refunded` drafts the reversal, exactly as for an invoice payment. The shared
  `invoice_payment_block` reads the same ledger, so no "Pay" link can ever charge a web order twice.
- **Why it wins.** Checkout keeps working while ERPNext deploys and restarts, which it does several
  times a day. WooCommerce brings the cart, tax, shipping zones, order emails, product structured
  data and Google's own feed plugin, all maintained by others. The catalog is small, so the sync is
  small.

**How to connect them: build our own sync, or install WooCommerce Fusion.** ERPNext core dropped
its WooCommerce connector in v15 (removed 2023-09-14, "broken and unmaintained"). The only
community app with v16 support is **WooCommerce Fusion** (`Starktail/woocommerce_fusion`,
`version-16`, v2.3.0 of 2026-08-17, about 100 installs). Read in source on 2026-10-28, it does a
lot: orders to Sales Orders, Payment Entries, price and stock pushes, multiple stores. But it does
not fit our rules out of the box:

- Its webhook **does not verify the signature** (the check is commented out, issue #271).
- In its default mode the hourly poll moves its "last synced" date forward as soon as it
  **enqueues** the jobs, so a job lost to a deploy's `FLUSHDB` is never looked for again. Its opt-in
  Batch API mode keeps a durable queue and avoids this.
- Its stock push can overwrite WooCommerce's own decrement for an order not yet imported, which
  oversold in one report (issue #276).
- Its tax mapping has open v16 bugs (#255, #259) and posts all tax to one account.
- It finds an existing Sales Order before creating one but has no lock or unique key, so a webhook
  and a poll arriving together could in principle create two.
- It is a second app on the production bench, with its own pip dependencies, outside this repo's
  CI and deploy pipeline.

**Recommendation: build a small sync inside `erpnext_enhancements`.** It is about four PRs, on
patterns this repo already has: a durable row per event (like `Stripe Event`), an hourly poll
and a re-drive sweep (like `poll_pending` and `retry_failed`), a signed webhook, and Payment
Entries into clearing (like `reconcile.finalize_payment`). It covers only what we need for a pilot
of 20 to 50 products. Fusion stays a fallback if the catalog ever grows into thousands of items
with variants. This is [D3](#9-decisions-for-nik).

### Option B — checkout hosted by ERPNext

Product pages stay on WordPress, but "Buy" sends the shopper to a page on
`erp.sapphirefountains.com` that collects the address, prices shipping and tax, and opens a Stripe
Checkout Session through our own module.

- **Source of truth.** ERPNext for everything, with no store copy of stock. But the WordPress
  product pages still show a price, and Google requires that price to match, so prices still need
  pushing to WordPress. A sync does not go away.
- **Order to ERPNext.** The cleanest of the three: the Stripe webhook already lands in ERPNext,
  creates the Sales Order and the Payment Entry, and the shared guard covers it with no new payment
  path.
- **Customer, tax, shipping, returns:** the same rules as A, but we build each piece. Tax would
  come from Stripe Tax (0.5% of volume on the no-code path) or our own Utah rate table. Shipping
  rates and the cart are ours to write.
- **What it costs.** We would build a cart, an address form, shipping rates, order emails and a
  Google product feed (Merchant API) ourselves. The checkout is on another subdomain from the
  product pages. And checkout goes down with every ERPNext deploy restart.
- **Frappe's `webshop` app** is the packaged version of this idea (it has a `version-16` branch).
  It replaces WordPress as the storefront rather than syncing with it, has no tagged releases, and
  pays through the `payments` app, which uses the Stripe SDK and the old Charges API. It conflicts
  with our SDK-free Stripe module, so this plan does not recommend it.

B is the right shape only if we decide the website should not sell at all and ERPNext should.

### Option C — checkout hosted by Google

This means UCP checkout ([§3](#ucp-checkout-why-not-now)): the shopper pays with Google Pay inside
AI Mode, Gemini or Universal Cart, and Google calls **our** checkout API to create and complete
the order.

- **It is not a hosted checkout.** Google hosts only the buttons. We would still write and run
  the checkout API (profile, sessions, shipping, completion, order updates), so C sits on top of A
  or B rather than replacing them.
- **We cannot get in today.** It is early access, Google selects merchants, and WooCommerce has
  no support. Our fountains, rentals and freight are excluded by Google's own rules.
- **A covers most of what it asks for.** Free listings approved, a return policy, a support
  contact, product ids that match our checkout. Doing A well is the way to qualify later.

C is a future add-on, not an alternative.

### Side by side

| | A. WooCommerce + ERPNext sync | B. Checkout on ERPNext | C. Google (UCP) |
|---|---|---|---|
| **Price and stock source** | ERPNext, pushed to WooCommerce on change | ERPNext; prices still pushed to WordPress pages | Whatever A or B uses |
| **Order becomes** | Submitted Sales Order + Payment Entry, from a durable `Web Order` row | Sales Order + Payment Entry from the Stripe webhook | Same as A or B, via our own UCP API |
| **Stock reservation** | WooCommerce at payment, then ERPNext against the Sales Order | ERPNext only | Ours |
| **Customer** | Matched on email, else a new Individual | Same | Same (Google passes the buyer's details) |
| **Tax** | WooCommerce Tax at checkout, recorded as Actual rows | Stripe Tax or our own table | Ours |
| **Shipping** | WooCommerce zones: weight table + pickup; freight is quote-only | Ours to build | "Special shipping" items excluded |
| **Returns** | In ERPNext, refund through our Stripe module | Same | Google asks for order updates incl. returns |
| **Stripe reconciliation** | WooCommerce's gateway on our account; `Stripe Payment` row per order; existing payout entry | Native to our module | Google Pay token charged by our processor |
| **Up during ERPNext deploys** | **Yes** | No | Depends on A or B |
| **Google listings** | Google for WooCommerce plugin, already installed | Our own Merchant API feed | Requires free listings first |
| **Build in this repo** | Sync module, about 4 PRs | Cart, checkout, rates, emails, feed: about 8 PRs | Not available to us |
| **Verdict** | **Recommended** | Only if the website should not sell | Revisit mid-2027 |

---

## 6. Failure modes and how the design handles them

These apply to the recommended option (A). Each one names the mechanism, so a reviewer can check
the build against it.

**A web order is a record before it is a job.** The webhook handler writes a `Web Order` row,
named by the store's order id, and commits it **inside the request**, before it enqueues
anything. Processing is a background job, but the job is never the only copy of the order.

| Failure | What happens | How the design handles it |
|---|---|---|
| **The store sends the same order twice** (webhook retry, or a webhook plus the poll) | Two Sales Orders | The `Web Order` name is the store order id, so a second insert is the same row. The Sales Order carries the order id in a unique field. Processing is a state machine on the row (Received → Customer → Sales Order → Payment Entry → Done) and every step checks before it creates. |
| **A webhook never arrives** | No ERPNext record | An hourly poll asks the store for orders modified since the last good poll, minus an overlap, and inserts any it does not have. This is the same backstop shape as `stripe_payments.tasks.poll_pending`. |
| **A deploy flushes Redis mid-order** | The enqueued job is gone | The row survives in MariaDB. A sweep (every 15 minutes) re-enqueues any `Web Order` not Done and not touched for 10 minutes. Deploys flush `:11000` and destroy queued jobs, so this sweep is the real guarantee. |
| **A step fails halfway** (Customer made, Sales Order refused) | A half-processed order | Each step is idempotent and the row records how far it got. A failure sets `Error` with the message, alerts Accounts once, and the next sweep retries. After 5 failures it stops and waits for a person, so a bad order cannot loop forever. |
| **Two shoppers buy the last unit** | Oversold | The store decrements its own stock at payment. ERPNext pushes stock to the store whenever it changes, minus a per-item safety buffer. If ERPNext still cannot reserve, the Sales Order is created anyway (the money is real) and flagged **Backorder**, and the shop contacts the customer with a ship date or a refund. Volume is low, so the buffer is the practical guard. |
| **Price drift** (ERPNext price changed, store still shows the old one) | The customer paid the store's price | The order's price wins. The Sales Order is created at the paid price and flagged if it differs from the ERPNext price by more than a cent. Price pushes go out on change, so drift is a window of minutes. |
| **Paid, then refunded in the store by mistake** | ERPNext still thinks it is paid | Refunds happen **only in ERPNext** (a return plus a refund through the Stripe module). Store staff lose the refund capability. If one happens anyway, the web order's `Stripe Payment` row means Stripe's `charge.refunded` reaches the module's existing handler, which drafts the reversing Payment Entry for Accounts. |
| **The charge never gets a Payment Entry** | Stripe Clearing is short, and the payout entry does not balance | Two routes post it: the order sync, and Stripe's own `payment_intent.succeeded` once the row exists. Both go through `finalize_payment`, which never posts twice. The payout entry already flags a variance for review, and a daily check lists charges on the account with no `Stripe Payment` row. |
| **The Stripe event arrives before the store's order** | No row to match yet | The event is stored (`Stripe Event`) and ignored as today. The order sync posts the payment when the order arrives, and `poll_pending` re-reads anything left `Processing`. Arrival order does not matter. |
| **A staff member sends a "Pay" link for a web order's invoice** | The customer pays twice | The web order's Sales Invoice is created already paid (the advance is allocated). The shared `invoice_payment_block` also learns about web orders, so it refuses any second payment even if allocation failed. |
| **The store or ERPNext is down** | Orders wait | The store keeps selling while ERPNext restarts, because checkout does not depend on ERPNext. Orders queue on the store side and the poll catches up. The store only stops showing new stock levels for those minutes. |

---

## 7. Phases and effort

Effort is in working days of build plus review, the way this repo's work sessions run, and
separately in people's time where the work is not code. Phases 1 and 2 can run side by side.

| Phase | What | Who | Effort | Done when |
|---|---|---|---|---|
| **0. Decide** | The 14 decisions; the pilot product list; return and shipping policy text; the CPA note on online sales tax | Nik, with the shop and Accounts | 1 to 2 weeks of elapsed time | Decisions recorded on the ERPNext Task |
| **1. SEO fixes** | The High items in [§4](#4-seo-audit-of-the-live-site) on staging then live: contact page indexable, project cards as real links and the duplicate posts redirected, one sitemap, LocalBusiness markup, the `/contact-us/` redirect. Business Profile checked | A WordPress administrator; AI drafts the copy | 1 to 2 days of admin time | Search Console shows the new sitemap read, and `/contact/` is indexable |
| **2. Catalog ready in ERPNext** | Pilot Items switched to Maintain Stock (PR #1378's button); a `Website` selling Price List; weights, photos, brand (our own = Sapphire Fountains) and GTINs for resold parts; a "listed on website" flag and safety stock per Item; WooCommerce SKUs changed to ERPNext item codes; prices confirmed by a person | Item Manager in ERPNext; 1 PR for the custom fields and a "Web catalog" readiness report | 1 to 2 days build; the data work depends on the list | The readiness report shows every pilot Item complete |
| **3. The sync** | A `web_store` module in this app: WooCommerce REST client on `requests` (no SDK, like Stripe and QuickBooks); settings Single with encrypted keys; ERPNext → WooCommerce price, stock and publish pushes; `Web Order` DocType; signed webhook; hourly poll; 15-minute re-drive sweep; Customer, Sales Order and `Stripe Payment` + `finalize_payment`; shipment and tracking back to WooCommerce; the `invoice_payment_block` extension. Bench-free tests in CI | Work sessions, draft PRs, reviewed and merged by the Sapphire Manager | About 4 PRs, 6 to 10 days | A test order on staging produces the full ERPNext trail, twice delivered, once recorded |
| **4. Open the store** | WooCommerce settings: the Stripe gateway on our account, WooCommerce Tax, shipping zones (weight table, local pickup), add-to-cart and a cart icon on the Bricks templates, order emails, the policy pages linked; freight items set to "Request a quote" | WordPress administrator; Nik connects Stripe | 1 to 2 days | **One small live order** paid on a phone, shipped and recorded, verified from the records (Nik's Stripe rollout rule) |
| **5. Google listings** | Merchant Center linked through Google for WooCommerce; shipping and return settings; Product markup fixed on product pages; Search Console merchant-listings report clean | Nik (accounts); WordPress administrator; AI checks the feed diagnostics | 1 to 3 days, then Google's review | Pilot products approved for free listings |
| **6. Later** | Shopping ads (spend, Nik's call); Business Agent once 50 listings are approved; UCP when WooCommerce supports it; location landing pages; the Search Console tool in ERPNext's MCP (SEO phase 3) | — | — | — |

**What the sync is not:** it never creates Items from WooCommerce, never edits product copy, and
never touches WordPress pages. Items are born in ERPNext; copy and photos are born in WordPress
under the Marketing approval rule.

**WooCommerce access for ERPNext:** a REST API key with read/write on products and orders only,
created by Nik in wp-admin for a dedicated `erpnext-sync` user with the Shop Manager role, and
pasted by Nik into the ERPNext settings form. It is never sent through chat, and never the
`seo-agent` user, which stays a Contributor.

---

## 8. What Nik sets up himself

Accounts, policies and anything that signs in. Nothing here needs a password in chat.

**Google**

1. **Merchant Center.** Sign in and check whether Google for WooCommerce already created or linked
   an account. Verify and claim `www.sapphirefountains.com`; fill in business details and a
   support contact; add the shipping settings and the return policy from D8 and D9. Leave tax
   blank (not needed in the US since 2025-07-01). Do this in phase 5, after the store sells.
2. **Google Business Profile.** Confirm it exists and is verified for the Bountiful shop, with the
   services, the service area (Wasatch Front and beyond) and the website link. Optionally add a few
   product lines and the rental fountains with the Product Editor. Use the profile's own link on
   the website instead of the two different Google Maps short links it carries now.
3. **Search Console.** Confirm ownership (Site Kit may already have it), submit the single sitemap
   once phase 1 picks one, and fill in shipping and returns.
4. **Google Pay & Wallet Console:** nothing until UCP.

**WordPress and WooCommerce**

5. **The Stripe gateway.** Install WooCommerce's official Stripe gateway and connect it to the
   existing Stripe account in its own screen (Stripe's sign-in, not ours). Card, Apple Pay and
   Google Pay; no surcharge.
6. **WooCommerce Tax.** Free in the US; it needs a WordPress.com connection, which the installed
   Jetpack may already provide.
7. **The ERPNext key.** Create the `erpnext-sync` Shop Manager user and its REST API key and
   webhook secret, and paste them into ERPNext's settings form.
8. **The SEO settings checklist** from phase 1, or hand it to whoever administers WordPress.
9. **Still open from 2026-10-27:** switch `seo-agent` to Contributor and install the SEO abilities
   mu-plugin (TASK-2026-02988).

**Business**

10. **Policies.** Return and refund terms (D9), a shipping policy, and terms of sale. The existing
    `/return-policy/` and `/terms/` pages may need rewriting for parts sales.
11. **The CPA.** Add online product sales to the OD-2 written matrix: Utah destination rates for
    in-state parcels, nothing for out-of-state parcels, and pickup at the shop's rate.
12. **The pilot list.** With the shop: which 20 to 50 products, their prices, weights and photos,
    and the manufacturer's GTIN for resold parts.

Each of these becomes an ERPNext Task with a ToDo for Nik once he has picked, so they show on his
list instead of in this document.

---

## 9. Decisions for Nik

Each has a recommendation first. The build waits for these.

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **D1** | Where shoppers check out | **A** WooCommerce on the website · **B** a checkout on ERPNext · **C** Google's checkout only | **A.** It stays up through ERPNext deploys and brings cart, tax, shipping and Google's feed plugin. C is not open to us ([§3](#3-google-what-exists-what-it-needs-and-what-fits)). |
| **D2** | What we sell online first | (a) replacement parts and own-brand controls that ship in a box · (b) also stocked parts from the shelf bins · (c) everything priced | **(a), 20 to 50 products**, chosen with whoever runs the parts shelves. Fountains, rentals and design stay "Request a quote". |
| **D3** | How WooCommerce and ERPNext connect | Our own sync in `erpnext_enhancements` · WooCommerce Fusion app | **Our own sync.** Fusion's unsigned webhook, lost-job and stock-race issues all hit rules we already hold ourselves to ([§5](#5-buying-online-three-options)). |
| **D4** | Which ERPNext document a paid web order creates | Sales Order → Delivery Note → Sales Invoice · Sales Invoice with Update Stock at shipping | **Sales Order first.** It shows the order the moment it is paid, holds the stock, and gives the shop a pick-and-ship step. |
| **D5** | Stock reservation | Turn on `Stock Settings` reservation · leave it off | **Turn it on**, scoped to web orders. Without it a paid order does not hold its unit against a technician pulling the last one for a job. |
| **D6** | The web customer record | One Customer per buyer, matched on email · one shared "Web Customer" | **One per buyer**, matched on email, new ones as Individual in their state's Territory. A buyer naming a company is flagged, never auto-merged. |
| **D7** | Sales tax at checkout | WooCommerce Tax (free) · Stripe Tax (0.5%) · our own rate table | **WooCommerce Tax**, Utah only at launch, subject to the CPA's matrix (OD-2). Utah's Tax Commission says goods shipped out of state are not Utah-taxable, and a new store starts far below other states' remote-seller thresholds (commonly $100,000 a year into that state). |
| **D8** | Shipping | Weight-based parcel table + local pickup · live carrier rates · flat rate | **Weight table + local pickup** at launch. Live carrier rates later if orders justify it. Freight items are quote-only. |
| **D9** | Return policy | Nik writes the terms | **30 days, unused, in original packaging; buyer pays return shipping on non-defective returns; special-order and electrical items that were installed are final sale.** Google needs a published policy before any listing. |
| **D10** | Chemicals online | Not at launch · local pickup only · ship as hazmat | **Not at launch.** Chlorine is a 5.1 oxidizer and acid a Class 8 corrosive; shipping needs hazmat packaging and training. Local pickup could come later. |
| **D11** | Google surfaces | Free listings only · free listings + Shopping ads · also apply for UCP checkout now | **Free listings first**, through Merchant Center and Google for WooCommerce, once the store sells. Shopping ads only after a month of clean listings, and any ad spend is Nik's call, per the marketing rule that nothing touches spend. **UCP and Universal Cart checkout: not now**; look again in mid-2027 or when WooCommerce supports it. |
| **D12** | Who approves product pages and prices | Marketing Manager in wp-admin (copy, photos) and an Item Manager in ERPNext (price, stock, what is listed) · one person for both | **Split as written.** It matches the agreed rule: AI drafts, a Marketing Manager approves, nobody approves their own change. |
| **D13** | The SEO fixes in [§4](#4-seo-audit-of-the-live-site) | Do the High ones by hand now · wait for phase 2's approved-write path | **By hand now** for the High items (mostly settings, not copy), staging first; page-by-page copy through phase 2. |
| **D14** | How WooCommerce takes the card | WooCommerce's official Stripe gateway on **our** Stripe account · WooPayments · a separate Stripe account | **The official Stripe gateway on our account**, card plus Apple Pay and Google Pay, surcharging off (OD-7). One account means one payout stream, which our module already reconciles. WooPayments and a second account would each split the money away from `Stripe Clearing`. |

---

## 10. Sources

Read on 2026-10-28. The date is the page's own publish or "updated" date; "no date" means the page
shows none. Trade press is marked as such and was used only where Google's pages are silent.

**Universal Cart and UCP**

- [Universal Cart launch](https://blog.google/products-and-platforms/products/shopping/universal-cart-launch/), Google blog, 2026-10-15: US launch on Search and Gemini; checkout powered by UCP; merchant stays seller of record.
- [Universal Cart preview at I/O](https://blog.google/products-and-platforms/products/shopping/google-shopping-cart/), Google blog, 2026-05-19: YouTube and Gmail later; UCP checkout expanding to Canada, Australia, then the UK.
- [Universal Cart store hand-off](https://www.searchenginejournal.com/google-universal-cart-store-handoff/593145/), Search Engine Journal (trade press), 2026-10-15: hand-off to the store's cart is one-way; merchants choose in the UCP hub.
- [Universal Cart calls retailers only at transfer](https://ppc.land/googles-universal-cart-calls-retailers-once-and-only-at-transfer/), PPC Land (trade press), 2026-10-16: cart transfer needs `/.well-known/ucp` and a cart endpoint.
- [UCP for merchants](https://developers.google.com/merchant/ucp), Google for Developers, no date: overview, waitlist, roadmap.
- [UCP guides](https://developers.google.com/merchant/ucp/guides), Google for Developers, updated 2026-10-19: early access, US rollout, `native_commerce` flag.
- [UCP Merchant Center prerequisites](https://developers.google.com/merchant/ucp/guides/merchant-center), Google for Developers, updated 2026-10-28: prerequisites and the products not eligible (custom, rentals, services, special shipping).
- [UCP checkout in Merchant Center](https://support.google.com/merchants/answer/16837055) and [the UCP integration hub](https://support.google.com/merchants/answer/16992327), Merchant Center Help, no date: early access, select merchants, build first then apply.
- [Google Pay UCP payment handler](https://developers.google.com/pay/api/universal-commerce-protocol/google-pay-payment-handler), Google for Developers, updated 2026-09-30: merchant id, tokenization.
- [Stripe and UCP for retail](https://docs.stripe.com/agentic-commerce/sellers/use-cases/retail/ucp), Stripe docs, no date: PaymentIntent-based UCP checkout.
- [ucp.dev](https://ucp.dev/), 2026: co-developers and endorsers; WooCommerce not listed.
- [UCP for WooCommerce](https://wordpress.org/plugins/universal-commerce-protocol-ucp-for-woocommerce/), WordPress.org, modified 2026-03-09: third-party, about 30 installs.
- [Agent Payments Protocol](https://cloud.google.com/blog/products/ai-machine-learning/announcing-agents-to-payments-ap2-protocol), Google Cloud blog, 2025-09-16.
- [Business Agent](https://support.google.com/merchants/answer/16410382), Merchant Center Help, no date: 50 approved listings; checkout "coming soon".

**Merchant Center and structured data**

- [Merchant Center requirements](https://support.google.com/merchants/answer/6363310), no date: working add-to-cart and checkout, contact details, return statement.
- [Products from your website](https://support.google.com/merchants/answer/12158480), no date: website crawl from Product markup. [Automatic item updates](https://support.google.com/merchants/answer/3246284), no date.
- [Content API for Shopping sunset](https://developers.google.com/shopping-content/guides/sunset), updated 2026-09-10: sunset 2026-08-18; HTTP 410 from 2026-09-01.
- [Return policies](https://support.google.com/merchants/answer/14011730), [shipping costs required](https://support.google.com/merchants/answer/6324484) and [shipping settings](https://support.google.com/merchants/answer/6069284), no date.
- [Tax no longer required in the US](https://support.google.com/merchants/answer/16098264), changes dated 2025-04-08 and 2025-07-01.
- [Price](https://support.google.com/merchants/answer/6324371), [brand](https://support.google.com/merchants/answer/6324351), [GTIN](https://support.google.com/merchants/answer/6324461), [identifier_exists](https://support.google.com/merchants/answer/6324478), [images](https://support.google.com/merchants/answer/6324350) (500 × 500 minimum from 2027-01-31), no date.
- [Dangerous products](https://support.google.com/merchants/answer/6150004) and [unsupported Shopping content](https://support.google.com/merchants/answer/6150006) (services, including bundled services), no date.
- [Merchant Center Next renamed](https://seroundtable.com/google-merchant-center-next-41658.html), Search Engine Roundtable (trade press), 2026-07-09.
- [Merchant listing structured data](https://developers.google.com/search/docs/appearance/structured-data/merchant-listing), Google Search Central, updated 2026-09-08.
- [Share your product data with Google](https://developers.google.com/search/docs/specialty/ecommerce/share-your-product-data-with-google), updated 2025-12-10.
- [More ways to share shipping](https://developers.google.com/search/blog/2025/11/more-ways-to-share-shipping), Search Central blog, November 2025; [shipping and returns in Search Console](https://www.seroundtable.com/google-search-console-shipping-return-without-merchant-center-40420.html), trade press, 2025-11-12.
- [Local inventory ads policies](https://support.google.com/merchants/answer/3271956) and [Business Profile products](https://support.google.com/business/answer/9934993), no date.
- [Google for WooCommerce](https://wordpress.org/plugins/google-listings-and-ads/), WordPress.org, v3.10.0 of 2026-10-20: Merchant API sync since 3.8.0; no UCP.

**ERPNext and WooCommerce connectors** (read in source with `gh api`)

- [frappe/erpnext PR #37015](https://github.com/frappe/erpnext/pull/37015), merged 2023-09-14: removes the WooCommerce integration; absent at tag `v16.52.1`.
- [Starktail/woocommerce_fusion](https://github.com/Starktail/woocommerce_fusion), branch `version-16`, v2.3.0 of 2026-08-17; [issue #271](https://github.com/Starktail/woocommerce_fusion/issues/271) (webhook signature not checked), [#276](https://github.com/Starktail/woocommerce_fusion/issues/276) (stock overwrite), [#255](https://github.com/Starktail/woocommerce_fusion/issues/255) and [#259](https://github.com/Starktail/woocommerce_fusion/issues/259) (tax mapping).
- [frappe/ecommerce_integrations](https://github.com/frappe/ecommerce_integrations), `version-16`, v16.0.0 of 2026-02-18: Shopify, Unicommerce, Zenoti, Amazon; no WooCommerce.
- [frappe/webshop](https://github.com/frappe/webshop), `version-16`, last commit 2026-09-15, no releases; [frappe/payments](https://github.com/frappe/payments), `version-16`: Stripe through the SDK and the Charges API.
- [libracore/WooCommerceConnector](https://github.com/libracore/WooCommerceConnector) (v12/v13) and [alyf-de/woocommerce_integration](https://github.com/alyf-de/woocommerce_integration) (v14/v15): no v16.

**Tax, payments and shipping**

- [Utah sales tax rates](https://tax.utah.gov/sales/rates) and [non-nexus sellers](https://tax.utah.gov/sales/non-nexus), Utah State Tax Commission, no date; [Tax Commission webinar](https://tax.utah.gov/train/webinars/2024-04-18-presentation.pdf), 2024-04-18: goods shipped out of Utah are not subject to Utah sales tax.
- [WooCommerce Tax](https://wordpress.org/plugins/woocommerce-services/), WordPress.org, v3.6.9 of 2026-07-20: free automated US sales tax at checkout.
- [Stripe Tax pricing](https://support.stripe.com/questions/understanding-stripe-tax-pay-as-you-go-pricing), Stripe Support, no date: 0.5% of volume on the no-code path.
- [UPS hazardous materials, fully regulated ground](https://www.ups.com/us/en/support/shipping-support/shipping-special-care-regulated-items/hazardous-materials-guide/49-cfr-shipping-examples/ground-fully-regulated), UPS, no date; [UPS chemical table](https://filexfer.ups.com/assets/resources/webcontent/en_US/chemical_table.pdf): calcium hypochlorite (5.1) and hydrochloric acid (8) shipping limits.

**Our own systems** (read-only, 2026-10-28): the live site's public pages, sitemap and the
WooCommerce Store API's public catalog endpoints; production ERPNext through the read-only
database tool; this repo's `stripe_payments/` README and source.
