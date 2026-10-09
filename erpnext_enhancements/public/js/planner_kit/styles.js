/*
 * Planner kit: its stylesheet, injected once as <style id="pk-style">. Every class starts with "pk-",
 * colors come from the Desk's theme variables (so dark mode works), and the fixed accents are the
 * planners' own (blue #2563eb, amber #d97706, red #dc2626, green #16a34a).
 *
 * Layers: the drawer sits at z-index 1025, above the page and its sticky head (6) and Frappe's
 * sidebar (1020-1023), below a dock (1030), a modal and its backdrop (1040/1050) and the
 * datepicker (9999), so the reason prompt and a date picker opened from a drawer show above it.
 * The menu and the toast sit just above the drawer.
 */

export const STYLE_ID = "pk-style";

export const CSS = `
.pk-drawer{position:fixed;top:0;right:0;bottom:0;width:var(--pk-w,440px);max-width:100vw;z-index:1025;display:flex;flex-direction:column;background:var(--card-bg,#fff);color:var(--text-color);border-left:1px solid var(--border-color);box-shadow:-10px 0 28px rgba(0,0,0,.16);outline:none;}
.pk-drawer[hidden]{display:none;}
.pk-drawer.pk-open{animation:pk-slide .16s ease-out;}
@keyframes pk-slide{from{transform:translateX(24px);opacity:.6;}to{transform:none;opacity:1;}}
.pk-drawer-head{display:flex;align-items:flex-start;gap:8px;padding:10px 12px;border-bottom:1px solid var(--border-color);}
.pk-drawer-heading{flex:1 1 auto;min-width:0;}
.pk-drawer-title{margin:0;font-size:15px;font-weight:600;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-drawer-sub{font-size:12px;color:var(--text-muted);margin-top:2px;}
.pk-drawer-sub[hidden]{display:none;}
.pk-drawer-tools{display:flex;align-items:center;gap:4px;flex:0 0 auto;}
.pk-drawer-close{border:none;background:none;color:var(--text-muted);font-size:22px;line-height:1;padding:0 4px;cursor:pointer;border-radius:6px;}
.pk-drawer-close:hover,.pk-drawer-close:focus{color:var(--text-color);background:var(--control-bg);outline:none;}
.pk-drawer-body{flex:1 1 auto;overflow:auto;padding:10px 12px 16px;-webkit-overflow-scrolling:touch;overscroll-behavior:contain;}
.pk-drawer-foot{display:flex;flex-wrap:wrap;gap:8px;justify-content:flex-end;padding:8px 12px;border-top:1px solid var(--border-color);}
.pk-drawer-foot[hidden]{display:none;}
.pk-busy{opacity:.55;transition:opacity .1s;}
.pk-staging{display:none !important;}
.pk-panel .form-layout{padding:0;}
.pk-panel .frappe-control{margin-bottom:10px;}
.pk-arrows{display:inline-flex;align-items:center;gap:4px;}
.pk-arrows-label{font-size:12px;color:var(--text-muted);min-width:96px;text-align:center;white-space:nowrap;}
.pk-toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:1046;display:flex;align-items:center;gap:10px;max-width:min(560px,calc(100vw - 24px));padding:9px 10px 9px 14px;border-radius:10px;background:var(--text-color,#1f2937);color:var(--card-bg,#fff);box-shadow:0 10px 28px rgba(0,0,0,.28);font-size:13px;}
.pk-toast-text{flex:1 1 auto;min-width:0;}
.pk-toast-action{border:none;background:none;color:inherit;font-weight:700;text-decoration:underline;padding:2px 6px;cursor:pointer;border-radius:6px;}
.pk-toast-action:hover,.pk-toast-action:focus{background:rgba(127,127,127,.25);outline:none;}
.pk-toast-close{border:none;background:none;color:inherit;opacity:.7;font-size:18px;line-height:1;padding:0 4px;cursor:pointer;}
.pk-toast-warning{box-shadow:0 0 0 2px #d97706,0 10px 28px rgba(0,0,0,.28);}
.pk-menu{position:fixed;z-index:1047;min-width:200px;max-width:min(320px,calc(100vw - 16px));padding:4px 0;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg,#fff);color:var(--text-color);box-shadow:0 10px 28px rgba(0,0,0,.2);}
.pk-menu-title{padding:4px 12px 6px;font-size:11px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.04em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-menu-item{display:flex;justify-content:space-between;align-items:center;gap:12px;width:100%;padding:6px 12px;border:none;background:none;color:inherit;font-size:13px;text-align:left;cursor:pointer;}
.pk-menu-item:hover,.pk-menu-item:focus{background:var(--control-bg);outline:none;}
.pk-menu-item[disabled]{opacity:.45;cursor:default;}
.pk-menu-danger{color:#b91c1c;}
.pk-menu-hint{font-size:11px;color:var(--text-muted);}
.pk-menu-divider{height:1px;margin:4px 0;background:var(--border-color);}
.pk-hint{display:flex;align-items:center;gap:8px;margin:0 0 8px;padding:6px 10px;border:1px dashed var(--primary,#2490ef);border-radius:8px;background:rgba(36,144,239,.06);font-size:12px;}
.pk-hint b{color:var(--primary,#2490ef);}
.pk-hint-close{margin-left:auto;border:1px solid var(--border-color);border-radius:6px;background:var(--card-bg);color:var(--text-color);font-size:12px;padding:1px 8px;cursor:pointer;}
.pk-glow{box-shadow:0 0 0 2px var(--primary,#2490ef),0 0 12px rgba(36,144,239,.5) !important;}
.pk-legend-section{margin-bottom:14px;}
.pk-legend-title{margin:0 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;}
.pk-legend-note{margin:0 0 6px;font-size:12px;color:var(--text-muted);}
.pk-legend-list{list-style:none;margin:0;padding:0;}
.pk-legend-item{display:flex;align-items:flex-start;gap:10px;padding:4px 0;font-size:13px;}
.pk-legend-sample{flex:0 0 112px;display:flex;align-items:center;min-height:20px;overflow:hidden;}
.pk-legend-sample > *{max-width:112px;}
.pk-legend-text{flex:1 1 auto;min-width:0;}
.pk-swatch{display:inline-block;width:24px;height:14px;border:1px solid var(--border-color);border-left-width:4px;border-radius:3px;}
.pk-chip{display:inline-block;font-size:10px;border-radius:8px;padding:0 6px;margin-right:3px;background:var(--control-bg);color:var(--text-muted);white-space:nowrap;}
.pk-chip-red{background:rgba(220,38,38,.12);color:#b91c1c;}
.pk-chip-amber{background:rgba(217,119,6,.14);color:#b45309;}
.pk-chip-green{background:rgba(22,163,74,.14);color:#15803d;}
.pk-chip-blue{background:rgba(37,99,235,.12);color:#1d4ed8;}
.pk-state{display:inline-block;font-size:11px;border-radius:8px;padding:0 7px;background:var(--control-bg);color:var(--text-muted);white-space:nowrap;}
.pk-state-green{background:rgba(22,163,74,.14);color:#15803d;}
.pk-state-amber{background:rgba(217,119,6,.14);color:#b45309;}
.pk-state-red{background:rgba(220,38,38,.12);color:#b91c1c;font-weight:600;}
.pk-contact{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px;}
.pk-week{display:flex;flex-direction:column;gap:8px;}
.pk-day{border:1px solid var(--border-color);border-radius:8px;overflow:hidden;}
.pk-day-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 8px;font-size:12px;cursor:pointer;background:var(--card-bg);}
.pk-day-head:hover,.pk-day-head:focus{background:var(--control-bg);outline:none;}
.pk-day-today .pk-day-head b{color:var(--primary,#2490ef);}
.pk-day-selected{border-color:var(--primary,#2490ef);}
.pk-day-off{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);}
.pk-day-conflict{box-shadow:inset 3px 0 0 #dc2626;}
.pk-day-drive{font-size:11px;color:var(--text-muted);margin-left:auto;}
.pk-items{list-style:none;margin:0;padding:0;}
.pk-item{display:flex;align-items:flex-start;gap:8px;padding:5px 8px;border-top:1px solid var(--border-color);border-left:3px solid #94a3b8;font-size:12px;background:var(--card-bg);}
.pk-item.pk-k-task{border-left-color:#2563eb;}
.pk-item.pk-k-visit{border-left-color:#64748b;}
.pk-item.pk-k-rental{border-left-color:#d97706;}
.pk-item.pk-k-travel{border-left-color:#a16207;}
.pk-grab{cursor:grab;-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;}
.pk-grab:hover{background:var(--control-bg);}
.pk-dragging{opacity:.35;}
.pk-kind{flex:0 0 50px;font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--text-muted);padding-top:1px;}
.pk-item-main{flex:1 1 auto;min-width:0;display:flex;flex-direction:column;}
.pk-item-label{font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-item-meta{font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-conflict{font-size:11px;color:#b91c1c;padding:3px 8px;}
.pk-warning{font-size:11px;color:#b45309;padding:3px 8px;}
.pk-empty{margin:0;padding:6px 8px;font-size:12px;color:var(--text-muted);}
.pk-note{margin:10px 0 0;font-size:12px;color:var(--text-muted);}
.pk-note-warn{margin:0 0 8px;padding:6px 10px;border:1px dashed #d97706;border-radius:8px;color:var(--text-color);}
.pk-stops{margin-top:12px;}
.pk-stops-head{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:4px;}
.pk-stops-head h4{margin:0;font-size:13px;font-weight:600;}
.pk-stop-list{list-style:none;margin:0;padding:0;border:1px solid var(--border-color);border-radius:8px;}
.pk-stop{display:flex;gap:8px;padding:6px 8px;border-top:1px solid var(--border-color);font-size:12px;}
.pk-stop:first-child{border-top:none;}
.pk-stop-num{flex:0 0 auto;width:20px;height:20px;border-radius:50%;background:#2563eb;color:#fff;font-size:11px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}
.pk-stop-body{min-width:0;flex:1 1 auto;}
.pk-stop-time{font-size:11px;color:var(--text-muted);font-weight:600;}
.pk-stop-label{font-weight:600;}
.pk-stop-sub,.pk-stop-addr{font-size:11px;color:var(--text-muted);}
a.pk-stop-addr{color:var(--primary,#2490ef);}
.pk-link{color:var(--primary,#2490ef);cursor:pointer;font-weight:600;border-radius:4px;}
.pk-link:hover,.pk-link:focus{text-decoration:underline;outline:none;}
.pk-columns{display:flex;gap:8px;align-items:flex-start;overflow-x:auto;padding-bottom:6px;-webkit-overflow-scrolling:touch;}
.pk-col{flex:0 0 224px;min-width:0;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);overflow:hidden;}
.pk-col-off{background:repeating-linear-gradient(135deg,transparent 0 6px,var(--control-bg) 6px 8px);}
.pk-col-head{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:6px 8px;font-size:12px;border-bottom:1px solid var(--border-color);}
.pk-col-meta{font-size:11px;color:var(--text-muted);padding:4px 8px;}
.pk-section-title{margin:12px 0 4px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--text-muted);font-weight:600;}
.pk-facts{width:100%;font-size:13px;margin:0 0 8px;border-collapse:collapse;}
.pk-facts th{color:var(--text-muted);font-weight:normal;padding:2px 10px 2px 0;vertical-align:top;white-space:nowrap;width:1%;}
.pk-facts td{padding:2px 0;}
.pk-tl{border:1px solid var(--border-color);border-radius:8px;padding:4px 8px;}
.pk-tl-scale{display:flex;justify-content:space-between;font-size:10px;color:var(--text-muted);padding:2px 0 4px;margin-left:calc(42% + 8px);}
.pk-tl-row{display:grid;grid-template-columns:minmax(0,42%) minmax(0,1fr);gap:8px;align-items:center;padding:3px 0;border-top:1px solid var(--border-color);font-size:12px;}
.pk-tl-label{min-width:0;display:flex;flex-direction:column;}
.pk-tl-label b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-weight:600;}
.pk-tl-label span{font-size:11px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pk-tl-track{position:relative;height:16px;border-radius:4px;background:var(--control-bg);}
.pk-tl-bar{position:absolute;top:2px;bottom:2px;min-width:4px;border-radius:3px;background:#2563eb;}
.pk-tl-bar.pk-tl-pencil{background:repeating-linear-gradient(135deg,rgba(100,116,139,.85) 0 3px,rgba(100,116,139,.35) 3px 6px);}
.pk-tl-bar.pk-tl-over{background:#dc2626;}
.pk-tl-bar.pk-tl-late{box-shadow:0 0 0 2px #dc2626;}
.pk-tl-bar.pk-tl-clip-start{border-top-left-radius:0;border-bottom-left-radius:0;}
.pk-tl-bar.pk-tl-clip-end{border-top-right-radius:0;border-bottom-right-radius:0;}
.pk-tl-today{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--primary,#2490ef);opacity:.7;}
.pk-tl-off{font-size:11px;color:var(--text-muted);}
@media (max-width:767px){
.pk-drawer{width:100vw;border-left:none;}
.pk-columns{flex-direction:column;overflow-x:visible;}
.pk-col{flex:1 1 auto;width:100%;}
.pk-toast{left:12px;right:12px;transform:none;max-width:none;}
.pk-legend-sample{flex-basis:88px;}
.pk-tl-row{grid-template-columns:minmax(0,1fr);}
.pk-tl-scale{margin-left:0;}
}
`;
