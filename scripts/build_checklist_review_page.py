"""Generate the strawman review page from the same data the patch seeds.

Generated rather than hand-written so the page and the seed cannot disagree: if a check is on
the page it is in the catalog, and the counts are counted rather than claimed.
"""

import html
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve()
for parent in REPO.parents:
	if (parent / "erpnext_enhancements").is_dir():
		sys.path.insert(0, str(parent))
		break
else:
	raise SystemExit("run this from inside the erpnext_enhancements checkout")

from erpnext_enhancements.quality.catalog import COMMISSIONING_CHECKS, COMMISSIONING_TEMPLATE_NAME
from erpnext_enhancements.quality.draft_catalog import (
	DRAFT_SECTIONS,
	DRAFT_TEMPLATES,
	UNSOURCED_SECTIONS,
)

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("inspection_checklists_review.html")

SECTIONS = {s[0]: s for s in DRAFT_SECTIONS}

#: How many templates use each section. A section used by three templates is corrected once.
USED_BY = {}
for _t in DRAFT_TEMPLATES:
	for _s in _t[3]:
		USED_BY[_s] = USED_BY.get(_s, 0) + 1

GROUP_NOTE = {
	"Service": (
		"Lifted from Sapphire's own maintenance sections, live on production since June. "
		"The chemistry ranges are yours verbatim.",
		"Sapphire Maintenance Section records",
	),
	"Design": (
		"Every gate asks whether the Water Feature Design record already says what it needs to "
		"say, rather than re-deriving the calculation on a form.",
		"water_engineering: Water Feature Design",
	),
	"Products": (
		"Keyed to the Control Panel Design record: NEMA rating, controller hardware, fuse and "
		"interlock schedules, safe state on power-up.",
		"water_engineering: Control Panel Design",
	),
	"Build": (
		"The three Build milestones left without a template. Drafted from the milestone "
		"descriptions and ordinary construction-inspection practice.",
		"partly drafted",
	),
	"Events": (
		"Nothing in the system describes an event setup. This set is drafted from the milestone "
		"descriptions and general practice, and it is the one to read hardest.",
		"no internal source",
	),
}

ORDER = ["Service", "Design", "Products", "Build", "Events"]

E = html.escape


def chip(text, kind=""):
	return f'<span class="chip {kind}">{E(text)}</span>'


def render_item(n, item):
	(label, criteria, check_type, method, uom, lo, hi, mandatory, photo, reference) = item
	flags = []
	if mandatory:
		flags.append(chip("Required", "req"))
	if photo:
		flags.append(chip("Photo if failed", "photo"))
	meta = [chip(check_type, "type")]
	if uom:
		rng = f"{lo:g}–{hi:g} {uom}" if (lo or hi) else uom
		meta.append(f'<span class="range">{E(rng)}</span>')
	src = (
		f'<div class="src"><span class="src-dot"></span>{E(reference)}</div>'
		if reference
		else '<div class="src src-none"><span class="src-dot"></span>no internal source</div>'
	)
	return f"""<li class="check">
  <div class="check-n">{n}</div>
  <div class="check-body">
    <p class="check-label">{E(label)}</p>
    <p class="check-std"><span class="std-key">Passes when</span> {E(criteria)}</p>
    <p class="check-how"><span class="std-key">How</span> {E(method)}</p>
    {src}
  </div>
  <div class="check-meta">{"".join(meta)}{"".join(flags)}</div>
</li>"""


def render_section(title, index):
	_t, section_type, description, _instr, items = SECTIONS[title]
	unsourced = title in UNSOURCED_SECTIONS
	rows = "\n".join(render_item(i + 1, item) for i, item in enumerate(items))
	uses = USED_BY.get(title, 1)
	shared = chip(f"shared by {uses} templates", "shared") if uses > 1 else ""
	return f"""<section class="sec {'sec-unsourced' if unsourced else ''}">
  <header class="sec-head">
    <h4>{E(title)}</h4>
    <div class="sec-meta">{shared}{chip(section_type, 'type')}<span class="count">{len(items)} checks</span></div>
  </header>
  <p class="sec-desc">{E(description)}</p>
  <ol class="checks">{rows}</ol>
</section>"""


def render_template(tpl):
	name, project_type, milestone_key, section_titles, _safety, _wrapup = tpl
	total = sum(len(SECTIONS[t][4]) for t in section_titles)
	secs = "\n".join(render_section(t, i) for i, t in enumerate(section_titles))
	return f"""<article class="tpl" id="{E(milestone_key)}">
  <header class="tpl-head">
    <h3>{E(name)}</h3>
    <div class="tpl-meta">
      <code>{E(milestone_key)}</code>
      <span class="count">{total} checks</span>
      <span class="status">Draft</span>
    </div>
  </header>
  {secs}
</article>"""


def render_group(project_type):
	tpls = [t for t in DRAFT_TEMPLATES if t[1] == project_type]
	note, source = GROUP_NOTE[project_type]
	titles = {s for t in tpls for s in t[3]}
	checks = sum(len(SECTIONS[s][4]) for s in titles)
	unsourced = project_type == "Events"
	extra = ""
	if project_type == "Build":
		extra = f"""<div class="callout">
      <p><strong>{E(COMMISSIONING_TEMPLATE_NAME)}</strong> is not in this review and is not a
      strawman. It is already <em>Active</em>, and its {len(COMMISSIONING_CHECKS)} commissioning
      checks came from the KPI design document — written by somebody who knew the trade.</p>
    </div>"""
	return f"""<div class="group {'group-unsourced' if unsourced else ''}" id="g-{E(project_type.lower())}">
  <header class="group-head">
    <h2>{E(project_type)}</h2>
    <div class="group-facts">
      <span class="count">{len(tpls)} templates</span>
      <span class="count">{checks} checks</span>
      <span class="provenance {'prov-none' if unsourced else 'prov-ok'}">{E(source)}</span>
    </div>
  </header>
  <p class="group-note">{E(note)}</p>
  {extra}
  {"".join(render_template(t) for t in tpls)}
</div>"""


total_checks = sum(len(s[4]) for s in DRAFT_SECTIONS)
unsourced_checks = sum(len(SECTIONS[t][4]) for t in UNSOURCED_SECTIONS)
sourced_checks = total_checks - unsourced_checks

nav = "".join(
	f'<a href="#g-{p.lower()}">{p}</a>' for p in ORDER
)

body = "\n".join(render_group(p) for p in ORDER)

HTML = f"""<title>Strawman Inspection Checklists</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,600;1,6..72,400&family=Public+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root {{
  --ground: #f6f8f9;
  --surface: #ffffff;
  --ink: #12262e;
  --body: #274049;
  --muted: #5d7480;
  --rule: #d7e1e5;
  --rule-soft: #e7eef0;
  --accent: #0d6b7d;
  --accent-soft: #e4f0f2;
  --sourced: #2f6b4f;
  --sourced-soft: #e6f0ea;
  --unsourced: #96501a;
  --unsourced-soft: #f8eee3;
  --required: #7a2f3a;
  --required-soft: #f6e8ea;
  --shadow: 0 1px 2px rgba(18,38,46,.06);
}}
@media (prefers-color-scheme: dark) {{
  :root:not([data-theme="light"]) {{
    --ground: #0d191e;
    --surface: #132229;
    --ink: #e8f0f2;
    --body: #c2d3d8;
    --muted: #8ba3ac;
    --rule: #263a43;
    --rule-soft: #1c2d35;
    --accent: #5fb9cb;
    --accent-soft: #14323a;
    --sourced: #7fc5a1;
    --sourced-soft: #163025;
    --unsourced: #d99a5e;
    --unsourced-soft: #33251a;
    --required: #e09aa5;
    --required-soft: #331f24;
    --shadow: none;
  }}
}}
:root[data-theme="dark"] {{
  --ground: #0d191e;
  --surface: #132229;
  --ink: #e8f0f2;
  --body: #c2d3d8;
  --muted: #8ba3ac;
  --rule: #263a43;
  --rule-soft: #1c2d35;
  --accent: #5fb9cb;
  --accent-soft: #14323a;
  --sourced: #7fc5a1;
  --sourced-soft: #163025;
  --unsourced: #d99a5e;
  --unsourced-soft: #33251a;
  --required: #e09aa5;
  --required-soft: #331f24;
  --shadow: none;
}}

* {{ box-sizing: border-box; }}
body {{
  background: var(--ground);
  color: var(--body);
  font-family: "Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15.5px;
  line-height: 1.6;
  margin: 0;
  padding-block: 0 72px;
  padding-inline: 20px;
  -webkit-font-smoothing: antialiased;
}}
.wrap {{ max-width: 880px; margin: 0 auto; }}
h1, h2, h3, h4 {{
  font-family: Newsreader, Georgia, "Times New Roman", serif;
  color: var(--ink);
  text-wrap: balance;
  margin: 0;
  font-weight: 600;
  line-height: 1.2;
}}
h1 {{ font-size: clamp(2rem, 5vw, 2.9rem); letter-spacing: -.015em; }}
h2 {{ font-size: 1.75rem; }}
h3 {{ font-size: 1.22rem; }}
h4 {{ font-size: 1.02rem; font-weight: 600; }}
p {{ margin: 0; }}
a {{ color: var(--accent); }}

/* ---- masthead ---- */
.masthead {{ padding-block: 56px 28px; border-bottom: 2px solid var(--ink); }}
.eyebrow {{
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: .72rem; letter-spacing: .14em; text-transform: uppercase;
  color: var(--muted); margin-bottom: 14px;
}}
.standfirst {{
  font-family: Newsreader, Georgia, serif;
  font-size: 1.22rem; line-height: 1.5; color: var(--body);
  margin-top: 18px; max-width: 62ch;
}}

/* ---- the gate ---- */
.gate {{
  margin-top: 28px; padding: 20px 22px;
  background: var(--accent-soft); border-left: 3px solid var(--accent);
  border-radius: 0 6px 6px 0;
}}
.gate h2 {{ font-size: 1.1rem; margin-bottom: 8px; }}
.gate p + p {{ margin-top: 10px; }}

/* ---- tally ---- */
.tally {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 1px; background: var(--rule); border: 1px solid var(--rule);
  margin-top: 28px; border-radius: 6px; overflow: hidden;
}}
.tally div {{ background: var(--surface); padding: 16px 18px; }}
.tally .n {{
  font-family: "IBM Plex Mono", monospace; font-size: 1.6rem; font-weight: 500;
  color: var(--ink); font-variant-numeric: tabular-nums; display: block; line-height: 1.1;
}}
.tally .k {{ font-size: .78rem; color: var(--muted); letter-spacing: .04em; }}

nav {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 28px; }}
nav a {{
  font-size: .83rem; padding: 5px 12px; border: 1px solid var(--rule);
  border-radius: 999px; text-decoration: none; color: var(--body); background: var(--surface);
}}
nav a:hover {{ border-color: var(--accent); color: var(--accent); }}
nav a:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}

/* ---- groups ---- */
.group {{ margin-top: 60px; }}
.group-head {{
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 10px 16px;
  padding-bottom: 10px; border-bottom: 1px solid var(--ink);
}}
.group-facts {{ display: flex; flex-wrap: wrap; gap: 10px; margin-left: auto; align-items: baseline; }}
.group-note {{ margin-top: 12px; color: var(--muted); max-width: 64ch; font-size: .95rem; }}
.count {{
  font-family: "IBM Plex Mono", monospace; font-size: .76rem; color: var(--muted);
  font-variant-numeric: tabular-nums;
}}
.provenance {{
  font-family: "IBM Plex Mono", monospace; font-size: .72rem;
  padding: 3px 9px; border-radius: 3px;
}}
.prov-ok {{ background: var(--sourced-soft); color: var(--sourced); }}
.prov-none {{ background: var(--unsourced-soft); color: var(--unsourced); font-weight: 500; }}

.callout {{
  margin-top: 16px; padding: 14px 16px; border: 1px dashed var(--rule);
  border-radius: 6px; font-size: .92rem; color: var(--muted); background: var(--surface);
}}
.callout strong {{ color: var(--ink); }}

/* ---- template ---- */
.tpl {{ margin-top: 30px; }}
.tpl-head {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 14px; }}
.tpl-meta {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: baseline; margin-left: auto; }}
.tpl-meta code {{
  font-family: "IBM Plex Mono", monospace; font-size: .74rem; color: var(--muted);
}}
.status {{
  font-family: "IBM Plex Mono", monospace; font-size: .7rem; letter-spacing: .08em;
  text-transform: uppercase; padding: 2px 8px; border: 1px solid var(--rule);
  border-radius: 3px; color: var(--muted);
}}

/* ---- section ---- */
.sec {{
  margin-top: 16px; background: var(--surface);
  border: 1px solid var(--rule); border-left: 3px solid var(--sourced);
  border-radius: 0 6px 6px 0; box-shadow: var(--shadow); overflow: hidden;
}}
.sec-unsourced {{ border-left-color: var(--unsourced); }}
.sec-head {{
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px 12px;
  padding: 14px 18px 0;
}}
.sec-meta {{ display: flex; gap: 8px; align-items: baseline; margin-left: auto; }}
.sec-desc {{
  padding: 6px 18px 14px; font-size: .87rem; color: var(--muted);
  border-bottom: 1px solid var(--rule-soft);
}}

/* ---- checks ---- */
.checks {{ list-style: none; margin: 0; padding: 0; }}
.check {{
  display: grid; grid-template-columns: 30px 1fr auto; gap: 4px 12px;
  padding: 14px 18px; border-bottom: 1px solid var(--rule-soft);
}}
.check:last-child {{ border-bottom: 0; }}
.check-n {{
  font-family: "IBM Plex Mono", monospace; font-size: .8rem; color: var(--muted);
  font-variant-numeric: tabular-nums; padding-top: 1px;
}}
.check-label {{ color: var(--ink); font-weight: 600; font-size: .98rem; }}
.check-std, .check-how {{ font-size: .88rem; margin-top: 3px; }}
.check-how {{ color: var(--muted); }}
.std-key {{
  font-family: "IBM Plex Mono", monospace; font-size: .69rem; text-transform: uppercase;
  letter-spacing: .07em; color: var(--muted); margin-right: 5px;
}}
.src {{
  margin-top: 7px; font-family: "IBM Plex Mono", monospace; font-size: .72rem;
  color: var(--sourced); display: flex; align-items: center; gap: 6px;
}}
.src-dot {{
  width: 5px; height: 5px; border-radius: 50%; background: currentColor; flex: 0 0 auto;
}}
.src-none {{ color: var(--unsourced); }}
.check-meta {{ display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }}
.chip {{
  font-family: "IBM Plex Mono", monospace; font-size: .68rem; letter-spacing: .03em;
  padding: 2px 8px; border-radius: 3px; white-space: nowrap;
  background: var(--rule-soft); color: var(--muted);
}}
.chip.req {{ background: var(--required-soft); color: var(--required); font-weight: 500; }}
.chip.photo {{ background: var(--accent-soft); color: var(--accent); }}
.chip.shared {{ background: var(--surface); border: 1px solid var(--rule); color: var(--muted); }}
.range {{
  font-family: "IBM Plex Mono", monospace; font-size: .72rem; color: var(--ink);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}}

footer {{
  margin-top: 72px; padding-top: 24px; border-top: 1px solid var(--rule);
  font-size: .87rem; color: var(--muted);
}}

@media (max-width: 620px) {{
  .check {{ grid-template-columns: 26px 1fr; }}
  .check-meta {{ grid-column: 2; flex-direction: row; flex-wrap: wrap; align-items: center; }}
  .group-facts, .tpl-meta, .sec-meta {{ margin-left: 0; }}
}}
@media (prefers-reduced-motion: reduce) {{
  * {{ animation: none !important; transition: none !important; }}
}}
</style>

<div class="wrap">
<header class="masthead">
  <p class="eyebrow">WI-075 · Quality &amp; Inspections · v1.456.0</p>
  <h1>Strawman Inspection Checklists</h1>
  <p class="standfirst">{total_checks} proposed checks across {len(DRAFT_TEMPLATES)} templates,
  drafted so there is something to argue with instead of a blank page. None of them is Sapphire's
  standard of care until you say so.</p>

  <div class="gate">
    <h2>Nothing here can be used by accident</h2>
    <p>Every template is seeded <strong>Draft</strong>, and a Draft template generates nothing —
    the endpoint refuses a non-Active template and the due sweep counts only Active ones. Each
    milestone goes on reporting <em>due and blocked</em> exactly as it did before.</p>
    <p><strong>Setting a template Active is the act of adopting it.</strong> Correct it first,
    then adopt it.</p>
  </div>

  <div class="tally">
    <div><span class="n">{total_checks}</span><span class="k">checks proposed</span></div>
    <div><span class="n">{sourced_checks}</span><span class="k">from an internal source</span></div>
    <div><span class="n">{unsourced_checks}</span><span class="k">drafted from nothing</span></div>
    <div><span class="n">{len(DRAFT_TEMPLATES)}</span><span class="k">templates, all Draft</span></div>
  </div>

  <nav>{nav}</nav>
</header>

{body}

<footer>
  <p>Green rule and green citation: lifted from something this company already wrote down, named
  per check. Amber: drafted from nothing — read those hardest.</p>
  <p style="margin-top:8px;">A section marked <em>shared</em> is used by more than one
  template — correcting it there corrects it everywhere it appears.</p>
  <p style="margin-top:8px;">To correct a check, quote it as
  <code>Service &gt; Safety and Electrical &gt; 3</code>. Generated from
  <code>quality/draft_catalog.py</code>, so this page and the seeded records cannot disagree.</p>
</footer>
</div>
"""

with open(OUT, "w", encoding="utf-8", newline="\n") as handle:
	handle.write(HTML)
print(f"wrote {OUT} ({len(HTML)} bytes, {total_checks} checks, {len(DRAFT_TEMPLATES)} templates)")
