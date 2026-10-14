/*
 * scanner_kit: every module's exports in one place, and `install(win)`, which scanner_kit.bundle.js
 * calls with the page's window. Importing this file does nothing: no DOM, no frappe, no global.
 *
 * install(win) sets `win.sf_scanner` (once; a newer version replaces an older one):
 *
 *   version
 *   sheets({route, names, mark, back_timeout_ms})   a page's sheets as route segments (sheets.js)
 *   camera.available()                              getUserMedia is there
 *   camera.prepare({unavailable, no_reader})        -> Promise of a reader, or null (said why)
 *   camera.reader()                                 -> Promise of a reader, or null (says nothing)
 *   camera.show({nav, reader, page, ...})           the Camera Scan sheet (camera.js)
 *   camera.failure_message(err)
 *   QR_DECODER                                      the vendored jsQR's path
 */

import { make_env } from "./env.js";
import { create_sheets } from "./sheets.js";
import { QR_DECODER, QR_FRAME_WIDTH, QR_RETRY_TIMEOUT_MS, create_reader, load_jsqr, supported_formats } from "./reader.js";
import { camera_available, failure_message, prepare_camera, show_camera } from "./camera.js";

export { make_env, create_sheets, QR_DECODER, QR_FRAME_WIDTH, QR_RETRY_TIMEOUT_MS, create_reader, load_jsqr, supported_formats };
export { camera_available, failure_message, prepare_camera, show_camera };

// 1 (v1.666.0): the first version.
export const VERSION = 1;

export function install(win) {
	if (!win) return null;
	if (win.sf_scanner && win.sf_scanner.version >= VERSION) return win.sf_scanner;
	const env = make_env(win);
	win.sf_scanner = {
		version: VERSION,
		sheets: (opts) => create_sheets(env, opts),
		camera: {
			available: () => camera_available(env),
			prepare: (opts) => prepare_camera(env, opts),
			reader: () => create_reader(env),
			show: (opts) => show_camera(env, opts),
			failure_message,
		},
		QR_DECODER,
	};
	return win.sf_scanner;
}
