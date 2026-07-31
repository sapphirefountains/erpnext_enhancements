# PDF generation — diagnosis and runbook

PDF generation is broken on production. **Both** backends fail, for different reasons, so
there is no working path at all: the default one cannot start its browser, and the manual
fallback is not installed.

This is an **environment** problem, not an application one. Nothing in this repo can fix
it; the commands below have to be run on the VM. The repo change that accompanies this
document is the codified package list (`infra/`) so a rebuilt VM inherits the fix.

## What is actually wrong

Verified against production on 2026-07-31.

| Fact | Value |
|---|---|
| Frappe / ERPNext | 16.25.0 / 16.26.2 |
| `Print Settings.pdf_generator` | `chrome` |
| `pdf_generator` hook | `frappe.utils.pdf.get_chrome_pdf` (the only one registered) |
| `pdf_generator` in `site_config.json` / `common_site_config.json` | **not set** |
| Page size | Letter, `with_letterhead = 1`, `repeat_header_footer = 1` |
| Letter Head | `Sapphire Fountains Default`, default, image `/private/files/Logo-cropped.png` |

Two distinct failures in the Error Log:

| Error | Backend | Occurrences | Window |
|---|---|---|---|
| `Chromium took too long to start.` | `pdf_generator=chrome` — **the default path** | 2 | 2026-07-27 |
| `No wkhtmltopdf executable found: "b''"` | `pdf_generator=wkhtmltopdf` — chosen manually in the print view | 9 | 2026-07-20 → 07-28 |

The volume is low because the failure is total: people try once, it fails, they stop. One
of those nine is literally
`download_pdf?doctype=Purchase Order&name=PO-2026-00215&format=PO Test Print Format` —
somebody attempting exactly what the Purchase Order print-format work needs.

### Why Chromium

Frappe 16 does not shell out to a binary for the chrome backend. It drives a headless
Chromium over the **DevTools Protocol**: `frappe/utils/pdf_generator/browser.py`'s
`Browser.open()` resolves a devtools URL and connects a CDP client to it (the launcher
lives in `pdf_generator/cdp_connection.py`). `Chromium took too long to start.` is what
that raises when the browser never becomes reachable.

> **Correction, 2026-07-31 — the binary is bench-managed, not a system package.**
>
> An earlier revision of this runbook told you to `apt-get install chromium` and to
> diagnose `/usr/bin/chromium`. **That was wrong, and installing the system package does
> not help the chrome backend at all.** Frappe ships and manages its own headless build
> under the bench:
>
> ```
> /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell
> ```
>
> Verified on production: Frappe logs *"Chromium executable is already executable:
> /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell"* and then still times
> out. So the binary is present and executable — this is the **"cannot launch"** branch,
> not the "missing" one, and every diagnostic has to point at *that* path.
>
> The system package is not entirely wasted: it drags in the shared libraries
> `headless_shell` needs (`libnss3`, `libatk-1.0`, `libgbm1`, `libasound2`, …), which is
> one of the three things that stop it starting. But it is a side effect, not the fix.

### The chrome setting does not cover server-side callers

Worth knowing before assuming a working chrome backend fixes everything.
`Print Settings.pdf_generator = "chrome"` governs the **HTTP `download_pdf` path** — the
Download PDF button, which puts `&pdf_generator=chrome` in the URL.

It does **not** govern `frappe.get_print(as_pdf=True)` or `frappe.attach_print`. Verified
on production: `frappe.get_print(..., as_pdf=True)` with no explicit generator raises the
*wkhtmltopdf* error, while the same call with `pdf_generator="chrome"` raises the Chromium
timeout. Server-side callers must pass the generator explicitly or they fall through to
wkhtmltopdf.

Two callers in this app do exactly that, which is why **wkhtmltopdf is still required even
once Chromium works**:

- `api/maintenance_workflow.py` → `frappe.attach_print` inside a `sendmail` — customer
  maintenance reports.
- `project_enhancements/esign/lifecycle.py` → `frappe.utils.pdf.get_pdf` — signed contract
  PDFs.

Both swallow the failure, so they have been degrading silently rather than erroring.

So the question the runbook has to answer is *which cause* — a binary that cannot launch
(sandbox, missing shared libraries, no `/dev/shm`), or one that launches too slowly under
memory pressure. Same error message for both.

### Why wkhtmltopdf

`No wkhtmltopdf executable found: "b''"` — the empty `b''` is `which wkhtmltopdf`
returning nothing. It is simply not installed.

### Root cause of both

`infra/variables.tf` provisioned the VM with:

```
["curl", "git", "nginx", "python3", "python3-pip", "python3-venv", "pipx"]
```

No browser, no wkhtmltopdf, no font packages. Nothing anywhere in this repo installs
either. The bench was built on a host that never had a PDF toolchain.

> **Note on `infra/configs/startup_script.sh`:** the `apt-get install` is guarded behind
> `SKIP_FIRST_BOOT`, so it runs *only on first boot*. Adding packages to the Terraform
> variable does **not** retrofit the running VM — it only means a rebuilt one starts
> correct. The running VM needs the manual install below regardless. That is why this is
> two changes, not one.

## Runbook

Run on the production VM (`production-erpnext-standard-vm`, us-east4-a) as a user with
sudo. **Diagnose first** — steps 1–3 are read-only and decide which fix applies.

### 1. Confirm the bench-managed binary, not a system one

```bash
ls -la /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell; command -v wkhtmltopdf || echo "wkhtmltopdf MISSING"
```

As of 2026-07-31 the first exists and is executable; the second is missing.

### 2. Make it start by hand — this is the step that names the cause

Run it as `frappe`, against the real path. Whatever it prints on failure *is* the
diagnosis, and it is far more specific than the timeout Frappe surfaces:

```bash
sudo -u frappe /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell --headless --no-sandbox --disable-gpu --dump-dom about:blank
```

- `error while loading shared libraries: …` → **missing library**, go to step 4.
- `Failed to move to new namespace` / sandbox complaints → it needs `--no-sandbox`; if
  the command above works *only* with that flag, the fix is enabling user namespaces
  (`sudo sysctl -w kernel.unprivileged_userns_clone=1`, persisted in `/etc/sysctl.d/`).
- Hangs or is very slow → memory or `/dev/shm`, step 3.

Check the libraries and `/dev/shm` directly. A headless Chromium on the default 64 MB
`/dev/shm` crashes on anything non-trivial:

```bash
ldd /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell | grep -i "not found"; df -h /dev/shm
```

### 3. Confirm memory headroom

Chromium needs a few hundred MB to start. Under pressure it starts *slowly*, which
presents as exactly the timeout being seen:

```bash
free -m; ps -o rss=,comm= -C headless_shell 2>/dev/null
```

### 4. Install what step 2 said was missing

The bench's own `headless_shell` needs the same shared libraries a system Chromium does,
and the fonts are needed regardless of backend:

```bash
sudo apt-get update && sudo apt-get install -y libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdrm2 libgbm1 libasound2t64 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 fonts-liberation fonts-dejavu-core fontconfig
```

(`libasound2t64` on Ubuntu 24.04+; on older releases it is `libasound2`.) Installing the
`chromium` package pulls most of these in as dependencies, which is a legitimate shortcut —
just don't expect Frappe to *use* that binary.

Fonts are not optional. Without them a headless browser renders boxes or silently
substitutes, which produces a PDF that generates "successfully" and looks wrong — a worse
failure than the current one, because nobody gets an error.

Re-run step 2. It must succeed before moving on.

### 5. Install wkhtmltopdf — not optional, despite the chrome setting

Two things need it, and neither is covered by `Print Settings.pdf_generator = "chrome"`
(see *The chrome setting does not cover server-side callers* above): customer maintenance
report emails (`attach_print`) and signed contract PDFs (`esign/lifecycle.py`). Both fail
silently today. The print view also lets a user pick `wkhtmltopdf` explicitly, and nine
people already have.

```bash
sudo apt-get install -y wkhtmltopdf
```

**Check it is the patched-Qt build** — the unpatched one silently drops headers, footers
and page breaks, which is precisely what a letterhead and "Page X of Y" need:

```bash
wkhtmltopdf --version
```

Look for `with patched qt`. If it is absent, install the `.deb` from the wkhtmltopdf
releases page for this Ubuntu release rather than the distro package.

### 6. Restart the bench so workers pick up the new PATH

```bash
sudo systemctl restart frappe-bench
```

### 7. Verify

From the desk, print one document per format and download it as an actual PDF — not the
browser preview. Then confirm the Error Log has stopped collecting new rows:

```bash
sudo -u frappe bench --site erp.sapphirefountains.com console
```

```python
frappe.db.sql("""SELECT method, COUNT(*) n, MAX(creation) latest FROM `tabError Log`
WHERE (method LIKE '%%wkhtmltopdf%%' OR method LIKE '%%Chromium%%') AND creation > NOW() - INTERVAL 1 DAY
GROUP BY method""", as_dict=True)
```

## Acceptance

Not done until each of these produces a real PDF file:

1. Purchase Order, Sales Invoice, Quotation, and one custom format (e.g. `Project
   Contract Print`).
2. Letterhead appears; `repeat_header_footer` is on, so headers and footers must repeat
   across pages and page numbers must be right.
3. A multi-page document breaks cleanly — no cut-off rows.
4. The PDF matches the HTML preview.
5. A large document completes inside the worker timeout.

## Related

- The **Purchase Order print format** work is blocked on this. A print format cannot be
  signed off on a browser preview, and the one existing attempt at a PO PDF is among the
  nine logged failures.
- `project_enhancements/esign/lifecycle.py` builds the signed-contract PDF and is wrapped
  in a bare `try/except` that logs and returns `None`, so contract e-signature has been
  degrading silently rather than erroring. Worth re-testing once this is fixed.
- `api/maintenance_workflow.py` calls `frappe.attach_print` inside a `sendmail`, so
  customer maintenance reports have been going out without their attachment.

## Verification log

Everything below was established from the production database and the deployed Frappe
source, with no shell on the VM.

**2026-07-31, first pass.** Both backends failing, confirmed from the Error Log. Cause of
the wkhtmltopdf failure unambiguous (no binary). Chromium cause not established.

**2026-07-31, after a system `chromium` was installed.** Re-probed by calling the
generators directly rather than reading the log:

| Call | Result |
|---|---|
| `get_pdf(html)` — no generator | `OSError: No wkhtmltopdf executable found: "b''"` |
| `get_print(..., as_pdf=True)` — no generator | same wkhtmltopdf error |
| `get_print(..., as_pdf=True, pdf_generator="chrome")` | `TimeoutError: Chromium took too long to start.` |

Three things that changes:

1. **Still broken.** Neither backend produces a PDF.
2. **The system package was the wrong target.** Frappe logged *"Chromium executable is
   already executable: /home/frappe/frappe-bench/chromium/chrome-linux/headless_shell"* —
   it manages its own build under the bench and never looks at `/usr/bin/chromium`. The
   earlier revision of this runbook was wrong to send you there.
3. **The chrome setting has a narrower scope than assumed.** `get_print` with no explicit
   generator goes to wkhtmltopdf even though `Print Settings.pdf_generator = "chrome"`.

The binary exists and is executable, so this is the *cannot launch* case. **Step 2 is what
names the cause** — running `headless_shell` by hand prints something far more specific
than the timeout Frappe surfaces.
