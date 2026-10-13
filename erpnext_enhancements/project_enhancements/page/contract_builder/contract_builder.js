// Contract Builder: fill a contract in one step at a time, read it as you go, and have the client
// initial and sign it right here, or send it to them.
//
// Nik, 2026-10-13: "an interface that is a walkthrough of the contract fields that need to be
// filled out so we can basically build it alone or even with the client themselves … with a
// fully generated contract at the end and the digital esignature capture we can do right there
// or send to a customer to have them sign at their pace."
//
// And on the first version: "Let's make the flow a lot better looking than that and smoother
// transitions and highlighting where they are at in the agreement process as they go through it
// and let them initial and sign as it gets filled out with information."
//
// So the page is three panes that stay put while the work moves through them:
//
//   rail    the steps, each with what is left to fill, and how far along the agreement is
//   stage   the step being filled in; it slides from one step to the next
//   paper   the agreement itself, re-rendered as values change. The part of it the current step
//           fills in is lit up and scrolled to, focusing a field brings its part into view, and a
//           value that changes flashes where it lands.
//
// "With the client" mode turns the paper into the signing surface: each initials box becomes a
// pad as soon as its clause is filled in (drawn on its own, every time), the signature line takes
// the client's signature as it is captured, and the last step executes the contract
// (contract_builder.sign_here → esign.api.sign_in_person). Initials are kept with the wording they
// were drawn beside; a clause that changes afterwards loses its initials here, and the server
// refuses any whose wording no longer matches the issued snapshot.
//
// Screens, each its own history entry so Back and Forward walk them (never break the browser's
// own navigation):
//
//   /desk/contract-builder                  start: pick a contract type, or open a draft
//   /desk/contract-builder/<contract>/<step> one step of the walk (a section of the doctype)
//   /desk/contract-builder/<contract>/review the whole agreement, the signature, sign or send
//   /desk/contract-builder/<contract>/sign   the client signing on another tab or device
//
// The steps are the Project Contract's own sections for this contract type
// (project_enhancements/contract_builder.py), so a field added to the doctype shows up here with
// nothing to change. Values are saved onto the real draft whenever the step changes; the preview
// renders the values on screen, unsaved ones included, through the same renderer as the form's
// Preview button and the printed PDF.
//
// "Hand them another device" still opens the public signing page in a new tab
// (esign.api.start_in_person_signing) rather than a frame: that page can go on to Stripe to save
// a card, and Stripe will not run inside a frame. "Send it to them" is the emailed link.

frappe.pages["contract-builder"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Contract Builder"),
		single_column: true,
	});
	wrapper.contract_builder = new ContractBuilder(page, wrapper);
};

frappe.pages["contract-builder"].on_page_show = function (wrapper) {
	if (wrapper.contract_builder) {
		wrapper.contract_builder.handle_route();
	}
};

const CB_API = "erpnext_enhancements.project_enhancements.contract_builder.";
const CB_CONTRACT = "erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract.";
const CB_ESIGN = "erpnext_enhancements.project_enhancements.esign.api.";
// Shared with public/js/project_enhancements/contract_viewer.js, so the agreement's print CSS is
// published once per page however many places show a contract.
const CB_STYLE_ID = "ee-contract-viewer-style";
const CB_PAPER_CSS =
	".contract-doc { background: #fff; color: #000; padding: 22px 26px; border-radius: 4px; }";
const CB_REVIEW = "review";
const CB_SIGN = "sign";
const CB_POLL_MS = 4000;
// The elements a clause lives in. Kept in step with CLAUSE_BLOCKS in esign/initials.py, which
// reads the same text off the snapshot to check initials were drawn beside today's wording.
const CB_BLOCKS = "p,li,td,th,h1,h2,h3,h4,h5,h6,dd,dt";
// Builder-only marks inside the paper, left out of every text comparison.
const CB_SKIP_TEXT = ".ct-initial,.ct-sig-line,.cb-deco";
const CB_WIDE = ["Small Text", "Text", "Long Text", "Text Editor", "Table", "HTML", "Code", "Markdown Editor"];
const CB_TINTS = ["#2563eb", "#0d9488", "#7c3aed", "#d97706", "#db2777", "#059669", "#4f46e5", "#0891b2"];

const CB_STYLE = `
.cb-app{--cb-accent:var(--primary,#2490ef);--cb-soft:color-mix(in srgb,var(--cb-accent) 12%,transparent);--cb-ease:cubic-bezier(.2,.7,.2,1);color:var(--text-color);max-width:1680px;margin:0 auto;padding:4px 0 40px;}
.cb-app *{box-sizing:border-box;}
.cb-top{background:var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:14px;padding:12px 16px 10px;margin-bottom:14px;box-shadow:var(--shadow-sm,0 1px 2px rgba(0,0,0,.05));}
.cb-top-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
.cb-home{border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);border-radius:10px;width:34px;height:34px;display:inline-flex;align-items:center;justify-content:center;cursor:pointer;flex:none;transition:background .15s;}
.cb-home:hover{background:var(--cb-soft);}
.cb-who{flex:1;min-width:200px;}
.cb-eyebrow{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--text-muted);font-weight:600;}
.cb-who-name{font-size:16px;font-weight:600;line-height:1.3;}
.cb-chip{display:inline-flex;align-items:center;gap:5px;font-size:12px;border-radius:999px;padding:2px 10px;background:var(--control-bg);color:var(--text-color);white-space:nowrap;}
.cb-chip.cb-green{background:color-mix(in srgb,#16a34a 16%,transparent);color:#16a34a;}
.cb-chip.cb-blue{background:var(--cb-soft);color:var(--cb-accent);}
.cb-chip.cb-orange{background:color-mix(in srgb,#d97706 16%,transparent);color:#d97706;}
.cb-save{font-size:12px;color:var(--text-muted);display:inline-flex;align-items:center;gap:6px;min-width:110px;justify-content:flex-end;}
.cb-save i{width:8px;height:8px;border-radius:50%;background:#16a34a;display:inline-block;transition:background .2s;}
.cb-save.is-dirty i{background:#d97706;}.cb-save.is-saving i{background:var(--cb-accent);animation:cb-blink 1s ease-in-out infinite;}
.cb-mode{display:inline-flex;background:var(--control-bg);border-radius:10px;padding:3px;gap:2px;}
.cb-mode button{border:0;background:none;color:var(--text-muted);font-size:13px;padding:6px 12px;border-radius:8px;cursor:pointer;transition:background .2s,color .2s,box-shadow .2s;white-space:nowrap;}
.cb-mode button.is-on{background:var(--card-bg,var(--fg-color));color:var(--text-color);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.12);}
.cb-progress{display:flex;align-items:center;gap:10px;margin-top:10px;}
.cb-progress-track{flex:1;height:6px;border-radius:999px;background:var(--control-bg);overflow:hidden;}
.cb-progress-bar{height:100%;width:0;border-radius:999px;background:linear-gradient(90deg,var(--cb-accent),color-mix(in srgb,var(--cb-accent) 60%,#22c55e));transition:width .6s var(--cb-ease);}
.cb-progress-label{font-size:12px;color:var(--text-muted);white-space:nowrap;}
.cb-layout{display:grid;grid-template-columns:230px minmax(0,1fr) minmax(0,1.08fr);gap:18px;align-items:start;}
.cb-rail{position:sticky;top:72px;}
.cb-rail ol{list-style:none;margin:0;padding:0;position:relative;}
.cb-rail ol::before{content:"";position:absolute;left:17px;top:18px;bottom:18px;width:2px;background:var(--border-color);}
.cb-step{position:relative;display:flex;gap:10px;align-items:flex-start;width:100%;text-align:left;background:none;border:0;border-radius:10px;padding:7px 8px;color:var(--text-muted);cursor:pointer;transition:background .2s,color .2s;}
.cb-step:hover{background:var(--control-bg);}
.cb-step.is-current{background:var(--cb-soft);color:var(--text-color);}
.cb-dot{position:relative;z-index:1;flex:none;width:22px;height:22px;margin-left:-1px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;background:var(--card-bg,var(--fg-color));border:2px solid var(--border-color);color:var(--text-muted);transition:all .3s var(--cb-ease);}
.cb-step.is-done .cb-dot{background:#16a34a;border-color:#16a34a;color:#fff;}
.cb-step.is-current .cb-dot{background:var(--cb-accent);border-color:var(--cb-accent);color:#fff;box-shadow:0 0 0 4px var(--cb-soft);}
.cb-step-text{display:flex;flex-direction:column;min-width:0;}
.cb-step-title{font-size:13.5px;line-height:1.35;}
.cb-step.is-current .cb-step-title{font-weight:600;}
.cb-step-meta{font-size:11.5px;color:var(--text-muted);}
.cb-step-meta.is-todo{color:#d97706;}
.cb-rail-foot{margin:14px 0 0 8px;display:flex;flex-direction:column;gap:6px;font-size:12.5px;}
.cb-rail-foot a{color:var(--text-muted);cursor:pointer;}
.cb-rail-foot a:hover{color:var(--text-color);}
.cb-stage{min-width:0;}
.cb-panel{background:var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:14px;box-shadow:var(--shadow-sm,0 1px 2px rgba(0,0,0,.05));}
.cb-panel.cb-in-fwd{animation:cb-in-fwd .32s var(--cb-ease) both;}
.cb-panel.cb-in-back{animation:cb-in-back .32s var(--cb-ease) both;}
.cb-panel.cb-in-fade{animation:cb-fade .28s ease both;}
@keyframes cb-in-fwd{from{opacity:0;transform:translateX(28px);}to{opacity:1;transform:none;}}
@keyframes cb-in-back{from{opacity:0;transform:translateX(-28px);}to{opacity:1;transform:none;}}
@keyframes cb-fade{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:none;}}
@keyframes cb-blink{50%{opacity:.3;}}
.cb-panel-head{padding:18px 22px 4px;}
.cb-panel-head h2{font-size:20px;font-weight:650;margin:2px 0 4px;}
.cb-lede{color:var(--text-muted);font-size:14px;margin:0;line-height:1.5;}
.cb-panel-body{padding:12px 22px 6px;}
.cb-panel-foot{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;padding:12px 22px;border-top:1px solid var(--border-color);position:sticky;bottom:0;background:var(--card-bg,var(--fg-color));border-radius:0 0 14px 14px;z-index:3;}
.cb-panel-foot .btn{border-radius:9px;}
.cb-foot-right{display:flex;gap:8px;align-items:center;}
.cb-notice{border-radius:10px;padding:10px 14px;margin:8px 0 12px;font-size:13.5px;line-height:1.5;background:var(--control-bg);}
.cb-notice.is-warn{background:color-mix(in srgb,#d97706 13%,transparent);}
.cb-notice.is-ok{background:color-mix(in srgb,#16a34a 13%,transparent);}
.cb-notice.is-info{background:var(--cb-soft);}
.cb-notice ul{margin:6px 0 0;padding-left:18px;}
.cb-notice a{cursor:pointer;text-decoration:underline;}
.cb-hint-row{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;}
.cb-link-btn{border:0;background:none;color:var(--cb-accent);font-weight:600;cursor:pointer;padding:0;font-size:13px;}
.cb-fields .form-column>form{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));column-gap:18px;align-items:start;}
.cb-fields .frappe-control{margin-bottom:12px;}
${CB_WIDE.map((t) => `.cb-fields .frappe-control[data-fieldtype="${t}"]`).join(",")}{grid-column:1/-1;}
.cb-fields .frappe-control.cb-is-focus .control-label{color:var(--cb-accent);}
.cb-choices{display:flex;flex-direction:column;gap:10px;margin:2px 0 6px;}
.cb-choices-label{font-size:12px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px;}
.cb-choice{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:14px;align-items:center;border:1.5px solid var(--border-color);border-radius:12px;padding:12px 14px;background:var(--card-bg,var(--fg-color));transition:border-color .2s,box-shadow .2s,background .2s;}
.cb-choice:hover{border-color:color-mix(in srgb,var(--cb-accent) 45%,var(--border-color));}
.cb-choice.is-on{border-color:var(--cb-accent);background:color-mix(in srgb,var(--cb-accent) 5%,var(--card-bg,var(--fg-color)));box-shadow:0 0 0 3px var(--cb-soft);}
.cb-tick{appearance:none;-webkit-appearance:none;width:22px;height:22px;border-radius:7px;border:2px solid var(--border-color);background:var(--control-bg);cursor:pointer;display:grid;place-content:center;margin:0;transition:all .18s;}
.cb-tick::after{content:"";width:6px;height:11px;border:solid #fff;border-width:0 2.5px 2.5px 0;transform:rotate(45deg) scale(0);margin-top:-2px;transition:transform .18s var(--cb-ease);}
.cb-tick:checked{background:var(--cb-accent);border-color:var(--cb-accent);}
.cb-tick:checked::after{transform:rotate(45deg) scale(1);}
.cb-tick:disabled{cursor:default;opacity:.7;}
.cb-choice-main{cursor:pointer;min-width:0;}
.cb-choice-title{font-weight:600;font-size:14.5px;line-height:1.3;}
.cb-choice-sub{font-size:12.5px;color:var(--text-muted);}
.cb-prices{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;}
.cb-price{display:flex;flex-direction:column;gap:2px;}
.cb-price small{font-size:11px;color:var(--text-muted);}
.cb-price-box{display:flex;align-items:center;background:var(--control-bg);border-radius:9px;padding:0 10px;height:36px;border:1px solid transparent;transition:border-color .2s,box-shadow .2s;}
.cb-price-box:focus-within{border-color:var(--cb-accent);box-shadow:0 0 0 3px var(--cb-soft);}
.cb-price-box span{color:var(--text-muted);font-size:13px;}
.cb-price-box input{border:0;background:none;outline:none;width:92px;height:34px;padding:0 4px;font-size:14px;color:var(--text-color);text-align:right;font-variant-numeric:tabular-nums;}
.cb-price-box input::-webkit-inner-spin-button{opacity:.4;}
@media (max-width:560px){.cb-choice{grid-template-columns:auto minmax(0,1fr);}.cb-prices{grid-column:1/-1;justify-content:flex-start;}}
.cb-doc{position:sticky;top:72px;height:calc(100vh - 92px);display:flex;flex-direction:column;background:var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:14px;overflow:hidden;box-shadow:var(--shadow-sm,0 1px 2px rgba(0,0,0,.05));}
.cb-doc-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border-color);}
.cb-doc-head b{font-size:14px;}
.cb-doc-status{flex:1;font-size:12px;color:var(--text-muted);text-align:right;}
.cb-doc-close{display:none;border:0;background:var(--control-bg);border-radius:8px;width:30px;height:30px;cursor:pointer;color:var(--text-color);}
.cb-doc-loading{height:2px;background:transparent;position:relative;overflow:hidden;}
.cb-doc.is-loading .cb-doc-loading::after{content:"";position:absolute;inset:0;width:40%;background:var(--cb-accent);animation:cb-load 1s ease-in-out infinite;}
@keyframes cb-load{from{transform:translateX(-100%);}to{transform:translateX(260%);}}
.cb-doc-scroll{flex:1;overflow-y:auto;background:color-mix(in srgb,var(--control-bg) 70%,#8a8f98 12%);padding:18px;scroll-behavior:smooth;}
.cb-paper-wrap{max-width:860px;margin:0 auto;}
.cb-paper-wrap .contract-doc{font-size:12.5px;box-shadow:0 2px 14px rgba(15,23,42,.14);transition:opacity .25s;}
.cb-paper-note{font-size:12px;color:var(--text-muted);margin:0 0 8px;}
.cb-doc-fab{display:none;}
.cb-app .contract-doc section.cb-sec{display:block;position:relative;margin:0 -12px;padding:2px 12px;border-radius:8px;transition:background .35s,box-shadow .35s,opacity .35s;}
.cb-app .contract-doc.cb-has-here section.cb-sec:not(.is-here){opacity:.55;}
.cb-app .contract-doc.cb-has-here section.cb-sec:not(.is-here):hover{opacity:1;}
.cb-app .contract-doc section.cb-sec.is-here{background:#f1f7ff;box-shadow:inset 3px 0 0 #2563eb;}
.cb-app .contract-doc section.cb-sec.is-focus{background:#e3efff;}
.cb-app .contract-doc section.cb-sec.is-here[data-here]::before{content:attr(data-here);position:absolute;top:-9px;right:10px;z-index:1;font:600 10px/1.6 system-ui,sans-serif;letter-spacing:.04em;text-transform:uppercase;color:#fff;background:#2563eb;border-radius:999px;padding:1px 9px;box-shadow:0 2px 6px rgba(37,99,235,.35);}
.cb-app .contract-doc .cb-changed{animation:cb-changed 1.9s ease-out;border-radius:3px;}
@keyframes cb-changed{0%,30%{background:#fde68a;box-shadow:0 0 0 3px #fde68a;}100%{background:transparent;box-shadow:0 0 0 3px transparent;}}
.cb-app .contract-doc .ct-initial.cb-ini{display:inline-flex;align-items:center;justify-content:center;gap:4px;min-width:74px;height:30px;padding:0 9px;margin:0 2px 0 6px;vertical-align:middle;border-radius:7px;border:1.5px dashed #94a3b8;background:#fff;font:600 11px/1 system-ui,sans-serif;color:#64748b;text-indent:0;transition:all .25s;}
.cb-app .contract-doc .ct-initial.cb-ini.is-ready{border:1.5px solid #2563eb;background:#eff6ff;color:#1d4ed8;}
.cb-app .contract-doc .ct-initial.cb-ini.is-live.is-ready{cursor:pointer;animation:cb-ring 1.8s ease-out infinite;}
.cb-app .contract-doc .ct-initial.cb-ini.is-live.is-ready:hover{background:#dbeafe;}
.cb-app .contract-doc .ct-initial.cb-ini.is-done{border:1.5px solid #16a34a;background:#f0fdf4;padding:0 6px;}
.cb-app .contract-doc .ct-initial.cb-ini.is-live.is-done{cursor:pointer;}
.cb-app .contract-doc .ct-initial.cb-ini.is-done img{max-height:24px;max-width:90px;}
.cb-app .contract-doc .ct-initial.cb-ini.is-wait{color:#94a3b8;font-weight:500;}
.cb-app .contract-doc .ct-initial.cb-ini.is-flash{box-shadow:0 0 0 4px #fde68a;}
.cb-app .contract-doc .ct-initial.cb-ini:focus-visible{outline:2px solid #2563eb;outline-offset:2px;}
@keyframes cb-ring{0%{box-shadow:0 0 0 0 rgba(37,99,235,.45);}70%{box-shadow:0 0 0 8px rgba(37,99,235,0);}100%{box-shadow:0 0 0 0 rgba(37,99,235,0);}}
.cb-app .contract-doc .ct-sig-line.cb-sig{display:inline-flex;align-items:flex-end;min-width:160px;min-height:34px;border-bottom:1px solid #000;padding:0 4px;vertical-align:bottom;}
.cb-app .contract-doc .cb-sig-here{border:0;border-radius:999px;background:#2563eb;color:#fff;font:600 11.5px/1 system-ui,sans-serif;padding:7px 12px;margin-bottom:4px;cursor:pointer;animation:cb-ring 1.8s ease-out infinite;}
.cb-app .contract-doc .cb-sig-typed{font-family:"Segoe Script","Brush Script MT","Lucida Handwriting",cursive;font-size:20px;line-height:1.1;color:#0f172a;}
.cb-app .contract-doc .cb-sig-img{max-height:44px;max-width:220px;}
.cb-app .contract-doc .cb-sig-pending{font:500 10px/1 system-ui,sans-serif;color:#64748b;margin-left:8px;align-self:center;}
.cb-checks{display:flex;flex-direction:column;gap:8px;margin:6px 0 14px;}
.cb-check{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--border-color);border-radius:12px;padding:11px 14px;}
.cb-check-icon{flex:none;width:24px;height:24px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;background:var(--control-bg);color:var(--text-muted);}
.cb-check.is-ok .cb-check-icon{background:#16a34a;color:#fff;}
.cb-check.is-todo .cb-check-icon{background:color-mix(in srgb,#d97706 18%,transparent);color:#d97706;}
.cb-check-body{flex:1;min-width:0;font-size:13.5px;}
.cb-check-body b{display:block;font-size:14px;margin-bottom:1px;}
.cb-check-body ul{margin:4px 0 0;padding-left:18px;color:var(--text-muted);}
.cb-check-body a{cursor:pointer;text-decoration:underline;}
.cb-signbox{border:1.5px solid var(--border-color);border-radius:14px;padding:16px;margin:4px 0 12px;background:color-mix(in srgb,var(--cb-accent) 3%,var(--card-bg,var(--fg-color)));transition:border-color .3s,box-shadow .3s;}
.cb-signbox.is-ready{border-color:var(--cb-accent);box-shadow:0 0 0 3px var(--cb-soft);}
.cb-signbox h3{font-size:16px;font-weight:650;margin:0 0 2px;}
.cb-form-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 14px;margin:12px 0;}
.cb-form-grid label,.cb-label{display:block;font-size:12px;color:var(--text-muted);margin-bottom:4px;font-weight:500;}
.cb-tabs{display:inline-flex;background:var(--control-bg);border-radius:9px;padding:3px;gap:2px;margin:4px 0 10px;}
.cb-tabs button{border:0;background:none;border-radius:7px;padding:5px 14px;font-size:13px;color:var(--text-muted);cursor:pointer;}
.cb-tabs button.is-on{background:var(--card-bg,var(--fg-color));color:var(--text-color);font-weight:600;box-shadow:0 1px 3px rgba(0,0,0,.12);}
.cb-typed-preview{font-family:"Segoe Script","Brush Script MT","Lucida Handwriting",cursive;font-size:28px;min-height:52px;border-bottom:1.5px solid var(--text-muted);padding:4px 6px;color:var(--text-color);}
.cb-padbox{position:relative;border:1.5px dashed var(--border-color);border-radius:12px;background:#fff;}
.cb-padbox canvas{display:block;width:100%;height:160px;touch-action:none;cursor:crosshair;border-radius:12px;}
.cb-padbox.is-short canvas{height:150px;}
.cb-pad-hint{position:absolute;left:14px;bottom:10px;font-size:12px;color:#94a3b8;pointer-events:none;}
.cb-pad-line{position:absolute;left:14px;right:14px;bottom:34px;border-bottom:1px solid #e2e8f0;pointer-events:none;}
.cb-pad-clear{position:absolute;right:10px;top:10px;border:1px solid #e2e8f0;background:#fff;color:#475569;border-radius:8px;font-size:12px;padding:3px 10px;cursor:pointer;}
.cb-consent{display:flex;gap:10px;align-items:flex-start;font-size:13px;margin:12px 0 4px;line-height:1.45;}
.cb-consent input{margin-top:3px;width:17px;height:17px;flex:none;}
.cb-disclosure{font-size:12.5px;margin:8px 0 0;}
.cb-disclosure summary{cursor:pointer;color:var(--text-muted);}
.cb-disclosure div{white-space:pre-wrap;margin-top:6px;color:var(--text-muted);max-height:180px;overflow:auto;}
.cb-missing{font-size:12.5px;color:var(--text-muted);margin:8px 0 0;}
.cb-actions{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0 4px;}
.cb-actions .btn{border-radius:9px;}
.cb-big{font-size:15px;padding:9px 20px;font-weight:600;}
.cb-or{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin:16px 0 6px;display:flex;align-items:center;gap:10px;}
.cb-or::before,.cb-or::after{content:"";flex:1;height:1px;background:var(--border-color);}
.cb-done{text-align:center;padding:30px 18px 18px;}
.cb-done-mark{width:68px;height:68px;border-radius:50%;background:#16a34a;color:#fff;display:inline-flex;align-items:center;justify-content:center;animation:cb-pop .5s var(--cb-ease) both;}
.cb-done-mark svg{width:34px;height:34px;stroke:#fff;stroke-width:3.2;fill:none;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:40;stroke-dashoffset:40;animation:cb-draw .45s .25s ease forwards;}
@keyframes cb-pop{from{transform:scale(.4);opacity:0;}to{transform:scale(1);opacity:1;}}
@keyframes cb-draw{to{stroke-dashoffset:0;}}
.cb-done h2{font-size:22px;margin:14px 0 6px;}
.cb-done p{color:var(--text-muted);max-width:520px;margin:4px auto;}
.cb-done .cb-actions{justify-content:center;margin-top:16px;}
.cb-pulse{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--cb-accent);margin-right:7px;animation:cb-blink 1.4s ease-in-out infinite;}
.cb-modal{position:fixed;inset:0;z-index:1060;background:rgba(15,23,42,.5);display:flex;align-items:center;justify-content:center;padding:16px;animation:cb-fade .2s ease both;}
.cb-sheet{background:var(--card-bg,var(--fg-color));color:var(--text-color);border-radius:16px;width:min(520px,100%);padding:20px;box-shadow:0 20px 50px rgba(0,0,0,.3);animation:cb-sheet .28s var(--cb-ease) both;}
@keyframes cb-sheet{from{transform:translateY(16px) scale(.97);opacity:0;}to{transform:none;opacity:1;}}
.cb-sheet h3{font-size:17px;font-weight:650;margin:2px 0 8px;}
.cb-quote{font-size:12.5px;line-height:1.5;color:var(--text-muted);border-left:3px solid var(--cb-accent);padding:6px 10px;margin:0 0 12px;max-height:120px;overflow:auto;background:var(--control-bg);border-radius:0 8px 8px 0;}
.cb-sheet-error{color:#dc2626;font-size:13px;margin:8px 0 0;}
.cb-sheet-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px;}
.cb-start-hero{background:linear-gradient(135deg,var(--cb-soft),transparent 70%),var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:16px;padding:24px 26px;margin:4px 0 18px;}
.cb-start-hero h2{font-size:22px;font-weight:650;margin:0 0 6px;}
.cb-start-hero p{color:var(--text-muted);max-width:760px;margin:0;font-size:14px;line-height:1.55;}
.cb-section-title{font-size:13px;font-weight:600;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin:18px 0 10px;}
.cb-types{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px;}
.cb-type{display:flex;gap:12px;align-items:flex-start;text-align:left;background:var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:14px;padding:14px;cursor:pointer;color:var(--text-color);transition:transform .2s var(--cb-ease),box-shadow .2s,border-color .2s;}
.cb-type:hover,.cb-type:focus-visible{transform:translateY(-2px);border-color:var(--cb-accent);box-shadow:0 8px 22px rgba(0,0,0,.08);outline:none;}
.cb-badge{flex:none;width:40px;height:40px;border-radius:11px;color:#fff;font-weight:700;font-size:13px;display:inline-flex;align-items:center;justify-content:center;letter-spacing:.02em;}
.cb-type h4{margin:0 0 3px;font-size:14.5px;font-weight:600;}
.cb-type p{margin:0;font-size:12.5px;color:var(--text-muted);line-height:1.45;}
.cb-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:10px;}
.cb-row{display:flex;gap:12px;align-items:center;background:var(--card-bg,var(--fg-color));border:1px solid var(--border-color);border-radius:12px;padding:11px 14px;cursor:pointer;transition:border-color .2s,box-shadow .2s;}
.cb-row:hover,.cb-row:focus-visible{border-color:var(--cb-accent);box-shadow:0 4px 14px rgba(0,0,0,.06);outline:none;}
.cb-row .cb-badge{width:34px;height:34px;font-size:11px;border-radius:9px;}
.cb-row-main{min-width:0;flex:1;}
.cb-row-title{font-weight:600;font-size:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.cb-row-sub{font-size:12px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.cb-skeleton{border-radius:14px;background:linear-gradient(90deg,var(--control-bg) 25%,color-mix(in srgb,var(--control-bg) 60%,transparent) 50%,var(--control-bg) 75%);background-size:200% 100%;animation:cb-shimmer 1.3s linear infinite;}
@keyframes cb-shimmer{from{background-position:200% 0;}to{background-position:-200% 0;}}
@media (max-width:1279px){
.cb-layout{grid-template-columns:minmax(0,1fr);}
.cb-rail{position:static;}
.cb-rail ol{display:flex;gap:4px;overflow-x:auto;padding-bottom:4px;}
.cb-rail ol::before{display:none;}
.cb-step{width:auto;flex:none;}
.cb-step-title{white-space:nowrap;}
.cb-step-meta{display:none;}
.cb-rail-foot{flex-direction:row;gap:16px;margin:6px 0 0 8px;}
.cb-doc{display:none;position:fixed;inset:12px;top:60px;height:auto;z-index:1050;box-shadow:0 20px 60px rgba(0,0,0,.35);}
.cb-doc.is-open{display:flex;animation:cb-fade .25s ease both;}
.cb-doc-close{display:inline-block;}
.cb-doc-fab{display:inline-flex;align-items:center;gap:8px;position:fixed;right:18px;bottom:84px;z-index:1040;border:0;border-radius:999px;background:var(--cb-accent);color:#fff;font-weight:600;font-size:14px;padding:11px 18px;box-shadow:0 8px 24px rgba(0,0,0,.25);cursor:pointer;}
}
@media (prefers-reduced-motion:reduce){.cb-app *,.cb-modal,.cb-sheet{animation:none!important;transition:none!important;}.cb-doc-scroll{scroll-behavior:auto;}}
`;

// ---------------------------------------------------------------------------------- helpers

function cb_esc(text) {
	return frappe.utils.escape_html(text == null ? "" : String(text));
}

// Whitespace-collapsed text: esign/initials.py normalize_clause.
function cb_norm(text) {
	return String(text || "")
		.replace(/\s+/g, " ")
		.trim();
}

function cb_key(text) {
	return cb_norm(text).toUpperCase();
}

// Per-tab memory for a contract (mode, initials drawn so far). sessionStorage: never sent
// anywhere and gone with the tab; unavailable storage just means nothing is remembered.
function cb_store(key, value) {
	try {
		if (value === undefined) return JSON.parse(window.sessionStorage.getItem(`cb:${key}`) || "null");
		if (value === null) window.sessionStorage.removeItem(`cb:${key}`);
		else window.sessionStorage.setItem(`cb:${key}`, JSON.stringify(value));
	} catch (e) {
		// Private mode or a full store: carry on without it.
	}
	return null;
}

function cb_badge(text, seed) {
	const letters = String(text || "?")
		.replace(/[^A-Za-z ]/g, " ")
		.split(/\s+/)
		.filter((w) => w && !["and", "of", "the", "for"].includes(w.toLowerCase()))
		.map((w) => w[0].toUpperCase())
		.join("")
		.slice(0, 3);
	let hash = 0;
	for (const ch of String(seed || text || "")) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
	return $('<span class="cb-badge"></span>')
		.css("background", CB_TINTS[hash % CB_TINTS.length])
		.text(letters || "?");
}

// The text a clause owns: its block's text, leaving out initials boxes, the signature line and
// anything the builder drew, and the text of any block nested inside it (CLAUSE_BLOCKS rules).
function cb_owned_text(block) {
	const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
	const parts = [];
	let node;
	while ((node = walker.nextNode())) {
		const parent = node.parentElement;
		if (!parent || parent.closest(CB_SKIP_TEXT)) continue;
		if (parent.closest(CB_BLOCKS) !== block) continue;
		parts.push(node.nodeValue);
	}
	return cb_norm(parts.join(""));
}

const CB_CHECK_SVG = '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>';

// A drawing pad: the backing store scaled to the screen so the PNG is sharp, the ink trimmed
// to its bounding box on export. One instance per pad, reset to empty whenever it opens.
class CBPad {
	constructor(canvas) {
		this.canvas = canvas;
		this.strokes = 0;
		this.ctx = null;
		let drawing = false;
		const pos = (event) => {
			const rect = canvas.getBoundingClientRect();
			return { x: event.clientX - rect.left, y: event.clientY - rect.top };
		};
		canvas.addEventListener("pointerdown", (event) => {
			if (!this.ctx) this.reset();
			drawing = true;
			this.strokes += 1;
			canvas.setPointerCapture(event.pointerId);
			const p = pos(event);
			this.ctx.beginPath();
			this.ctx.moveTo(p.x, p.y);
			this.ctx.lineTo(p.x + 0.01, p.y + 0.01);
			this.ctx.stroke();
			if (this.on_ink) this.on_ink();
			event.preventDefault();
		});
		canvas.addEventListener("pointermove", (event) => {
			if (!drawing) return;
			const p = pos(event);
			this.ctx.lineTo(p.x, p.y);
			this.ctx.stroke();
			event.preventDefault();
		});
		["pointerup", "pointercancel", "pointerleave"].forEach((name) =>
			canvas.addEventListener(name, () => {
				drawing = false;
			})
		);
	}

	reset() {
		const ratio = window.devicePixelRatio || 1;
		const rect = this.canvas.getBoundingClientRect();
		this.canvas.width = Math.round((rect.width || 400) * ratio);
		this.canvas.height = Math.round((rect.height || 150) * ratio);
		this.ctx = this.canvas.getContext("2d");
		this.ctx.scale(ratio, ratio);
		this.ctx.lineWidth = 2.4;
		this.ctx.lineCap = "round";
		this.ctx.lineJoin = "round";
		this.ctx.strokeStyle = "#0f172a";
		this.strokes = 0;
	}

	png() {
		if (!this.strokes) return "";
		const { width: w, height: h } = this.canvas;
		const data = this.canvas.getContext("2d").getImageData(0, 0, w, h).data;
		let minX = w;
		let minY = h;
		let maxX = -1;
		let maxY = -1;
		for (let y = 0; y < h; y++) {
			for (let x = 0; x < w; x++) {
				if (data[(y * w + x) * 4 + 3] > 8) {
					if (x < minX) minX = x;
					if (x > maxX) maxX = x;
					if (y < minY) minY = y;
					if (y > maxY) maxY = y;
				}
			}
		}
		if (maxX < 0) return "";
		const pad = 6;
		minX = Math.max(0, minX - pad);
		minY = Math.max(0, minY - pad);
		maxX = Math.min(w - 1, maxX + pad);
		maxY = Math.min(h - 1, maxY + pad);
		const out = document.createElement("canvas");
		out.width = maxX - minX + 1;
		out.height = maxY - minY + 1;
		out.getContext("2d").drawImage(this.canvas, minX, minY, out.width, out.height, 0, 0, out.width, out.height);
		return out.toDataURL("image/png");
	}
}

// SHA-256 of a PNG data URI's bytes, as esign/initials.py hashes them to refuse a copied
// drawing. Null where the browser offers no SubtleCrypto; the server still checks.
function cb_png_digest(data_uri) {
	try {
		if (!window.crypto || !window.crypto.subtle) return Promise.resolve(null);
		const raw = atob(String(data_uri).split(",")[1] || "");
		const bytes = new Uint8Array(raw.length);
		for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
		return window.crypto.subtle.digest("SHA-256", bytes).then((buffer) =>
			Array.from(new Uint8Array(buffer))
				.map((b) => b.toString(16).padStart(2, "0"))
				.join("")
		);
	} catch (e) {
		return Promise.resolve(null);
	}
}

// ---------------------------------------------------------------------------------- the page

class ContractBuilder {
	constructor(page, wrapper) {
		this.page = page;
		this.$root = $('<div class="cb-app"></div>').appendTo(page.body);
		if (!document.getElementById("cb-style")) {
			$('<style id="cb-style"></style>').text(CB_STYLE).appendTo(document.head);
		}
		this.data = null; // get_builder payload
		this.values = null; // the contract's values as edited on screen
		this.dirty = false;
		this.saving = false;
		this.fg = null;
		this.step_index = 0;
		this.view = null; // the step key, CB_REVIEW or CB_SIGN on screen
		this.loading = false;
		this.$shell = null;
		this.$paper = null;
		this.preview_key = null;
		this.preview_timer = null;
		this.preview_seq = 0;
		this.mode = "prepare"; // or "client": the client is here, initialing and signing
		this.initials = {}; // key -> {image, clause, sha, label}
		this.boxes = []; // the initials boxes in the paper, in order
		this.sig = { mode: "Typed", name: "", title: "", email: "", agree: false, image: "" };
		this.autopay = null;
		this.signing_now = false;
		this.sign_url = null; // held in memory only: it is a bearer link, never put in the URL
		this.poll = null;

		// Leaving the builder by any other way (the sidebar, the awesome bar, a link) saves what
		// was typed on the step, and closing or reloading the tab with unsaved values asks first.
		$(wrapper).on("hide", () => {
			this.stop_poll();
			this.close_modal();
			this.flush().catch(() => {});
		});
		window.addEventListener("beforeunload", (event) => {
			if ((frappe.get_route() || [])[0] !== "contract-builder") return;
			if (!this.data || !this.data.editable || !(this.dirty || this.tables_changed())) return;
			event.preventDefault();
			event.returnValue = "";
		});
	}

	// ------------------------------------------------------------------ routing

	handle_route() {
		const route = frappe.get_route();
		const name = route[1] ? decodeURIComponent(route[1]) : null;
		const step = route[2] ? decodeURIComponent(route[2]) : null;
		this.stop_poll();
		this.close_modal();

		if (!name) {
			this.page.set_title(__("Contract Builder"));
			return this.flush()
				.catch(() => {})
				.then(() => {
					this.data = null;
					this.$shell = null;
					this.show_start();
				});
		}

		if (this.data && this.data.name === name) {
			// Back or Forward between steps. Save what was typed on the way out; if the save is
			// refused (frappe has already said why), show the step anyway: the values stay on
			// screen, still unsaved, rather than being lost to a blank error page.
			return this.flush()
				.catch(() => {})
				.then(() => this.show(step));
		}
		return this.load(name)
			.then(() => this.show(step))
			.catch((error) => this.show_problem(error));
	}

	go(step, replace) {
		if (replace) frappe.route_flags.replace_route = true;
		frappe.set_route("contract-builder", this.data.name, step);
		// Read by the router's push, which has already run; left set it would turn the next
		// step into a replace (the rental planner's pattern).
		delete frappe.route_flags.replace_route;
	}

	// Save, then move. A refused save keeps the user where they are: frappe has shown the reason.
	save_and_go(step) {
		this.flush()
			.then(() => this.go(step))
			.catch(() => {});
	}

	load(name) {
		this.$shell = null;
		this.$paper = null;
		this.preview_key = null;
		this.$root.html(`<div style="display:grid;grid-template-columns:230px 1fr 1.08fr;gap:18px">
			<div class="cb-skeleton" style="height:320px"></div>
			<div class="cb-skeleton" style="height:520px"></div>
			<div class="cb-skeleton" style="height:620px"></div></div>`);
		return frappe.xcall(`${CB_API}get_builder`, { name }).then((data) => {
			this.set_data(data);
			this.mode = cb_store(`mode:${name}`) === "client" && this.can_sign_here() ? "client" : "prepare";
			this.initials = data.status === "Signed" ? {} : cb_store(`initials:${name}`) || {};
			this.sig = Object.assign(
				{ mode: "Typed", name: "", title: "", email: "", agree: false, image: "" },
				{ name: this.values.contact_person || "", email: this.values.contact_email || "" }
			);
			this.autopay = null;
			// Nothing of the last contract survives into this one: not its signing link (a
			// bearer link), not its initials boxes, not a half-finished signing.
			this.sign_url = null;
			this.boxes = [];
			this.signing_now = false;
		});
	}

	set_data(data) {
		this.data = data;
		this.values = JSON.parse(JSON.stringify(data.doc || {}));
		this.dirty = false;
		this.page.set_title(data.title || data.name);
		if (this.$shell) this.update_chrome();
	}

	order(view) {
		const steps = (this.data && this.data.steps) || [];
		const index = steps.findIndex((s) => s.key === view);
		if (index >= 0) return index;
		if (view === CB_REVIEW) return steps.length;
		if (view === CB_SIGN) return steps.length + 1;
		return -1;
	}

	show(step) {
		const steps = this.data.steps || [];
		if (!step) {
			// The first visit to a contract lands on a step. A replace, not a new entry, so Back
			// from it leaves the builder rather than bouncing on a bare URL; through the router,
			// which renders the step when it lands (on_page_show).
			return this.go(this.data.docstatus === 0 && steps.length ? steps[0].key : CB_REVIEW, true);
		}
		const from = this.order(this.view);
		const to = this.order(step);
		const dir = from < 0 ? "fade" : to < from ? "back" : to > from ? "fwd" : "fade";
		if (step === CB_SIGN) return this.render_sign(dir);
		if (step === CB_REVIEW) return this.render_review(dir);
		const index = steps.findIndex((s) => s.key === step);
		if (index < 0) return this.go(CB_REVIEW, true);
		return this.render_step(index, dir);
	}

	show_problem(error) {
		const message = (error && (error.message || error._server_messages)) || __("Something went wrong.");
		this.$root.html(
			`<div class="cb-panel" style="padding:20px;margin-top:16px">${cb_esc(
				typeof message === "string" ? message : __("This contract could not be opened.")
			)}<div style="margin-top:10px"><a href="/desk/contract-builder">${__("Back to the Contract Builder")}</a></div></div>`
		);
	}

	can_sign_here() {
		return !!(this.data && this.data.can_sign_here && this.data.status !== "Signed" && this.data.docstatus !== 2);
	}

	live() {
		return this.mode === "client" && this.can_sign_here();
	}

	// What this agreement calls the other side: "client", "subcontractor", "architect" …
	// (contract_builder.PARTY_NOUNS). The mode is still called "client" internally.
	noun(capital) {
		const noun = (this.data && this.data.party_noun) || __("client");
		// "Employee-Contractor initials", as the agreement's own caption (initials.INITIALS_CAPTIONS) reads.
		return capital ? noun.replace(/(^|[\s-])(\w)/g, (m, sep, ch) => sep + ch.toUpperCase()) : noun;
	}

	// -------------------------------------------------------------- start screen

	show_start() {
		this.$root.html(`<div class="cb-skeleton" style="height:120px;margin-bottom:18px"></div>
			<div class="cb-skeleton" style="height:240px"></div>`);
		frappe.xcall(`${CB_API}get_builder_start`).then((start) => {
			const options = frappe.route_options || {};
			frappe.route_options = null; // consumed, so a later plain visit does not replay it
			const $wrap = $('<div class="cb-in-fade cb-panel" style="border:0;box-shadow:none;background:none"></div>');
			$wrap.append(`
				<div class="cb-start-hero">
					<h2>${__("Build an agreement")}</h2>
					<p>${__(
						"Pick the kind of agreement. The builder walks you through each part with the finished agreement beside you, lighting up the part you are filling in. On your own, or with the other side at the table (the client, subcontractor, architect …): they can initial each clause as it is filled in and sign at the end, right here, or get it by email to sign in their own time."
					)}</p>
				</div>`);
			$wrap.append(`<div class="cb-section-title">${__("Start a new one")}</div>`);
			const $types = $('<div class="cb-types"></div>').appendTo($wrap);
			(start.types || []).forEach((type) => {
				$('<button type="button" class="cb-type"></button>')
					.append(cb_badge(type.title, type.template_key))
					.append(
						$("<div></div>")
							.append($("<h4></h4>").text(type.title))
							.append($("<p></p>").text(type.description || ""))
					)
					.on("click", () => this.start_dialog(type, {}))
					.appendTo($types);
			});
			this.append_list($wrap, __("Drafts in progress"), start.drafts, start.types);
			this.append_list($wrap, __("Waiting for a signature"), start.awaiting, start.types);
			this.$root.empty().append($wrap);

			const wanted = (start.types || []).find((t) => t.name === options.template);
			if (wanted) this.start_dialog(wanted, options);
		});
	}

	append_list($wrap, title, rows, types) {
		if (!rows || !rows.length) return;
		$wrap.append($('<div class="cb-section-title"></div>').text(title));
		const $list = $('<div class="cb-list"></div>').appendTo($wrap);
		rows.forEach((row) => {
			const type = (types || []).find((t) => t.template_key === row.template_key);
			$('<div class="cb-row" role="button" tabindex="0"></div>')
				.append(cb_badge(type ? type.title : row.template_key, row.template_key))
				.append(
					$('<div class="cb-row-main"></div>')
						.append($('<div class="cb-row-title"></div>').text(row.party_display || row.title || row.name))
						.append(
							$('<div class="cb-row-sub"></div>').text(
								[type ? type.title : null, row.name, frappe.datetime.prettyDate(row.modified)]
									.filter(Boolean)
									.join(" · ")
							)
						)
				)
				.on("click keydown", (event) => {
					if (event.type === "keydown" && event.key !== "Enter") return;
					frappe.set_route("contract-builder", row.name);
				})
				.appendTo($list);
		});
	}

	start_dialog(type, options) {
		const any_party = type.party_type === "Any Party";
		const supplier = type.party_type === "Supplier";
		const fields = [
			{
				fieldname: "project",
				fieldtype: "Link",
				options: "Project",
				label: __("Project"),
				default: options.project,
				description: __("The client, site and description come from the project."),
			},
		];
		if (!supplier && !any_party) {
			fields.push({
				fieldname: "opportunity",
				fieldtype: "Link",
				options: "Opportunity",
				label: __("Or an Opportunity"),
				default: options.opportunity,
				depends_on: "eval:!doc.project",
			});
		}
		if (any_party) {
			fields.push({
				fieldname: "party_type",
				fieldtype: "Select",
				label: __("Party Type"),
				options: "Customer\nSupplier\nEmployee",
				default: "Customer",
				reqd: 1,
			});
			fields.push({
				fieldname: "party",
				fieldtype: "Dynamic Link",
				options: "party_type",
				label: __("Party"),
				reqd: 1,
				get_options: () => dialog.get_value("party_type"),
			});
		} else {
			fields.push({
				fieldname: "party",
				fieldtype: "Link",
				options: type.party_type,
				label: supplier ? __("Subcontractor") : __("Client"),
				default: options.customer || options.supplier,
				reqd: supplier ? 1 : 0,
				description: supplier ? "" : __("Leave blank to take the project's customer."),
			});
		}
		const dialog = new frappe.ui.Dialog({
			title: __("New {0}", [type.title]),
			fields,
			primary_action_label: __("Start"),
			primary_action: (values) => {
				let source_doctype = null;
				let source_name = null;
				if (values.project) {
					source_doctype = "Project";
					source_name = values.project;
				} else if (values.opportunity) {
					source_doctype = "Opportunity";
					source_name = values.opportunity;
				} else if (supplier && values.party) {
					source_doctype = "Supplier";
					source_name = values.party;
				}
				dialog.disable_primary_action();
				frappe
					.xcall(`${CB_CONTRACT}create_contract`, {
						template: type.name,
						source_doctype,
						source_name,
						party: values.party || null,
					})
					.then((name) => {
						dialog.hide();
						frappe.set_route("contract-builder", name);
					})
					.catch(() => dialog.enable_primary_action());
			},
		});
		dialog.show();
	}

	// ------------------------------------------------------------------ the shell

	// Built once per contract. Steps swap only the stage; the rail and the agreement stay put,
	// so the preview keeps its place and nothing flashes between steps.
	ensure_shell() {
		if (this.$shell && this.$shell.attr("data-contract") === this.data.name && $.contains(document, this.$shell[0])) {
			return;
		}
		this.$paper = null;
		this.preview_key = null;
		const $shell = $(`
			<div data-contract="">
				<header class="cb-top">
					<div class="cb-top-row">
						<button type="button" class="cb-home" title="${__("All contracts")}" aria-label="${__("All contracts")}">
							<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6"/></svg>
						</button>
						<div class="cb-who"><div class="cb-eyebrow"></div><div class="cb-who-name"></div></div>
						<span class="cb-status"></span>
						<span class="cb-save" aria-live="polite"><i></i><span></span></span>
						<div class="cb-mode" role="group" aria-label="${__("Who is filling it in")}">
							<button type="button" data-mode="prepare">${__("Preparing it")}</button>
							<button type="button" data-mode="client">${__("With the {0}", [this.noun()])}</button>
						</div>
					</div>
					<div class="cb-progress">
						<div class="cb-progress-track"><div class="cb-progress-bar"></div></div>
						<span class="cb-progress-label"></span>
					</div>
				</header>
				<div class="cb-layout">
					<nav class="cb-rail" aria-label="${__("Steps")}"><ol></ol><div class="cb-rail-foot"></div></nav>
					<main class="cb-stage"></main>
					<aside class="cb-doc" aria-label="${__("The agreement")}">
						<div class="cb-doc-head"><b>${__("The agreement")}</b><span class="cb-doc-status"></span>
							<button type="button" class="cb-doc-close" aria-label="${__("Close")}">✕</button></div>
						<div class="cb-doc-loading"></div>
						<div class="cb-doc-scroll"><div class="cb-paper-wrap"></div></div>
					</aside>
				</div>
				<button type="button" class="cb-doc-fab">${__("Show the agreement")}</button>
			</div>`);
		$shell.attr("data-contract", this.data.name);
		$shell.find(".cb-home").on("click", () => this.flush().then(() => frappe.set_route("contract-builder")).catch(() => {}));
		$shell.find(".cb-mode button").on("click", (event) => this.set_mode($(event.currentTarget).attr("data-mode")));
		$shell.find(".cb-doc-fab").on("click", () => $shell.find(".cb-doc").addClass("is-open"));
		$shell.find(".cb-doc-close").on("click", () => $shell.find(".cb-doc").removeClass("is-open"));
		const $foot = $shell.find(".cb-rail-foot");
		$("<a></a>")
			.text(__("Open the full form"))
			.on("click", () =>
				this.flush()
					.then(() => frappe.set_route("Form", "Project Contract", this.data.name))
					.catch(() => {})
			)
			.appendTo($foot);
		this.$root.empty().append($shell);
		this.$shell = $shell;
		this.update_chrome();
	}

	update_chrome() {
		if (!this.$shell) return;
		const data = this.data;
		const $s = this.$shell;
		$s.find(".cb-eyebrow").first().text(`${data.template_title || ""} · ${data.name}`);
		$s.find(".cb-who-name").text(this.values.party_display || this.values.party || __("No {0} yet", [this.noun()]));
		const status = data.status || (data.docstatus === 0 ? "Draft" : "");
		const colour = { Signed: "cb-green", "Out for Signature": "cb-blue", Void: "cb-orange" }[status] || "";
		$s.find(".cb-status").html("").append($(`<span class="cb-chip ${colour}"></span>`).text(__(status)));
		$s.find(".cb-mode").toggle(this.can_sign_here());
		$s.find(".cb-mode button").each((i, el) => {
			const on = $(el).attr("data-mode") === this.mode;
			$(el).toggleClass("is-on", on).attr("aria-pressed", on ? "true" : "false");
		});
		const progress = data.progress || { filled: 0, total: 0 };
		const pct = progress.total ? Math.round((100 * progress.filled) / progress.total) : 100;
		$s.find(".cb-progress-bar").css("width", `${status === "Signed" ? 100 : pct}%`);
		$s.find(".cb-progress-label").text(
			status === "Signed"
				? __("Signed")
				: __("{0} of {1} filled in", [progress.filled, progress.total])
		);
		this.update_savestate();
		this.render_rail();
	}

	update_savestate() {
		if (!this.$shell) return;
		const $save = this.$shell.find(".cb-save");
		const dirty = this.dirty && this.data.editable;
		$save.toggleClass("is-dirty", dirty && !this.saving).toggleClass("is-saving", this.saving);
		$save.toggle(!!this.data.editable);
		$save
			.find("span")
			.text(this.saving ? __("Saving…") : dirty ? __("Unsaved changes") : __("All changes saved"));
	}

	render_rail() {
		if (!this.$shell) return;
		const data = this.data;
		const $ol = this.$shell.find(".cb-rail ol").empty();
		const todo = {};
		(data.blanks || []).forEach((blank) => {
			todo[blank.step] = (todo[blank.step] || 0) + 1;
		});
		const current = this.order(this.view);
		(data.steps || []).forEach((step, index) => {
			const left = data.docstatus === 0 ? todo[index] || 0 : 0;
			const done = !left && (index < current || data.docstatus !== 0);
			const meta = left ? __("{0} to fill", [left]) : done ? __("Done") : "";
			this.rail_item(step.key, index + 1, step.title, meta, {
				current: step.key === this.view,
				done,
				todo: !!left && index < current,
			}).appendTo($ol);
		});
		const pending = this.boxes.filter((box) => !this.initials[box.key]).length;
		const signed = data.status === "Signed";
		let meta = "";
		if (signed) meta = __("Signed");
		else if (this.live() && this.boxes.length) meta = __("{0} of {1} initialed", [this.boxes.length - pending, this.boxes.length]);
		this.rail_item(CB_REVIEW, (data.steps || []).length + 1, __("Review and sign"), meta, {
			current: this.view === CB_REVIEW || this.view === CB_SIGN,
			done: signed,
		}).appendTo($ol);
	}

	rail_item(key, number, title, meta, state) {
		const $li = $("<li></li>");
		const $button = $('<button type="button" class="cb-step"></button>')
			.toggleClass("is-current", !!state.current)
			.toggleClass("is-done", !!state.done && !state.current)
			.attr("aria-current", state.current ? "step" : null)
			.append(
				$('<span class="cb-dot"></span>').html(state.done && !state.current ? "&#10003;" : String(number))
			)
			.append(
				$('<span class="cb-step-text"></span>')
					.append($('<span class="cb-step-title"></span>').text(title))
					.append($('<span class="cb-step-meta"></span>').toggleClass("is-todo", !!state.todo).text(meta || ""))
			)
			.on("click", () => {
				if (!state.current) this.save_and_go(key);
			});
		return $li.append($button);
	}

	set_mode(mode) {
		if (mode === this.mode || !this.can_sign_here()) return;
		this.mode = mode === "client" ? "client" : "prepare";
		cb_store(`mode:${this.data.name}`, this.mode);
		this.update_chrome();
		this.decorate();
		if (this.view === CB_REVIEW) this.render_review("fade");
		else this.update_step_hint();
		if (this.live()) {
			frappe.show_alert(
				{
					message: __("With the {0}: each initials box lights up in the agreement as its clause is filled in.", [this.noun()]),
					indicator: "blue",
				},
				6
			);
		}
	}

	// Swap the stage's panel for the next one, sliding in from the side the step lies on.
	swap_stage($panel, dir) {
		const $stage = this.$shell.find(".cb-stage");
		$stage.children().remove();
		$panel.addClass(`cb-panel cb-in-${dir || "fade"}`).appendTo($stage);
		const top = this.$shell.offset().top - 70;
		if (window.scrollY > top + 10) window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
		return $panel;
	}

	// ---------------------------------------------------------------------- a step

	render_step(index, dir) {
		this.ensure_shell();
		const step = this.data.steps[index];
		this.step_index = index;
		this.view = step.key;
		const editable = !!this.data.editable;
		const total = this.data.steps.length;
		const $panel = $(`<section>
			<div class="cb-panel-head"><div class="cb-eyebrow"></div><h2></h2><p class="cb-lede"></p></div>
			<div class="cb-panel-body"><div class="cb-step-hint"></div><div class="cb-fields"></div></div>
			<div class="cb-panel-foot"><div class="cb-foot-left"></div><div class="cb-foot-right"></div></div>
		</section>`);
		$panel.find(".cb-eyebrow").text(__("Step {0} of {1}", [index + 1, total + 1]));
		$panel.find("h2").text(step.title);
		$panel.find(".cb-lede").text(step.intro || "").toggle(!!step.intro);
		if (!editable) {
			$panel
				.find(".cb-step-hint")
				.before(
					$('<div class="cb-notice is-info"></div>').text(
						__("This contract has been issued, so its terms are fixed. To change them, cancel and amend it from the full form.")
					)
				);
		}
		this.swap_stage($panel, dir);

		// Built once the panel is on the page: some controls measure themselves as they are made.
		const $body = $panel.find(".cb-fields");
		this.loading = true;
		const fields = step.fields
			.filter((field) => field.fieldtype !== "Column Break")
			.map((field) => this.control_def(field, editable));
		this.fg = new frappe.ui.FieldGroup({ fields, body: $body[0] });
		this.fg.make();
		const initial = {};
		step.fields.forEach((field) => {
			if (field.fieldtype === "Table" && !field.fixed_rows) return;
			if (this.values[field.fieldname] !== undefined) initial[field.fieldname] = this.values[field.fieldname];
		});
		this.fg.set_values(initial).then(() => {
			this.fg.refresh_dependency();
			this.loading = false;
		});
		step.fields
			.filter((field) => field.fieldtype === "Table" && field.fixed_rows)
			.forEach((field) => this.render_choices(field, editable));
		$body.on("focusin", ".frappe-control", (event) => {
			const $control = $(event.currentTarget);
			$body.find(".cb-is-focus").removeClass("cb-is-focus");
			$control.addClass("cb-is-focus");
			this.focus_field($control.attr("data-fieldname"));
		});

		const $left = $panel.find(".cb-foot-left");
		const $right = $panel.find(".cb-foot-right");
		if (index > 0) {
			$(`<button class="btn btn-default">${__("Back")}</button>`)
				.on("click", () => this.save_and_go(this.data.steps[index - 1].key))
				.appendTo($left);
		}
		if (editable) {
			$(`<button class="btn btn-default">${__("Save")}</button>`)
				.on("click", () => this.flush(true).catch(() => {}))
				.appendTo($right);
		}
		const next = this.data.steps[index + 1];
		$('<button class="btn btn-primary"></button>')
			.text(next ? __("Next: {0}", [next.title]) : __("Review and sign"))
			.append(' <span aria-hidden="true">→</span>')
			.on("click", () => this.save_and_go(next ? next.key : CB_REVIEW))
			.appendTo($right);

		this.update_chrome();
		this.refresh_preview({ now: true, scroll: true });
	}

	control_def(field, editable) {
		const df = Object.assign({}, field);
		delete df.child_fields;
		if (!editable) df.read_only = 1;
		["depends_on", "mandatory_depends_on", "read_only_depends_on"].forEach((key) => {
			if (df[key]) df[key] = this.condition(df[key]);
		});
		if (df.fieldtype === "Table" && field.fixed_rows) {
			// Drawn by render_choices into this placeholder: a menu of options, not a grid.
			return { fieldtype: "HTML", fieldname: `${field.fieldname}__choices`, label: field.label };
		}
		if (df.fieldtype === "Table") {
			const rows = (this.values[field.fieldname] || []).map((row) => Object.assign({}, row));
			// A cell edit fires the child field's onchange, never the table's, so each column
			// marks the step dirty itself; added and removed rows are caught by tables_changed().
			const touched = () => {
				if (this.loading) return;
				this.changed(field.fieldname);
			};
			df.fields = (field.child_fields || []).map((child) =>
				Object.assign({}, child, editable ? { onchange: touched } : { read_only: 1 })
			);
			df.data = rows;
			df.get_data = () => rows;
			df.in_place_edit = true;
			df.cannot_add_rows = !editable;
			df.cannot_delete_rows = !editable;
		}
		if (df.fieldtype === "Dynamic Link") {
			df.get_options = () => this.current("party_type");
		}
		df.onchange = () => {
			if (this.loading) return;
			this.changed(field.fieldname);
			if (this.fg) this.fg.refresh_dependency();
		};
		return df;
	}

	changed(fieldname) {
		this.dirty = true;
		this.collect();
		this.update_savestate();
		this.refresh_preview({ from_field: fieldname });
	}

	// depends_on is written against the whole contract (doc.template_key, doc.payment_method …),
	// but a FieldGroup only knows the fields of this one step. Evaluate it against the contract's
	// values with the step's live inputs laid over them.
	condition(expression) {
		if (typeof expression !== "string") return expression;
		return (step_values) => {
			const doc = Object.assign({}, this.values, step_values || {});
			if (expression.startsWith("eval:")) {
				return frappe.utils.eval(expression.slice(5), { doc, parent: doc });
			}
			const value = doc[expression];
			return Array.isArray(value) ? value.length > 0 : !!value;
		};
	}

	current(fieldname) {
		if (this.fg && this.fg.fields_dict[fieldname]) return this.fg.get_value(fieldname);
		return this.values[fieldname];
	}

	// Every input of the step into this.values, empties included: FieldGroup.get_values() drops
	// an empty field, which would make clearing one impossible.
	collect() {
		if (!this.fg) return;
		Object.keys(this.fg.fields_dict).forEach((fieldname) => {
			const control = this.fg.fields_dict[fieldname];
			if (!control.get_value || fieldname.endsWith("__choices")) return;
			if (["HTML", "Column Break", "Section Break", "Button"].includes(control.df.fieldtype)) return;
			const value = control.get_value();
			this.values[fieldname] = value === undefined ? null : value;
		});
	}

	// A fixed menu of rows (the maintenance plan's options, an owner contract's phases): tick it,
	// price it. The rows are the contract's own; nothing is added or removed here.
	render_choices(field, editable) {
		const control = this.fg.fields_dict[`${field.fieldname}__choices`];
		if (!control) return;
		const rows = this.values[field.fieldname] || [];
		const children = field.child_fields || [];
		const tick = children.find((c) => c.fieldtype === "Check");
		const label = children.find((c) => c.fieldtype === "Data" && c.read_only);
		const money = children.filter((c) => ["Currency", "Float", "Int"].includes(c.fieldtype) && !c.hidden);
		const unit = children.find((c) => c.fieldtype === "Data" && !c.read_only && !c.hidden);
		const $list = $('<div class="cb-choices"></div>');
		if (field.label) $list.append($('<div class="cb-choices-label"></div>').text(__(field.label)));
		rows.forEach((row) => {
			const on = !!(tick && row[tick.fieldname]);
			const per = unit && row[unit.fieldname] ? row[unit.fieldname] : "";
			const $row = $('<div class="cb-choice"></div>').toggleClass("is-on", on);
			const title = label ? row[label.fieldname] || "" : "";
			const $check = $('<input type="checkbox" class="cb-tick">')
				.prop("checked", on)
				.prop("disabled", !editable || !tick)
				.attr("aria-label", title);
			const toggle = (checked) => {
				row[tick.fieldname] = checked ? 1 : 0;
				$check.prop("checked", checked);
				$row.toggleClass("is-on", checked);
				this.changed(field.fieldname);
			};
			$check.on("change", () => toggle($check.prop("checked")));
			$row.append($check);
			$('<div class="cb-choice-main"></div>')
				.append($('<div class="cb-choice-title"></div>').text(title))
				.append($('<div class="cb-choice-sub"></div>').text(per ? __("Priced per {0}", [per]) : ""))
				.on("click", () => {
					if (editable && tick) toggle(!$check.prop("checked"));
				})
				.appendTo($row);
			const $prices = $('<div class="cb-prices"></div>').appendTo($row);
			money.forEach((child) => {
				const $input = $('<input type="number" step="0.01" min="0" inputmode="decimal">')
					.attr("aria-label", `${title} ${__(child.label)}`)
					.val(row[child.fieldname] || "")
					.prop("disabled", !editable)
					.on("input", () => {
						row[child.fieldname] = $input.val() === "" ? null : flt($input.val());
						this.changed(field.fieldname);
					})
					.on("focus", () => this.focus_field(field.fieldname));
				const $box = $('<div class="cb-price-box"><span>$</span></div>').append($input);
				if (per && money.length === 1) $box.append($("<span></span>").text(`/ ${per}`));
				$('<label class="cb-price"></label>')
					.append(money.length > 1 ? $("<small></small>").text(__(child.label)) : "")
					.append($box)
					.appendTo($prices);
			});
			$list.append($row);
		});
		$(control.wrapper).empty().append($list);
	}

	// -------------------------------------------------------------------- saving

	// Rows added to or removed from a grid fire no change at all, so compare the step's tables
	// with what was last saved.
	tables_changed() {
		if (!this.fg || !this.data || !this.data.steps) return false;
		const step = this.data.steps[this.step_index];
		if (!step || this.view !== step.key) return false;
		return step.fields.some((field) => {
			if (field.fieldtype !== "Table" || field.fixed_rows) return false;
			const control = this.fg.fields_dict[field.fieldname];
			if (!control || !control.get_value) return false;
			const names = (field.child_fields || []).map((child) => child.fieldname);
			const shape = (rows) =>
				JSON.stringify((rows || []).map((row) => names.map((name) => (row[name] === undefined ? null : row[name]))));
			return shape(control.get_value()) !== shape(this.data.doc[field.fieldname]);
		});
	}

	flush(announce) {
		if (this.data && this.data.editable && !this.dirty && this.tables_changed()) this.dirty = true;
		if (!this.data || !this.dirty || !this.data.editable) {
			if (announce) frappe.show_alert({ message: __("Saved"), indicator: "green" });
			return Promise.resolve();
		}
		this.collect();
		const view = this.view;
		this.saving = true;
		this.update_savestate();
		return frappe
			.xcall(`${CB_API}save_builder`, { name: this.data.name, values: JSON.stringify(this.values) })
			.then((data) => {
				this.saving = false;
				this.set_data(data);
				if (announce) {
					frappe.show_alert({ message: __("Saved"), indicator: "green" });
					// Totals and the party's name are worked out on save: show them.
					if (view && view !== CB_REVIEW && view !== CB_SIGN) this.show(view);
				}
			})
			.catch((error) => {
				this.saving = false;
				this.update_savestate();
				throw error;
			});
	}

	// ------------------------------------------------------------------- the paper

	preview_args() {
		// The executed copy once signed; otherwise the values on screen, so the client's
		// signature line is marked for the builder (project_contract._preview_doc).
		if (this.data.status === "Signed") return { name: this.data.name };
		return { doc: JSON.stringify(this.values) };
	}

	refresh_preview(opts) {
		if (!this.$shell) return;
		const options = opts || {};
		const args = this.preview_args();
		const key = JSON.stringify(args);
		clearTimeout(this.preview_timer);
		if (key === this.preview_key && this.$paper) {
			this.highlight(options.scroll);
			return;
		}
		this.preview_timer = setTimeout(() => this.paint(args, key, options), options.now ? 0 : 450);
	}

	paint(args, key, options) {
		const seq = ++this.preview_seq;
		const $doc = this.$shell.find(".cb-doc").addClass("is-loading");
		return frappe
			.xcall(`${CB_CONTRACT}get_contract_html`, args)
			.then((payload) => {
				if (seq !== this.preview_seq || !payload || !this.$shell) return;
				this.ensure_styles(payload.css);
				const $scroll = this.$shell.find(".cb-doc-scroll");
				const top = $scroll.scrollTop();
				const before = this.$paper ? this.block_texts(this.$paper[0]) : null;
				const $paper = $('<div class="contract-doc"></div>')
					.addClass(this.data.template_key ? `contract-${this.data.template_key}` : "")
					.html(payload.html);
				const $wrap = this.$shell.find(".cb-paper-wrap").empty();
				if (payload.executed) {
					$wrap.append($('<p class="cb-paper-note"></p>').text(__("The executed copy: the document that was signed.")));
				}
				$wrap.append($paper);
				this.$paper = $paper;
				this.preview_key = key;
				this.sectionize();
				const changed = before ? this.mark_changes(before) : [];
				this.decorate();
				$scroll.css("scroll-behavior", "auto").scrollTop(top).css("scroll-behavior", "");
				this.highlight(options.scroll);
				if (options.from_field && changed.length) this.bring_into_view(changed[0]);
			})
			.finally(() => $doc.removeClass("is-loading"));
	}

	ensure_styles(css) {
		let style = document.getElementById(CB_STYLE_ID);
		if (!style) {
			style = document.createElement("style");
			style.id = CB_STYLE_ID;
			document.head.appendChild(style);
		}
		const text = `${CB_PAPER_CSS}\n${css || ""}`;
		if (style.textContent !== text) style.textContent = text;
	}

	// Each <h1> and what follows it, up to the next, becomes one <section>: the unit the builder
	// lights up. Only the builder's copy of the paper is touched.
	sectionize() {
		this.$paper.find("h1").each((i, h1) => {
			const section = document.createElement("section");
			section.className = "cb-sec";
			section.setAttribute("data-key", cb_key(h1.textContent));
			h1.parentNode.insertBefore(section, h1);
			let node = h1;
			while (node && !(node !== h1 && node.nodeType === 1 && node.tagName === "H1")) {
				const next = node.nextSibling;
				section.appendChild(node);
				node = next;
			}
		});
	}

	block_texts(root) {
		const seen = new Set();
		root.querySelectorAll(CB_BLOCKS).forEach((block) => {
			const text = cb_owned_text(block);
			if (text) seen.add(text);
		});
		return seen;
	}

	// What a change just did to the agreement, flashed where it landed.
	mark_changes(before) {
		const changed = [];
		this.$paper[0].querySelectorAll(CB_BLOCKS).forEach((block) => {
			const text = cb_owned_text(block);
			if (!text || before.has(text) || block.querySelector(CB_BLOCKS)) return;
			changed.push(block);
		});
		if (changed.length > 12) return []; // a whole new layout (a choice that reshapes the text), not an edit
		changed.forEach((block) => {
			block.classList.add("cb-changed");
			setTimeout(() => block.classList.remove("cb-changed"), 2000);
		});
		return changed;
	}

	sections_for(fieldnames) {
		const wanted = new Set();
		(fieldnames || []).forEach((name) => {
			((this.data.field_sections || {})[name] || []).forEach((heading) => wanted.add(cb_key(heading)));
		});
		if (!wanted.size || !this.$paper) return [];
		return this.$paper
			.find("section.cb-sec")
			.toArray()
			.filter((section) => {
				const key = section.getAttribute("data-key") || "";
				for (const heading of wanted) if (key.startsWith(heading)) return true;
				return false;
			});
	}

	step_sections() {
		const step = (this.data.steps || []).find((s) => s.key === this.view);
		if (!step) return [];
		return this.sections_for(step.fields.map((f) => f.fieldname));
	}

	// Light up the part of the agreement this step fills in; dim the rest a little.
	highlight(scroll) {
		if (!this.$paper) return;
		this.$paper.find("section.cb-sec").removeClass("is-here is-focus").removeAttr("data-here");
		const here = this.step_sections();
		const step = (this.data.steps || []).find((s) => s.key === this.view);
		this.$paper.toggleClass("cb-has-here", !!here.length);
		here.forEach((section) => section.classList.add("is-here"));
		if (here.length) here[0].setAttribute("data-here", step ? __("Step {0}: {1}", [this.step_index + 1, step.title]) : __("Filling in now"));
		if (scroll && here.length) this.bring_into_view(here[0], "start");
		this.update_step_hint();
	}

	focus_field(fieldname) {
		if (!this.$paper) return;
		this.$paper.find("section.cb-sec.is-focus").removeClass("is-focus");
		const sections = this.sections_for([fieldname]);
		sections.forEach((section) => section.classList.add("is-focus"));
		if (sections.length) this.bring_into_view(sections[0], "start", true);
	}

	// Scroll the agreement (not the page) so an element shows, unless it already does.
	bring_into_view(element, block, only_if_hidden) {
		const scroller = this.$shell && this.$shell.find(".cb-doc-scroll")[0];
		if (!scroller || !element) return;
		const box = scroller.getBoundingClientRect();
		const rect = element.getBoundingClientRect();
		const visible = rect.top >= box.top + 8 && rect.top <= box.bottom - 60;
		if (visible && (only_if_hidden || block !== "start")) return;
		const offset = rect.top - box.top + scroller.scrollTop;
		const target = block === "start" ? offset - 14 : offset - box.height / 2 + rect.height / 2;
		scroller.scrollTo({ top: Math.max(0, target), behavior: "smooth" });
	}

	// --------------------------------------------------------------- initials + signature

	clause_block(span) {
		const block = span.closest(CB_BLOCKS);
		if (block && cb_owned_text(block)) return block;
		const all = Array.from(this.$paper[0].querySelectorAll(CB_BLOCKS));
		let index = block ? all.indexOf(block) : all.findIndex((b) => b.compareDocumentPosition(span) & Node.DOCUMENT_POSITION_PRECEDING);
		if (!block && index < 0) index = all.length;
		for (let i = index - 1; i >= 0; i--) {
			if (cb_owned_text(all[i])) return all[i];
		}
		return block;
	}

	// The text a set of initials is drawn beside: esign/initials.py clause_texts.
	clause_text(span) {
		const block = this.clause_block(span);
		return block ? cb_owned_text(block) : "";
	}

	// Each initials box in the paper, drawn for what it is now: waiting for its clause to be filled
	// in, ready to initial, or initialed. Initials whose clause changed since are dropped.
	decorate() {
		if (!this.$paper) return;
		const live = this.live();
		const signed = this.data.status === "Signed";
		const stale = [];
		const seen = new Set();
		this.boxes = [];
		this.$paper.find(".ct-initial[data-initial]").each((i, el) => {
			const key = el.getAttribute("data-initial");
			if (!key || seen.has(key) || el.classList.contains("is-signed")) return;
			seen.add(key);
			const label = el.getAttribute("data-label") || key;
			const block = this.clause_block(el);
			const clause = block ? cb_owned_text(block) : "";
			const saved = this.initials[key];
			if (saved && saved.clause !== clause) {
				delete this.initials[key];
				stale.push(label);
			}
			const complete = !!clause && !(block && block.querySelector(".ct-blank"));
			const state = this.initials[key] ? "done" : complete ? "ready" : "wait";
			const box = { key, label, el, state, clause };
			this.boxes.push(box);
			this.paint_box(box, live && !signed);
		});
		if (stale.length) {
			this.save_initials();
			frappe.show_alert(
				{
					message: __("{0} changed after it was initialed, so it needs initialing again.", [stale.join(", ")]),
					indicator: "orange",
				},
				8
			);
		}
		this.decorate_signature();
		this.update_doc_status();
		this.render_rail();
	}

	paint_box(box, live) {
		const el = box.el;
		el.className = `ct-initial cb-ini is-${box.state}${live ? " is-live" : ""}`;
		el.innerHTML = "";
		if (box.state === "done") {
			const img = document.createElement("img");
			img.src = this.initials[box.key].image;
			img.alt = __("{0} initials", [this.noun(true)]);
			el.appendChild(img);
		} else {
			el.textContent = box.state === "ready" ? (live ? __("Initial here") : __("Initials")) : __("Fill in first");
		}
		el.title = box.state === "wait" ? __("Fill in this clause first: {0}", [box.label]) : box.label;
		$(el).off(".cb");
		if (live && box.state !== "wait") {
			el.setAttribute("role", "button");
			el.setAttribute("tabindex", "0");
			el.setAttribute("aria-label", __("Initial: {0}", [box.label]));
			$(el).on("click.cb", () => this.open_initial(box));
			$(el).on("keydown.cb", (event) => {
				if (event.key === "Enter" || event.key === " ") {
					event.preventDefault();
					this.open_initial(box);
				}
			});
		} else {
			el.removeAttribute("role");
			el.removeAttribute("tabindex");
		}
	}

	save_initials() {
		cb_store(`initials:${this.data.name}`, Object.keys(this.initials).length ? this.initials : null);
	}

	update_doc_status() {
		if (!this.$shell) return;
		const total = this.boxes.length;
		const done = this.boxes.filter((box) => this.initials[box.key]).length;
		let text = "";
		if (this.data.status === "Signed") text = __("Signed");
		else if (this.live() && total) text = __("{0} of {1} clauses initialed", [done, total]);
		else if (total) text = __("{0} clauses for the {1} to initial", [total, this.noun()]);
		this.$shell.find(".cb-doc-status").text(text);
	}

	update_step_hint() {
		if (!this.$shell || this.view === CB_REVIEW || this.view === CB_SIGN) return;
		const $hint = this.$shell.find(".cb-step-hint").empty();
		if (!this.live() || !this.$paper) return;
		const here = new Set(this.step_sections());
		const boxes = this.boxes.filter((box) => here.has(box.el.closest("section.cb-sec")));
		if (!boxes.length) return;
		const left = boxes.filter((box) => !this.initials[box.key]);
		const ready = left.filter((box) => box.state === "ready");
		const $note = $('<div class="cb-notice is-info cb-hint-row"></div>').appendTo($hint);
		if (!left.length) {
			$note.removeClass("is-info").addClass("is-ok").text(__("The {0} has initialed every clause in this part.", [this.noun()]));
			return;
		}
		$note.append(
			$("<span></span>").text(
				ready.length
					? __("{0} clause(s) in this part are ready for the {1}'s initials.", [ready.length, this.noun()])
					: __("{0} clause(s) here need the {1}'s initials once they are filled in.", [left.length, this.noun()])
			)
		);
		if (ready.length) {
			$('<button type="button" class="cb-link-btn"></button>')
				.text(__("Show me"))
				.on("click", () => this.show_box(ready[0]))
				.appendTo($note);
		}
	}

	show_box(box) {
		this.$shell.find(".cb-doc").addClass("is-open");
		this.bring_into_view(box.el, "center");
		box.el.classList.add("is-flash");
		setTimeout(() => box.el.classList.remove("is-flash"), 1600);
		if (box.el.focus) box.el.focus({ preventScroll: true });
	}

	// One set of initials, on a pad that starts empty every time (a redo included), with the
	// clause quoted above it so the client reads what they are initialing.
	open_initial(box) {
		if (!this.live()) return;
		this.close_modal();
		const $modal = $(`<div class="cb-modal" role="dialog" aria-modal="true">
			<div class="cb-sheet">
				<div class="cb-eyebrow">${__("{0} initials", [this.noun(true)])}</div>
				<h3></h3>
				<div class="cb-quote"></div>
				<div class="cb-padbox is-short"><canvas aria-label="${__("Initials pad")}"></canvas>
					<span class="cb-pad-line"></span><span class="cb-pad-hint">${__("Draw your initials here")}</span>
					<button type="button" class="cb-pad-clear">${__("Clear")}</button></div>
				<p class="cb-sheet-error" hidden></p>
				<div class="cb-sheet-actions">
					<button type="button" class="btn btn-default cb-cancel">${__("Cancel")}</button>
					<button type="button" class="btn btn-primary cb-apply">${__("Initial this clause")}</button>
				</div>
			</div></div>`);
		$modal.find("h3").text(box.label);
		const quote = box.clause.replace(/^(\d+(?:\.\d+)*)(?=\S)/, "$1 ");
		$modal.find(".cb-quote").text(quote.length > 600 ? `${quote.slice(0, 600)}…` : quote);
		$modal.appendTo(document.body);
		this.$modal = $modal;
		const pad = new CBPad($modal.find("canvas")[0]);
		requestAnimationFrame(() => pad.reset());
		const $error = $modal.find(".cb-sheet-error");
		$modal.find(".cb-pad-clear").on("click", () => pad.reset());
		$modal.find(".cb-cancel").on("click", () => this.close_modal(box));
		$modal.on("click", (event) => {
			if (event.target === $modal[0]) this.close_modal(box);
		});
		this.modal_keys = (event) => {
			if (event.key === "Escape") this.close_modal(box);
		};
		document.addEventListener("keydown", this.modal_keys);
		$modal.find(".cb-apply").on("click", () => {
			const image = pad.png();
			if (!image) {
				$error.text(__("Draw the initials in the box first.")).prop("hidden", false);
				return;
			}
			cb_png_digest(image).then((sha) => {
				const copy = sha && Object.keys(this.initials).some((key) => key !== box.key && this.initials[key].sha === sha);
				if (copy) {
					$error.text(__("Each set of initials has to be drawn separately.")).prop("hidden", false);
					pad.reset();
					return;
				}
				this.initials[box.key] = { image, clause: box.clause, sha, label: box.label };
				this.save_initials();
				this.close_modal();
				box.state = "done";
				this.paint_box(box, true);
				this.update_doc_status();
				this.render_rail();
				this.update_step_hint();
				if (this.view === CB_REVIEW) this.render_review_checks();
				this.advance_from(box);
			});
		});
	}

	close_modal(box) {
		if (this.modal_keys) document.removeEventListener("keydown", this.modal_keys);
		this.modal_keys = null;
		if (this.$modal) this.$modal.remove();
		this.$modal = null;
		if (box && box.el && box.el.focus) box.el.focus({ preventScroll: true });
	}

	// Nik: "when doing one initial can you have it auto scroll to the next and the next". On to the
	// next box waiting in this part, then anywhere after it, then any skipped earlier; it is shown,
	// not opened, so the client reads the clause first. Once the last is done, on to the signature.
	advance_from(box) {
		const ready = this.boxes.filter((b) => b.state === "ready" && !this.initials[b.key]);
		const here = new Set(this.step_sections());
		const start = this.boxes.indexOf(box);
		const next =
			ready.find((b) => here.has(b.el.closest("section.cb-sec")) && this.boxes.indexOf(b) > start) ||
			ready.find((b) => this.boxes.indexOf(b) > start) ||
			ready[0];
		if (next) return this.show_box(next);
		const pending = this.boxes.filter((b) => !this.initials[b.key]);
		if (!pending.length) {
			frappe.show_alert({ message: __("Every clause is initialed."), indicator: "green" }, 5);
			const line = this.$paper && this.$paper.find('.ct-sig-line[data-sig="client"]')[0];
			if (line) this.bring_into_view(line, "center");
		}
	}

	// The client's signature line in the paper: "Sign here" with the client, then their signature
	// as it is captured, ahead of execution.
	decorate_signature() {
		if (!this.$paper) return;
		const line = this.$paper.find('.ct-sig-line[data-sig="client"]')[0];
		if (!line) return;
		if (!line.hasAttribute("data-blank")) line.setAttribute("data-blank", line.innerHTML);
		const sig = this.sig;
		const has_sig = sig.mode === "Drawn" ? !!sig.image : !!cb_norm(sig.name);
		if (!this.live() || (!has_sig && this.view === CB_REVIEW && !this.$shell.find(".cb-signbox").length)) {
			line.className = "ct-sig-line";
			line.innerHTML = line.getAttribute("data-blank");
			return;
		}
		line.className = "ct-sig-line cb-sig";
		line.innerHTML = "";
		const deco = document.createElement("span");
		deco.className = "cb-deco";
		if (has_sig) {
			if (sig.mode === "Drawn") {
				const img = document.createElement("img");
				img.className = "cb-sig-img";
				img.src = sig.image;
				img.alt = __("Signature");
				deco.appendChild(img);
			} else {
				const typed = document.createElement("span");
				typed.className = "cb-sig-typed";
				typed.textContent = cb_norm(sig.name);
				deco.appendChild(typed);
			}
			const pending = document.createElement("span");
			pending.className = "cb-sig-pending";
			pending.textContent = __("not yet executed");
			deco.appendChild(pending);
		} else {
			const button = document.createElement("button");
			button.type = "button";
			button.className = "cb-sig-here";
			button.textContent = __("Sign here");
			button.addEventListener("click", () => this.go_sign());
			deco.appendChild(button);
		}
		line.appendChild(deco);
	}

	go_sign() {
		if (this.view === CB_REVIEW) {
			const $box = this.$shell.find(".cb-signbox");
			if ($box.length) {
				$box[0].scrollIntoView({ behavior: "smooth", block: "center" });
				$box.find("input:visible").first().trigger("focus");
			}
			return;
		}
		this.save_and_go(CB_REVIEW);
	}

	// -------------------------------------------------------------------- review

	render_review(dir) {
		this.ensure_shell();
		this.fg = null;
		this.view = CB_REVIEW;
		const data = this.data;
		const signed = data.status === "Signed";
		const $panel = $(`<section>
			<div class="cb-panel-head"><div class="cb-eyebrow"></div><h2></h2><p class="cb-lede"></p></div>
			<div class="cb-panel-body"><div class="cb-checks"></div><div class="cb-review-main"></div></div>
		</section>`);
		$panel.find(".cb-eyebrow").text(signed ? __("Complete") : __("Last step"));
		$panel.find("h2").text(signed ? __("Signed") : __("Review and sign"));
		$panel
			.find(".cb-lede")
			.text(
				signed
					? __("The agreement is executed. The signed copy is on the right, and the {0} has been emailed theirs.", [this.noun()])
					: this.live()
					? __("Read it through together on the right. When every clause is initialed, the {0} signs below.", [this.noun()])
					: __("Read it through on the right, then sign it with the {0} here or send it to them.", [this.noun()])
			);
		this.swap_stage($panel, dir);
		this.render_review_checks();
		const $main = $panel.find(".cb-review-main");
		if (signed) this.render_signed($main);
		else if (data.docstatus === 2) {
			$main.append(
				$('<div class="cb-notice is-warn"></div>').text(
					__("This contract was cancelled. Amend it from the full form to make a new revision.")
				)
			);
		} else if (this.live()) this.render_signbox($main);
		else this.render_send_options($main);
		this.update_chrome();
		this.refresh_preview({ now: true });
		if (this.$paper) {
			this.highlight(false);
			this.decorate_signature();
		}
	}

	render_review_checks() {
		const $checks = this.$shell && this.$shell.find(".cb-checks");
		if (!$checks || !$checks.length) return;
		$checks.empty();
		const data = this.data;
		if (data.status === "Signed" || data.docstatus === 2) return;
		const check = (ok, title, $detail) =>
			$('<div class="cb-check"></div>')
				.addClass(ok ? "is-ok" : "is-todo")
				.append($('<span class="cb-check-icon"></span>').html(ok ? "&#10003;" : "!"))
				.append($('<div class="cb-check-body"></div>').append($("<b></b>").text(title)).append($detail || ""))
				.appendTo($checks);

		if (data.docstatus === 0) {
			const blanks = data.blanks || [];
			if (blanks.length) {
				const $ul = $("<ul></ul>");
				blanks.forEach((blank, index) => {
					const step = data.steps[blank.step];
					$("<li></li>")
						.toggle(index < 5)
						.append(document.createTextNode(`${blank.label} `))
						.append(step ? $("<a></a>").text(__("({0})", [step.title])).on("click", () => this.go(step.key)) : "")
						.appendTo($ul);
				});
				if (blanks.length > 5) {
					$('<li style="list-style:none;margin-left:-18px"></li>')
						.append(
							$('<button type="button" class="cb-link-btn"></button>')
								.text(__("Show all {0}", [blanks.length]))
								.on("click", (event) => {
									$ul.children().show();
									$(event.currentTarget).parent().remove();
								})
						)
						.appendTo($ul);
				}
				check(
					false,
					__("{0} will print as blank lines", [blanks.length]),
					$("<div></div>")
						.text(__("Fine for anything the {0} fills in by hand; otherwise go back and fill them in.", [this.noun()]))
						.append($ul)
				);
			} else {
				check(true, __("Everything the agreement prints is filled in"));
			}
		}
		if (this.live() && this.boxes.length) {
			const left = this.boxes.filter((box) => !this.initials[box.key]);
			const $detail = $("<div></div>");
			if (left.length) {
				const ready = left.filter((box) => box.state === "ready");
				$detail.text(
					ready.length
						? __("Tap each highlighted box in the agreement. Each set is drawn separately.")
						: __("The rest light up once their clauses are filled in.")
				);
				if (ready.length) {
					$detail.append(" ").append(
						$('<button type="button" class="cb-link-btn"></button>')
							.text(__("Take me to the next one"))
							.on("click", () => this.show_box(ready[0]))
					);
				}
			}
			check(
				!left.length,
				left.length
					? __("{0} of {1} clauses initialed", [this.boxes.length - left.length, this.boxes.length])
					: __("Every clause is initialed"),
				$detail
			);
		}
		const live = ["Sent", "Viewed"].includes((data.signature || {}).status) ? data.signature : null;
		if (live) {
			check(
				true,
				live.signing_channel === "In Person" ? __("Open for signing on another device") : __("Out for signature"),
				$("<div></div>").text(
					live.signing_channel === "In Person"
						? __("A signing link is open on a device here.")
						: __("Sent to {0}; {1}.", [
								live.signer_email,
								live.view_count ? __("they have opened it") : __("not opened yet"),
						  ])
				)
			);
		}
		this.update_signbox();
	}

	// The signature, captured right here with the client: their name, where the signed copy goes,
	// typed or drawn, and the consent the signing page asks for, word for word.
	render_signbox($main) {
		const data = this.data;
		const signing = data.signing || {};
		const sig = this.sig;
		const $box = $(`<div class="cb-signbox">
			<h3>${__("The {0}'s signature", [this.noun()])}</h3>
			<p class="cb-lede" style="font-size:13px"></p>
			<div class="cb-form-grid">
				<div><label for="cb-sig-name">${__("Full name")}</label><input id="cb-sig-name" class="form-control" maxlength="140" autocomplete="off"></div>
				<div><label for="cb-sig-title">${__("Title (optional)")}</label><input id="cb-sig-title" class="form-control" maxlength="140" autocomplete="off"></div>
				<div style="grid-column:1/-1"><label for="cb-sig-email">${__("Email for their signed copy")}</label><input id="cb-sig-email" type="email" class="form-control" autocomplete="off"></div>
			</div>
			<div class="cb-tabs" role="tablist"><button type="button" data-sig-mode="Typed">${__("Type it")}</button><button type="button" data-sig-mode="Drawn">${__("Draw it")}</button></div>
			<div class="cb-sig-typed-pane"><div class="cb-typed-preview" aria-hidden="true"></div></div>
			<div class="cb-sig-drawn-pane"><div class="cb-padbox"><canvas aria-label="${__("Signature pad")}"></canvas>
				<span class="cb-pad-line"></span><span class="cb-pad-hint">${__("Sign here")}</span>
				<button type="button" class="cb-pad-clear">${__("Clear")}</button></div></div>
			<details class="cb-disclosure"><summary>${__("How electronic signing works, and the right to a paper copy")}</summary><div></div></details>
			<label class="cb-consent"><input type="checkbox" id="cb-sig-agree"><span></span></label>
			<div class="cb-actions"><button type="button" class="btn btn-primary cb-big cb-execute">${__("Sign & execute")}</button></div>
			<p class="cb-missing"></p>
		</div>`);
		$box
			.find(".cb-lede")
			.text(
				__("Presented in person by {0}. Issuing and signing happen together: the terms lock the moment the {1} signs.", [
					signing.presenter || frappe.session.user_fullname || "",
					this.noun(),
				])
			);
		$box.find(".cb-disclosure div").text(signing.disclosure_text || "");
		$box.find(".cb-disclosure").toggle(!!signing.disclosure_text);
		$box
			.find(".cb-consent span")
			.text(signing.consent_text || __("I have read this agreement and intend to sign it electronically."));
		$box.find("#cb-sig-name").val(sig.name);
		$box.find("#cb-sig-title").val(sig.title);
		$box.find("#cb-sig-email").val(sig.email);
		$box.find("#cb-sig-agree").prop("checked", !!sig.agree);
		$main.append($box);

		const pad = new CBPad($box.find(".cb-sig-drawn-pane canvas")[0]);
		pad.on_ink = () => {
			clearTimeout(this.sig_timer);
			this.sig_timer = setTimeout(() => {
				sig.image = pad.png();
				this.decorate_signature();
				this.update_signbox();
			}, 250);
		};
		$box.find(".cb-pad-clear").on("click", () => {
			pad.reset();
			sig.image = "";
			this.decorate_signature();
			this.update_signbox();
		});
		const set_mode = (mode) => {
			sig.mode = mode;
			$box.find(".cb-tabs button").each((i, el) => $(el).toggleClass("is-on", $(el).attr("data-sig-mode") === mode));
			$box.find(".cb-sig-typed-pane").toggle(mode === "Typed");
			$box.find(".cb-sig-drawn-pane").toggle(mode === "Drawn");
			if (mode === "Drawn") requestAnimationFrame(() => (sig.image ? null : pad.reset()));
			this.decorate_signature();
			this.update_signbox();
		};
		$box.find(".cb-tabs button").on("click", (event) => set_mode($(event.currentTarget).attr("data-sig-mode")));
		set_mode(sig.mode === "Drawn" && sig.image ? "Typed" : sig.mode);
		$box.find(".cb-typed-preview").text(sig.name);
		$box.find("#cb-sig-name").on("input", (event) => {
			sig.name = event.target.value;
			$box.find(".cb-typed-preview").text(sig.name);
			this.decorate_signature();
			this.update_signbox();
		});
		$box.find("#cb-sig-title").on("input", (event) => {
			sig.title = event.target.value;
		});
		$box.find("#cb-sig-email").on("input", (event) => {
			sig.email = event.target.value;
			this.update_signbox();
		});
		$box.find("#cb-sig-agree").on("change", (event) => {
			sig.agree = event.target.checked;
			this.update_signbox();
		});
		$box.find(".cb-execute").on("click", () => this.execute());

		if (data.can_send) {
			$main.append(`<div class="cb-or">${__("or")}</div>`);
			const $more = $('<div class="cb-actions"></div>').appendTo($main);
			$('<button class="btn btn-default"></button>')
				.text(__("Send it to them to sign at their own pace"))
				.on("click", () => this.send_dialog())
				.appendTo($more);
			$('<button class="btn btn-default"></button>')
				.text(__("Hand them another device"))
				.on("click", () => this.sign_here_dialog())
				.appendTo($more);
		}
		this.update_signbox();
	}

	sign_blockers() {
		const sig = this.sig;
		const missing = [];
		const left = this.boxes.filter((box) => !this.initials[box.key]).length;
		if (left) missing.push(__("{0} clause(s) to initial", [left]));
		if (!cb_norm(sig.name)) missing.push(__("their full name"));
		if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(cb_norm(sig.email))) missing.push(__("an email for their copy"));
		if (sig.mode === "Drawn" && !sig.image) missing.push(__("their drawn signature"));
		if (!sig.agree) missing.push(__("the consent box"));
		return missing;
	}

	update_signbox() {
		const $box = this.$shell && this.$shell.find(".cb-signbox");
		if (!$box || !$box.length) return;
		const missing = this.sign_blockers();
		$box.toggleClass("is-ready", !missing.length);
		$box.find(".cb-execute").prop("disabled", !!(missing.length || this.signing_now));
		$box.find(".cb-missing").text(missing.length ? __("Still needed: {0}.", [missing.join(", ")]) : __("Ready to sign."));
	}

	execute() {
		const missing = this.sign_blockers();
		if (missing.length) return this.update_signbox();
		const sig = this.sig;
		const issuing = this.data.docstatus === 0;
		const message = issuing
			? __("This issues {0} and executes it with {1}'s signature. The terms lock from here, and the signed copy is emailed to {2}.", [
					this.data.name,
					cb_esc(cb_norm(sig.name)),
					cb_esc(cb_norm(sig.email)),
			  ])
			: __("This executes {0} with {1}'s signature, and the signed copy is emailed to {2}.", [
					this.data.name,
					cb_esc(cb_norm(sig.name)),
					cb_esc(cb_norm(sig.email)),
			  ]);
		frappe.confirm(message, () => {
			const initials = {};
			this.boxes.forEach((box) => {
				const drawn = this.initials[box.key];
				if (drawn) initials[box.key] = { image: drawn.image, clause: drawn.clause };
			});
			this.signing_now = true;
			this.update_signbox();
			const $button = this.$shell.find(".cb-execute").text(__("Signing…"));
			this.flush()
				.then(() =>
					frappe.xcall(`${CB_API}sign_here`, {
						name: this.data.name,
						signer_email: cb_norm(sig.email),
						signed_name: cb_norm(sig.name),
						signed_title: cb_norm(sig.title),
						signature_mode: sig.mode,
						signature_image: sig.mode === "Drawn" ? sig.image : "",
						initials: JSON.stringify(initials),
						agree: sig.agree ? 1 : 0,
					})
				)
				.then((result) => {
					this.signing_now = false;
					if (result && result.ok) {
						this.initials = {};
						cb_store(`initials:${this.data.name}`, null);
						this.autopay = result.autopay || null;
						this.set_data(result.payload);
						this.preview_key = null;
						this.render_review("fade");
						return;
					}
					$button.text(__("Sign & execute"));
					frappe.msgprint({ message: cb_esc(result.error || __("It could not be signed.")), indicator: "orange" });
					if (result.reload) {
						// Signed or voided under us: show the contract as it now is.
						return frappe.xcall(`${CB_API}get_builder`, { name: this.data.name }).then((data) => {
							this.set_data(data);
							this.preview_key = null;
							this.render_review("fade");
						});
					}
					(result.redo || []).forEach((key) => delete this.initials[key]);
					this.save_initials();
					this.decorate();
					this.render_review_checks();
					const first = this.boxes.find((box) => (result.redo || []).includes(box.key));
					if (first) this.show_box(first);
				})
				.catch(() => {
					this.signing_now = false;
					$button.text(__("Sign & execute"));
					this.update_signbox();
				});
		});
	}

	render_signed($main) {
		const data = this.data;
		const $done = $(`<div class="cb-done"><span class="cb-done-mark">${CB_CHECK_SVG}</span><h2></h2><p></p><div class="cb-actions"></div></div>`);
		$done.find("h2").text(__("Signed and executed"));
		$done.find("p").text(
			__("{0} signed {1}. Their copy is on its way to them by email.", [
				this.values.signed_by || this.values.party_display || __("The {0}", [this.noun()]),
				data.name,
			])
		);
		const $actions = $done.find(".cb-actions");
		if (this.autopay && this.autopay.offer) {
			$('<button class="btn btn-primary"></button>')
				.text(__("Save a card on file now"))
				.on("click", (event) => this.card_on_file($(event.currentTarget)))
				.appendTo($actions);
		}
		if (data.maintenance_contract) {
			$('<button class="btn btn-default"></button>')
				.text(__("Open the maintenance contract"))
				.on("click", () => frappe.set_route("Form", "Sapphire Maintenance Contract", data.maintenance_contract))
				.appendTo($actions);
		}
		$('<button class="btn btn-default"></button>')
			.text(__("Open the contract"))
			.on("click", () => frappe.set_route("Form", "Project Contract", data.name))
			.appendTo($actions);
		$('<button class="btn btn-default"></button>')
			.text(__("Print / PDF"))
			.on("click", () => frappe.set_route("print", "Project Contract", data.name))
			.appendTo($actions);
		$main.append($done);
	}

	// The card-on-file page the agreement promises (Exhibit B), on its own tab: Stripe will not
	// run inside the desk. Opened inside the click so a popup blocker lets it through.
	card_on_file($button) {
		const tab = window.open("", "_blank");
		if (tab) {
			try {
				tab.opener = null;
			} catch (e) {
				// Some browsers refuse the assignment; the tab still works.
			}
			tab.document.write(`<p style="font-family:sans-serif;padding:24px">${__("Opening the secure card page…")}</p>`);
		}
		$button.prop("disabled", true);
		frappe
			.xcall(`${CB_API}start_card_on_file`, { name: this.data.name })
			.then((result) => {
				if (result && result.ok && result.checkout_url) {
					if (tab) tab.location.href = result.checkout_url;
					return;
				}
				if (tab) tab.close();
				frappe.show_alert(
					{
						message:
							result && result.already
								? __("The card page was already opened for this agreement.")
								: __("The card page could not be opened; follow up on payment separately."),
						indicator: "orange",
					},
					8
				);
			})
			.catch(() => tab && tab.close());
	}

	render_send_options($main) {
		const data = this.data;
		const $actions = $('<div class="cb-actions"></div>').appendTo($main);
		const button = (label, cls, handler) =>
			$(`<button class="btn ${cls}"></button>`).text(label).on("click", handler).appendTo($actions);
		if (this.can_sign_here()) {
			button(__("Sign it here with the {0}", [this.noun()]), "btn-primary cb-big", () => this.set_mode("client"));
		}
		if (data.can_send) {
			button(__("Send it to them to sign"), this.can_sign_here() ? "btn-default" : "btn-primary", () => this.send_dialog());
			button(__("Hand them another device"), "btn-default", () => this.sign_here_dialog());
		} else if (data.can_issue && !this.can_sign_here()) {
			button(__("Issue the contract"), "btn-primary", () => this.issue_only());
		}
		if (data.docstatus === 0 && data.editable) {
			button(__("Save draft"), "btn-default", () => this.flush(true).catch(() => {}));
		}
		if (data.docstatus === 1) {
			button(__("Print / PDF"), "btn-default", () => frappe.set_route("print", "Project Contract", data.name));
		}
		if (!data.esign_enabled) {
			$main.append(
				$('<div class="cb-notice"></div>').text(
					__("Online signing is switched off, so print the issued contract for a wet signature and record it with Mark as Signed on the full form.")
				)
			);
		}
	}

	confirm_issue_text() {
		return this.data.docstatus === 0
			? __("This issues {0}: its terms are locked from here, and a change after this means a new revision.", [this.data.name])
			: "";
	}

	issue() {
		if (this.data.docstatus !== 0) return Promise.resolve();
		return this.flush().then(() =>
			frappe.xcall(`${CB_API}issue_contract`, { name: this.data.name }).then((data) => this.set_data(data))
		);
	}

	issue_only() {
		frappe.confirm(this.confirm_issue_text(), () =>
			this.issue().then(() => {
				frappe.show_alert({ message: __("Issued"), indicator: "green" });
				this.render_review("fade");
			})
		);
	}

	live_emailed_link() {
		const sig = this.data.signature || {};
		return ["Sent", "Viewed"].includes(sig.status) && sig.signing_channel !== "In Person" ? sig : null;
	}

	sign_here_dialog() {
		const emailed = this.live_emailed_link();
		const replaces = emailed
			? `<p class="text-warning">${cb_esc(
					__(
						"This replaces the signing link already emailed to {0}: that link stops working. If they would rather sign from the email, close this and leave it be.",
						[emailed.signer_email]
					)
			  )}</p>`
			: "";
		const dialog = new frappe.ui.Dialog({
			title: __("Hand them another device"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__(
						"The agreement opens on a new tab, ready to hand to the client. They re-type their email, initial each marked clause, and sign. Their signed copy is emailed to them."
					)}</p><p class="text-muted">${__(
						"This device stays signed in as you, so stay with them while they sign."
					)}</p>${replaces}<p class="text-muted">${cb_esc(this.confirm_issue_text())}</p>`,
				},
				{
					fieldname: "email",
					fieldtype: "Data",
					options: "Email",
					label: __("{0}'s email", [this.noun(true)]),
					default: this.values.contact_email,
					reqd: 1,
				},
			],
			primary_action_label: __("Open it for signing"),
			primary_action: (values) => {
				// Opened now, inside the click, so a popup blocker lets it through; it is pointed
				// at the signing page once the link exists.
				const tab = window.open("", "_blank");
				if (tab) {
					// The client holds this tab: it must not be able to reach back into the
					// staff member's desk window through window.opener.
					try {
						tab.opener = null;
					} catch (e) {
						// Some browsers refuse the assignment; the tab still works.
					}
					tab.document.write(`<p style="font-family:sans-serif;padding:24px">${__("Preparing the agreement…")}</p>`);
				}
				dialog.hide();
				this.issue()
					.then(() =>
						frappe.xcall(`${CB_ESIGN}start_in_person_signing`, {
							project_contract: this.data.name,
							signer_email: values.email,
						})
					)
					.then((result) => {
						this.sign_url = result.url;
						if (tab) tab.location.href = result.url;
						this.go(CB_SIGN);
					})
					.catch(() => {
						if (tab) tab.close();
					});
			},
		});
		dialog.show();
	}

	send_dialog() {
		const dialog = new frappe.ui.Dialog({
			title: __("Send it to them to sign"),
			fields: [
				{
					fieldtype: "HTML",
					options: `<p>${__(
						"They get an email with a link to read, initial and sign the agreement in their own time."
					)}</p><p class="text-muted">${cb_esc(this.confirm_issue_text())}</p>`,
				},
				{
					fieldname: "recipient_email",
					fieldtype: "Data",
					options: "Email",
					label: __("Send to"),
					default: this.values.contact_email,
					reqd: 1,
					description: __("They'll be asked to re-type this address to confirm it's them."),
				},
				{
					fieldname: "message",
					fieldtype: "Small Text",
					label: __("Note (optional)"),
					description: __("Included in the email above the signing button."),
				},
			],
			primary_action_label: __("Send"),
			primary_action: (values) => {
				dialog.hide();
				this.issue()
					.then(() =>
						frappe.xcall(`${CB_ESIGN}send_for_signature`, {
							project_contract: this.data.name,
							recipient_email: values.recipient_email,
							message: values.message,
						})
					)
					.then((result) => {
						frappe.show_alert({ message: __("Sent to {0}", [result.to]), indicator: "green" }, 6);
						return frappe.xcall(`${CB_API}get_builder`, { name: this.data.name }).then((data) => {
							this.set_data(data);
							this.render_review("fade");
						});
					});
			},
		});
		dialog.show();
	}

	// -------------------------------------------------------------------- signing elsewhere

	render_sign(dir) {
		this.ensure_shell();
		this.fg = null;
		this.view = CB_SIGN;
		const party = this.values.party_display || this.values.party || __("the {0}", [this.noun()]);
		const $panel = $('<section><div class="cb-panel-body"></div></section>');
		const $body = $panel.find(".cb-panel-body");
		this.swap_stage($panel, dir);
		this.update_chrome();
		this.refresh_preview({ now: true });
		if (this.data.status === "Signed") {
			// Signed while this screen watched. It stays this entry's screen, so Back still goes
			// to the review it came from (which now shows the executed copy) without a repeat.
			this.render_signed($body);
			const $back = $('<div class="cb-actions" style="justify-content:center"></div>').appendTo($body);
			$(`<button class="btn btn-default">${__("See the signed agreement")}</button>`)
				.on("click", () => this.go(CB_REVIEW))
				.appendTo($back);
			return;
		}
		const $hero = $('<div class="cb-done"></div>').appendTo($body);
		$hero.append($("<h2></h2>").text(__("Hand the device to {0}", [party])));
		$hero.append(
			$("<p></p>").text(
				__(
					"The agreement is open on the other tab. When they have signed, the page tells them to hand the device back, and this screen updates by itself."
				)
			)
		);
		const $status = $("<div></div>").appendTo($hero);
		const $actions = $('<div class="cb-actions"></div>').appendTo($hero);
		if (this.sign_url) {
			$(`<button class="btn btn-primary">${__("Open the signing page")}</button>`)
				.on("click", () => window.open(this.sign_url, "_blank"))
				.appendTo($actions);
		} else {
			$(`<button class="btn btn-primary">${__("Open it for signing on this device")}</button>`)
				.on("click", () => this.sign_here_dialog())
				.appendTo($actions);
		}
		$(`<button class="btn btn-default">${__("Email it to them instead")}</button>`)
			.on("click", () => this.send_dialog())
			.appendTo($actions);
		$(`<button class="btn btn-default">${__("Back to the review")}</button>`)
			.on("click", () => this.go(CB_REVIEW))
			.appendTo($actions);

		const check = () =>
			frappe.xcall(`${CB_API}get_signing_status`, { name: this.data.name }).then((state) => {
				const request = state.request || {};
				if (state.contract_status === "Signed" || request.status === "Signed") {
					this.stop_poll();
					this.sign_url = null;
					frappe.show_alert({ message: __("Signed by {0}", [request.signed_name || party]), indicator: "green" }, 8);
					return frappe.xcall(`${CB_API}get_builder`, { name: this.data.name }).then((data) => {
						this.set_data(data);
						this.preview_key = null;
						this.render_sign("fade");
					});
				}
				const seen = request.view_count ? __("The agreement is open on the other tab.") : __("Waiting for the agreement to open…");
				$status.html(`<p><span class="cb-pulse"></span>${cb_esc(seen)}</p>`);
			});
		check();
		this.poll = setInterval(() => {
			if (frappe.get_route()[0] !== "contract-builder") return this.stop_poll();
			check();
		}, CB_POLL_MS);
	}

	stop_poll() {
		if (this.poll) clearInterval(this.poll);
		this.poll = null;
	}
}
