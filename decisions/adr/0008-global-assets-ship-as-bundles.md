# 0008. Global browser assets ship as esbuild bundles, not raw `/assets` paths

- **Status:** Accepted
- **Date:** 2026-07-29 (recorded retroactively)

## Context

Frappe lets an app include global desk assets either as a raw path
(`/assets/erpnext_enhancements/js/thing.js`) or as an esbuild bundle (`thing.bundle.js`,
resolved through `assets.json` to a content-hashed filename).

Raw `/assets` paths are served with a **one-year immutable `Cache-Control`** and carry no
content hash. So an edit to one never reaches a device that already cached it. Ever — not on
the next deploy, not on a hard refresh.

That produced the v0.8.1 bug: a Kanban fix that worked on every developer's desktop and left
every phone in the field still broken, with no error anywhere to explain it.

## Decision

Every global asset ships as a bundle. `app_include_css` and `app_include_js` in `hooks.py`
reference `name.bundle.css` / `name.bundle.js`, and the content hash makes cache invalidation
automatic.

Two deliberate exceptions, both vendored global-defining UMD libraries — `vue.global.js` and
`frappe-gantt.umd.js`:

- Importing a UMD build from an esbuild bundle **captures its exports** instead of letting it
  set `window.Vue` / `window.Gantt`, which is the entire reason those files exist.
- Their content never changes, so the immutable cache cannot serve them stale.

They are loaded first, so the globals exist before any bundled consumer runs.

Per-module browser assets live under `public/{js,css}/<module>/` to avoid collisions after the
app merge.

## Consequences

- **A raw `/assets` path in `hooks.py` is a bug**, even when it works in testing — desktop
  browsers you have just cleared will show you the new file while field devices will not.
- Adding a global script means adding it to the relevant bundle entry
  (`public/js/erpnext_enhancements.bundle.js`, `kanban.bundle.js`, …), not adding a new
  include line.
- `.scss` entries build to a `.css` asset name — `desk_addons.bundle.scss` is referenced as
  `desk_addons.bundle.css` — because its imports must be inlined by sass rather than esbuild.
- Include **order** is preserved deliberately in the bundle entry files, because the CSS
  cascade depends on it. The comments in `hooks.py` record that; keep them.
- A stale-asset symptom that reproduces only on some devices is almost always this. Check the
  include style before debugging the code.
- The standalone PWAs (`/kiosk`, `/wall`) sit outside the bundle mechanism, so they version
  their assets and service workers off the shared per-deploy token in `utils/deploy.py`.
