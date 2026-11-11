# Frappe Pilot for the Sapphire ERPNext site: evaluation and plan

Written 2026-11-11. **Evaluation only: nothing was installed, and no host or cloud setting was
changed.** Nik asked to bring [frappe/pilot](https://github.com/frappe/pilot) to the Sapphire
ERPNext site "to help visually manage it". This page records what Pilot is, whether it fits how
production is hosted, and the plan.

"The website" is read here as the ERPNext site, `erp.sapphirefountains.com`. Pilot manages Frappe
benches and sites and has nothing to offer the public WordPress site, `sapphirefountains.com`.

## Short answer

- **What it is.** Pilot is Frappe's new server manager: a `pilot` command line plus a browser
  Admin panel that creates and runs Frappe benches on a server. It replaces the classic `bench`
  tool and the hand-written server setup around it. It is not a Frappe app and adds nothing to the
  Desk.
- **Does it fit today's production server? No.** Three reasons, each enough on its own:
  1. **It cannot take over an existing bench.** Pilot only manages benches it created, under its
     own directory. Moving to it means building a new server with Pilot and restoring the site
     into it, then rewriting the deploy pipeline. That is a re-platform, not an install.
  2. **It is pre-alpha by its own label.** The newest release is `v0.0.100-pre-alpha`
     (2026-11-09), the 13th release in two weeks, and it updates itself daily unless told not
     to.
  3. **Its Admin panel is a full remote console for the server.** It includes a Python console,
     an SQL console, a code editor for app files and "Login as admin" for every site. This site
     was broken into through the Administrator account on 2026-08-02; a second, more powerful
     admin login is a real cost.
- **Recommendation.** Do not put Pilot on production now. Get the visual management in three
  steps: use the health screen Frappe already ships (today, no work), rehearse Pilot on a
  throwaway machine to see its screens with Sapphire's apps (needs Nik's yes), and decide whether
  to build a read-only Server Status page inside this app. Revisit Pilot on production when it has
  a stable release and the rehearsal has passed. Details are in [The plan](#the-plan).

## What Pilot is

Read from the repository on 2026-11-11 (branch `develop`, release `v0.0.100-pre-alpha`).

| Question | Answer |
|---|---|
| Kind of tool | A server and bench manager. A Python command line (`pilot`) and a Flask Admin backend with a Vue Admin panel. It runs on the server, beside the benches it manages |
| Is it a Frappe app? | No. It has no `hooks.py`, is not installed on a site, and adds no DocTypes. By default it writes four keys into each site's `site_config.json` (`pilot_endpoint`, `pilot_auth_token`, `cloud_server_url`, `cloud_settings_embed_url`) so the Desk can show a "Cloud Settings" dialog. Setting `allow_site_to_pilot_communication = false` stops that for sites created afterwards |
| What it manages | Benches, apps, sites, processes (web, realtime, background workers, Redis), the database server, nginx, TLS certificates, backups, updates and migrations, logs, alerts, SSH keys |
| Supported versions | Frappe v16 or later. Debian 12+, Ubuntu 24.04+, Fedora 43+, Arch, or macOS for development. Python 3.11+ on the host, Node.js 24. Each bench gets Python 3.14 through `uv` |
| License | AGPL-3.0. No fee. We would run it unmodified |
| Maturity | Repository created 2026-05-21. 98 releases in under six months, all marked pre-release and named `pre-alpha`. About 30 open issues and 19 open pull requests. Written by Frappe's own team, with commits every working day |
| What it needs | Root once, to prepare the host (packages, an nginx include, sudo grants for nginx and certbot, a log-rotation rule). After that everything runs as one ordinary user under `systemctl --user`. The Admin panel listens on its own port (7000 or 8002 in the docs) and in production needs its own hostname |
| Outside services | None required. Optional: an S3 bucket for backups, a mail server or webhooks for alerts, an LLM provider for an admin assistant, and "Central", which is how Frappe Cloud manages a Pilot host. Telemetry ships only when an endpoint and a token are configured, which a self-hosted install does not have |

### The screens, and who they are for

Every screen is for whoever administers the server. Nobody in the shop or the office would use
them. From `docs/admin-ui.md` and the Admin panel's source tree:

| Screen | What it shows or does |
|---|---|
| Analytics | Charts of CPU, memory, disk, network and per-process use for the server, the database and each site, from live to one week back. The closest thing in Pilot to "health at a glance" |
| Activity | A feed of what was done on the bench and by whom |
| Sites | Each site's apps, domains and certificates, backups and restore, site config, "Visit site", "Login as admin", rename, drop |
| Marketplace and Apps | Install an app from a Git repository or Frappe's registry; installed versions; branch switch; uninstall |
| Updates and Migrations | Updates available per app. An update runs as a tracked operation: maintenance mode, a safeguard backup per site, the app update, then a migrate per site. The backup and the migrate run only when the update touches patches, hooks, fixtures or DocType JSON, and the backup covers the database, not files. A failure stops in "needs attention" with the failing patch named, and offers Retry, Skip this patch, Restore or Dismiss |
| Tasks | Every long operation with its steps, live log and final status |
| Logs | Bench and process logs in the browser |
| Database | A database analyzer, the binlog browser, the process list, MariaDB settings and the memory split |
| Console | A Python console and an SQL console against a site |
| Editor | A code editor for the apps' files on the server (on by default) |
| Storage | Disk use by database (with binlogs and a purge) and by bench files: apps, each site's files and backups, logs |
| Settings | Bench config, workers, alerts (CPU, memory, disk, site down, backups failing) to mail or webhooks, firewall and WAF, S3, automatic updates, and the SSH keys allowed to log in as the bench user |

The Updates and Migrations screen is the part with the most to offer Sapphire. A deploy here has
ended half-applied before (v1.395.0, see `CLAUDE.md`), and a per-site safeguard backup with a
Restore button is exactly the missing piece. It is also the newest part of Pilot: three of its
open pull requests (#604, #605, #606) are still adding recovery for interrupted migrations.

## How it meets this production server

Checked read-only on 2026-11-11: production runs frappe 16.56.0, erpnext 16.54.0,
erpnext_enhancements 1.799.1, frappe_assistant_core 3.3.0, and newsletter, telephony and payments,
on MariaDB 10.11.19 (the Debian package).

| Pilot expects | Production has | Result |
|---|---|---|
| Frappe v16+ | 16.56.0 | Fits |
| Debian 12+ | Debian 12 | Fits |
| Node.js 24, bench Python 3.14 | Node 24, Python 3.14 env | Fits |
| A bench it created, under `~/pilot/benches/` | A classic bench at `/home/frappe/frappe-bench`, built by `bench init` | **Does not fit.** Pilot has no command to adopt or import a classic bench. The only open request in this area (#731) is export and import between two Pilot hosts |
| Its own process units (`systemctl --user`) and its own Redis | `frappe-bench.service`, described in [`infra/docs/production-web-tier.md`](../infra/docs/production-web-tier.md), restarted by both Cloud Build pipelines | **Does not fit.** Pilot would start its own processes on free ports for its own bench and leave `frappe-bench.service` alone, so it would not show or manage production's processes at all |
| Its own MariaDB 11.8 (rootless), or an existing server it is pointed at | The system MariaDB 10.11.19 | Workable through `[mariadb] existing = true`. But the installer adds MariaDB's 11.8 package repository to the host and ranks it above Debian's own packages, so a later `apt upgrade` would move the system database to 11.8 |
| To render nginx's site config and set nginx's worker user | bench's generated nginx config plus [`infra/configs/nginx-realip.conf`](../infra/configs/nginx-realip.conf) | **Conflicts.** The installer changes the worker user in `/etc/nginx/nginx.conf`, adds an include and removes the default site |
| To pull app updates itself, from its registry or a branch tip | A merge to `main` triggers Cloud Build, which resets the app, migrates, builds, flushes Redis and restarts | **Needs rewriting.** Deploying a named, CI-tested commit from CI is still an open request in Pilot (#732) |
| A bench user it prepares; default name `frappe` | A `frappe` user that already owns the bench | **Risky.** The installer's defaults would change sudo grants, linger and nginx settings for the account production runs as |
| Every app to pass its validator on install and on every update, with no override | Seven apps | Six look fine on paper: each declares a Frappe range that includes 16.56. **payments must be on its `version-16` branch**; its `develop` branch declares Frappe 17 and would be refused. Import, hook-path and dependency-resolution checks can only be answered by a rehearsal |
| A hostname for the Admin panel in production | TLS ends at the Google load balancer; SSH is through IAP only | Open. The panel must not be reachable from the internet (next section) |

Run as root on a host, the installer also adds the Fluent Bit package repository, installs
PostgreSQL, supervisor, certbot, Fluent Bit and nginx's ModSecurity module where they are missing,
and writes sudo grants, a log-rotation rule and a swap helper. It does not stop a service that was
already enabled before it ran, so the live database, nginx and Redis would keep running. The harm
is to the host's configuration, not an immediate outage.

So: Pilot could run a server like this one, but it cannot be added to this one. Getting there
means a new VM prepared by Pilot's installer, the seven apps fetched through `pilot get-app`, the
site restored from a backup, the load balancer pointed at the new VM, and these repository files
rewritten: `infra/cloudbuild-deploy.yaml`, `infra/cloudbuild-upstream.yaml`,
`infra/configs/startup_script.sh`, `infra/configs/install_bench_units.sh` and the units in
`infra/configs/systemd/`.

Two things would get better on a Pilot server, and are worth knowing when weighing it:

- **Redis survives an app update.** Pilot restarts web and the workers but leaves Redis running,
  so the queued background jobs that today's deploy destroys with `FLUSHDB` would survive.
- **The web port is held by a systemd socket**, so a restart queues requests instead of refusing
  them. What runs behind it is a choice to make in the rehearsal: every new Pilot bench starts in
  "lite mode", one process for web, realtime and jobs. gunicorn, socketio and worker pools as
  separate units exist only with `[lite_mode] enabled = false`, which is the shape
  `infra/docs/production-web-tier.md` plans for this site.

## Risks of the Admin panel

Pilot's own `SPEC.md` states the trust model in one line: "Admin access to one bench is equivalent
to shell access as the host user". It also says the sudo grants the installer writes make that
user root-equivalent on the machine. That is honest and it is by design; a server manager has to
be able to do everything. For Sapphire it means:

- The Admin panel is one password (optionally with a TOTP code) away from a Python console on the
  production database, every site's files and backups, and a one-click Administrator login.
- It must never have a public hostname. If Pilot is ever used on a Sapphire VM, the panel is
  reached through the IAP tunnel only, the way SSH is today.
- Automatic Pilot updates are on by default (a daily cron at 00:00 UTC). On a pre-alpha tool that
  means unreviewed code changes arrive on the server every night. Turn it off and pin a release.
- The code editor is on by default. Editing an app's files on the server would drift production
  away from `main`, which the next deploy then silently resets. Turn it off.
- Settings can add SSH keys for the bench user, so a panel login can be turned into a lasting
  shell login.
- Pinning a Pilot release does not pin everything it installs: the Admin panel's Python
  environment takes one dependency (`datum`) from the tip of a Git branch.

## Paths considered

| Path | Verdict |
|---|---|
| **Wait on production; use Frappe's built-in health screen now; rehearse Pilot off production; decide on an in-app Server Status page; revisit Pilot at a gate** | **Recommended** |
| Run Pilot's installer on the production VM, beside the existing bench | Rejected: it would still not see the existing bench, and its host preparation touches nginx, sudo and the package sources of the live server |
| Rebuild production on Pilot now | Rejected: pre-alpha, and it replaces the deploy pipeline, the process units and the nginx setup in one move |
| Install Pilot only as a read-only viewer of the current bench | Rejected: there is no such mode |
| Put Pilot on the beta VM now | Deferred, not rejected: it is the right second step after a throwaway rehearsal, because the beta VM is also built from this repository's Terraform |
| Go back to Frappe Cloud for its dashboard | Rejected: the move off Frappe Cloud was made in July 2026 for reasons this request does not change |

## The plan

Who does what: a **build session** writes code and opens draft pull requests; the **Sapphire
Manager** session reviews and merges on Nik's word; **Nik** does anything on a VM or in the cloud
console.

### Step 1: use what Frappe already ships (Nik, today, no change)

Frappe 16.56 includes a **System Health Report** at `/desk/system-health-report`, readable by a
System Manager. Its sections are Background Jobs, Scheduler, Errors, Emails, Database, MariaDB
Variables, Cache, File Storage, Backups, Users and Realtime. Beside it sit the RQ Job and RQ
Worker lists, Scheduled Job Log, Error Log and the About dialog's installed versions. Open these
first; they may already cover part of what "visually manage" means.

### Step 2: answer the open questions (Nik)

See [Open questions](#open-questions-for-nik). The first one decides whether Steps 3 and 4 are
wanted at all.

### Step 3: rehearse Pilot off production (build session, after Nik's yes)

Goal: Nik sees the real screens with Sapphire's apps and data shape, and we learn what breaks.

1. Use a disposable Debian 12 VM that holds nothing else, in the test project. Not the
   production VM, not the beta VM, and not the WSL distribution that holds the local mirror,
   because the installer changes nginx, sudo and package sources on whatever it runs on. A fresh
   WSL distribution might work and costs nothing, but Pilot's docs never mention WSL and it
   depends on systemd user services, so that is unproven.
2. Install one pinned release with the installer's `--version` option, then turn automatic
   updates and the code editor off.
3. `pilot new --frappe-branch version-16`, then `pilot init --commit <sha>` to put frappe on
   production's commit. Then `pilot get-app <repo> --branch <branch> --commit <sha>` for erpnext,
   erpnext_enhancements, frappe_assistant_core, newsletter, telephony and payments. Name the
   commit every time: left to itself Pilot installs what its registry lists, which has lagged the
   `version-16` branch (issue #755). Record every validator refusal.
4. Create a site and restore the sanitized mirror backup into it. Never a raw production backup.
5. Walk every screen in the table above and capture them. Run one app update through the Updates
   screen, and force one failing patch to see Retry and Restore work.
6. Write up: what worked, what was refused, how long the restore took, and a go or no-go for a
   beta VM on Pilot.

Nothing in this step touches production, the beta VM, Terraform or Cloud Build.

### Step 4: a Server Status page in this app (build session, if Nik wants it)

The gaps the built-in health report leaves are specific to how this site is deployed, and they
are all readable from inside Frappe without touching the host:

- installed versions of each app against the newest upstream release tag;
- the last deploy: which commit, when, and whether the migrate finished (the tail of the Patch
  Log against `patches.txt`);
- a scheduler heartbeat as one green or red tile (minutes since the last Scheduled Job Log row);
- the queue: depth and the age of the oldest job;
- the newest offsite backup and its age (the `offsite_backup` module already tracks this);
- Error Log rows per hour against the usual rate.

That is one read-only Desk page for System Managers, a MINOR release, with no new access to the
server. It gives the at-a-glance view on today's production with none of Pilot's risks. It does
not give Pilot's actions (update, restore, restart); those stay in the pipeline and the runbook.

### Step 5: revisit Pilot for the VMs (gated)

Open this step only when all of these are true:

1. Pilot has a release that is not labelled pre-alpha or alpha.
2. The rehearsal passed with all seven apps and a restored site.
3. There is a supported way to deploy a CI-tested commit (Pilot issue #732), or we have written
   and rehearsed our own replacement for `infra/cloudbuild-deploy.yaml`.
4. The runbook in `infra/docs/production-web-tier.md` is complete on both VMs, so the comparison
   is against the web tier this repository already describes.

Then, in order: beta VM rebuilt on Pilot (Nik on the VM; a build session for the Terraform,
startup script and Cloud Build changes); at least a month on beta including two Frappe upgrades
through Pilot's Updates screen; then a production cutover in a maintenance window, with the old
VM kept until the new one has run a week.

## Cost and upkeep

- **License:** none. AGPL-3.0, run unmodified.
- **Step 1:** nothing.
- **Step 3:** a throwaway VM the size of beta costs a few dollars for the days it exists. One
  build session.
- **Step 4:** one build session and one review; the usual version bump.
- **Step 5:** the expensive one. Several pull requests against `infra/`, a rewritten runbook, one
  beta rebuild and one production cutover with downtime that depends on the restore time measured
  in Step 3.
- **Upkeep on Pilot:** it shipped 13 releases between 2026-10-27 and 2026-11-09. Pinning a
  release and upgrading on purpose means somebody reads release notes and re-tests regularly. The
  current setup has no equivalent chore, because `bench` changes rarely.

## Open questions for Nik

1. **What do you want to see or do "visually"?** Health at a glance; one-click Frappe and ERPNext
   updates with a rollback; backups and restore; logs; database tools; or something about the Desk
   itself (icons, forms, layout)? If it is the last one, Pilot is the wrong tool and this page can
   be closed.
2. **Who would use it?** Only you, or also the people who look after the VMs?
3. **May a build session run the rehearsal (Step 3)?** It needs you to create a throwaway
   Debian 12 VM in the test project and delete it afterwards.
4. **Do you want the Server Status page (Step 4) built now?** It is the fastest way to a visual
   view of production.
5. **Is moving the VMs to Pilot something you want in the next few months at all**, knowing it
   means rebuilding them, or is Step 4 enough until Pilot is stable?

## Sources

All read on 2026-11-11.

- Pilot: [`README.md`](https://github.com/frappe/pilot/blob/develop/README.md),
  [`SPEC.md`](https://github.com/frappe/pilot/blob/develop/SPEC.md),
  [`install.sh`](https://github.com/frappe/pilot/blob/develop/install.sh), `pyproject.toml`, and
  under [`docs/`](https://github.com/frappe/pilot/tree/develop/docs): `architecture.md`,
  `admin-ui.md`, `commands.md`, `configuration.md`, `production.md`, `migration.md`,
  `app-dependencies.md`.
- Pilot issues and pull requests: #427 (an existing bench after installing Pilot), #731 (export
  and import), #732 (deploy from CI at the tested commit), #755 (registry behind `version-16`),
  #604 to #606 (migration recovery).
- Frappe: `frappe/desk/doctype/system_health_report/` at tag `v16.56.0`.
- This repository: [`infra/README.md`](../infra/README.md),
  [`infra/cloudbuild-deploy.yaml`](../infra/cloudbuild-deploy.yaml),
  [`infra/cloudbuild-upstream.yaml`](../infra/cloudbuild-upstream.yaml),
  [`infra/docs/production-web-tier.md`](../infra/docs/production-web-tier.md).
- Production, read-only: `tabInstalled Application` and `SELECT VERSION()`.
