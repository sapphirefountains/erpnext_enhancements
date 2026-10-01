// Drives the REAL Review Room page script (design_review/page/review_room/review_room.js) in headless
// Chrome against a mock server built from a bundle, with real (DevTools Protocol) mouse input, and
// prints a pass/fail line per check: routes, Back/Forward, sandboxed frames, pins, click-through
// inside the frames, ranking, verdicts, notes, phone width.
//
//   python scripts/design_review/qa/make_mock.py <bundle.json>
//   FRAPPE_DIR=../frappe node scripts/design_review/qa/run_room.js
//
// Uses the Training review's screens (L3, E1). Needs Chrome (CHROME env or the Windows default)
// and jQuery from a frappe checkout. Not in CI: CI has no bundle and no Chrome.
const path = require("path"), fs = require("fs"), http = require("http");
const cdp = require("./cdp.js");
const REPO = path.resolve(__dirname, "..", "..", "..", "erpnext_enhancements");
const FRAPPE_DIR = process.env.FRAPPE_DIR || path.resolve(REPO, "..", "..", "frappe");
const FILES = {
  "/": [path.join(__dirname, "harness.html"), "text/html"],
  "/jquery.js": [path.join(FRAPPE_DIR, "frappe/public/js/lib/jquery/jquery.min.js"), "text/javascript"],
  "/review_room.js": [REPO + "/design_review/page/review_room/review_room.js", "text/javascript"],
  "/mock.json": [path.join(__dirname, "mock.json"), "application/json"],
  "/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2": [REPO + "/public/fonts/big_noodle_titling.woff2", "font/woff2"],
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const check = (name, ok, detail) => results.push({ name, ok: !!ok, detail });

(async () => {
  const srv = http.createServer((q, r) => {
    const f = FILES[q.url.split("?")[0]];
    if (!f) { r.statusCode = 404; return r.end(); }
    r.setHeader("content-type", f[1]); r.end(fs.readFileSync(f[0]));
  }).listen(8766);
  const b = await cdp.launch(9334);
  const p = await cdp.open(b, "about:blank", 1440, 1000);
  await p.send("Network.enable");
  await p.goto("http://127.0.0.1:8766/#review-room");
  await sleep(800);
  const route = () => p.eval("location.hash");
  const center = async (expr) => p.eval(`(function(){var el=${expr};if(!el)return null;el.scrollIntoView({block:'center'});var r=el.getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()`);

  // 1. list -> open
  const open = await center(`document.querySelector('[data-open]')`);
  check("list shows the review", !!open);
  await p.click(open.x, open.y); await sleep(900);
  check("opening lands on the first track", (await route()) === "#review-room/DR-2026-001/guide", await route());

  // 2. learner track overview
  const tab = await center(`document.querySelector('[data-track="learner"]')`);
  await p.click(tab.x, tab.y); await sleep(1500);
  check("learner tab is a route", (await route()) === "#review-room/DR-2026-001/learner", await route());
  const cards = await p.eval(`document.querySelectorAll('[data-option]').length`);
  check("five learner options", cards === 5, cards);
  await sleep(1500);
  const thumbs = await p.eval(`document.querySelectorAll('.dr-thumb iframe').length`);
  check("option thumbnails render in frames", thumbs >= 3, thumbs);
  const rank = await p.eval(`document.querySelectorAll('.dr-rank-row').length`);
  check("ranking panel lists the five", rank === 5, rank);
  await p.shot(path.join(__dirname, "room-track.png"));

  // 3. ranking: move L3 up twice and save
  for (let k = 0; k < 2; k++) {
    const up = await center(`[...document.querySelectorAll('.dr-rank-row')].find(r=>r.textContent.includes('L3')).querySelector('button')`);
    await p.click(up.x, up.y); await sleep(200);
  }
  const save = await center(`document.querySelector('.dr-save')`);
  await p.click(save.x, save.y); await sleep(900);
  const vote = await p.eval(`JSON.stringify(CALLS.filter(c=>c.m==='cast_vote').pop())`);
  check("saving sends the ranking, L3 first", vote && JSON.parse(JSON.parse(vote).args.ranking)[0] === "L3", vote);

  // 4. open L3 -> first screen
  const optL3 = await center(`document.querySelector('[data-option="L3"]')`);
  await p.click(optL3.x, optL3.y); await sleep(2200);
  check("an option opens on its first screen", (await route()) === "#review-room/DR-2026-001/learner/L3/S01", await route());
  const sandboxes = await p.eval(`[...document.querySelectorAll('.dr-frames iframe')].map(f=>f.getAttribute('sandbox'))`);
  check("frames are sandboxed allow-same-origin only", sandboxes.length >= 1 && sandboxes.every((s) => s === "allow-same-origin"), sandboxes);
  const pickBtn = await center(`document.querySelector('[data-mode="annotate"]')`);
  await p.click(pickBtn.x, pickBtn.y); await sleep(500);
  const pins = await p.eval(`document.querySelectorAll('.dr-pin').length`);
  check("pins are drawn from the frozen codes", pins > 5, pins);
  const outside = await p.eval(`[...document.querySelectorAll('.dr-slot')].map(sl=>{var b=sl.getBoundingClientRect();return [...sl.querySelectorAll('.dr-pin')].filter(pn=>{var r=pn.getBoundingClientRect();return r.top>b.bottom||r.left>b.right}).length}).reduce((a,b)=>a+b,0)`);
  check("no pin is drawn outside its frame", outside === 0, outside);
  const navs = await p.eval(`document.querySelector('.dr-frames iframe').contentDocument.querySelectorAll('[data-nav]').length`);
  check("click-through rules marked links in the frame", navs > 2, navs);
  const sideBySide = await p.eval(`(function(){var s=[...document.querySelectorAll('.dr-slot')];return s.length<2||Math.abs(s[0].getBoundingClientRect().top-s[1].getBoundingClientRect().top)<2})()`);
  check("desktop and phone frames sit side by side on a wide screen", sideBySide);
  const strip = await p.eval(`[...document.querySelectorAll('.dr-strip .dr-stage')].map(x=>x.textContent)`);
  check("the strip shows each stage once", new Set(strip).size === strip.length, strip);
  await p.shot(path.join(__dirname, "room-screen.png"));
  const clickBtn = await center(`document.querySelector('[data-mode="click"]')`);
  await p.click(clickBtn.x, clickBtn.y); await sleep(500);
  check("clicking through hides pins on parts without notes", (await p.eval(`document.querySelectorAll('.dr-pin').length`)) === 0);

  // 5. real click inside the frame on a linked control
  const target = await p.eval(`(function(){
    var f=[...document.querySelectorAll('.dr-frames iframe')].pop(); var d=f.contentDocument; var slot=f.parentElement.getBoundingClientRect();
    var s=parseFloat(f.style.transform.replace('scale(','')); var el=[...d.querySelectorAll('a[data-nav],button[data-nav]')].find(e=>/RESUME|CONTINUE/.test(e.textContent.toUpperCase()));
    if(!el) return null; var r=el.getBoundingClientRect(); return {x:slot.left+(r.left+r.width/2)*s, y:slot.top+(r.top+r.height/2)*s, to:el.getAttribute('data-nav'), label:el.textContent.trim()};})()`);
  check("a linked RESUME/CONTINUE control exists", !!target, target);
  if (target) {
    await p.click(target.x, target.y); await sleep(2000);
    check("clicking it inside the frame moves to the lesson", (await route()) === "#review-room/DR-2026-001/learner/L3/S04", JSON.stringify({ route: await route(), target }));
    // 6. Back
    await p.eval("history.back()"); await sleep(1800);
    check("browser Back returns to the previous screen", (await route()) === "#review-room/DR-2026-001/learner/L3/S01", await route());
    await p.eval("history.forward()"); await sleep(1800);
    check("browser Forward goes again", (await route()) === "#review-room/DR-2026-001/learner/L3/S04", await route());
  }

  // 7. pick a part by its pin and add a note
  const pick2 = await center(`document.querySelector('[data-mode="annotate"]')`);
  await p.click(pick2.x, pick2.y); await sleep(500);
  const pin = await center(`document.querySelector('.dr-pin')`);
  await p.click(pin.x, pin.y); await sleep(400);
  const selected = await p.eval(`(document.querySelector('.dr-notes-panel .dr-code')||{}).textContent`);
  check("a pin selects its part", /^L3-S04-E\d\d$/.test(selected || ""), selected);
  await p.eval(`document.querySelector('.dr-notes-panel textarea').value='Make the video bigger'`);
  const add = await center(`[...document.querySelectorAll('.dr-notes-panel button')].find(b=>b.textContent.trim()==='Add note')`);
  await p.click(add.x, add.y); await sleep(1500);
  const note = await p.eval(`JSON.stringify(CALLS.filter(c=>c.m==='add_note').pop())`);
  check("the note is sent with that element code", note && JSON.parse(note).args.code === selected, note);

  // 8. verdict
  const yes = await center(`document.querySelector('[data-verdict="Yes"]')`);
  await p.click(yes.x, yes.y); await sleep(900);
  const verdict = await p.eval(`JSON.stringify(CALLS.filter(c=>c.m==='cast_verdict').pop())`);
  check("a verdict is sent for this option and screen", verdict && JSON.parse(verdict).args.screen_code === "S04" && JSON.parse(verdict).args.verdict === "Yes", verdict);

  // 9. cross-track: entry E1 chooser, Learn door -> learner L1 S01
  await p.goto("http://127.0.0.1:8766/#review-room/DR-2026-001/entry/E1/S02"); await sleep(2500);
  check("the chosen mode is remembered across a reload", (await p.eval(`document.querySelector('[data-mode="annotate"]').classList.contains('active')`)));
  const clickMode = await center(`document.querySelector('[data-mode="click"]')`);
  await p.click(clickMode.x, clickMode.y); await sleep(400);
  const door = await p.eval(`(function(){
    var f=[...document.querySelectorAll('.dr-frames iframe')][0]; var d=f.contentDocument; var slot=f.parentElement.getBoundingClientRect();
    var s=parseFloat(f.style.transform.replace('scale(','')); var el=d.querySelector('[data-c="Learn door"]');
    if(!el) return null; var r=el.getBoundingClientRect(); return {x:slot.left+(r.left+r.width/2)*s, y:slot.top+(r.top+r.height/2)*s, to:el.getAttribute('data-nav')};})()`);
  check("the Learn door is linked", door && door.to === "learner:S01", door);
  if (door) {
    await p.click(door.x, door.y); await sleep(2500);
    check("the Learn door crosses to the learner app, same option position", (await route()) === "#review-room/DR-2026-001/learner/L1/S01", await route());
  }

  // 10. phone width
  await p.resize(400, 860); await sleep(1200);
  const overflow = await p.eval(`document.documentElement.scrollWidth - window.innerWidth`);
  check("no sideways scroll at phone width", overflow <= 1, overflow);
  await p.shot(path.join(__dirname, "room-phone.png"));

  check("no script errors", (await p.eval("ERRORS.length")) === 0, await p.eval("ERRORS"));
  check("no errors thrown in the page", p.errors().length === 0, p.errors());
  for (const r of results) console.log((r.ok ? "PASS " : "FAIL ") + r.name + (r.ok ? "" : "  -> " + JSON.stringify(r.detail)));
  console.log(results.filter((r) => r.ok).length + "/" + results.length + " passed");
  p.close(); srv.close(); b.proc.kill();
})().catch((e) => { console.error(e); process.exit(1); });
