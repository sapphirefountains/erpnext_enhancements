# Workflow Guide — Three-State Terraform

- [Quick Start](#quick-start)
- [File Map](#file-map)
- [Three-State Model](#three-state-model)
- [State Boundaries & Dependencies](#state-boundaries)
- [Daily Workflow](#daily-workflow)
- [Common Tasks](#common-tasks)
- [Warnings & Notes](#warnings)
- [Troubleshooting](#troubleshooting)

---

<a name="quick-start"></a>
## Quick Start

```bash
cd infra

# 1. Initialize each backend (one-time per state, or after backend changes)
terraform init -backend-config=backend-shared.hcl -reconfigure
terraform init -backend-config=backend-test.hcl -reconfigure
terraform init -backend-config=backend-prod.hcl -reconfigure

# 2. Apply by environment (switch backends between applies)
terraform apply -var-file=shared.tfvars   # project, VPC, NAT, IAM, LB, SSL, Cloud Build
terraform apply -var-file=test.tfvars     # spot VM, test disks
terraform apply -var-file=prod.tfvars     # standard VM, prod disks

# 3. See outputs for the active backend
terraform output
```

---

<a name="file-map"></a>
## File Map — What Each `.tf` Does

Every file is evaluated in **all three modes**, but resources are gated by `deployment_mode` (structural) and `provision_*` toggles (feature-level).

| File | What it manages | Active in |
|------|----------------|-----------|
| `providers.tf` | Google provider + GCS backend (`backend-{mode}.hcl`) | always |
| `variables.tf` | All input variables with types + defaults | always |
| `outputs.tf` | Post-apply outputs (IPs, VM names, SSH keys) | always |
| `project.tf` | Project reuse, API enablement | always (reads existing project) |
| `network.tf` | VPC lookup/creation, subnet, locals for network IDs | always |
| `compute.tf` | Standard VM, spot VM, instance groups, health checks, firewalls | `provision_compute_vm` / `provision_spot_vm` / shared |
| `nat.tf` | Cloud NAT routers + gateways | shared only |
| `iam.tf` | Service accounts, project IAM bindings | shared only |
| `ips.tf` | Static IP reservations (regional + global) | shared only |
| `ssl.tf` | SSL certs via Certificate Manager | shared or prod |
| `load_balancer.tf` | Global HTTP(S) LBs, backend services, URL maps | shared only |
| `cloud_build.tf` | State bucket, secrets, Cloud Build v2 connection, triggers, deploy SSH key | shared only |
| `mig.tf` | MIG SA + IAM (shared), MIG templates + autoscalers (test/prod) | depends |
| `sql.tf` | Cloud SQL instances, PSA ranges | `provision_sql` |
| `cloud_run.tf` | Cloud Run services | `provision_cloud_run` |
| `cloud_function.tf` | Cloud Functions v2 | `provision_cloud_function` |
| `artifact_registry.tf` | Artifact Registry Docker repos | `provision_artifact_registry` |

### Config files (`configs/`)

| File | Used by | Purpose |
|------|---------|---------|
| `configs/cloud_build.yaml` | `cloud_build.tf` | Cloud Build connection + trigger definitions |
| `configs/load_balancer.yaml` | `load_balancer.tf` | Backend service, URL map, TLS, forwarding rule config |
| `configs/ips.yaml` | `ips.tf` | Static IP address definitions |
| `configs/ssl.yaml` | `ssl.tf` | SSL certificate + map definitions |
| `configs/sql.yaml` | `sql.tf` | Cloud SQL instance definitions |
| `configs/cloud_run.yaml` | `cloud_run.tf` | Cloud Run service definitions |
| `configs/artifact_registry.yaml` | `artifact_registry.tf` | Artifact Registry repo definitions |
| `configs/startup_script.sh` | `compute.tf` | Rendered startup script template for VMs |

### Backend + variable files

| File | Purpose |
|------|---------|
| `backend-shared.hcl` | GCS prefix `terraform/shared` |
| `backend-test.hcl` | GCS prefix `terraform/test` |
| `backend-prod.hcl` | GCS prefix `terraform/prod` |
| `shared.tfvars` | Variables for shared infra (project, VPC, IAM, LB, Cloud Build, SSL, IPs) |
| `test.tfvars` | Variables for test env (spot VM, disks, snapshots) |
| `prod.tfvars` | Variables for prod env (standard VM, disk attaches) |

**`shared.tfvars` is gitignored** (contains GitHub token). It must be kept locally and in Secret Manager. `test.tfvars` and `prod.tfvars` are committed to the repo.

### How variables flow

```
.tfvars files ──► variables.tf ──► *.tf ──► modules/*/
                     │
               (types + defaults)
```

Each `.tf` file reads `var.*` values. Resources are conditionally created via:
- `count = var.deployment_mode == "shared" && var.provision_* ? 1 : 0` (structural + feature gate)
- `count = var.provision_compute_vm ? 1 : 0` (feature-only gate, no mode restriction)

---

<a name="three-state-model"></a>
## Three-State Model

Infrastructure is split into **three isolated Terraform states** so each environment can be planned/applied independently without affecting others.

| State | Backend prefix | Variable file | Manages |
|-------|---------------|---------------|---------|
| **shared** | `terraform/shared` | `shared.tfvars` | Project, VPC, NAT, IAM, Cloud Build connection, IPs, SSL, Load Balancers, firewalls, health checks, state bucket, MIG service account |
| **test** | `terraform/test` | `test.tfvars` | Spot VM, test disks, snapshot schedules, snapshots |
| **prod** | `terraform/prod` | `prod.tfvars` | Standard VM, prod boot/data disks |

### Why three states?

1. **Isolation** — a `terraform destroy` in test can't accidentally wipe the shared LB or the prod VM.
2. **Independent RBAC** — different operators or CI/CD pipelines can manage different scopes.
3. **Faster plans** — each state has fewer resources, so `terraform plan` is faster.
4. **Lower risk** — changes to the test VM don't require planning through the entire shared infrastructure.

### How the `deployment_mode` gate works

Every resource file uses `var.deployment_mode` to decide whether it's active:

```
shared resources → gated by count = var.deployment_mode == "shared" && var.provision_*
test resources  → gated by count = var.provision_spot_vm (no mode gate, but toggle only active in test)
prod resources  → gated by count = var.provision_compute_vm (same)
```

The `project.tf` and `network.tf` modules **always run** (no mode gate) — they read the existing project and VPC in all modes.

---

<a name="state-boundaries"></a>
## State Boundaries & Dependencies

### What depends on what

```
shared (project, VPC, LB, SSL, Cloud Build)
  │
  ├── test (spot VM) — uses shared VPC, registers in shared LB backend
  │                     uses shared Cloud Build deploy trigger
  │
  └── prod (standard VM) — uses shared VPC, registers in shared LB backend
                           uses shared Cloud Build deploy trigger
```

**Key rule**: Shared must be applied **before** test/prod, because VMs need the VPC and the LB backend configuration creates instance groups that the LB references.

### Resources that cross state boundaries

| Resource | Created in | Consumed by |
|----------|-----------|-------------|
| VPC + subnet | shared | test (VM network), prod (VM network) |
| Load balancer + backend service | shared | test (spot VM instance group), prod (standard VM instance group) |
| Instance group (unmanaged) | test/prod (in `compute.tf`) | shared (LB backend config in `load_balancer.tf`) |
| Cloud Build triggers | shared | test and prod VMs (deploy pipelines) |
| Static IPs | shared | test and prod LBs |
| SSL cert + map | shared | LB HTTPS |

### The instance group handoff (critical)

The LB lives in **shared** state, but the **instance group** that routes traffic to the VM is created alongside the VM in **test** or **prod** state.

- In `load_balancer.yaml`, the backend NEG references an instance group name. In shared mode, Terraform validates plan output but cannot resolve the actual instance group ID (it's in another state).
- This is handled by the `backend_type` config in `load_balancer.yaml` — it creates the LB backend targeting a named instance group, and the group must already exist (created by test/prod apply).
- **Order of first-time provisioning**: shared → test → prod. Shared creates the VPC and LB skeleton (without backends if the VMs aren't there yet), then test/prod create VMs with instance groups, then you re-run shared to attach the backend.

---

<a name="daily-workflow"></a>
## Daily Workflow

### Making a change

1. Edit the appropriate `.tfvars` file:
   - Shared resource change → `shared.tfvars`
   - Spot VM or test change → `test.tfvars`
   - Standard VM or prod change → `prod.tfvars`
   - Infrastructure code change → the relevant `.tf` file (works for all modes)

2. Initialize the correct backend:
   ```bash
   terraform init -backend-config=backend-shared.hcl -reconfigure
   ```

3. Plan and apply:
   ```bash
   terraform plan -var-file=shared.tfvars
   terraform apply -var-file=shared.tfvars
   ```

### Adding a new service

1. Set the `provision_*` toggle to `true` in the appropriate `.tfvars` file:
   ```hcl
   # In shared.tfvars (for shared services like Cloud NAT, IPs, SSL)
   provision_ips = true

   # In test.tfvars (for test-only services like spot VM features)
   enable_spot_vm_snapshot_schedule = true
   ```

2. If the toggle defaults to `false`, you may also need to update the API list in `project.tf` (some services require specific APIs).

3. Plan and apply the relevant state.

### Changing a VM type or disk size

Edit the relevant `.tfvars` file:

```hcl
# In test.tfvars
spot_machine_type = "n2d-standard-16"    # upgrade test VM

# In prod.tfvars
compute_machine_type = "n2d-standard-16" # upgrade prod VM
vm_data_disk_size = 500                   # resize data disk
```

Then apply that state only:
```bash
terraform init -backend-config=backend-test.hcl -reconfigure
terraform apply -var-file=test.tfvars
```

### Adding a new IAP user

Edit `shared.tfvars`:
```hcl
iap_tunnel_members = [
  "user:alice@example.com",
  "user:bob@example.com",
]
```

Apply shared state:
```bash
terraform init -backend-config=backend-shared.hcl -reconfigure
terraform apply -var-file=shared.tfvars
```

### Running CI/CD triggers

Triggers are managed in **shared** state but target VMs in test/prod:

```bash
# Deploy app to test VM
gcloud builds triggers run app-deploy-test --region=us-east1 --branch=main

# Update upstream apps on prod VM
gcloud builds triggers run app-upstream-prod --region=us-east1 --branch=main
```

### One-time: adding deploy SSH key to project metadata

After first `terraform apply` of shared state:
```bash
gcloud compute project-info add-metadata \
  --metadata="ssh-keys=deploy:$(terraform -chdir=infra output -raw deploy_ssh_public_key)"
```

---

<a name="common-tasks"></a>
## Common Tasks

### "I only want a test VM with no load balancer"

```hcl
# In test.tfvars (spot VM already disabled in shared LB by default)
provision_spot_vm = true

# In shared.tfvars — disable the LB backends for spot VM
provision_spot_vm_lb_backend     = false
provision_standard_vm_lb_backend = true   # keep prod LB if you need it
```

### "I want to change the test VM zone"

```hcl
# In test.tfvars
spot_vm_region = "us-west1"
restore_spot_vm_from_snapshot = true
```

Apply test state. The VM is recreated in the new zone from the latest snapshot.

### "I want to attach existing disks to the prod VM"

```hcl
# In prod.tfvars
boot_disk_source_attach = "projects/my-project/zones/us-east4-a/disks/prod-boot"
data_disk_source_attach = "projects/my-project/zones/us-east4-a/disks/prod-data"
reuse_existing_disks    = true
```

Apply prod state.

### "I want to destroy the test VM but keep everything else"

```bash
terraform init -backend-config=backend-test.hcl -reconfigure
terraform destroy -var-file=test.tfvars
```

The shared LB and prod VM are unaffected.

### "I want to SSH into a private VM"

```bash
gcloud compute ssh test-erpnext-spot-vm --zone us-central1-a --tunnel-through-iap
gcloud compute ssh production-erpnext-standard-vm --zone us-east4-a --tunnel-through-iap
```

Requires your user to be in `iap_tunnel_members` (managed in shared state).

### "I want to see all resources across all states"

```bash
# Shared
terraform init -backend-config=backend-shared.hcl -reconfigure
terraform state list

# Test
terraform init -backend-config=backend-test.hcl -reconfigure
terraform state list

# Prod
terraform init -backend-config=backend-prod.hcl -reconfigure
terraform state list
```

---

<a name="warnings"></a>
## Warnings & Notes

### Data Loss Risks

- **`terraform destroy` without backend switching** destroys only the current state's resources. Always verify you're on the right backend before destroy.
- **`reuse_existing_disks = false`** + destroy = data loss on the next VM recreate. Boot disk is inline and replaced.
- **Switching `reuse_existing_disks` from `true` to `false`** destroys the independent boot disk. Data is lost.
- **Disk resize** (`vm_data_disk_size`, `vm_boot_disk_size`): Terraform expands the disk in-place, but you must run `resize2fs` inside the VM to use the new space.

### Cross-State Pitfalls

- **Shared → test → prod apply order**: If you destroy and recreate shared (VPC), all VMs lose their network. Reapply test/prod after.
- **Instance group + LB mismatch**: If you delete a VM in test/prod, the shared LB still references its instance group. The LB will return 502s until you reapply shared to update the backend config (or remove the dead backend).
- **State drift**: If someone applies shared with `provision_spot_vm_lb_backend = false`, the LB stops routing to the test VM. The test VM and its instance group still exist in test state, creating an orphaned instance group.
- **`terraform refresh` in one state does not see resources in other states**: The load balancer's backend status won't show health from VMs managed in other states.

### Security

- **`shared.tfvars` contains the GitHub token** — never commit it. It's gitignored and stored in Secret Manager.
- **Secret Manager `SECRET_TFVARS`** must be manually updated when `shared.tfvars` changes, or CI/CD shared apply will use stale values.
- **Deploy SSH key** (`DEPLOY_SSH_KEY`) is auto-generated and stored in Secret Manager. The public key must be added to project metadata manually (see One-time step above).
- **IAP tunnel access** is controlled by `iap_tunnel_members` in shared state. Adding users there grants them SSH access to all VMs.

### CI/CD

- **Automatic triggers** (`app-deploy-test`, `app-deploy-prod`) run on push to the branch specified by `cloud_build_deploy_branch` (default: `main`).
- **Manual triggers** (`infra-*-{apply,destroy,refresh}`, `app-upstream-*`) are manual-only. They push with branch `manual-trigger-only` which never matches any real branch.
- **`app-deploy-prod` is disabled by default** (`disabled = true`). Enable it by removing or setting `disabled = false` in `cloud_build.tf` if you want auto-deploys to production.
- **`shared.tfvars` in CI/CD**: The pipeline writes `SECRET_TFVARS` from Secret Manager to `shared.tfvars` before running. You must update the secret whenever `shared.tfvars` contents change. This only affects shared runs — test and prod tfvars are read from the repo directly (no secrets).

### `terraform.tfvars` (legacy)

The old monolithic `terraform.tfvars` file is kept for reference but **not used** by the three-state workflow. All active configuration is in `{shared,test,prod}.tfvars`. Remove `terraform.tfvars` once you're confident the migration is stable.

---

<a name="troubleshooting"></a>
## Troubleshooting

### "terraform plan shows resources I don't own in this state"

The `deployment_mode` gate should prevent this. Check that:
- `count` expressions on resources include `var.deployment_mode == "shared"`
- You've initialized the correct backend and loaded the correct tfvars

### "Error acquiring the state lock"

Another process is applying the same state. Wait or force-unlock:
```bash
terraform force-unlock <LOCK_ID>
```

### "Backend GCS bucket doesn't exist"

Only happens if the state bucket itself is missing. The bucket is managed by shared state. Run shared apply first:
```bash
terraform init -backend-config=backend-shared.hcl -reconfigure
terraform apply -var-file=shared.tfvars
```

### "Error 403: Required 'compute.instances.create' permission"

The account running Terraform needs appropriate roles. If using a service account, ensure it has `roles/compute.admin` at project level.

### "Load balancer shows 502 / unhealthy backends"

1. Verify the VM is running: `gcloud compute instances list`
2. Verify the instance group exists: `gcloud compute instance-groups list`
3. Reapply shared state to refresh the backend config: `terraform apply -var-file=shared.tfvars`
4. Check firewall rules allow LB probes on ports 80/443/8000

### "terraform apply says no changes but I edited tfvars"

Run `terraform plan` first to see what changed. If it still says no changes, check that:
- Your variable is actually referenced by a resource (e.g., `count = var.provision_spot_vm ? 1 : 0`)
- You're using the correct backend + var-file for the variable you changed
- The resource already exists in the desired state

### "I want to see what exists without changing anything"

```bash
terraform init -backend-config=backend-shared.hcl -reconfigure
terraform show          # show full state
terraform state list    # list all resources
terraform output        # show outputs only
```
