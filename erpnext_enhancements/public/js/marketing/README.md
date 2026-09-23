# `public/js/marketing/`: the /marketing app

TASK-2026-01487, decision 8: a daily-driver tool for a non-technical marketing hire that keeps
them out of the Desk. The shell is [`www/marketing.html`](../../../www/marketing.html); the server
surface is `marketing/publish/spa.py` (the app's endpoints) plus `approval.py` (the four post
actions). Built on the feedback SPA's pattern, which inherited it from the chat SPA retired in
v1.426.0.

## What it does

| Surface | Route | Reads | Writes |
|---|---|---|---|
| Calendar (month / week), unscheduled drafts, IG and YouTube quota | `/marketing`, `/marketing/calendar/YYYY-MM`, `/marketing/week/YYYY-MM-DD` | `get_calendar` | `reschedule` (drag a post to another day) |
| Composer: accounts, text, link, media, YouTube fields, time, per-network preview, live checks | `/marketing/new[/YYYY-MM-DD]`, `/marketing/post/NAME` | `get_post`, `check_post` | `save_post`, `submit_for_approval`, `approve`, `send_back`, `cancel`, `delete_post` |
| Media library and picker, with upload | `/marketing/media`, and a dialog in the composer | `list_media` | `create_asset` |
| Approval queue | `/marketing/queue` | `approval_queue` | `send_back` |
| Results: job state, link, attempt log, engagement | `/marketing/post/NAME/results` | `get_results` | none, on purpose (below) |

The results page is read-only on purpose. An **Unconfirmed** job may already be live, and it is
answered on the job's Desk form by someone who has looked at the network
(`sweeper.resolve_job`). A button here would answer without looking.

## Modules

| File | What it is |
|---|---|
| `routes.js` | **Pure.** URL ↔ view. Every route survives a round trip, so every link the app builds survives a refresh |
| `calendar.js` | **Pure.** Day arithmetic on `YYYY-MM-DD` strings and UTC dates. **Never the browser's time zone**: `scheduled_at` is the site's local time, and "today" comes from the server |
| `composer.js` | **Pure.** Counters, the `save_post` payload, and the per-network preview, which mirrors what the publishers send (Instagram never gets the link; Facebook and LinkedIn put it in the text only beside media; YouTube appends it) |
| `transport.js` | One `fetch` wrapper, every call a POST with the CSRF header; the `M` endpoint map; the unattached upload |
| `dom.js` | Node builders (`el`, `fill`, …), pills with a glyph as well as a colour, `outLink` (http(s) or plain text), `assetThumb`, `dialog` |
| `app.js` | Layout, router, notices, and **the one placeholder writer** (`showPlaceholder`) |
| `view_*.js` | One surface each, as in the table above |

## The rules, and what enforces them

`scripts/test_marketing_source_rules.js` fails the build on each of these:

- **No `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write`, anywhere.** Captions,
  comments, asset titles and alt text are typed by one employee and shown to another, the
  approver among them.
- **No Vue** (the Desk's `window.Vue` never meets this document) and **no `frappe.*`** (a website
  route does not load the Desk bundle).
- **Cross-module names resolve.** A name used but never imported compiles to a bare global and
  throws at load, which esbuild cannot see.
- **No state renders nothing.** Every list renderer branches on the empty case and reaches
  `app.showPlaceholder`. An emptied list reads as a broken page.
- **A surface that reads shared state also writes it**, or is listed as read-only with its reason.
- **What is approved is what was reviewed.** Approve, save and reschedule send the `modified` the
  page loaded; the server refuses a post that changed underneath them. Approve also stops while
  the page holds unsaved changes: an approver who edits becomes the last editor, and somebody
  else must approve.
- **`--ee-brand` (#00a0dd) never carries text**: 2.97:1 against white both ways. `--ee-brand-ink`
  for text, `--ee-brand-surface` under white text.
- **Every heading class sets its colour**, because Frappe's fixed `--heading-color` otherwise wins
  on the dark theme. **The stylesheet's comments balance**, because esbuild ships broken CSS
  with only a warning.
- **The bundle stays under its gzip ceiling** (below).

`scripts/test_marketing_client.js` checks the pure modules, including the calendar under three
other `TZ` values.

## Size

Measured 2026-09-22 on raw source, and on code only (comments stripped), since these files are
comment-dense and only the code ships after esbuild. **None of it loads on the Desk**: the
bundle is referenced only by the website route.

| | raw | gzipped |
|---|---|---|
| `view_composer.js` | 23,945 | 7,203 |
| `view_media.js` | 9,693 | 3,642 |
| `view_calendar.js` | 9,145 | 3,303 |
| `app.js` | 7,688 | 2,838 |
| `dom.js` | 7,663 | 2,842 |
| `composer.js` | 7,168 | 2,587 |
| `transport.js` | 6,108 | 2,658 |
| `calendar.js` | 5,252 | 2,096 |
| `view_results.js` | 4,233 | 1,791 |
| `routes.js` | 3,892 | 1,451 |
| `view_queue.js` | 3,349 | 1,456 |
| **the whole client, code only** | **~70.7 KB** | **~19.7 KB** |
| `marketing.bundle.css` | ~21.2 KB | ~3.3 KB |

The source-rule check holds the code-only gzip under **24 KB**. Raising that is fine, but do it
in the same commit as the reason.

## Running the checks

```bash
node scripts/test_marketing_client.js
node scripts/test_marketing_source_rules.js
python -m unittest erpnext_enhancements.tests.test_marketing_spa
```

No runner and no `npm install`. Node prints a `MODULE_TYPELESS_PACKAGE_JSON` warning when it
imports these ES modules; **do not silence it by adding `"type": "module"` to `package.json`**,
which would turn the repo's CommonJS guards into parse errors.
