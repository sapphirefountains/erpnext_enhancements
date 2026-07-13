# Test VM Setup Documentation — `test-erpnext-spot-vm`

> **Generated:** 2026-07-12  
> **Project:** `erpnext-465317`  
> **GCP Zone:** `us-east1-b`  
> **VM Name:** `test-erpnext-spot-vm`  
> **Internal IP:** `10.142.0.8`  
> **External IP:** None (outbound-only via Cloud NAT, public IP `35.194.95.244`)  
> **SSH Access:** IAP tunnel only (`gcloud compute ssh --tunnel-through-iap`)  
> **Load Balancer:** `spot-glb` → `spot-glb-http-rule` (port 80) → `targetHttpProxies/spot-glb` → `spot-glb-spot-vm-backend`  
> **Site:** `sapphirefountainstest.v.frappe.cloud` (restored from Frappe Cloud backup)

---

## 1. VM Specification

| Property | Value |
|---|---|
| Machine type | `n2d-standard-8` (8 vCPU, 32 GB RAM) |
| OS | Debian 13 "trixie" |
| Kernel | `6.12.95+deb13-cloud-amd64` |
| Architecture | `x86_64` |
| Provisioning model | SPOT (preemptible) |
| Boot disk | 50 GB pd-balanced, persistent (survives VM deletion) |
| Data disk | 200 GB pd-balanced, mounted at `/mnt/data` |

---

## 2. Disk Layout

```
NAME     SIZE  TYPE  FSTYPE  MOUNTPOINT      UUID
sda       50G  disk
├─sda1  49.9G  part  ext4    /               5b263563-...
├─sda14    3M  part
└─sda15  124M  part  vfat    /boot/efi       BA5C-9AEA
sdb      200G  disk  ext4    /mnt/data       bd498a9e-...
```

### Usage
```
Filesystem      Size  Used  Avail  Use%  Mounted on
/dev/sda1        49G   13G    35G    27%  /
/dev/sdb        196G  3.2G   183G     2%  /mnt/data
```

### Symlink
`/data` → `/mnt/data` (so all paths under `/data/` resolve to the persistent disk)

### fstab
```
PARTUUID=... /        ext4  rw,discard,errors=remount-ro,x-systemd.growfs  0 1
PARTUUID=... /boot/efi vfat defaults,umask=077                            0 2
/dev/sdb      /data   ext4  defaults,nofail                                0 2
```

---

## 3. Persistent Data Disk (`/dev/sdb` — 200 GB at `/mnt/data`)

```
/mnt/data/
├── frappe-sites/         # Frappe site data (sites folder symlink target)
│   ├── apps.json
│   ├── apps.txt
│   ├── assets/
│   ├── common_site_config.json
│   └── sapphirefountainstest.v.frappe.cloud/   # The active site
│       ├── locks/
│       ├── logs/
│       ├── private/
│       ├── public/
│       └── site_config.json
├── logs/                 # Bench logs (logs symlink target)
├── lost+found/
└── mysql/                # MariaDB data directory
```

---

## 4. Software Versions

| Component | Version |
|---|---|
| **Debian** | 13 "trixie" |
| **Linux kernel** | 6.12.95+deb13-cloud-amd64 |
| **nginx** | 1.26.3 |
| **MariaDB** | 11.8.6-MariaDB-0+deb13u1 |
| **Redis** | 8.0.2 |
| **Python (env)** | 3.14.6 (bench virtualenv) |
| **Python (system)** | 3.13.5 |
| **Node.js** | (via yarn) |
| **Bench** | 5.31.0 (installed via pipx) |

### Frappe Apps (from `apps.json`)

| App | Version | Branch | Commit |
|---|---|---|---|
| frappe | 16.26.3 | — | — |
| erpnext | 16.26.2 | version-16 | `d1d3b24` |
| newsletter | 0.0.1 | version-16 | `33ebdf9` |
| payments | 0.0.1 | version-16 | `cca07d9` |
| telephony | 0.0.1 | develop | `58d3218` |
| email_delivery_service | 0.0.1 | main | `0ad67e6` |
| wiki | 3.0.0 | version-3 | `7fe4ab2` |
| frappe_assistant_core | 2.5.0 | main | `e50c5c3` |
| erpnext_enhancements | 1.155.0 | main | `869c3b9` |

---

## 5. Network

- **VPC:** default (projects/erpnext-465317/global/networks/default)
- **Subnet:** us-east1 default (10.142.0.0/20)
- **Internal IP:** 10.142.0.8/32 (dynamic)
- **External IP:** None
- **Outbound internet:** Via Cloud NAT (IP `35.194.95.244`)
- **DNS:** Google internal (169.254.169.254), search domain `us-east1-b.c.erpnext-465317.internal`
- **Firewall:**
  - IAP SSH (35.235.240.0/20 → tcp:22) — `allow-iap-ssh-to-vms`
  - LB health checks (130.211.0.0/22, 35.191.0.0/16 → tcp:80,443,8000) — `allow-lb-to-production-vm`
  - No `ufw` (uncomplicated firewall is not active)

---

## 6. Services (systemd)

| Service | Status | Description |
|---|---|---|
| `mariadb.service` | active | MariaDB database server |
| `redis-server.service` | active | System Redis on port 6379 |
| `nginx.service` | active | nginx reverse proxy on port 80 |
| `frappe-bench.service` | active | Frappe bench processes (gunicorn:8000, socketio:9000, scheduler, worker, file watcher, and bench-managed Redis on 13000/11000) |

### Port Map
```
6379   redis-server         (system)
13000  redis_cache          (bench-managed)
11000  redis_queue          (bench-managed)
8000   gunicorn (web)       (bench-managed)
9000   socketio             (bench-managed)
80     nginx                (reverse proxy → 8000)
22     SSH                  (IAP only)
```

---

## 7. MariaDB

- **Version:** 11.8.6-MariaDB
- **Data directory:** `/data/mysql/` (on persistent disk)
- **Authentication:** `mysql_native_password` (all users)
- **Root password:** `frappe` (set during setup)
- **Users:**
  - `root`@`localhost`
  - `mysql`@`localhost`
  - `_800b9f9415173cf2`@`localhost` (site-specific database user)
- **Databases:**

| Database | Size |
|---|---|
| `_800b9f9415173cf2` | ~12 MB (site data — 239 tables) |
| `mysql` | ~5 MB |
| `sys` | 32 KB |
| `performance_schema` | 0 B |

---

## 8. Bench Directory Structure

```
/home/frappe/frappe-bench/
├── Procfile                          # Honcho process definition
├── apps/                             # All Frappe applications
│   ├── frappe                        # (core framework)
│   ├── erpnext
│   ├── email_delivery_service
│   ├── erpnext_enhancements
│   ├── frappe_assistant_core
│   ├── newsletter
│   ├── payments
│   ├── telephony
│   └── wiki
├── config/                           # Generated config files
│   ├── nginx.conf                    # nginx server block (in use)
│   ├── pids/
│   ├── redis_cache.conf
│   ├── redis_cache.acl
│   ├── redis_queue.conf
│   └── redis_queue.acl
├── env/                              # Python virtualenv (Python 3.14.6)
├── logs -> /data/logs                # Logs on persistent disk
├── patches.txt
└── sites -> /data/frappe-sites       # Sites on persistent disk
```

---

## 9. Site: `sapphirefountainstest.v.frappe.cloud`

- **Restored from:** Frappe Cloud backup (2026-07-09)
- **Database:** `_800b9f9415173cf2`
- **Database user:** `_800b9f9415173cf2`@`localhost`
- **Installed apps:** frappe, erpnext, email_delivery_service, erpnext_enhancements, frappe_assistant_core, newsletter, telephony, payments
- **nginx port:** 80
- **Default site:** Yes (`serve_default_site: true`)
- **Scheduler:** Enabled

### Site Config
```json
{
    "db_name": "_800b9f9415173cf2",
    "db_password": "cDo6wUlvARR9y19l",
    "db_type": "mariadb",
    "db_user": "_800b9f9415173cf2",
    "installed_apps": [
        "frappe", "erpnext", "email_delivery_service",
        "erpnext_enhancements", "frappe_assistant_core",
        "newsletter", "telephony", "payments"
    ],
    "nginx_port": 80
}
```

### Common Site Config
```json
{
    "background_workers": 1,
    "frappe_user": "frappe",
    "gunicorn_workers": 17,
    "live_reload": true,
    "redis_cache": "redis://127.0.0.1:13000",
    "redis_queue": "redis://127.0.0.1:11000",
    "redis_socketio": "redis://127.0.0.1:13000",
    "serve_default_site": true,
    "webserver_port": 8000
}
```

---

## 10. nginx Configuration

- **Config file:** `/etc/nginx/sites-available/frappe-bench` (symlinked from `sites-enabled/`)
- **Upstreams:**
  - `frappe-bench-frappe` → `127.0.0.1:8000` (gunicorn)
  - `frappe-bench-socketio-server` → `127.0.0.1:9000` (socketio)
- **server_name:** `sapphirefountainstest.v.frappe.cloud`
- **Static assets:** `/assets` with 1-year cache
- **Protected files:** `/protected/` internal only
- **Client max body:** 50 MB
- **Gzip:** enabled for JS, CSS, JSON, SVG, fonts, XML

---

## 11. Backup & Restore

### Files in `/home/frappe/backup/`
| File | Size | Description |
|---|---|---|
| `20260709_203020-...-database-enc.sql.gz` | 125 MB | Encrypted SQL dump (AES-256-CBC) |
| `public_...-files-enc.tar` | 162 MB | Encrypted public files archive |
| `private_...-private-files-enc.tar` | 1.8 GB | Encrypted private files archive |
| `...-site_config_backup-enc.json` | 987 B | Unencrypted site config (metadata) |

### Encryption
- **Key:** provided by user (stored in site_config_backup)
- **Algorithm:** AES-256-CBC (Frappe Cloud native format)
- **Decryption:** Handled automatically by `bench restore --encryption-key`

### Restore Command (used)
```bash
bench --site sapphirefountainstest.v.frappe.cloud restore \
  backup/database-enc.sql.gz \
  --with-public-files backup/public-files-enc.tar \
  --with-private-files backup/private-files-enc.tar \
  --encryption-key <key> \
  --mariadb-root-password <password> \
  --force
```

---

## 12. Migrations Applied

After restore, `bench migrate` ran successfully with:
- All app DocTypes updated (frappe, erpnext, email_delivery_service, erpnext_enhancements, frappe_assistant_core, newsletter, telephony, payments)
- Custom patches executed:
  - `erpnext_enhancements.patches.remove_opportunity_won_reason`
  - `erpnext_enhancements.patches.seed_hr_team_role`
  - `erpnext_enhancements.patches.default_contacts_ux_on`
  - `erpnext_enhancements.patches.backfill_contact_custom_account` (normalized 843 contacts)
  - `erpnext_enhancements.patches.seed_dispatch_user_role`
- All dashboards, fixtures, languages, portal items, and workspaces synced
- Search index rebuild queued

---

## 13. Systemd Service: `frappe-bench.service`

```ini
[Unit]
Description=Frappe Bench (Development Server)
After=network.target redis-server.service mariadb.service
Wants=redis-server.service mariadb.service

[Service]
Type=simple
User=frappe
Group=frappe
WorkingDirectory=/home/frappe/frappe-bench
Environment=PATH=/home/frappe/.local/bin:/usr/local/sbin:...
ExecStart=/home/frappe/.local/bin/bench start
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

All bench-managed processes (web, worker, scheduler, socketio, file watcher, redis_cache, redis_queue) are launched via `honcho` as a single process group.

---

## 14. Key Commands Reference

```bash
# SSH into the VM (IAP tunnel)
gcloud compute ssh test-erpnext-spot-vm \
  --project=erpnext-465317 \
  --zone=us-east1-b \
  --tunnel-through-iap

# IAP tunnel with port forwarding for local browser access
gcloud compute ssh test-erpnext-spot-vm \
  --project=erpnext-465317 \
  --zone=us-east1-b \
  --tunnel-through-iap -- -L 8000:localhost:8000

# Check service status
sudo systemctl status frappe-bench mariadb nginx redis-server

# View bench logs
sudo journalctl -u frappe-bench -n 100 --no-pager

# Bench commands (run as frappe user)
sudo -u frappe bash -c 'export PATH=$HOME/.local/bin:$PATH && cd ~/frappe-bench && bench list-apps'
sudo -u frappe bash -c 'export PATH=$HOME/.local/bin:$PATH && cd ~/frappe-bench && bench --site sapphirefountainstest.v.frappe.cloud list-apps'

# Start/stop/restart bench
sudo systemctl restart frappe-bench

# MariaDB access
sudo mysql -u root -p'frappe'

# Check health
curl -s -o /dev/null -w "%{http_code}" http://localhost/api/method/frappe.ping
curl -s -H "Host: sapphirefountainstest.v.frappe.cloud" http://localhost/ | head -20

# Check LB health
gcloud compute backend-services get-health spot-glb-spot-vm-backend \
  --project=erpnext-465317 --global
```

---

## 15. Login Credentials

| Credential | Value |
|---|---|
| Frappe site name | `sapphirefountainstest.v.frappe.cloud` |
| MariaDB root password | `frappe` |
| Site DB name | `_800b9f9415173cf2` |
| Site DB password | `cDo6wUlvARR9y19l` |
| Site DB user | `_800b9f9415173cf2` |
| Backup encryption key | User-provided |
| IAP tunnel access | `user:sinjini@d3vtech.com` |
| Administrator password | Set during `bench new-site` (not Frappe Cloud password) |

---

## 16. Links

- **Frappe site (via IAP tunnel):** `http://sapphirefountainstest.v.frappe.cloud:8000` (requires hosts file entry + IAP tunnel)
- **Load balancer (GCP):** `https://console.cloud.google.com/net-services/loadbalancing/details/http/spot-glb?project=erpnext-465317`
- **VM (GCP):** `https://console.cloud.google.com/compute/instancesDetail/zones/us-east1-b/instances/test-erpnext-spot-vm?project=erpnext-465317`
