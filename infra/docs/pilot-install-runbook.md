# Installing Frappe Pilot for erp.sapphirefountains.com

Written 2026-11-11, after Nik's decision the same day: **use Pilot itself, all of it, rather than
rebuild parts of it in this app; Parker and James will use it too, and later others.** Background
and the reasons this is a new server rather than an add-on are in
[`docs/frappe-pilot-evaluation.md`](../../docs/frappe-pilot-evaluation.md).

**Nothing in this file has been run.** Commands marked *confirm* come from Pilot's documentation
or source and have not been tried on a Sapphire machine. Pilot is pre-alpha
(`v0.0.100-pre-alpha`), so expect at least one step to behave differently from what is written.

## The shape of the install

Pilot cannot take over the bench that runs production today. So the install is:

1. Build a **new VM** next to the production VM and install Pilot on it.
2. Create a Pilot bench there with the same seven apps at the same commits.
3. Restore a **copy** of the site into it, with the scheduler and outgoing email switched off.
   This is the test: if it fails, production never noticed.
4. Use Pilot against the copy until the checklist in Part 6 passes.
5. **Cut over**: one maintenance window, a fresh backup, a restore, and the load balancer pointed
   at the new VM. The old VM stays intact for a week as the way back.

Parts 0 to 6 touch nothing that production uses, and Part 5 is where Pilot's screens can first be
used. They can be done today. Part 7 (repository and Terraform changes) and Part 8 (the cutover)
are what make it the live server.

Who does what: **Nik** does everything on a VM or in the Google Cloud console. A **build
session** supplies exact commands, reads the output, fixes whatever Pilot's app validator refuses
in this repository, and writes the Part 7 pull requests. The **Sapphire Manager** merges on Nik's
word.

## Part 0: record what production runs (Nik, on the production VM, read-only)

```bash
sudo su - frappe
cd ~/frappe-bench
for a in apps/*/; do echo "== $a"; git -C "$a" remote -v | head -1; git -C "$a" rev-parse --abbrev-ref HEAD; git -C "$a" rev-parse HEAD; done
python3 -c "import json;print(sorted(json.load(open('sites/common_site_config.json'))))"
python3 -c "import json;print(sorted(json.load(open('sites/erp.sapphirefountains.com/site_config.json'))))"
df -h /home/frappe; du -sh sites/erp.sapphirefountains.com/public/files sites/erp.sapphirefountains.com/private/files
```

Keep the output. It gives each app's repository, branch and commit (Part 3 needs all three), and
the **names** of the config keys the site depends on. The two `python3` lines print key names
only, never values, so the output is safe to paste into a session.

Known already, from the database on 2026-11-11: frappe 16.56.0, erpnext 16.54.0,
erpnext_enhancements, frappe_assistant_core 3.3.0, newsletter, telephony, payments. The database
is about 5.5 GB and the attached files about 6.7 GB.

## Part 1: the new VM (Nik, Cloud console)

- In Compute Engine, open `production-erpnext-standard-vm` and choose **Create similar**. That
  copies the network, subnet, service account, no-public-address setting and the `web-frontend`
  network tag.
- Change: the name (for example `pilot-erpnext-vm`); the boot disk to a **new** Debian 12 disk of
  100 GB, balanced; remove the attached data disk and the startup script. Keep the zone
  (`us-east4-a`) and the machine type (`n2d-standard-8`), since this VM is meant to become
  production.
- Do not add it to the load balancer.

This VM is outside Terraform for now. Part 7 brings it in.

```bash
gcloud compute ssh pilot-erpnext-vm --zone=us-east4-a --project=erpnext-465317 --tunnel-through-iap
```

Then install what production's first boot installed and Pilot's installer does not: the PDF
toolchain and fonts in `startup_script_packages` (`infra/variables.tf`), and
[`infra/configs/nginx-realip.conf`](../configs/nginx-realip.conf) as
`/etc/nginx/conf.d/00-realip.conf` once nginx exists (after Part 2).

## Part 2: install Pilot (Nik, on the new VM)

Pin the release and read the installer before running it. The README's one-liner pipes the
`develop` branch's script straight into a shell; this fetches the script that belongs to the
release instead.

```bash
curl -fsSL -o /tmp/pilot-install.sh https://raw.githubusercontent.com/frappe/pilot/v0.0.100-pre-alpha/install.sh
less /tmp/pilot-install.sh
sudo sh /tmp/pilot-install.sh --user frappe --version v0.0.100-pre-alpha
```

*Confirm:* the exact form `--version` accepts (the tag as shown on Pilot's Releases page).

The installer runs twice. As root it prepares the host and the `frappe` user and stops. Then it
must run again **as that user in a real login session**, because Pilot's services are systemd
user services: `sudo su - frappe` is not enough. Follow the instruction the installer prints; a
fresh SSH login as `frappe` works.

```bash
pilot --version
pilot admin auto-update off
```

Automatic updates stay off. Pilot is upgraded on purpose, with `pilot admin upgrade`, after
reading the release notes.

## Part 3: the bench and the apps (Nik, as `frappe`; a build session reads the output)

```bash
pilot new sapphire --frappe-branch version-16
pilot -b sapphire init --commit <frappe commit from Part 0>
pilot -b sapphire get-app https://github.com/frappe/erpnext --branch version-16 --commit <sha>
pilot -b sapphire get-app https://github.com/sapphirefountains/erpnext_enhancements --branch main --commit <sha>
pilot -b sapphire get-app <repo> --branch <branch> --commit <sha>   # frappe_assistant_core, newsletter, telephony, payments
pilot -b sapphire list-apps
```

- `pilot new` asks for the **Admin panel password**. Make it long and unique; it is the key to
  the whole server. Nik types it; it never goes into a session, a file in this repository or the
  vault.
- Always pass `--branch` and `--commit`. Left alone, Pilot installs the release its registry
  lists, which has lagged the `version-16` branch (Pilot issue #755).
- `payments` must be on its `version-16` branch. Its `develop` branch declares Frappe 17 and is
  refused.
- **This is the first real test.** Pilot validates every app (Frappe version range, Python
  dependencies resolved against every other app, imports, hook paths) and has no override. If it
  refuses `erpnext_enhancements`, paste the message into a build session: the fix is a pull
  request here, not a flag there.

Before going further, in `bench.toml` or the Admin panel's Settings (*confirm* the key names;
`pilot -b sapphire set-bench-config x y` prints the valid ones):

| Setting | Value | Why |
|---|---|---|
| Lite mode | off | Lite mode is one process for web, realtime and jobs. This site's code and runbooks assume gunicorn, socketio and separate short and long workers |
| Code editor | off | Editing app files on the server drifts it away from `main` |
| Site-to-Pilot communication | off | Otherwise each site gets a "Cloud Settings" dialog in the Desk for every System Manager |

## Part 4: restore a quarantined copy (Nik)

A restored copy holds production's real credentials for Google, QuickBooks, Twilio, Stripe, Plaid
and the mail server. If its scheduler runs, it syncs, sends and prunes for real, in duplicate.
**Switch these off before the copy exists.** Put both keys in the bench's
`sites/common_site_config.json`, through the Admin panel's common site config editor or
(*confirm*) `pilot -b sapphire frappe set-config -g <key> 1 -p`:

```json
{ "pause_scheduler": 1, "mute_emails": 1 }
```

Then, on the copy:

- Nobody creates or edits business records in it. Saving a Project or an Event there can still
  call Google or QuickBooks, because those calls happen on save, not on a schedule.
- Give it a name that cannot be confused with production: `erp-copy.localhost`.

Getting the data across, in order of preference:

1. **Fresh backup, copied VM to VM.** On the production VM,
   `bench --site erp.sapphirefountains.com backup --with-files`, then copy the four files to the
   new VM over the internal network (`gcloud compute scp`). A plain backup causes no downtime.
   In the Admin panel, **create the site empty first, then use Restore on it with all four
   parts**: database, public files, private files and config.
2. **Pilot pulls it.** The restore screen can fetch the latest backup of a remote Frappe site
   itself, given the site's URL and its Administrator password. It is the least typing, but it
   means entering the Administrator password into a pre-alpha tool. Change that password
   afterwards if this route is used.

**The config file is not optional.** It carries the site's `encryption_key`, and every stored
password and API secret in the database is unreadable without it. Pilot's "new site from backup"
takes only the database and the two file archives, which is why this runbook restores into an
existing site instead. Afterwards, compare the key on both VMs without printing it:

```bash
python3 -c "import json,hashlib,sys;print(hashlib.sha256(json.load(open(sys.argv[1]))['encryption_key'].encode()).hexdigest()[:12])" <path to site_config.json>
```

The two short hashes must match.

Write down how long the restore and its migrate take. That number is the cutover's downtime.

## Part 5: reach the Admin panel

Put the bench into production mode with local-only names and no TLS. Nothing public points at
this VM yet.

```bash
pilot -b sapphire setup production --admin-domain pilot.localhost
```

From a PC with `gcloud`, open a tunnel and leave it running:

```bash
gcloud compute ssh pilot-erpnext-vm --zone=us-east4-a --project=erpnext-465317 --tunnel-through-iap -- -N -L 8080:localhost:80
```

Browsers resolve every `*.localhost` name to the PC itself, so no DNS or hosts-file change is
needed: the Admin panel is `http://pilot.localhost:8080` and the copy is
`http://erp-copy.localhost:8080`. *Confirm:* that the panel's sign-in works over plain HTTP
through the tunnel.

Then, in the panel's Settings, enroll a two-factor device and save the recovery codes.

### Parker, James and others

Two facts about how Pilot signs people in decide this:

- **There is one Admin password and there are no roles.** Everyone who signs in can do
  everything: restore over the site, drop it, read the database, log in to ERPNext as
  Administrator. Pilot can enroll several two-factor devices, one per person, but it cannot tell
  two people apart beyond that and cannot limit what either may do.
- **The panel must not be on the open internet.** A tunnel through `gcloud` works for Nik and is
  the wrong tool for people who do not use a command line.

So the access path for more than one person is a Part 7 item: a hostname for the panel on the
Google load balancer with **Identity-Aware Proxy** in front of it, allowing only named Sapphire
Google accounts. Each person signs in to Google first (which gives a per-person record and a
per-person off switch), then enters the Pilot password and their own two-factor code. Until that
exists, Parker and James see Pilot on Nik's screen or through their own tunnel.

## Part 6: checks on the copy

Tick each before planning the cutover:

- [ ] All seven apps installed at production's commits; `pilot -b sapphire list-apps` matches Part 0.
- [ ] The copy opens, and the Desk loads with its assets (no missing bundles).
- [ ] Record counts match production for three or four DocTypes of your choosing.
- [ ] A PDF prints from the copy (the PDF toolchain is a host package, see Part 1).
- [ ] Analytics, Logs, Tasks, Database and Storage all show data.
- [ ] A manual backup from the Sites screen succeeds, and its files exist.
- [ ] An app update runs end to end from the Updates screen: push a trivial commit to a test
      branch of this repository, or update one app by one commit.
- [ ] The restore time from Part 4 is an acceptable outage.
- [ ] `systemctl --user status` shows the web, socketio, worker and Redis units running, and they
      come back after a VM reboot.

## Part 7: what has to change in this repository before the cutover (build session)

Each is a draft pull request. Terraform changes are applied by Nik through the `infra-prod-apply`
trigger.

| Change | Why |
|---|---|
| **Health check** (`infra/compute.tf`): send `Host: erp.sapphirefountains.com` and probe `/api/method/ping` | Today's check is `GET /` with no host name. Pilot's nginx answers 404 to any request whose host it does not know, so the new VM would be marked unhealthy and receive no traffic |
| **Load balancer backend**: the production backend's instance group points at the new VM | The actual switch. Keep the old VM out of the group but running |
| **The new VM in Terraform** (import, or define and import its disk) | So the next `terraform apply` does not delete or ignore it |
| **`app-deploy-prod` trigger: off** | It SSHes into the old VM and runs `bench`. After cutover it would fail on every merge, or worse, restart the old VM's processes |
| **`app-upstream-prod` trigger: off** | Frappe and ERPNext upgrades move to Pilot's Updates screen |
| **Admin panel hostname behind Identity-Aware Proxy**: DNS record, Google-managed certificate, backend service, IAP with an allow-list | Part 5: how Parker and James get in |
| **`startup_script.sh`, `install_bench_units.sh`, `infra/configs/systemd/`**: mark as the old VM's, or retire | Pilot writes its own systemd user units; two sets of units for one bench is the failure to avoid |
| **Docs**: `infra/README.md`, `infra/docs/GCP_SETUP.md`, `docs/development.md`, the deploy notes in `CLAUDE.md` | Every `bench --site … <command>` becomes `pilot -b sapphire frappe --site … <command>`, and "a merge deploys" stops being true |

### How deploys work afterwards (a decision for Nik)

Pilot has no command-line "update this app" yet; updates start from the Updates screen (or its
API). With `follow_branch = true` on `erpnext_enhancements`, a merge to `main` shows up there as
an update, and someone clicks it. The update takes a safeguard database backup, migrates, and
offers Restore if a patch fails, and it leaves Redis running, so queued background jobs survive.

The cost: **merging no longer deploys by itself**, and a session cannot deploy on its own.
Deploying from CI at the tested commit is an open request in Pilot (#732). If automatic deploys
matter more than the Restore button, a build session can write a small Cloud Build step that
calls Pilot's update API on the VM; that is extra work and is not in this plan.

## Part 8: the cutover (Nik, in a quiet window; a build session on call)

1. Part 6 is ticked and the Part 7 pull requests are merged, with Terraform **not yet applied**.
2. Old VM: `bench --site erp.sapphirefountains.com set-maintenance-mode on`. Wait until
   `bench doctor` shows no pending jobs.
3. Old VM: `bench --site erp.sapphirefountains.com backup --with-files`. Copy the files to the
   new VM.
4. New VM: drop the copy site. Create the site `erp.sapphirefountains.com` empty, then Restore
   that backup into it with all four parts, and check the `encryption_key` hash as in Part 4.
5. New VM: set `host_name` to `https://erp.sapphirefountains.com` on the site (a restore does not
   carry it), and add every common-config key from Part 0 that the new bench lacks, including
   `server_script_enabled: false`.
6. Apply Terraform: health check, backend, triggers. Wait for the backend to report HEALTHY.
7. New VM: remove `pause_scheduler` and `mute_emails`. Not before step 6.
8. Verify, in this order: sign in at the real address; the version in About; a new row in
   Scheduled Job Log within five minutes; realtime (a notification arrives without a reload); a
   PDF; one outgoing email; the client address on a fresh Activity Log row is a real one, not the
   load balancer's; the ERPNext connector that sessions use still answers.
9. Old VM: `sudo systemctl stop frappe-bench` and `sudo systemctl disable frappe-bench`, so its
   scheduler can never run against the same integrations. Leave the VM and its disks alone.

Tell the team before step 2 and after step 8. Sessions started before the cutover will need a
fresh sign-in.

## Part 9: the way back

Within the first hour, if step 8 fails and cannot be fixed quickly: put the old VM back in the
backend's instance group, start `frappe-bench` on it, turn maintenance mode off there, and stop
the new VM's bench (`pilot -b sapphire stop`) so only one scheduler runs. Anything entered on the
new VM in that hour is lost, so decide early.

After real work has been entered on the new VM, the way back is closed; fix forward. Keep the old
VM a week, then snapshot its disks and delete it.

## Part 10: living with it

- **Upgrading Pilot** is a deliberate act: read the release notes, `pilot admin upgrade`, check
  the panel and the site. It shipped 13 releases in the two weeks before this was written.
- **Backups.** Pilot schedules its own hourly check and daily backup per site, kept on the VM
  unless an S3 bucket is configured. The app's nightly offsite backup to Google Drive
  (`offsite_backup`) keeps running from inside Frappe and stays the offsite copy. Check after the
  first night that both ran and that the disk is not filling.
- **Access review.** One password is shared by everyone with access. Change it whenever someone
  with access leaves, and remove their two-factor device and their Google account from the
  Identity-Aware Proxy allow-list.
- **The vault's ops runbook** describes the old VM (`sudo su - frappe`, `bench`,
  `systemctl restart frappe-bench`). It needs rewriting after the cutover, not before.

## Not known yet

- Whether `erpnext_enhancements` and the other six apps pass Pilot's validator together. Part 3
  answers it.
- Whether Pilot's nginx passes the load balancer's forwarded scheme and client address through
  correctly with TLS ending at Google. Step 8's checks answer it; `nginx-realip.conf` is the
  known fix for the address.
- Whether the Admin panel signs in over plain HTTP through a tunnel (Part 5).
- How long a restore takes (Part 4).
