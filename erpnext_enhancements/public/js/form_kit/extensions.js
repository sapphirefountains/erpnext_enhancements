/*
 * form kit P8.1: the guided page's extension point. A DocType whose flow needs more than plain
 * fields on a step (the Item flow's name builder, its supplier, stock and asset blocks) registers
 * one extension here, and the page asks for it by DocType. The page stays generic: it knows only
 * this registry and the small interface below, never what an Item is.
 *
 * A config step names its block with `extension` (form_kit/config, flow_steps passes it through
 * as step.extension). For that step the page calls:
 *
 *   ext.render(name, $box, ctx)          draw block `name` into $box, above the step's fields
 *   ext.on_change(fieldname, value, ctx) a field on the step changed (optional)
 *   ext.leave()                          the step or the record is going away (optional)
 *   ext.before_create()                  step 1 is about to create the record: "" lets it go, any
 *                                        other text holds it back and is shown (optional)
 *   ext.created(record)                  the create went through: { name, ... } (optional; never
 *                                        called for a create that failed)
 *
 * and `ctx` is what the page lends a block:
 *
 *   ctx.doctype, ctx.name, ctx.is_new, ctx.step, ctx.sf (sf_kit), ctx.kit (sf_form_kit)
 *   ctx.values()               the record's values as the page holds them
 *   ctx.carried()              a new record's start values for later steps (route options the
 *                              create sends along); {} for a saved record or an older editor
 *   ctx.started()              every start value of a new record, ones no field takes included
 *                              (a buying line's supplier and rate); {} likewise
 *   ctx.set_value(f, v)        put a value into a field, on screen and in what the page saves
 *   ctx.save_first()           send what the page still holds; resolves when the record is saved
 *   ctx.record_changed()       the block wrote to the record on the server: take its new version
 *   ctx.can_write              whether this person may change the record
 *
 * A block writes through its own whitelisted endpoints, never through the page's save, and calls
 * save_first() before and record_changed() after, so the page's next save is not a conflict.
 * An extension that throws is caught by the page: the step's own fields always show.
 *
 * Pure: no frappe, no DOM.
 */

const registry = {};

// `factory` makes a fresh extension object each time a record of `doctype` opens.
export function register_extension(doctype, factory) {
	if (!doctype || typeof factory !== "function") return;
	registry[doctype] = factory;
}

// A new extension for `doctype`, or null when it has none (Opportunity) or it failed to start.
export function extension_for(doctype) {
	const factory = registry[doctype];
	if (!factory) return null;
	try {
		return factory() || null;
	} catch (e) {
		return null;
	}
}

export function has_extension(doctype) {
	return Object.prototype.hasOwnProperty.call(registry, doctype);
}
