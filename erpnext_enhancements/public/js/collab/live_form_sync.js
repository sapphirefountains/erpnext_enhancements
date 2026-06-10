/**
 * live_form_sync.js — Google-Docs-style live collaborative editing for forms.
 *
 * Targets: desk forms of the doctypes in COLLAB_DOCTYPES (pilot: Task).
 * Loaded via: erpnext_enhancements.bundle.js (global desk bundle).
 *
 * While two or more users have the same document open:
 *   - every local field change is debounced (DEBOUNCE_MS per field) and POSTed
 *     to erpnext_enhancements.api.collab.broadcast_field_update, which
 *     permission-checks it and re-publishes "collab_field_update" to the
 *     document's realtime room (clients never emit to each other directly);
 *   - incoming changes are applied with frappe.model.set_value behind an
 *     origin/echo guard, so controls update live on every viewer's screen;
 *   - when anyone saves, collaborators silently adopt the saved state and the
 *     new `modified` timestamp (so their own save never trips
 *     TimestampMismatchError), keeping any local unsaved edits layered on top.
 *     Frappe's "document has been modified" conflict banner is suppressed for
 *     collab forms via a guarded prototype patch on show_conflict_message.
 *
 * Conflict model: last-write-wins per field. A field the local user is
 * actively typing in is never clobbered mid-keystroke — remote values for it
 * are parked and applied on blur only if no newer local edit happened.
 *
 * Child tables (v1): cell edits on already-saved rows sync live (matched by
 * row name); row add/remove propagates at the next save via the save-merge
 * (unsaved rows have per-client local names, so live row sync would need an
 * id-mapping protocol — deferred to v2).
 *
 * Presence ("currently viewing" avatars) is Frappe's built-in FormViewers —
 * nothing here touches it (which is also why teardown() never calls
 * doc_unsubscribe: the form lifecycle owns the room).
 */
(function () {
	if (window.__ee_live_form_sync_loaded) return;
	window.__ee_live_form_sync_loaded = true;

	// v2: move to a child table on ERPNext Enhancements Settings and ship via
	// extend_bootinfo. Keep in sync with COLLAB_DOCTYPES in api/collab.py
	// (the Python set is the security authority; this one only gates
	// client-side attachment). Before onboarding a new doctype, audit its form
	// scripts: field triggers with non-idempotent side effects (server calls)
	// fire on every receiving client when remote values are applied.
	const COLLAB_DOCTYPES = ["Task"];
	const DEBOUNCE_MS = 300;
	const CLIENT_ID =
		((frappe.session && frappe.session.user) || "unknown") +
		":" +
		Math.random().toString(36).slice(2, 12);

	// "doctype/docname" -> LiveFormSync. frappe.model.on has no off(), so
	// model observers are registered once per doctype and route through this
	// registry to the currently attached instance.
	const active_syncs = {};
	const observed = new Set();

	function values_equal(a, b) {
		if (a === b) return true;
		const norm = (v) => (v == null ? "" : String(v));
		return norm(a) === norm(b);
	}

	function ensure_observers(doctype) {
		if (observed.has(doctype)) return;
		observed.add(doctype);
		frappe.model.on(doctype, "*", (fieldname, value, doc) => {
			if (!doc || doc.doctype !== doctype) return;
			const sync = active_syncs[doctype + "/" + doc.name];
			sync && sync.on_local_change(fieldname, doc);
		});
		const meta = frappe.get_meta(doctype);
		((meta && meta.fields) || [])
			.filter((df) => frappe.model.table_fields.includes(df.fieldtype) && df.options)
			.forEach((df) => {
				const marker = doctype + "→" + df.options;
				if (observed.has(marker)) return;
				observed.add(marker);
				frappe.model.on(df.options, "*", (fieldname, value, doc) => {
					if (!doc || doc.parenttype !== doctype || !doc.parent) return;
					const sync = active_syncs[doctype + "/" + doc.parent];
					sync && sync.on_local_change(fieldname, doc);
				});
			});
	}

	class LiveFormSync {
		constructor(frm) {
			this.frm = frm;
			this.doctype = frm.doc.doctype;
			this.docname = frm.doc.name;
			this.debounce_timers = {}; // field key -> timeout id
			this.local_dirty = {}; // field keys edited locally since last adopted save
			this.pending_remote = {}; // field key -> {dt, dn, fieldname, value, at} parked while focused
			this.last_sent = {}; // field key -> last broadcast/applied value (loop breaker)
			this.last_local_edit_at = {}; // field key -> Date.now() of last local edit
			this.applying_remote = false;
			this._saving = false;
			this._degraded_notified = false;
			this._merge_chain = Promise.resolve();
			this._merge_queued = false;
		}

		_key(dt, dn, fieldname) {
			return dt + "|" + dn + "|" + fieldname;
		}

		attach() {
			active_syncs[this.doctype + "/" + this.docname] = this;
			ensure_observers(this.doctype);
			frappe.realtime.doc_subscribe(this.doctype, this.docname);
			this._bound_remote_field = (data) => this._on_remote_field(data);
			this._bound_doc_update = (data) => this._on_doc_saved(data);
			this._bound_reconnect = () => this._on_reconnect();
			frappe.realtime.on("collab_field_update", this._bound_remote_field);
			frappe.realtime.on("doc_update", this._bound_doc_update);
			frappe.realtime.on("connect", this._bound_reconnect);
		}

		teardown() {
			Object.values(this.debounce_timers).forEach(clearTimeout);
			this.debounce_timers = {};
			clearTimeout(this._saving_timer);
			frappe.realtime.off("collab_field_update", this._bound_remote_field);
			frappe.realtime.off("doc_update", this._bound_doc_update);
			frappe.realtime.off("connect", this._bound_reconnect);
			if (active_syncs[this.doctype + "/" + this.docname] === this) {
				delete active_syncs[this.doctype + "/" + this.docname];
			}
			if (this.frm._live_sync === this) delete this.frm._live_sync;
			// no doc_unsubscribe: the built-in form lifecycle (doc_open /
			// doc_close) owns the room and FormViewers depends on it.
		}

		// ---------------------------------------------------------- outbound

		on_local_change(fieldname, doc) {
			if (this.applying_remote || this._saving) return;
			if (!fieldname || fieldname.startsWith("__")) return;
			const is_child = doc.doctype !== this.doctype;
			// unsaved rows have per-client local names ("new-...") that can't
			// be matched on other clients; they propagate at the next save.
			if (is_child && doc.__islocal) return;
			const value = doc[fieldname];
			if (value != null && typeof value === "object") return; // scalars only
			const df = frappe.meta.get_docfield(doc.doctype, fieldname, doc.name);
			if (!df || frappe.model.no_value_type.includes(df.fieldtype)) return;
			const key = this._key(doc.doctype, doc.name, fieldname);
			// loop breaker — also swallows fetch_from / trigger echoes that
			// recompute the same value we just received or sent.
			if (values_equal(this.last_sent[key], value)) return;
			this.local_dirty[key] = true;
			this.last_local_edit_at[key] = Date.now();
			delete this.pending_remote[key]; // local edit supersedes a parked remote value
			clearTimeout(this.debounce_timers[key]);
			this.debounce_timers[key] = setTimeout(() => {
				delete this.debounce_timers[key];
				this._send(doc, fieldname, key);
			}, DEBOUNCE_MS);
		}

		_send(doc, fieldname, key) {
			const is_child = doc.doctype !== this.doctype;
			const value = doc[fieldname]; // read at fire time: latest value wins
			this.last_sent[key] = value;
			frappe.call({
				method: "erpnext_enhancements.api.collab.broadcast_field_update",
				type: "POST",
				args: {
					doctype: this.doctype,
					docname: this.docname,
					fieldname: fieldname,
					value: value,
					origin: CLIENT_ID,
					child_doctype: is_child ? doc.doctype : null,
					child_name: is_child ? doc.name : null,
				},
				error: (r) => {
					const exc_type =
						(r && r.exc_type) || (r && r.responseJSON && r.responseJSON.exc_type);
					if (exc_type === "PermissionError") {
						// write access revoked mid-session: stop broadcasting
						this.teardown();
						return;
					}
					if (!this._degraded_notified) {
						this._degraded_notified = true;
						frappe.show_alert(
							{
								message: __(
									"Live sync degraded — edits will still save normally."
								),
								indicator: "orange",
							},
							5
						);
					}
				},
			});
		}

		// ----------------------------------------------------------- inbound

		_on_remote_field(data) {
			if (!data || data.origin === CLIENT_ID) return;
			if (data.doctype !== this.doctype || data.docname !== this.docname) return;
			let target_dt = this.doctype;
			let target_dn = this.docname;
			if (data.child_doctype) {
				const row =
					locals[data.child_doctype] && locals[data.child_doctype][data.child_name];
				// unknown row (e.g. added remotely, not yet saved here): the
				// next save-merge delivers it.
				if (!row || row.parent !== this.docname) return;
				target_dt = data.child_doctype;
				target_dn = data.child_name;
			}
			const key = this._key(target_dt, target_dn, data.fieldname);
			const target = locals[target_dt] && locals[target_dt][target_dn];
			if (!target) return;
			if (values_equal(target[data.fieldname], data.value)) {
				this.last_sent[key] = data.value;
				delete this.pending_remote[key];
				return;
			}
			// a local edit inside the debounce window is newer — local wins
			if (this.debounce_timers[key]) return;
			if (
				this._is_locally_focused(
					data.fieldname,
					data.child_doctype ? data.child_name : null
				)
			) {
				this._defer_remote(key, {
					dt: target_dt,
					dn: target_dn,
					fieldname: data.fieldname,
					value: data.value,
					at: Date.now(),
				});
				return;
			}
			this._apply_remote(target_dt, target_dn, data.fieldname, data.value, key);
		}

		_apply_remote(dt, dn, fieldname, value, key) {
			// Correctness against echo loops rests on the value-equality
			// checks in both directions (last_sent here, on_local_change's
			// compare on the other side); applying_remote only short-circuits
			// the synchronous/microtask portion of set_value's trigger chain.
			// Async triggers (fetch_from, server calls) may finish after the
			// flag clears — their recomputed values broadcast redundantly and
			// are dropped by equality on every receiver.
			this.last_sent[key] = value;
			this.applying_remote = true;
			try {
				frappe.model.set_value(dt, dn, fieldname, value);
			} finally {
				setTimeout(() => {
					this.applying_remote = false;
				}, 0);
			}
		}

		_is_locally_focused(fieldname, child_name) {
			const active = document.activeElement;
			if (!active || active === document.body) return false;
			if (child_name) {
				const $active = $(active);
				return (
					$active.closest(".grid-row").attr("data-name") === child_name &&
					$active.closest("[data-fieldname]").attr("data-fieldname") === fieldname
				);
			}
			const control = this.frm.fields_dict[fieldname];
			if (!control || !control.$wrapper || !control.$wrapper.length) return false;
			return control.$wrapper[0].contains(active);
		}

		_defer_remote(key, info) {
			this.pending_remote[key] = info;
			$(document.activeElement).one("blur.ee_collab", () => {
				setTimeout(() => this._flush_pending(key), 0);
			});
		}

		_flush_pending(key) {
			const info = this.pending_remote[key];
			if (!info) return;
			delete this.pending_remote[key];
			// a newer local edit wins under last-write-wins
			if ((this.last_local_edit_at[key] || 0) > info.at) return;
			const target = locals[info.dt] && locals[info.dt][info.dn];
			if (!target || values_equal(target[info.fieldname], info.value)) return;
			this._apply_remote(info.dt, info.dn, info.fieldname, info.value, key);
		}

		// --------------------------------------------------------- save sync

		_is_visible() {
			return (
				frappe.get_route && frappe.get_route()[0] === "Form" && window.cur_frm === this.frm
			);
		}

		_on_doc_saved(data) {
			if (!data || data.doctype !== this.doctype || data.name !== this.docname) return;
			// our own save: the save response adopts the new state; events
			// arriving mid-save are ours echoing back.
			if (this._saving) return;
			if (data.modified === this.frm.doc.modified) return;
			if (!this.frm.is_dirty()) {
				// clean form: Frappe's built-in doc_update handler already
				// reloads it silently (visible) or flags __needs_refresh
				// (background) — just add awareness.
				if (this._is_visible()) this._notify_remote_save(null);
				return;
			}
			// dirty form: model.js flagged a conflict; merge silently instead.
			this.frm.doc.__needs_refresh = false;
			this._queue_merge();
		}

		_queue_merge() {
			if (this._merge_queued) return;
			this._merge_queued = true;
			this._merge_chain = this._merge_chain.then(() => {
				this._merge_queued = false;
				return this._sync_from_server();
			});
		}

		_sync_from_server() {
			return frappe
				.call({
					method: "frappe.client.get",
					args: { doctype: this.doctype, name: this.docname },
				})
				.then((r) => r && r.message && this._merge_saved(r.message))
				.catch(() => {
					// fetch failed (e.g. doc deleted): the form's own handling
					// of a vanished document applies; nothing to merge.
				});
		}

		_merge_saved(saved) {
			if (!saved || saved.name !== this.docname) return;
			const frm = this.frm;
			const meta = frappe.get_meta(this.doctype);
			const table_dfs = [];
			((meta && meta.fields) || []).forEach((df) => {
				if (frappe.model.table_fields.includes(df.fieldtype)) {
					table_dfs.push(df);
					return;
				}
				if (frappe.model.no_value_type.includes(df.fieldtype)) return;
				const key = this._key(this.doctype, this.docname, df.fieldname);
				const saved_val = saved[df.fieldname];
				if (values_equal(frm.doc[df.fieldname], saved_val)) {
					delete this.local_dirty[key];
					return;
				}
				if (this.local_dirty[key]) return; // genuine unsaved local edit — keep it
				if (this._is_locally_focused(df.fieldname, null)) {
					this._defer_remote(key, {
						dt: this.doctype,
						dn: this.docname,
						fieldname: df.fieldname,
						value: saved_val,
						at: Date.now(),
					});
					return;
				}
				this._apply_remote(this.doctype, this.docname, df.fieldname, saved_val, key);
			});

			table_dfs.forEach((df) => this._merge_table(df, saved[df.fieldname] || []));

			// adopt bookkeeping directly on the doc (NOT via set_value): this
			// is what lets the collaborator's next save pass check_if_latest().
			frm.doc.modified = saved.modified;
			frm.doc.modified_by = saved.modified_by;
			frm.doc.docstatus = saved.docstatus;

			if (
				!Object.keys(this.local_dirty).length &&
				!Object.keys(this.pending_remote).length
			) {
				// fully converged with the saved state: clear "Not Saved"
				frm.doc.__unsaved = 0;
				if (typeof frm.refresh_header === "function") frm.refresh_header();
			}
			this._notify_remote_save(saved.modified_by);
		}

		_merge_table(df, saved_rows) {
			const frm = this.frm;
			const parentfield = df.fieldname;
			const local_rows = frm.doc[parentfield] || [];
			// rows the local user is still composing survive the adoption
			const local_new_rows = local_rows.filter((row) => row.__islocal);
			// unsaved local cell edits on saved rows get re-applied by row name
			const dirty_cells = [];
			Object.keys(this.local_dirty).forEach((key) => {
				const [dt, dn, fieldname] = key.split("|");
				if (dt !== df.options) return;
				const row = locals[dt] && locals[dt][dn];
				if (row && row.parent === this.docname && row.parentfield === parentfield) {
					dirty_cells.push({ dn, fieldname, value: row[fieldname], key });
				}
			});

			frappe.model.clear_table(frm.doc, parentfield);
			saved_rows.forEach((row) => {
				locals[row.doctype] = locals[row.doctype] || {};
				locals[row.doctype][row.name] = row;
				frm.doc[parentfield].push(row);
			});
			local_new_rows.forEach((row) => {
				locals[row.doctype] = locals[row.doctype] || {};
				locals[row.doctype][row.name] = row; // clear_table dropped it from locals
				frm.doc[parentfield].push(row);
				row.idx = frm.doc[parentfield].length;
			});
			dirty_cells.forEach((cell) => {
				const row = locals[df.options] && locals[df.options][cell.dn];
				if (!row || row.parent !== this.docname) {
					// the save deleted this row; the local edit goes with it
					delete this.local_dirty[cell.key];
					return;
				}
				if (values_equal(row[cell.fieldname], cell.value)) {
					delete this.local_dirty[cell.key];
				} else {
					row[cell.fieldname] = cell.value;
				}
			});
			frm.refresh_field(parentfield);
		}

		_notify_remote_save(modified_by) {
			const show = (user) => {
				if (!user || user === frappe.session.user) return;
				const info = frappe.user_info(user);
				frappe.show_alert(
					{
						message: __("Updated by {0}", [(info && info.fullname) || user]),
						indicator: "blue",
					},
					3
				);
			};
			if (modified_by) {
				show(modified_by);
				return;
			}
			frappe.db
				.get_value(this.doctype, this.docname, "modified_by")
				.then((r) => show(r && r.message && r.message.modified_by));
		}

		// ------------------------------------------------- local save events

		on_local_before_save() {
			this._saving = true;
			clearTimeout(this._saving_timer);
			// before_save has no matching "save failed" event; the timer keeps
			// a failed save from muting remote doc_update events forever.
			this._saving_timer = setTimeout(() => {
				this._saving = false;
			}, 15000);
		}

		on_local_save() {
			clearTimeout(this._saving_timer);
			this._saving = false;
			Object.values(this.debounce_timers).forEach(clearTimeout);
			this.debounce_timers = {};
			this.local_dirty = {};
			this.pending_remote = {};
			this.last_sent = {};
		}

		// ----------------------------------------------------- reconnect

		_on_reconnect() {
			frappe.realtime.doc_subscribe(this.doctype, this.docname);
			// events may have been missed while offline — resync from server
			if (this.frm.is_dirty()) {
				this._queue_merge();
			} else if (this._is_visible() && typeof this.frm.debounced_reload_doc === "function") {
				this.frm.debounced_reload_doc();
			} else {
				this.frm.doc.__needs_refresh = true;
			}
		}
	}

	function attach_or_reattach(frm) {
		// only saved, editable (draft) docs sync; new docs have no room or name
		if (frm.is_new() || frm.doc.docstatus !== 0) {
			if (frm._live_sync) frm._live_sync.teardown();
			return;
		}
		if (frm._live_sync && frm._live_sync.docname === frm.doc.name) return;
		if (frm._live_sync) frm._live_sync.teardown(); // form object reused for another docname
		frm._live_sync = new LiveFormSync(frm);
		frm._live_sync.attach();
	}

	COLLAB_DOCTYPES.forEach((doctype) => {
		// appends to any existing doctype_js handlers; does not replace them
		frappe.ui.form.on(doctype, {
			refresh(frm) {
				attach_or_reattach(frm);
			},
			before_save(frm) {
				frm._live_sync && frm._live_sync.on_local_before_save();
			},
			after_save(frm) {
				frm._live_sync && frm._live_sync.on_local_save();
			},
		});
	});

	// Collab forms merge remote saves silently; suppress the built-in
	// "document has been modified" conflict banner for them only.
	if (
		frappe.ui.form.Form &&
		frappe.ui.form.Form.prototype.show_conflict_message &&
		!frappe.ui.form.Form.prototype.__ee_collab_conflict_patch
	) {
		frappe.ui.form.Form.prototype.__ee_collab_conflict_patch = true;
		const orig_show_conflict = frappe.ui.form.Form.prototype.show_conflict_message;
		frappe.ui.form.Form.prototype.show_conflict_message = function () {
			if (this._live_sync) return;
			return orig_show_conflict.apply(this, arguments);
		};
	}
})();
