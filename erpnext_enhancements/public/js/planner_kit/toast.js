/*
 * Planner kit: a small notice along the bottom with at most one action, e.g. "Dig moved to Thu,
 * Oct 15 · Undo". One at a time: a new toast replaces the one showing, sf_kit's included (one per
 * document, v1.632.0). It goes after `timeout` ms (8 s by default: long enough to reach Undo),
 * stays while the pointer is over it or focus is inside it, hands focus back when it closes, and
 * is announced politely to a screen reader.
 *
 *   const toast = planner_kit.toast("Dig moved to Thu, Oct 15", {
 *     action_label: "Undo", on_action: () => ..., timeout: 8000, tone: "success" | "warning" | "info",
 *   });
 *   toast.close();  planner_kit.toast.close();
 *
 * The implementation is sf_kit's (public/js/sf_kit/toast.js); this keeps the planners' pk- class
 * names, which the pk-toast rules in styles.js are written against. scripts/test_sf_kit.mjs holds
 * the markup to what this file drew before the move, byte for byte.
 */

import { create_toast as create_sf_toast } from "../sf_kit/toast.js";

export function create_toast(env) {
	return create_sf_toast(env, { prefix: "pk" });
}
