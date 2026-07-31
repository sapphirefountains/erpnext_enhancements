# PDF generation — diagnosis and runbook

PDF generation is broken on production. **Both** backends fail, for different reasons, so
there is no working path at all — and as of 2026-07-31 both root causes are established
rather than inferred: Debian's wkhtmltopdf **segfaults on any input**, and the bench's
Chromium **traps on startup before it can print an error**.

This is an **environment** problem, not an application one. Nothing in this repo can fix
it; the commands below have to be run on the VM. The repo change that accompanies this
document is the codified package list (`infra/`) so a rebuilt VM inherits the fix.

> ## The host is Debian 12, not Ubuntu
>
> **Read this before copying any `apt` command from anywhere, including earlier revisions
> of this file.** `production-erpnext-standard-vm` runs **Debian GNU/Linux 12 (bookworm)**,
> x86_64, on a bare VM under systemd — not a container, and not Ubuntu.
>
> ```
> VERSION_ID="12"  VERSION_CODENAME=bookworm
> glibc 2.36  ·  kernel 6.1.0-51-cloud-amd64  ·  AMD EPYC 7B13
> ```
>
> This has bitten this runbook twice, in both directions:
>
> - **An earlier revision of step 4 listed `libasound2t64`.** That is an Ubuntu 24.04 name
>   from the 64-bit `time_t` transition and **does not exist on bookworm** (verified:
>   `apt-cache show libasound2t64` → no such package). `apt-get install` aborts *wholesale*
>   on one unknown package name, so that single word meant **none** of the other fifteen
>   packages installed either. Anyone who ran it concluded "the libraries did not help"
>   while having installed nothing. The correct name here is plain `libasound2`.
> - **The Ubuntu 22.04 (`jammy`) wkhtmltopdf `.deb` cannot install here.** It depends on
>   `libjpeg-turbo8`, another Ubuntu-only name; bookworm has `libjpeg62-turbo`. That is
>   almost certainly the history behind the `rc  wkhtmltox  1:0.12.6.1-2.jammy` entry still
>   in dpkg — installed, unsatisfiable, removed.
>
> Debian and Ubuntu package names diverge often enough that every command below was checked
> against this box rather than assumed.

## What is actually wrong

Verified against production on 2026-07-31, with a shell on the VM.

| Fact | Value |
|---|---|
| OS | **Debian 12 (bookworm)**, x86_64, glibc 2.36, kernel 6.1.0-51-cloud-amd64 |
| Frappe | 16.29.0 |
| `Print Settings.pdf_generator` | `chrome` — **but see below; this is not what the server reads** |
| `Print Format.pdf_generator` | **28 rows pinned to `wkhtmltopdf`**, 25 empty, 9 `chrome` |
| `pdf_generator` hook | `frappe.utils.pdf.get_chrome_pdf` (the only one registered) |
| Bench Chromium | `/home/frappe/frappe-bench/chromium/chrome-linux/headless_shell`, 178 MB, present, executable |
| System wkhtmltopdf | `/usr/bin/wkhtmltopdf`, Debian `0.12.6-2+b1` — **unpatched Qt** |
| Service manager | `frappe-bench.service` under **systemd**. There is no supervisor on this box |
| Letter Head | `Sapphire Fountains Default`, default, image `/private/files/Logo-cropped.png` |

Two distinct failures in the Error Log:

| Error | Backend | Occurrences | Window |
|---|---|---|---|
| `Chromium took too long to start.` | `pdf_generator=chrome` | 2 | 2026-07-27 |
| `No wkhtmltopdf executable found: "b''"` | `pdf_generator=wkhtmltopdf` | 9 | 2026-07-20 → 07-28 |

The volume is low because the failure is total: people try once, it fails, they stop. One
of those nine is literally
`download_pdf?doctype=Purchase Order&name=PO-2026-00215&format=PO Test Print Format` —
somebody attempting exactly what the Purchase Order print-format work needs.

Note that `No wkhtmltopdf executable found` is **not a Frappe string** — it is pdfkit's
`Configuration.__init__`, which resolves the binary with `subprocess.Popen(['which',
'wkhtmltopdf'])`. The empty `b''` means `which` returned nothing, i.e. the binary was
genuinely absent. Those nine are **historical**: they fall exactly in the window when
`wkhtmltox` sat in `rc` state and Debian's package was not yet installed. A recurrence
*now* would mean a PATH problem in the worker environment instead.

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

> **Second correction, 2026-07-31 — "took too long to start" is not a timeout, and the
> missing-libraries theory was wrong too.**
>
> Running the binary directly settles it. `headless_shell --version` — which renders
> nothing and needs no sandbox — exits **133** (SIGTRAP) and writes **zero bytes to stdout
> and stderr**. Every flag combination behaves identically. A process that cannot even
> print its own version string is not starting slowly; it is calling `IMMEDIATE_CRASH()`
> before logging is up.
>
> This retires the shared-library hypothesis the previous correction offered: **all 53
> `ldd` entries resolve, with zero "not found"**. The full ruled-out list is in step 2 of
> the runbook. The only unmet declared dependency is `libvulkan1`, from the build's own
> `deb.deps`.
>
> The practical consequence: **do not spend time tuning memory, `/dev/shm` or the CDP
> timeout.** None of them can explain a SIGTRAP on `--version`.

### The `chrome` setting is not what the server reads — Frappe pinned every format to wkhtmltopdf

> **Correction, 2026-07-31.** An earlier revision said `Print Settings.pdf_generator`
> governs the `download_pdf` path and that server-side callers "fall through" to
> wkhtmltopdf. The observed behaviour was right; the mechanism was wrong, and the
> difference matters because it changes what you have to fix.

The generator is resolved by `frappe/utils/print_utils.py` in this order:

1. `form_dict.pdf_generator` (the query parameter)
2. an explicit argument
3. **`Print Format.pdf_generator` — the field on the format row**

`Print Settings.pdf_generator` is **never consulted server-side**. It is read in
`print.js`, client-side, and appended to the Download PDF URL as `&pdf_generator=`.

And Frappe's own upgrade patch `sets_wkhtmltopdf_as_default_for_pdf_generator_field.py`
(registered in `frappe/patches.txt`) walks every Print Format and writes `"wkhtmltopdf"`
into that field. **It has run on this site** — confirmed in `tabPatch Log` — which is why
28 formats now read `wkhtmltopdf`.

So server-side renders are not defaulting anywhere. They are **explicitly pinned**:

```
Print Format.pdf_generator   wkhtmltopdf  28    (empty)  25    chrome  9
Purchase Order - Sapphire →  wkhtmltopdf
```

That last line matters for the PO print-format work: the format this app ships is itself
pinned to the backend that segfaults. New formats inherit `wkhtmltopdf` from the field's
DocType default, so creating one does not opt out.

**wkhtmltopdf also cannot be retired even if Chromium is fixed.**
`frappe.utils.pdf.get_pdf` calls `pdfkit.from_string` directly with no generator branch at
all, so everything routed through it is wkhtmltopdf-only whatever any setting says:

- `report_to_pdf` — every report PDF export.
- `project_enhancements/esign/lifecycle.py` → `get_pdf` — signed contract PDFs.
- `api/maintenance_workflow.py` → `attach_print` inside a `sendmail` — customer maintenance
  reports. (This one *is* redirectable: it goes via `get_print`.)

The last two swallow the failure, so they have been degrading silently rather than
erroring.

There is also **no chrome → wkhtmltopdf error fallback**. `get_chrome_pdf` is a
`try/finally` with no `except`, so a Chromium timeout propagates. The only fallback is the
*declined-hook* one: `get_chrome_pdf` returns `None` when the generator is not `"chrome"`,
and the caller falls through.

### Why wkhtmltopdf fails: the Debian build is unpatched **and** segfaults

Two separate problems, and only the first is the well-known one.

**It is the unpatched-Qt build.** Debian builds against system Qt5WebKit and its own
package page says so — "not built against a forked version of Qt". The version string is
`wkhtmltopdf 0.12.6` with **no `(with patched qt)` suffix**. Reproduced verbatim on the box:

```
The switch --print-media-type, is not support using unpatched qt, and will be ignored.
The switch --footer-right, is not support using unpatched qt, and will be ignored.
```

This is not avoidable by configuration: `frappe/utils/pdf.py::prepare_options()`
**unconditionally** sets `"print-media-type": None`, so 100% of Frappe's wkhtmltopdf jobs
trip it. `--disable-smart-shrinking`, `--header-html`/`--footer-html` and `--header-spacing`
are also patched-Qt-only and are passed whenever a letterhead has `repeat_header_footer`.

**But those lines are warnings, not the crash.** The switch is ignored and execution
continues. The actual failure is worse:

```
$ printf '<h1>hi</h1>' > /tmp/a.html
$ wkhtmltopdf /tmp/a.html /tmp/a.pdf          # no flags at all
rc=139        # 128 + 11 = SIGSEGV, inside libQt5WebKit.so.5
```

**It segfaults on a one-line HTML file with no options.** So this build is not
feature-limited, it is non-functional — dropping the patched-Qt-only flags would not have
helped. Frappe surfaces this as `exited with non-zero code -11` and the qthack warnings
happen to be on the same stderr, which is what made them look causal.

Frappe does have a guard — `is_wkhtmltopdf_valid()` runs `--version` and checks for `qt` in
the output, which Debian's string fails — but it is only called **client-side** from the
print view, never enforced server-side. That is why requests still reach the binary.

### Root cause of both

`infra/variables.tf` provisioned the VM with:

```
["curl", "git", "nginx", "python3", "python3-pip", "python3-venv", "pipx"]
```

No browser, no wkhtmltopdf, no font packages. Nothing anywhere in this repo installs
either. The bench was built on a host that never had a PDF toolchain.

The absence has since been *partly* filled by hand, which is what makes the current state
confusing rather than simply broken: a Debian `wkhtmltopdf` was installed (segfaults), an
Ubuntu `wkhtmltox` was installed and removed (wrong distro), and Frappe downloaded its own
Chromium (traps). Three interventions, no working PDF, and each one changed the error
message — which is why this document has needed three passes.

> **Note on `infra/configs/startup_script.sh`:** the `apt-get install` is guarded behind
> `SKIP_FIRST_BOOT`, so it runs *only on first boot*. Adding packages to the Terraform
> variable does **not** retrofit the running VM — it only means a rebuilt one starts
> correct. The running VM needs the manual install below regardless. That is why this is
> two changes, not one.

## Runbook

Run on the production VM (`production-erpnext-standard-vm`, us-east4-a) as a user with
sudo.

Step 0 is read-only and gates everything else. **Step 1 (wkhtmltopdf) is the one that
restores PDF generation** — it is the backend 28 Print Formats are actually pinned to, and
it has a known-good package. Steps 2–3 (Chromium) matter for the longer term, since
wkhtmltopdf has been unmaintained since 2023, but they are not what unblocks printing
today.

### 0. Prove the OS before running anything

Every `apt` command below is Debian-specific. One Ubuntu package name aborts the whole
install (see the box at the top of this file).

```bash
. /etc/os-release && echo "$ID $VERSION_ID $VERSION_CODENAME"
```

Must print `debian 12 bookworm`. If it does not, **stop** — none of the package names below
are trustworthy.

### 1. Fix wkhtmltopdf — the patched-Qt bookworm build

Do this first: it is the backend 28 Print Formats are pinned to, including
`Purchase Order - Sapphire`.

The wkhtmltopdf project was archived in 2023 and `0.12.6.1-3` (2023-05-22) is its final
release. Its build targets are `bookworm, bullseye, jammy, focal, bionic` — **a genuine
Debian 12 asset exists**, so no cross-distro package is needed. Do not substitute the
bullseye one: it needs `libssl1.1`, which bookworm does not ship.

```bash
curl -fsSLO --output-dir /tmp https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6.1-3/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb
```

Verify before installing — this is a 16,986,150-byte file and the checksum was taken from
the downloaded asset:

```bash
echo "98ba0d157b50d36f23bd0dedf4c0aa28c7b0c50fcdcdc54aa5b6bbba81a3941d  /tmp/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb" | sha256sum -c -
```

Remove Debian's segfaulting build and the leftover `rc` residue from the failed jammy
attempt:

```bash
sudo apt-get purge -y wkhtmltopdf wkhtmltox
```

Install. The package declares `Conflicts/Provides/Replaces: wkhtmltopdf`, so apt handles
the swap and pulls its dependencies (`libjpeg62-turbo`, `libssl3`, `xfonts-75dpi`,
`xfonts-base`, `fontconfig`, …):

```bash
sudo apt-get install -y /tmp/wkhtmltox_0.12.6.1-3.bookworm_amd64.deb
```

**The check that decides everything** — the suffix must be there:

```bash
wkhtmltopdf --version
```

Expect `wkhtmltopdf 0.12.6.1 (with patched qt)`. If `(with patched qt)` is absent, stop:
nothing downstream will work, and Frappe passes `--print-media-type` on every single job.

Then prove it actually renders, using the flag that was being rejected:

```bash
printf '<h1>hi</h1>' > /tmp/a.html && wkhtmltopdf --print-media-type /tmp/a.html /tmp/a.pdf && ls -l /tmp/a.pdf
```

Exit 0, no `unpatched qt` warning, and a non-zero-byte PDF. The old build exited 139
(SIGSEGV) here.

The new binary lands in `/usr/local/bin/`, which precedes `/usr/bin` on the bench user's
PATH (`/home/frappe/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:…`), so it
wins even if a distro package reappears later.

### 2. Diagnose Chromium — it does not merely start slowly

The error message says "took too long to start", which reads like a timeout. It is not.
Run the binary by hand:

```bash
/home/frappe/frappe-bench/chromium/chrome-linux/headless_shell --version
```

As of 2026-07-31 this exits **133** (128 + 5 = SIGTRAP) and writes **zero bytes to both
stdout and stderr** — even for `--version`, which does no rendering. That is
`IMMEDIATE_CRASH()` firing before logging initialises, not a slow launch.

What has been **ruled out**, so nobody re-checks them:

| Suspect | Finding |
|---|---|
| Missing shared libraries | All 53 `ldd` entries resolve; **zero** "not found" |
| glibc too old | Binary needs ≤ `GLIBC_2.25`; host has 2.36 |
| CPU instruction set | AMD EPYC 7B13, has `sse4_2` and `avx` |
| ASLR entropy | `setarch -R` makes no difference |
| seccomp / NoNewPrivs | Both `0` in the calling process |
| `/dev/shm` too small | 16 GB, not the old 64 MB default |
| Corrupt/partial download | Valid ELF; `icudtl.dat`, all `.pak` files and libs present |

The one unmet dependency is in the build's own `deb.deps` manifest:

```bash
sudo apt-get update && sudo apt-get install -y libvulkan1
```

Then re-run the `--version` check above.

### 3. If Chromium still traps, re-provision it

Frappe 16 ships a bench command for this — it is the supported path, and it re-downloads
the binary rather than patching around it:

```bash
cd /home/frappe/frappe-bench && bench setup-chrome
```

### 4. Fonts

Needed regardless of backend. Without them a headless browser renders boxes or silently
substitutes, producing a PDF that generates "successfully" and looks wrong — a worse
failure than the current one, because nobody gets an error.

```bash
sudo apt-get install -y fonts-liberation fonts-dejavu-core fontconfig
```

### 5. Chromium's remaining runtime libraries

Only if step 2 or 3 shows something missing. These are the bookworm names — note
`libasound2`, **not** `libasound2t64`:

```bash
sudo apt-get install -y libnss3 libnspr4 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libcups2 libdrm2 libgbm1 libasound2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libpango-1.0-0 libcairo2 libxshmfence1
```

All of these were confirmed present in bookworm's index on this host.

### 6. Restart the bench so workers pick up the new binary

This box runs the bench as a **systemd** unit, `frappe-bench.service`. There is **no
supervisor here** — `supervisorctl` is not installed, so any instruction of the form
`supervisorctl restart all` (a common Frappe idiom, and one that has been mistakenly
suggested for this host) will simply fail.

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
  nine logged failures. Note that `Purchase Order - Sapphire` carries
  `pdf_generator = "wkhtmltopdf"` — inherited from the field's DocType default, not set by
  `setup_print_formats.py` — so it renders through the segfaulting backend. Step 1 is
  therefore a hard prerequisite for signing that work off, not an alternative to step 2.
- **Report PDF exports** go through `report_to_pdf` → `frappe.utils.pdf.get_pdf`, which
  calls pdfkit directly with no generator branch. They are wkhtmltopdf-only no matter what
  any setting says, so they cannot be rescued by fixing Chromium.
- `project_enhancements/esign/lifecycle.py` builds the signed-contract PDF and is wrapped
  in a bare `try/except` that logs and returns `None`, so contract e-signature has been
  degrading silently rather than erroring. Worth re-testing once this is fixed.
- `api/maintenance_workflow.py` calls `frappe.attach_print` inside a `sendmail`, so
  customer maintenance reports have been going out without their attachment.

## Verification log

The first two passes were established from the production database and the deployed Frappe
source, with **no shell on the VM** — which is why both of them misdiagnosed Chromium.

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

**2026-07-31, third pass — with a shell on the VM.** This is the pass that settled both
root causes, and it overturned parts of the two above. Commands were run as the `frappe`
user via `frappe.utils.execute_in_shell`.

| Probe | Result |
|---|---|
| `/etc/os-release` | **Debian 12 bookworm**, not Ubuntu — invalidating the `t64` package names |
| `dpkg -l \| grep wkhtml` | `ii wkhtmltopdf 0.12.6-2+b1` and `rc wkhtmltox 1:0.12.6.1-2.jammy` |
| `wkhtmltopdf /tmp/a.html /tmp/a.pdf` (no flags) | **rc=139, SIGSEGV** in `libQt5WebKit.so.5` |
| `wkhtmltopdf --print-media-type …` | `not support using unpatched qt` — a *warning*; the segfault is separate |
| `headless_shell --version` | **rc=133, SIGTRAP**, zero bytes on stdout *and* stderr |
| `ldd headless_shell \| grep "not found"` | **0 results** of 53 entries |
| `deb.deps` vs installed | only **`libvulkan1`** missing |
| `setarch -R headless_shell --version` | still rc=133 — not ASLR |
| `/proc/self/status` | `Seccomp: 0`, `NoNewPrivs: 0` — not sandboxing |
| `df -h /dev/shm` | 16 GB — not the classic 64 MB trap |
| `objdump -T` glibc versions | max `GLIBC_2.25`; host has 2.36 — not a glibc wall |
| `apt-cache show libasound2t64` | **no such package** — the step-4 command installed nothing |
| `tabPrint Format.pdf_generator` | **28 `wkhtmltopdf`**, 25 empty, 9 `chrome` |
| `tabPatch Log` | `sets_wkhtmltopdf_as_default_for_pdf_generator_field` **has run** |
| `systemctl` / `command -v supervisorctl` | `frappe-bench.service` active; **no supervisorctl** |

Four things this changes:

1. **Both backends are non-functional, not degraded.** wkhtmltopdf segfaults on
   `<h1>hi</h1>`; Chromium traps on `--version`. Neither has ever been close to working.
2. **The missing-library theory is dead** for Chromium, and the memory / `/dev/shm` /
   timeout theories with it. Nothing that explains a SIGTRAP on `--version` is a resource
   problem.
3. **The runbook's own step 4 was self-defeating** — one Ubuntu package name aborted the
   entire install, so anyone who ran it installed nothing while believing otherwise.
4. **`Print Settings.pdf_generator = "chrome"` was never the operative setting.** The
   server reads `Print Format.pdf_generator`, and Frappe's patch pinned 28 of them —
   including this app's own `Purchase Order - Sapphire` — to wkhtmltopdf.
