// The page's server calls, by the names in docs/fountain-designer/PLAN.md 3.4, and the live derive's
// debounce. Pure: the transport (sf_kit.call, or the demo fixture) and the timers come in from the page,
// so scripts/test_fountain_designer.mjs drives both under node.

export const METHOD_BASE = "erpnext_enhancements.water_engineering.api.designer.";

// The access check the old wizard uses (read on Water Feature Design). It is already on every site, so
// the page gates on it before the designer's own endpoints exist (phase E).
export const PERMISSION_METHOD = "erpnext_enhancements.water_engineering.api.water_design.check_permission";

export const METHODS = [
	"boot",
	"derive",
	"get_design",
	"save_design",
	"push_quotation",
	"push_material_request",
	"catalog_search",
	"acknowledge_issue",
	"email_draft",
];

// Which calls only read: they go as GET on the quiet path. derive is a read too, but its model can be
// tens of kilobytes, too long for a query string, so it is a POST like the writes.
const GET_METHODS = new Set(["boot", "get_design", "catalog_search"]);

const json = (value) => (value == null ? null : typeof value === "string" ? value : JSON.stringify(value));

// transport(method, args, opts) -> Promise. The demo passes its own transport and answers every name in
// METHODS from the fixture; check_permission still goes to the server in demo mode (the page's gate).
export function create_api(transport) {
	const send = (name, args) =>
		transport(METHOD_BASE + name, args || {}, { quiet: true, method: GET_METHODS.has(name) ? "GET" : "POST" });
	return {
		check_permission: () => transport(PERMISSION_METHOD, {}, { quiet: true }),
		boot: () => send("boot"),
		derive: (model) => send("derive", { model_json: json(model) }),
		// No name: the designs the person may read, newest first ({ designs: [...] }). A name: that design.
		list_designs: () => send("get_design", {}),
		get_design: (name) => send("get_design", { name }),
		// name null creates the design. fields: the record's own fields (design_title, project,
		// opportunity, customer), sent beside the model because they are not part of it.
		save_design: (name, model, fields) =>
			send("save_design", { name: name || null, model_json: json(model), fields_json: json(fields || {}) }),
		push_quotation: (name, quotation) => send("push_quotation", { name, quotation: quotation || null }),
		push_material_request: (name) => send("push_material_request", { name }),
		catalog_search: (equipment_class, q) => send("catalog_search", { equipment_class, q: q || "" }),
		acknowledge_issue: (design, issue_key, note) => send("acknowledge_issue", { design, issue_key, note: note || "" }),
		// The start of an email sending one paper of a saved design, with that paper as a PDF File on the design.
		email_draft: (name, print_format) => send("email_draft", { name, print_format: print_format || null, attach_pdf: 1 }),
	};
}

// The live derive. Every edit calls schedule(model); the request goes once the person has paused for
// `delay` ms, and only the reply to the newest model is drawn: an edit made after a request went out, or
// a later request, makes the earlier reply stale and it is dropped, never painted over the newer state
// (the wizard's load_design_state guard). opts:
//   derive(model) -> Promise of the reply
//   delay (400), set_timeout, clear_timeout
//   on_pending(), on_result(reply, model), on_error(err, model): each called only for the current model
export function create_deriver(opts) {
	const delay = opts.delay == null ? 400 : opts.delay;
	const set_timeout = opts.set_timeout || ((fn, ms) => setTimeout(fn, ms));
	const clear_timeout = opts.clear_timeout || ((id) => clearTimeout(id));
	let seq = 0;
	let timer = null;
	let latest = null;
	let in_flight = 0;
	const counts = { sent: 0, drawn: 0, dropped: 0 };

	function fire() {
		timer = null;
		const mine = seq;
		const model = latest;
		counts.sent += 1;
		in_flight += 1;
		if (opts.on_pending) opts.on_pending(model);
		let reply;
		try {
			reply = Promise.resolve(opts.derive(model));
		} catch (err) {
			reply = Promise.reject(err);
		}
		reply.then(
			(result) => {
				in_flight -= 1;
				if (mine !== seq) return void (counts.dropped += 1);
				counts.drawn += 1;
				if (opts.on_result) opts.on_result(result, model);
			},
			(err) => {
				in_flight -= 1;
				if (mine !== seq) return void (counts.dropped += 1);
				if (opts.on_error) opts.on_error(err, model);
			}
		);
	}

	return {
		schedule(model, wait) {
			seq += 1;
			latest = model;
			if (timer !== null) clear_timeout(timer);
			timer = set_timeout(fire, wait == null ? delay : wait);
		},
		// Right away: the first derive of a design that has just opened.
		now(model) {
			this.schedule(model, 0);
		},
		// Drop whatever is waiting or in flight: the design on screen changed.
		cancel() {
			seq += 1;
			if (timer !== null) clear_timeout(timer);
			timer = null;
		},
		busy: () => timer !== null || in_flight > 0,
		counts: () => Object.assign({}, counts),
	};
}

// A trailing debounce for the autosave and the catalog typeahead, with flush() to run a waiting call at
// once (a step change, leaving the design) and cancel().
export function debounce(fn, delay, set_timeout, clear_timeout) {
	const st = set_timeout || ((f, ms) => setTimeout(f, ms));
	const ct = clear_timeout || ((id) => clearTimeout(id));
	let timer = null;
	let args = null;
	const run = () => {
		timer = null;
		const a = args;
		args = null;
		return fn.apply(null, a || []);
	};
	const call = (...next) => {
		args = next;
		if (timer !== null) ct(timer);
		timer = st(run, delay);
	};
	call.flush = () => {
		if (timer === null) return undefined;
		ct(timer);
		return run();
	};
	call.cancel = () => {
		if (timer !== null) ct(timer);
		timer = null;
		args = null;
	};
	call.pending = () => timer !== null;
	return call;
}
