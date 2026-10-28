# Production web tier: gunicorn instead of the Werkzeug development server

Shipped in the repo in v1.753.3. **Nothing changes on a VM until somebody runs the steps below
on it.** First comes the interim nginx guard on prod (Step 0), then the cutover: beta first,
then prod. ERPNext tracks the repo work and the VM steps as two Tasks under *Platform Hygiene &
CI* (TASK-2026-01566) on PRJ-00580.

## What was wrong

Found on 2026-10-28 while #1377 was being checked: prod's web process is `bench serve`, the
Werkzeug **development** server. Werkzeug's development server, with its debugger on, is not
meant to face the internet.

The chain on the VM:

- `frappe-bench.service` runs `honcho start` (`infra/configs/startup_script.sh` created it
  before this release; the vault's ops runbook recorded the same process list on 2026-10-06).
- honcho runs the bench's development `Procfile`, whose web line is `web: bench serve --port
  8000`.
- In frappe 16.53.0 (prod's version), `bench serve` is `frappe.app.serve()`: Werkzeug's
  `run_simple()` with its debugger and reloader on, around `application_with_statics()`.
- `infra/assets/test-vm-setup.md` lists "gunicorn:8000" for the test VM, but its own unit runs
  `bench start`. The table was wrong.

Besides the debugger:

- **The port.** `bench serve` listens on 0.0.0.0:8000. The `allow-lb-to-production-vm` firewall
  rule's ports (`lb_firewall_ports`) default to 80, 443 and 8000, opened only to Google's
  load-balancer ranges. gunicorn binds 127.0.0.1, and the health check goes to nginx on :80.
- **The reloader.** It restarted the web process whenever a `.py` file changed. A deploy's
  `git reset --hard` changes them before `bench migrate` runs, so for a minute or two every
  deploy served new code against the old schema.

## What changes

`frappe-bench.service` keeps its name and becomes an umbrella that runs nothing itself. Every
process is a unit of its own, `PartOf=frappe-bench.service` and enabled into its `.wants`. So
`sudo systemctl restart|stop|start frappe-bench` acts on all of them. The units mirror bench's
production process list (bench v5.31.1 `supervisor.conf`), under systemd, because there is no
supervisor on these VMs.

| Development Procfile line (honcho) | Production unit (`infra/configs/systemd/`) |
|---|---|
| `redis_cache: redis-server config/redis_cache.conf` | `frappe-bench-redis-cache.service`, the same command |
| `redis_queue: redis-server config/redis_queue.conf` | `frappe-bench-redis-queue.service`, the same command |
| `web: bench serve --port 8000` | `frappe-bench-frappe-web.service`: **gunicorn** on `frappe.app:application`, 127.0.0.1 only |
| `socketio: …` | `frappe-bench-node-socketio.service`: `node apps/frappe/socketio.js` |
| `watch: bench watch` (if present) | dropped: it rebuilds assets on every file change, and the deploy runs `bench build` |
| `schedule: bench schedule` | `frappe-bench-frappe-schedule.service`, the same command |
| `worker: bench worker` (every queue, one process) | `frappe-bench-frappe-short-worker@1..N` (short, default) and `frappe-bench-frappe-long-worker@1..N` (long, default, short). N is `background_workers`, 1 when unset |

gunicorn reads its settings from `common_site_config.json` through
[`infra/configs/gunicorn.conf.py`](../configs/gunicorn.conf.py), with bench's own defaults:

| Key | Setting | Default |
|---|---|---|
| `webserver_port` | `bind` | 8000 |
| `gunicorn_workers` | workers | 2 × CPUs + 1 |
| `http_timeout` | `timeout` | 120 s |
| `gunicorn_max_requests` | `max_requests` | 5000, or 0 with one worker |

There are three deliberate departures from a bare bench:

- **`limit_request_line` is 8190.** gunicorn's default is 4094. nginx accepts 8 KB and the
  development server accepted 64 KB, so 4094 would turn some long GET requests into 400s.
- **`--workers` is on gunicorn's command line as well as in the config file.**
  `frappe.concurrency_limiter` (16.53.0) reads it from `sys.argv` to size every
  `@frappe.concurrent_limit()` pool, frappe's `download_pdf` included. Left in the config file
  alone, frappe would see one worker and allow one PDF render at a time across the site. The
  unit asks the config file for the number (run as a script, it prints it).
- **The worker class is gunicorn's default, `sync`**, as bench leaves it.

[`infra/configs/install_bench_units.sh`](../configs/install_bench_units.sh) installs the units
and the gunicorn config, checks the prerequisites (`--check`) and rolls back (`--rollback`). It
never starts or stops anything. It refuses to install while any of these is true:

- **The old unit is anything but plainly stopped** (inactive, failed or unknown).
- **Something the old process tree got would not reach the units.** It checks for:
  - a bench `.env`, which honcho loaded into every process;
  - environment set in front of a Procfile command;
  - a drop-in under `frappe-bench.service.d`.
- **The node on the units' PATH is older than `apps/frappe/package.json` requires** (node 24 or
  later at 16.53.0).

## What does not change

- **nginx, after Step 0.** Prod's server block is bench's generated one, and it already serves
  `/assets` and existing `/files` from disk. Checked from outside on 2026-10-28:
  - `/assets/...` comes back with nginx's ETag format and `cache-control: max-age=31536000`;
  - a missing asset gets nginx's own 404;
  - `/files/sapphire-swirl.svg` carries the template's `content-disposition: attachment` rule
    for SVG.

  gunicorn takes the same `127.0.0.1:8000` upstream:
  - `conf.d/00-realip.conf` is untouched;
  - the `proxy_set_header X-Forwarded-For $remote_addr` line in `frappe.conf` still forwards the
    corrected client address.

  `--check` confirms the upstream and the `/assets` location before anything is switched.
- **The deploy.** `infra/cloudbuild-deploy.yaml` still runs `systemctl restart frappe-bench`,
  and the deploy user's sudoers line still allows exactly that. Both work on a VM that has been
  cut over and on one that has not, so the PR and the cutovers can land in any order. The
  deploy still FLUSHDBs both Redis instances first, which still destroys queued jobs; that is
  a separate problem.
- **Ports.** 8000 web, 9000 socketio, 13000 Redis cache, 11000 Redis queue.
- **On prod only: frappe's own `_dev_server` behavior.** It is set by `DEV_SERVER`, which `bench
  start` exports and a bare `honcho start` does not. Prod's frappe responses carry
  `cache-control: public, …`, which `_dev_server` would have overwritten with no-cache. So on
  prod it was already off, and frappe's logging level and caching do not move.
  **Beta may differ.** In July its unit ran `bench start` (`infra/assets/test-vm-setup.md`
  §13), which exports `DEV_SERVER`. Step 2's `systemctl cat frappe-bench` shows whether it
  still does. If so, `_dev_server` is on there today and goes off at the cutover: the no-cache
  headers stop, and frappe logs at ERROR instead of WARNING. Don't mistake that for a
  regression.

## What behaves differently afterwards

1. **A request is killed at `http_timeout` (120 s).** Before, nginx gave up on the client at
   120 s (the user saw a 504) but the request ran on to the end on the server. Now gunicorn
   kills the worker and the transaction rolls back. Anything that "worked despite the 504" will
   stop finishing. Long work belongs in a background job.
2. **The Triton chat stream is cut at 120 s.** `triton_chat.stream_query`
   (`erpnext_enhancements/triton_chat.py`) relays Triton's SSE stream from inside the request.
   - **Why it is cut.** A `sync` worker sends gunicorn no heartbeat while it writes a response
     out, so a stream that runs past `timeout` is killed mid-answer even while data is flowing.
     A long multi-tool turn can run that long. Each open stream also holds one of the
     `gunicorn_workers` for its whole length.
   - **Why it worked before.** Under the development server neither happened. nginx's
     `proxy_read_timeout` only limits the gap between reads, and the load balancer's backend
     timeout is 3600 s.
   - **The options, which are Nik's call** (nothing here changes the worker class):
     - raise `http_timeout`, which also lengthens how long every runaway request holds a worker;
     - run gthread workers (`--worker-class gthread --threads N`, what Frappe Cloud does), under
       which a long request is not killed at all. That also changes the premise of
       `gating_api.BATCH_TIME_BUDGET_SECONDS`;
     - serve the stream from a second gunicorn behind its own nginx location, with a longer
       timeout.

   Beta's checks include a long Triton turn so the effect is seen before prod.
3. **Concurrency is a fixed pool.** Before, there was one process with a thread per request, and
   no limit. Now there are `gunicorn_workers` processes, one request each: 17 on prod's 8 vCPUs
   unless the key says otherwise. A slow request holds one of them.
4. **Module-level state is per process.** The knowledge-base search index, for one, now exists
   once per gunicorn worker instead of once.
5. **No reloader.** New code reaches the web tier when the deploy restarts it, after `bench
   migrate`, and not before.
6. **Two RQ workers instead of one.** A long job no longer holds up short ones behind it. Two
   jobs can now run at the same time, which a single worker never allowed.
7. **Memory.** 17 gunicorn workers and 2 RQ workers is a few GB. Prod has 32 GB. Beta is an
   n2d-standard-2 with 8 GB, so check its `gunicorn_workers` (step 3 below).
8. **Logs.** gunicorn, socketio, the scheduler and Redis log to the journal under their own unit
   names, for example `journalctl -u frappe-bench-frappe-web`. The workers still append to
   `logs/worker.log` and `logs/worker.error.log`.

## Never on these VMs

- **`bench restart`** does nothing here. There is no supervisorctl, and `restart_systemd_on_update`
  is unset; leave it unset, because bench's systemd restart looks for a `frappe-bench.target`
  that does not exist. Use `sudo systemctl restart frappe-bench`.
- **`bench setup production`, `bench setup supervisor` or `bench setup systemd`.** The first
  installs supervisor and starts a second copy of every process. The other two write
  definitions that fight these units.
- **`bench start` in a terminal** starts a second stack on the same ports.
- **`bench setup nginx`.** It regenerates `config/nginx.conf`. If prod's frappe block is that
  file, it drops the Step 0 guard.

## Step 0, prod, today: the interim nginx guard

Until prod is cut over, add two lines to the frappe block's `location @webserver`. They keep the
development server's debugger out of reach. The first refuses any request carrying the
debugger's parameter. The second refuses requests whose Host the debugger would treat as local.
It does this only inside `@webserver`, so nothing else nginx serves is affected:

```nginx
        if ($arg___debugger__) { return 404; }
        if ($host ~* "^(localhost|127\.0\.0\.1)$|\.localhost$") { return 444; }
```

**Why not a `default_server` catch-all that returns 444 for unknown Hosts:**

- The load balancer's health check sets no Host of its own, so GCP sends the VM's IP address as
  the Host (`erpnext-standalone-health-check` in `infra/compute.tf` has no `host`).
- A catch-all would answer that check with 444. The check would fail, and the load balancer
  would take prod out of service.
- The second line above refuses the same dangerous Hosts without touching the check.

The commands below were run against a rendering of bench's nginx template with a real nginx,
reaching the block through a symlink as bench links it. The results:

- a plain request passed through;
- the debugger parameter got 404;
- `localhost`, `x.localhost` and `127.0.0.1:8000` as Host got 444;
- an IP-address Host passed;
- a second run added nothing;
- the rollback restored the file.

Run as yourself on the prod VM (step 1 below shows how to connect):

```bash
FILE=$(readlink -f "$(sudo grep -Rl 'location @webserver' /etc/nginx/conf.d /etc/nginx/sites-enabled | head -n 1)")
echo "$FILE"
sudo cp -p "$FILE" /root/frappe-nginx.pre-guard
sudo grep -q 'arg___debugger__' "$FILE" || sudo sed -i '/location @webserver {/a\        if ($arg___debugger__) { return 404; }\n        if ($host ~* "^(localhost|127\\.0\\.0\\.1)$|\\.localhost$") { return 444; }' "$FILE"
sudo sed -n '/location @webserver {/,/}/p' "$FILE" | head -n 4
sudo nginx -t && sudo systemctl reload nginx
```

- **The `echo`** prints the one file that holds the frappe block. `readlink -f` resolves a
  symlink, so `sed` edits the real file and the link stays a link.
- **The backup** goes to `/root`, outside every directory nginx loads, so it is never read as
  config.
- **The `grep -q`** makes a second run a no-op.
- **The `sed -n`** shows the block's first lines: `location @webserver {`, then the two new
  `if` lines.
- **If `nginx -t` fails,** nothing was reloaded. Restore the backup (below).

Check from your PC:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://erp.sapphirefountains.com/api/method/ping
curl -s -o /dev/null -w '%{http_code}\n' "https://erp.sapphirefountains.com/api/method/ping?__debugger__=yes"
```

Expect `200`, then `404`. Then confirm the site still loads in the browser. The load balancer
keeps sending traffic only while its health check passes, so a working site is that check.

To undo:

```bash
sudo cp -p /root/frappe-nginx.pre-guard "$FILE" && sudo nginx -t && sudo systemctl reload nginx
```

The guard can stay after the cutover. Under gunicorn there is no debugger, so it never fires.

## Cutover runbook (Nik)

Before you start:

- **The PR is merged and deployed.** Check that the VM's checkout has
  `apps/erpnext_enhancements/infra/configs/install_bench_units.sh` (step 2).
- **Order and timing.** Do beta first. Do prod the same day once beta passes step 9, or the next
  morning at the latest. Until prod is cut over, only Step 0 stands between it and the
  development server.
- **Downtime.** Allow about a minute: the stop in step 5 to the start in step 7. Do prod in a
  quiet hour.
- **Merges.** Ask the Sapphire Manager session to hold merges while you work, so a deploy does
  not restart the bench halfway through.
- **Who runs what.** Commands marked **(you)** run in your own login shell on the VM; that
  account has sudo. Commands marked **(frappe)** run after `sudo -iu frappe`. Never run `bench`
  with sudo.

| | Beta | Prod |
|---|---|---|
| VM | `test-erpnext-spot-vm` | `production-erpnext-standard-vm` |
| Zone | `us-central1-a` (spot; check below) | `us-east4-a` |
| URL | `https://beta.erp.sapphirefountains.com` | `https://erp.sapphirefountains.com` |

### 0. Beta only: make sure the spot VM is up and current

```bash
gcloud compute instances list --project erpnext-465317 --account nikolas.bradshaw@sapphirefountains.com --filter="name=test-erpnext-spot-vm" --format="value(name,zone,status)"
```

If it is not `RUNNING`, start it:

```bash
gcloud compute instances start test-erpnext-spot-vm --project erpnext-465317 --zone us-central1-a --account nikolas.bradshaw@sapphirefountains.com
```

If it was stopped when the PR deployed, its checkout is behind. Run the test deploy and wait for
it to finish:

```bash
gcloud builds triggers run app-deploy-test --branch=main --project erpnext-465317 --region us-east1 --account nikolas.bradshaw@sapphirefountains.com
```

**Prod only:** check that no deploy is running. This should print nothing:

```bash
gcloud builds list --ongoing --project erpnext-465317 --region us-east1 --account nikolas.bradshaw@sapphirefountains.com
```

### 1. Connect

```bash
gcloud compute ssh test-erpnext-spot-vm --project erpnext-465317 --zone us-central1-a --tunnel-through-iap --account nikolas.bradshaw@sapphirefountains.com
```

For prod, use `production-erpnext-standard-vm` and `--zone us-east4-a`.

### 2. Look at the current state (frappe)

```bash
sudo -iu frappe
cd ~/frappe-bench
ls apps/erpnext_enhancements/infra/configs/install_bench_units.sh
cat Procfile
ls -la ~/frappe-bench/.env
systemctl cat frappe-bench
grep -E '"(gunicorn_workers|background_workers|http_timeout|webserver_port|socketio_port)"' sites/common_site_config.json
bench doctor
exit
```

- **`ls install_bench_units.sh`** must find the file. If it does not, the deploy has not reached
  this VM.
- **The `Procfile`** should show `web: bench serve --port 8000`. Note any `NAME=value` in front
  of a command.
- **`ls -la .env`** should say "No such file". If there is one, honcho gave its variables to
  every process. Send Claude the variable **names** (not the values) before going on.
- **`systemctl cat frappe-bench`** should show one file, with `ExecStart=… honcho start` or
  `bench start`. Any `# /etc/systemd/system/frappe-bench.service.d/…` section is a drop-in: send
  it to Claude.
- **The `grep`** prints only those five keys, and no secrets.
- **`bench doctor`** should show no pending jobs. A stop cuts short whatever is running, as every
  deploy does.

### 3. Dry run (you)

```bash
sudo bash /home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/install_bench_units.sh --check
```

Every line should read `ok`, including `node vXX (frappe needs >= 24)`, `nginx proxies to
127.0.0.1:8000` and `nginx serves /assets itself`.

- **A `MISSING` or `BLOCKER` line stops the install.**
  - `node` missing from the units' PATH happens, for example, when node was installed with nvm.
    Note where `which node` points as frappe.
  - A `.env`, a Procfile environment prefix, a drop-in or an old node each name what they found.

  Send Claude the output.
- **A `WARNING` line.** For a different node than the Procfile's, or anything about nginx, stop
  and send Claude the output (and `sudo nginx -T` for nginx).
- **The `gunicorn:` line** shows the worker count it will run with. On **beta** (2 vCPUs, 8
  GB), if that count is above 5, set it to 5 and run the check again:

  ```bash
  sudo -iu frappe bash -c 'cd ~/frappe-bench && bench set-config -g gunicorn_workers 5 -p'
  ```

### 4. Note what listens now (you)

```bash
sudo ss -ltnp | grep -E ':(8000|9000|11000|13000)\b'
```

Expect Python on 8000 (the development server), node on 9000, and two `redis-server` processes.

### 5. Stop the old unit (you)

```bash
sudo systemctl stop frappe-bench
systemctl is-active frappe-bench
sudo ss -ltnp | grep -E ':(8000|9000|11000|13000)\b' || echo "all four ports are free"
```

`is-active` should print `inactive` (or `failed`). If anything still listens, for example a
`bench start` someone left running in a terminal, stop that process before you go on.

### 6. Install (you)

```bash
sudo bash /home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/install_bench_units.sh
```

It saves the old unit to `/var/backups/frappe-bench.service.pre-gunicorn` and ends with
`next: sudo systemctl start frappe-bench`.

### 7. Start (you)

```bash
sudo systemctl start frappe-bench
sleep 15
systemctl list-units 'frappe-bench*' --no-pager
```

Every unit should be `active running`, except `frappe-bench.service`, which is `active
exited` because it runs nothing itself.

### 8. Verify on the VM (you)

```bash
sudo ss -ltnp | grep -E ':(8000|9000|11000|13000)\b'
systemctl status frappe-bench-frappe-web --no-pager | head -n 30
pgrep -af 'frappe serve|bench serve|honcho' || echo "no development server: good"
sudo journalctl -u 'frappe-bench-*' --since '-10 min' -p warning --no-pager | tail -n 40
```

- **Ports:** `127.0.0.1:8000` belongs to gunicorn, 9000 to node, and 11000 and 13000 to
  redis-server.
- **The web unit's status** lists its process tree: the gunicorn master, whose command line
  shows `--workers N`, and N workers. If setproctitle is installed, gunicorn renames them
  `gunicorn: master [frappe-bench-web]` and `gunicorn: worker [frappe-bench-web]`; either form
  is fine.
- **The `pgrep`** must print the "good" line. The development server's own process is `python
  -m frappe.utils.bench_helper frappe serve`, hence the pattern.
- **The journal:** quiet, or nothing beyond startup noise.

### 9. Verify from outside (from your PC, Git Bash)

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://beta.erp.sapphirefountains.com/api/method/ping
curl -sI https://beta.erp.sapphirefountains.com/assets/frappe/images/frappe-framework-logo.svg | grep -i cache-control
```

Expect `200`, then `cache-control: max-age=31536000` (nginx still serves `/assets`). For prod, use
`https://erp.sapphirefountains.com`. Then run the error-page check in your ERPNext Task: it must
now be a plain 404.

Then in the browser:

- [ ] **Log in.** The Desk loads with its styles and icons.
- [ ] **Realtime.** Open DevTools (F12), go to Console, and enter `frappe.realtime.socket.connected`.
  It should print `true`.
- [ ] **Kiosk.** Open `/kiosk`, clock in, and clock back out. The interval should appear where
  it always does.
- [ ] **Upload.** Attach a small image to a test ToDo with the paperclip, then open the
  attachment. It is private, so this also exercises nginx's `/protected/` hand-off. Delete the
  ToDo afterwards.
- [ ] **Client IP.** In the Activity Log, your newest Login row shows your own public IP, not a
  Google `35.191.x` or `130.211.x` address and not `127.0.0.1`.
- [ ] **Background jobs.** About five minutes later:
  - the newest Scheduled Job Log row is minutes old;
  - the RQ Worker list shows two workers;
  - `tail -n 20 /home/frappe/frappe-bench/logs/worker.error.log` (frappe) shows nothing new.
- [ ] **Beta only: a long Triton turn.** Ask Triton something that needs several tools and runs
  for over two minutes. If the answer stops at about 120 s, that is item 2 of "What behaves
  differently". Note it for Nik's decision before prod. It is not a reason to roll back.

Then mark your Task's beta (or prod) step done and tell Claude what you saw.

### Rollback (you)

If anything above fails and the fix is not obvious, go back to the development server.

```bash
sudo systemctl stop frappe-bench
sudo bash /home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/install_bench_units.sh --rollback
sudo systemctl start frappe-bench
pgrep -af 'frappe serve|bench serve' | head -n 2
```

The last command should show the development server again. The rollback:

- puts the saved unit back (`/var/backups/frappe-bench.service.pre-gunicorn`);
- removes the new units;
- leaves the backup in place, so a later install still finds it.

On prod, keep the Step 0 guard. To try again, start at step 3.

### Changing settings later

- **gunicorn workers, timeout or port:** run `bench set-config -g <key> <value> -p` (frappe),
  then `sudo systemctl restart frappe-bench`. No re-install is needed.
- **`background_workers`** (how many RQ worker instances): set it, then re-run the installer.
  The installer rebuilds the umbrella's `.wants` and tells you the next step. After a decrease
  that step is `stop` then `start`, not `restart`: `PartOf=` restarts a running instance
  whether or not anything still wants it.
- **Any file in `infra/configs/systemd/` or `gunicorn.conf.py`:** the change reaches a VM when
  the installer runs there. That happens on every boot once the VM's startup script is the new
  one (next section), or when someone runs it by hand followed by `sudo systemctl restart
  frappe-bench`.

### The startup script

A VM's boot script is instance metadata that Terraform renders from
`infra/configs/startup_script.sh`, and only `infra-test-apply` or `infra-prod-apply` updates
it. Until that runs, a reboot runs the **old** script.

- **What the old script does:** it starts `frappe-bench`. That is fine: the new umbrella is on
  the boot disk.
- **The risk it still carries:** it also still runs `bench setup production frappe --yes ||
  true`. No supervisor was on prod on 2026-08-03 or 2026-10-06, so that call has evidently not
  got far so far. Nothing stops it from getting further on a later boot, installing supervisor,
  and starting a second copy of every process.
- **What the new script does instead:**
  - refreshes cut-over units on every boot;
  - leaves a VM that has not been cut over on its old unit;
  - generates nginx's frappe block only when `nginx -T` shows none loaded, and unlinks it again
    if it fails `nginx -t`.

Before any `infra-*-apply`, record how each VM's nginx blocks are linked, and send the output to
Claude with the plan:

```bash
ls -l /etc/nginx/conf.d /etc/nginx/sites-enabled
```

Apply it when the infra state is otherwise clean. Read `terraform plan` first; do not run
`infra-prod-apply` blind. To tell which script a VM has (1 means the old one, 0 the new):

```bash
gcloud compute instances describe production-erpnext-standard-vm --zone us-east4-a --project erpnext-465317 --account nikolas.bradshaw@sapphirefountains.com --format="value(metadata.items.startup-script)" | grep -c "bench setup production frappe --yes"
```

**A managed instance group would need changes first.** `infra/mig.tf` health-checks
`mig_health_check_port`, which defaults to 8000 (`infra/variables.tf`), and the load-balancer
firewall opens 8000. gunicorn listens on 127.0.0.1 only, so a MIG built on these units would
fail that check. Point it at nginx on :80 before enabling one. Today's VMs use the standalone
check on :80, so nothing is affected now.

## Code comments that assume gunicorn

These were written as if prod ran gunicorn. Most become true at the cutover. None was a bug
under the development server: each describes a limit that was looser then, not a guard that
failed.

| Where | What it says | Before the cutover | After |
|---|---|---|---|
| `assistant_tools/gating_api.py:25` and `:79` (`BATCH_TIME_BUDGET_SECONDS`), CHANGELOG 1.528.0 | gunicorn kills a worker past bench's `http_timeout` (120 s), so a batch stops starting actions after 45 s | False: nothing was killed; nginx 504'd the client and the request finished | **True** (sync workers, `timeout = http_timeout`). The 45 s budget is now load-bearing |
| `www/pay-card.html:165` | the request may still be running after the page gives up, "gunicorn's gthread workers, for one, are not killed at their timeout" | The point held (nothing was killed); the example did not describe this bench | The point holds up to 120 s, after which a sync worker **is** killed. The gthread example still does not describe this bench. The safety argument (Processing committed under the invoice lock, idempotency key) does not depend on either. A one-line comment fix |
| `api/item_suggest.py:53` | a 60 s timeout "well inside the web worker's" | No web-worker timeout existed; nginx's 120 s did | **True** |
| `api/kiosk_photos.py:129` | "a few requests run at once, one per worker" | False: one thread per request, no cap | **True**: at most `gunicorn_workers` at once |
| `stripe_payments/core/card_element.py:153`, `stripe_payments/README.md:437` | a slow Stripe never holds a web worker for 30 s per invoice | Harmless either way: a thread, not a scarce worker | **True**, and it matters: one of 17 |
| `stripe_payments/README.md:548`, `stripe_payments/core/invoice_pdf.py:68` | frappe's limiter pool defaults to half the web tier | False: frappe finds no fixed pool under the development server and sets **no** limit | **True** (half of `--workers`), which is why `--workers` is on the command line |
| `knowledge_base/search_service.py:27`, `:97`; `knowledge_base/README.md:789`; `work-items/WI-080-company-knowledge-base.md:555` | the search index lives "in each web worker's memory … per worker process" | One process, so one index shared by every request thread | **True**: one per gunicorn worker, each built on first search. About 0.5 MB each |
| `training/video_upload.py:14`, `training/page/training_canvas/training_canvas.js:2183`, `tests/test_training_video_upload.py:10`, `.github/workflows/ci.yml:2654`, `docs/gcs-training-cors.md:5`, CHANGELOG 1.437.0 | an upload streams "through a gunicorn worker synchronously" | Through a development-server thread; the 25 MB ceiling and the reasoning held | **True**, and the reasoning is stronger: a held worker is one of 17 |
| `procurement_print.py:27`, `public/js/project_enhancements.js:96`, `docs/procurement-tracker-map.md:380`, CHANGELOG 1.517.0 | a large synchronous print "risks the worker timeout" | The server never timed out; the user got nginx's 504 while the render carried on | **True** |
| `docs/pdf-generation.md:407` | "a large document completes inside the worker timeout" | Only nginx's 120 s applied | **True** (the same 120 s) |
| `quickbooks_online/core/api.py:174` | a full import ran "far longer than the HTTP gateway/worker timeout, which surfaced as a 504" | The 504 was nginx's gateway timeout; there was no worker timeout | **True** on both counts |
| CHANGELOG 1.744.1 (e-sign signer IP) | the TCP peer "that connected to gunicorn" is nginx | True of the development server too: nginx was the peer | True |
| `hooks.py:3561`, `:3583`; `monkeypatches.py:5`; `knowledge_base/template_guard.py:51`; `setup/dock_profiles.py:466`; `setup/README.md:265`; `ai_governance/fac_list_documents_count.py:19`, `:52`, `:209` | something happens once per worker, or in every worker | True: these mean any frappe process, web or RQ | True; the count of web processes goes from 1 to 17 |
| `decisions/adr/0009-erpnext-google-chat-triton.md:9609` (and `:8239`, `:8822`) | "nginx → frappe-bench:8000 (gunicorn)" | False | True. The chat module these describe was deleted (ADR 0011), so this is history |
| `infra/assets/test-vm-setup.md` §6 | the test VM's bench runs "gunicorn:8000" | False: its own §13 runs `bench start` | True once beta is cut over; the file now carries a note |
