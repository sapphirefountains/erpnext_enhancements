// Plan a Trip — the office's step-by-step way to enter a whole trip.
//
// A desk Page (/desk/plan-a-trip, or ?trip=TRIP-... to carry on with one) that walks through a
// Travel Trip one question at a time: the trip, who's going, getting there, getting back, where
// everyone sleeps, getting around, the schedule, the files — and ends on a checklist of what is
// still missing. It reads and writes the same Travel Trip as the desk form, through
// travel_management/planner.py, and every save runs the Travel Trip controller.
//
// BOOKINGS HERE, ONE ROW PER PERSON THERE. The office books for the crew, so a card on this page
// is a booking — one flight with four people on it, one room shared by two — with a tick box per
// person. The server stores one row per person, each with that person's own confirmation number,
// so each traveler's itinerary, email and calendar invite shows their booking and nobody else's.
// Cards carry a `changed` set: an existing row only has a shared field rewritten when it was
// actually edited here (see the planner.py module docstring for why).
//
// SAVING. Moving between steps saves, and so does leaving the page. Nothing is saved until the
// trip has its basics and at least one person — the Travel Trip controller refuses a trip with
// no traveler. A save based on a version somebody else replaced is refused by the server, and
// the page offers a reload rather than merging.
//
// HISTORY. Every step, and the list, is its own browser history entry (?trip=...&step=..., or
// ?new=1&step=... before the trip has a name), so Back returns to the previous step and Forward
// restores it; Back from the first screen leaves the page as usual. A move BACK — to an earlier
// step (a tab, the Back button), or wherever the phone's Back leads — saves quietly and always
// happens: a half-finished card must never trap anyone on a step, and what was typed stays on
// the page, marked "Not saved", until a save goes through. A move FORWARD — a later step, by
// Next, a tab or the phone's Forward — passes the checks and the save the Next button always
// did. When a Forward is refused, the page goes back to the entry of the step still showing (the
// entry asked for is left where it was, to go Forward to once fixed) and says why once it is
// there, because frappe closes any open dialog on every route change. A trip the server would
// not take yet (nobody on it, or a save refused) is kept in memory when the list replaces it —
// never dropped.
//
// VIEWS. A saved trip can also be looked at whole, from any step: an Overview (every day, each
// booking once with who is on it and each person's own confirmation number, and what the
// checklist says is missing, shown where it is missing), a Crew grid (people down the side,
// days across), Side by side (a column per person), View as (exactly what one person's
// /itinerary shows) and a Map (every airport, hotel, stop, pick-up, drop-off and freight
// delivery, numbered in the order the trip reaches them, with the drives between them). A view
// is not a step: it adds &view= (and &as= for View as) to the address of the step it was opened
// from, so opening one, switching views or picking another person is its own history entry,
// and "Back to planning" returns to that step. The Map's day chips are not: a choice on that
// screen. Nothing is edited in a view, so opening one is never refused: what is on the page is
// saved quietly first, and a view drawn while a save was refused says those changes are not in
// it. The views come from api.travel.get_trip_views, which leaves money out for everyone but a
// travel coordinator — on the server, not by hiding it here. The trip on screen is loaded
// again when it is asked for from outside the page (the form's "Trip views", Back from the
// form) and nothing unsaved is on it: it may have been saved over there.
//
// WHO TO CALL AND THE TRIP SHEET. The Overview and View as show the server's contacts (911, the
// office's travel desk, who booked the trip, the trip lead, the job site's contact, each hotel
// with the nearest urgent care), phones as tel: links. Review, the Overview and View as open the
// trip sheet (the "Trip Sheet" print format as a PDF, the whole trip or one person's) in a new
// tab: a link, not a screen, so no history entry. Its cost total is on a coordinator's copy
// only, which the server decides. The link is drawn only while the site has the format (the
// views' addresses, or get_plan's `sheet_available` for Review): frappe prints a format it
// cannot find as Standard, every cost on it.
//
// COPY A PAST TRIP (Nik, 2026-09-26). A repeat job at the same site is planned from the last one:
// the list offers every trip this person can see, of any status (planner.get_copyable_trips),
// and picking one opens the copy screen — its own history entry, ?copy=<trip> — with what that
// trip had, a new first day (the next weekday, to start with) and "Bring the same crew". "Make
// the copy" asks planner.copy_plan for the trip moved to that day: its crew, hotels, rentals,
// drives, flights, stops and freight, every date shifted by the same number of days, and every
// confirmation number, tracking number, cost and file left behind. It starts the way "Start a
// new trip" does — a draft of its own, ?new=1 pushed as a new entry — and is not saved until
// the first Next. Back returns to the copy screen and, like leaving any trip, saves what the
// server will take, so the copy screen says a copy of that trip was already made (and opens it)
// rather than let a second one be made without a word. The form's "Copy trip" opens the copy
// screen for its trip, through frappe.route_options like its other buttons.
//
// TIMES are native <input type="time">, which shows AM/PM on a US browser or phone. A stored
// time of exactly midnight reads as "no time given": the Datetime column cannot hold a date
// without a time, so a flight whose time is not known yet is stored at 00:00:00.
//
// The checklist is the server's (planner.get_state -> completeness.find_gaps). The only thing
// computed here is the at-a-glance coverage on the step being edited, which is replaced by the
// server's answer on the next save.
//
// BEDS, GUESTS AND ROUND TRIPS. Someone needs a bed only on the nights they are away: their own
// dates, narrowed by their own flights and drives (stay_window, the rule in
// completeness.stay_window), so a day trip needs none. A room can have guests staying free — an
// upgraded room, a spare bed — who take no share of its cost; the server fits a guest's check-in
// and check-out to their own nights. A round-trip ticket is one charge: a flight with no cost
// whose confirmation number is on a flight that has one rides on that fare (fare_card, the rule
// in completeness.on_another_ticket), and the checklist does not ask for a cost on it.
//
// PAPERWORK. Every booking card has its files (a boarding pass, the hotel confirmation, the
// rental agreement, a bill of lading), and the Files step has the trip's own (a site map, the
// safety plan, an insurance certificate, the job packet). Each is a Trip Document row, for
// everyone on the booking (the whole crew, for a trip-wide file) or for one person — a flight's
// boarding pass starts as its person's (flight_pass_for). The file itself is uploaded straight
// to the trip as a File by frappe's uploader, always private (attach_files), which leaves the
// trip's `modified` alone; the row naming it is saved with everything else, so a file attached
// to a booking not saved yet ("new:<n>") follows that booking to the id it is given on save
// (planner.merge_documents). An upload keeps going after its dialog closes, and lands whenever
// it lands — during a save, a reload of the trip, or once the trip is put aside — and
// add_document puts it where it is not lost. Uploading needs a saved trip: a File is attached
// to a trip by its name. A receipt is not paperwork: it is money, and stays on the cost row.
// The checklist asks for a booking's paperwork only once it has its confirmation number, and
// only flags it, like everything else on the checklist.
//
// PAPERWORK IS A SEPARATE, QUIETER TALLY (Nik, 2026-09-26). A paperwork gap (check "documents")
// is never counted with the rest: not in a step tab's badge, not in "Mark as booked"'s "N things
// missing", not in the Overview's "Still missing" tile, not in the form's headline. It is counted
// on its own as files ("10 files not attached yet", tp_files_not_attached, the rule in
// completeness.files_not_attached) and shown muted (tp-paper), never as a red gap or a red card:
// on a card, on the Files step, in Review's Paperwork section and as a note on the Overview. The
// first real trip is why: v1.544.0 had just taken it from six flags to none, and every booking on
// it has its number and no file yet, so counted as gaps its paperwork would have put ten back.
//
// Styling uses Frappe CSS variables so Frappe Light and Timeless Night both work. The page loader
// serves this file version-aware, so no .bundle.* is needed.

frappe.pages["plan-a-trip"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Plan a Trip"),
		single_column: true,
	});
	wrapper.trip_planner = new TripPlanner(page, wrapper);
	// Leaving the page (a link, the sidebar, "create a new Supplier" opening a form) saves
	// what is there. Frappe triggers "hide" on the page wrapper when another page takes over.
	$(wrapper).on("hide", () => wrapper.trip_planner.on_hide());
};

frappe.pages["plan-a-trip"].on_page_show = function (wrapper) {
	if (wrapper.trip_planner) wrapper.trip_planner.handle_route();
};

const TP_STYLE = `
.tp-wrap{max-width:760px;margin:0 auto;padding-bottom:110px;font-size:15px;color:var(--text-color);}
.tp-muted{color:var(--text-muted);font-size:13px;}
.tp-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 8px;}
.tp-head h4{margin:0;font-size:19px;}
.tp-pill{display:inline-block;font-size:12px;border-radius:10px;padding:2px 10px;background:var(--control-bg);border:1px solid var(--border-color);}
.tp-pill-green{background:#e7f7ed;color:#15803d;border-color:#15803d;}
.tp-tabs{display:flex;gap:8px;overflow-x:auto;padding:2px 0 12px;position:sticky;top:0;background:var(--bg-color);z-index:3;}
.tp-tab{flex:0 0 auto;padding:8px 14px;border-radius:16px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:14px;cursor:pointer;white-space:nowrap;}
.tp-tab.tp-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-tab .tp-badge{display:inline-block;min-width:18px;margin-left:6px;padding:0 5px;border-radius:9px;background:#fde8e8;color:#b91c1c;font-size:12px;font-weight:600;text-align:center;}
.tp-tab.tp-active .tp-badge{background:#fff;}
.tp-step-title{font-size:18px;font-weight:600;margin:6px 0 2px;}
.tp-step-help{color:var(--text-muted);margin-bottom:14px;line-height:1.45;}
.tp-card{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:12px;}
.tp-card.tp-bad{border-color:#dc2626;}
.tp-card-head{display:flex;align-items:center;gap:8px;margin-bottom:10px;}
.tp-card-head b{font-size:16px;}
.tp-card-head .tp-remove{margin-left:auto;}
.tp-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px 14px;}
.tp-field{display:flex;flex-direction:column;gap:4px;min-width:0;}
.tp-field label{font-size:13px;color:var(--text-muted);margin:0;}
.tp-field label .tp-req{color:#b91c1c;}
.tp-field input,.tp-field select,.tp-field textarea{width:100%;padding:8px 10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:15px;}
.tp-field input[type=checkbox],.tp-field input[type=radio]{width:auto;padding:0;}
.tp-field textarea{min-height:84px;}
.tp-field .frappe-control{margin:0;}
.tp-field .frappe-control input{height:auto;}
.tp-pair{display:flex;flex-wrap:wrap;gap:6px;}
.tp-pair input[type=date]{flex:1 1 150px;min-width:0;}
.tp-pair input[type=time]{flex:1 1 120px;min-width:0;}
.tp-wide{grid-column:1/-1;}
.tp-seg{display:flex;flex-wrap:wrap;gap:8px;}
.tp-seg button{flex:1 1 auto;min-height:42px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;padding:6px 12px;}
.tp-seg button.tp-on{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-chips{display:flex;flex-wrap:wrap;gap:8px;}
.tp-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border-radius:16px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;user-select:none;}
.tp-chip.tp-on{background:rgba(36,144,239,.12);border-color:var(--primary,#2490ef);font-weight:600;}
.tp-chip.tp-ok{background:#e7f7ed;border-color:#15803d;color:#15803d;cursor:default;}
.tp-chip.tp-miss{background:#fde8e8;border-color:#dc2626;color:#b91c1c;cursor:default;}
.tp-refs{display:flex;flex-direction:column;gap:6px;}
.tp-ref-row{display:flex;align-items:center;gap:8px;}
.tp-ref-row span{flex:0 0 160px;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tp-ref-row input{flex:1;padding:6px 10px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.tp-check{display:flex;align-items:center;gap:8px;font-size:14px;cursor:pointer;margin:2px 0;}
.tp-add{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0 14px;}
.tp-btn{min-height:40px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:14px;cursor:pointer;padding:6px 14px;}
.tp-btn-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-btn-link{border:none;background:none;color:var(--primary,#2490ef);padding:0;min-height:0;cursor:pointer;font-size:14px;}
.tp-gap{display:flex;align-items:flex-start;gap:8px;padding:8px 10px;border-radius:8px;background:#fde8e8;color:#b91c1c;font-size:14px;margin-top:8px;}
.tp-gap .tp-btn-link{margin-left:auto;white-space:nowrap;}
.tp-ok-line{padding:8px 10px;border-radius:8px;background:#e7f7ed;color:#15803d;font-size:14px;margin-top:8px;}
.tp-paper{display:flex;align-items:flex-start;gap:8px;padding:6px 10px;border-radius:8px;border:1px dashed var(--border-color);color:var(--text-muted);font-size:13px;margin-top:6px;}
.tp-paper .tp-btn-link{margin-left:auto;white-space:nowrap;font-size:13px;}
.tp-paper-count{color:var(--text-muted);font-size:13px;}
.tp-crew-row{display:flex;align-items:center;gap:10px;padding:10px 12px;border:1px solid var(--border-color);border-radius:10px;margin-bottom:8px;background:var(--card-bg);}
.tp-crew-row.tp-on{border-color:var(--primary,#2490ef);}
.tp-crew-row .tp-crew-name{flex:1;min-width:0;}
.tp-crew-extra{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:8px;}
.tp-crew-extra input{padding:6px 8px;border-radius:8px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);}
.tp-matrix{overflow-x:auto;margin-bottom:14px;}
.tp-matrix table{border-collapse:collapse;font-size:13px;}
.tp-matrix th,.tp-matrix td{border:1px solid var(--border-color);padding:5px 8px;text-align:center;white-space:nowrap;}
.tp-matrix th:first-child,.tp-matrix td:first-child{text-align:left;}
.tp-matrix td.tp-y{background:#e7f7ed;color:#15803d;}
.tp-matrix td.tp-n{background:#fde8e8;color:#b91c1c;font-weight:600;}
.tp-day{font-weight:600;margin:14px 0 6px;}
.tp-review-sec{margin-bottom:16px;}
.tp-review-sec h5{margin:0 0 6px;font-size:15px;}
.tp-sum{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:14px;}
.tp-sum div{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;}
.tp-sum b{display:block;font-size:18px;}
.tp-sum div.tp-quiet{border-style:dashed;}
.tp-sum div.tp-quiet b{color:var(--text-muted);font-weight:500;}
.tp-nav{position:fixed;left:0;right:0;bottom:0;display:flex;gap:10px;padding:12px 14px calc(12px + env(safe-area-inset-bottom));background:var(--bg-color);border-top:1px solid var(--border-color);z-index:5;}
.tp-nav button{flex:1;min-height:48px;border-radius:9px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:16px;cursor:pointer;}
.tp-nav button.tp-primary{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-nav button:disabled{opacity:.5;cursor:default;}
.tp-savestate{position:fixed;left:0;right:0;bottom:calc(76px + env(safe-area-inset-bottom));text-align:center;font-size:13px;pointer-events:none;z-index:6;}
.tp-savestate span{display:inline-block;padding:4px 12px;border-radius:12px;background:var(--control-bg);color:var(--text-muted);border:1px solid var(--border-color);}
.tp-savestate.tp-err span{background:#fde8e8;color:#b91c1c;border-color:#b91c1c;font-weight:600;}
.tp-list-item,.tp-copy-item{display:block;width:100%;text-align:left;background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:14px;margin-bottom:10px;color:var(--text-color);cursor:pointer;}
.tp-list-item h5,.tp-copy-item h5{margin:0 0 4px;font-size:16px;}
.tp-copy-more summary{cursor:pointer;color:var(--primary,#2490ef);font-size:14px;margin:0 0 10px;}
.tp-copy-facts{margin-top:8px;font-size:14px;line-height:1.5;}
.tp-copy-go{margin-top:14px;}
.tp-empty{text-align:center;padding:40px 16px;color:var(--text-muted);}
.tp-wrap.tp-views-wide{max-width:none;}
.tp-viewbar{display:flex;flex-wrap:wrap;align-items:center;gap:6px;margin:0 0 10px;}
.tp-vbtn{padding:5px 12px;border-radius:14px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:13px;cursor:pointer;white-space:nowrap;}
.tp-vbtn.tp-active{background:var(--primary,#2490ef);border-color:var(--primary,#2490ef);color:#fff;font-weight:600;}
.tp-viewbar select{padding:5px 10px;border-radius:14px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:13px;max-width:210px;}
.tp-viewbar select.tp-active{border-color:var(--primary,#2490ef);font-weight:600;}
.tp-view-top{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:4px 0 12px;}
.tp-view-head{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 0 12px;}
.tp-view-head .tp-step-title{margin:0;}
.tp-notice{padding:8px 10px;border-radius:8px;background:#fef3c7;color:#92400e;border:1px solid #f59e0b;font-size:14px;margin-bottom:12px;}
.tp-flag{display:flex;align-items:flex-start;gap:8px;padding:6px 10px;border-radius:8px;background:#fde8e8;color:#b91c1c;font-size:13px;margin-top:6px;}
.tp-flag .tp-btn-link{margin-left:auto;white-space:nowrap;font-size:13px;}
.tp-tl-day{margin-bottom:16px;}
.tp-tl-date{font-weight:600;font-size:15px;margin:0 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border-color);}
.tp-today-tag{display:inline-block;margin-left:6px;padding:0 8px;border-radius:9px;background:var(--primary,#2490ef);color:#fff;font-size:12px;font-weight:600;}
.tp-tl-item{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);margin-bottom:6px;}
.tp-tl-item.tp-bad{border-color:#dc2626;}
.tp-tl-icon{flex:0 0 22px;font-size:17px;line-height:1.3;text-align:center;}
.tp-tl-time{flex:0 0 130px;color:var(--text-muted);font-size:13px;padding-top:2px;}
.tp-tl-body{flex:1;min-width:0;}
.tp-tl-title{font-weight:600;}
.tp-tl-sub{color:var(--text-muted);font-size:13px;}
.tp-whos{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
.tp-who{display:inline-block;padding:2px 9px;border-radius:12px;border:1px solid var(--border-color);background:var(--control-bg);font-size:12px;}
.tp-who.tp-who-miss{border-color:#dc2626;color:#b91c1c;background:#fde8e8;}
.tp-cost{color:var(--text-muted);font-size:13px;margin-top:4px;}
.tp-xscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;border:1px solid var(--border-color);border-radius:10px;margin-bottom:12px;max-width:100%;width:fit-content;}
.tp-xtable{border-collapse:separate;border-spacing:0;font-size:13px;}
.tp-xtable th,.tp-xtable td{border-right:1px solid var(--border-color);border-bottom:1px solid var(--border-color);padding:6px 8px;background:var(--card-bg);}
.tp-xtable .tp-sticky{position:sticky;left:0;z-index:1;text-align:left;}
.tp-xtable td.tp-off,.tp-xtable th.tp-off{background:var(--control-bg);color:var(--text-muted);}
.tp-xtable .tp-today{box-shadow:inset 0 3px 0 var(--primary,#2490ef);}
.tp-xtable td.tp-cell-warn{background:#fde8e8;}
.tp-cgrid th{text-align:center;white-space:nowrap;font-weight:600;}
.tp-cgrid td{text-align:center;white-space:nowrap;min-width:54px;font-size:15px;}
.tp-cgrid .tp-sticky{min-width:110px;max-width:150px;}
.tp-cgrid .tp-name{display:block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;}
.tp-warn{color:#b91c1c;font-weight:700;}
.tp-cmp th{text-align:left;vertical-align:top;min-width:210px;}
.tp-cmp td{vertical-align:top;min-width:210px;max-width:290px;white-space:normal;}
.tp-cmp .tp-sticky{min-width:96px;max-width:120px;}
.tp-line{font-size:13px;line-height:1.35;margin-bottom:5px;}
.tp-miss-text{color:#b91c1c;font-weight:600;}
.tp-legend{display:flex;flex-wrap:wrap;gap:12px;font-size:13px;color:var(--text-muted);margin-bottom:12px;}
.tp-pday{font-weight:600;margin:14px 0 6px;max-width:640px;}
.tp-pcard{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:12px 14px;margin-bottom:8px;max-width:640px;}
.tp-kicker{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.03em;}
.tp-ptitle{font-size:16px;font-weight:600;margin:2px 0;}
.tp-psub{font-size:13px;color:var(--text-muted);}
.tp-pnr{display:flex;align-items:center;gap:10px;margin-top:6px;font-size:14px;font-weight:600;}
.tp-preview{max-width:760px;}
.tp-preview iframe{display:block;width:100%;height:560px;border:1px solid var(--border-color);border-radius:8px;background:#fff;margin:10px 0;}
.tp-preview dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:10px 0 0;font-size:14px;}
.tp-preview dt{color:var(--text-muted);font-weight:normal;}
.tp-preview dd{margin:0;min-width:0;overflow-wrap:anywhere;}
.tp-events{margin:6px 0 10px;padding-left:18px;font-size:14px;}
.tp-docs{margin-top:12px;}
.tp-docs-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 8px;margin-bottom:6px;}
.tp-docs-head label{font-size:13px;color:var(--text-muted);margin:0;}
.tp-doc{border:1px solid var(--border-color);border-radius:8px;padding:6px 8px 6px 10px;margin-bottom:6px;background:var(--control-bg);}
.tp-doc-line{display:flex;align-items:center;gap:8px;min-width:0;}
.tp-doc-icon{flex:0 0 22px;font-size:17px;text-align:center;}
.tp-doc-title{flex:1;min-width:0;font-size:14px;line-height:1.3;overflow-wrap:anywhere;}
.tp-doc-title .tp-muted{display:block;}
.tp-doc-open{flex:0 0 auto;display:inline-flex;align-items:center;min-height:36px;padding:0 6px;font-size:14px;font-weight:600;white-space:nowrap;}
.tp-doc-x{flex:0 0 auto;min-width:36px;min-height:36px;border:none;background:none;color:var(--text-muted);font-size:17px;line-height:1;cursor:pointer;}
.tp-doc-edit{display:flex;flex-wrap:wrap;gap:6px 8px;margin:4px 0 2px 30px;}
.tp-doc-edit select{flex:1 1 160px;min-width:0;padding:6px 8px;border-radius:8px;border:1px solid var(--border-color);background:var(--card-bg);color:var(--text-color);font-size:14px;}
.tp-docchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px;}
.tp-docchip{display:inline-block;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:2px 9px;border-radius:12px;border:1px solid var(--border-color);background:var(--control-bg);color:var(--text-color);font-size:12px;text-decoration:none;}
a.tp-docchip:hover,a.tp-pdoc:hover{border-color:var(--primary,#2490ef);text-decoration:none;}
.tp-bookdocs{margin-bottom:14px;}
.tp-bookdocs-head{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 10px;font-weight:600;margin:0 0 6px;}
.tp-bookdocs-head .tp-btn-link{font-weight:normal;}
.tp-pdocs{display:flex;flex-direction:column;gap:6px;margin-top:8px;}
.tp-pdoc{display:flex;align-items:center;gap:8px;min-height:44px;padding:6px 10px;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg);color:var(--text-color);font-size:14px;text-decoration:none;}
.tp-pdoc span{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.tp-contacts{max-width:1000px;}
.tp-contacts-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:8px;}
.tp-contact{background:var(--card-bg);border:1px solid var(--border-color);border-radius:10px;padding:10px 12px;font-size:14px;line-height:1.45;min-width:0;overflow-wrap:anywhere;}
.tp-contact .tp-kicker{margin-bottom:2px;}
.tp-contact a{display:inline-block;padding:2px 0;}
.tp-contact.tp-sos{border-color:#dc2626;}
.tp-contact.tp-sos a{color:#b91c1c;font-size:18px;font-weight:700;}
.tp-map-days{margin:0 0 10px;}
.tp-map{height:440px;width:100%;border:1px solid var(--border-color);border-radius:10px;background:var(--control-bg);}
.tp-map-status{margin:6px 0 10px;}
.tp-map-list{max-width:760px;}
.tp-map-day-head{font-weight:600;font-size:15px;margin:14px 0 6px;padding-bottom:4px;border-bottom:1px solid var(--border-color);}
.tp-map-row{display:flex;align-items:center;gap:10px;padding:8px 10px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg);margin-bottom:6px;}
.tp-map-num{flex:0 0 auto;min-width:26px;height:26px;padding:0 6px;border-radius:13px;color:#fff;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;}
.tp-map-body{flex:1;min-width:0;overflow-wrap:anywhere;}
.tp-map-body .tp-muted{display:block;}
.tp-map-open{flex:0 0 auto;font-size:13px;white-space:nowrap;}
@media (max-width:600px){.tp-map{height:340px;}
.tp-map-row{flex-wrap:wrap;row-gap:2px;}
.tp-map-body{flex:1 1 calc(100% - 46px);}
.tp-map-open{margin-left:36px;}}
@media (max-width:600px){.tp-ref-row span{flex-basis:110px;}
.tp-tl-item{flex-wrap:wrap;}
.tp-tl-time{flex:1 1 auto;}
.tp-tl-body{flex:1 1 100%;padding-left:32px;}
.tp-cmp th,.tp-cmp td{min-width:170px;}
.tp-preview iframe{height:460px;}
.tp-doc-edit{margin-left:0;}}
[data-theme="dark"] .tp-notice{background:rgba(245,158,11,.15);color:#fcd34d;border-color:#b45309;}
[data-theme="dark"] .tp-flag,[data-theme="dark"] .tp-who.tp-who-miss,[data-theme="dark"] .tp-xtable td.tp-cell-warn{background:rgba(220,38,38,.18);color:#fca5a5;}
[data-theme="dark"] .tp-who.tp-who-miss{border-color:#f87171;}
[data-theme="dark"] .tp-warn,[data-theme="dark"] .tp-miss-text{color:#f87171;}
[data-theme="dark"] .tp-contact.tp-sos{border-color:#f87171;}
[data-theme="dark"] .tp-contact.tp-sos a{color:#fca5a5;}
`;

// Steps, in order. `leg` ties a transport step to the Leg stored on its rows.
const TP_STEPS = [
	{ key: "trip", title: __("The trip") },
	{ key: "crew", title: __("Who's going") },
	{ key: "there", title: __("Getting there"), leg: "Outbound" },
	{ key: "back", title: __("Getting back"), leg: "Return" },
	{ key: "lodging", title: __("Where everyone sleeps") },
	{ key: "around", title: __("Getting around"), leg: "During Trip" },
	{ key: "freight", title: __("Freight") },
	{ key: "schedule", title: __("Schedule") },
	{ key: "files", title: __("Files") },
	{ key: "review", title: __("Review") },
];

const TP_LEG_STEP = { Outbound: "there", Return: "back", "During Trip": "around" };

// What the page offers for ground transport, in the words the office uses. The values are the
// Trip Ground Transport `transport_type` options.
const TP_RIDE_TYPES = [
	{ value: "Company Fleet", label: __("Company vehicle") },
	{ value: "Personal Vehicle", label: __("Personal vehicle") },
	{ value: "Rental/Third Party", label: __("Rental") },
	{ value: "Taxi/Rideshare", label: __("Taxi or rideshare") },
];

// Shared fields per table: mirrors planner.BOOKING_TABLES. A new card sends all of them as
// changed; an existing card sends only what was edited.
const TP_SHARED = {
	flights: [
		"leg",
		"airline",
		"flight_number",
		"departure_airport",
		"departure_time",
		"arrival_airport",
		"arrival_time",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
	accommodations: [
		"hotel_lodging",
		"check_in_date",
		"check_in_time",
		"check_out_date",
		"check_out_time",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
	ground_transport: [
		"leg",
		"transport_type",
		"supplier",
		"vehicle",
		"pickup_location",
		"dropoff_location",
		"pickup_datetime",
		"arrival_datetime",
		"return_datetime",
		"cargo",
		"billable",
		"paid_by",
		"paid_by_traveler",
	],
};

// Freight fields: mirrors planner.FREIGHT_FIELDS. One row per shipment, not per person.
const TP_FREIGHT = [
	"carrier",
	"tracking_number",
	"contents",
	"traveler",
	"ship_from",
	"deliver_to",
	"pickup_from",
	"pickup_to",
	"delivery_from",
	"delivery_to",
	"cost",
	"billable",
	"paid_by",
	"paid_by_traveler",
	"booking_group",
];

// Trip Document fields: mirrors planner.DOCUMENT_FIELDS. `booking_group` is the page key of the
// card the file is on ("" for the whole trip), which the server swaps for the stored id.
const TP_DOCUMENT = ["title", "kind", "file", "traveler", "booking_group"];

// What a file is: the Trip Document `kind` options, in their order, each with its icon.
const TP_DOC_KINDS = [
	"Boarding pass",
	"Booking confirmation",
	"Rental agreement",
	"Bill of lading",
	"Site map",
	"Safety plan",
	"Insurance certificate",
	"Job packet",
	"Other",
];

const TP_DOC_ICONS = {
	"Boarding pass": "&#127915;",
	"Booking confirmation": "&#128196;",
	"Rental agreement": "&#128221;",
	"Bill of lading": "&#128230;",
	"Site map": "&#128506;",
	"Safety plan": "&#9888;",
	"Insurance certificate": "&#128737;",
	"Job packet": "&#128450;",
	Other: "&#128196;",
};

// What a file attached to a booking starts as, by its table; a file for the whole trip starts
// as "Other". The kind can be changed on the page.
const TP_DOC_DEFAULT_KIND = {
	flights: "Boarding pass",
	accommodations: "Booking confirmation",
	ground_transport: "Rental agreement",
	freight: "Bill of lading",
};

const TP_UNBOOKED = ["Company Fleet", "Personal Vehicle"];

// How many past trips "Copy a past trip" lists before "Show N more" (planner.get_copyable_trips
// sends up to 50, the latest first).
const TP_COPY_SHOWN = 6;

// A paperwork gap's `check` (completeness.PAPERWORK_CHECK): the quieter tally, counted apart
// from every other gap (see PAPERWORK in the header).
const TP_PAPERWORK = "documents";

// The looks at a whole saved trip (see VIEWS in the header), by their &view= key. Not steps:
// TP_STEPS is unchanged by them, and a view is drawn over the step it was opened from.
const TP_VIEWS = ["overview", "grid", "compare", "person", "map"];

// One icon per kind of itinerary item (api/travel.py shape_itinerary's `type`).
const TP_ICONS = {
	flight: "&#9992;",
	hotel_checkin: "&#127976;",
	hotel_checkout: "&#127976;",
	ground: "&#128663;",
	freight: "&#128230;",
	agenda: "&#128205;",
};

// The Map view's places, by their `kind` (views.build_trip_views' `places`): the word the list
// uses, its icon (`glyph` is the same icon as text, for the map's own popups, which are built
// from elements), and its marker's color. The colors are literal, not theme variables: a marker
// sits on Google's tiles, not on the desk.
const TP_PLACE_KINDS = {
	airport: { label: __("Airport"), icon: "&#9992;", glyph: "✈", color: "#2563eb" },
	hotel: { label: __("Hotel"), icon: "&#127976;", glyph: "\u{1F3E8}", color: "#7c3aed" },
	stop: { label: __("Stop"), icon: "&#128205;", glyph: "\u{1F4CD}", color: "#dc2626" },
	pickup: { label: __("Pick-up"), icon: "&#128663;", glyph: "\u{1F697}", color: "#ea580c" },
	dropoff: { label: __("Drop-off"), icon: "&#128663;", glyph: "\u{1F697}", color: "#c2410c" },
	freight: { label: __("Freight delivery"), icon: "&#128230;", glyph: "\u{1F4E6}", color: "#92400e" },
};

// The lines between places on the Map view (`legs`), by their `kind`.
const TP_LEG_COLORS = { drive: "#0f766e", walk: "#0f766e", flight: "#475569" };

// The Google Maps libraries the Map view uses, every one: EEGoogleMaps.load imports only what it
// is asked for (and "maps"), and a symbol from a library nobody asked for is undefined at runtime
// (tests/test_google_maps_loader.py). marker: AdvancedMarkerElement and Marker; geocoding:
// Geocoder; routes: DirectionsService.
const TP_MAP_LIBRARIES = ["marker", "geocoding", "routes"];

function tp_esc(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

function tp_date_part(datetime) {
	return datetime ? String(datetime).slice(0, 10) : "";
}

function tp_time_part(datetime) {
	// "2026-10-03 14:30:00" -> "14:30". Midnight is "not given" (see the header).
	if (!datetime || String(datetime).length < 16) return "";
	const time = String(datetime).slice(11, 16);
	return time === "00:00" ? "" : time;
}

function tp_join_datetime(date, time) {
	if (!date) return "";
	return `${date} ${time ? time : "00:00"}:00`;
}

function tp_pretty_date(date) {
	return date ? moment(date).format("ddd, MMM D") : "";
}

function tp_pretty_time(time) {
	return time ? moment(time, "HH:mm:ss").format("h:mm A") : "";
}

function tp_clock(datetime) {
	// A datetime's time of day, "2:30 PM" — nothing at exactly midnight, which is "no time
	// given" (see the header). A Time field is read by tp_pretty_time instead: there 00:00 is
	// a real midnight.
	return tp_pretty_time(tp_time_part(datetime));
}

function tp_span(from, to) {
	if (from && to && from !== to) return `${from} – ${to}`;
	return from || to || "";
}

// Days from one ISO date to another ("2026-10-01" to "2026-10-03" is 2), counted in UTC from the
// strings so no time zone or daylight-saving change moves it; NaN when either is not a date.
function tp_days_apart(from, to) {
	return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86400000);
}

// An ISO date moved by `days` ("2026-12-31" + 1 is "2027-01-01"), in UTC like tp_days_apart, so a
// daylight-saving change never moves it; "" when it is not a date.
function tp_add_days(date, days) {
	const time = Date.parse(`${date}T00:00:00Z`);
	if (!date || isNaN(time)) return "";
	return new Date(time + (Number(days) || 0) * 86400000).toISOString().slice(0, 10);
}

// The first Monday-to-Friday day after `date`: where a copied trip starts until someone picks.
function tp_next_weekday(date) {
	let day = tp_add_days(date, 1);
	while (day && [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay())) day = tp_add_days(day, 1);
	return day;
}

// ISO days as runs of consecutive dates, each run written once ("Thu, Oct 1 – Sun, Oct 4", "Tue,
// Oct 6"): a hotel is on every night of a stay, and its popup would otherwise list each one.
function tp_day_runs(days) {
	const runs = [];
	(days || [])
		.filter((day) => typeof day === "string" && day)
		.slice()
		.sort()
		.forEach((day) => {
			const last = runs[runs.length - 1];
			if (last && tp_days_apart(last[1], day) === 1) last[1] = day;
			else if (!last || last[1] !== day) runs.push([day, day]);
		});
	return runs.map(([first, end]) => tp_span(tp_pretty_date(first), tp_pretty_date(end))).filter(Boolean);
}

function tp_item_sort_key(item) {
	// The server orders a day by time of day; a flight, drive or shipment stored at midnight
	// has no time yet, so it goes with the untimed items at the top, never as 12 AM.
	const datetime = {
		flight: item.departure_time,
		ground: item.pickup_datetime,
		freight: item.delivery_from || item.pickup_from,
	}[item.type];
	if (datetime && !tp_time_part(datetime)) return "";
	return String(item.sort_time || "");
}

function tp_by_time(items) {
	return (items || [])
		.map((item, index) => ({ item, index, key: tp_item_sort_key(item) }))
		.sort((a, b) => a.key.localeCompare(b.key) || a.index - b.index)
		.map((entry) => entry.item);
}

function tp_items_by_date(days) {
	const out = {};
	(days || []).forEach((day) => {
		out[day.date] = tp_by_time(day.items);
	});
	return out;
}

function tp_nights(days) {
	// The nights one person sleeps in a booked room: a check-in and a check-out of the same
	// booking cover every night from the one to the day before the other. A room without both
	// days, or checking out on (or before) the day it checks in, covers no night — the rule in
	// completeness.lodging_gaps. Counting its first night put "Night in a room" and "No bed
	// tonight" in the same grid cell; the checklist flags the missing day on the room instead.
	const stays = {};
	(days || []).forEach((day) =>
		(day.items || []).forEach((item) => {
			if (item.type !== "hotel_checkin" && item.type !== "hotel_checkout") return;
			const key = item.group || item.hotel || "";
			const stay = (stays[key] = stays[key] || {});
			if (item.type === "hotel_checkin") stay.in = item.date;
			else stay.out = item.date;
		})
	);
	const nights = new Set();
	Object.values(stays).forEach((stay) => {
		if (!stay.in || !stay.out || stay.out <= stay.in) return;
		tp_days_between(stay.in, stay.out)
			.slice(0, -1)
			.forEach((night) => nights.add(night));
	});
	return nights;
}

function tp_event_when(event) {
	// A calendar invite's event, as a line: "Mon, Oct 5, 7:15 AM – 8:30 AM".
	const day = (value) => tp_pretty_date(tp_date_part(value));
	if (event.all_day) {
		return tp_span(day(event.start), event.end ? day(event.end) : "");
	}
	const start = [day(event.start), tp_clock(event.start)].filter(Boolean).join(", ");
	if (!event.end) return start;
	const end =
		tp_date_part(event.end) === tp_date_part(event.start)
			? tp_clock(event.end)
			: [day(event.end), tp_clock(event.end)].filter(Boolean).join(", ");
	return tp_span(start, end);
}

function tp_days_between(start, end) {
	const days = [];
	if (!start || !end) return days;
	const cursor = moment(start);
	const last = moment(end);
	while (cursor.isSameOrBefore(last, "day") && days.length < 120) {
		days.push(cursor.format("YYYY-MM-DD"));
		cursor.add(1, "day");
	}
	return days;
}

function tp_has_own_dates(traveler, trip) {
	return !!(
		(traveler.from_date && traveler.from_date !== trip.start_date) ||
		(traveler.to_date && traveler.to_date !== trip.end_date)
	);
}

function tp_sorted_stops(stops) {
	return stops
		.slice()
		.sort((a, b) => `${a.date || ""} ${a.time || ""}`.localeCompare(`${b.date || ""} ${b.time || ""}`));
}

function tp_html_to_text(html) {
	// DOMParser, not innerHTML: a parsed document never runs handlers or loads images.
	if (!html) return "";
	const marked = String(html)
		.replace(/<br\s*\/?>/gi, "\n")
		.replace(/<\/(p|div|li)>/gi, "\n");
	return new DOMParser().parseFromString(marked, "text/html").body.textContent.replace(/\n{3,}/g, "\n\n").trim();
}

function tp_text_to_html(text) {
	if (!text || !text.trim()) return "";
	return text
		.trim()
		.split(/\n/)
		.map((line) => `<p>${tp_esc(line) || "<br>"}</p>`)
		.join("");
}

// A file's address, only when it is one to open: the site's own (/files/..., /private/files/...)
// or a web link. Never a javascript: or protocol-relative address, and no backslash: a browser
// reads "/\host" as "//host".
function tp_file_url(url) {
	const value = String(url || "").trim();
	return /^(\/(?![/\\])|https?:\/\/)/i.test(value) && !value.includes("\\") ? value : "";
}

// "/private/files/boarding-pass.pdf" -> "boarding-pass.pdf" (planner.get_state's file_name).
function tp_file_name(url) {
	const path = String(url || "").split(/[?#]/)[0];
	const name = path.split("/").pop() || "";
	try {
		return decodeURIComponent(name);
	} catch (e) {
		return name;
	}
}

function tp_is_image(name) {
	return /\.(png|jpe?g|gif|webp|heic)$/i.test(String(name || ""));
}

// A phone number as a tel: address: its digits and a leading +, nothing else, and "" for one not to
// dial. An extension ("ext. 2", "x12", "#3") is left off the link: its digits glued on would dial
// another number. A note beside the number ("Front desk 702-555-0150") is dropped, but a number
// spelled in letters ("1-800-FLOWERS") would dial something else entirely (1800), so it is no
// number to dial, and neither is one with fewer than three digits or more than fifteen. The number
// is shown as it is stored; only the link is cleaned. The same rule as /itinerary's telHref and the
// server's views.tel_href (tests/test_travel_planner.py runs all three over one table).
function tp_tel(phone) {
	let text = String(phone == null ? "" : phone)
		.replace(/^\s*tel:/i, "")
		.split(/ext|x|#|;|,/i)[0];
	if (/[a-z]/i.test(text)) {
		const runs = text.match(/\+?\d[\d\s().-]*\d/g) || [];
		if (runs.length !== 1 || runs[0].replace(/\D/g, "").length < 7) return "";
		text = runs[0];
	}
	const digits = text.replace(/\D/g, "");
	if (digits.length < 3 || digits.length > 15) return "";
	return `tel:${/^\s*\+/.test(text) ? "+" : ""}${digits}`;
}

// An email address as a mailto: link, or "" for anything that is not plainly one (no spaces,
// quotes, brackets, or "?" and "&", which would add a subject or a body to the email).
function tp_mailto(email) {
	const value = String(email || "").trim();
	return /^[^\s@<>"'()?&]+@[^\s@<>"'()?&]+\.[^\s@<>"'()?&]+$/.test(value) ? `mailto:${value}` : "";
}

// A web link the server built (a hotel's nearest urgent care, directions): https only.
function tp_https(url) {
	const value = String(url || "").trim();
	return /^https:\/\/[^\s"'<>]+$/i.test(value) ? value : "";
}

// A place's point, when it has a real one: two finite numbers, and not 0,0 (a blank point reads
// back as 0.0 — see api/travel._poi_address_location).
function tp_point(place) {
	if (!place || place.lat == null || place.lng == null || place.lat === "" || place.lng === "") return null;
	const lat = Number(place.lat);
	const lng = Number(place.lng);
	if (!Number.isFinite(lat) || !Number.isFinite(lng) || (lat === 0 && lng === 0)) return null;
	return { lat: lat, lng: lng };
}

// Google Maps, searched for a place: its point when it has one, else its words. Always https.
function tp_maps_search(place) {
	const point = tp_point(place);
	const query = point ? `${point.lat},${point.lng}` : String((place && place.query) || "").trim();
	return query ? `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}` : "";
}

function tp_maps_directions(address) {
	const value = String(address || "").trim();
	return value ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(value)}` : "";
}

// An address on this site (a path), or "". Never "//host", a scheme, or a backslash anywhere: a
// browser reads "/\host" as "//host", another site.
function tp_local_url(url) {
	const value = String(url || "");
	return /^\/(?![/\\])[^\s\\]*$/.test(value) ? value : "";
}

// The trip sheet (the "Trip Sheet" print format, a PDF of the whole trip, or of one person's when
// `as` names them), spelled as views.trip_sheet_url spells it, for the Review step only: its
// answer (get_plan, save_plan) carries no address. Every view draws the server's own address
// instead (get_trip_views' sheet_url, people_sheet_urls). Drawn only when the server says the
// format exists (`sheet_available`): frappe prints a format it cannot find as Standard, costs
// included. &pdf_generator=chrome is not optional: download_pdf uses wkhtmltopdf unless told.
function tp_sheet_url(trip, as) {
	let url = `/api/method/frappe.utils.print_format.download_pdf?doctype=Travel%20Trip&name=${encodeURIComponent(
		trip || ""
	)}&format=Trip%20Sheet&no_letterhead=1&pdf_generator=chrome`;
	if (as) url += `&as=${encodeURIComponent(as)}`;
	return url;
}

// ------------------------------------------------------------------ the Map view's lookups
//
// Where a place the map had to look up is (its words -> {lat, lng}): kept for this page load,
// and in this browser for 30 days, which is as long as Google's terms let a looked-up point be
// kept. A convenience only: every read and write of the browser's storage is tried, and without
// it the map looks the place up again. A place Google could not find is remembered for this
// page load only. A road between two points is kept for this page load only.
const TP_GEO_KEY = "tp_map_geocode_v1";
const TP_GEO_DAYS = 30;
const TP_GEO_KEEP = 300;
const tp_map_memory = { places: new Map(), roads: new Map(), directions_off: false };

function tp_geo_key(query) {
	return String(query || "")
		.trim()
		.toLowerCase();
}

function tp_geo_store() {
	try {
		return window.localStorage || null;
	} catch (e) {
		return null;
	}
}

// A point, null for "not found", or undefined for "never looked up".
function tp_geo_read(query) {
	const key = tp_geo_key(query);
	if (!key) return undefined;
	if (tp_map_memory.places.has(key)) return tp_map_memory.places.get(key);
	try {
		const store = tp_geo_store();
		const all = store ? JSON.parse(store.getItem(TP_GEO_KEY) || "{}") : null;
		const hit = all && all[key];
		if (hit && Number.isFinite(hit.lat) && Number.isFinite(hit.lng) && Date.now() - (hit.at || 0) < TP_GEO_DAYS * 86400000) {
			const point = { lat: hit.lat, lng: hit.lng };
			tp_map_memory.places.set(key, point);
			return point;
		}
	} catch (e) {
		// Blocked, full or unreadable storage is the same as an empty one.
	}
	return undefined;
}

function tp_geo_write(query, point) {
	const key = tp_geo_key(query);
	if (!key) return;
	tp_map_memory.places.set(key, point);
	if (!point) return;
	try {
		const store = tp_geo_store();
		if (!store) return;
		const all = JSON.parse(store.getItem(TP_GEO_KEY) || "{}") || {};
		all[key] = { lat: point.lat, lng: point.lng, at: Date.now() };
		Object.keys(all)
			.sort((a, b) => (all[b].at || 0) - (all[a].at || 0))
			.slice(TP_GEO_KEEP)
			.forEach((old) => delete all[old]);
		store.setItem(TP_GEO_KEY, JSON.stringify(all));
	} catch (e) {
		// As above: the map works without it.
	}
}

// A point Google handed back, as {lat, lng}: a LatLng has lat() and lng() methods, a plain
// LatLngLiteral has lat and lng properties.
function tp_lat_lng(loc) {
	if (!loc) return null;
	if (typeof loc.lat === "function") return { lat: loc.lat(), lng: loc.lng() };
	if (typeof loc.lat === "number") return { lat: loc.lat, lng: loc.lng };
	return null;
}

// The desk's theme, "dark" or "light": what a Google map is built for (EEGoogleMaps.mapOptions).
function tp_desk_theme() {
	const root = typeof document !== "undefined" && document ? document.documentElement : null;
	return root && root.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

// Put a marker or a line on the map, or take it off: a Marker or a Polyline has setMap, an
// AdvancedMarkerElement only its `map` property.
function tp_set_map(thing, map) {
	if (!thing) return;
	if (typeof thing.setMap === "function") thing.setMap(map);
	else thing.map = map;
}

// The whole-trip map, once Google Maps has loaded: a marker per place, numbered as the list
// under it numbers them (view_map), each opening a popup built from elements (never from HTML
// in the data); the drives along the road (DirectionsService, the only road service this view
// uses: Travel Settings' "Use Routes API" is the Pick Routing Map's setting and never reaches
// here), a straight dashed line where no road could be found, and each flight as a dashed line
// along the curve of the earth. `alive()` says whether this map is still the one on screen:
// every answer from Google that lands later is dropped.
class TpTripMap {
	constructor(maps, container, options) {
		this.maps = maps;
		this.alive = options.alive;
		this.config = options.config || {};
		const style = window.EEGoogleMaps.mapOptions(this.config, options.theme);
		this.map = new maps.Map(
			container,
			Object.assign(
				{
					zoom: 4,
					center: { lat: 39.5, lng: -98.35 },
					mapTypeControl: false,
					streetViewControl: false,
					fullscreenControl: true,
					gestureHandling: "cooperative",
				},
				style
			)
		);
		// Advanced markers need a Map ID; without one the map uses the classic marker.
		this.advanced = !!(style.mapId && maps.marker && maps.marker.AdvancedMarkerElement);
		this.info = new maps.InfoWindow();
		this.markers = [];
		this.lines = [];
		this.day = "";
		this.pending = {};
	}

	// Every place on the map; resolves with how many could not be placed. The day shown is the
	// map's own (this.day): a day chip tapped while the places are still being looked up calls
	// show_day, and the draw that finishes after it keeps that day rather than the one it was
	// started with (which put every day back on the map under the tapped chip).
	draw(places, legs, day) {
		this.day = day || "";
		const points = {};
		const placed = places.map((place) =>
			this.locate(place).then((point) => {
				if (!point || !this.alive()) return;
				points[place.key] = point;
				const marker = this.marker_for(place, point);
				marker.addListener("click", () => {
					this.info.setContent(this.popup(place));
					this.info.open({ map: this.map, anchor: marker });
				});
				this.markers.push({ place: place, marker: marker, point: point });
			})
		);
		return Promise.all(placed).then(() => {
			if (!this.alive()) return 0;
			this.show_day(this.day);
			// The markers are up; the lines follow, one road at a time.
			this.draw_legs(legs, points).catch(() => {});
			return places.length - this.markers.length;
		});
	}

	locate(place) {
		const point = tp_point(place);
		if (point) return Promise.resolve(point);
		const query = String(place.query || "").trim();
		if (!query) return Promise.resolve(null);
		const known = tp_geo_read(query);
		if (known !== undefined) return Promise.resolve(known);
		const key = tp_geo_key(query);
		if (this.pending[key]) return this.pending[key];
		this.pending[key] = new Promise((resolve) => {
			try {
				this.geocoder = this.geocoder || new this.maps.Geocoder();
				this.geocoder.geocode({ address: query }, (results, status) => {
					if (status === "OK" && results && results[0] && results[0].geometry) {
						const found = tp_lat_lng(results[0].geometry.location);
						tp_geo_write(query, found);
						resolve(found);
						return;
					}
					if (status === "ZERO_RESULTS") tp_geo_write(query, null);
					resolve(null);
				});
			} catch (e) {
				resolve(null);
			}
		});
		return this.pending[key];
	}

	marker_for(place, point) {
		const kind = TP_PLACE_KINDS[place.kind] || TP_PLACE_KINDS.stop;
		const title = `${place.number}. ${place.label || kind.label}`;
		if (this.advanced) {
			const pin = document.createElement("div");
			pin.textContent = String(place.number);
			pin.style.cssText =
				"min-width:26px;height:26px;padding:0 6px;border-radius:13px;border:2px solid #fff;" +
				"display:flex;align-items:center;justify-content:center;box-sizing:border-box;" +
				`background:${kind.color};color:#fff;font:700 12px/1 sans-serif;box-shadow:0 1px 3px rgba(0,0,0,.4);`;
			return new this.maps.marker.AdvancedMarkerElement({ position: point, content: pin, title: title });
		}
		return new this.maps.Marker({
			position: point,
			title: title,
			label: { text: String(place.number), color: "#ffffff", fontSize: "12px", fontWeight: "700" },
			icon: {
				path: this.maps.SymbolPath.CIRCLE,
				scale: 13,
				fillColor: kind.color,
				fillOpacity: 1,
				strokeColor: "#ffffff",
				strokeWeight: 2,
			},
		});
	}

	// A place's popup: text in elements, and its Google Maps link. The popup is always light,
	// so its colors are literal.
	popup(place) {
		const kind = TP_PLACE_KINDS[place.kind] || TP_PLACE_KINDS.stop;
		const box = document.createElement("div");
		box.style.cssText = "min-width:170px;max-width:260px;color:#202124;font-size:13px;line-height:1.4;";
		const line = (text, bold) => {
			if (!text) return;
			const el = document.createElement("div");
			el.textContent = text;
			if (bold) el.style.fontWeight = "600";
			box.appendChild(el);
		};
		line(`${place.number}. ${place.label || kind.label}`, true);
		line(`${kind.glyph} ${kind.label}`);
		// A hotel is on every night of a stay: its days as runs ("Oct 1 – Oct 4"), not each date.
		line(tp_day_runs(place.days || []).join(", "));
		line(place.first_time ? __("From {0}", [tp_pretty_time(place.first_time)]) : "");
		line((place.who || []).join(", "));
		const url = tp_maps_search(place);
		if (url) {
			const link = document.createElement("a");
			link.href = url;
			link.target = "_blank";
			link.rel = "noopener";
			link.textContent = __("Open in Google Maps");
			box.appendChild(link);
		}
		return box;
	}

	// One day's places and legs, or every day's ("").
	show_day(day) {
		this.day = day || "";
		const bounds = new this.maps.LatLngBounds();
		let shown = 0;
		this.markers.forEach((entry) => {
			const on = !this.day || (entry.place.days || []).includes(this.day);
			tp_set_map(entry.marker, on ? this.map : null);
			if (on) {
				bounds.extend(entry.point);
				shown += 1;
			}
		});
		this.lines.forEach((entry) => tp_set_map(entry.line, this.on_day(entry.leg) ? this.map : null));
		if (shown > 1) {
			this.map.fitBounds(bounds, 48);
		} else if (shown === 1) {
			this.map.setCenter(bounds.getCenter());
			this.map.setZoom(13);
		}
		this.info.close();
	}

	on_day(leg) {
		return !this.day || leg.day === this.day;
	}

	async draw_legs(legs, points) {
		for (const leg of legs) {
			if (!this.alive()) return;
			const from = points[leg.from];
			const to = points[leg.to];
			if (!from || !to || (from.lat === to.lat && from.lng === to.lng)) continue;
			const kind = TP_LEG_COLORS[leg.kind] ? leg.kind : "drive";
			const road = kind === "flight" ? null : await this.road(from, to, kind);
			if (!this.alive()) return;
			const line = road ? this.solid_line(road, kind) : this.dashed_line([from, to], kind);
			this.lines.push({ leg: leg, line: line });
			tp_set_map(line, this.on_day(leg) ? this.map : null);
		}
	}

	// The road from one point to another, as a path, or null (then a straight dashed line), from
	// DirectionsService. A key without the Directions API (REQUEST_DENIED) turns it off for the
	// rest of this page load, so no request is made that cannot succeed.
	road(from, to, kind) {
		const key = `${kind}|${from.lat.toFixed(5)},${from.lng.toFixed(5)}|${to.lat.toFixed(5)},${to.lng.toFixed(5)}`;
		if (tp_map_memory.roads.has(key)) return Promise.resolve(tp_map_memory.roads.get(key));
		const keep = (path) => {
			tp_map_memory.roads.set(key, path);
			return path;
		};
		if (tp_map_memory.directions_off) return Promise.resolve(null);
		return this.by_directions(from, to, kind).then(keep, (status) => {
			if (status === "REQUEST_DENIED") tp_map_memory.directions_off = true;
			if (status === "ZERO_RESULTS" || status === "NOT_FOUND") keep(null);
			return null;
		});
	}

	by_directions(from, to, kind) {
		return new Promise((resolve, reject) => {
			try {
				this.directions = this.directions || new this.maps.DirectionsService();
				this.directions.route(
					{
						origin: from,
						destination: to,
						travelMode: kind === "walk" ? this.maps.TravelMode.WALKING : this.maps.TravelMode.DRIVING,
					},
					(result, status) => {
						const route = status === "OK" && result && result.routes && result.routes[0];
						const path = route ? (route.overview_path || []).map(tp_lat_lng).filter(Boolean) : [];
						if (path.length >= 2) resolve(path);
						else reject(status || "ERROR");
					}
				);
			} catch (e) {
				reject("ERROR");
			}
		});
	}

	solid_line(path, kind) {
		return new this.maps.Polyline({
			path: path,
			strokeColor: TP_LEG_COLORS[kind],
			strokeOpacity: 0.85,
			strokeWeight: 4,
		});
	}

	// A flight, or a drive no road was found for: dashes, so it never reads as the road taken.
	dashed_line(path, kind) {
		return new this.maps.Polyline({
			path: path,
			geodesic: kind === "flight",
			strokeOpacity: 0,
			icons: [
				{
					icon: { path: "M 0,-1 0,1", strokeOpacity: 0.9, strokeColor: TP_LEG_COLORS[kind], scale: 3 },
					offset: "0",
					repeat: "14px",
				},
			],
		});
	}
}

function tp_is_paperwork(gap) {
	return !!gap && gap.check === TP_PAPERWORK;
}

// The quieter tally's number: each person on a flight still without a boarding pass or ticket,
// one file for any other booking — completeness.files_not_attached.
function tp_files_not_attached(gaps) {
	return (gaps || [])
		.filter(tp_is_paperwork)
		.reduce((count, gap) => count + Math.max(1, (gap.employee_names || []).length), 0);
}

function tp_files_not_attached_text(gaps) {
	const count = tp_files_not_attached(gaps);
	return count === 1 ? __("1 file not attached yet") : __("{0} files not attached yet", [count]);
}

function tp_server_messages(xhr) {
	// What frappe.request.cleanup shows for a refused call — its _server_messages, which
	// frappe.msgprint takes as they are — for a call made silent (see TripPlanner.save).
	try {
		const messages = JSON.parse(xhr.responseJSON._server_messages);
		return messages && messages.length ? messages : null;
	} catch (e) {
		return null;
	}
}

class TripPlanner {
	constructor(page, wrapper) {
		this.page = page;
		this.wrapper = wrapper;
		this.body = $('<div class="tp-wrap"></div>').appendTo(page.main);
		$(`<style>${TP_STYLE}</style>`).appendTo(page.main);
		this.step = 0;
		this.state = null;
		this.lookups = null;
		this.saving = null;
		// The trip load() is fetching ({name, promise}), until it answers.
		this.loading = null;
		this.card_seq = 0;
		// Bumped by every move, so an answer for a screen already left (a slow load, a save
		// that finishes after Back) is dropped instead of drawn.
		this.nav_seq = 0;
		// A trip started here is a "draft" until saved; its history entries carry the draft
		// id, and `drafts` maps it to the name the trip got, so Back onto its "?new=1" entries
		// reopens that trip instead of starting a blank one.
		this.draft_id = null;
		this.drafts = {};
		// Trips replaced on screen before the server would take them (see keep_current).
		this.kept = {};
		// Where the entry on screen sits in history (history_mark().pos), how far the last
		// Back/Forward moved (route_delta), and what to say once a refused move is undone
		// (refuse_route).
		this.pos = null;
		this.route_delta = 0;
		this.return_notice = null;
		// The view drawn over the step, if any (TP_VIEWS; see open_view), and whose itinerary
		// View as shows. `views` is get_trip_views' answer for one trip version
		// ({key: name@modified, data}), shared by every view until the trip is saved again.
		this.view = null;
		this.view_as = "";
		this.views = null;
		this.views_failed = null;
		this.views_pending = null;
		// View as's email and calendar invite preview ({employee, open, data, failed}), and the
		// people Side by side leaves out ({trip, people}, for one trip only): things on one
		// screen, not screens of their own.
		this.preview = null;
		this.compare_hidden = null;
		// The Map view's day ({trip, day}, "" for every day: a choice on that screen, not a
		// screen of its own), and a count bumped by every draw of it, so an answer from Google
		// for a map no longer on screen is dropped (view_map).
		this.map_day = null;
		this.map_gen = 0;
		// The Map view's watch on the desk theme (watch_map_theme): one at a time.
		this.map_theme_observer = null;
		// View as asked for someone the saved trip does not have ({trip, shown}): it shows its
		// default person instead and says so (note_missing_person, view_person).
		this.as_missing = null;
		// Who is looking, as the server says (get_plan, get_trip_views): whether they are a
		// travel coordinator, and their own Employee. null until it has said.
		this.viewer = { is_coordinator: null, employee: null };
		// The copy screen (render_copy): {trip, source (get_plan's state for it), failed,
		// start_date, keep_crew, making}, kept while the same trip's copy screen comes back (Back
		// from the copy it made); and `copies`, each copy's draft id -> the trip it was made from,
		// so that screen can say one was made already.
		this.copy = null;
		this.copies = {};
		// Another desk page has been shown since this one last routed (on_hide, route()).
		this.away = false;
		this.bind_unload();
		// No handle_route() here: frappe fires on_page_show right after on_page_load, and
		// route_args() consumes frappe.route_options — a second pass would find them gone
		// and draw the landing over the trip it had just been asked to open.
	}

	bind_unload() {
		$(window).on("beforeunload.tp", () => {
			if ((this.state && this.is_dirty()) || this.unsaved_kept().length) {
				return __("This trip has changes that are not saved yet.");
			}
		});
	}

	// Another desk page took over (the form, a link, the sidebar). Save what is here, and note
	// that the trip on screen may be saved over there before this page is shown again (route()).
	on_hide() {
		this.away = true;
		this.save_quietly();
	}

	// ------------------------------------------------------------------ routing and loading

	handle_route() {
		// Switching trips, or back to the list, saves the one on screen first. A step of the
		// same trip is saved by go(), which also refuses a move to a later step if that save
		// is refused.
		const args = this.route_args();
		const mark = this.history_mark();
		const seq = ++this.nav_seq;
		// How far the entry the page was showing is from this one: above 0 when this move was
		// a Back (go() never refuses one), and what refuse_route() moves by to undo a Forward.
		// 0 when it cannot tell — an entry frappe made carries no mark.
		this.route_delta = mark && typeof mark.pos === "number" && this.pos != null ? this.pos - mark.pos : 0;
		// The reason a refused move gave, due on the entry it went back to and nowhere else.
		const notice = this.return_notice;
		this.return_notice = null;
		const say = notice && mark && mark.pos === notice.pos ? notice.say : null;
		if (this.state && this.is_dirty() && !this.saving && !this.is_on_screen(args, mark)) {
			this.save({ quiet: true }).then(() => {
				// Back/Forward again while that saved: the newer entry has its turn instead.
				if (seq === this.nav_seq) this.route(args, mark);
			});
			return;
		}
		this.route(args, mark);
		if (say) say();
	}

	// The mark this page leaves in history.state on the entries it writes: {draft, back, pos}.
	// frappe's own entries (a link, the sidebar, frappe.set_route from the form) carry none.
	history_mark() {
		const state = window.history.state;
		return state && state.tp ? state.tp : null;
	}

	is_on_screen(args, mark) {
		if (!this.state) return false;
		if (args.trip) return this.state.name === args.trip;
		return !!(args.new && mark && mark.draft && mark.draft === this.draft_id);
	}

	route_args() {
		// What the page was asked to open: {trip, new, step, view, as, copy} — `view` and `as` are
		// a look at the whole trip (see open_view), `as` the person View as shows; `copy` is the
		// copy screen for a past trip (render_copy), an address of its own with no other key.
		//
		// Frappe v16 delivers these in frappe.route_options, not the address bar.
		// frappe.set_route("plan-a-trip", {trip: ...}) (the form's button, the list's + Add)
		// puts the object there and writes the address WITHOUT a query string; a link to
		// /desk/plan-a-trip?trip=... has its query copied there by the router's link handler;
		// and a full load of that URL is copied there by router.set_route_options_from_url on
		// every route change. v1.520.0 read only location.search, so every click redrew the
		// landing — "it just refreshes the page". The address bar is still read as a fallback.
		//
		// Consumed, like the list view does: route_options outlives the route, and a later
		// plain visit would otherwise replay the last trip instead of showing the list.
		const options = frappe.route_options || {};
		const pick = (key) => {
			let value = options[key];
			if (value == null || value === "") value = frappe.utils.get_url_arg(key);
			delete options[key];
			return value == null ? "" : String(value);
		};
		const args = {
			trip: pick("trip"),
			new: pick("new"),
			step: pick("step"),
			view: pick("view"),
			as: pick("as"),
			copy: pick("copy"),
		};
		if (frappe.route_options && !Object.keys(frappe.route_options).length) {
			frappe.route_options = null;
		}
		return args;
	}

	route(args, mark) {
		const draft = (mark && mark.draft) || "";
		// Back on this page from another one (on_hide), whichever entry that lands on.
		const away = this.away;
		this.away = false;
		if (this.is_on_screen(args, mark)) {
			// The trip on screen, asked for from outside this page's own entries (the form's
			// "Trip views" or "Plan step by step", a checklist link), or come back to from
			// another page (Back from the form). It may have been changed and saved there. The
			// page used to keep what it had: the Crew grid drew the answer cached for the old
			// version (views_key is this page's copy of `modified`), and View as swapped a person
			// added on the form for the default one, because it checked against the old crew.
			// With nothing unsaved on it the trip is loaded again. With something unsaved, what
			// was typed stays, and only the views are asked for again.
			if (args.trip && (!mark || away)) {
				if (!this.is_dirty() && !this.saving) {
					this.reload_on_screen(args, mark);
					return;
				}
				this.views = null;
			}
			// Onto one of this trip's views, or from one back to a step (route_view).
			if (this.route_view(args, mark)) return;
			// Back/Forward between this trip's own steps, or a deep link's &step=. A plain visit
			// to the trip on screen (the form's button, no step) keeps the step showing.
			if (args.step || mark) this.jump_to(args.step || "trip", { from_route: true });
			// An entry frappe made for it (the form's "Plan step by step", a checklist link)
			// has no query string — v16 set_route pushes the path alone — so name what it is
			// showing, or Back onto it later, or a reload, would find the list.
			if (!mark) this.set_address(this.address_args());
			return;
		}
		// A "new trip" that is not this page's own entry, while the new trip on screen is
		// still unsaved: keep it rather than start another, and make that entry its own.
		if (args.new && !draft && this.state && !this.state.name) {
			this.set_address(this.address_args());
			return;
		}
		this.keep_current();
		// A view belongs to the trip it names (a reload of a view, Back onto one of another
		// trip, the form's "Trip views"); a new trip and the list have none. Who View as shows
		// is checked once the trip is here (settle_view).
		const want = this.wanted_view(args);
		this.view = want.view || null;
		this.view_as = want.as;
		this.preview = null;
		if (args.trip) {
			if (!this.restore_kept(args.trip, args.step)) this.load(args.trip, args.step, draft);
			return;
		}
		if (args.new) {
			// One of this page's own "new trip" entries is the trip started there, never a
			// blank one — even after it was saved and got a name.
			const saved_as = draft && this.drafts[draft];
			if (saved_as) {
				if (!this.restore_kept(saved_as, args.step)) this.load(saved_as, args.step, draft);
			} else if (!draft || !this.restore_kept(`draft:${draft}`, args.step)) {
				this.start_new(draft);
			}
			return;
		}
		// The copy screen for a past trip: Back from the copy it made, Forward from the list, a
		// reload of it, the form's "Copy trip".
		if (args.copy) {
			this.render_copy(args.copy);
			return;
		}
		this.render_landing();
	}

	// route(), for the trip on screen: load it again, on the step and view the address asks
	// for. The step is the one showing when a frappe entry names none, as route() keeps it.
	// View as's person is checked against the crew as stored, once it has loaded (settle_view).
	reload_on_screen(args, mark) {
		const step = args.step || (mark ? "trip" : TP_STEPS[this.step].key);
		this.view = TP_VIEWS.includes(args.view) ? args.view : null;
		this.view_as = this.view === "person" ? args.as || "" : "";
		this.preview = null;
		this.load(args.trip, step, (mark && mark.draft) || this.draft_id || "");
	}

	// The trip on screen is about to give way to the list, another trip or a new one. One the
	// server would not take — nobody on it yet, or a save that was refused — is kept rather
	// than dropped: Back/Forward to it, or its card on the list, brings it back as it was.
	// Before history had an entry for the list, Back left the page and the trip simply stayed
	// in memory; drawing the list over it would otherwise throw it away without a word.
	keep_current() {
		if (!this.state || (this.state.name && !this.is_dirty())) return;
		const key = this.state.name || `draft:${this.draft_id}`;
		this.kept[key] = { state: this.state, baseline: this.baseline, step: this.step, draft: this.draft_id };
	}

	restore_kept(key, step_key) {
		const kept = this.kept[key];
		if (!kept) return false;
		delete this.kept[key];
		this.state = kept.state;
		this.baseline = kept.baseline;
		this.draft_id = kept.draft;
		this.step = kept.step;
		if (step_key) this.jump_to(step_key, { silent: true });
		this.settle_view();
		this.set_address(this.address_args());
		this.render();
		this.fetch_views();
		return true;
	}

	unsaved_kept() {
		return Object.keys(this.kept).filter((key) => this.serialize(this.kept[key].state) !== this.kept[key].baseline);
	}

	// The address of the step on screen, and of the view drawn over it. The keys are always in
	// this order — trip, step, view, as — because back_one_step and leave_view compare query
	// strings as they are written.
	address_args() {
		const step = TP_STEPS[this.step].key;
		if (!this.state || !this.state.name) return { new: 1, step: step };
		const args = { trip: this.state.name, step: step };
		if (this.view) args.view = this.view;
		if (this.view === "person" && this.view_as) args.as = this.view_as;
		return args;
	}

	query_for(args) {
		return Object.entries(args || {})
			.filter(([, value]) => value)
			.map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
			.join("&");
	}

	set_address(args, push) {
		// Keep the address bar on what is open, so a reload or a bookmark comes back to it
		// (a full load's query string reaches route_args through the router). The history API,
		// not frappe.set_route: the page moves between its own trips itself, and routing to
		// the page it is already on is exactly what went wrong in v1.520.0.
		//
		// `push` makes a new entry (a move the user made: a step, a trip picked from the list);
		// otherwise the entry on screen is corrected in place. A push that would repeat the
		// entry it is on replaces it instead, so no entry is ever in history twice in a row.
		// Each entry is marked (history_mark) with the draft it belongs to, the address of the
		// entry behind it, which the page's own Back button reads (back_one_step), and its
		// position — one more than the entry it was pushed from — which tells handle_route how
		// far a Back/Forward moved. An entry frappe made is numbered as the next one after the
		// entry the page showed last: exact unless other pages' entries sit between them, and
		// a refused move is the only thing that relies on it (refuse_route).
		//
		// Only while this page is the one on screen: an answer that lands after the user went
		// elsewhere must not rewrite the address of wherever they went.
		if ((frappe.get_route() || [])[0] !== "plan-a-trip") return;
		const query = this.query_for(args);
		const url = window.location.pathname + (query ? `?${query}` : "");
		const here = this.history_mark();
		const pos = here && typeof here.pos === "number" ? here.pos : this.pos == null ? 0 : this.pos + 1;
		try {
			if (push && url !== window.location.pathname + window.location.search) {
				const tp = { draft: this.draft_id || null, back: window.location.search, pos: pos + 1 };
				window.history.pushState({ tp: tp }, "", url);
				this.pos = pos + 1;
			} else {
				const tp = Object.assign({}, (window.history.state || {}).tp, {
					draft: this.draft_id || null,
					pos: pos,
				});
				window.history.replaceState(Object.assign({}, window.history.state, { tp: tp }), "", url);
				this.pos = pos;
			}
		} catch (e) {
			// An address bar that does not follow is cosmetic; never fail a load over it.
		}
	}

	// The entry on screen is the one the page is showing now (a Back/Forward onto the step
	// already drawn, which is where undoing a refused move lands).
	note_entry() {
		const mark = this.history_mark();
		if (mark && typeof mark.pos === "number") this.pos = mark.pos;
	}

	// Undo a Forward the page will not follow (go() never refuses a Back): return to the entry
	// of the step still showing, so the one asked for stays in history, and say why once
	// there. Not before: frappe closes any open dialog on every route change
	// (router.set_history -> hide_open_dialog), so a message shown now would vanish on the way.
	// Where the page cannot tell how far it moved — an entry frappe made, such as a link with
	// &step= — it corrects that entry in place and says why at once instead.
	refuse_route(delta, say) {
		if (delta) {
			this.return_notice = { pos: this.pos, say: say };
			window.history.go(delta);
			return;
		}
		this.set_address(this.address_args());
		say();
	}

	// ------------------------------------------------------------------ views: routing
	//
	// A view is drawn over the step it was opened from and is addressed as that step plus
	// &view= (and &as=). Opening one, switching to another, or picking another person for
	// View as is a move the user made: one new history entry, pushed after it is drawn.
	// Back/Forward onto a view's entry redraws it (route_view — go() with the step already on
	// screen draws nothing), and one from a view to its step's entry puts the step back.

	// The view an address asks for: {view, as}, "" for none. Only a saved trip has views, and
	// only View as has a person — checked against the crew once the trip is on screen.
	wanted_view(args) {
		let view = args.trip && TP_VIEWS.includes(args.view) ? args.view : "";
		let as = "";
		if (view === "person") {
			as = args.as || "";
			if (this.state && this.state.name && this.state.name === args.trip) {
				const asked = as;
				as = this.person_or_default(asked);
				this.note_missing_person(asked, as);
				if (!as) view = "";
			}
		}
		return { view: view, as: as };
	}

	// After a load: drop a view the trip cannot have, and put a person View as can show.
	settle_view() {
		if (!this.view) return;
		if (!this.state || !this.state.name || !TP_VIEWS.includes(this.view)) {
			this.view = null;
			this.view_as = "";
			return;
		}
		if (this.view !== "person") {
			this.view_as = "";
			return;
		}
		const asked = this.view_as;
		this.view_as = this.person_or_default(asked);
		this.note_missing_person(asked, this.view_as);
		if (!this.view_as) this.view = null;
	}

	person_or_default(employee) {
		if (employee && this.crew().some((t) => t.employee === employee)) return employee;
		return this.default_person();
	}

	// An address naming someone the trip does not have (a link from before they were taken off,
	// or a typo): View as shows its default person, as it always has, but no longer silently —
	// view_person says the person asked for is not on the saved trip. Never named: the id came
	// from an address.
	note_missing_person(asked, shown) {
		this.as_missing =
			asked && shown && asked !== shown && this.state ? { trip: this.state.name, shown: shown } : null;
	}

	// Whom View as opens on: the person looking, when they are going; else the trip lead;
	// else whoever is first.
	default_person() {
		const crew = this.crew().filter((t) => t.employee);
		const me = this.viewer.employee;
		if (me && crew.some((t) => t.employee === me)) return me;
		const lead = crew.find((t) => t.is_trip_lead);
		return lead ? lead.employee : crew.length ? crew[0].employee : "";
	}

	// route(), for the trip already on screen: true when it was a view's move and is handled.
	route_view(args, mark) {
		const want = this.wanted_view(args);
		const key = args.step || (mark ? "trip" : "");
		const index = key ? TP_STEPS.findIndex((s) => s.key === key) : this.step;
		if (want.view) {
			// Onto a view's entry (Back/Forward, the form's "Trip views" onto the trip on
			// screen). Its step is only where "Back to planning" returns to, so it is taken as
			// it is, with no checks: nothing is edited under a view.
			const step = index >= 0 ? index : this.step;
			if (want.view === this.view && want.as === this.view_as && step === this.step) {
				this.note_entry();
				if (!mark) this.set_address(this.address_args());
				// Nothing to redraw, but this move bumped nav_seq, so an answer still on its way
				// for this very screen will be dropped when it lands (a Forward refused while the
				// views loaded comes straight back here). Ask again for whatever it is waiting
				// on, or it says "Loading..." for good: fetch_views does nothing when the answer
				// is already here.
				this.fetch_views();
				this.retry_preview();
				return true;
			}
			this.enter_view(want.view, want.as, true, step);
			return true;
		}
		if (!this.view) return false;
		// From a view to its own step's entry (Back, or the form's "Plan step by step"): the
		// step comes back, and nothing is saved — nothing was edited.
		if (index < 0 || index === this.step) {
			this.close_view(false);
			if (!mark || index < 0) this.set_address(this.address_args());
			else this.note_entry();
			return true;
		}
		// To another step's entry: go() moves there and show_step() puts the view away. A
		// Forward it refuses goes back to the view's entry, and the view is still on screen.
		return false;
	}

	// A view picked on the page (the view bar, a name on the crew grid, the person menu): a
	// new history entry. Never refused — only a saved trip has views, and nothing is edited in
	// one.
	open_view(view, as) {
		if (!this.state || !this.state.name || !TP_VIEWS.includes(view)) return;
		as = view === "person" ? this.person_or_default(as) : "";
		if (view === "person" && !as) return;
		if (view === this.view && as === this.view_as) return;
		// Picked on the page, from the crew: nobody is missing.
		this.as_missing = null;
		this.enter_view(view, as, false);
	}

	// Draw a view: a user's move pushes an entry once it is drawn, one Back/Forward asked for
	// is already in the address (which is corrected in place, e.g. an unknown &as=). Anything
	// unsaved on the page is saved quietly first — a view shows the saved trip — and when that
	// save is refused the view opens anyway and says the changes are not in it (render_view:
	// a view has no "Not saved" pill, since the pill belongs to the Back/Next bar a view does
	// not draw). `step`: the step a view's entry was opened from, which it is drawn over.
	enter_view(view, as, from_route, step) {
		const seq = from_route ? this.nav_seq : ++this.nav_seq;
		const draw = () => {
			// Back/Forward, or another tap, while that saved: that move decides.
			if (seq !== this.nav_seq) return;
			if (step != null) this.step = step;
			this.view = view;
			this.view_as = as;
			this.preview = null;
			this.render();
			frappe.utils.scroll_to(0);
			this.set_address(this.address_args(), !from_route);
			this.fetch_views();
		};
		if (this.is_dirty()) this.save({ quiet: true }).then(draw);
		else draw();
	}

	// Put the view away and show its step again; `push` for a move made on the page. What a
	// refused save left on the page is marked "Not saved" again: render() draws a new, empty
	// pill.
	close_view(push) {
		this.view = null;
		this.view_as = "";
		this.preview = null;
		this.render();
		if (this.is_dirty()) this.set_save_state("error");
		frappe.utils.scroll_to(0);
		if (push) this.set_address(this.address_args(), true);
	}

	// "Back to planning": the same as the browser's Back when the entry behind is this view's
	// step — so view, Back to planning, view piles nothing up — and a new entry for the step
	// otherwise (a view opened from a link or a reload has nothing of this page's behind it).
	leave_view() {
		if (!this.view || !this.state) return;
		const plain = { trip: this.state.name, step: TP_STEPS[this.step].key };
		const mark = this.history_mark();
		if (mark && mark.back === `?${this.query_for(plain)}`) {
			window.history.back();
			return;
		}
		++this.nav_seq;
		this.close_view(true);
	}

	views_key() {
		return this.state && this.state.name ? `${this.state.name}@${this.state.modified || ""}` : "";
	}

	// Every view draws from one get_trip_views answer per version of the trip: fetched once,
	// kept until a save gives the trip a new `modified`. An answer for a screen already left
	// is dropped, like load()'s, and not kept either.
	fetch_views() {
		if (!this.view || !this.state || !this.state.name) return;
		const key = this.views_key();
		if (this.views && this.views.key === key) return;
		const seq = this.nav_seq;
		const pending = this.views_pending;
		if (pending && pending.key === key && pending.seq === seq) return;
		const asked = { key: key, seq: seq };
		this.views_pending = asked;
		this.views_failed = null;
		const done = (data) => {
			if (this.views_pending === asked) this.views_pending = null;
			// Moved on while it loaded (Back, another view, another trip): that screen wins.
			if (seq !== this.nav_seq) return;
			if (data && typeof data === "object") {
				this.views = { key: key, data: data };
				this.note_viewer(data);
			} else {
				this.views_failed = key;
			}
			if (this.view) this.render();
		};
		frappe
			.call({ method: "erpnext_enhancements.api.travel.get_trip_views", args: { trip: this.state.name } })
			.then(
				(r) => done(r && r.message),
				() => done(null)
			);
	}

	// What the server says about the person looking (get_plan's state, get_trip_views).
	note_viewer(source) {
		if (!source) return;
		if (source.is_coordinator !== undefined && source.is_coordinator !== null) {
			this.viewer.is_coordinator = !!source.is_coordinator;
		}
		if (source.viewer_employee !== undefined) this.viewer.employee = source.viewer_employee || null;
	}

	// load() and start_new() replace the trip on screen (route() has kept it first if it
	// needed keeping), so it is cleared before the fetch: a failed load must not leave the old
	// trip in `state` looking as if it were the one showing. `loading` says which trip is on
	// its way, for a file that finishes uploading meanwhile (add_document waits for it).
	load(name, step_key, draft) {
		const seq = this.nav_seq;
		this.state = null;
		this.remove_chrome();
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		const loading = { name: name, promise: null };
		const done = () => {
			if (this.loading === loading) this.loading = null;
		};
		this.loading = loading;
		loading.promise = frappe
			.call({ method: "erpnext_enhancements.travel_management.planner.get_plan", args: { trip: name } })
			.then(
				(r) => {
					done();
					// Moved on while it loaded (Back again, another trip): that screen wins.
					if (seq !== this.nav_seq) return;
					const data = (r && r.message) || {};
					this.lookups = data.lookups;
					this.draft_id = draft || null;
					this.adopt(data.state);
					this.step = 0;
					if (step_key) this.jump_to(step_key, { silent: true });
					// A view asked for with the trip (a reload of one, the form's "Trip views").
					this.settle_view();
					this.set_address(this.address_args());
					this.render();
					this.fetch_views();
				},
				() => {
					done();
					if (seq !== this.nav_seq) return;
					this.body.html(`<div class="tp-empty">${__("This trip could not be opened.")}</div>`);
				}
			);
	}

	start_new(draft) {
		const seq = this.nav_seq;
		this.state = null;
		this.remove_chrome();
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		frappe.call({ method: "erpnext_enhancements.travel_management.planner.get_plan" }).then(
			(r) => {
				if (seq !== this.nav_seq) return;
				const data = (r && r.message) || {};
				this.lookups = data.lookups;
				this.draft_id = draft || frappe.utils.get_random(8);
				this.adopt(this.blank_state());
				this.step = 0;
				this.set_address(this.address_args());
				this.render();
			},
			() => {
				if (seq !== this.nav_seq) return;
				this.body.html(`<div class="tp-empty">${__("You are not allowed to plan trips.")}</div>`);
			}
		);
	}

	// From the list: a new history entry, so Back returns to the list.
	open_from_landing(args) {
		if (args.trip && this.kept[args.trip]) {
			this.carry_on(args.trip);
			return;
		}
		++this.nav_seq;
		this.view = null;
		this.view_as = "";
		if (args.trip) {
			this.draft_id = null;
			this.set_address({ trip: args.trip, step: "trip" }, true);
			this.load(args.trip);
		} else {
			this.draft_id = frappe.utils.get_random(8);
			this.set_address({ new: 1, step: "trip" }, true);
			this.start_new(this.draft_id);
		}
	}

	carry_on(key) {
		const kept = this.kept[key];
		if (!kept) return;
		++this.nav_seq;
		this.view = null;
		this.view_as = "";
		this.draft_id = kept.draft;
		const step = TP_STEPS[kept.step].key;
		this.set_address(kept.state.name ? { trip: kept.state.name, step: step } : { new: 1, step: step }, true);
		this.restore_kept(key);
	}

	render_landing() {
		this.state = null;
		this.draft_id = null;
		this.view = null;
		this.view_as = "";
		this.preview = null;
		this.set_address({});
		this.remove_chrome();
		this.body.removeClass("tp-views-wide");
		const seq = this.nav_seq;
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		// The past trips to copy, asked for alongside: a list that cannot be had is left off,
		// never the landing with it.
		const copyable = frappe
			.call({ method: "erpnext_enhancements.travel_management.planner.get_copyable_trips", silent: true })
			.then(
				(r) => (Array.isArray(r && r.message) ? r.message : []),
				() => []
			);
		frappe.call({ method: "erpnext_enhancements.travel_management.planner.get_recent_plans" }).then(
			(r) => copyable.then((past) => this.draw_landing(seq, (r && r.message) || [], past)),
			() => {
				if (seq !== this.nav_seq) return;
				this.body.html(`<div class="tp-empty">${__("Could not load trips.")}</div>`);
			}
		);
	}

	// The list: trips put aside unsaved, "Start a new trip", the trips to carry on with, and the
	// past trips to copy (see COPY A PAST TRIP in the header).
	draw_landing(seq, rows, past) {
		if (seq !== this.nav_seq) return;
		const items = rows
			.map(
				(row) => `<button class="tp-list-item" data-name="${tp_esc(row.name)}">
					<h5>${tp_esc(row.purpose || row.name)}</h5>
					<div class="tp-muted">${tp_esc(tp_pretty_date(row.start_date))} - ${tp_esc(
					tp_pretty_date(row.end_date)
				)} &middot; ${tp_esc(__(row.status))} &middot; ${tp_esc(row.name)}</div>
				</button>`
			)
			.join("");
		// Trips put aside unsaved on the way here (keep_current), first: their changes exist
		// nowhere else.
		const unsaved = this.unsaved_kept()
			.map((key) => {
				const kept = this.kept[key];
				return `<div class="tp-gap"><span>${__("{0} has changes that are not saved yet.", [
					tp_esc(kept.state.trip.purpose || kept.state.name || __("A new trip")),
				])}</span><button class="tp-btn-link" data-kept="${tp_esc(key)}">${__("Carry on")} &rarr;</button></div>`;
			})
			.join("");
		this.body.html(`
			${unsaved}
			<div class="tp-card">
				<div class="tp-step-title">${__("Plan a new trip")}</div>
				<div class="tp-step-help">${__(
					"One step at a time: the trip, who's going, how everyone gets there and back, where they sleep, and the schedule. At the end you get a checklist of anything still missing."
				)}</div>
				<button class="tp-btn tp-btn-primary" data-action="new">${__("Start a new trip")}</button>
			</div>
			${
				rows.length
					? `<h5 style="margin:18px 0 8px;">${__("Carry on with a trip")}</h5>${items}`
					: `<div class="tp-muted">${__("No upcoming trips yet.")}</div>`
			}
			${this.copy_list(past)}`);
		// Opened directly, not through frappe.set_route: routing to the page we are already on
		// is what made these buttons redraw the landing in v1.520.0.
		this.body.find('[data-action="new"]').on("click", () => this.open_from_landing({ new: 1 }));
		this.body.find(".tp-list-item").on("click", (event) => {
			this.open_from_landing({ trip: String($(event.currentTarget).attr("data-name")) });
		});
		this.body.find("[data-kept]").on("click", (event) => {
			this.carry_on(String($(event.currentTarget).attr("data-kept")));
		});
		this.body.find("[data-copy]").on("click", (event) => {
			this.open_copy(String($(event.currentTarget).attr("data-copy")));
		});
	}

	// "Copy a past trip" on the list: the latest few, and the rest behind "Show N more" (a choice
	// on the list, not a screen). "" when there is nothing to copy.
	copy_list(past) {
		const trips = (past || []).filter((row) => row && row.name);
		if (!trips.length) return "";
		const item = (row) => {
			const dates = tp_span(tp_pretty_date(row.start_date), tp_pretty_date(row.end_date));
			const job = row.travel_for_name ? ` &middot; ${__("for {0}", [tp_esc(row.travel_for_name)])}` : "";
			return `<button class="tp-copy-item" data-copy="${tp_esc(row.name)}">
				<h5>${tp_esc(row.purpose || row.name)}</h5>
				<div class="tp-muted">${tp_esc(dates)} &middot; ${tp_esc(__(row.status || ""))} &middot; ${tp_esc(
				row.name
			)}${job}</div>
			</button>`;
		};
		const first = trips.slice(0, TP_COPY_SHOWN).map(item).join("");
		const rest = trips.slice(TP_COPY_SHOWN);
		return `<h5 style="margin:18px 0 4px;">${__("Copy a past trip")}</h5>
			<div class="tp-step-help">${__(
				"Plan another trip like one already done: the same crew, hotel, rentals, stops and freight, moved to new dates. Confirmation numbers and costs are left behind."
			)}</div>
			${first}
			${
				rest.length
					? `<details class="tp-copy-more"><summary>${__("Show {0} more", [rest.length])}</summary>${rest
							.map(item)
							.join("")}</details>`
					: ""
			}`;
	}

	// ------------------------------------------------------------------ copy a past trip

	// A past trip picked on the list: its copy screen, as a new history entry, so Back returns
	// to the list.
	open_copy(trip) {
		if (!trip) return;
		++this.nav_seq;
		this.set_address({ copy: trip }, true);
		this.render_copy(trip);
	}

	// The copy screen for `trip` (see COPY A PAST TRIP in the header). Like the list, it has no
	// trip of its own on screen: route() has kept the one it replaces, if that needed keeping.
	// What was picked on it, and the trip it copies, stay while it is the same trip's copy screen,
	// so Back from the copy it made comes straight back to it as it was.
	render_copy(trip) {
		this.state = null;
		this.draft_id = null;
		this.view = null;
		this.view_as = "";
		this.preview = null;
		this.set_address({ copy: trip });
		this.remove_chrome();
		this.body.removeClass("tp-views-wide");
		if (!this.copy || this.copy.trip !== trip) {
			this.copy = {
				trip: trip,
				source: null,
				failed: false,
				start_date: tp_next_weekday(frappe.datetime.get_today()),
				keep_crew: true,
				making: false,
			};
		}
		const copy = this.copy;
		copy.making = false;
		if (copy.source) {
			this.draw_copy();
			return;
		}
		const seq = this.nav_seq;
		copy.failed = false;
		this.body.html(`<div class="tp-empty">${__("Loading...")}</div>`);
		frappe
			.call({ method: "erpnext_enhancements.travel_management.planner.get_plan", args: { trip: trip } })
			.then(
				(r) => {
					// Moved on while it loaded: that screen wins.
					if (seq !== this.nav_seq || this.copy !== copy) return;
					const data = r && r.message;
					if (data && data.state && data.state.trip) copy.source = data.state;
					else copy.failed = true;
					this.draw_copy();
				},
				() => {
					if (seq !== this.nav_seq || this.copy !== copy) return;
					copy.failed = true;
					this.draw_copy();
				}
			);
	}

	draw_copy() {
		const copy = this.copy;
		if (!copy) return;
		this.body.empty();
		const $top = $('<div class="tp-view-top"></div>').appendTo(this.body);
		$(`<button class="tp-btn-link">&larr; ${__("Back to the list")}</button>`)
			.appendTo($top)
			.on("click", () => this.leave_copy());
		$(`<div class="tp-step-title">${__("Copy a past trip")}</div>`).appendTo(this.body);
		if (copy.failed || !copy.source) {
			$(`<div class="tp-empty">${__("This trip could not be opened.")}</div>`).appendTo(this.body);
			return;
		}
		$(
			`<div class="tp-step-help">${__(
				"The copy brings over the crew, hotels, rentals and drives, flights, stops and freight, every date moved by the same number of days. Confirmation numbers, tracking numbers, costs and files are left behind: nothing on it is booked yet."
			)}</div>`
		).appendTo(this.body);
		this.copy_source_card(this.body, copy.source, copy.trip);
		this.copy_made_note(this.body);

		const $form = $('<div class="tp-card"></div>').appendTo(this.body);
		const $grid = $('<div class="tp-grid"></div>').appendTo($form);
		const $when = $('<div class="tp-muted tp-wide"></div>');
		const say_when = () => $when.text(this.copy_dates_text());
		this.input(this.field($grid, __("New start date"), true), "date", copy.start_date, (value) => {
			copy.start_date = value;
			say_when();
		});
		$when.appendTo($grid);
		say_when();
		const $crew = $(`<label class="tp-check tp-wide"><input type="checkbox"> ${__(
			"Bring the same crew"
		)}</label>`).appendTo($grid);
		const $crew_help = $('<div class="tp-muted tp-wide"></div>').appendTo($grid);
		const say_crew = () =>
			$crew_help.text(
				copy.keep_crew
					? __("Everyone on it goes again, on the same bookings. Change who's going on the next steps.")
					: __("Pick who's going on the Who's going step. They go on every booking, and you change who is on each one on the steps after it.")
			);
		$crew
			.find("input")
			.prop("checked", !!copy.keep_crew)
			.on("change", (e) => {
				copy.keep_crew = !!e.target.checked;
				say_crew();
			});
		say_crew();
		const $go = $('<div class="tp-copy-go"></div>').appendTo($form);
		$(`<button class="tp-btn tp-btn-primary">${copy.making ? __("Making the copy...") : __("Make the copy")}</button>`)
			.appendTo($go)
			.prop("disabled", !!copy.making)
			.on("click", () => this.make_copy());
	}

	// What the trip being copied had: its dates, who went, and how many of each booking. No
	// money: the copy leaves every cost behind.
	copy_source_card($parent, s, trip) {
		const t = s.trip || {};
		const dates = tp_span(tp_pretty_date(t.start_date), tp_pretty_date(t.end_date));
		const job = t.travel_for_name ? ` &middot; ${__("for {0}", [tp_esc(t.travel_for_name)])}` : "";
		const crew = (s.travelers || []).map((x) => x.employee_name || x.employee).filter(Boolean);
		const count = (table) => ((s.bookings || {})[table] || []).length;
		const had = [
			[__("Flights"), count("flights")],
			[__("Rooms"), count("accommodations")],
			[__("Drives and rentals"), count("ground_transport")],
			[__("Shipments"), (s.freight || []).length],
			[__("Stops"), (s.stops || []).length],
		]
			.filter(([, n]) => n)
			.map(([label, n]) => `${tp_esc(label)}: ${n}`);
		$(`<div class="tp-card">
			<h5 style="margin:0 0 4px;font-size:16px;">${tp_esc(t.purpose || trip)}</h5>
			<div class="tp-muted">${tp_esc(dates)} &middot; ${tp_esc(__(s.status || ""))} &middot; ${tp_esc(
			s.name || trip
		)}${job}</div>
			<div class="tp-copy-facts">
				<div>${crew.length ? __("Who went: {0}", [crew.map(tp_esc).join(", ")]) : __("Nobody was on it.")}</div>
				${had.length ? `<div>${had.join(" &middot; ")}</div>` : ""}
			</div>
		</div>`).appendTo($parent);
	}

	// Copies of this trip made on this page already: Back from one saves it, as leaving any trip
	// does, so each is offered here to open rather than let a second one be made without a word.
	copy_made_note($parent) {
		const copy = this.copy;
		Object.keys(this.copies)
			.filter((draft) => this.copies[draft] === copy.trip)
			.forEach((draft) => {
				const name = this.drafts[draft];
				if (name) {
					$(`<div class="tp-notice">${__("You made a copy of this trip already: {0}.", [tp_esc(name)])}
						<button class="tp-btn-link" data-made="${tp_esc(name)}">${__("Open it")} &rarr;</button></div>`)
						.appendTo($parent)
						.find("button")
						.on("click", () => this.open_from_landing({ trip: name }));
				} else if (this.kept[`draft:${draft}`]) {
					$(`<div class="tp-notice">${__("You made a copy of this trip that is not saved yet.")}
						<button class="tp-btn-link" data-made="draft:${tp_esc(draft)}">${__("Carry on")} &rarr;</button></div>`)
						.appendTo($parent)
						.find("button")
						.on("click", () => this.carry_on(`draft:${draft}`));
				}
			});
	}

	// "The new trip: Mon, Nov 2 – Wed, Nov 4": the copied trip's length, from the day picked.
	copy_dates_text() {
		const copy = this.copy;
		const t = (copy && copy.source && copy.source.trip) || {};
		const length = tp_days_apart(t.start_date, t.end_date);
		if (!copy || !copy.start_date || isNaN(length)) return "";
		const end = tp_add_days(copy.start_date, Math.max(0, length));
		return __("The new trip: {0}.", [tp_span(tp_pretty_date(copy.start_date), tp_pretty_date(end))]);
	}

	// "Back to the list": the browser's Back when the list is the entry behind (it is whenever the
	// list led here), and a new entry for the list otherwise (the form's "Copy trip", a reload).
	leave_copy() {
		const mark = this.history_mark();
		if (mark && mark.back === "") {
			window.history.back();
			return;
		}
		++this.nav_seq;
		this.set_address({}, true);
		this.render_landing();
	}

	// "Make the copy". Its answer is dropped when the page has moved on meanwhile (Back, the
	// list, another desk page): no draft appears behind whatever is on screen then, and the copy
	// screen, shown again, can make it again (render_copy clears `making`). One press is one
	// copy: a second while the first is on its way does nothing.
	make_copy() {
		const copy = this.copy;
		if (!copy || !copy.source || copy.making) return;
		if (!copy.start_date) {
			frappe.msgprint({
				title: __("The new first day"),
				message: __("Pick the day the new trip starts."),
				indicator: "orange",
			});
			return;
		}
		const seq = ++this.nav_seq;
		copy.making = true;
		this.draw_copy();
		const current = () => {
			if (seq !== this.nav_seq || this.away || this.copy !== copy) return false;
			copy.making = false;
			return true;
		};
		frappe
			.call({
				method: "erpnext_enhancements.travel_management.planner.copy_plan",
				args: { trip: copy.trip, start_date: copy.start_date, keep_crew: copy.keep_crew ? 1 : 0 },
			})
			.then(
				(r) => {
					if (!current()) return;
					const data = r && r.message;
					if (data && data.state && data.state.trip) {
						this.start_copy(data, copy.trip);
						return;
					}
					this.draw_copy();
					frappe.msgprint({
						title: __("Copy a past trip"),
						message: __("The copy could not be made. Try again, or start a new trip."),
						indicator: "orange",
					});
				},
				() => {
					// The server's reason is already on screen: frappe.call shows it.
					if (current()) this.draw_copy();
				}
			);
	}

	// A copy made: a trip of its own, started the way "Start a new trip" starts one — a draft,
	// drawn and then pushed as its "?new=1" entry, so Back returns to the copy screen.
	start_copy(data, source) {
		this.lookups = data.lookups || this.lookups || { employees: [] };
		this.draft_id = frappe.utils.get_random(8);
		this.copies[this.draft_id] = source;
		this.view = null;
		this.view_as = "";
		this.preview = null;
		this.adopt_copy(data.state);
		this.step = 0;
		this.render();
		frappe.utils.scroll_to(0);
		this.set_address({ new: 1, step: "trip" }, true);
		frappe.show_alert({
			message: __("Copied. Check the dates and the crew: it is saved when you press Next."),
			indicator: "blue",
		});
	}

	// planner.copy_plan's state: get_plan's shape for a trip not saved yet. Every booking on it is
	// new, so each card is marked changed in every shared field as new_card marks one (the first
	// save writes all of it), and the trip is unsaved as a whole: no baseline, so the first Next
	// saves it even when nothing on it was touched — and so does leaving it, like any new trip.
	// Its notes go with that first save as they were written (payload).
	adopt_copy(state) {
		state.travelers = state.travelers || [];
		state.stops = state.stops || [];
		this.adopt(state);
		const s = this.state;
		const keys = s.freight.map((item) => item.booking_group);
		Object.keys(s.bookings).forEach((table) => {
			s.bookings[table].forEach((card) => {
				(TP_SHARED[table] || []).forEach((field) => card.changed.add(field));
				card.changed.add("cost");
				keys.push(card.group);
				// A copy made without the crew: nobody is on this booking yet, and the page will
				// not save a booking with nobody on it, so it waits for the crew (add_traveler).
				if (!card.members.length) card.awaiting_crew = true;
			});
		});
		// The copy's own "new:<n>" keys: a booking added to it here must not take one of them.
		keys.forEach((key) => {
			const match = /^new:(\d+)$/.exec(String(key || ""));
			if (match) this.card_seq = Math.max(this.card_seq, Number(match[1]));
		});
		this.baseline = "";
	}

	// options: `silent` sets the step without moving (a load before its first draw);
	// `from_route` is a move Back/Forward asked for (see go()).
	jump_to(step_key, options) {
		options = options || {};
		const index = TP_STEPS.findIndex((s) => s.key === step_key);
		if (index < 0) {
			// An address naming no step this page has: stay, and say where we are instead.
			if (options.from_route) this.set_address(this.address_args());
			return;
		}
		if (options.silent) {
			this.step = index;
		} else {
			this.go(index, options.from_route);
		}
	}

	// ------------------------------------------------------------------ state

	blank_state() {
		return {
			name: null,
			modified: null,
			status: "Planning",
			can_write: true,
			trip: {
				purpose: "",
				travel_type: "Domestic",
				company: this.lookups.default_company,
				start_date: "",
				end_date: "",
				travel_for_doctype: "",
				travel_for_name: "",
				billable: 0,
				trip_description: "",
			},
			travelers: [],
			bookings: { flights: [], accommodations: [], ground_transport: [] },
			freight: [],
			stops: [],
			documents: [],
			gaps: [],
		};
	}

	adopt(state) {
		// Server state -> page state. Cards get a page id, an empty `changed` set, and the
		// "same confirmation for everyone" switch set from what is stored. Who is looking
		// (is_coordinator, viewer_employee: planner.get_plan) is about the person, not the
		// trip, so it is kept on the page and outlives a save that does not repeat it.
		this.note_viewer(state);
		state.bookings = state.bookings || { flights: [], accommodations: [], ground_transport: [] };
		Object.keys(state.bookings).forEach((table) => {
			state.bookings[table].forEach((card) => this.prepare_card(card, table));
		});
		state.freight = state.freight || [];
		// The trip's files (Trip Document rows). A file on a booking names the card's key; one
		// on a card that was "new:<n>" comes back from save_plan already naming the id that
		// card was given, so nothing is re-keyed here.
		state.documents = state.documents || [];
		this.rehome_documents(state);
		state.description_text = tp_html_to_text(state.trip.trip_description);
		state.description_changed = false;
		this.state = state;
		this.infer_legs();
		this.baseline = this.serialize();
	}

	prepare_card(card, table) {
		card.table = table;
		card.uid = `c${++this.card_seq}`;
		card.changed = new Set();
		card.members = card.members || [];
		const refs = card.members.map((m) => m.ref || "");
		card.same_ref = refs.every((ref) => ref === refs[0]);
		return card;
	}

	infer_legs() {
		// A flight or drive typed on the form may have no Leg. Place it by its people's own
		// dates — the rule in completeness.booking_leg, which the checklist's "Fix" links use —
		// and mark it so the next save makes it explicit. v1.520.0 used the TRIP's dates,
		// which put a whole-crew flight on day two (how the late starters got there) under
		// "Getting around", and saving would have stored that.
		const t = this.state.trip;
		const crew = this.crew();
		["flights", "ground_transport"].forEach((table) => {
			this.state.bookings[table].forEach((card) => {
				if (card.values.leg) return;
				const when = tp_date_part(
					table === "flights" ? card.values.departure_time : card.values.pickup_datetime
				);
				let leg;
				if (!when) {
					const hired = ["Rental/Third Party", "Taxi/Rideshare"].includes(card.values.transport_type);
					leg = table === "ground_transport" && hired ? "During Trip" : "Outbound";
				} else {
					const whole = card.members.some((m) => !m.traveler);
					let pool = crew.filter((c) => whole || card.members.some((m) => m.traveler === c.employee));
					if (!pool.length) pool = crew;
					const windows = pool.length
						? pool.map((c) => [c.from_date || t.start_date, c.to_date || t.end_date])
						: [[t.start_date, t.end_date]];
					if (windows.some(([from]) => from && when <= from)) leg = "Outbound";
					else if (windows.some(([, to]) => to && when >= to)) leg = "Return";
					else leg = "During Trip";
				}
				card.values.leg = leg;
				card.changed.add("leg");
			});
		});
	}

	serialize(state) {
		const s = state || this.state;
		return JSON.stringify({
			trip: s.trip,
			desc: s.description_changed ? s.description_text : null,
			status: s.status,
			travelers: s.travelers,
			bookings: Object.keys(s.bookings).map((table) =>
				s.bookings[table].map((card) => [card.values, card.members, card.mileage || null])
			),
			freight: s.freight,
			stops: s.stops,
			documents: s.documents || [],
		});
	}

	is_dirty() {
		return !!this.state && this.serialize() !== this.baseline;
	}

	crew() {
		return this.state ? this.state.travelers : [];
	}

	crew_name(employee) {
		const t = this.crew().find((x) => x.employee === employee);
		if (t) return t.employee_name || employee;
		const e = (this.lookups.employees || []).find((x) => x.name === employee);
		return e ? e.employee_name || employee : employee;
	}

	cards(table) {
		return this.state.bookings[table];
	}

	// A shipment's key, the way completeness.group_key reads it: its stored booking_group, else
	// its own row. A shipment added on the page has a "new:<n>" key until its first save gives
	// it an id (planner.merge_freight), the same as a new card.
	freight_key(item) {
		return item.booking_group || (item.name ? `row:${item.name}` : "");
	}

	documents() {
		return (this.state && this.state.documents) || [];
	}

	// The files on one booking (its page key), or, for "", the trip's own.
	documents_for(key) {
		return this.documents().filter((doc) => (doc.booking_group || "") === (key || ""));
	}

	// Who is on a card: its people, or everyone for a whole-crew card entered on the form.
	// `state`: the trip the card is on, when it is not the one on screen.
	card_people(card, state) {
		const crew = state ? state.travelers || [] : this.crew();
		if (card.members.some((m) => !m.traveler)) return crew.map((t) => t.employee);
		return card.members.map((m) => m.traveler).filter(Boolean);
	}

	leg_cards(leg) {
		return this.cards("flights")
			.filter((c) => c.values.leg === leg)
			.concat(this.cards("ground_transport").filter((c) => c.values.leg === leg));
	}

	set_value(card, field, value) {
		card.values[field] = value;
		card.changed.add(field);
	}

	new_card(table, leg, members) {
		const values = {};
		TP_SHARED[table].forEach((field) => {
			values[field] = "";
		});
		values.billable = this.state.trip.billable ? 1 : 0;
		values.paid_by = "Company";
		values.cost = 0;
		if (leg) values.leg = leg;
		const card = this.prepare_card(
			{
				group: `new:${++this.card_seq}`,
				values: values,
				members: (members || []).map((employee) => ({ name: null, traveler: employee, ref: "" })),
			},
			table
		);
		card.same_ref = true;
		TP_SHARED[table].forEach((field) => card.changed.add(field));
		card.changed.add("cost");
		if (table === "ground_transport") card.mileage = { driver: "", distance: 0 };
		return card;
	}

	uncovered(leg) {
		// Crew not yet on any card for this leg (or, for rooms, in no room and needing a bed at
		// all), so a new card starts with the people who still need one.
		const covered = new Set();
		const pool = leg ? this.leg_cards(leg) : this.cards("accommodations");
		pool.forEach((card) => card.members.forEach((m) => covered.add(m.traveler)));
		const everyone = this.crew().map((t) => t.employee);
		const missing = this.crew()
			.filter((t) => !covered.has(t.employee) && (leg || this.needs_a_bed(t)))
			.map((t) => t.employee);
		return missing.length ? missing : everyone;
	}

	// ------------------------------------------------------------------ saving

	can_create() {
		const t = this.state.trip;
		return !!(t.purpose && t.travel_type && t.start_date && t.end_date && this.crew().length);
	}

	problems() {
		// What the server would refuse, said before the round trip and in plain words.
		const out = [];
		const t = this.state.trip;
		if (!t.purpose) out.push(__("Say what the trip is for."));
		if (!t.start_date || !t.end_date) out.push(__("Pick the trip's first and last day."));
		if (t.start_date && t.end_date && t.end_date < t.start_date) out.push(__("The last day is before the first day."));
		this.cards("flights").forEach((card) => {
			if (!card.values.airline || !card.values.flight_number) {
				out.push(__("Every flight needs its airline and flight number."));
			}
		});
		// Time ranges: an end before its start is always a typo, and the server would
		// store it without complaint.
		const backwards = (start, end) => start && end && tp_date_part(start) && end < start;
		this.cards("flights").forEach((card) => {
			if (backwards(card.values.departure_time, card.values.arrival_time) && tp_time_part(card.values.arrival_time)) {
				out.push(__("A flight lands before it takes off."));
			}
		});
		this.cards("accommodations").forEach((card) => {
			const v = card.values;
			if (!v.hotel_lodging) out.push(__("Every room needs its hotel."));
			if (v.check_in_date && v.check_out_date && v.check_out_date < v.check_in_date) {
				out.push(__("A room checks out before it checks in."));
			}
		});
		this.cards("ground_transport").forEach((card) => {
			const v = card.values;
			if (!v.transport_type) out.push(__("Pick what kind of vehicle each drive is."));
			if (v.transport_type === "Personal Vehicle" && flt(card.mileage && card.mileage.distance) > 0) {
				if (!card.mileage.driver) out.push(__("Say who is driving the personal vehicle."));
			}
			if (tp_time_part(v.arrival_datetime) && backwards(v.pickup_datetime, v.arrival_datetime)) {
				out.push(__("A drive arrives before it leaves."));
			}
		});
		this.state.freight.forEach((item) => {
			if (!item.carrier) out.push(__("Every shipment needs its carrier."));
			if (backwards(item.pickup_from, item.pickup_to) || backwards(item.delivery_from, item.delivery_to)) {
				out.push(__("A pickup or delivery window ends before it starts."));
			}
			if (item.paid_by === "Employee" && !item.paid_by_traveler) out.push(__("Say which person paid."));
		});
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				if (!card.members.length) out.push(__("Every booking needs at least one person ticked."));
				if (card.values.paid_by === "Employee" && !card.values.paid_by_traveler) {
					out.push(__("Say which person paid."));
				}
			});
		});
		this.state.stops.forEach((stop) => {
			if (!stop.date || !stop.activity_description) out.push(__("Every stop needs a day and what is happening."));
			if (stop.time && stop.end_time && stop.end_time < stop.time) out.push(__("A stop ends before it starts."));
		});
		return Array.from(new Set(out));
	}

	payload() {
		const s = this.state;
		const trip = Object.assign({}, s.trip);
		delete trip.trip_description;
		if (s.description_changed) trip.trip_description = tp_text_to_html(s.description_text);
		// A trip not saved yet that came with notes (a copy of a past trip: adopt_copy) sends them
		// as they were written, formatting and all.
		else if (!s.name && s.trip.trip_description) trip.trip_description = s.trip.trip_description;
		const bookings = {};
		Object.keys(s.bookings).forEach((table) => {
			bookings[table] = s.bookings[table].map((card) => ({
				group: card.group,
				label: this.card_label(card),
				values: card.values,
				changed: Array.from(card.changed),
				members: card.members.map((m) =>
					Object.assign(
						{ name: m.name || null, traveler: m.traveler, ref: m.ref || "" },
						table === "accommodations" ? { guest: m.guest ? 1 : 0 } : {}
					)
				),
				mileage: card.mileage || null,
			}));
		});
		return {
			trip: trip,
			status: s.status,
			travelers: s.travelers.map((t) => ({
				employee: t.employee,
				is_trip_lead: t.is_trip_lead ? 1 : 0,
				from_date: t.from_date || "",
				to_date: t.to_date || "",
			})),
			bookings: bookings,
			freight: s.freight.map((item) => {
				const out = { name: item.name || null };
				TP_FREIGHT.forEach((field) => {
					out[field] = item[field] == null ? "" : item[field];
				});
				// Its page key, as a card sends its `group`: the stored id, or "row:<name>" /
				// "new:<n>" for one the server gives an id to (and remaps its files to).
				out.booking_group = this.freight_key(item);
				return out;
			}),
			// Every file the trip lists: an existing row by name, a new one without. The file is
			// already attached to the trip; this is the row that says what it is and whose.
			documents: (s.documents || []).map((doc) => {
				const out = { name: doc.name || null };
				TP_DOCUMENT.forEach((field) => {
					out[field] = doc[field] == null ? "" : doc[field];
				});
				if (!out.title) out.title = doc.file_name || tp_file_name(doc.file);
				return out;
			}),
			stops: tp_sorted_stops(s.stops).map((stop) => ({
				name: stop.name || null,
				date: stop.date || "",
				time: stop.time || "",
				end_time: stop.end_time || "",
				activity_description: stop.activity_description || "",
				related_party_doctype: stop.related_party_doctype || "",
				related_party_name: stop.related_party_name || "",
				location: stop.location || "",
				// Typed but never picked from Google: the server finds or makes the
				// Travel POI by this name (planner.apply_plan).
				location_text: stop.location ? "" : stop.location_text || "",
			})),
		};
	}

	save(options) {
		// Resolves true when the page is safe to move on from: saved, nothing to save, or too
		// early to save (no crew yet). Resolves false when the save was refused.
		//
		// options: `quiet` says nothing about what problems() found; `silent` also keeps the
		// server's refusal off the screen, in `refusal`, for the caller to show once it is
		// safe to (a move Back/Forward asked for — see refuse_route).
		options = options || {};
		this.refusal = null;
		if (!this.state || !this.state.can_write) return Promise.resolve(true);
		if (this.saving) return this.saving;
		if (!this.state.name && !this.can_create()) return Promise.resolve(true);
		if (!this.is_dirty() && !options.force) return Promise.resolve(true);

		const problems = this.problems();
		if (problems.length) {
			if (!options.quiet) this.say_problems(problems);
			return Promise.resolve(false);
		}

		const was_new = !this.state.name;
		const draft = this.draft_id;
		const saving_state = this.state;
		this.set_save_state("saving");
		this.saving = frappe
			.call({
				method: "erpnext_enhancements.travel_management.planner.save_plan",
				args: {
					plan: JSON.stringify(this.payload()),
					trip: this.state.name || undefined,
					modified: this.state.modified || undefined,
				},
				silent: !!options.silent,
			})
			.then(
				(r) => {
					this.saving = null;
					const fresh = r && r.message;
					if (!fresh) return false;
					if (was_new && draft) this.drafts[draft] = fresh.name;
					if (this.state !== saving_state) {
						// Saved after Back had already put the list (or another trip) on screen.
						// Nothing to draw, and a copy kept for being unsaved is saved now: Forward
						// to it loads the saved trip.
						Object.keys(this.kept).forEach((key) => {
							if (this.kept[key].state === saving_state) delete this.kept[key];
						});
						return true;
					}
					this.adopt(fresh);
					this.set_save_state("saved");
					if ((fresh.notes || []).length) {
						frappe.msgprint({ title: __("Saved, with notes"), message: fresh.notes.map(tp_esc).join("<br>") });
					}
					// The trip exists now: point the address at it, but only while this page is
					// still the one on screen — a save fired by leaving the page must not
					// rewrite the address of wherever you went — and only while the entry on
					// screen is still this new trip's own. The entry keeps its step; its older
					// "?new=1" entries find the trip through `drafts`.
					const mark = this.history_mark();
					if (
						was_new &&
						frappe.get_route()[0] === "plan-a-trip" &&
						frappe.utils.get_url_arg("new") &&
						(!mark || !mark.draft || mark.draft === draft)
					) {
						this.set_address({ trip: fresh.name, step: frappe.utils.get_url_arg("step") || "" });
					}
					return true;
				},
				(error) => {
					// The server's own message is already on screen (frappe.call shows it) —
					// unless the call was silent, which leaves it here instead: the messages
					// frappe.request.cleanup would have shown, or none for no answer at all.
					this.saving = null;
					this.set_save_state("error");
					if (options.silent) this.refusal = tp_server_messages(error);
					return false;
				}
			);
		return this.saving;
	}

	save_quietly() {
		if (this.state && this.is_dirty()) this.save({ quiet: true });
	}

	say_problems(problems) {
		frappe.msgprint({
			title: __("A few things to fill in first"),
			message: `<ul>${problems.map((p) => `<li>${tp_esc(p)}</li>`).join("")}</ul>`,
			indicator: "orange",
		});
	}

	// Why a save refused a move, said after refuse_route() has undone it: what problems()
	// finds, or what the server said (kept by a silent save), or — no answer at all — only
	// the "Not saved" the page already shows.
	say_refusal() {
		const problems = this.state ? this.problems() : [];
		if (problems.length) {
			this.say_problems(problems);
		} else if (this.refusal) {
			frappe.msgprint(this.refusal);
		}
	}

	// Every step change: the tabs, Back/Next, the checklist's Fix links — and Back/Forward
	// (`from_route`, through handle_route). A move the user made pushes a history entry once
	// it has happened; one Back/Forward asked for is already in the address.
	//
	// A move BACK is never refused: an earlier step, or wherever the phone's Back leads (a
	// later step too, when a tab jumped back from it; route_delta > 0). Holding Back up behind
	// the save of a half-finished card trapped people on the page: every press of Back showed
	// "A few things to fill in first", and each refusal overwrote the entry it was going back
	// to. It saves quietly first — the next step is drawn from the state that save returns,
	// so nothing typed is left on an object the page no longer shows — and moves, saved or not.
	//
	// A move FORWARD passes the checks and the save the Next button always did. One the
	// phone's Forward asked for and that is refused is undone by refuse_route().
	go(index, from_route) {
		if (!this.state) return;
		if (index === this.step) {
			if (from_route) {
				this.note_entry();
			} else if (this.view) {
				// A tab or a checklist "Fix" link to the step a view is drawn over: the step
				// comes back, as a new entry — Back returns to the view.
				++this.nav_seq;
				this.close_view(true);
			}
			return;
		}
		const seq = from_route ? this.nav_seq : ++this.nav_seq;
		if (index < this.step || (from_route && this.route_delta > 0)) {
			this.save({ quiet: true }).then((ok) => {
				// Back/Forward, or another tap, since this started: that move decides.
				if (seq !== this.nav_seq) return;
				this.show_step(index, !from_route);
				if (!ok) this.set_save_state("error");
			});
			return;
		}
		const delta = from_route ? this.route_delta : 0;
		const refuse = (say) => (from_route ? this.refuse_route(delta, say) : say());
		// Leaving the first step needs the basics, even before there is anything to save.
		if (this.step === 0) {
			const t = this.state.trip;
			if (!t.purpose || !t.start_date || !t.end_date || t.end_date < t.start_date) {
				refuse(() =>
					frappe.msgprint({
						title: __("The trip first"),
						message: __("Say what the trip is for and pick its first and last day."),
						indicator: "orange",
					})
				);
				return;
			}
		}
		if (index > 1 && !this.crew().length) {
			refuse(() =>
				frappe.msgprint({
					title: __("Who's going?"),
					message: __("Pick at least one person on the Who's going step first."),
					indicator: "orange",
				})
			);
			// A tap goes to where the fix is; Back/Forward has just been undone instead.
			if (!from_route && this.step !== 1) this.show_step(1, true);
			return;
		}
		this.save({ quiet: from_route, silent: from_route }).then((ok) => {
			if (seq !== this.nav_seq) return;
			if (ok) {
				this.show_step(index, !from_route);
			} else if (from_route) {
				this.refuse_route(delta, () => this.say_refusal());
			}
		});
	}

	// Draw step `index` and put it in the address: a new entry for a move made on the page,
	// the entry already there for one Back/Forward made.
	show_step(index, push) {
		this.step = Math.max(0, Math.min(TP_STEPS.length - 1, index));
		// A step is on screen now, not a view: a tab, Next or a "Fix" link tapped in a view,
		// or Back/Forward from a view's entry to another step's.
		this.view = null;
		this.view_as = "";
		this.preview = null;
		this.render();
		frappe.utils.scroll_to(0);
		this.set_address(this.address_args(), push);
	}

	// The page's own Back button: the same as the phone's. When the entry behind this one is
	// the previous step (it is whenever Next or a tab led here), go back through history
	// rather than pushing that step on top — Next, Back, Next must not leave a stack the
	// browser's Back replays — and go() saves on the way, as it does for the phone's Back.
	back_one_step() {
		if (!this.state || this.step === 0) return;
		const previous = Object.assign(this.address_args(), { step: TP_STEPS[this.step - 1].key });
		const mark = this.history_mark();
		if (!mark || mark.back !== `?${this.query_for(previous)}`) {
			this.go(this.step - 1);
			return;
		}
		window.history.back();
	}

	set_save_state(kind) {
		if (!this.save_state_el) return;
		const labels = {
			saving: __("Saving..."),
			saved: __("Saved"),
			error: __("Not saved"),
		};
		this.save_state_el.toggleClass("tp-err", kind === "error").html(`<span>${labels[kind] || ""}</span>`);
		if (kind === "saved") {
			setTimeout(() => {
				if (this.save_state_el) this.save_state_el.html("");
			}, 1600);
		}
	}

	// ------------------------------------------------------------------ gaps

	// Every gap the server sent, paperwork included: what a card or a list shows.
	gaps() {
		return (this.state && this.state.gaps) || [];
	}

	// What the checklist counts: every gap but paperwork. The step badges, "Mark as booked" and
	// a red card read these.
	counted_gaps() {
		return this.gaps().filter((g) => !tp_is_paperwork(g));
	}

	// The quieter tally (see PAPERWORK in the header).
	paperwork_gaps() {
		return this.gaps().filter(tp_is_paperwork);
	}

	// A step's counted gaps: its tab's badge. A booking's paperwork is on the same step as its
	// confirmation gap, and still never in the badge.
	gaps_for_step(key) {
		return this.counted_gaps().filter((g) => g.step === key);
	}

	gap_text(gap) {
		const who = tp_esc(gap.employee_name || "");
		const what = tp_esc(this.booking_heading(gap.group, gap.label) || __("A booking"));
		if (gap.check === "travel") {
			return gap.kind === "Outbound"
				? __("{0} has no way there yet.", [who])
				: __("{0} has no way back yet.", [who]);
		}
		if (gap.check === "lodging" && gap.kind === "nights") {
			const nights = (gap.nights || []).map((n) => tp_esc(tp_pretty_date(n))).join(", ");
			return __("{0} has nowhere to sleep on {1}.", [who, nights]);
		}
		if (gap.check === "lodging") {
			return __("{0}: the check-in and check-out days are missing.", [what]);
		}
		if (gap.check === "confirmation" && gap.table === "freight") {
			return __("{0}: no tracking, PRO or BOL number.", [what]);
		}
		if (gap.check === "confirmation") {
			const names = (gap.employee_names || []).map(tp_esc).join(", ");
			return names
				? __("{0}: no confirmation number for {1}.", [what, names])
				: __("{0}: no confirmation number.", [what]);
		}
		if (gap.check === "cost" && gap.table === "flights") {
			return __(
				"{0}: no cost entered. If it is part of a round trip paid on another flight, give it that flight's confirmation number.",
				[what]
			);
		}
		if (gap.check === "cost") {
			return __("{0}: no cost entered.", [what]);
		}
		if (gap.check === "documents") {
			return __("{0}: {1}", [what, this.paperwork_missing(gap)]);
		}
		return what;
	}

	// What a "documents" gap says is missing (completeness.document_gaps), without the booking's
	// name: the booking has its confirmation number, and its paperwork is not attached yet.
	paperwork_missing(gap) {
		if (gap.table === "flights") {
			const names = (gap.employee_names || []).map(tp_esc).join(", ");
			return names
				? __("no boarding pass or ticket for {0}.", [names])
				: __("no boarding pass or ticket attached.");
		}
		if (gap.table === "accommodations") return __("no confirmation attached.");
		if (gap.table === "ground_transport") return __("no rental agreement attached.");
		if (gap.table === "freight") return __("no bill of lading attached.");
		return __("no paperwork attached.");
	}

	// Each gap as a line. A paperwork gap is the quieter tally: a muted note (tp-paper) whose
	// link says "Attach", never the red line of a real gap.
	render_gap_list($parent, gaps, with_fix) {
		gaps.forEach((gap) => {
			const quiet = tp_is_paperwork(gap);
			const $gap = $(
				`<div class="${quiet ? "tp-paper" : "tp-gap"}"><span>${quiet ? "&#128196; " : ""}${this.gap_text(gap)}</span></div>`
			).appendTo($parent);
			if (with_fix) {
				$(`<button class="tp-btn-link">${quiet ? __("Attach") : __("Fix")} &rarr;</button>`)
					.appendTo($gap)
					.on("click", () => this.jump_to(gap.step));
			}
		});
	}

	// ------------------------------------------------------------------ rendering

	remove_chrome() {
		// The Back/Next bar and the save pill live outside `body`; render() puts them back for
		// a step. (A view, the list and "Loading..." have neither.)
		this.page.main.find(".tp-nav, .tp-savestate").remove();
		this.save_state_el = null;
	}

	render() {
		if (!this.state) return;
		// Each Google suggestion box lives on <body>, outside this page; drop the old
		// ones before their inputs are thrown away.
		(this.suggesters || []).forEach((suggester) => suggester.destroy());
		this.suggesters = [];
		// A Map view being drawn again starts its own watch on the theme; anything else needs none.
		this.stop_map_theme_watch();
		this.body.empty();
		// A view is drawn instead of the step, across the page's full width, without the
		// step tabs or the Back/Next bar: "Back to planning" is its way back.
		const view = this.view && this.state.name ? this.view : null;
		this.body.toggleClass("tp-views-wide", !!view);
		this.render_header();
		this.render_view_bar();
		if (view) {
			this.remove_chrome();
			this.render_view(view);
			return;
		}
		this.render_tabs();
		const $step = $('<div class="tp-step"></div>').appendTo(this.body);
		const step = TP_STEPS[this.step];
		if (!this.state.can_write) {
			$(`<div class="tp-gap">${__("You can look at this trip but not change it.")}</div>`).appendTo($step);
		}
		({
			trip: () => this.step_trip($step),
			crew: () => this.step_crew($step),
			there: () => this.step_legs($step, step),
			back: () => this.step_legs($step, step),
			lodging: () => this.step_lodging($step),
			around: () => this.step_legs($step, step),
			freight: () => this.step_freight($step),
			schedule: () => this.step_schedule($step),
			files: () => this.step_files($step),
			review: () => this.step_review($step),
		})[step.key]();
		this.render_nav();
	}

	render_header() {
		const s = this.state;
		const title = s.trip.purpose || __("New trip");
		const dates =
			s.trip.start_date && s.trip.end_date
				? `${tp_pretty_date(s.trip.start_date)} - ${tp_pretty_date(s.trip.end_date)}`
				: "";
		const $head = $(`<div class="tp-head">
			<h4>${tp_esc(title)}</h4>
			<span class="tp-pill ${s.status === "Booked" ? "tp-pill-green" : ""}">${tp_esc(__(s.status))}</span>
			<span class="tp-muted">${tp_esc(dates)}${s.name ? ` &middot; ${tp_esc(s.name)}` : ""}</span>
		</div>`).appendTo(this.body);
		if (s.name) {
			$(`<a class="tp-muted" style="margin-left:auto;" href="${frappe.utils.get_form_link("Travel Trip", s.name)}">${__(
				"Open the full form"
			)}</a>`).appendTo($head);
		}
	}

	// The ways to look at the whole trip, on every step of a saved trip and on each view.
	render_view_bar() {
		if (!this.state.name) return;
		const $bar = $('<div class="tp-viewbar"></div>').appendTo(this.body);
		$(`<span class="tp-muted">${__("See the whole trip:")}</span>`).appendTo($bar);
		[
			["overview", __("Overview")],
			["grid", __("Crew grid")],
			["compare", __("Side by side")],
			["map", __("Map")],
		].forEach(([key, label]) => {
			$(`<button class="tp-vbtn ${this.view === key ? "tp-active" : ""}">${tp_esc(label)}</button>`)
				.appendTo($bar)
				.on("click", () => this.open_view(key));
		});
		const crew = this.crew().filter((t) => t.employee);
		if (!crew.length) return;
		const person = this.view === "person" ? this.view_as : "";
		const $who = $(`<select class="${person ? "tp-active" : ""}" aria-label="${__("View as")}"></select>`).appendTo($bar);
		if (!person) $("<option></option>").val("").text(__("View as...")).appendTo($who);
		crew.forEach((t) => {
			$("<option></option>")
				.val(t.employee)
				.text(person ? __("View as {0}", [t.employee_name || t.employee]) : t.employee_name || t.employee)
				.appendTo($who);
		});
		$who.val(person).on("change", () => {
			const employee = $who.val();
			if (employee) this.open_view("person", employee);
		});
	}

	render_tabs() {
		const $tabs = $('<div class="tp-tabs"></div>').appendTo(this.body);
		TP_STEPS.forEach((step, index) => {
			const count = this.gaps_for_step(step.key).length;
			$(`<button class="tp-tab ${index === this.step ? "tp-active" : ""}">${tp_esc(step.title)}${
				count ? `<span class="tp-badge">${count}</span>` : ""
			}</button>`)
				.appendTo($tabs)
				.on("click", () => this.go(index));
		});
	}

	render_nav() {
		this.remove_chrome();
		const nav = $('<div class="tp-nav"></div>').appendTo(this.page.main);
		this.save_state_el = $('<div class="tp-savestate"></div>').appendTo(this.page.main);
		$(`<button>${__("Back")}</button>`)
			.appendTo(nav)
			.prop("disabled", this.step === 0)
			.on("click", () => this.back_one_step());
		if (this.step < TP_STEPS.length - 1) {
			$(`<button class="tp-primary">${__("Next")}: ${tp_esc(TP_STEPS[this.step + 1].title)}</button>`)
				.appendTo(nav)
				.on("click", () => this.go(this.step + 1));
		} else {
			$(`<button class="tp-primary">${__("Save")}</button>`)
				.appendTo(nav)
				.on("click", () => this.save().then(() => this.render()));
		}
	}

	step_intro($step, title, help) {
		$(`<div class="tp-step-title">${tp_esc(title)}</div><div class="tp-step-help">${tp_esc(help)}</div>`).appendTo(
			$step
		);
	}

	field($parent, label, required, extra_class) {
		const $field = $(`<div class="tp-field ${extra_class || ""}"><label>${tp_esc(label)}${
			required ? ' <span class="tp-req">*</span>' : ""
		}</label></div>`).appendTo($parent);
		return $field;
	}

	input($parent, type, value, on_change, attrs) {
		const $input = $(`<input type="${type}">`).val(value == null ? "" : value).appendTo($parent);
		Object.entries(attrs || {}).forEach(([key, val]) => $input.attr(key, val));
		$input.on(type === "text" || type === "number" ? "input" : "change", () => on_change($input.val()));
		return $input;
	}

	link($parent, doctype, value, on_change, placeholder) {
		// A Frappe Link control: search, and "Create a new ..." for anyone allowed to create
		// one. set_input seeds the value without firing onchange, so loading a card never
		// marks it edited.
		const $holder = $("<div></div>").appendTo($parent);
		const control = frappe.ui.form.make_control({
			parent: $holder,
			df: {
				fieldtype: "Link",
				options: doctype,
				fieldname: `tp_${doctype.replace(/\W/g, "_").toLowerCase()}_${++this.card_seq}`,
				placeholder: placeholder || "",
				onchange: () => on_change(String(control.value || "").trim()),
			},
			render_input: true,
			only_input: true,
		});
		control.refresh();
		if (value) control.set_input(value);
		return control;
	}

	place($parent, value, on_change, placeholder) {
		// A text box that suggests real places and addresses from Google as you type: the
		// same component the Address form and quick entry use
		// (global_enhancements/address_autocomplete.js, loaded on every desk page). A pick
		// fills in Google's wording, place name and address ("Hilton Las Vegas, Paradise
		// Road, Las Vegas, NV, USA") and passes the point along; typing without picking
		// is still plain text. With no Maps key, or no Places API on it, the component
		// leaves an ordinary text box and says why in the console.
		const $input = this.input($parent, "text", value, (text) => on_change(text.trim(), null), {
			autocomplete: "off",
			placeholder: placeholder || __("Start typing a place or an address"),
		});
		const lib = window.erpnext_enhancements && window.erpnext_enhancements.address_autocomplete;
		if (lib && lib.attach) {
			const suggester = lib.attach($input[0], {
				on_pick: (_values, meta) => {
					const text = meta.label || meta.formatted_address || $input.val();
					$input.val(text);
					on_change(text, meta);
				},
			});
			if (suggester) this.suggesters.push(suggester);
		}
		return $input;
	}

	time_input($parent, label, value, on_change) {
		// A Time column holds "15:00:00"; the browser's time box shows it as 3:00 PM.
		return this.input(this.field($parent, label), "time", value ? String(value).slice(0, 5) : "", (v) =>
			on_change(v ? `${v}:00` : "")
		);
	}

	time_window($parent, label, from, to, on_change) {
		// One day with a start and an end time — a delivery window, "Tue Oct 6, 8 AM – 12 PM".
		// Both ends share the day; a window that runs across days is for the full form.
		const $field = this.field($parent, label, false, "tp-wide");
		const $pair = $('<div class="tp-pair"></div>').appendTo($field);
		const $date = $('<input type="date">').val(tp_date_part(from || to)).appendTo($pair);
		const $from = $('<input type="time">').val(tp_time_part(from)).appendTo($pair);
		$(`<span class="tp-muted" style="align-self:center;">${__("to")}</span>`).appendTo($pair);
		const $to = $('<input type="time">').val(tp_time_part(to)).appendTo($pair);
		const fire = () => {
			const date = $date.val();
			on_change(tp_join_datetime(date, $from.val()), date && $to.val() ? tp_join_datetime(date, $to.val()) : "");
		};
		$date.on("change", fire);
		$from.on("change", fire);
		$to.on("change", fire);
	}

	segmented($parent, options, current, on_pick) {
		const $seg = $('<div class="tp-seg"></div>').appendTo($parent);
		options.forEach((option) => {
			$(`<button type="button" class="${option.value === current ? "tp-on" : ""}">${tp_esc(option.label)}</button>`)
				.appendTo($seg)
				.on("click", () => on_pick(option.value));
		});
		return $seg;
	}

	// ------------------------------------------------------------------ step 1: the trip

	step_trip($step) {
		const t = this.state.trip;
		this.step_intro(
			$step,
			__("The trip"),
			__("What the trip is for, when it is, and which job it belongs to.")
		);
		const $card = $('<div class="tp-card"></div>').appendTo($step);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);

		this.input(this.field($grid, __("What is the trip for?"), true, "tp-wide"), "text", t.purpose, (v) => {
			t.purpose = v;
		}).attr("placeholder", __("e.g. Install the lobby fountain at the Hilton Las Vegas"));

		const $type = this.field($grid, __("Kind of trip"), true, "tp-wide");
		this.segmented(
			$type,
			(this.lookups.travel_types || []).map((v) => ({ value: v, label: __(v) })),
			t.travel_type,
			(v) => {
				t.travel_type = v;
				this.render();
			}
		);

		this.input(this.field($grid, __("First day"), true), "date", t.start_date, (v) => {
			const end = !t.end_date || t.end_date < v ? v : t.end_date;
			this.move_trip_dates(v, end);
			$grid.find('input[data-tp="end"]').val(end);
		});
		this.input(
			this.field($grid, __("Last day"), true),
			"date",
			t.end_date,
			(v) => this.move_trip_dates(t.start_date, v),
			{ "data-tp": "end" }
		);

		const $for = this.field($grid, __("Which job is it for?"), false, "tp-wide");
		const $pair = $('<div class="tp-grid"></div>').appendTo($for);
		const $select = $("<select></select>").appendTo($('<div class="tp-field"></div>').appendTo($pair));
		[""].concat(this.lookups.travel_for_doctypes || []).forEach((doctype) => {
			$("<option></option>")
				.val(doctype)
				.text(doctype ? __(doctype) : __("Not tied to a job"))
				.appendTo($select);
		});
		$select.val(t.travel_for_doctype || "");
		const $link_holder = $('<div class="tp-field"></div>').appendTo($pair);
		const mount = () => {
			$link_holder.empty();
			if (!t.travel_for_doctype) return;
			this.link($link_holder, t.travel_for_doctype, t.travel_for_name, (v) => {
				t.travel_for_name = v;
			}, __("Search {0}", [__(t.travel_for_doctype)]));
		};
		$select.on("change", () => {
			t.travel_for_doctype = $select.val();
			t.travel_for_name = "";
			mount();
		});
		mount();

		if ((this.lookups.companies || []).length > 1) {
			const $company = $("<select></select>").appendTo(this.field($grid, __("Company"), true));
			this.lookups.companies.forEach((c) => $("<option></option>").val(c).text(c).appendTo($company));
			$company.val(t.company || this.lookups.default_company).on("change", () => {
				t.company = $company.val();
			});
		}

		const $bill = $(`<label class="tp-check tp-wide"><input type="checkbox"> ${__(
			"Bill these travel costs to the customer"
		)}</label>`).appendTo($grid);
		$bill.find("input")
			.prop("checked", !!t.billable)
			.on("change", (e) => {
				t.billable = e.target.checked ? 1 : 0;
			});

		const $notes = this.field($grid, __("Notes for the crew"), false, "tp-wide");
		$("<textarea></textarea>")
			.val(this.state.description_text || "")
			.appendTo($notes)
			.on("input", (e) => {
				this.state.description_text = e.target.value;
				this.state.description_changed = true;
			});
	}

	// ------------------------------------------------------------------ step 2: crew

	step_crew($step) {
		this.step_intro(
			$step,
			__("Who's going"),
			__(
				"Tick everyone on the trip and pick the trip lead. Change someone's dates only if they arrive late or leave early."
			)
		);
		const $search = $(`<input type="text" class="form-control" placeholder="${__("Find a person")}">`).appendTo(
			$step
		);
		const $list = $('<div style="margin-top:10px;"></div>').appendTo($step);
		const t = this.state.trip;

		const draw = () => {
			$list.empty();
			const term = ($search.val() || "").toLowerCase();
			// Alphabetical and stable: a row must not jump away from the pointer when ticked.
			(this.lookups.employees || [])
				.filter((e) => !term || `${e.employee_name} ${e.designation || ""}`.toLowerCase().includes(term))
				.forEach((employee) => {
					const traveler = this.crew().find((x) => x.employee === employee.name);
					const $row = $(`<div class="tp-crew-row ${traveler ? "tp-on" : ""}">
						<input type="checkbox" ${traveler ? "checked" : ""}>
						<div class="tp-crew-name"><b>${tp_esc(employee.employee_name || employee.name)}</b>
							<div class="tp-muted">${tp_esc(employee.designation || "")}</div>
							<div class="tp-crew-extra"></div>
						</div>
					</div>`).appendTo($list);
					$row.find('input[type="checkbox"]').on("change", (e) => {
						if (e.target.checked) {
							this.add_traveler(employee);
							draw();
							return;
						}
						// Unticking takes them off every booking and takes their files off the
						// list, and ticking them again brings neither back: asked first, the way
						// a booking's Remove asks, unless they have nothing to lose.
						const question = this.untick_question(employee.name);
						if (!question) {
							this.remove_traveler(employee.name);
							draw();
							return;
						}
						e.target.checked = true; // until they say yes
						frappe.confirm(question, () => {
							this.remove_traveler(employee.name);
							draw();
						});
					});
					if (!traveler) return;
					const $extra = $row.find(".tp-crew-extra");
					$(`<label class="tp-check"><input type="radio" name="tp-lead" ${
						traveler.is_trip_lead ? "checked" : ""
					}> ${__("Trip lead")}</label>`)
						.appendTo($extra)
						.find("input")
						.on("change", () => {
							this.crew().forEach((x) => {
								x.is_trip_lead = x.employee === employee.name ? 1 : 0;
							});
						});
					// The controller stores the trip dates on every traveler who has none of
					// their own, so "own dates" means different from the trip's, not "set".
					// `_own` holds the box open between ticking it and changing a date.
					const own = traveler._own || tp_has_own_dates(traveler, t);
					const $own = $(`<label class="tp-check"><input type="checkbox" ${own ? "checked" : ""}> ${__(
						"Different dates"
					)}</label>`).appendTo($extra);
					if (own) {
						const $from = $('<input type="date">').val(traveler.from_date || t.start_date).appendTo($extra);
						$('<span class="tp-muted">&ndash;</span>').appendTo($extra);
						const $to = $('<input type="date">').val(traveler.to_date || t.end_date).appendTo($extra);
						$from.attr({ min: t.start_date, max: t.end_date }).on("change", () => {
							traveler.from_date = $from.val();
						});
						$to.attr({ min: t.start_date, max: t.end_date }).on("change", () => {
							traveler.to_date = $to.val();
						});
					}
					$own.find("input").on("change", (e) => {
						traveler._own = e.target.checked;
						if (e.target.checked) {
							traveler.from_date = t.start_date;
							traveler.to_date = t.end_date;
						} else {
							traveler.from_date = "";
							traveler.to_date = "";
						}
						draw();
					});
				});
		};
		$search.on("input", draw);
		draw();
	}

	move_trip_dates(start, end) {
		// Travelers who were on the trip's dates follow them. Their stored dates are the old
		// trip dates (the controller fills blanks), so without this a longer trip would leave
		// the whole crew on the old days. Blank means "the trip's dates" to the server.
		const t = this.state.trip;
		this.crew().forEach((traveler) => {
			if (traveler.from_date === t.start_date) traveler.from_date = "";
			if (traveler.to_date === t.end_date) traveler.to_date = "";
		});
		t.start_date = start;
		t.end_date = end;
	}

	add_traveler(employee) {
		if (this.crew().some((x) => x.employee === employee.name)) return;
		this.state.travelers.push({
			name: null,
			employee: employee.name,
			employee_name: employee.employee_name,
			is_trip_lead: this.crew().length ? 0 : 1,
			from_date: "",
			to_date: "",
		});
		// A copy made without its crew (adopt_copy): whoever is ticked goes on every booking still
		// waiting for people, and drives a personal drive that has a distance and no driver.
		// Otherwise the first Next could never save it ("Every booking needs at least one person
		// ticked"), and the booking steps, where people are ticked, come after that save. Who is
		// on each booking is changed there. The first save replaces these cards with the server's,
		// so this ends with it.
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				if (!card.awaiting_crew) return;
				if (!card.members.some((m) => m.traveler === employee.name)) {
					card.members.push({ name: null, traveler: employee.name, ref: "" });
				}
				if (card.mileage && !card.mileage.driver && flt(card.mileage.distance) > 0) {
					card.mileage.driver = employee.name;
				}
			});
		});
	}

	// What unticking someone on Who's going would take with them, in words, or "" when there is
	// nothing: their place on each booking (their confirmation number with it), and their own
	// files. Ticking them again brings neither back.
	untick_question(employee) {
		let bookings = 0;
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				if (card.members.some((m) => m.traveler === employee)) bookings += 1;
			});
		});
		const files = this.documents().filter((doc) => doc.traveler === employee).length;
		if (!bookings && !files) return "";
		const parts = [__("Take {0} off the trip?", [tp_esc(this.crew_name(employee))])];
		if (bookings === 1) parts.push(__("They come off 1 booking, and their confirmation number on it."));
		else if (bookings) parts.push(__("They come off {0} bookings, and their confirmation numbers on them.", [bookings]));
		if (files === 1) parts.push(__("1 file for them comes off the trip's list; it stays attached to the trip on the full form."));
		else if (files) parts.push(__("{0} files for them come off the trip's list; they stay attached to the trip on the full form.", [files]));
		return parts.join(" ");
	}

	remove_traveler(employee) {
		// Take them off every booking too, or the server refuses a booking for someone who
		// is not in the crew.
		this.state.travelers = this.crew().filter((x) => x.employee !== employee);
		if (this.crew().length && !this.crew().some((x) => x.is_trip_lead)) this.crew()[0].is_trip_lead = 1;
		Object.keys(this.state.bookings).forEach((table) => {
			this.cards(table).forEach((card) => {
				card.members = card.members.filter((m) => m.traveler !== employee);
				if (card.values.paid_by_traveler === employee) {
					this.set_value(card, "paid_by", "Company");
					this.set_value(card, "paid_by_traveler", "");
				}
				if (card.mileage && card.mileage.driver === employee) card.mileage.driver = "";
			});
		});
		// A shipment they were to receive goes to the whole crew, and one they paid for back to
		// the company, as on a card: the server refuses a receiver who is not in the crew.
		(this.state.freight || []).forEach((item) => {
			if (item.traveler === employee) item.traveler = "";
			if (item.paid_by_traveler === employee) {
				item.paid_by = "Company";
				item.paid_by_traveler = "";
			}
		});
		// And their own files (a boarding pass in their name): the server refuses a file for
		// someone who is not on the trip. The files stay attached to the trip on the form.
		this.state.documents = this.documents().filter((doc) => doc.traveler !== employee);
	}

	// ------------------------------------------------------------------ steps 3, 4, 6: getting there / back / around

	step_legs($step, step) {
		const leg = step.leg;
		const help = {
			Outbound: __(
				"How each person gets to the job: flights, a company truck, their own car. Tick who is on each one. A round-trip ticket's whole fare goes here."
			),
			Return: __(
				"How each person gets home. Tick who is on each one. The flight home on a round-trip ticket has no cost of its own: give it the same confirmation number as the way there and leave the cost blank."
			),
			"During Trip": __("Rental cars, trucks and rides used while you are there. Optional."),
		}[leg];
		this.step_intro($step, step.title, help);

		if (leg !== "During Trip") {
			const $coverage = $('<div class="tp-chips" style="margin-bottom:14px;"></div>').appendTo($step);
			this.draw_leg_coverage($coverage, leg);
		}

		const cards = this.leg_cards(leg);
		cards.forEach((card) => {
			if (card.table === "flights") this.flight_card($step, card);
			else this.ride_card($step, card);
		});

		const $add = $('<div class="tp-add"></div>').appendTo($step);
		if (leg !== "During Trip") {
			$(`<button class="tp-btn">+ ${__("Add a flight")}</button>`)
				.appendTo($add)
				.on("click", () => {
					this.cards("flights").push(this.new_card("flights", leg, this.uncovered(leg)));
					this.render();
				});
			$(`<button class="tp-btn">+ ${__("Add a drive")}</button>`)
				.appendTo($add)
				.on("click", () => {
					const card = this.new_card("ground_transport", leg, this.uncovered(leg));
					card.values.transport_type = "Company Fleet";
					this.cards("ground_transport").push(card);
					this.render();
				});
		} else {
			$(`<button class="tp-btn">+ ${__("Add a rental or ride")}</button>`)
				.appendTo($add)
				.on("click", () => {
					const card = this.new_card("ground_transport", leg, this.crew().map((x) => x.employee));
					card.values.transport_type = "Rental/Third Party";
					this.cards("ground_transport").push(card);
					this.render();
				});
		}
		if (leg === "Return" && !cards.length && this.leg_cards("Outbound").length) {
			$(`<button class="tp-btn">${__("Copy the way there, reversed")}</button>`)
				.appendTo($add)
				.on("click", () => {
					this.copy_reversed();
					this.render();
				});
		}

		// Per-booking gaps are already on their cards; the step keeps the per-person ones.
		this.render_gap_list(
			$step,
			this.gaps_for_step(step.key).filter((g) => !g.group),
			false
		);
	}

	draw_leg_coverage($coverage, leg) {
		$coverage.empty();
		const covered = new Set();
		this.leg_cards(leg).forEach((card) => card.members.forEach((m) => covered.add(m.traveler)));
		this.crew().forEach((t) => {
			const ok = covered.has(t.employee);
			$(`<span class="tp-chip ${ok ? "tp-ok" : "tp-miss"}">${ok ? "&#10003;" : "&#10007;"} ${tp_esc(
				t.employee_name || t.employee
			)}</span>`).appendTo($coverage);
		});
	}

	copy_reversed() {
		this.leg_cards("Outbound").forEach((out) => {
			const members = out.members.map((m) => m.traveler);
			const card = this.new_card(out.table, "Return", members);
			if (out.table === "flights") {
				card.values.airline = out.values.airline;
				card.values.departure_airport = out.values.arrival_airport;
				card.values.arrival_airport = out.values.departure_airport;
				// A round trip is one ticket: the way home carries the way there's confirmation
				// number and no cost, so the checklist finds its fare on the way there.
				card.members.forEach((member) => {
					const source = out.members.find((m) => m.traveler === member.traveler);
					member.ref = source ? source.ref || "" : "";
				});
				card.same_ref = out.same_ref;
			} else {
				card.values.transport_type = out.values.transport_type;
				card.values.supplier = out.values.supplier;
				card.values.vehicle = out.values.vehicle;
				card.values.pickup_location = out.values.dropoff_location;
				card.values.dropoff_location = out.values.pickup_location;
				if (out.mileage) card.mileage = Object.assign({}, out.mileage, { claimed: false });
			}
			card.values.paid_by = out.values.paid_by;
			card.values.paid_by_traveler = out.values.paid_by_traveler;
			if (this.state.trip.end_date) {
				const field = out.table === "flights" ? "departure_time" : "pickup_datetime";
				card.values[field] = tp_join_datetime(this.state.trip.end_date, "");
			}
			this.cards(out.table).push(card);
		});
	}

	card_shell($parent, card, title) {
		// Red for a real gap only: missing paperwork is a quiet note on the card (card_gaps).
		const bad = this.counted_gaps().some((g) => g.group === card.group);
		const $card = $(`<div class="tp-card ${bad ? "tp-bad" : ""}">
			<div class="tp-card-head"><b>${tp_esc(title)}</b></div>
		</div>`).appendTo($parent);
		if (!card.protected) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($card.find(".tp-card-head"))
				.on("click", () => {
					const files = this.documents_for(card.group).length;
					const question = files
						? __("Remove this booking for everyone on it? Its files come off the trip's list with it; they stay attached to the trip on the full form.")
						: __("Remove this booking for everyone on it?");
					frappe.confirm(question, () => {
						const list = this.cards(card.table);
						list.splice(list.indexOf(card), 1);
						// A file must name a booking the trip has (planner.merge_documents).
						this.state.documents = this.documents().filter((doc) => doc.booking_group !== card.group);
						this.render();
					});
				});
		} else {
			$(`<span class="tp-muted" style="margin-left:auto;">${__("On an Expense Claim")}</span>`).appendTo(
				$card.find(".tp-card-head")
			);
		}
		return $card;
	}

	card_label(card) {
		const v = card.values;
		if (card.table === "flights") return [v.airline, v.flight_number].filter(Boolean).join(" ") || __("Flight");
		if (card.table === "accommodations") return v.hotel_lodging || __("Room");
		const type = TP_RIDE_TYPES.find((x) => x.value === v.transport_type);
		return v.supplier || v.vehicle || (type ? type.label : __("Drive"));
	}

	freight_label(item) {
		return [item.carrier, item.tracking_number].filter(Boolean).join(" ") || __("Shipment");
	}

	// What each booking is called in a list ({page key: {label, heading}}), where two bookings
	// can have the same name: the policy is one adult to a room, so two rooms at one hotel are two
	// bookings named after it. Only where names are alike, a heading adds who is on the booking
	// ("Harborview Suites · Ana"), then its dates if that still leaves two alike (one person's
	// split stay) — as /itinerary's bookingHeadings does on its Documents screen. Only the words
	// change: a booking is still found by its key (card.group, freight_key), never by its name.
	booking_headings() {
		if (!this.state) return {};
		const entries = [];
		const people_words = (card) =>
			card.members.some((m) => !m.traveler)
				? __("Whole crew")
				: this.card_people(card)
						.map((employee) => this.crew_name(employee))
						.join(", ");
		["flights", "accommodations", "ground_transport"].forEach((table) => {
			this.cards(table).forEach((card) => {
				const v = card.values;
				entries.push({
					key: card.group,
					label: this.card_label(card),
					who: people_words(card),
					dates: {
						flights: [tp_date_part(v.departure_time)],
						accommodations: [v.check_in_date, v.check_out_date],
						ground_transport: [tp_date_part(v.pickup_datetime), tp_date_part(v.return_datetime)],
					}[table],
				});
			});
		});
		(this.state.freight || []).forEach((item) => {
			const key = this.freight_key(item);
			if (!key) return;
			entries.push({
				key: key,
				label: this.freight_label(item),
				who: item.traveler ? this.crew_name(item.traveler) : __("Whole crew"),
				dates: [tp_date_part(item.pickup_from), tp_date_part(item.delivery_from || item.delivery_to)],
			});
		});
		entries.forEach((entry) => {
			entry.heading = entry.label;
		});
		const tell = (describe) => {
			const count = {};
			entries.forEach((entry) => {
				count[entry.heading] = (count[entry.heading] || 0) + 1;
			});
			entries.forEach((entry) => {
				const more = count[entry.heading] > 1 ? describe(entry) : "";
				if (more) entry.heading += ` · ${more}`;
			});
		};
		tell((entry) => entry.who);
		tell((entry) => {
			const days = entry.dates.filter(Boolean).map(tp_pretty_date);
			return tp_span(days[0] || "", days[1] || "");
		});
		const out = {};
		entries.forEach((entry) => {
			out[entry.key] = { label: entry.label, heading: entry.heading };
		});
		return out;
	}

	// A booking's words in a list: `label` (the server's, on a gap) or its name on the page, plus
	// what tells it from a booking of the same name (booking_headings). A key the page does not
	// have keeps `label` as it is.
	booking_heading(key, label) {
		const entry = key ? this.booking_headings()[key] : null;
		if (!entry) return label || "";
		return `${label || entry.label}${entry.heading.slice(entry.label.length)}`;
	}

	datetime_pair($parent, label, value, on_change, required) {
		const $field = this.field($parent, label, required);
		const $pair = $('<div class="tp-pair"></div>').appendTo($field);
		const $date = $('<input type="date">').val(tp_date_part(value)).appendTo($pair);
		const $time = $('<input type="time">').val(tp_time_part(value)).appendTo($pair);
		const fire = () => on_change(tp_join_datetime($date.val(), $time.val()));
		$date.on("change", fire);
		$time.on("change", fire);
	}

	flight_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, `${__("Flight")}: ${this.card_label(card)}`);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Airline"), true),
			"Supplier",
			v.airline,
			(val) => this.set_value(card, "airline", val),
			__("e.g. Southwest Airlines")
		);
		this.input(this.field($grid, __("Flight number"), true), "text", v.flight_number, (val) =>
			this.set_value(card, "flight_number", val.trim())
		).attr("placeholder", "WN 1234");
		this.input(this.field($grid, __("From (airport)")), "text", v.departure_airport, (val) =>
			this.set_value(card, "departure_airport", val.trim())
		).attr("placeholder", "PHX");
		this.input(this.field($grid, __("To (airport)")), "text", v.arrival_airport, (val) =>
			this.set_value(card, "arrival_airport", val.trim())
		).attr("placeholder", "LAS");
		this.datetime_pair($grid, __("Takes off"), v.departure_time, (val) => this.set_value(card, "departure_time", val));
		this.datetime_pair($grid, __("Lands"), v.arrival_time, (val) => this.set_value(card, "arrival_time", val));
		this.members_block($card, card, __("Who's on this flight?"));
		this.refs_block($card, card);
		this.paperwork_block($card, this.booking_target(card));
		this.money_block($card, card, __("Total for all tickets"));
		const fare = this.fare_card(card);
		if (fare) {
			$(`<div class="tp-ok-line">&#10003; ${tp_esc(
				__("Round trip: paid with the ticket for {0}. Add a cost here only if this flight cost extra, like a fare upgrade.", [
					this.flight_words(fare),
				])
			)}</div>`).appendTo($card);
		}
		this.card_gaps($card, card);
	}

	fare_card(card) {
		// The flight whose fare this one rides on, or null — completeness.on_another_ticket. A
		// round-trip ticket is one charge, so the way home has no cost of its own: a flight with
		// no cost where everyone on it has a confirmation number that is on another flight WITH
		// a cost is covered by that fare.
		if (card.table !== "flights" || flt(card.values.cost)) return null;
		const pnr = (ref) => String(ref || "").trim().toUpperCase();
		const refs = card.members.map((m) => pnr(m.ref));
		if (!refs.length || refs.some((ref) => !ref)) return null;
		const paid = this.cards("flights").filter((c) => c !== card && flt(c.values.cost));
		let fare = null;
		for (const ref of refs) {
			const found = paid.find((c) => c.members.some((m) => pnr(m.ref) === ref));
			if (!found) return null;
			fare = fare || found;
		}
		return fare;
	}

	flight_words(card) {
		// "SLC → San Diego on Sun, Sep 27", or the airline and number when the airports are blank.
		const v = card.values;
		const route = v.departure_airport && v.arrival_airport ? `${v.departure_airport} → ${v.arrival_airport}` : this.card_label(card);
		const day = tp_pretty_date(tp_date_part(v.departure_time));
		return day ? __("{0} on {1}", [route, day]) : route;
	}

	ride_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, this.card_label(card));
		const $type = this.field($card, __("What kind?"), true);
		this.segmented($type, TP_RIDE_TYPES, v.transport_type, (value) => {
			this.set_value(card, "transport_type", value);
			if (value === "Company Fleet") {
				this.set_value(card, "paid_by", "Company");
				this.set_value(card, "paid_by_traveler", "");
			}
			this.render();
		});
		const $grid = $('<div class="tp-grid" style="margin-top:10px;"></div>').appendTo($card);
		const unbooked = TP_UNBOOKED.includes(v.transport_type);
		if (v.transport_type === "Company Fleet") {
			this.link(
				this.field($grid, __("Vehicle (optional)")),
				"Vehicle",
				v.vehicle,
				(val) => this.set_value(card, "vehicle", val)
			);
		} else if (!unbooked) {
			this.link(
				this.field($grid, v.transport_type === "Taxi/Rideshare" ? __("Company") : __("Rental company")),
				"Supplier",
				v.supplier,
				(val) => this.set_value(card, "supplier", val),
				v.transport_type === "Taxi/Rideshare" ? "Uber" : "Enterprise"
			);
		}
		this.place(this.field($grid, v.leg === "During Trip" ? __("Pick up at") : __("Leaving from")), v.pickup_location, (val) =>
			this.set_value(card, "pickup_location", val)
		);
		this.place(this.field($grid, v.leg === "During Trip" ? __("Drop off at") : __("Going to")), v.dropoff_location, (val) =>
			this.set_value(card, "dropoff_location", val)
		);
		this.datetime_pair($grid, v.leg === "During Trip" ? __("Pick up") : __("Leaves"), v.pickup_datetime, (val) =>
			this.set_value(card, "pickup_datetime", val)
		);
		if (v.transport_type === "Company Fleet" || v.transport_type === "Personal Vehicle") {
			// A drive's time range: when it leaves and when it gets there.
			this.datetime_pair($grid, __("Arrives"), v.arrival_datetime, (val) => this.set_value(card, "arrival_datetime", val));
		}
		if (!unbooked) {
			this.datetime_pair($grid, __("Return by"), v.return_datetime, (val) =>
				this.set_value(card, "return_datetime", val)
			);
		}
		if (v.transport_type !== "Taxi/Rideshare") {
			// Our own load: the fountain on the trailer, a rented box truck of materials.
			// Carrier shipments are on the Freight step instead.
			const $haul = this.field($grid, __("Hauling (optional)"), false, "tp-wide");
			$("<textarea rows=\"2\"></textarea>")
				.val(v.cargo || "")
				.attr("placeholder", __("What's on the truck or trailer, e.g. 12 ft basin, pump vault, tools"))
				.appendTo($haul)
				.on("input", (e) => this.set_value(card, "cargo", e.target.value));
		}
		this.members_block($card, card, __("Who's riding?"));
		if (v.transport_type === "Personal Vehicle") this.mileage_block($card, card);
		if (!unbooked) this.refs_block($card, card);
		// On every ride: a company truck's or a personal car's paperwork is welcome, it is just
		// never asked for (completeness.document_gaps checks a rental only).
		this.paperwork_block($card, this.booking_target(card));
		if (!unbooked) this.money_block($card, card, __("Total cost"));
		this.card_gaps($card, card);
	}

	members_block($card, card, label) {
		const $field = this.field($card, label, true);
		$field.css("margin-top", "12px");
		const $chips = $('<div class="tp-chips"></div>').appendTo($field);
		const on = new Set(card.members.map((m) => m.traveler));
		const whole_crew = card.members.some((m) => !m.traveler);
		if (whole_crew) {
			$(`<div class="tp-muted">${__(
				"Entered on the form for the whole crew. Tick people to give each of them their own booking."
			)}</div>`).appendTo($field);
		}
		this.crew().forEach((t) => {
			const checked = whole_crew || on.has(t.employee);
			$(`<span class="tp-chip ${checked ? "tp-on" : ""}">${checked ? "&#10003; " : ""}${tp_esc(
				t.employee_name || t.employee
			)}</span>`)
				.appendTo($chips)
				.on("click", () => {
					if (card.protected) return;
					if (whole_crew) {
						// Leaving "whole crew": everyone currently implied, minus this person.
						const ref = card.members[0] ? card.members[0].ref : "";
						card.members = this.crew()
							.filter((x) => x.employee !== t.employee)
							.map((x) => ({ name: null, traveler: x.employee, ref: ref }));
					} else if (on.has(t.employee)) {
						card.members = card.members.filter((m) => m.traveler !== t.employee);
					} else {
						const ref = card.same_ref && card.members[0] ? card.members[0].ref : "";
						card.members.push({ name: null, traveler: t.employee, ref: ref });
					}
					this.render();
				});
		});
	}

	refs_block($card, card) {
		const $field = this.field($card, __("Confirmation number"));
		$field.css("margin-top", "12px");
		if (card.members.length > 1) {
			$(`<label class="tp-check"><input type="checkbox" ${card.same_ref ? "checked" : ""}> ${__(
				"Same for everyone"
			)}</label>`)
				.appendTo($field)
				.find("input")
				.on("change", (e) => {
					card.same_ref = e.target.checked;
					if (card.same_ref) {
						const first = (card.members.find((m) => m.ref) || {}).ref || "";
						card.members.forEach((m) => {
							m.ref = first;
						});
					}
					this.render();
				});
		}
		if (card.same_ref || card.members.length <= 1) {
			const first = card.members[0] ? card.members[0].ref : "";
			this.input($field, "text", first, (val) => {
				card.members.forEach((m) => {
					m.ref = val.trim();
				});
			});
			return;
		}
		const $refs = $('<div class="tp-refs"></div>').appendTo($field);
		card.members.forEach((member) => {
			const $row = $(`<div class="tp-ref-row"><span>${tp_esc(this.crew_name(member.traveler))}</span></div>`).appendTo(
				$refs
			);
			$('<input type="text">')
				.val(member.ref || "")
				.appendTo($row)
				.on("input", (e) => {
					member.ref = e.target.value.trim();
				});
		});
	}

	money_block($card, card, cost_label) {
		const v = card.values;
		const $grid = $('<div class="tp-grid" style="margin-top:12px;"></div>').appendTo($card);
		const $cost = this.field($grid, cost_label);
		const $hint = $('<div class="tp-muted"></div>');
		const hint = () => {
			// A room's guests pay no share (planner.merge_bookings); a room of guests only
			// splits as usual.
			const payers = card.members.filter((m) => !m.guest);
			const n = payers.length || card.members.length;
			const free = payers.length ? card.members.filter((m) => m.guest && m.traveler) : [];
			const parts = [];
			if (n > 1 && flt(v.cost)) parts.push(__("About {0} each", [format_currency(flt(v.cost) / n, this.lookups.currency)]));
			if (free.length) parts.push(__("Staying free: {0}", [free.map((m) => this.crew_name(m.traveler)).join(", ")]));
			$hint.text(parts.join(" · "));
		};
		this.input($cost, "number", v.cost || "", (val) => {
			this.set_value(card, "cost", flt(val));
			hint();
		}, { min: "0", step: "0.01", inputmode: "decimal" });
		$hint.appendTo($cost);
		hint();
		const $paid = $("<select></select>").appendTo(this.field($grid, __("Who paid?")));
		$("<option></option>").val("Company").text(__("The company")).appendTo($paid);
		this.crew().forEach((t) => {
			$("<option></option>")
				.val(t.employee)
				.text(__("{0} (to reimburse)", [t.employee_name || t.employee]))
				.appendTo($paid);
		});
		$paid.val(v.paid_by === "Employee" && v.paid_by_traveler ? v.paid_by_traveler : "Company").on("change", () => {
			const who = $paid.val();
			this.set_value(card, "paid_by", who === "Company" ? "Company" : "Employee");
			this.set_value(card, "paid_by_traveler", who === "Company" ? "" : who);
		});
	}

	mileage_block($card, card) {
		card.mileage = card.mileage || { driver: "", distance: 0 };
		const m = card.mileage;
		const $grid = $('<div class="tp-grid" style="margin-top:12px;"></div>').appendTo($card);
		const $driver = $("<select></select>").appendTo(this.field($grid, __("Who's driving?")));
		$("<option></option>").val("").text(__("Pick the driver")).appendTo($driver);
		card.members.forEach((member) => {
			if (!member.traveler) return;
			$("<option></option>").val(member.traveler).text(this.crew_name(member.traveler)).appendTo($driver);
		});
		$driver.val(m.driver || "").prop("disabled", !!m.claimed).on("change", () => {
			m.driver = $driver.val();
		});
		const $miles = this.field($grid, __("Miles for this drive"));
		const $note = $('<div class="tp-muted"></div>');
		const note = () => {
			const rate = flt(this.lookups.mileage_rate);
			$note.text(
				rate && flt(m.distance)
					? __("Reimbursed at {0} a mile: {1}", [
							format_currency(rate, this.lookups.currency),
							format_currency(rate * flt(m.distance), this.lookups.currency),
					  ])
					: ""
			);
		};
		this.input($miles, "number", m.distance || "", (val) => {
			m.distance = flt(val);
			note();
		}, { min: "0", step: "1", inputmode: "decimal" }).prop("disabled", !!m.claimed);
		$note.appendTo($miles);
		note();
	}

	// The booking's gaps on its card: a real gap red, its paperwork a muted note (render_gap_list).
	card_gaps($card, card) {
		this.render_gap_list(
			$card,
			this.gaps().filter((g) => g.group === card.group),
			false
		);
	}

	// ------------------------------------------------------------------ paperwork
	//
	// Where a file goes, for paperwork_block and the uploader (a "target"):
	//   key(state)  the booking's page key now (card.group, freight_key), "" for the whole
	//               trip, or null once the booking is gone (removed, or redrawn by a save that
	//               finished while a file was uploading — a save hands the page new cards).
	//               `state` is the trip on screen unless given (a trip kept aside: add_document);
	//   label()     the booking's name, as the server will store it (booking_label);
	//   kind        what a file attached here starts as;
	//   for_whom(state)  whom a file attached here starts as being for ("" = everyone), when
	//               that is not everyone on it: a flight's boarding pass (flight_pass_for);
	//   people()    whom a file here can be for, one at a time;
	//   everyone    the "for everyone" choice, in the words of this booking or the trip;
	//   need        what the checklist asks for here (completeness.document_gaps), or "".

	booking_target(card) {
		const rental = card.values.transport_type === "Rental/Third Party";
		const needs = {
			flights: __("The checklist asks for each person's boarding pass or ticket once the flight has a confirmation number."),
			accommodations: __("The checklist asks for the booking confirmation once the room has a confirmation number."),
			ground_transport: rental
				? __("The checklist asks for the rental agreement once the rental has a confirmation number.")
				: "",
		};
		// This booking on `state`: the card itself, or — redrawn by a save while a file uploaded
		// — the card with its id. One that was "new:<n>" has been given another, and is gone.
		const find = (state) => {
			state = state || this.state;
			if (!state) return null;
			const list = state.bookings[card.table] || [];
			if (list.includes(card)) return card;
			return list.find((other) => other.group === card.group) || null;
		};
		return {
			key: (state) => {
				const found = find(state);
				return found ? found.group : null;
			},
			label: () => this.card_label(card),
			// A company truck or a personal car has no rental agreement.
			kind: card.table === "ground_transport" && !rental ? "Other" : TP_DOC_DEFAULT_KIND[card.table],
			for_whom: card.table === "flights" ? (state) => this.flight_pass_for(find(state), state || this.state) : null,
			people: () => this.card_people(card),
			everyone: __("Everyone on this booking"),
			everyone_short: __("everyone on this booking"),
			need: needs[card.table] || "",
		};
	}

	// Whom a file attached to a flight starts as being for. A boarding pass is one person's:
	// uploaded as everyone's, one person's pass cleared the whole flight's paperwork and showed
	// on everyone's itinerary as theirs (completeness counts a pass for nobody in particular
	// only on a one-person flight). So: the flight's one person, or the first on it with no
	// boarding pass or ticket of their own yet — "upload everyone's passes" fills them in card
	// order — and everyone once each has theirs. "Who is it for?" under the file changes it.
	flight_pass_for(card, state) {
		if (!card || !state) return "";
		const people = this.card_people(card, state);
		if (people.length === 1) return people[0];
		const has = new Set(
			(state.documents || [])
				.filter((doc) => (doc.booking_group || "") === card.group && doc.traveler)
				.filter((doc) => doc.kind === "Boarding pass" || doc.kind === "Booking confirmation")
				.map((doc) => doc.traveler)
		);
		return people.find((employee) => !has.has(employee)) || "";
	}

	freight_target(item) {
		return {
			key: (state) => {
				state = state || this.state;
				if (!state) return null;
				const freight = state.freight || [];
				const key = this.freight_key(item);
				if (freight.includes(item)) return key || null;
				// Redrawn by a save while the file uploaded: the same shipment, by its id.
				return key && freight.some((other) => this.freight_key(other) === key) ? key : null;
			},
			label: () => [item.carrier, item.tracking_number].filter(Boolean).join(" ") || __("Shipment"),
			kind: TP_DOC_DEFAULT_KIND.freight,
			people: () => this.crew().map((t) => t.employee),
			everyone: __("Everyone who sees this shipment"),
			everyone_short: __("everyone who sees this shipment"),
			need: __("The checklist asks for the bill of lading once the shipment has a tracking number."),
		};
	}

	trip_target() {
		return {
			key: () => "",
			label: () => "",
			kind: "Other",
			people: () => this.crew().map((t) => t.employee),
			everyone: __("Whole crew"),
			everyone_short: __("the whole crew"),
			need: __("A site map, the safety plan, an insurance certificate, the job packet: for the whole crew or one person."),
		};
	}

	// A booking's files (or the trip's own), each with what it is and who it is for, editable in
	// place, and "Attach a file" once the trip is saved. `heading`: false where the card around
	// it already says what these are (the Files step).
	paperwork_block($parent, target, heading) {
		const $box = $('<div class="tp-docs"></div>').appendTo($parent);
		const draw = () => {
			$box.empty();
			const key = target.key();
			const docs = key === null ? [] : this.documents_for(key);
			const $head = $(
				`<div class="tp-docs-head">${heading === false ? "" : `<label>${__("Paperwork")}</label>`}</div>`
			).appendTo($box);
			if (target.need) $(`<span class="tp-muted">${tp_esc(target.need)}</span>`).appendTo($head);
			docs.forEach((doc) => this.doc_row($box, doc, target, draw));
			if (!this.state.can_write) {
				if (!docs.length) $(`<div class="tp-muted">${__("No files yet.")}</div>`).appendTo($box);
				return;
			}
			if (!this.state.name) {
				$(`<div class="tp-muted">${__("Save the trip first to attach files.")}</div>`).appendTo($box);
				return;
			}
			$(`<button class="tp-btn">&#128206; ${__("Attach a file")}</button>`)
				.appendTo($box)
				.on("click", () => this.attach_files(target));
		};
		draw();
		return $box;
	}

	// One file: its icon, name, who it is for and an Open link; and, for anyone who may change
	// the trip, what it is, who it is for, and a way to take it off the list.
	doc_row($parent, doc, target, redraw) {
		const kind = TP_DOC_KINDS.includes(doc.kind) ? doc.kind : "Other";
		const title = doc.title || doc.file_name || tp_file_name(doc.file) || __("File");
		const url = tp_file_url(doc.file);
		const who = doc.traveler ? __("for {0}", [this.crew_name(doc.traveler)]) : target.everyone_short;
		// Who it is for is said in words only where it cannot be changed: otherwise the "Who is
		// it for?" menu under it says it.
		const $row = $(`<div class="tp-doc">
			<div class="tp-doc-line">
				<span class="tp-doc-icon" title="${tp_esc(__(kind))}">${TP_DOC_ICONS[kind]}</span>
				<span class="tp-doc-title" title="${tp_esc(title)}">${tp_esc(title)}${
					this.state.can_write ? "" : `<span class="tp-muted">${tp_esc(who)}</span>`
				}</span>
				${url ? `<a class="tp-doc-open" target="_blank" rel="noopener" href="${tp_esc(url)}">${__("Open")} &#8599;</a>` : ""}
			</div>
		</div>`).appendTo($parent);
		if (!this.state.can_write) return;
		$(`<button class="tp-doc-x" title="${__("Take this file off")}" aria-label="${__("Take this file off")}">&#10005;</button>`)
			.appendTo($row.find(".tp-doc-line"))
			.on("click", () => {
				frappe.confirm(
					__("Take {0} off the trip's files? It stays attached to the trip on the full form.", [tp_esc(title)]),
					() => {
						const list = this.documents();
						const at = list.indexOf(doc);
						if (at >= 0) list.splice(at, 1);
						redraw();
					}
				);
			});
		const $edit = $('<div class="tp-doc-edit"></div>').appendTo($row);
		const $kind = $(`<select aria-label="${__("What is it?")}"></select>`).appendTo($edit);
		TP_DOC_KINDS.forEach((value) => $("<option></option>").val(value).text(__(value)).appendTo($kind));
		$kind.val(kind).on("change", () => {
			doc.kind = $kind.val();
			redraw();
		});
		const $for = $(`<select aria-label="${__("Who is it for?")}"></select>`).appendTo($edit);
		$("<option></option>").val("").text(target.everyone).appendTo($for);
		const people = target.people();
		people.forEach((employee) => {
			$("<option></option>").val(employee).text(__("Only {0}", [this.crew_name(employee)])).appendTo($for);
		});
		if (doc.traveler && !people.includes(doc.traveler)) {
			// Someone taken off the crew on the full form is not on the trip at all; the next
			// save takes their file off the list, and says so (planner.merge_documents).
			const on_trip = this.crew().some((t) => t.employee === doc.traveler);
			$("<option></option>")
				.val(doc.traveler)
				.text(
					on_trip
						? __("Only {0} (not on this booking)", [this.crew_name(doc.traveler)])
						: __("Only {0} (not on the trip)", [this.crew_name(doc.traveler)])
				)
				.appendTo($for);
		}
		$for.val(doc.traveler || "").on("change", () => {
			doc.traveler = $for.val();
			redraw();
		});
	}

	// "Attach a file": frappe's own uploader onto the saved trip — a File is attached to a trip
	// by its name, so a trip not saved yet has no button. Always private: frappe offers a
	// "Private" box per file and "Set all public" unless told not to, and a public File is
	// served to anyone, signed in or not, at /files/<the name it was uploaded with> — a
	// boarding pass is a name and a PNR (the server refuses one too: planner.merge_documents).
	// No web link: a trip's files are uploads. Opened only from that button: nothing on the way
	// to a step touches frappe.ui, and the Back/Forward harness, like a page that has not loaded
	// the uploader yet, has none.
	attach_files(target) {
		const s = this.state;
		if (!s || !s.name || !s.can_write) return;
		const trip = s.name;
		const open = () => {
			new frappe.ui.FileUploader({
				doctype: "Travel Trip",
				docname: trip,
				folder: "Home/Attachments",
				make_attachments_public: false,
				allow_toggle_private: false,
				allow_web_link: false,
				allow_multiple: true,
				on_success: (file) => this.add_document(trip, target, file),
			});
		};
		if (frappe.ui.FileUploader) open();
		else frappe.require("file_uploader.bundle.js", open);
	}

	// One uploaded file (the uploader calls this once per file, whenever each one lands): a new
	// file on the list, on the booking it was attached from, and for whom that booking's
	// paperwork is still missing (target.for_whom; everyone on it otherwise). It is saved with
	// the next save, like any other edit; the File itself is already on the trip.
	//
	// An upload keeps going when its dialog is closed — by the phone's Back (frappe closes any
	// dialog on a route change), by its X, or a tap outside it — so a file can land at any
	// moment, and never where it would be lost:
	// - during a save: that save sent the page before this file was on it, and its answer
	//   replaces the page's trip, so the file waits for the answer and goes on the trip it
	//   brings (a booking that had its id keeps it; one that was "new:<n>" has another now,
	//   and the file goes with the whole trip's, with a note);
	// - while its trip is loading again (Back onto the page from the form): it waits for it;
	// - on a trip put aside unsaved (keep_current): it goes on that trip, and "Carry on"
	//   brings it back with the rest of its changes.
	// Only a trip that is no longer on the page at all gets "no longer open here": the File is
	// attached to it, on its full form, but not on its list.
	add_document(trip, target, file) {
		if (!file || !file.file_url) return;
		if (this.saving) {
			this.saving.then(() => this.add_document(trip, target, file));
			return;
		}
		const file_name = file.file_name || tp_file_name(file.file_url);
		const s = this.state;
		if (s && s.name === trip) {
			this.push_document(s, target, file, file_name, true);
			if (!this.view) this.render();
			return;
		}
		const kept = this.kept[trip];
		if (kept) {
			this.push_document(kept.state, target, file, file_name, false);
			frappe.show_alert({
				message: __("{0} is on {1}'s files, with its changes that are not saved yet.", [
					tp_esc(file_name),
					tp_esc(kept.state.trip.purpose || trip),
				]),
				indicator: "blue",
			});
			return;
		}
		if (!s && this.loading && this.loading.name === trip) {
			this.loading.promise.then(() => this.add_document(trip, target, file));
			return;
		}
		// Another trip, or the list, came on screen while it uploaded.
		frappe.show_alert({
			message: __("{0} is attached to {1}, which is no longer open here.", [tp_esc(file_name), tp_esc(trip)]),
			indicator: "orange",
		});
	}

	// add_document's row, on `state` (the trip on screen, or one kept aside): on its booking
	// when `state` still has it, else with the whole trip's files, said out loud on screen.
	push_document(state, target, file, file_name, on_screen) {
		let key = target.key(state);
		if (key === null) {
			key = "";
			if (on_screen) {
				frappe.show_alert({
					message: __("{0} is with the whole trip's files: the booking changed while it uploaded.", [tp_esc(file_name)]),
					indicator: "orange",
				});
			}
		}
		state.documents = state.documents || [];
		state.documents.push({
			name: null,
			title: file_name,
			kind: target.kind,
			file: file.file_url,
			traveler: key && target.for_whom ? target.for_whom(state) : "",
			booking_group: key,
			booking_label: key ? target.label() : "",
			file_name: file_name,
			is_image: tp_is_image(file_name) ? 1 : 0,
		});
	}

	// Every booking the page has, with where it is planned: the Files step lists their files,
	// each under its name, told apart from another booking of the same name (booking_heading).
	booking_list() {
		const icons = { flights: "&#9992;", accommodations: "&#127976;", ground_transport: "&#128663;" };
		const out = [];
		["flights", "accommodations", "ground_transport"].forEach((table) => {
			this.cards(table).forEach((card) => {
				out.push({
					key: card.group,
					label: this.booking_heading(card.group) || this.card_label(card),
					icon: icons[table],
					step: table === "accommodations" ? "lodging" : TP_LEG_STEP[card.values.leg] || "around",
					target: this.booking_target(card),
				});
			});
		});
		this.state.freight.forEach((item) => {
			const key = this.freight_key(item);
			if (!key) return;
			out.push({
				key: key,
				label: this.booking_heading(key) || this.freight_label(item),
				icon: "&#128230;",
				step: "freight",
				target: this.freight_target(item),
			});
		});
		return out;
	}

	// A file whose booking the trip no longer has (deleted on the form) is listed with the whole
	// trip's files, which is where the next save stores it (planner.merge_documents). get_state
	// already sends it that way; this is for an answer that does not, since a file sent naming
	// a booking the trip does not have can be refused, and that would stop every later save of
	// the trip, not only this file's. Who it is for stays.
	rehome_documents(state) {
		const keys = new Set();
		Object.keys(state.bookings).forEach((table) => state.bookings[table].forEach((card) => keys.add(card.group)));
		(state.freight || []).forEach((item) => {
			const key = this.freight_key(item);
			if (key) keys.add(key);
		});
		state.documents.forEach((doc) => {
			if (doc.booking_group && !keys.has(doc.booking_group)) {
				doc.booking_group = "";
				doc.booking_label = "";
			}
		});
	}

	// ------------------------------------------------------------------ step 5: lodging

	step_lodging($step) {
		this.step_intro(
			$step,
			__("Where everyone sleeps"),
			__(
				"Add each room and tick who is in it, including anyone sharing it for free. The grid shows who still needs a bed on which night, going by their flights and drives: someone out and back on the same day needs none."
			)
		);
		const $matrix = $('<div class="tp-matrix"></div>').appendTo($step);
		this.draw_nights($matrix);

		this.cards("accommodations").forEach((card) => this.room_card($step, card));

		const $add = $('<div class="tp-add"></div>').appendTo($step);
		$(`<button class="tp-btn">+ ${__("Add a room")}</button>`)
			.appendTo($add)
			.on("click", () => {
				const last = this.cards("accommodations").slice(-1)[0];
				const card = this.new_card("accommodations", null, this.uncovered(null));
				// A second room is usually at the same hotel on the same nights.
				card.values.hotel_lodging = last ? last.values.hotel_lodging : "";
				card.values.check_in_date = last ? last.values.check_in_date : this.state.trip.start_date;
				card.values.check_out_date = last ? last.values.check_out_date : this.state.trip.end_date;
				card.values.paid_by = last ? last.values.paid_by : "Company";
				card.values.paid_by_traveler = last ? last.values.paid_by_traveler : "";
				this.cards("accommodations").push(card);
				this.render();
			});
		this.render_gap_list($step, this.gaps_for_step("lodging").filter((g) => !g.group), false);
	}

	draw_nights($matrix) {
		const t = this.state.trip;
		const nights = tp_days_between(t.start_date, t.end_date).slice(0, -1);
		if (!nights.length || !this.crew().length) {
			$matrix.html(`<div class="tp-muted">${__("A one-day trip: nobody needs a bed.")}</div>`);
			return;
		}
		const head = nights.map((n) => `<th>${tp_esc(moment(n).format("ddd D"))}</th>`).join("");
		const rows = this.crew()
			.map((traveler) => {
				const [from, to] = this.stay_window(traveler);
				const day_trip = from && from === to ? ` <span class="tp-muted">${__("day trip")}</span>` : "";
				const cells = nights
					.map((night) => {
						if (!from || !to || night < from || night >= to) return "<td></td>";
						const ok = this.cards("accommodations").some(
							(card) =>
								card.members.some((m) => !m.traveler || m.traveler === traveler.employee) &&
								card.values.check_in_date &&
								card.values.check_out_date &&
								card.values.check_in_date <= night &&
								night < card.values.check_out_date
						);
						return ok ? '<td class="tp-y">&#10003;</td>' : '<td class="tp-n">&#10007;</td>';
					})
					.join("");
				return `<tr><td>${tp_esc(traveler.employee_name || traveler.employee)}${day_trip}</td>${cells}</tr>`;
			})
			.join("");
		$matrix.html(`<table><thead><tr><th>${__("Night of")}</th>${head}</tr></thead><tbody>${rows}</tbody></table>`);
	}

	stay_window(traveler) {
		// [first night, the morning they leave] — completeness.stay_window. Their own dates,
		// narrowed by their own travel: no bed before the day their way there leaves, none from
		// the day their way home leaves. Someone out and back on one day needs no bed, whatever
		// dates the crew step has them down for.
		const t = this.state.trip;
		let from = traveler.from_date || t.start_date || "";
		let to = traveler.to_date || t.end_date || "";
		const first = { Outbound: "", Return: "" };
		["flights", "ground_transport"].forEach((table) => {
			this.cards(table).forEach((card) => {
				const leg = card.values.leg;
				if (leg !== "Outbound" && leg !== "Return") return;
				if (!card.members.some((m) => !m.traveler || m.traveler === traveler.employee)) return;
				const when = tp_date_part(table === "flights" ? card.values.departure_time : card.values.pickup_datetime);
				if (when && (!first[leg] || when < first[leg])) first[leg] = when;
			});
		});
		if (first.Outbound && (!from || first.Outbound > from)) from = first.Outbound;
		if (first.Return && (!to || first.Return < to)) to = first.Return;
		return [from, to];
	}

	needs_a_bed(traveler) {
		const [from, to] = this.stay_window(traveler);
		return !!(from && to && from < to);
	}

	guest_nights(card, employee) {
		// A guest's check-in and check-out: the room's, cut to their own nights. The server
		// stores the same (planner.fit_guest_stays); this is only what the card shows.
		const v = card.values;
		const traveler = this.crew().find((t) => t.employee === employee);
		if (!traveler || !v.check_in_date || !v.check_out_date) return [v.check_in_date, v.check_out_date];
		const [from, to] = this.stay_window(traveler);
		const start = from && from > v.check_in_date ? from : v.check_in_date;
		const end = to && to < v.check_out_date ? to : v.check_out_date;
		return start < end ? [start, end] : [v.check_in_date, v.check_out_date];
	}

	guests_block($card, card) {
		// Someone sharing a room at no cost — an upgraded room, a spare bed. They are ticked
		// into the room like anyone else (so it is on their itinerary, with its confirmation
		// number) and take no share of its cost.
		const named = card.members.filter((m) => m.traveler);
		if (named.length < 2 || named.length !== card.members.length) return;
		const $field = this.field($card, __("Anyone staying free?"));
		$field.css("margin-top", "12px");
		$(`<div class="tp-muted">${__(
			"Tick anyone sharing this room at no extra cost, like an upgraded room. They pay no share of it, and their check-in and check-out follow their own nights."
		)}</div>`).appendTo($field);
		const $chips = $('<div class="tp-chips"></div>').appendTo($field);
		card.members.forEach((member) => {
			const on = !!member.guest;
			$(`<span class="tp-chip ${on ? "tp-on" : ""}">${on ? "&#10003; " : ""}${tp_esc(this.crew_name(member.traveler))}</span>`)
				.appendTo($chips)
				.on("click", () => {
					if (card.protected) return;
					member.guest = on ? 0 : 1;
					this.render();
				});
		});
		card.members
			.filter((m) => m.guest)
			.forEach((member) => {
				const [check_in, check_out] = this.guest_nights(card, member.traveler);
				$(`<div class="tp-muted">${tp_esc(
					__("{0}: in {1}, out {2}", [this.crew_name(member.traveler), tp_pretty_date(check_in), tp_pretty_date(check_out)])
				)}</div>`).appendTo($field);
			});
	}

	room_card($parent, card) {
		const v = card.values;
		const $card = this.card_shell($parent, card, `${__("Room")}: ${this.card_label(card)}`);
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Hotel"), true, "tp-wide"),
			"Supplier",
			v.hotel_lodging,
			(val) => this.set_value(card, "hotel_lodging", val),
			__("e.g. Hilton Las Vegas")
		);
		if (card.address) {
			$(`<div class="tp-muted tp-wide">${tp_esc(card.address)}</div>`).appendTo($grid);
		}
		const redraw = () => this.draw_nights(this.body.find(".tp-matrix"));
		this.input(this.field($grid, __("Check in"), true), "date", v.check_in_date, (val) => {
			this.set_value(card, "check_in_date", val);
			redraw();
		});
		this.time_input($grid, __("Check-in time (optional)"), v.check_in_time, (val) =>
			this.set_value(card, "check_in_time", val)
		);
		this.input(this.field($grid, __("Check out"), true), "date", v.check_out_date, (val) => {
			this.set_value(card, "check_out_date", val);
			redraw();
		});
		this.time_input($grid, __("Check-out time (optional)"), v.check_out_time, (val) =>
			this.set_value(card, "check_out_time", val)
		);
		this.members_block($card, card, __("Who's in this room?"));
		this.guests_block($card, card);
		this.refs_block($card, card);
		this.paperwork_block($card, this.booking_target(card));
		this.money_block($card, card, __("Total for the room, whole stay"));
		this.card_gaps($card, card);
	}

	// ------------------------------------------------------------------ freight

	step_freight($step) {
		this.step_intro(
			$step,
			__("Freight"),
			__(
				"Equipment and materials a carrier ships to the job: the tracking number, the pickup and delivery windows, and who on the crew meets it. What rides on our own truck goes under Hauling on that drive instead. Optional."
			)
		);
		this.state.freight.forEach((item) => this.freight_card($step, item));
		$(`<div class="tp-add"><button class="tp-btn">+ ${__("Add a shipment")}</button></div>`)
			.appendTo($step)
			.find("button")
			.on("click", () => {
				const item = { name: null };
				TP_FREIGHT.forEach((field) => {
					item[field] = "";
				});
				item.paid_by = "Company";
				item.cost = 0;
				item.billable = this.state.trip.billable ? 1 : 0;
				// A key for it before it is saved, so a file can be attached to it now.
				item.booking_group = `new:${++this.card_seq}`;
				this.state.freight.push(item);
				this.render();
			});
	}

	freight_card($parent, item) {
		// A shipment is one row; its checklist key is completeness.group_key's, its stored
		// booking_group (a row from before shipments had one: "row:<name>").
		const key = this.freight_key(item);
		const gaps = key ? this.gaps().filter((g) => g.group === key) : [];
		// Red for a real gap only; a bill of lading not attached yet is a quiet note.
		const bad = gaps.some((g) => !tp_is_paperwork(g));
		const title = [item.carrier, item.tracking_number].filter(Boolean).join(" ") || __("Shipment");
		const $card = $(`<div class="tp-card ${bad ? "tp-bad" : ""}">
			<div class="tp-card-head"><b>${tp_esc(__("Freight"))}: ${tp_esc(title)}</b></div>
		</div>`).appendTo($parent);
		if (!item.protected) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($card.find(".tp-card-head"))
				.on("click", () => {
					const shipment = this.freight_key(item);
					const files = shipment ? this.documents_for(shipment).length : 0;
					const question = files
						? __("Remove this shipment? Its files come off the trip's list with it; they stay attached to the trip on the full form.")
						: __("Remove this shipment?");
					frappe.confirm(question, () => {
						this.state.freight.splice(this.state.freight.indexOf(item), 1);
						if (shipment) {
							this.state.documents = this.documents().filter((doc) => doc.booking_group !== shipment);
						}
						this.render();
					});
				});
		}
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		this.link(
			this.field($grid, __("Carrier"), true),
			"Supplier",
			item.carrier,
			(val) => {
				item.carrier = val;
			},
			__("e.g. Old Dominion")
		);
		this.input(this.field($grid, __("Tracking / PRO / BOL #")), "text", item.tracking_number, (val) => {
			item.tracking_number = val.trim();
		});
		$("<textarea rows=\"2\"></textarea>")
			.val(item.contents || "")
			.attr("placeholder", __("e.g. 2 crates: basin liner, pump skid"))
			.appendTo(this.field($grid, __("What's being shipped"), false, "tp-wide"))
			.on("input", (e) => {
				item.contents = e.target.value;
			});
		this.place(this.field($grid, __("Ship from")), item.ship_from, (val) => {
			item.ship_from = val;
		});
		this.place(this.field($grid, __("Deliver to")), item.deliver_to, (val) => {
			item.deliver_to = val;
		});
		this.time_window($grid, __("Pickup window"), item.pickup_from, item.pickup_to, (from, to) => {
			item.pickup_from = from;
			item.pickup_to = to;
		});
		this.time_window($grid, __("Delivery window"), item.delivery_from, item.delivery_to, (from, to) => {
			item.delivery_from = from;
			item.delivery_to = to;
		});
		const $who = $("<select></select>").appendTo(this.field($grid, __("Who meets the delivery?")));
		$("<option></option>").val("").text(__("Whole crew")).appendTo($who);
		this.crew().forEach((t) => {
			$("<option></option>").val(t.employee).text(t.employee_name || t.employee).appendTo($who);
		});
		$who.val(item.traveler || "").on("change", () => {
			item.traveler = $who.val();
		});
		this.paperwork_block($card, this.freight_target(item));
		// money_block works on a card; a shipment's fields ARE its values, with nobody to
		// split the cost between.
		this.money_block($card, { values: item, members: [], changed: new Set() }, __("Freight cost"));
		this.render_gap_list($card, gaps, false);
	}

	// ------------------------------------------------------------------ step 7: schedule

	step_schedule($step) {
		this.step_intro(
			$step,
			__("Schedule"),
			__("What happens each day: customer visits, site work, a supply run. Optional, and it lands on everyone's itinerary.")
		);
		const t = this.state.trip;
		const days = tp_days_between(t.start_date, t.end_date);
		const stops = this.state.stops;

		// Sort a copy for display. Sorting the state itself would make an untouched trip look
		// edited; the order is written on the next save instead (see payload()).
		let current_day = null;
		tp_sorted_stops(stops).forEach((stop) => {
			if (stop.date !== current_day) {
				current_day = stop.date;
				$(`<div class="tp-day">${tp_esc(tp_pretty_date(stop.date) || __("No day yet"))}</div>`).appendTo($step);
			}
			this.stop_card($step, stop, days);
		});

		$(`<div class="tp-add"><button class="tp-btn">+ ${__("Add a stop")}</button></div>`)
			.appendTo($step)
			.find("button")
			.on("click", () => {
				const last = stops.slice(-1)[0];
				stops.push({
					name: null,
					date: last ? last.date : days[0] || "",
					time: "",
					end_time: "",
					activity_description: "",
					related_party_doctype: "",
					related_party_name: "",
					location: "",
				});
				this.render();
			});
	}

	stop_card($parent, stop, days) {
		const $card = $('<div class="tp-card"></div>').appendTo($parent);
		const when = stop.time
			? tp_pretty_time(stop.time) + (stop.end_time ? ` - ${tp_pretty_time(stop.end_time)}` : "")
			: __("Any time");
		const $head = $(`<div class="tp-card-head"><b>${tp_esc(when)}</b></div>`).appendTo($card);
		if (!stop.outcome_name) {
			$(`<button class="tp-btn-link tp-remove">${__("Remove")}</button>`)
				.appendTo($head)
				.on("click", () => {
					this.state.stops.splice(this.state.stops.indexOf(stop), 1);
					this.render();
				});
		}
		const $grid = $('<div class="tp-grid"></div>').appendTo($card);
		const $day = $("<select></select>").appendTo(this.field($grid, __("Day"), true));
		days.forEach((d) => $("<option></option>").val(d).text(tp_pretty_date(d)).appendTo($day));
		if (stop.date && !days.includes(stop.date)) {
			$("<option></option>").val(stop.date).text(tp_pretty_date(stop.date)).appendTo($day);
		}
		$day.val(stop.date || "").on("change", () => {
			stop.date = $day.val();
		});
		this.time_input($grid, __("From"), stop.time, (val) => {
			stop.time = val;
		});
		this.time_input($grid, __("Until (optional)"), stop.end_time, (val) => {
			stop.end_time = val;
		});
		this.input(this.field($grid, __("What's happening?"), true, "tp-wide"), "text", stop.activity_description, (val) => {
			stop.activity_description = val;
		}).attr("placeholder", __("e.g. Walk the site with the GC"));

		const $who = this.field($grid, __("Meeting with (optional)"), false, "tp-wide");
		const $pair = $('<div class="tp-grid"></div>').appendTo($who);
		const $type = $("<select></select>").appendTo($('<div class="tp-field"></div>').appendTo($pair));
		[""].concat(this.lookups.related_party_doctypes || []).forEach((doctype) => {
			$("<option></option>")
				.val(doctype)
				.text(doctype ? __(doctype) : __("Nobody in particular"))
				.appendTo($type);
		});
		$type.val(stop.related_party_doctype || "");
		const $holder = $('<div class="tp-field"></div>').appendTo($pair);
		const mount = () => {
			$holder.empty();
			if (!stop.related_party_doctype) return;
			this.link($holder, stop.related_party_doctype, stop.related_party_name, (val) => {
				stop.related_party_name = val;
			});
		};
		$type.on("change", () => {
			stop.related_party_doctype = $type.val();
			stop.related_party_name = "";
			mount();
		});
		mount();
		// The Place is a Travel POI. Picking a Google suggestion finds or makes that POI
		// right away, with its point, so the trip map can plot it; a name typed without
		// picking is sent as text and the server finds or makes the POI on save.
		const $place = this.field($grid, __("Place (optional)"), false, "tp-wide");
		this.place(
			$place,
			stop.location_title || stop.location_text || "",
			(text, meta) => {
				if (!meta) {
					if (text !== stop.location_title) {
						stop.location = "";
						stop.location_title = "";
					}
					stop.location_text = text;
					return;
				}
				frappe
					.call({
						method: "erpnext_enhancements.travel_management.planner.place_to_poi",
						args: {
							label: text,
							address: meta.formatted_address,
							latitude: meta.latitude,
							longitude: meta.longitude,
						},
					})
					.then((r) => {
						const poi = r && r.message;
						if (!poi) return;
						stop.location = poi.name;
						stop.location_title = poi.poi_name;
						stop.location_text = "";
					});
			},
			__("e.g. The Home Depot, Mesa")
		);
	}

	// ------------------------------------------------------------------ step 9: files

	step_files($step) {
		this.step_intro(
			$step,
			__("Files"),
			__(
				"Paperwork for the whole trip, like a site map, the safety plan, an insurance certificate or the job packet, and every file attached to a booking. A boarding pass, a hotel confirmation or a rental agreement goes on its booking: attach it there. Receipts go with the cost on the full form, not here."
			)
		);
		const $trip = $('<div class="tp-card"></div>').appendTo($step);
		$(`<div class="tp-card-head"><b>${__("For the whole trip")}</b></div>`).appendTo($trip);
		this.paperwork_block($trip, this.trip_target(), false);

		const $on = $('<div class="tp-card"></div>').appendTo($step);
		$(`<div class="tp-card-head"><b>${__("On bookings")}</b></div>`).appendTo($on);
		let any = false;
		this.booking_list().forEach((booking) => {
			const docs = this.documents_for(booking.key);
			if (!docs.length) return;
			any = true;
			const $booking = $('<div class="tp-bookdocs"></div>').appendTo($on);
			const $head = $(`<div class="tp-bookdocs-head"><span>${booking.icon} ${tp_esc(booking.label)}</span></div>`).appendTo(
				$booking
			);
			const step = TP_STEPS.find((s) => s.key === booking.step);
			$(`<button class="tp-btn-link">${__("Go to {0}", [tp_esc(step ? step.title : booking.step)])} &rarr;</button>`)
				.appendTo($head)
				.on("click", () => this.jump_to(booking.step));
			docs.forEach((doc) => this.doc_row($booking, doc, booking.target, () => this.render()));
		});
		if (!any) {
			$(`<div class="tp-muted">${__(
				"No files on a booking yet. Attach a boarding pass, a confirmation or a rental agreement on the booking itself."
			)}</div>`).appendTo($on);
		}
		// The paperwork not attached yet, counted as files and muted (the quieter tally), each with
		// the step whose card takes it.
		const paperwork = this.paperwork_gaps();
		if (paperwork.length) {
			$(`<div class="tp-docs-head" style="margin-top:12px;"><label>${tp_esc(
				tp_files_not_attached_text(paperwork)
			)}</label></div>`).appendTo($on);
			this.render_gap_list($on, paperwork, true);
		}
	}

	// ------------------------------------------------------------------ step 10: review

	step_review($step) {
		const s = this.state;
		this.step_intro(
			$step,
			__("Review"),
			__("Everything for this trip in one place, and anything still missing. Fix it now or come back later.")
		);
		if (!s.name) {
			$(`<div class="tp-gap">${__("This trip is not saved yet: it needs its basics and at least one person.")}</div>`).appendTo(
				$step
			);
			return;
		}

		const total =
			Object.keys(s.bookings).reduce(
				(sum, table) => sum + this.cards(table).reduce((acc, card) => acc + flt(card.values.cost), 0),
				0
			) + s.freight.reduce((acc, item) => acc + flt(item.cost), 0);
		$(`<div class="tp-sum">
			<div><span class="tp-muted">${__("People")}</span><b>${this.crew().length}</b></div>
			<div><span class="tp-muted">${__("Flights")}</span><b>${this.cards("flights").length}</b></div>
			<div><span class="tp-muted">${__("Rooms")}</span><b>${this.cards("accommodations").length}</b></div>
			<div><span class="tp-muted">${__("Drives and rentals")}</span><b>${this.cards("ground_transport").length}</b></div>
			<div><span class="tp-muted">${__("Shipments")}</span><b>${s.freight.length}</b></div>
			<div><span class="tp-muted">${__("Files")}</span><b>${this.documents().length}</b></div>
			<div><span class="tp-muted">${__("Booked so far")}</span><b>${tp_esc(format_currency(total, this.lookups.currency))}</b></div>
		</div>`).appendTo($step);

		this.render_numbers_by_person($step);

		const sections = [
			{ check: "travel", title: __("Travel both ways"), ok: __("Everyone has a way there and a way back.") },
			{ check: "lodging", title: __("A bed every night"), ok: __("Everyone has somewhere to sleep every night.") },
			{
				check: "confirmation",
				title: __("Confirmation numbers"),
				ok: __("Every booking has its confirmation number, and every shipment its tracking number."),
			},
			{
				check: "cost",
				title: __("Cost and who paid"),
				ok: __("Every booking has a cost, or is the way home on a round-trip ticket that has one."),
			},
		];
		sections.forEach((section) => {
			const $sec = $(`<div class="tp-review-sec"><h5>${tp_esc(section.title)}</h5></div>`).appendTo($step);
			const gaps = this.counted_gaps().filter((g) => g.check === section.check);
			if (!gaps.length) {
				$(`<div class="tp-ok-line">&#10003; ${tp_esc(section.ok)}</div>`).appendTo($sec);
			} else {
				this.render_gap_list($sec, gaps, true);
			}
		});
		// The fifth section is the quieter tally: its own count, in files, and muted notes rather
		// than missing items. It is not in "Mark as booked"'s count below.
		const $paper = $(`<div class="tp-review-sec"><h5>${__("Paperwork")}</h5></div>`).appendTo($step);
		const paperwork = this.paperwork_gaps();
		if (!paperwork.length) {
			$(`<div class="tp-ok-line">&#10003; ${__("Every booking that's made has its paperwork attached.")}</div>`).appendTo(
				$paper
			);
		} else {
			$(`<div class="tp-paper-count">${tp_esc(tp_files_not_attached_text(paperwork))}</div>`).appendTo($paper);
			this.render_gap_list($paper, paperwork, true);
		}

		const $actions = $('<div class="tp-add" style="margin-top:18px;"></div>').appendTo($step);
		if (s.status === "Planning" && s.can_write) {
			$(`<button class="tp-btn tp-btn-primary">${__("Mark as booked")}</button>`)
				.appendTo($actions)
				.on("click", () => this.mark_booked());
		}
		// Only a travel coordinator may email the whole crew (api.travel.send_itinerary_email);
		// anyone else on the crew may email themselves. Each button waits for the server to say
		// who is looking. It always has by now, since get_plan and save_plan both report the
		// viewer, and only a saved trip reaches these buttons. If an answer ever came without
		// it, showing neither is safe. Showing "Email everyone" is not: for anyone but a
		// coordinator it fails every time, which is the bug this replaced.
		const me = this.viewer.employee;
		if (this.viewer.is_coordinator === true) {
			$(`<button class="tp-btn">${__("Email everyone their itinerary")}</button>`)
				.appendTo($actions)
				.on("click", () => this.send_itineraries());
		} else if (this.viewer.is_coordinator === false && me && this.crew().some((t) => t.employee === me)) {
			$(`<button class="tp-btn">${__("Email me my itinerary")}</button>`)
				.appendTo($actions)
				.on("click", () => this.send_itineraries(me));
		}
		// The whole trip on a page or two, for the job folder or a crew lead who won't open the
		// app. Anyone can print it; only a coordinator's copy carries the cost (the server's rule).
		// Only once the site has the format: a link to a missing one prints Standard, with costs.
		if (s.sheet_available === true) this.sheet_link($actions, __("Print the trip sheet"), tp_sheet_url(s.name));
		$(`<a class="tp-btn" style="display:inline-flex;align-items:center;" href="${frappe.utils.get_form_link("Travel Trip", s.name)}">${__("Open the full form")}</a>`).appendTo($actions);
		if (this.is_dirty()) {
			$(`<div class="tp-muted">${__("The trip sheet shows the trip as last saved.")}</div>`).appendTo($step);
		}
	}

	render_numbers_by_person($step) {
		// What each person will see on their own itinerary: every booking they are on, with
		// their own confirmation number, or a flag where it is missing. Shipments are listed
		// under whoever meets them (all of the crew when nobody is named).
		const $sec = $(`<div class="tp-review-sec"><h5>${__("Each person's confirmation numbers")}</h5></div>`).appendTo(
			$step
		);
		const $table = $('<div class="tp-matrix"><table><tbody></tbody></table></div>').appendTo($sec);
		const $body = $table.find("tbody");
		const icon = { flights: "&#9992;", accommodations: "&#127976;", ground_transport: "&#128663;" };
		this.crew().forEach((t) => {
			const lines = [];
			Object.keys(this.state.bookings).forEach((table) => {
				this.cards(table).forEach((card) => {
					const member = card.members.find((m) => !m.traveler || m.traveler === t.employee);
					if (!member) return;
					const unbooked = table === "ground_transport" && TP_UNBOOKED.includes(card.values.transport_type);
					let number;
					if (member.ref) number = tp_esc(member.ref);
					else if (unbooked) number = `<span class="tp-muted">${__("nothing to confirm")}</span>`;
					else number = `<b style="color:#b91c1c;">${__("missing")}</b>`;
					lines.push(`${icon[table]} ${tp_esc(this.card_label(card))}: ${number}`);
				});
			});
			this.state.freight.forEach((item) => {
				if (item.traveler && item.traveler !== t.employee) return;
				const number = item.tracking_number
					? tp_esc(item.tracking_number)
					: `<b style="color:#b91c1c;">${__("missing")}</b>`;
				lines.push(`&#128230; ${tp_esc(item.carrier || __("Shipment"))}: ${number}`);
			});
			$(`<tr><td>${tp_esc(t.employee_name || t.employee)}</td><td style="text-align:left;white-space:normal;">${
				lines.length ? lines.join("<br>") : `<span class="tp-muted">${__("No bookings yet")}</span>`
			}</td></tr>`).appendTo($body);
		});
	}

	mark_booked() {
		const book = () => {
			this.state.status = "Booked";
			this.save({ force: true }).then((ok) => {
				if (!ok) this.state.status = "Planning";
				this.render();
			});
		};
		// Paperwork not attached yet is not "missing" here: it is the quieter tally, and a trip
		// whose only open items are files is booked without a question.
		const count = this.counted_gaps().length;
		if (!count) {
			book();
			return;
		}
		frappe.confirm(
			__("{0} things on the checklist are still missing. Mark the trip as booked anyway?", [count]),
			book
		);
	}

	// Everyone on the trip (a coordinator), or one person: yourself, or anyone for a
	// coordinator (View as's "Email this to ...").
	send_itineraries(employee) {
		const name = employee ? this.crew_name(employee) : "";
		const yourself = !!employee && employee === this.viewer.employee;
		let question = __("Email each person on this trip their own itinerary, with a calendar invite?");
		if (yourself) question = __("Email you your itinerary, with a calendar invite?");
		else if (employee) question = __("Email {0} their itinerary, with a calendar invite?", [tp_esc(name)]);
		frappe.confirm(question, () => {
			this.save().then((ok) => {
				if (!ok) return;
				const args = { trip: this.state.name };
				if (employee) args.employee = employee;
				frappe
					.call({ method: "erpnext_enhancements.api.travel.send_itinerary_email", args: args })
					.then((r) => {
						const count = ((r && r.message) || []).length;
						let message = __("Itinerary sent to {0} people", [count]);
						if (yourself) message = __("Your itinerary is on its way.");
						else if (employee) message = __("Itinerary sent to {0}", [tp_esc(name)]);
						frappe.show_alert({ message: message, indicator: "green" });
					});
			});
		});
	}

	// ------------------------------------------------------------------ views: drawing
	//
	// Everything below draws from get_trip_views' answer (fetch_views), never from the page's
	// own state: that answer is the saved trip, shaped by the same code as /itinerary and the
	// itinerary email, with money in it only for a travel coordinator.

	render_view(view) {
		const $view = $('<div class="tp-view"></div>').appendTo(this.body);
		const $top = $('<div class="tp-view-top"></div>').appendTo($view);
		$(`<button class="tp-btn">&larr; ${__("Back to planning")}</button>`)
			.appendTo($top)
			.on("click", () => this.leave_view());
		$(`<span class="tp-muted">${tp_esc(TP_STEPS[this.step].title)}</span>`).appendTo($top);
		if (this.is_dirty()) {
			$(`<div class="tp-notice">${__(
				"Your latest changes are not saved yet, so they are not shown here."
			)}</div>`).appendTo($view);
		}
		const key = this.views_key();
		const data = this.views && this.views.key === key ? this.views.data : null;
		if (!data) {
			if (this.views_failed === key) {
				const $failed = $(`<div class="tp-empty">${__("This could not be loaded.")} </div>`).appendTo($view);
				$(`<button class="tp-btn-link">${__("Try again")}</button>`)
					.appendTo($failed)
					.on("click", () => {
						this.views_failed = null;
						this.render();
					});
			} else {
				$(`<div class="tp-empty">${__("Loading...")}</div>`).appendTo($view);
				this.fetch_views();
			}
			return;
		}
		({
			overview: () => this.view_overview($view, data),
			grid: () => this.view_grid($view, data),
			compare: () => this.view_compare($view, data),
			person: () => this.view_person($view, data),
			map: () => this.view_map($view, data),
		})[view]();
	}

	// One itinerary item, in the few words the Overview, the grid and Side by side use:
	// {time, title, sub: [lines], ref: {label, value, needed} | null}. Plain text, escaped
	// where it is drawn.
	item_facts(item) {
		const arrow = (from, to) => (from || to ? `${from || "?"} → ${to || "?"}` : "");
		const ride = TP_RIDE_TYPES.find((x) => x.value === item.transport_type);
		if (item.type === "flight") {
			return {
				time: tp_span(tp_clock(item.departure_time), tp_clock(item.arrival_time)),
				title: [item.airline, item.flight_number].filter(Boolean).join(" ") || __("Flight"),
				sub: [arrow(item.departure_airport, item.arrival_airport)],
				ref: { label: __("PNR"), value: item.booking_reference, needed: true },
			};
		}
		if (item.type === "hotel_checkin" || item.type === "hotel_checkout") {
			const hotel = item.hotel || __("Hotel");
			return {
				time: tp_pretty_time(item.time),
				title: item.type === "hotel_checkin" ? __("Check in: {0}", [hotel]) : __("Check out: {0}", [hotel]),
				sub: [item.address],
				ref: { label: __("Confirmation"), value: item.booking_confirmation, needed: true },
			};
		}
		if (item.type === "ground") {
			return {
				time: tp_span(tp_clock(item.pickup_datetime), tp_clock(item.arrival_datetime)),
				title: item.provider || (ride ? ride.label : item.transport_type) || __("Drive"),
				sub: [
					arrow(item.pickup_location, item.dropoff_location),
					item.cargo ? __("Hauling: {0}", [item.cargo]) : "",
				],
				ref: {
					label: __("Confirmation"),
					value: item.booking_reference,
					needed: !TP_UNBOOKED.includes(item.transport_type),
				},
			};
		}
		if (item.type === "freight") {
			const delivery = !!item.delivery_from;
			return {
				time: delivery
					? tp_span(tp_clock(item.delivery_from), tp_clock(item.delivery_to))
					: tp_span(tp_clock(item.pickup_from), tp_clock(item.pickup_to)),
				title: __("Freight: {0}", [item.carrier || __("Shipment")]),
				sub: [
					item.contents,
					delivery
						? item.deliver_to
							? __("Delivers to {0}", [item.deliver_to])
							: ""
						: item.ship_from
						? __("Picked up from {0}", [item.ship_from])
						: "",
				],
				ref: { label: __("Tracking"), value: item.tracking_number, needed: true },
			};
		}
		return {
			time: tp_span(tp_pretty_time(item.time), tp_pretty_time(item.end_time)),
			title: item.activity || __("Stop"),
			sub: [[item.related_party, item.poi && item.poi.poi_name].filter(Boolean).join(" · ")],
			ref: null,
		};
	}

	// The checklist's per-person gaps that belong on a day: a night with no bed, and the first
	// (or last) day of someone with no way there (or back).
	day_gaps(data) {
		const crew = data.crew || [];
		const out = [];
		(data.gaps || []).forEach((gap) => {
			const person = crew.find((c) => c.employee === gap.employee);
			const who = gap.employee_name || (person && person.employee_name) || gap.employee || "";
			if (gap.check === "lodging" && gap.kind === "nights") {
				(gap.nights || []).forEach((date) => {
					out.push({ gap: gap, employee: gap.employee, who: who, date: date, text: __("No bed tonight") });
				});
			} else if (gap.check === "travel") {
				const there = gap.kind === "Outbound";
				const date = there
					? (person && person.from_date) || data.start_date
					: (person && person.to_date) || data.end_date;
				if (!date) return;
				out.push({
					gap: gap,
					employee: gap.employee,
					who: who,
					date: date,
					text: there ? __("No way there") : __("No way back"),
				});
			}
		});
		return out;
	}

	// What the checklist says about one booking, on the booking.
	booking_gap_text(gap) {
		if (gap.check === "confirmation") {
			if (gap.table === "freight") return __("No tracking, PRO or BOL number yet.");
			const names = (gap.employee_names || []).join(", ");
			return names ? __("No confirmation number for {0}.", [names]) : __("No confirmation number yet.");
		}
		if (gap.check === "cost") return __("No cost entered.");
		if (gap.check === "lodging") return __("The check-in or check-out day is missing.");
		if (gap.check === "documents") {
			// Plain text here (view_flag escapes it); paperwork_missing escapes its names for the
			// steps' HTML.
			if (gap.table === "flights") {
				const names = (gap.employee_names || []).join(", ");
				return names ? __("No boarding pass or ticket for {0}.", [names]) : __("No boarding pass or ticket attached.");
			}
			if (gap.table === "accommodations") return __("No confirmation attached.");
			if (gap.table === "ground_transport") return __("No rental agreement attached.");
			if (gap.table === "freight") return __("No bill of lading attached.");
			return __("No paperwork attached.");
		}
		return gap.label || "";
	}

	// A booking's files on a view (the itinerary's `documents`: never a receipt, which is
	// money), each a link to the file: its name and who it is for.
	doc_chips(docs) {
		return (docs || [])
			.map((doc) => {
				const url = tp_file_url(doc.url);
				const kind = TP_DOC_KINDS.includes(doc.kind) ? doc.kind : "Other";
				const text = `${doc.title || doc.file_name || __("File")} · ${
					doc.for_name ? __("for {0}", [doc.for_name]) : __("everyone")
				}`;
				return url
					? `<a class="tp-docchip" target="_blank" rel="noopener" href="${tp_esc(url)}" title="${tp_esc(
							text
					  )}">${TP_DOC_ICONS[kind]} ${tp_esc(text)}</a>`
					: `<span class="tp-docchip">${TP_DOC_ICONS[kind]} ${tp_esc(text)}</span>`;
			})
			.join("");
	}

	// The trip's own files (no booking) on the Overview: every one, whoever it is for. A file's
	// `group` is its booking's key and is null for the whole trip's (shape_itinerary), which is
	// also how a file whose booking has been removed reads; its label can be blank either way.
	trip_files($parent, data) {
		const docs = (data.documents || []).filter((doc) => !doc.group);
		if (!docs.length) return;
		const $sec = $(`<div class="tp-review-sec"><h5>${__("Files for the whole trip")}</h5></div>`).appendTo($parent);
		$(`<div class="tp-docchips">${this.doc_chips(docs)}</div>`).appendTo($sec);
	}

	// A checklist item where it applies, with the step that fixes it.
	view_flag($parent, text, gap) {
		const $flag = $(`<div class="tp-flag"><span>&#9888; ${tp_esc(text)}</span></div>`).appendTo($parent);
		if (gap && gap.step && TP_STEPS.some((s) => s.key === gap.step)) {
			$(`<button class="tp-btn-link">${__("Fix")} &rarr;</button>`)
				.appendTo($flag)
				.on("click", () => this.jump_to(gap.step));
		}
	}

	// Paperwork not attached yet, on a view: a muted note, never a flag, and no Fix button — the
	// quieter tally (see PAPERWORK in the header). Plain text in, escaped here.
	view_note($parent, text) {
		$(`<div class="tp-paper"><span>&#128196; ${tp_esc(text)}</span></div>`).appendTo($parent);
	}

	day_heading(date, data, today) {
		const outside = (data.start_date && date < data.start_date) || (data.end_date && date > data.end_date);
		return `${tp_esc(tp_pretty_date(date))}${
			date === today ? ` <span class="tp-today-tag">${__("Today")}</span>` : ""
		}${outside ? ` <span class="tp-muted">${__("(outside the trip dates)")}</span>` : ""}`;
	}

	// ---- Overview: every day, each booking once, the checklist where it applies

	view_overview($view, data) {
		const crew = data.crew || [];
		const days = data.days || [];
		const money = data.money || null;
		const whole = tp_items_by_date(data.whole);
		const all = [].concat(...Object.values(whole));
		// Counted by the server the way Review counts its cards (views.booking_counts). Counting
		// the items here missed a room with no dates yet, which is on no day: "Rooms 3" here,
		// "Rooms 4" on Review. An answer without the counts falls back to the items.
		const counts = data.bookings || {};
		const bookings = (table, types) =>
			typeof counts[table] === "number"
				? counts[table]
				: new Set(all.filter((i) => types.includes(i.type)).map((i) => i.group || JSON.stringify(i))).size;
		// The trip's own days: a day before or after it is drawn when something is booked on
		// it, but it does not make a four-day trip five days long.
		const trip_days = days.filter(
			(date) => (!data.start_date || date >= data.start_date) && (!data.end_date || date <= data.end_date)
		);
		const tiles = [
			[__("People"), crew.length],
			[__("Days"), trip_days.length],
			[__("Flights"), bookings("flights", ["flight"])],
			[__("Rooms"), bookings("accommodations", ["hotel_checkin", "hotel_checkout"])],
			[__("Drives and rides"), bookings("ground_transport", ["ground"])],
			[__("Shipments"), bookings("freight", ["freight"])],
			// Paperwork is not "missing": it has its own muted tile, and only when there is some.
			[__("Still missing"), (data.gaps || []).filter((gap) => !tp_is_paperwork(gap)).length],
		];
		const files = tp_files_not_attached(data.gaps);
		if (files) tiles.push([__("Files not attached"), files, "tp-quiet"]);
		if (money) tiles.push([__("Booked so far"), format_currency(money.total, money.currency)]);
		const $actions = $('<div class="tp-add"></div>').appendTo($view);
		// The server's address, and only one of this site's; none while the format is missing.
		const sheet = tp_local_url(data.sheet_url);
		if (sheet) this.sheet_link($actions, __("Print the trip sheet"), sheet);
		$(`<div class="tp-sum">${tiles
			.map(
				([label, value, quiet]) =>
					`<div${quiet ? ` class="${quiet}"` : ""}><span class="tp-muted">${tp_esc(label)}</span><b>${tp_esc(value)}</b></div>`
			)
			.join("")}</div>`).appendTo($view);
		this.contacts_block($view, data.contacts);
		this.trip_files($view, data);

		// Per-person gaps on their day; per-booking gaps on the booking, the first time it
		// shows; anything with nowhere to go at the end.
		const on_days = {};
		const placed = new Set();
		this.day_gaps(data).forEach((entry) => {
			(on_days[entry.date] = on_days[entry.date] || []).push(entry);
		});
		const by_group = {};
		(data.gaps || []).forEach((gap) => {
			if (gap.group) (by_group[gap.group] = by_group[gap.group] || []).push(gap);
		});
		const shown = new Set();
		const today = frappe.datetime.get_today();
		days.forEach((date) => {
			const $day = $('<div class="tp-tl-day"></div>').appendTo($view);
			$(`<div class="tp-tl-date">${this.day_heading(date, data, today)}</div>`).appendTo($day);
			(on_days[date] || []).forEach((entry) => {
				placed.add(entry.gap);
				this.view_flag($day, `${entry.text}: ${entry.who}`, entry.gap);
			});
			const items = whole[date] || [];
			if (!items.length) {
				$(`<div class="tp-muted">${__("Nothing scheduled")}</div>`).appendTo($day);
				return;
			}
			items.forEach((item) => {
				const first = item.group && !shown.has(item.group);
				if (item.group) shown.add(item.group);
				const gaps = first ? by_group[item.group] || [] : [];
				gaps.forEach((gap) => placed.add(gap));
				this.overview_item($day, item, gaps, first && money ? (money.groups || {})[item.group] : null, money);
			});
		});

		const rest = (data.gaps || []).filter((gap) => !placed.has(gap));
		if (rest.length) {
			const $rest = $(`<div class="tp-review-sec"><h5>${__("Not on any day yet")}</h5></div>`).appendTo($view);
			rest.forEach((gap) => {
				const text = tp_html_to_text(this.gap_text(gap));
				if (tp_is_paperwork(gap)) this.view_note($rest, text);
				else this.view_flag($rest, text, gap);
			});
		}
	}

	// A booking's gaps on the Overview: a real gap is a red flag and frames the booking red; its
	// paperwork not attached yet is only a muted note (the quieter tally).
	overview_item($parent, item, gaps, cost, money) {
		const flags = gaps.filter((gap) => !tp_is_paperwork(gap));
		const papers = gaps.filter(tp_is_paperwork);
		const facts = this.item_facts(item);
		const $item = $(`<div class="tp-tl-item ${flags.length ? "tp-bad" : ""}">
			<div class="tp-tl-icon">${TP_ICONS[item.type] || ""}</div>
			<div class="tp-tl-time">${tp_esc(facts.time)}</div>
			<div class="tp-tl-body">
				<div class="tp-tl-title">${tp_esc(facts.title)}</div>
				${facts.sub
					.filter(Boolean)
					.map((line) => `<div class="tp-tl-sub">${tp_esc(line)}</div>`)
					.join("")}
			</div>
		</div>`).appendTo($parent);
		const $body = $item.find(".tp-tl-body");
		const chips = this.item_chips(item, facts);
		if (chips) $(`<div class="tp-whos">${chips}</div>`).appendTo($body);
		if ((item.documents || []).length) {
			$(`<div class="tp-docchips">${this.doc_chips(item.documents)}</div>`).appendTo($body);
		}
		if (item.type === "freight" && facts.ref.value) {
			$(`<div class="tp-tl-sub">${tp_esc(facts.ref.label)}: <b>${tp_esc(facts.ref.value)}</b></div>`).appendTo($body);
		}
		// A booking with nothing entered says so through its cost gap; a company truck costs nothing.
		if (cost && flt(cost.cost) > 0) {
			const paid =
				cost.paid_by === "Employee"
					? __("paid by {0}, to reimburse", [cost.paid_by_name || __("an employee")])
					: cost.paid_by === "Company"
					? __("paid by the company")
					: "";
			$(`<div class="tp-cost">${tp_esc(
				[`${__("Cost")}: ${format_currency(cost.cost, money.currency)}`, paid].filter(Boolean).join(" · ")
			)}</div>`).appendTo($body);
		}
		flags.forEach((gap) => this.view_flag($body, this.booking_gap_text(gap), gap));
		papers.forEach((gap) => this.view_note($body, this.booking_gap_text(gap)));
	}

	// Who is on a booking, each with their own number (the Overview shows each booking once).
	item_chips(item, facts) {
		if (item.type === "agenda") return "";
		if (item.type === "freight") {
			const who = item.whole_crew || !item.received_by ? __("Whole crew") : __("Received by {0}", [item.received_by]);
			return `<span class="tp-who">${tp_esc(who)}</span>`;
		}
		const ref = facts.ref;
		const chip = (name, number, check) => {
			const missing = check && ref && ref.needed && !number;
			return `<span class="tp-who ${missing ? "tp-who-miss" : ""}">${tp_esc(name)}${
				number ? `: <b>${tp_esc(number)}</b>` : missing ? ` &middot; ${__("no number")}` : ""
			}</span>`;
		};
		if (!Array.isArray(item.members)) {
			// An answer without per-person numbers: the names, and the numbers as one line.
			const names = (item.travelers || []).map((name) => chip(name, "", false)).join("");
			return names + (ref && ref.value ? chip(ref.label, ref.value, false) : "");
		}
		return item.members.map((m) => chip(this.member_label(item, m), m.ref, true)).join("");
	}

	// A person on a booking, as a chip names them. A room's guest (staying free in someone
	// else's room) is marked, with their own nights when they are not the room's: shape_itinerary
	// gives a member its own row's check-in and check-out only when they differ.
	member_label(item, member) {
		const name = member.employee ? member.employee_name || member.employee : __("Whole crew");
		if (!member.guest) return name;
		const day = (date) => (date ? moment(date).format("ddd") : "");
		const nights =
			member.check_in_date || member.check_out_date
				? [day(member.check_in_date || item.date), day(member.check_out_date)].filter(Boolean).join("–")
				: "";
		return nights ? __("{0} (guest, {1})", [name, nights]) : __("{0} (guest)", [name]);
	}

	// ---- Crew grid: people down the side, days across

	view_grid($view, data) {
		const crew = data.crew || [];
		const days = data.days || [];
		if (!crew.length || !days.length) {
			$(`<div class="tp-muted">${__("Nobody is on this trip yet.")}</div>`).appendTo($view);
			return;
		}
		const today = frappe.datetime.get_today();
		const warnings = {};
		this.day_gaps(data).forEach((entry) => {
			const key = `${entry.employee}|${entry.date}`;
			(warnings[key] = warnings[key] || []).push(entry.text);
		});
		const head = days
			.map(
				(date) =>
					`<th class="${date === today ? "tp-today" : ""}">${tp_esc(moment(date).format("ddd"))}<br>${tp_esc(
						moment(date).format("MMM D")
					)}</th>`
			)
			.join("");
		const rows = crew
			.map((person) => {
				const own = (data.people || {})[person.employee] || [];
				const by_date = tp_items_by_date(own);
				const nights = tp_nights(own);
				const cells = days
					.map((date) => {
						const off = date < person.from_date || date > person.to_date;
						const icons = [];
						const lines = [];
						(by_date[date] || []).forEach((item) => {
							// Stops are the whole crew's: only on the days this person is there.
							if (item.type === "agenda" && off) return;
							const facts = this.item_facts(item);
							lines.push([facts.time, facts.title].filter(Boolean).join(" "));
							// A room shows as the nights slept in it, not as its two days.
							if (item.type !== "hotel_checkin" && item.type !== "hotel_checkout") {
								icons.push(TP_ICONS[item.type]);
							}
						});
						if (nights.has(date)) {
							icons.push(TP_ICONS.hotel_checkin);
							lines.push(__("Night in a room"));
						}
						const warn = warnings[`${person.employee}|${date}`] || [];
						if (warn.length) {
							icons.push('<span class="tp-warn">&#9888;</span>');
							lines.push(...warn);
						}
						const classes = [off ? "tp-off" : "", warn.length ? "tp-cell-warn" : ""];
						return `<td class="${classes.join(" ")}" title="${tp_esc(lines.join("\n"))}">${icons.join(" ")}</td>`;
					})
					.join("");
				return `<tr><th class="tp-sticky"><button class="tp-btn-link tp-name" data-employee="${tp_esc(
					person.employee
				)}" title="${__("See what {0} sees", [tp_esc(person.employee_name)])}">${tp_esc(
					person.employee_name
				)}</button></th>${cells}</tr>`;
			})
			.join("");
		const $scroll = $(`<div class="tp-xscroll"><table class="tp-xtable tp-cgrid">
			<thead><tr><th class="tp-sticky">${__("Who")}</th>${head}</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`).appendTo($view);
		$scroll.find("[data-employee]").on("click", (event) => {
			this.open_view("person", String($(event.currentTarget).attr("data-employee")));
		});
		$(`<div class="tp-legend">
			<span>&#9992; ${__("flight")}</span>
			<span>&#127976; ${__("night in a room")}</span>
			<span>&#128663; ${__("drive or ride")}</span>
			<span>&#128205; ${__("stop")}</span>
			<span>&#128230; ${__("shipment")}</span>
			<span><span class="tp-warn">&#9888;</span> ${__("something missing")}</span>
			<span>${__("Gray: not on the trip that day. Tap a name to see what that person sees.")}</span>
		</div>`).appendTo($view);
	}

	// ---- Side by side: a column per person, a row per day

	view_compare($view, data) {
		const crew = data.crew || [];
		const days = data.days || [];
		// Who is left out is a choice about this trip: another trip starts with everyone, even
		// when the same people are on it.
		if (!this.compare_hidden || this.compare_hidden.trip !== data.trip) {
			this.compare_hidden = { trip: data.trip, people: new Set() };
		}
		const hidden = this.compare_hidden.people;
		const $pick = $('<div class="tp-chips" style="margin-bottom:12px;"></div>').appendTo($view);
		crew.forEach((person) => {
			const on = !hidden.has(person.employee);
			$(`<span class="tp-chip ${on ? "tp-on" : ""}">${on ? "&#10003; " : ""}${tp_esc(person.employee_name)}</span>`)
				.appendTo($pick)
				.on("click", () => {
					// Who is shown is a choice on this screen, not a screen: no history entry.
					if (on) hidden.add(person.employee);
					else hidden.delete(person.employee);
					this.render();
				});
		});
		const shown = crew.filter((person) => !hidden.has(person.employee));
		if (!shown.length) {
			$(`<div class="tp-muted">${__("Pick at least one person above.")}</div>`).appendTo($view);
			return;
		}
		const today = frappe.datetime.get_today();
		const warnings = {};
		this.day_gaps(data).forEach((entry) => {
			const key = `${entry.employee}|${entry.date}`;
			(warnings[key] = warnings[key] || []).push(entry.text);
		});
		const by_person = {};
		shown.forEach((person) => {
			by_person[person.employee] = tp_items_by_date((data.people || {})[person.employee]);
		});
		const head = shown
			.map(
				(person) => `<th><b>${tp_esc(person.employee_name)}</b><div class="tp-muted">${tp_esc(
					tp_span(tp_pretty_date(person.from_date), tp_pretty_date(person.to_date))
				)}</div></th>`
			)
			.join("");
		const rows = days
			.map((date) => {
				const cells = shown
					.map((person) => {
						const off = date < person.from_date || date > person.to_date;
						const lines = [];
						(by_person[person.employee][date] || []).forEach((item) => {
							if (item.type === "agenda" && off) return;
							lines.push(this.compare_line(item));
						});
						(warnings[`${person.employee}|${date}`] || []).forEach((text) => {
							lines.push(`<div class="tp-line tp-miss-text">&#9888; ${tp_esc(text)}</div>`);
						});
						return `<td class="${off ? "tp-off" : ""}">${
							lines.join("") || '<span class="tp-muted">&ndash;</span>'
						}</td>`;
					})
					.join("");
				return `<tr><th class="tp-sticky ${date === today ? "tp-today" : ""}">${this.day_heading(
					date,
					data,
					today
				)}</th>${cells}</tr>`;
			})
			.join("");
		$(`<div class="tp-xscroll"><table class="tp-xtable tp-cmp">
			<thead><tr><th class="tp-sticky">${__("Day")}</th>${head}</tr></thead>
			<tbody>${rows}</tbody>
		</table></div>`).appendTo($view);
	}

	compare_line(item) {
		const facts = this.item_facts(item);
		const ref = facts.ref;
		let number = "";
		if (ref && ref.value) number = ` &middot; ${tp_esc(ref.value)}`;
		else if (ref && ref.needed) number = ` &middot; <span class="tp-miss-text">${__("no number")}</span>`;
		const files = (item.documents || []).length;
		const count = files
			? ` <span class="tp-muted" title="${tp_esc(__("{0} files", [files]))}">&middot; &#128196; ${files}</span>`
			: "";
		return `<div class="tp-line">${TP_ICONS[item.type] || ""} ${
			facts.time ? `<span class="tp-muted">${tp_esc(facts.time)}</span> ` : ""
		}${tp_esc(facts.title)}${number}${count}</div>`;
	}

	// ---- Map: every place on the trip, the drives between them, and a list of them by day
	//
	// The places are the server's (views.build_trip_views' `places` and `legs`: airports,
	// hotels, stops, pick-ups and drop-offs, freight deliveries — nothing about money). A place
	// with a point is put where it is; one with only words (a hotel's address, "PHX airport") is
	// looked up with Google's Geocoder and remembered (tp_geo_read). Each is numbered in the
	// order the trip reaches it — by its first day, then its first time — and the list under the
	// map, which is drawn whether or not the map can be (no key, Google down), uses the same
	// numbers, each place with its Google Maps link. The day chips are a choice on this screen,
	// not a screen: no history entry.

	view_map($view, data) {
		// Any map drawn before this one is no longer on screen: its answers are dropped.
		const gen = ++this.map_gen;
		const trip = data.trip;
		const places = this.map_places(data);
		const legs = (Array.isArray(data.legs) ? data.legs : []).filter((leg) => leg && leg.from && leg.to);
		if (!places.length) {
			$(`<div class="tp-muted">${__(
				"Nothing on this trip has a place yet. Airports, hotels, drives, stops and freight deliveries show here once they are entered."
			)}</div>`).appendTo($view);
			return;
		}
		// Days are numbered from the trip's first day, as the printed Trip Sheet numbers them.
		const start = data.start_date || "";
		const day_list = [];
		places.forEach((place) =>
			place.days.forEach((day) => {
				if (!day_list.includes(day)) day_list.push(day);
			})
		);
		day_list.sort();
		if (!this.map_day || this.map_day.trip !== trip || (this.map_day.day && !day_list.includes(this.map_day.day))) {
			this.map_day = { trip: trip, day: "" };
		}
		// No key, or no loader (the desk bundle did not load): the list is the map.
		const maps = data.maps || {};
		const blocked = !maps.api_key
			? __("Add the Google Maps key in Travel Settings to see the map.")
			: !window.EEGoogleMaps
			? __("The map could not be loaded right now. The places are listed below.")
			: "";
		const $chips = $('<div class="tp-viewbar tp-map-days"></div>').appendTo($view);
		const $canvas = blocked ? null : $('<div class="tp-map"></div>').appendTo($view);
		const $status = $(`<div class="tp-muted tp-map-status">${tp_esc(blocked)}</div>`).appendTo($view);
		const $list = $('<div class="tp-map-list"></div>').appendTo($view);
		let trip_map = null;

		const draw_day = () => {
			const day = this.map_day.day;
			$chips.empty();
			[["", __("All days")]]
				.concat(day_list.map((date) => [date, this.map_day_words(date, start)]))
				.forEach(([date, words]) => {
					$(`<button class="tp-vbtn tp-map-day ${date === day ? "tp-active" : ""}" data-day="${tp_esc(date)}">${tp_esc(
						words
					)}</button>`)
						.appendTo($chips)
						.on("click", () => {
							if (this.map_day.day === date) return;
							this.map_day = { trip: trip, day: date };
							draw_day();
						});
				});
			$list.empty();
			this.map_list($list, places, day_list, day, start);
			if (trip_map) trip_map.show_day(day);
		};
		draw_day();
		if (blocked) return;

		const key = this.views_key();
		const alive = () => gen === this.map_gen && this.view === "map" && this.views_key() === key;
		const theme = tp_desk_theme();
		this.watch_map_theme(theme, alive);
		window.EEGoogleMaps.load({ apiKey: maps.api_key, libraries: TP_MAP_LIBRARIES })
			.then((google_maps) => {
				if (!alive()) return null;
				trip_map = new TpTripMap(google_maps, $canvas[0], { alive: alive, config: maps, theme: theme });
				return trip_map.draw(places, legs, this.map_day.day);
			})
			.then(
				(missing) => {
					if (!alive() || !missing) return;
					$status.text(
						missing === 1
							? __("1 place could not be found on the map. Its Google Maps link is in the list below.")
							: __("{0} places could not be found on the map. Their Google Maps links are in the list below.", [
									missing,
							  ])
					);
				},
				() => {
					if (!alive()) return;
					trip_map = null;
					$canvas.remove();
					$status.text(__("The map could not be loaded right now. The places are listed below."));
				}
			);
	}

	// A Map ID and a colorScheme are fixed when a Google map is made (google_maps_loader.js):
	// setOptions() restyles neither. So when the desk's theme flips while the map is on screen (the
	// theme toggle, or Automatic following the device), the view is drawn again for the new theme,
	// as the location timeline and the kiosk map are: from the views' answer already here, on the
	// day already chosen, and the old map's late answers are dropped (map_gen). One watch at a
	// time: the next view_map and every render() stop this one, and it stops itself once its map
	// has left the screen.
	watch_map_theme(theme, alive) {
		this.stop_map_theme_watch();
		const root = typeof document !== "undefined" && document ? document.documentElement : null;
		if (typeof MutationObserver !== "function" || !root) return;
		const observer = new MutationObserver(() => {
			if (!alive()) {
				observer.disconnect();
				if (this.map_theme_observer === observer) this.map_theme_observer = null;
				return;
			}
			if (tp_desk_theme() === theme) return;
			this.stop_map_theme_watch();
			this.render();
		});
		observer.observe(root, { attributes: true, attributeFilter: ["data-theme"] });
		this.map_theme_observer = observer;
	}

	stop_map_theme_watch() {
		if (this.map_theme_observer) this.map_theme_observer.disconnect();
		this.map_theme_observer = null;
	}

	// The places, numbered in the order the trip reaches them: by first day, then first time (a
	// place with no time comes after the timed ones that day), then as the server listed them;
	// a place with no day yet last. Each keeps its own sorted days.
	map_places(data) {
		const list = (Array.isArray(data.places) ? data.places : []).filter(
			(place) => place && typeof place === "object" && (tp_point(place) || String(place.query || "").trim())
		);
		// Plain comparison, not localeCompare: a collation puts "~" (no time) before the digits.
		const compare = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
		return list
			.map((place, index) => {
				const days = (Array.isArray(place.days) ? place.days : []).filter((d) => typeof d === "string").sort();
				const time = /^\d{2}:\d{2}/.test(String(place.first_time || "")) ? String(place.first_time).slice(0, 5) : "~";
				return { place: place, index: index, days: days, time: time };
			})
			.sort(
				(a, b) =>
					(a.days.length ? 0 : 1) - (b.days.length ? 0 : 1) ||
					compare(a.days[0] || "", b.days[0] || "") ||
					compare(a.time, b.time) ||
					a.index - b.index
			)
			.map((entry, i) =>
				Object.assign({}, entry.place, {
					key: entry.place.key || `place-${entry.index}`,
					days: entry.days,
					who: Array.isArray(entry.place.who) ? entry.place.who : [],
					number: i + 1,
				})
			);
	}

	// "Day 2 · Tue, Oct 6": numbered from the trip's first day (`start`), as the Trip Sheet
	// numbers it (views.build_trip_sheet), so the map and the paper in a crew lead's hand say the
	// same day. A day before the trip starts (a flight the evening before) has no number. Until
	// 2026-09-27 this counted the days the views show, which include that evening, so every chip
	// was one ahead of the sheet.
	map_day_words(date, start) {
		const pretty = tp_pretty_date(date) || date;
		const number = start ? tp_days_apart(start, date) + 1 : NaN;
		return number >= 1 ? __("Day {0} · {1}", [number, pretty]) : pretty;
	}

	// The list under the map: each day's places (a hotel is under every day it is on), with the
	// map's numbers, what each is, when and who, and its Google Maps link; then the places with
	// no day yet, on "All days" only.
	map_list($list, places, day_list, day, start) {
		const row = (place, first) => {
			const kind = TP_PLACE_KINDS[place.kind] || TP_PLACE_KINDS.stop;
			const url = tp_maps_search(place);
			const facts = [
				kind.label,
				first && place.first_time ? tp_pretty_time(place.first_time) : "",
				place.who.join(", "),
			].filter(Boolean);
			$(`<div class="tp-map-row">
				<span class="tp-map-num" style="background:${kind.color};">${place.number}</span>
				<span class="tp-map-body"><b>${kind.icon} ${tp_esc(place.label || kind.label)}</b><span class="tp-muted">${tp_esc(
					facts.join(" · ")
				)}</span></span>
				${
					url
						? `<a class="tp-map-open" target="_blank" rel="noopener" href="${tp_esc(url)}">${__("Open in Google Maps")} &#8599;</a>`
						: ""
				}
			</div>`).appendTo($list);
		};
		(day ? [day] : day_list).forEach((date) => {
			$(`<div class="tp-map-day-head">${tp_esc(this.map_day_words(date, start))}</div>`).appendTo($list);
			places.filter((place) => place.days.includes(date)).forEach((place) => row(place, place.days[0] === date));
		});
		const undated = day ? [] : places.filter((place) => !place.days.length);
		if (undated.length) {
			$(`<div class="tp-map-day-head">${__("No date yet")}</div>`).appendTo($list);
			undated.forEach((place) => row(place, false));
		}
	}

	// ---- Who to call, and the trip sheet

	// A link that opens the trip sheet (a PDF) in a new tab: no history entry, nothing saved.
	// The sheet is the trip as saved, like every view.
	sheet_link($parent, label, url) {
		$(`<a class="tp-btn" style="display:inline-flex;align-items:center;" target="_blank" rel="noopener" href="${tp_esc(
			url
		)}">&#128424; ${tp_esc(label)}</a>`).appendTo($parent);
	}

	// Who to call (the server's `contacts`, api/travel._trip_contacts): 911, the office's travel
	// desk, who booked the trip, the trip lead, the job site's contact, and each hotel with the
	// nearest urgent care and directions. Only these: a crew member's own phone, address or next
	// of kin is never sent (the trip lead's work mobile is). A phone is a tel: link of its digits;
	// a Google link the server built is shown only when it is https. `hotels`: the hotels to
	// show — one person's, on View as — else every hotel on the trip.
	contacts_block($parent, contacts, hotels) {
		if (!contacts || typeof contacts !== "object") return;
		const $sec = $(`<div class="tp-review-sec tp-contacts"><h5>${__("Who to call")}</h5></div>`).appendTo($parent);
		const $grid = $('<div class="tp-contacts-grid"></div>').appendTo($sec);
		const phone = (value) => {
			if (!value) return "";
			const tel = tp_tel(value);
			return tel ? `<a href="${tp_esc(tel)}">&#128222; ${tp_esc(value)}</a>` : tp_esc(value);
		};
		const email = (value) => {
			if (!value) return "";
			const mail = tp_mailto(value);
			return mail ? `<a href="${tp_esc(mail)}">${tp_esc(value)}</a>` : tp_esc(value);
		};
		const web = (url, text) =>
			url ? `<a target="_blank" rel="noopener" href="${tp_esc(url)}">${tp_esc(text)} &#8599;</a>` : "";
		const card = (kicker, name, lines, extra) => {
			const body = lines
				.filter(Boolean)
				.map((line) => `<div>${line}</div>`)
				.join("");
			if (!name && !body) return;
			$(`<div class="tp-contact ${extra || ""}"><div class="tp-kicker">${tp_esc(kicker)}</div>${
				name ? `<b>${tp_esc(name)}</b>` : ""
			}${body}</div>`).appendTo($grid);
		};
		card(__("Emergency"), "", [phone(contacts.emergency || "911")], "tp-sos");
		const office = contacts.office;
		if (office && typeof office === "object") {
			card(__("Office travel desk"), office.label || __("Travel desk"), [phone(office.phone), email(office.email)]);
		}
		const booked = contacts.booked_by;
		if (booked && typeof booked === "object") {
			card(__("Booked by"), booked.name, [phone(booked.phone), email(booked.email)]);
		}
		const lead = contacts.lead;
		if (lead && typeof lead === "object") card(__("Trip lead"), lead.name, [phone(lead.phone)]);
		const site = contacts.site;
		if (site && typeof site === "object") {
			card(__("Job site"), site.label, [
				site.contact_name ? tp_esc(site.contact_name) : "",
				phone(site.phone),
				email(site.email),
				site.address ? tp_esc(site.address) : "",
				web(tp_maps_directions(site.address), __("Directions")),
			]);
		}
		const stays = Array.isArray(hotels) ? hotels : Array.isArray(contacts.hotels) ? contacts.hotels : [];
		stays.forEach((hotel) => {
			if (!hotel || typeof hotel !== "object") return;
			card(__("Hotel"), hotel.name, [
				phone(hotel.phone),
				hotel.address ? tp_esc(hotel.address) : "",
				[web(tp_https(hotel.urgent_care_url), __("Nearest urgent care")), web(tp_https(hotel.directions_url), __("Directions"))]
					.filter(Boolean)
					.join(" &middot; "),
			]);
		});
	}

	// The hotels on one person's card, as `contacts` lists them: exactly what their /itinerary's
	// contacts show. The server says which (get_trip_views' `people_hotels`, by the rule
	// /itinerary uses: every room pinned to them or to the whole crew, dates or none). Rebuilding
	// it here from their check-ins left off a room with no dates yet, which has none. An answer
	// without the list falls back to the check-ins.
	person_hotels(data, employee) {
		const listed = data.people_hotels && data.people_hotels[employee];
		const names = new Set();
		if (Array.isArray(listed)) {
			listed.forEach((name) => names.add(name));
		} else {
			((data.people || {})[employee] || []).forEach((day) =>
				(day.items || []).forEach((item) => {
					if ((item.type === "hotel_checkin" || item.type === "hotel_checkout") && item.hotel) names.add(item.hotel);
				})
			);
		}
		const hotels = (data.contacts && Array.isArray(data.contacts.hotels) && data.contacts.hotels) || [];
		return hotels.filter((hotel) => hotel && names.has(hotel.name));
	}

	// ---- View as: exactly what one person's /itinerary shows

	view_person($view, data) {
		const employee = this.view_as;
		const person = (data.crew || []).find((c) => c.employee === employee);
		const name = (person && person.employee_name) || this.crew_name(employee);
		const $head = $('<div class="tp-view-head"></div>').appendTo($view);
		$(`<div class="tp-step-title">${__("What {0} sees", [tp_esc(name)])}</div>`).appendTo($head);
		const missing = this.as_missing;
		if (missing && missing.trip === data.trip && missing.shown === employee) {
			$(`<div class="tp-notice">${__(
				"The person in that link is not on the saved trip, so this shows {0} instead.",
				[tp_esc(name)]
			)}</div>`).appendTo($view);
		}
		if (data.itinerary_url) {
			$(`<a class="tp-btn" style="display:inline-flex;align-items:center;" target="_blank" rel="noopener" href="${tp_esc(
				`${data.itinerary_url}&as=${encodeURIComponent(employee)}`
			)}">${__("Open {0}'s phone view", [tp_esc(name)])} &#8599;</a>`).appendTo($head);
		}
		const days = (data.people || {})[employee];
		if (!days) {
			$(`<div class="tp-muted">${__("{0} is not on the saved trip yet.", [tp_esc(name)])}</div>`).appendTo($view);
			return;
		}
		// Their own sheet: the trip as their /itinerary shows it (the server filters it by `as`),
		// at the address the server gave for them, and only one of this site's. None while the
		// format is missing: the page never spells one the server did not send.
		const sheet = tp_local_url((data.people_sheet_urls || {})[employee]);
		if (sheet) this.sheet_link($head, __("Print {0}'s sheet", [name]), sheet);
		// Who to call, as their phone shows it: the hotels they sleep in, not everyone's.
		this.contacts_block($view, data.contacts, this.person_hotels(data, employee));
		this.render_preview($view, employee, name, data);
		this.person_trip_files($view, data, employee, name);
		if (!days.length) {
			$(`<div class="tp-muted">${__("Nothing on {0}'s itinerary yet.", [tp_esc(name)])}</div>`).appendTo($view);
			return;
		}
		const today = frappe.datetime.get_today();
		days.forEach((day) => {
			$(`<div class="tp-pday">${this.day_heading(day.date, data, today)}</div>`).appendTo($view);
			tp_by_time(day.items).forEach((item) => this.person_card($view, item));
		});
	}

	// One item as /itinerary draws it (public/js/travel/itinerary.js RENDERERS): the kicker,
	// the title, the lines under it, the number with a Copy button, the booking's files, and
	// a maps link for a stop that has a point.
	person_card($parent, item) {
		const kinds = {
			flight: () => ({
				kicker: [__("Flight"), item.airline],
				title: `${item.flight_number || ""}  ${item.departure_airport || "?"} → ${item.arrival_airport || "?"}`,
				sub: [tp_span(tp_clock(item.departure_time), tp_clock(item.arrival_time))],
				ref: [__("PNR"), item.booking_reference],
			}),
			hotel_checkin: () => ({
				kicker: [__("Hotel check-in"), tp_pretty_time(item.time)],
				title: item.hotel,
				sub: [item.address],
				ref: [__("Confirmation"), item.booking_confirmation],
			}),
			hotel_checkout: () => ({
				kicker: [__("Hotel check-out"), tp_pretty_time(item.time)],
				title: item.hotel,
				sub: [item.address],
				ref: [__("Confirmation"), item.booking_confirmation],
			}),
			ground: () => {
				const ride = TP_RIDE_TYPES.find((x) => x.value === item.transport_type);
				return {
					kicker: [ride ? ride.label : item.transport_type || __("Ground transport")],
					title: `${item.pickup_location || "?"} → ${item.dropoff_location || "?"}`,
					sub: [
						[item.provider, tp_span(tp_clock(item.pickup_datetime), tp_clock(item.arrival_datetime))]
							.filter(Boolean)
							.join(" · "),
						tp_clock(item.return_datetime)
							? __("Return by {0} ({1})", [tp_clock(item.return_datetime), tp_date_part(item.return_datetime)])
							: "",
						item.cargo ? __("Hauling: {0}", [item.cargo]) : "",
					],
					ref: [__("Confirmation"), item.booking_reference],
				};
			},
			freight: () => {
				const delivery = tp_span(tp_clock(item.delivery_from), tp_clock(item.delivery_to));
				const pickup = tp_span(tp_clock(item.pickup_from), tp_clock(item.pickup_to));
				return {
					kicker: [__("Freight"), item.carrier],
					title: item.contents || __("Shipment"),
					sub: [
						item.deliver_to || delivery
							? [__("Delivers"), delivery, item.deliver_to ? __("to {0}", [item.deliver_to]) : ""]
									.filter(Boolean)
									.join(" ")
							: "",
						item.ship_from || pickup
							? [
									__("Picked up"),
									pickup,
									item.pickup_from ? __("on {0}", [tp_date_part(item.pickup_from)]) : "",
									item.ship_from ? __("from {0}", [item.ship_from]) : "",
							  ]
									.filter(Boolean)
									.join(" ")
							: "",
						item.received_by ? __("Received by {0}", [item.received_by]) : "",
					],
					ref: [__("Tracking"), item.tracking_number],
				};
			},
			agenda: () => ({
				kicker: [__("Stop"), tp_span(tp_pretty_time(item.time), tp_pretty_time(item.end_time))],
				title: item.activity,
				sub: [[item.related_party, item.poi && item.poi.poi_name].filter(Boolean).join(" · "), item.visit_notes],
				ref: null,
			}),
		};
		const card = (kinds[item.type] || kinds.agenda)();
		const $card = $(`<div class="tp-pcard">
			<div class="tp-kicker">${TP_ICONS[item.type] || ""} ${tp_esc(card.kicker.filter(Boolean).join(" · "))}</div>
			<div class="tp-ptitle">${tp_esc(card.title || "")}</div>
			${card.sub
				.filter(Boolean)
				.map((line) => `<div class="tp-psub">${tp_esc(line)}</div>`)
				.join("")}
		</div>`).appendTo($parent);
		if (card.ref && card.ref[1]) {
			const value = String(card.ref[1]);
			const $ref = $(`<div class="tp-pnr"><span>${tp_esc(card.ref[0])}: ${tp_esc(value)}</span></div>`).appendTo($card);
			$(`<button class="tp-btn-link">${__("Copy")}</button>`)
				.appendTo($ref)
				.on("click", () => this.copy_text(value));
		}
		// The booking's files this person can see (a boarding pass in their name, the hotel
		// confirmation for everyone in the room). Never the cost row's receipt: that is money,
		// and shape_itinerary no longer sends it.
		this.person_files($card, item.documents);
		if (item.type === "agenda" && item.poi && item.poi.lat != null && item.poi.lng != null) {
			const point = `${Number(item.poi.lat)},${Number(item.poi.lng)}`;
			$(`<div><a target="_blank" rel="noopener" href="https://maps.google.com/?q=${encodeURIComponent(point)}">${__(
				"Open in Maps"
			)} &#8599;</a></div>`).appendTo($card);
		}
	}

	// View as: the trip's own files this person sees (for the whole crew, or for them), and a
	// link to the Documents screen of their phone view, which lists every file they can see.
	// The server lists each person's files (people_documents, shape_itinerary's own rule), so
	// nothing here decides who sees what; a file whose `group` is null is the whole trip's.
	person_trip_files($view, data, employee, name) {
		const docs = ((data.people_documents || {})[employee] || []).filter((doc) => !doc.group);
		if (!docs.length && !data.itinerary_url) return;
		const $sec = $(`<div class="tp-review-sec" style="max-width:640px;"><h5>${__("Files for the whole trip")}</h5></div>`).appendTo(
			$view
		);
		if (docs.length) this.person_files($sec, docs);
		else $(`<div class="tp-muted">${__("None for {0}.", [tp_esc(name)])}</div>`).appendTo($sec);
		if (data.itinerary_url) {
			$(`<div style="margin-top:8px;"><a class="tp-btn-link" target="_blank" rel="noopener" href="${tp_esc(
				`${data.itinerary_url}&as=${encodeURIComponent(employee)}&view=docs`
			)}">${__("All of {0}'s files, as their phone shows them", [tp_esc(name)])} &#8599;</a></div>`).appendTo($sec);
		}
	}

	// Files as View as lists them: one row each, big enough to tap, opening the file.
	person_files($parent, docs) {
		if (!(docs || []).length) return;
		const $list = $('<div class="tp-pdocs"></div>').appendTo($parent);
		docs.forEach((doc) => {
			const url = tp_file_url(doc.url);
			const kind = TP_DOC_KINDS.includes(doc.kind) ? doc.kind : "Other";
			const text = [doc.title || doc.file_name || __("File"), doc.for_name ? __("for {0}", [doc.for_name]) : ""]
				.filter(Boolean)
				.join(" · ");
			const inner = `${TP_DOC_ICONS[kind]} <span>${tp_esc(text)}</span>${url ? ` <b>${__("Open")} &#8599;</b>` : ""}`;
			$(
				url
					? `<a class="tp-pdoc" target="_blank" rel="noopener" href="${tp_esc(url)}">${inner}</a>`
					: `<div class="tp-pdoc">${inner}</div>`
			).appendTo($list);
		});
	}

	copy_text(text) {
		if (frappe.utils.copy_to_clipboard) {
			frappe.utils.copy_to_clipboard(text);
		} else if (navigator.clipboard && navigator.clipboard.writeText) {
			navigator.clipboard.writeText(text).then(() => frappe.show_alert({ message: __("Copied"), indicator: "green" }));
		}
	}

	// ---- View as: their itinerary email and calendar invite, rendered and never sent

	render_preview($view, employee, name, data) {
		const open = !!(this.preview && this.preview.employee === employee && this.preview.open);
		const $sec = $('<div class="tp-card tp-preview"></div>').appendTo($view);
		$(`<button class="tp-btn-link" aria-expanded="${open}">${open ? "&#9662;" : "&#9656;"} ${__(
			"Their itinerary email and calendar invite"
		)}</button>`)
			.appendTo($sec)
			.on("click", () => this.toggle_preview(employee));
		if (!open) {
			$(`<div class="tp-muted">${__("See exactly what {0} would get, before anything is sent.", [
				tp_esc(name),
			])}</div>`).appendTo($sec);
			return;
		}
		const preview = this.preview;
		if (preview.failed) {
			$(`<div class="tp-muted">${__("The preview could not be made.")}</div>`).appendTo($sec);
			return;
		}
		if (!preview.data) {
			$(`<div class="tp-muted">${__("Loading...")}</div>`).appendTo($sec);
			return;
		}
		const mail = preview.data;
		const to = mail.to_email ? `${mail.to_name || name} <${mail.to_email}>` : mail.to_name || name;
		$(`<dl>
			<dt>${__("Subject")}</dt><dd>${tp_esc(mail.subject || "")}</dd>
			<dt>${__("To")}</dt><dd>${tp_esc(to)}</dd>
		</dl>`).appendTo($sec);
		if (mail.no_email) {
			$(`<div class="tp-notice" style="margin-top:10px;">${__(
				"{0} has no email address on file, so nothing would be sent.",
				[tp_esc(name)]
			)}</div>`).appendTo($sec);
		}
		// A sandbox with nothing allowed: the email is shown, never run.
		$('<iframe sandbox=""></iframe>')
			.attr("title", __("Itinerary email"))
			.attr("srcdoc", mail.html || "")
			.appendTo($sec);
		$(`<h5 style="margin:6px 0 0;">${__("Calendar invite")}</h5>`).appendTo($sec);
		const events = mail.events || [];
		if (events.length) {
			$(`<ul class="tp-events">${events
				.map(
					(event) =>
						`<li><b>${tp_esc(event.summary || "")}</b> &middot; ${tp_esc(tp_event_when(event))}${
							event.location ? ` &middot; ${tp_esc(event.location)}` : ""
						}</li>`
				)
				.join("")}</ul>`).appendTo($sec);
		} else {
			$(`<div class="tp-muted">${__("No events.")}</div>`).appendTo($sec);
		}
		const $actions = $('<div class="tp-add"></div>').appendTo($sec);
		if (mail.ics) {
			$(`<button class="tp-btn">${__("Download the invite (.ics)")}</button>`)
				.appendTo($actions)
				.on("click", () => this.download_ics(mail.ics, mail.ics_filename));
		}
		const coordinator = data.is_coordinator || this.viewer.is_coordinator;
		const me = data.viewer_employee || this.viewer.employee;
		if (!mail.no_email && (coordinator || employee === me)) {
			$(`<button class="tp-btn tp-btn-primary">${__("Email this to {0}", [tp_esc(name)])}</button>`)
				.appendTo($actions)
				.on("click", () => this.send_itineraries(employee));
		}
	}

	// Open or close the preview. It is part of the View as screen, not a screen of its own,
	// so it makes no history entry; its answer is dropped once the page has moved on.
	toggle_preview(employee) {
		if (!this.state || !this.state.name) return;
		if (this.preview && this.preview.employee === employee && this.preview.open) {
			this.preview.open = false;
			this.render();
			return;
		}
		if (this.preview && this.preview.employee === employee && this.preview.data) {
			this.preview.open = true;
			this.render();
			return;
		}
		const seq = this.nav_seq;
		const asked = { employee: employee, open: true, data: null, failed: false };
		this.preview = asked;
		this.render();
		const done = (data) => {
			if (seq !== this.nav_seq || this.preview !== asked) return;
			asked.data = data && typeof data === "object" ? data : null;
			asked.failed = !asked.data;
			this.render();
		};
		frappe
			.call({
				method: "erpnext_enhancements.api.travel.preview_itinerary_email",
				args: { trip: this.state.name, employee: employee },
			})
			.then(
				(r) => done(r && r.message),
				() => done(null)
			);
	}

	// An open preview still waiting for its answer, when a move that stayed on this screen has
	// made toggle_preview drop that answer (route_view): ask for it again.
	retry_preview() {
		const preview = this.preview;
		if (!preview || !preview.open || preview.data || preview.failed) return;
		this.preview = null;
		this.toggle_preview(preview.employee);
	}

	download_ics(text, filename) {
		const blob = new Blob([text], { type: "text/calendar;charset=utf-8" });
		const url = URL.createObjectURL(blob);
		const link = document.createElement("a");
		link.href = url;
		link.download = filename || "itinerary.ics";
		document.body.appendChild(link);
		link.click();
		link.remove();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
}
