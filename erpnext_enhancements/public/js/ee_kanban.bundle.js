/*
 * Kanban patch suite — single esbuild bundle (content-hashed filename).
 *
 * WHY A BUNDLE: these four scripts used to be listed individually in hooks.py
 * `app_include_js` as raw `/assets/erpnext_enhancements/js/...` paths. Raw asset
 * URLs never change between deploys, and the server (Frappe Cloud, same as the
 * stock bench nginx template) serves `/assets` with
 * `Cache-Control: max-age=31536000, immutable` — so a browser keeps executing
 * the FIRST copy of each file it ever downloaded, for up to a year, without
 * even revalidating on a normal reload. That is how the hold-to-drag fix could
 * work on a hard-refreshed desktop while every phone/tablet still ran a stale
 * pre-fix copy that grabbed cards instantly mid-scroll. `*.bundle.js` entries
 * are built by frappe's esbuild with a content hash in the filename (resolved
 * through assets.json, same as desk_enhancements.bundle.css), so every deploy
 * gets a new URL and every device picks up the new code on its next page load.
 *
 * Import order mirrors the old hooks.py order. Each file is a self-contained
 * IIFE; see each file's header for what it patches and when it can be removed:
 *   - kanban_leak_fix.js    -> remove once upstream frappe/frappe#24156 ships
 *                              in our deployed frappe version.
 *   - kanban_scroll_perf.js -> remove once frappe core stops reading offsetLeft
 *                              on mousemove in bind_clickdrag.
 */
import "./kanban_patches.js";
import "./kanban_customization.js";
import "./kanban_leak_fix.js";
import "./kanban_scroll_perf.js";
