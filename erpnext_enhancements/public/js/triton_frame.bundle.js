/**
 * triton_frame.bundle.js: the framed Triton widget's loader (v1.802.0).
 *
 * Its own bundle, and never in `app_include_js`: it is fetched by
 * `global_enhancements/triton_frame_gate.js` only for a person whose boot carries
 * `frappe.boot.triton_frame`, i.e. only for someone on the framed widget. The Desk's main bundle
 * does not grow by it, and on the classic widget none of it is downloaded or run.
 *
 * It defines one thing, `window.SapphireTritonFrame.start({ config, giveUp })`, which builds the
 * button and the panel and returns `{ toggle, ask, destroy, isOpen, isReady }`. It does not start
 * by itself: the gate calls `start`, so the gate alone decides whether the framed widget or the
 * classic one owns the button (and it has already decided "classic" if this file arrived late).
 *
 * The code is in `triton_frame/`; see `loader.js` for the rules it keeps.
 */
import { detectPageContext } from "./capture/page_context.js";
import { createLoader, validConfig } from "./triton_frame/loader.js";

window.SapphireTritonFrame = {
	start({ config, giveUp }) {
		if (!validConfig(config)) {
			giveUp("config", { open: false, ask: null });
			return null;
		}
		return createLoader({ win: window, config: config, giveUp: giveUp, detect: detectPageContext });
	},
};
