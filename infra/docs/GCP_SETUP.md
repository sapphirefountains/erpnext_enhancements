# GCP Infrastructure Setup — Sapphire Fountains ERPNext

## Overview

This document describes the complete GCP infrastructure for hosting the ERPNext/Frappe application at Sapphire Fountains. The infrastructure is managed with Terraform using a three-state isolation model.

### Architecture Summary

```
Cloudflare (DNS)
    |
    ├── erp.sapphirefountains.com ──► GCP LB (production-glb) ──► production-erpnext-standard-vm (us-east4)
    |                                    |                            └── nginx:80 → frappe-bench:8000
    |                                    └── Port 80 → 301 redirect to HTTPS
    |
    └── beta.erp.sapphirefountains.com ──► GCP LB (spot-glb) ──► test-erpnext-spot-vm (us-central1)
                                             |                        └── nginx:80 → frappe-bench:8000
                                             └── Port 80 → 301 redirect to HTTPS
```

---

## Prerequisites

### Tools

| Tool | Version | Purpose |
|------|---------|---------|
| Terraform | >= 1.12.2 | Infrastructure provisioning |
| gcloud CLI | Latest | GCP API interaction, SSH, IAP tunneling |
| Google provider | ~> 7.27.0 | Terraform GCP provider |

### GCP Project

```bash
gcloud projects create erpnext-465317 \
  --name="ERPNext Production" \
  --organization="..."
gcloud config set project erpnext-465317
gcloud beta billing projects link erpnext-465317 \
  --billing-account=XXXXXX-XXXXXX-XXXXXX
```

### APIs

Enabled (auto-managed by Terraform based on provisioning toggles):

- `compute.googleapis.com` — VMs, LBs, networking
- `cloudbuild.googleapis.com` — CI/CD pipelines
- `cloudresourcemanager.googleapis.com` — Project management
- `iam.googleapis.com` — Service accounts, roles
- `iap.googleapis.com` — IAP tunnel access
- `secretmanager.googleapis.com` — Secret storage
- `serviceusage.googleapis.com` — API lifecycle

---

## Deployment Model

Three independent Terraform states share the same codebase, differentiated by the `deployment_mode` variable.

### State Layout

| State | Backend Prefix | tfvars | Mode | Resources |
|-------|---------------|--------|------|-----------|
| **shared** | `terraform/shared` | `shared.tfvars` | `"shared"` | Project, VPC, NAT, IAM, Cloud Build, IPs, LBs, firewalls, health checks |
| **prod** | `terraform/prod` | `prod.tfvars` | `"prod"` | Production VM, persistent boot + data disks |
| **test** | `terraform/test` | `test.tfvars` | `"test"` | Spot VM, persistent disks, snapshot schedule |

### Apply Order

For first-time provisioning:

```
1. terraform apply -var-file=shared.tfvars   # Create shared infra
2. terraform apply -var-file=prod.tfvars      # Create prod VM (creates instance group)
3. terraform apply -var-file=test.tfvars      # Create test VM (creates instance group)
4. terraform apply -var-file=shared.tfvars    # Re-apply shared to finalize LB backends
```

For subsequent updates, each state is independent.

### Backend Configuration

```bash
# shared
terraform init -backend-config=backend-shared.hcl

# prod
terraform init -backend-config=backend-prod.hcl

# test
terraform init -backend-config=backend-test.hcl
```

Each backend config:
```hcl
# backend-shared.hcl
bucket = "tf-state-v8"
prefix = "terraform/shared"
```

---

## Network Topology

### VPC

```
VPC: default (auto-mode) or custom (sapphire-vpc)
  ├── Subnet: us-east1 (default)
  ├── Subnet: us-east4 (prod VM)
  └── Subnet: us-central1 (spot VM)
```

The project uses the default VPC (`use_default_vpc = true`). A custom VPC is available by setting `use_default_vpc = false` and specifying `network` / `subnetwork`.

### Cloud NAT

Three Cloud NAT gateways for outbound internet from private VMs:

| Region | Router | NAT |
|--------|--------|-----|
| `us-east4` | `erpnext-nat-router-us-east4` | `erpnext-cloud-nat-us-east4` |
| `us-east1` | `erpnext-nat-router-us-east1` | `erpnext-cloud-nat-us-east1` |
| `us-central1` | `erpnext-nat-router-us-central1` | `erpnext-cloud-nat-us-central1` |

All NAT gateways use AUTO_ONLY IP allocation and ALL_SUBNETWORKS_ALL_IPRanges.

### Firewall Rules

| Rule | Source | Ports | Target | Purpose |
|------|--------|-------|--------|---------|
| `allow-lb-to-production-vm` | `130.211.0.0/22`, `35.191.0.0/16` | 80, 443, 8000 | tag `web-frontend` | LB health checks + forwarding |
| `allow-iap-ssh-to-vms` | `35.235.240.0/20` | 22 | all instances | IAP SSH tunnel |
| `allow-lb-to-mig` | `130.211.0.0/22`, `35.191.0.0/16` | 80, 443, 8000 | tag `erpnext-mig-node` | MIG health checks + forwarding |
| `allow-ssh-to-mig` | `35.235.240.0/20` | 22 | tag `erpnext-mig-node` | MIG IAP SSH |

### Static IPs

| Name | Type | Scope | Used By |
|------|------|-------|---------|
| `sapphire-glb-ip` | Global | External | Production LB (`136.68.113.208`) |
| `spot-glb-ip` | Global | External | Spot/test LB (`34.149.67.36`) |
| `web-ip` | Regional | External | (reserved) |

---

## Compute VMs

### Production VM

| Attribute | Value |
|-----------|-------|
| Name | `production-erpnext-standard-vm` |
| Zone | `us-east4-a` |
| Machine type | `n2d-standard-8` (8 vCPU, 32 GB) |
| Boot disk | 50 GB pd-balanced, attached from `prod-erpnext-boot-east4` |
| Data disk | 200 GB pd-balanced, attached from `prod-erpnext-data-east4` |
| Public IP | No (private, egress via Cloud NAT) |
| Network tags | `web-frontend`, `prod-loadbalancer-target` |
| Labels | `role = web-frontend` |
| nginx | Listens on port 80, proxies to `127.0.0.1:8000` (frappe-bench) |

### Spot/Test VM

| Attribute | Value |
|-----------|-------|
| Name | `test-erpnext-spot-vm` |
| Zone | `us-central1-a` |
| Machine type | `n2d-standard-2` (2 vCPU, 8 GB) |
| Provisioning | SPOT (preemptible, termination = DELETE) |
| Boot disk | Restored from latest snapshot (`test-erpnext-spot-vm-boot-restored`) |
| Data disk | 200 GB pd-balanced, restored from latest snapshot |
| Public IP | No (private, egress via Cloud NAT) |
| Network tags | `web-frontend`, `test-loadbalancer-target` |
| Labels | `role = batch-processor` |
| Snapshots | Daily at 02:00 UTC, retention 7 days, storage in `us` |

### Startup Script

The startup script (`configs/startup_script.sh`) runs on every boot:

1. **First boot:** Detects missing `bench` binary
   - Installs packages: curl, git, nginx, python3, python3-pip, python3-venv, pipx
   - Creates `frappe` user
   - Installs frappe-bench via pipx

2. **Every boot:**
   - Mounts data disk (auto-detects unmounted ext4, formats if needed, adds to fstab)
   - Starts MariaDB
   - Enables/starts `frappe-bench` systemd service
   - Creates `deploy` user with passwordless sudo for `systemctl restart frappe-bench`
   - Runs `bench setup production frappe`

3. **Reboot detection:** If `bench` command exists, skips first-boot installation

### Disk Persistence

- `reuse_existing_disks = true` preserves boot/data disks across `terraform destroy` + `terraform apply`
- `boot_disk_auto_delete = false` prevents VM deletion from removing the boot disk
- Spot VM disks are independent `google_compute_disk` resources with `lifecycle { ignore_changes = [snapshot] }` to prevent snapshot changes from triggering recreation

---

## Load Balancers

### Global External Application Load Balancers (x2)

Two HTTPS LBs terminate TLS at the edge and forward plain HTTP to the backend VMs.

```
Production LB (production-glb)
├── Frontend
│   ├── IP: 136.68.113.208
│   ├── Port 443: production-glb-https-rule → production-glb target HTTPS proxy
│   │                                                      └── production-glb URL map
│   │                                                              └── production-glb-production-vm-backend
│   │                                                                      └── production-erpnext-standard-vm:80
│   └── Port 80: production-glb-http-rule → production-glb-http-proxy
│                                                    └── production-glb-http-redirect URL map
│                                                            └── 301 redirect to HTTPS
│
├── SSL: Google-managed cert "production-glb-web-ssl-cert"
│        Domain: erp.sapphirefountains.com (ACTIVE)
│
└── Backend: production-glb-production-vm-backend
    ├── Protocol: HTTP
    ├── Port: 80
    └── Health check: HTTP / (port 80, interval 10s)

Spot LB (spot-glb)
├── Frontend
│   ├── IP: 34.149.67.36
│   ├── Port 443: spot-glb-https-rule → spot-glb target HTTPS proxy
│   │                                              └── spot-glb URL map
│   │                                                      └── spot-glb-spot-vm-backend
│   │                                                              └── test-erpnext-spot-vm:80
│   └── Port 80: spot-glb-http-rule → spot-glb-http-proxy
│                                                └── spot-glb-http-redirect URL map
│                                                        └── 301 redirect to HTTPS
│
├── SSL: Google-managed cert "spot-glb-web-ssl-cert"
│        Domain: beta.erp.sapphirefountains.com (ACTIVE)
│
└── Backend: spot-glb-spot-vm-backend
    ├── Protocol: HTTP
    ├── Port: 80
    └── Health check: HTTP / (port 80, interval 10s)
```

### SSL Certificates

Classic Google-managed SSL certificates (not Certificate Manager):

- Single domain per LB (`erp.sapphirefountains.com` / `beta.erp.sapphirefountains.com`)
- Auto-provisioned, auto-renewed (90-day renewal cycle)
- DNS verification: handled via domain provider (Cloudflare)
- Status: **ACTIVE** for both certificates

### HTTP → HTTPS Redirect

Each LB has a dedicated URL map with `default_url_redirect { https_redirect = true }` for port 80 traffic, returning HTTP 301 to the HTTPS equivalent URL.

---

## CI/CD (Cloud Build)

### Service Account

`sa-terraform-provisioner@erpnext-465317.iam.gserviceaccount.com` with 15 roles:

| Role | Purpose |
|------|---------|
| `roles/storage.admin` | Terraform state bucket |
| `roles/compute.networkAdmin` | VPC, NAT, firewall |
| `roles/compute.securityAdmin` | SSL certificates |
| `roles/compute.loadBalancerAdmin` | Load balancers |
| `roles/compute.instanceAdmin.v1` | VMs |
| `roles/run.admin` | Cloud Run |
| `roles/cloudfunctions.admin` | Cloud Functions |
| `roles/cloudsql.admin` | Cloud SQL |
| `roles/artifactregistry.admin` | Artifact Registry |
| `roles/secretmanager.admin` | Secrets |
| `roles/certificatemanager.owner` | Certificate Manager |
| `roles/resourcemanager.projectIamAdmin` | IAM |
| `roles/iam.serviceAccountUser` | Service account management |
| `roles/cloudbuild.admin` | Cloud Build |
| `roles/serviceusage.serviceUsageAdmin` | API lifecycle |

### Triggers

| Trigger | Mode | Action | Event |
|---------|------|--------|-------|
| `infra-shared-apply` | shared | apply | Manual |
| `infra-shared-destroy` | shared | destroy | Manual |
| `infra-shared-refresh` | shared | refresh | Manual |
| `infra-prod-apply` | prod | apply | Manual |
| `infra-prod-destroy` | prod | destroy | Manual |
| `infra-prod-refresh` | prod | refresh | Manual |
| `infra-test-apply` | test | apply | Manual |
| `infra-test-destroy` | test | destroy | Manual |
| `infra-test-refresh` | test | refresh | Manual |
| `app-deploy-test` | — | deploy | Push to `main` (branch) |
| `app-deploy-prod` | — | deploy | Push to `main` (disabled by default) |
| `app-upstream-test` | — | upstream update | Manual |
| `app-upstream-prod` | — | upstream update | Manual |

### Pipeline Flow

**Infrastructure pipeline** (`cloudbuild.yaml`):
1. Fix module symlinks for runner container
2. `terraform init` with backend prefix matching `$_DEPLOYMENT_MODE`
3. For shared mode: write `SECRET_TFVARS` from Secret Manager to `shared.tfvars`
4. `terraform apply` / `terraform destroy` / `terraform refresh`

**App deploy pipeline** (`cloudbuild-deploy.yaml`):
1. Extract `DEPLOY_SSH_KEY` from Secret Manager
2. Check if target VM is RUNNING
3. SSH via IAP tunnel as `deploy` user
4. `git pull`, `bench migrate`, `bench build`, `systemctl restart frappe-bench`

**Upstream update pipeline** (`cloudbuild-upstream.yaml`):
1. SSH via IAP as `deploy` user
2. `bench update --pull --no-backup`
3. `systemctl restart frappe-bench`

### Secrets in Secret Manager

| Secret | Purpose |
|--------|---------|
| `github-token` | GitHub PAT for Cloud Build v2 connection |
| `github-authorizer-credential` | GitHub App authorizer |
| `DEPLOY_SSH_KEY` | SSH private key (RSA 4096) for CI/CD deploy |

---

## DNS (Cloudflare)

Both domains are managed in Cloudflare:

| Domain | Type | Value | Proxy |
|--------|------|-------|-------|
| `erp.sapphirefountains.com` | A | `136.68.113.208` | DNS-only (gray cloud) |
| `beta.erp.sapphirefountains.com` | A | `34.149.67.36` | DNS-only (gray cloud) |

Cloudflare is DNS-only (not proxied), meaning traffic goes directly from the browser to the GCP load balancer. This avoids the need for Cloudflare SSL/TLS configuration and origin certificate validation.

---

## Variable Configuration

### shared.tfvars (gitignored)

```hcl
deployment_mode     = "shared"
project_id          = "erpnext-465317"
region              = "us-east1"
provision_ips       = true
glb_ip_name         = "sapphire-glb-ip"
domains             = ["erp.sapphirefountains.com", "beta.erp.sapphirefountains.com"]
provision_load_balancer          = true
provision_spot_vm_lb_backend     = true
provision_standard_vm_lb_backend = true
provision_cloud_build            = true
provision_cloud_nat              = true
standard_vm_name = "production-erpnext-standard-vm"
spot_vm_name     = "test-erpnext-spot-vm"
vm_region        = "us-east4"
spot_vm_region   = "us-central1"
iap_tunnel_members = ["user:admin@company.com"]
```

A template is available at `shared.tfvars.template` with placeholder values.

### prod.tfvars

```hcl
deployment_mode   = "prod"
provision_compute_vm = true
standard_vm_name     = "production-erpnext-standard-vm"
compute_machine_type = "n2d-standard-8"
vm_region            = "us-east4"
enable_vm_persistence = true
reuse_existing_disks  = true
boot_disk_source_attach = "projects/erpnext-465317/zones/us-east4-a/disks/prod-erpnext-boot-east4"
data_disk_source_attach = "projects/erpnext-465317/zones/us-east4-a/disks/prod-erpnext-data-east4"
```

### test.tfvars

```hcl
deployment_mode          = "test"
provision_spot_vm       = true
spot_vm_name            = "test-erpnext-spot-vm"
spot_machine_type       = "n2d-standard-2"
spot_vm_region          = "us-central1"
enable_vm_persistence   = true
reuse_existing_disks    = true
restore_spot_vm_from_snapshot = true
enable_spot_vm_snapshot_schedule = true
```

---

## Common Operations

### SSH into VMs

```bash
# Test VM (us-central1-a)
gcloud compute ssh test-erpnext-spot-vm \
  --zone=us-central1-a \
  --project=erpnext-465317 \
  --tunnel-through-iap

# Production VM (us-east4-a)
gcloud compute ssh production-erpnext-standard-vm \
  --zone=us-east4-a \
  --project=erpnext-465317 \
  --tunnel-through-iap
```

### Check LB health

```bash
# Backend health
gcloud compute backend-services get-health spot-glb-spot-vm-backend \
  --global --project=erpnext-465317
gcloud compute backend-services get-health production-glb-production-vm-backend \
  --global --project=erpnext-465317

# SSL cert status
gcloud compute ssl-certificates list --global --project=erpnext-465317

# Test end-to-end (bypass local DNS)
curl -s --resolve erp.sapphirefountains.com:443:136.68.113.208 \
  https://erp.sapphirefountains.com/ | head -5
curl -s --resolve beta.erp.sapphirefountains.com:443:34.149.67.36 \
  https://beta.erp.sapphirefountains.com/ | head -5
```

### Restore Spot VM from latest snapshot

```bash
# Taint disks to force recreation from newest snapshot
terraform taint google_compute_disk.spot_boot_from_snapshot
terraform taint google_compute_disk.spot_data_from_snapshot
terraform apply -var-file=test.tfvars
```

### Check VM internal configuration

```bash
# nginx config
gcloud compute ssh test-erpnext-spot-vm --zone=us-central1-a \
  --command="sudo cat /etc/nginx/sites-enabled/frappe-bench"

# Running services
gcloud compute ssh test-erpnext-spot-vm --zone=us-central1-a \
  --command="sudo ss -tlnp | grep -E '80|8000|9000|3306'"

# Frappe bench status
gcloud compute ssh test-erpnext-spot-vm --zone=us-central1-a \
  --command="sudo systemctl status frappe-bench"
```

---

## Troubleshooting

### Production LB returns 503

**Check:** Is the backend service protocol set to `HTTP`?
```bash
gcloud compute backend-services describe production-glb-production-vm-backend \
  --global --format="value(protocol)"
```

**Fix:** Should return `HTTP`, not `HTTPS`. Update `load_balancer.yaml` to include `protocol: "HTTP"` in `backend_service_configs`.

### "Site cannot be reached" in browser

**Check:** Does local DNS point to the correct IP?
```bash
dig erp.sapphirefountains.com +short
# Should return 136.68.113.208
```

**Fix:** Remove overrides from `C:\Windows\System32\drivers\etc\hosts` or equivalent.

### SSL certificate stuck in PROVISIONING

- Verify DNS A record points to the LB IP
- Google-managed certs can take 30-60 minutes to provision
- Add `_acme-challenge.<domain>` CNAME record if required by the DNS provider
- Check certificate status: `gcloud compute ssl-certificates list --global`

### Terraform state lock

```bash
# Find lock info
terraform plan -var-file=shared.tfvars
# Force unlock
terraform force-unlock <LOCK_ID>
```

---

## Module Reference

All custom modules are in `modules/` and are based on Google Cloud Foundation Fabric patterns:

| Module | Source | Purpose |
|--------|--------|---------|
| `net-lb-app-ext` | `../modules/net-lb-app-ext` | Global external application LB with SSL support |
| `compute-vm` | `../modules/compute-vm` | Compute Engine VMs with flexible disk/network config |
| `net-address` | `../modules/net-address` | Static IP management (regional, global) |
| `project` | `../modules/project` | Project management and API enablement |
| `cloudsql-instance` | `../modules/cloudsql-instance` | Cloud SQL instances with PSA |
| `secret-manager` | `../modules/secret-manager` | Secret Manager secrets + versions |
| `cloud-build-v2-connection` | `../modules/cloud-build-v2-connection` | Cloud Build v2 GitHub connection + triggers |
| `cloud-run-v2` | `../modules/cloud-run-v2` | Cloud Run v2 services |
| `cloud-function-v2` | `../modules/cloud-function-v2` | Cloud Functions v2 |
| `artifact-registry` | `../modules/artifact-registry` | Artifact Registry Docker repos |

---

## Directory Structure

```
infra/
├── *.tf                    # Root Terraform configs
├── configs/                # YAML/script templates
│   ├── load_balancer.yaml  # LB definitions (templated)
│   ├── compute_vm.yaml     # Standard VM config
│   ├── spot_vm.yaml        # Spot VM config
│   ├── ips.yaml            # IP address definitions
│   ├── startup_script.sh   # VM startup script
│   ├── sql.yaml            # Cloud SQL config
│   ├── cloud_build.yaml    # Cloud Build config
│   ├── cloud_run.yaml      # Cloud Run config
│   ├── cloud_function.yaml # Cloud Function config
│   ├── artifact_registry.yaml
│   └── ssl.yaml            # Legacy (unused)
├── modules/                # Custom Terraform modules
├── docs/                   # Documentation
├── *.tfvars                # Variable files (shared.tfvars gitignored)
├── *.hcl                   # Backend configs
├── cloudbuild*.yaml        # CI/CD pipeline definitions
└── README.md               # Detailed playbook
```
