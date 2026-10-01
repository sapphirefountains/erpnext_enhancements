// Drives the REAL Review Room app (public/js/design_review/, served unbundled as ES modules) in
// headless Chrome at /review, against a mock server built from a bundle, with real (DevTools
// Protocol) mouse and keyboard input. Prints a pass/fail line per check: routes as real paths,
// Back/Forward, sandboxed frames that keep their SVG and CSS, click-through inside the frames,
// ranking, verdicts, markup and notes, compare, phone width.
//
//   python scripts/design_review/qa/make_mock.py <bundle.json>
//   node scripts/design_review/qa/run_room.js
//
// SITE_CSS: optional comma-separated stylesheet URLs a real site loads before ours (website.bundle,
// erpnext-web, ...; copy them from any web page's <link>s). Served as /site.css, so frappe's
// bootstrap resets are in play the way they are on the site.
//
// Written against the Training review's bundle (tracks guide, entry, learner, canvas; option L3).
// Needs Chrome (CHROME env or the Windows default). Not in CI: CI has no bundle and no Chrome.
const path = require("path"), fs = require("fs"), http = require("http");
const cdp = require("./cdp.js");
const REPO = path.resolve(__dirname, "..", "..", "..", "erpnext_enhancements");
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".html": "text/html", ".woff2": "font/woff2" };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail });

function fileFor(url) {
  if (url === "/review" || url.startsWith("/review/")) return path.join(__dirname, "harness.html");
  if (url === "/mock.json") return path.join(__dirname, "mock.json");
  if (url.startsWith("/js/")) return path.join(REPO, "public", url);
  if (url.startsWith("/css/")) return path.join(REPO, "public", url);
  if (url === "/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2") return path.join(REPO, "public", "fonts", "big_noodle_titling.woff2");
  return null;
}

(async () => {
  let siteCss = "";
  for (const url of (process.env.SITE_CSS || "").split(",").filter(Boolean)) {
    siteCss += (await (await fetch(url)).text()) + "\n";
  }
  const srv = http.createServer((q, r) => {
    if (q.url === "/site.css") { r.setHeader("content-type", "text/css"); return r.end(siteCss); }
    const f = fileFor(decodeURIComponent(q.url.split("?")[0]));
    if (!f || !fs.existsSync(f)) { r.statusCode = 404; return r.end(); }
    r.setHeader("content-type", TYPES[path.extname(f)] || "application/octet-stream");
    r.end(fs.readFileSync(f));
  }).listen(8766);
  const b = await cdp.launch(9334);
  const p = await cdp.open(b, "about:blank", 1440, 1000);
  await p.goto("http://127.0.0.1:8766/review");
  await sleep(800);
  const route = () => p.eval("location.pathname");
  const center = async (expr) => p.eval(`(function(){var el=${expr};if(!el)return null;el.scrollIntoView({block:'center'});var r=el.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);
  // The centre of an element inside the first stage frame, in page coordinates (the frame is scaled).
  const inFrame = async (selector) => p.eval(`(function(){var f=document.querySelector('.rr-fbox iframe');if(!f)return null;var d=f.contentDocument;var el=d.querySelector(${JSON.stringify(selector)});if(!el)return null;var fr=f.getBoundingClientRect();var s=fr.width/f.offsetWidth;var r=el.getBoundingClientRect();return {x:fr.left+(r.left+r.width/2)*s,y:fr.top+(r.top+r.height/2)*s,nav:el.getAttribute('data-nav')}})()`);
  const clickText = async (selector, text) => {
    const at = await center(`Array.from(document.querySelectorAll(${JSON.stringify(selector)})).find(function(b){return b.textContent.trim().indexOf(${JSON.stringify(text)})===0})`);
    if (at) await p.click(at.x, at.y);
    return at;
  };

  // 1. list -> open
  const open = await center(`document.querySelector('.rr-review-card')`);
  check("the list shows the review", !!open);
  await p.click(open.x, open.y); await sleep(1200);
  check("opening lands on the first ranked track, as a real path", (await route()) === "/review/DR-2026-001/entry", await route());

  // 2. learner overview
  await clickText(".rr-seg button", "Learner"); await sleep(1500);
  check("a track is a route", (await route()) === "/review/DR-2026-001/learner", await route());
  const cards = await p.eval(`document.querySelectorAll('.rr-cards .rr-card').length`);
  check("five learner options", cards === 5, cards);
  await sleep(1200);
  const thumbs = await p.eval(`document.querySelectorAll('.rr-thumb iframe').length`);
  check("option thumbnails render in frames", thumbs >= 3, thumbs);
  check("the ranking lists the five", (await p.eval(`document.querySelectorAll('.rr-rank').length`)) === 5);
  const contrast = await p.eval(`(function(){var a=getComputedStyle(document.querySelector('.rr-title')).color,b=getComputedStyle(document.querySelector('.rr-card h2')).color,t=getComputedStyle(document.body).color;return {a:a,b:b,t:t}})()`);
  check("headings take the text colour (website CSS colours them)", contrast.a === contrast.t && contrast.b === contrast.t, contrast);
  await p.shot(path.join(__dirname, "room-track.png"));

  // 3. ranking: move the third up twice and save
  for (let k = 0; k < 2; k++) {
    const up = await center(`document.querySelectorAll('.rr-rank')[${2 - k}].querySelector('.rr-icon')`);
    await p.click(up.x, up.y);
  }
  await clickText(".rr-btn", "SAVE MY RANKING"); await sleep(900);
  const saved = await p.eval(`QA_CALLS.filter(function(c){return c.method==='cast_vote'}).map(function(c){return c.args.ranking})`);
  check("saving sends the ranking with L3 first", saved.length === 1 && JSON.parse(saved[0])[0] === "L3", saved);
  check("the tally shows one ranking", /1 ranked/.test(await p.eval(`document.querySelector('.rr-tally').textContent`)));

  // 4. open L3 -> first screen
  const thumbL3 = await center(`Array.from(document.querySelectorAll('.rr-card')).find(function(c){return /L3/.test(c.textContent)}).querySelector('.rr-thumb')`);
  await p.click(thumbL3.x, thumbL3.y); await sleep(1800);
  check("an option opens on its first screen", (await route()) === "/review/DR-2026-001/learner/L3/S01", await route());
  const sandbox = await p.eval(`Array.from(document.querySelectorAll('iframe')).map(function(f){return f.getAttribute('sandbox')})`);
  check("every frame is sandboxed without scripts", sandbox.length > 0 && sandbox.every((s) => s === "allow-same-origin"), sandbox);
  check("every frame carries the content policy", await p.eval(`Array.from(document.querySelectorAll('iframe')).every(function(f){return /default-src 'none'/.test(f.srcdoc)})`));
  const geometry = await p.eval(`(function(){var d=document.querySelector('.rr-fbox iframe').contentDocument;var paths=Array.from(d.querySelectorAll('path'));return {paths:paths.length,drawn:paths.filter(function(x){return (x.getAttribute('d')||'').length>2}).length,gap:Array.from(d.querySelectorAll('*')).some(function(e){var g=getComputedStyle(e).gap;return g&&g!=='normal'&&g!=='0px'})}})()`);
  check("SVG paths keep their geometry (the v1.570.0 bug)", geometry.paths > 0 && geometry.drawn === geometry.paths, geometry);
  check("CSS gap survives (the v1.570.0 bug)", geometry.gap, geometry);
  check("the kit font is loaded in the frame", await p.eval(`(function(){var d=document.querySelector('.rr-fbox iframe').contentDocument;return d.fonts.check('700 20px "Big Noodle Titling"')})()`));
  await p.shot(path.join(__dirname, "room-screen.png"));

  // 5. click-through inside the frame, then Back and Forward
  const door = await inFrame(`[data-nav]:not([data-nav^="done:"]):not([data-nav="up"])`);
  check("the screen has a linked spot", !!door, door);
  if (door) {
    await p.click(door.x, door.y); await sleep(1500);
    const moved = await route();
    check("clicking it inside the frame moves on", moved !== "/review/DR-2026-001/learner/L3/S01" && /^\/review\/DR-2026-001\//.test(moved), { moved, door });
    await p.eval("history.back()"); await sleep(1500);
    check("browser Back returns to the previous screen", (await route()) === "/review/DR-2026-001/learner/L3/S01", await route());
    await p.eval("history.forward()"); await sleep(1500);
    check("browser Forward goes again", (await route()) === moved, await route());
    await p.eval("history.back()"); await sleep(1500);
  }

  // 6. verdict
  await clickText(".rr-vb", "YES"); await sleep(800);
  check("a verdict is saved and shown", (await p.eval(`document.querySelector('.rr-vb.is-yes').getAttribute('aria-pressed')`)) === "true");
  check("the rail marks the screen", await p.eval(`!!document.querySelector('.rr-srow[aria-pressed="true"] .rr-vd.is-yes')`));

  // 7. markup -> select a part -> note
  await p.key("m"); await sleep(900);
  const tags = await p.eval(`document.querySelectorAll('.rr-pcode').length`);
  check("MARKUP tags the parts", tags > 0, tags);
  const tag = await center(`document.querySelector('.rr-pcode')`);
  await p.click(tag.x, tag.y); await sleep(500);
  const code = await p.eval(`(document.querySelector('.rr-sel-c')||{}).textContent`);
  check("clicking a tag selects its element code", /^L3-S01-E\d\d$/.test(code || ""), code);
  const area = await center(`document.querySelector('.rr-pbody textarea')`);
  await p.click(area.x, area.y); await p.type("Make this bigger");
  await clickText(".rr-btn", "ADD NOTE"); await sleep(1200);
  const notes = await p.eval(`document.querySelectorAll('.rr-pbody .rr-note').length`);
  check("the note appears under the part", notes === 1, notes);
  check("the rail counts it", (await p.eval(`(document.querySelector('.rr-srow[aria-pressed="true"] .rr-pip')||{}).textContent`)) === "1");
  check("the part is marked as noted inside the frame", await p.eval(`!!document.querySelector('.rr-fbox iframe').contentDocument.querySelector('[data-rr-has]')`));
  await p.key("m"); await sleep(500);

  // 8. compare
  await clickText(".rr-tab", "ALL"); await sleep(1800);
  check("ALL is a route", (await route()) === "/review/DR-2026-001/learner/all/S01", await route());
  check("compare shows all five options", (await p.eval(`document.querySelectorAll('.rr-cell').length`)) === 5);
  await p.eval("history.back()"); await sleep(1500);

  // 9. deep link and refresh
  await p.goto("http://127.0.0.1:8766/review/DR-2026-001/entry/E1/S02"); await sleep(1800);
  check("a deep link renders its screen", (await p.eval(`document.querySelector('.rr-capt h1').textContent`)).indexOf("E1") === 0);

  // 10. phone width
  await p.resize(390, 844); await p.goto("http://127.0.0.1:8766/review/DR-2026-001/learner/L3/S01"); await sleep(1800);
  const overflow = await p.eval(`document.documentElement.scrollWidth - window.innerWidth`);
  check("no sideways scroll at phone width", overflow <= 1, overflow);
  const fit = await p.eval(`Math.max.apply(null, Array.from(document.querySelectorAll('.rr-fbox')).map(function(b){return b.getBoundingClientRect().right}))`);
  check("frames fit the phone", fit <= 390, fit);
  await p.shot(path.join(__dirname, "room-phone.png"));

  // 11. light theme at desktop width, for the eye (both themes are tokens in the bundle CSS)
  await p.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: "light" }] });
  await p.resize(1440, 1000); await p.goto("http://127.0.0.1:8766/review/DR-2026-001/learner/L3/S04"); await sleep(1800);
  check("the bar is one row on a 1440 screen", (await p.eval(`document.querySelector('.rr-bar').getBoundingClientRect().height`)) < 60);
  await p.shot(path.join(__dirname, "room-light.png"));
  // The experience-map board was the screen v1.570.0 damaged most visibly on production.
  await p.goto("http://127.0.0.1:8766/review/DR-2026-001/guide/G/X5"); await sleep(2000);
  const board = await p.eval(`(function(){var f=document.querySelector('.rr-fbox iframe');var d=f&&f.contentDocument;return d?{svg:d.querySelectorAll('svg path[d],svg circle[r]').length,label:document.querySelector('.rr-frl').textContent}:null})()`);
  check("the X5 board draws its SVG", board && board.svg > 0, board);
  await p.shot(path.join(__dirname, "room-board.png"));

  check("no errors thrown in the page", p.errors().length === 0, p.errors());
  for (const r of results) console.log((r.ok ? "PASS " : "FAIL ") + r.name + (r.ok ? "" : "  -> " + JSON.stringify(r.detail)));
  console.log(results.filter((r) => r.ok).length + "/" + results.length + " passed");
  p.close(); srv.close(); b.proc.kill();
})().catch((e) => { console.error(e); process.exit(1); });
