#!/usr/bin/env node
/**
 * Does an open desk menu actually paint on top? Measured, not reasoned about.
 *
 * NOT WIRED TO CI, AND THAT IS THE POINT OF THIS HEADER. It needs a real browser
 * engine, which the CI runners do not have; the contract that CI *can* check
 * lives in `erpnext_enhancements/tests/test_dropdown_stacking.py`. Run this by
 * hand whenever you touch the glass theme, `.page-head`, `.btn`, or anything
 * that sets `backdrop-filter`, `transform`, `filter`, `opacity`, `will-change`,
 * `contain`, `isolation` or a `position`+`z-index` pair on a Frappe core
 * element:
 *
 *     npm i --no-save playwright-core
 *     node scripts/probe_menu_stacking.mjs                 # the working tree
 *     node scripts/probe_menu_stacking.mjs --ref origin/main
 *
 * Exits non-zero if any menu is covered. On this repo's remote sessions Chromium
 * is already at $PLAYWRIGHT_BROWSERS_PATH; pass --chromium <path> otherwise.
 *
 * WHY A PROBE AND NOT AN ASSERTION ABOUT CSS TEXT
 * ----------------------------------------------
 * The bug this exists for is a *stacking context* bug: a menu sealed inside an
 * ancestor that carries `backdrop-filter`, so the menu's own z-index is resolved
 * inside the context that is losing and cannot rescue it. Nothing about that is
 * visible in the declaration you are reading -- it depends on three stylesheets,
 * the DOM nesting Frappe chose, and which pseudo-class is active. The only
 * question worth asking is "what does the browser paint on top", and
 * `document.elementFromPoint` over the open menu answers exactly that.
 *
 * It found two live traps the app had shipped, and it also *corrected* a wrong
 * diagnosis: a first run said bootstrap's `.btn-group > .btn:focus { z-index: 1 }`
 * was a third live trap, which was an artefact of this file not yet carrying
 * Frappe's own counter-rule at list.scss:547-554. Which is the standing warning:
 *
 * THE FRAPPE SIDE OF THE PAGE BELOW IS A TRANSCRIPTION, AND A TRANSCRIPTION CAN
 * BE INCOMPLETE. Every value in the `#frappe` block is copied from
 * frappe/frappe at branch **version-16** with its source file and line beside
 * it, because the sibling ../frappe checkout is v17 and lies about production
 * (CLAUDE.md). A rule that is missing here is a trap this reports that does not
 * exist, or misses one that does. On a Frappe upgrade, re-derive it:
 *
 *     git show origin/version-16:frappe/public/scss/desk/page.scss
 *     curl -sS https://raw.githubusercontent.com/frappe/frappe/version-16/<path>
 *
 * WHAT IT CHECKS
 * --------------
 *   query report + page "..." menu          (the General Ledger report)
 *   list view    + page "..." menu          (control: was never broken)
 *   list view    + sort menu, in four button states: idle / hover / focus / both
 *                                           (the <ul> is INSIDE the <button>, so
 *                                            button state decides the stacking)
 */

import { readFileSync, writeFileSync, mkdtempSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const PAGE = String.raw`<!doctype html>
<html><head><meta charset="utf-8"><title>frappe v16 stacking repro</title>
<style id="frappe">
/* ===== bootstrap 4.6.2 (the parts frappe relies on) ===== */
.dropdown-menu { position: absolute; z-index: 1000; display: none; float: left;
  min-width: 10rem; padding: .5rem 0; margin: .125rem 0 0; background-color: #fff;
  border: 1px solid rgba(0,0,0,.15); border-radius: .25rem; }
.dropdown-menu.show { display: block; }
.dropdown-menu-right { right: 0; left: auto; }
.btn-group { position: relative; display: inline-flex; vertical-align: middle; }
.btn-group > .btn { position: relative; flex: 1 1 auto; }
.btn-group > .btn:focus, .btn-group > .btn:active { z-index: 1; }
.modal { position: fixed; top: 0; left: 0; z-index: 1050; display: none; width: 100%; height: 100%; }
.modal-backdrop { position: fixed; top: 0; left: 0; z-index: 1040; width: 100vw; height: 100vh; background: #000; }
.sticky-top { position: sticky; top: 0; z-index: 1020; }

/* ===== frappe v16 desk (verbatim) ===== */
body { margin: 0; display: flex; flex-direction: row; flex-wrap: nowrap;
       align-items: flex-start; justify-content: flex-start; position: relative; }
.main-section { width: 100%; height: 100vh; overflow: scroll; overflow-x: hidden; }
.sticky-top { z-index: 1019; }                                   /* main.scss:45-47 */
.navbar { height: 60px; background: #fff; border-bottom: 1px solid #ddd; }
.page-head { z-index: 6; position: sticky; background: #fff;     /* page.scss:119-126 */
             border-bottom: 1px solid #ddd; transition: .5s top; top: 0; }
.page-head-content { height: 60px; padding: 8px 0; gap: 10px; display: flex;
                     justify-content: space-between; align-items: center; }
.page-actions { align-items: center; display: flex; }
.menu-btn-group, .actions-btn-group { display: flex; }           /* page.scss:171-181 */
.menu-btn-group .dropdown-menu, .actions-btn-group .dropdown-menu { width: max-content; }
#page-query-report .page-head { position: unset; }               /* report.scss:2-5 */
#page-query-report .page-form { position: relative; z-index: 5; }/* report.scss:7-10 */
.page-form { margin: 0; padding: 4px 15px; display: flex; flex-wrap: wrap;
             background-color: #fff; }                            /* page.scss:134-139 */
.form-tabs-list { position: sticky; background: #fff; z-index: 5; } /* form.scss:484-487 */
.filter-popover { z-index: 1019; }                                /* filters.scss:5-11 */
/* list.scss:1-16 */
.layout-main-section-wrapper:not(.disable-scrolling) .frappe-list .result-container
  .result .list-row-container:first-child { position: sticky; top: 0; z-index: 2; }
.list-row-container { background: #eee; padding: 10px 15px; border-bottom: 1px solid #ddd; }
.datatable .dt-header { position: sticky; top: 0; z-index: 2; background: #e9e9e9; }
/* list.scss:547-554 — frappe's OWN guard against bootstrap's focus z-index,
   scoped to a sort selector that sits inside .page-form */
.page-form .sort-selector .btn-group .btn:focus { z-index: unset; }
.datatable { background: #fff; }
.dt-row { height: 35px; border-bottom: 1px solid #eee; padding: 8px; }
.btn { padding: 4px 8px; border: 1px solid #ccc; background: #f5f5f5; border-radius: 6px; }
.report-wrapper { min-height: 900px; }
/* mobile.scss:12-24 */
@media (max-width: 991px) { .layout-main { position: relative; } }
.frappe-list { min-height: 900px; }
</style>


<!-- the app stylesheet under test is injected here by the probe -->
<style id="app"></style>
</head>
<body>
<div class="main-section">
  <header class="navbar sticky-top"><div class="container">navbar</div></header>
  <div id="page-container">
    <div class="page-head flex">
      <div class="container">
        <div class="page-head-content">
          <div class="page-title">General Ledger</div>
          <div class="page-actions flex">
            <div class="standard-actions flex">
              <div class="menu-btn-group">
                <button type="button" class="btn btn-default icon-btn menu-more-button" data-toggle="dropdown">…</button>
                <ul id="page-menu" class="dropdown-menu dropdown-menu-right show" role="menu">
                  <li><a class="dropdown-item">Actions &gt; Create Card</a></li>
                  <li><a class="dropdown-item">Set Chart</a></li>
                  <li><a class="dropdown-item">Add Column</a></li>
                  <li><a class="dropdown-item">Save</a></li>
                  <li><a class="dropdown-item">Print</a></li>
                  <li><a class="dropdown-item">PDF</a></li>
                  <li><a class="dropdown-item">Export</a></li>
                  <li><a class="dropdown-item">Setup Auto Email</a></li>
                  <li><a class="dropdown-item">User Permissions</a></li>
                  <li><a class="dropdown-item">Add to Desk</a></li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="container page-body">
      <div class="page-wrapper"><div class="page-content"><div class="layout-main">
        <div class="layout-main-section-wrapper"><div class="layout-main-section">

          <div class="page-form row">
            <div class="standard-filter-section flex">Company: Sapphire Fountains</div>
            <div class="filter-section flex">
              <div class="sort-selector">
                <div class="btn-group">
                  <button class="btn btn-default btn-sm btn-order">↓</button>
                  <button type="button" class="btn btn-default btn-sm sort-selector-button" data-toggle="dropdown">
                    <span class="dropdown-text">Last Updated On</span>
                    <ul id="sort-menu" class="dropdown-menu dropdown-menu-right show">
                      <li><a class="dropdown-item option">Last Updated On</a></li>
                      <li><a class="dropdown-item option">Created On</a></li>
                      <li><a class="dropdown-item option">ID</a></li>
                      <li><a class="dropdown-item option">Modified By</a></li>
                      <li><a class="dropdown-item option">Item Name</a></li>
                    </ul>
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- LIST body -->
          <div class="frappe-list" id="list-body">
            <div class="result-container"><div class="result">
              <div class="list-row-container" id="list-header">ID | Item Name | Status</div>
              <div class="list-row-container">ITEM-0001</div>
              <div class="list-row-container">ITEM-0002</div>
              <div class="list-row-container">ITEM-0003</div>
              <div class="list-row-container">ITEM-0004</div>
              <div class="list-row-container">ITEM-0005</div>
            </div></div>
          </div>

          <!-- REPORT body -->
          <div class="report-wrapper" id="report-body" style="display:none">
            <div class="datatable">
              <div class="dt-header" id="dt-header">Posting Date | Account | Debit</div>
              <div class="dt-row">09-08-2026 | 1410 - Stock In Hand | $3,000</div>
              <div class="dt-row">09-08-2026 | 2210 - Stock Received</div>
              <div class="dt-row">09-09-2026 | 1410 - Stock In Hand | $500</div>
              <div class="dt-row">09-09-2026 | 2210 - Stock Received</div>
              <div class="dt-row">09-11-2026 | 1410 - Stock In Hand | $200</div>
            </div>
          </div>

        </div></div>
      </div></div></div>
    </div>
  </div>
</div>
</body></html>
`

const REPO = join(dirname(fileURLToPath(import.meta.url)), '..')
const STYLESHEET = 'erpnext_enhancements/public/css/desk_enhancements.bundle.css'

const args = process.argv.slice(2)
const argOf = (name) => {
	const i = args.indexOf(name)
	return i === -1 ? null : args[i + 1]
}
const ref = argOf('--ref')
const chromium_path =
	argOf('--chromium') ||
	`${process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers'}/chromium-1194/chrome-linux/chrome`

const css = ref
	? execFileSync('git', ['-C', REPO, 'show', `${ref}:${STYLESHEET}`], { encoding: 'utf8', maxBuffer: 1 << 26 })
	: readFileSync(join(REPO, STYLESHEET), 'utf8')

let chromium
try {
	;({ chromium } = await import('playwright-core'))
} catch {
	// Deliberately not a dependency: nothing else in this repo drives a browser,
	// and `npm ci` runs on every CI job.
	console.error('playwright-core is not installed. Run:  npm i --no-save playwright-core')
	process.exit(2)
}

const dir = mkdtempSync(join(tmpdir(), 'menu-stacking-'))
const file = join(dir, 'page.html')
writeFileSync(file, PAGE)

const CASES = [
	['query report', 'page menu', { pageId: 'page-query-report', show: 'report-body', hide: 'list-body', menuId: 'page-menu' }],
	['list view', 'page menu', { pageId: 'page-List/Item/List', show: 'list-body', hide: 'report-body', menuId: 'page-menu' }],
	['list view', 'sort menu', { pageId: 'page-List/Item/List', show: 'list-body', hide: 'report-body', menuId: 'sort-menu' }],
]

const browser = await chromium.launch({ executablePath: chromium_path })
const context = await browser.newContext({ viewport: { width: 414, height: 896 } })
const page = await context.newPage()

let failures = 0
console.log(`probing ${ref ? `${ref}:${STYLESHEET}` : 'the working tree'}\n`)

for (const [pageName, menuName, cfg] of CASES) {
	// Button state only matters where the <ul> lives INSIDE the button. The page
	// menu's <ul> is a SIBLING of its button, so no state on it can contain one.
	const states = cfg.menuId === 'sort-menu' ? ['idle', 'hover', 'focus', 'hover+focus'] : ['idle']
	for (const state of states) {
		await page.goto('file://' + file)
		await page.evaluate(([cfg, css]) => {
			document.getElementById('app').textContent = css
			document.getElementById('page-container').id = cfg.pageId
			document.getElementById(cfg.show).style.display = ''
			document.getElementById(cfg.hide).style.display = 'none'
			for (const m of document.querySelectorAll('.dropdown-menu')) m.classList.remove('show')
			document.getElementById(cfg.menuId).classList.add('show')
		}, [cfg, css])

		const button = cfg.menuId === 'sort-menu' ? '.sort-selector-button' : '.menu-more-button'
		if (state.includes('focus')) await page.evaluate((b) => document.querySelector(b).focus(), button)
		// force: the open menu covers the button, which is the whole point
		if (state.includes('hover')) await page.locator(button).hover({ force: true })
		// `.btn` carries `transition: all 0.3s ease`, so a backdrop-filter switched
		// off ANIMATES out -- and mid-animation the element still has one, and so
		// still forms a stacking context. Reading before it settles reports the
		// old answer. This cost an hour the first time.
		await page.waitForTimeout(600)

		const result = await page.evaluate((menuId) => {
			const menu = document.getElementById(menuId)
			const box = menu.getBoundingClientRect()
			const hits = [0.05, 0.25, 0.5, 0.75, 0.95].map((f) => {
				const el = document.elementFromPoint(box.left + box.width / 2, box.top + box.height * f)
				if (!el) return 'OUTSIDE-VIEWPORT'
				return menu.contains(el) || el === menu ? 'MENU' : el.id || el.className || el.tagName
			})
			return { hits, onTop: hits.every((h) => h === 'MENU') }
		}, cfg.menuId)

		if (!result.onTop) failures++
		console.log(
			`  ${(result.onTop ? 'PASS' : 'FAIL').padEnd(5)} ${pageName.padEnd(13)} ${menuName.padEnd(10)} ` +
				`${state.padEnd(12)} ${result.onTop ? '' : 'covered by ' + JSON.stringify(result.hits)}`
		)
	}
}

await browser.close()
console.log(`\n${failures === 0 ? 'all menus paint on top' : `${failures} menu(s) painted behind the page`}`)
process.exit(failures === 0 ? 0 : 1)
