# Service Provisioning Playbook

This playbook is a reusable, modular Terraform configuration designed to provision various Google Cloud Platform (GCP) services dynamically. The playbook supports toggling services on/off via `terraform.tfvars`, and handles network configurations dynamically using a global public/private toggling variable (`ip_external`).

## Architecture & Design Principles

The playbook is built on top of Google Cloud Foundation Fabric (CFF) modules. It features:
* **Independent Enablement:** Every resource group is wrapped in conditional statements driven by `provision_*` toggles.
* **Unified Network Toggling**: The `ip_external` variable acts as a master toggle to switch resources between public-facing configurations (assigning public IPs, setting open ingress rules) and private-only configurations.
* **VPC Networking Options**: Allows choosing between a custom VPC network/subnetwork setup or reusing the pre-existing default VPC network and subnet via the `use_default_vpc` toggle.
* **Parameterizable Settings**: All configurable fields are mapped to variables that can be modified directly in the `terraform.tfvars` file, removing any hardcoded environment details.

## Detailed Service Configurations

### 1. Project Management
Manages Google Cloud Project creation or reuse via the `create_project` variable. 
* Enables required APIs dynamically based on the enabled provisioning toggles.
* Connects the project to a billing account if creating a new project.

### 2. IP Addresses (Static IP Allocations)
Allocates regional static IP addresses and global IP addresses.
* If `ip_external` is set to `true`, regional static IP addresses are provisioned as external (public) IPs.
* If `ip_external` is set to `false`, regional static IP addresses are provisioned as internal (private) IPs.

### 3. Managed SSL & Certificate Manager
Provisions managed and self-managed SSL certificates and maps them to a Certificate Map.
* Connects seamlessly with the Application Load Balancer to secure client traffic.

### 4. Cloud Run Services
Deploys Cloud Run (v2) services with customizable revision templates, environment variables, and secrets.
* Ingress settings are set dynamically: `INGRESS_TRAFFIC_ALL` if `ip_external` is true, and `INGRESS_TRAFFIC_INTERNAL_ONLY` if it is false (which can be overridden in `service_config`).

### 5. Cloud Functions (v2)
Provisions v2 Cloud Functions, including custom runtime configuration, memory, bucket sources, and IAM.
* Ingress settings default to `ALLOW_ALL` if `ip_external` is true, and `ALLOW_INTERNAL_ONLY` if false.

### 6. Standard Compute VMs
Provisions standard Compute Engine virtual machines.
* **External IP Control**: Uses `vm_ip_external` to control external IP assignment, falling back to the master `ip_external` toggle if not set.
* **Disk Configuration**: Boot disk is 50GB pd-balanced, sized by `vm_boot_disk_size`, controlled by `boot_disk_auto_delete`.
* **Disk Lifecycle**: Supports the same `reuse_existing_disks` toggle as Spot VMs. When `true`, the boot disk is promoted to an independent resource (named `<vm>-boot`) that persists across VM recreation.
* **Boot Disk Deletion**: `boot_disk_auto_delete` (default `false`) controls whether the boot disk is deleted when the instance is deleted. Set to `true` to clean up automatically.

### 7. Spot Compute VMs
Provisions ephemeral, cost-efficient Spot VMs. Supports two disk lifecycle strategies controlled by `reuse_existing_disks`.

* **External IP Control**: Integrates the same `vm_ip_external` (falling back to `ip_external`) public/private IP toggle logic.
* Supports customizable termination actions (`STOP` or `DELETE`).
* **Load Balancer Backend**: Controlled by `provision_spot_vm_lb_backend`. When `true`, creates an unmanaged instance group and registers the VM as a backend in the Load Balancer. When `false` (default), the VM is standalone with no LB integration.
* **Disk Configuration**:
  * **Boot Disk**: 50GB pd-balanced, sized by `vm_boot_disk_size`, controlled by `boot_disk_auto_delete`.
  * **Data Disk**: 200GB pd-balanced, sized by `vm_data_disk_size`, created as an independent resource.
* **Disk Lifecycle** (`reuse_existing_disks` toggle):
  * `false` (default): Boot disk is inline — survives VM deletion (if `boot_disk_auto_delete = false`) but a new one is created on each replacement. Orphan disks accumulate.
  * `true`: Boot disk is promoted to an independent `google_compute_disk` resource (named `<vm>-boot`). Both boot and data disks persist fully independently and are reattached on VM recreation. No orphan accumulation.
* **Boot Disk Deletion**: `boot_disk_auto_delete` (default `false`) controls whether the boot disk is deleted when the instance is deleted. Set to `true` to clean up automatically.

### 8. Cloud SQL Database Instances
Deploys Cloud SQL database instances dynamically using the `cloudsql-instance` module.
* If `ip_external` is set to `true`, the instance is configured with public IP access (`public_ipv4 = true`).
* If `ip_external` is set to `false`, the instance is configured with private IP access via Private Services Access (PSA) on the specified VPC network.

### 9. External Application Load Balancers
Provisions Global External Application Load Balancers (HTTP/HTTPS) with custom URL maps, backend services, Network Endpoint Groups (NEGs), and SSL certificate map bindings.

* **Dynamic Backend Selection**: The `default_service` is automatically resolved from the first provisioned backend in priority order: Prod MIG → Test MIG → Standard VM → Spot VM → Cloud Run. The LB module is skipped entirely when no backends are provisioned (`default_service` is `null`).
* **Multiple Backend Support**: Backend types can be combined arbitrarily by enabling their respective `provision_*` toggles. Each backend type is defined as a conditional block in `configs/load_balancer.yaml`.
* **Spot VM Integration**: Use `provision_spot_vm_lb_backend` to toggle whether the Spot VM is included as a backend. When enabled, the module automatically creates an unmanaged instance group for the VM.
* **Firewall**: `allow-lb-to-production-vm` ingress rule opens ports 80/443 to Google Cloud Load Balancer health checker ranges (`130.211.0.0/22`, `35.191.0.0/16`). Targets VMs tagged `web-frontend`.

### 10. IAP SSH Tunnel Access (Secure VM Connectivity)
Enables secure SSH access to VM instances without public IPs via Identity-Aware Proxy (IAP) TCP forwarding.

* **Firewall Rule**: `allow-iap-ssh-to-vms` allows TCP port 22 from the IAP proxy range (`35.235.240.0/20`) to **all instances** in the VPC network (no tag restriction). Controlled by `enable_iap_ssh_firewall`.
* **IAM Binding**: Grants `roles/iap.tunnelResourceAccessor` to users listed in `iap_tunnel_members`, enabling them to tunnel through IAP.
* **Usage**: Connect with `gcloud compute ssh --tunnel-through-iap --project <PROJECT> --zone <ZONE> <VM_NAME>`.
* **Dependencies**: Requires `iap.googleapis.com` to be enabled (included in `base_apis`).

### 11. CI/CD Cloud Build Triggers
Deploys Cloud Build (v2) connections and triggers securely, utilizing Google Secret Manager to store GitHub Personal Access Tokens (PATs) for repository mirroring and validation.

### 12. Artifact Registry
Provisions Google Cloud Artifact Registry Docker repositories dynamically using the `artifact-registry` module.
* Dynamically parses configurations from `configs/artifact_registry.yaml` to create repositories.
* Grants the `roles/artifactregistry.reader` role on the repository to the Cloud Run Service Agent (`service-${project_number}@serverless-robot-prod.iam.gserviceaccount.com`), enabling Cloud Run to fetch container images securely.

### 13. CI/CD — Cloud Build Deploy & Upstream Triggers

Automatically deploys Frappe app updates to test/production VMs when code is pushed to the repo.

**How it works:**
- **4 triggers** created in Cloud Build: `deploy-test`, `deploy-prod`, `upstream-test`, `upstream-prod`.
- Each trigger SSHes into the target VM via IAP as the `deploy` user, runs bench commands, and restarts `frappe-bench.service`.
- The deploy SSH key pair is generated by Terraform (`tls_private_key`), the private key is stored in Secret Manager (`DEPLOY_SSH_KEY`), and the public key must be added to project metadata (one-time manual step after `terraform apply`).

**Manual one-time step after first apply:**
```bash
gcloud compute project-info add-metadata \
  --metadata="ssh-keys=deploy:$(terraform -chdir=infra output -raw deploy_ssh_public_key)"
```

**Cloud Build YAML files (set `cloudbuild_deploy_yaml_path` / `cloudbuild_upstream_yaml_path` if you move them):**
| File | Purpose |
|------|---------|
| `infra/cloudbuild-deploy.yaml` | Pulls only `erpnext_enhancements`, runs `bench migrate && bench build`, restarts service |
| `infra/cloudbuild-upstream.yaml` | Pulls all upstream Frappe apps (`bench update --pull`), restarts service |

**Substitutions used by the YAML files:**
| Substitution | Purpose |
|---|---|
| `_VM_NAME` | Target VM name |
| `_VM_ZONE` | Target VM zone |
| `_ALLOW_SKIP` | When `true`, skips gracefully if VM is not RUNNING (e.g. preempted spot VM) |

**IAM**: Cloud Build SA is granted `iap.tunnelResourceAccessor` role for IAP SSH. Trigger configs reference YAML paths via `cloudbuild_deploy_yaml_path` / `cloudbuild_upstream_yaml_path` variables.

### 14. Spot VM Snapshot Schedule & Restore

**Automated daily snapshots** (`enable_spot_vm_snapshot_schedule`):
- Creates a `google_compute_resource_policy` with a daily schedule (default: 02:00 UTC).
- Attached to both the boot disk and data disk of the spot VM.
- Retention: `snapshot_schedule_retention_days` (default: 7).

**Restore from snapshot** (`restore_spot_vm_from_snapshot`):
- When `true`, Terraform queries GCP for the latest snapshot matching the spot VM's boot/data disk names using a regex filter.
- Creates **persistent "restored" disks** named `<vm>-boot-restored` and `<vm>-data-restored` from those snapshots.
- The spot VM always references these named "restored" disks — they are created once and stable via `lifecycle { ignore_changes = [snapshot] }`.
- Flipping `restore_spot_vm_from_snapshot` between `true`/`false` no longer destroys disks or recreates the VM.
- To restore from a newer snapshot: `terraform taint google_compute_disk.spot_boot_from_snapshot[0]` and `google_compute_disk.spot_data_from_snapshot[0]`.

**Persistent disks**: After the one-time migration, the spot VM boots from `test-erpnext-spot-vm-boot-restored` and stores data on `test-erpnext-spot-vm-data-restored`. These disks survive VM recreation.

### 15. Managed Instance Groups (MIG) & Autoscaling
Provisions regional and/or zonal Managed Instance Groups (MIGs) and Templates designed for ERPNext database consolidation.
* **Flexibility**: Supports provisioning zonal only, regional only, or both concurrently via `use_zonal_mig` and `use_regional_mig` toggles.
* **Cost Optimization Strategy**: Employs standard N2D AMD instances for the Production environment (eligible for 1-Year Committed Use Discounts) and Spot preemptible N2D AMD instances for the Testing environment to minimize compute charges.
* **Stateful Storage**: Implements `stateful_disk` rules (`delete_rule = "NEVER"`) ensuring the 200GB Balanced Persistent Disk (`pd-balanced`) carrying database records and Frappe user files is preserved and re-attached when instances are preempted or updated.
* **Local SSD Scratch Disks**: Configures high-performance NVMe Local SSDs (375GB) dynamically inside templates to optimize database IOPS for the self-managed MariaDB server.
* **Autoscaling & Cost Cap Toggles**: Configures optional CPU-based autoscalers constrained to a default maximum replica size of 1 instance to prevent unexpected billing.

---

## Deployment & Usage

### 1. Customize Variables
Set up your configurations in [terraform.tfvars](terraform.tfvars). Make sure to replace all `<PLACEHOLDERS>` (e.g. `<YOUR_GCP_PROJECT_ID>`, `<YOUR_PREFERRED_REGION>`) with actual target values.

### 2. Initialize Terraform
Run `terraform init` to download required providers (e.g., `google`, `google-beta`, `random`) and load the local fabric modules:
```bash
terraform init
```

### 3. Validate Configuration
Ensure the configurations are syntactically correct and satisfy module requirements:
```bash
terraform validate
```

### 4. Apply Configuration
Run `terraform plan` and `terraform apply` to provision the resources:
```bash
terraform plan
terraform apply
```

---

## Detailed Guide to Enabling and Configuring Services

Each service in this playbook can be toggled independently via boolean variables in `terraform.tfvars`, and its specific options can be tailored via YAML files in the `configs/` directory.

### Enabling Services
Modify [terraform.tfvars](terraform.tfvars) to set the following toggles to `true` or `false`:

| Toggle Variable | Target Service | Configuration File | Key Features Enabled |
|---|---|---|---|
| `provision_artifact_registry` | Artifact Registry | [configs/artifact_registry.yaml](configs/artifact_registry.yaml) | Docker repositories, automated reader IAM roles for Cloud Run |
| `provision_ips` | IP Addresses | [configs/ips.yaml](configs/ips.yaml) | Static public/private IP addresses allocation |
| `provision_ssl` | SSL Certificate Manager | [configs/ssl.yaml](configs/ssl.yaml) | SSL Certificates and Maps |
| `provision_cloud_run` | Cloud Run Services | [configs/cloud_run.yaml](configs/cloud_run.yaml) | Serverless containers, ingress routing, env vars |
| `provision_cloud_function` | Cloud Functions (v2) | [configs/cloud_function.yaml](configs/cloud_function.yaml) | Event-driven code deployment from Cloud Storage |
| `provision_compute_vm` | Standard Compute VMs | [configs/compute_vm.yaml](configs/compute_vm.yaml) | Compute Engine VMs, startup scripts, network interfaces |
| `provision_spot_vm` | Spot VMs | [configs/spot_vm.yaml](configs/spot_vm.yaml) | Cost-effective VM instances with termination actions |
| `provision_spot_vm_lb_backend` | Spot VM LB Backend | [configs/spot_vm.yaml](configs/spot_vm.yaml) | Registers Spot VM as LB backend (creates instance group) |
| `provision_sql` | Cloud SQL Instances | [configs/sql.yaml](configs/sql.yaml) | Managed databases, Private Services Access (PSA) |
| `provision_load_balancer` | Application Load Balancer | [configs/load_balancer.yaml](configs/load_balancer.yaml) | Global HTTP/HTTPS Load Balancer, routing rules, backend groups |
| `provision_cloud_build` | Cloud Build CI/CD | [configs/cloud_build.yaml](configs/cloud_build.yaml) | GitHub repository mirroring, webhook validation triggers |
| `provision_prod_mig` | Production MIG | - | Production environment MIG (N2D AMD family, stateful data disk, Local SSD) |
| `provision_test_mig` | Testing MIG | - | Testing environment MIG (N2D AMD Spot family, stateful data disk, Local SSD) |
| `reuse_existing_disks` | VM Disk Lifecycle | [configs/spot_vm.yaml](configs/spot_vm.yaml), [configs/compute_vm.yaml](configs/compute_vm.yaml) | Promotes boot disk to independent resource — persists across VM recreation for both Spot and Standard VMs |
| `boot_disk_auto_delete` | Boot Disk Deletion | [configs/spot_vm.yaml](configs/spot_vm.yaml), [configs/compute_vm.yaml](configs/compute_vm.yaml) | Controls `auto_delete` flag on boot disk — `false` (default) preserves the disk when the VM is deleted |
| `enable_iap_ssh_firewall` | IAP SSH Tunnel | - | Allows IAP SSH access to all VMs, no public IP required |
| `iap_tunnel_members` | IAP Tunnel Users | - | IAM members granted `roles/iap.tunnelResourceAccessor` |
| `restore_spot_vm_from_snapshot` | Spot VM Restore | [compute.tf](compute.tf) | Creates spot VM disks from latest snapshot; stable via `ignore_changes` |
| `enable_spot_vm_snapshot_schedule` | Snapshot Schedule | [spot_vm.yaml](configs/spot_vm.yaml) / [compute.tf](compute.tf) | Automated daily snapshots for spot VM boot and data disks |
| `snapshot_schedule_*` | Snapshot Timing | [spot_vm.yaml](configs/spot_vm.yaml) | Start time, retention days, storage location for automated snapshots |
| `health_check_*` | Health Check Tuning | [compute.tf](compute.tf) | Timing, thresholds, and request path for LB health probes |
| `lb_firewall_ports` / `lb_source_ranges` | LB Firewall | [compute.tf](compute.tf) | Ports and source IP ranges for load balancer traffic |
| `iap_source_range` | IAP Firewall | [compute.tf](compute.tf) | Source IP range for IAP SSH tunneling |
| `disk_type` | Disk Configuration | [compute.tf](compute.tf) | Disk type for boot and data disks (pd-balanced, pd-ssd, etc.) |
| `local_ssd_size` | Disk Configuration | [compute.tf](compute.tf) | Size in GB for each local SSD scratch disk |
| `vm_labels` / `spot_vm_labels` | VM Labels | [compute_vm.yaml](configs/compute_vm.yaml) / [spot_vm.yaml](configs/spot_vm.yaml) | Custom labels applied to standard and spot VMs |
| `startup_script_packages` | Startup Script | [startup_script.sh](configs/startup_script.sh) | APT packages installed on first boot |
| `deploy_user` / `deploy_user_sudo_command` | CI/CD Deploy User | [startup_script.sh](configs/startup_script.sh) | Username and sudo command for CI/CD SSH access |
| `provision_cloud_build` | CI/CD Triggers | [cloud_build.tf](cloud_build.tf) | Creates deploy and upstream Cloud Build triggers with IAP SSH + SSH key |
| `cloudbuild_deploy_yaml_path` | CI/CD YAML Path | [cloud_build.tf](cloud_build.tf) | Path to `cloudbuild-deploy.yaml` in repo (default: `infra/cloudbuild-deploy.yaml`) |
| `cloudbuild_upstream_yaml_path` | CI/CD YAML Path | [cloud_build.tf](cloud_build.tf) | Path to `cloudbuild-upstream.yaml` in repo (default: `infra/cloudbuild-upstream.yaml`) |

---

### Step-by-Step Scenario: Building, Pushing, and Running a Container Image with Artifact Registry and Cloud Run

To configure Cloud Run to run an image built and stored in your own project's Artifact Registry, follow these steps:

#### 1. Enable Artifact Registry
In `terraform.tfvars`, set the provisioning toggle:
```hcl
provision_artifact_registry = true
```
Ensure your repository configuration in `configs/artifact_registry.yaml` contains:
```yaml
app-images:
  format:
    docker:
      standard: {}
  description: "Docker repository for Cloud Run images"
```

#### 2. Run Terraform Apply
Deploy the project and the registry repository first:
```bash
terraform apply
```

#### 3. Build & Push Your Container Image
Authenticate your local Docker client to your Artifact Registry:
```bash
gcloud auth configure-docker <REGION>-docker.pkg.dev
```
Build your container image:
```bash
docker build -t <REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1 .
```
Push the container image:
```bash
docker push <REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1
```

#### 4. Configure Cloud Run to Pull from the Registry
In `terraform.tfvars`, enable Cloud Run and specify the container image path pointing to your newly created registry:
```hcl
provision_cloud_run = true
cloud_run_image     = "<REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1"
```
During deployment, the playbook automatically:
1. Provisions the registry `app-images`.
2. Creates the Cloud Run service identity.
3. Grants `roles/artifactregistry.reader` permission on the registry to the Cloud Run service identity (`service-${project_number}@serverless-robot-prod.iam.gserviceaccount.com`).
4. Provisions the Cloud Run service pulling your image.

Run the final apply to deploy the Cloud Run service:
```bash
terraform apply
```

### Step-by-Step Scenario: Spot VM with Persistent Disks and Load Balancer Backend

This scenario provisions a Spot VM with independent (reusable) disks and registers it as a backend behind the Application Load Balancer.

#### 1. Enable Services and Configure Disks

In `terraform.tfvars`, set the provisioning toggles and disk settings:

```hcl
provision_spot_vm              = true
provision_spot_vm_lb_backend   = true
provision_load_balancer        = true
reuse_existing_disks           = true
boot_disk_auto_delete          = false
vm_boot_disk_size              = 50
vm_data_disk_size              = 200
```

#### 2. Run Terraform Apply

```bash
terraform apply
```

This creates the following resources:

| Resource | Name Pattern | Details |
|---|---|---|
| Spot VM | `<prefix>-spot-vm` | N2-standard-4, Spot provisioning, no public IP |
| Boot Disk | `<prefix>-spot-vm-boot` | 50GB pd-balanced, independent resource |
| Data Disk | `<prefix>-spot-vm-data-disk` | 200GB pd-balanced, independent resource |
| Instance Group | `<prefix>-spot-vm-ig` | Unmanaged, single VM |
| LB Backend Service | `<prefix>-l7-xlb-backend-default` | Points to the instance group |

Both disks have `auto_delete = false` and persist as standalone resources. If the Spot VM is preempted or you run `terraform destroy` and re-apply, the disks are reattached to the new VM — no data loss, no orphan accumulation.

#### 3. Connect via IAP

Since no public IP is assigned, use IAP SSH tunneling:

```bash
gcloud compute ssh --tunnel-through-iap --project <PROJECT> --zone <ZONE> <prefix>-spot-vm
```

#### 4. Switching Disk Lifecycle Strategies

To switch from independent disks back to inline disks (default behavior):

```hcl
reuse_existing_disks = false
```

**Important**: There is a one-time transition when switching from `false` → `true`:
- Terraform detaches the inline boot disk and creates a new independent disk.
- The old inline disk becomes an orphan — delete it manually via `gcloud compute disks delete`.
- All future applies reuse the independent disk cleanly.

---
<!-- BEGIN TFDOC -->
## Variables

| name | description | type | required | default |
|---|---|---|---|---|
| [project_id](variables.tf#L132) | The ID of the project to create or reuse. | <code>string</code> | ✓ |  |
| [api_url](variables.tf#L17) | The API URL used by the frontend service container. | <code>string</code> |  | <code>&#34;https:&#47;&#47;api.example.com&#34;</code> |
| [billing_account_id](variables.tf#L23) | The billing account ID to associate with the created project. | <code>string</code> |  | <code>null</code> |
| [cloud_build_connection](variables.tf#L29) | The name of the Cloud Build connection. | <code>string</code> |  | <code>&#34;github-pipeline-connection&#34;</code> |
| [cloudbuild_deploy_yaml_path](variables.tf#L489) | The file path inside the repo pointing to cloudbuild-deploy.yaml. | <code>string</code> |  | <code>&#34;infra&#47;cloudbuild-deploy.yaml&#34;</code> |
| [cloudbuild_upstream_yaml_path](variables.tf#L495) | The file path inside the repo pointing to cloudbuild-upstream.yaml. | <code>string</code> |  | <code>&#34;infra&#47;cloudbuild-upstream.yaml&#34;</code> |
| [cloudbuild_yaml_path](variables.tf#L483) | The file path inside the repo pointing to the infra cloudbuild.yaml. | <code>string</code> |  | <code>&#34;infra&#47;cloudbuild.yaml&#34;</code> |
| [cloud_build_github_token](variables.tf#L35) | The GitHub Personal Access Token (PAT) for Cloud Build connection. | <code>string</code> |  | <code>&#34;ghp_1234567890abcdefghijklmnopqrstuvwxyz&#34;</code> |
| [cloud_build_installation_id](variables.tf#L41) | The GitHub App installation ID on the repo. | <code>number</code> |  | <code>12345678</code> |
| [cloud_build_repo_uri](variables.tf#L47) | The remote URI of the repository for Cloud Build connection. | <code>string</code> |  | <code>&#34;https:&#47;&#47;github.com&#47;example-org&#47;example-repo.git&#34;</code> |
| [cloud_function_bucket](variables.tf#L53) | Bucket name where Cloud Function source archives are uploaded. | <code>string</code> |  | <code>&#34;demo-function-deploy-bucket&#34;</code> |
| [cloud_run_image](variables.tf#L59) | The container image to deploy to Cloud Run. | <code>string</code> |  | <code>&#34;us-docker.pkg.dev&#47;cloudrun&#47;container&#47;hello&#34;</code> |
| [compute_machine_type](variables.tf#L65) | The machine type for standard Compute Engine VM instances. | <code>string</code> |  | <code>&#34;e2-medium&#34;</code> |
| [create_project](variables.tf#L71) | Whether to create a new project or reuse an existing one. | <code>bool</code> |  | <code>true</code> |
| [deploy_user](variables.tf#L680) | Username for the CI/CD deploy user created on VMs. | <code>string</code> |  | <code>&#34;deploy&#34;</code> |
| [deploy_user_sudo_command](variables.tf#L686) | The specific sudo command allowed for the deploy user without a password. | <code>string</code> |  | <code>&#34;&#47;usr&#47;bin&#47;systemctl restart frappe-bench&#34;</code> |
| [disk_type](variables.tf#L649) | Default disk type for VM boot and data disks (e.g. pd-balanced, pd-ssd, pd-standard). | <code>string</code> |  | <code>&#34;pd-balanced&#34;</code> |
| [domain_name](variables.tf#L77) | The domain name for the managed SSL certificate. | <code>string</code> |  | <code>&#34;app.example.com&#34;</code> |
| [enable_prod_autoscaling](variables.tf#L556) | Toggle to enable/disable autoscaling for the production MIG. | <code>bool</code> |  | <code>false</code> |
| [enable_test_autoscaling](variables.tf#L562) | Toggle to enable/disable autoscaling for the testing MIG. | <code>bool</code> |  | <code>false</code> |
| [enable_spot_vm_snapshot_schedule](variables.tf#L770) | If true, attaches a daily snapshot schedule to the spot VM's boot and data disks. | <code>bool</code> |  | <code>false</code> |
| [glb_ip_name](variables.tf#L83) | The name of the global external IP address for the load balancer. | <code>string</code> |  | <code>&#34;glb-ip&#34;</code> |
| [health_check_healthy_threshold](variables.tf#L615) | The number of consecutive successes to mark a VM as healthy. | <code>number</code> |  | <code>2</code> |
| [health_check_interval_sec](variables.tf#L603) | The interval in seconds between health check probes. | <code>number</code> |  | <code>10</code> |
| [health_check_request_path](variables.tf#L627) | The URL path used by health checks to probe VM readiness. | <code>string</code> |  | <code>&#34;&#47;&#34;</code> |
| [health_check_timeout_sec](variables.tf#L609) | The timeout in seconds for each health check probe. | <code>number</code> |  | <code>5</code> |
| [health_check_unhealthy_threshold](variables.tf#L621) | The number of consecutive failures to mark a VM as unhealthy. | <code>number</code> |  | <code>3</code> |
| [iap_source_range](variables.tf#L643) | Source IP range for IAP SSH tunneling. | <code>string</code> |  | <code>&#34;35.235.240.0&#47;20&#34;</code> |
| [iap_tunnel_members](variables.tf#L593) | List of members (users/groups/SAs) to grant IAP tunnel access for SSH. | <code>list(string)</code> |  | <code>[]</code> |
| [ip_external](variables.tf#L89) | Toggle static IPs, Cloud SQL, VMs, and Cloud Run to be external (true) or internal (false). | <code>bool</code> |  | <code>false</code> |
| [lb_firewall_ports](variables.tf#L633) | List of TCP ports opened to the load balancer probe source ranges. | <code>list(string)</code> |  | <code>[&#34;80&#34;, &#34;443&#34;, &#34;8000&#34;]</code> |
| [lb_source_ranges](variables.tf#L638) | Source IP ranges for Google Cloud Load Balancer health probes and traffic. | <code>list(string)</code> |  | <code>[&#34;130.211.0.0&#47;22&#34;, &#34;35.191.0.0&#47;16&#34;]</code> |
| [local_ssd_size](variables.tf#L654) | Size in GB for each local SSD scratch disk. | <code>number</code> |  | <code>375</code> |
| [mig_data_disk_size](variables.tf#L580) | The storage capacity in GB for the stateful Balanced Persistent Disk attached to MIG instances. | <code>number</code> |  | <code>200</code> |
| [mig_health_check_port](variables.tf#L592) | The application/health-check port for the ERPNext instances. | <code>number</code> |  | <code>8000</code> |
| [mig_local_ssd_count](variables.tf#L586) | Number of high-performance Local SSDs to attach to each MIG instance (each is 375 GB). | <code>number</code> |  | <code>1</code> |
| [network](variables.tf#L95) | The VPC network to deploy resources into. | <code>string</code> |  | <code>&#34;default&#34;</code> |
| [prefix](variables.tf#L101) | An optional prefix applied to created resources. | <code>string</code> |  | <code>null</code> |
| [prod_autoscaling_max_replicas](variables.tf#L568) | The maximum number of instances for the production MIG autoscaler. | <code>number</code> |  | <code>1</code> |
| [prod_mig_machine_type](variables.tf#L532) | Machine type for the production MIG instances (N2D AMD family recommended for Committed Use Discounts). | <code>string</code> |  | <code>&#34;n2d-standard-8&#34;</code> |
| [prod_mig_zone](variables.tf#L544) | The zone to provision the production MIG in. | <code>string</code> |  | <code>&#34;us-central1-a&#34;</code> |
| [provision_artifact_registry](variables.tf#L136) | Toggle to enable/disable Artifact Registry setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_build](variables.tf#L142) | Toggle to enable/disable Cloud Build setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_function](variables.tf#L148) | Toggle to enable/disable Cloud Function setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_run](variables.tf#L154) | Toggle to enable/disable Cloud Run setup. | <code>bool</code> |  | <code>false</code> |
| [provision_compute_vm](variables.tf#L160) | Toggle to enable/disable standard Compute Engine VM setup. | <code>bool</code> |  | <code>false</code> |
| [provision_iam](variables.tf#L334) | Toggle to enable/disable IAM permissions setup. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_artifact_registry](variables.tf#L340) | Toggle to enable/disable Artifact Registry IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_cloud_build](variables.tf#L346) | Toggle to enable/disable Cloud Build IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_secret_manager](variables.tf#L352) | Toggle to enable/disable Secret Manager IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_sql](variables.tf#L358) | Toggle to enable/disable Cloud SQL IAM client permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_ips](variables.tf#L364) | Toggle to enable/disable static IP setup. | <code>bool</code> |  | <code>false</code> |
| [provision_load_balancer](variables.tf#L370) | Toggle to enable/disable Load Balancer setup. | <code>bool</code> |  | <code>false</code> |
| [provision_prod_mig](variables.tf#L508) | Toggle to enable/disable the Production Managed Instance Group. | <code>bool</code> |  | <code>false</code> |
| [provision_spot_vm](variables.tf#L376) | Toggle to enable/disable Spot VM setup. | <code>bool</code> |  | <code>false</code> |
| [provision_sql](variables.tf#L382) | Toggle to enable/disable Cloud SQL database setup. | <code>bool</code> |  | <code>false</code> |
| [provision_ssl](variables.tf#L388) | Toggle to enable/disable Managed SSL setup. | <code>bool</code> |  | <code>false</code> |
| [provision_test_mig](variables.tf#L514) | Toggle to enable/disable the Testing Managed Instance Group. | <code>bool</code> |  | <code>false</code> |
| [region](variables.tf#L394) | The default GCP region to deploy regional resources. | <code>string</code> |  | <code>&#34;us-central1&#34;</code> |
| [snapshot_schedule_retention_days](variables.tf#L770) | Number of days to retain automated snapshots. | <code>number</code> |  | <code>7</code> |
| [snapshot_schedule_start_time](variables.tf#L764) | The start time (HH:MM) in UTC for the daily snapshot schedule. | <code>string</code> |  | <code>&#34;02:00&#34;</code> |
| [snapshot_schedule_storage_location](variables.tf#L776) | The GCS storage location (region) for snapshot data. | <code>string</code> |  | <code>&#34;us&#34;</code> |
| [spot_machine_type](variables.tf#L400) | The machine type for Spot VM instances. | <code>string</code> |  | <code>&#34;n2-standard-4&#34;</code> |
| [spot_vm_labels](variables.tf#L665) | Labels applied to the spot VM. | <code>map(string)</code> |  | <code>{role = &#34;batch-processor&#34;}</code> |
| [sql_db_version](variables.tf#L406) | The database version for Cloud SQL (e.g. POSTGRES_15). | <code>string</code> |  | <code>&#34;POSTGRES_15&#34;</code> |
| [sql_tier](variables.tf#L412) | The machine tier for the Cloud SQL instance. | <code>string</code> |  | <code>&#34;db-f1-micro&#34;</code> |
| [ssl_cert_name](variables.tf#L418) | The name of the SSL certificate resource. | <code>string</code> |  | <code>&#34;web-ssl-cert&#34;</code> |
| [ssl_map_name](variables.tf#L424) | The name of the Certificate Map. | <code>string</code> |  | <code>&#34;web-ssl-map&#34;</code> |
| [startup_script_packages](variables.tf#L674) | List of APT packages to install on first boot. | <code>list(string)</code> |  | <code>[&#34;curl&#34;, &#34;git&#34;, &#34;nginx&#34;, &#34;python3&#34;, &#34;python3-pip&#34;, &#34;python3-venv&#34;, &#34;pipx&#34;]</code> |
| [state_bucket_name](variables.tf#L456) | The globally unique name of the GCS bucket for remote state storage. | <code>string</code> | ✓ |  |
| [state_bucket_region](variables.tf#L461) | The GCP region/location where the state GCS bucket is located. | <code>string</code> |  | <code>&#34;us-central1&#34;</code> |
| [subnetwork](variables.tf#L430) | The subnetwork to deploy resources into. | <code>string</code> |  | <code>&#34;default&#34;</code> |
| [test_autoscaling_max_replicas](variables.tf#L574) | The maximum number of instances for the testing MIG autoscaler. | <code>number</code> |  | <code>1</code> |
| [test_mig_machine_type](variables.tf#L538) | Machine type for the testing MIG instances (N2D AMD family with SPOT pricing). | <code>string</code> |  | <code>&#34;n2d-standard-8&#34;</code> |
| [test_mig_zone](variables.tf#L550) | The zone to provision the testing MIG in. | <code>string</code> |  | <code>&#34;us-central1-b&#34;</code> |
| [use_default_vpc](variables.tf#L113) | Toggle to use the pre-existing default VPC and default subnet in the project instead of creating a custom VPC. | <code>bool</code> |  | <code>false</code> |
| [use_regional_mig](variables.tf#L526) | Toggle to use regional Managed Instance Groups (and regional autoscalers). | <code>bool</code> |  | <code>false</code> |
| [use_zonal_mig](variables.tf#L520) | Toggle to use zonal Managed Instance Groups (and zonal autoscalers). | <code>bool</code> |  | <code>true</code> |
| [vm_ip_external](variables.tf#L95) | Toggle to assign external (public) IPs to VMs. | <code>bool</code> |  | <code>null</code> |
| [vm_labels](variables.tf#L659) | Labels applied to the standard VM. | <code>map(string)</code> |  | <code>{role = &#34;web-frontend&#34;}</code> |
| [web_ip_name](variables.tf#L436) | The name of the regional external/internal static IP address. | <code>string</code> |  | <code>&#34;web-ip&#34;</code> |

## Outputs

| name | description | sensitive |
|---|---|:---:|
| [artifact_registries](outputs.tf#L17) | The outputs of the provisioned Artifact Registry repositories. |  |
| [cloud_functions](outputs.tf#L23) | The outputs of the provisioned Cloud Functions (v2). |  |
| [cloud_run_services](outputs.tf#L29) | The outputs of the provisioned Cloud Run services (v2). |  |
| [compute_vms](outputs.tf#L35) | The outputs of standard Compute VMs. |  |
| [ips](outputs.tf#L41) | The outputs of the provisioned IP addresses. |  |
| [load_balancers](outputs.tf#L47) | The outputs of the provisioned Load Balancers. |  |
| [prod_mig](outputs.tf#L79) | The outputs and details of the production zonal Managed Instance Group. |  |
| [prod_region_mig](outputs.tf#L84) | The outputs and details of the production regional Managed Instance Group. |  |
| [project_id](outputs.tf#L53) | The GCP Project ID where resources were provisioned. |  |
| [deploy_ssh_public_key](outputs.tf#L59) | Public key for the CI/CD deploy SSH key pair. Add to project metadata for Cloud Build IAP SSH access. |  |
| [deploy_ssh_private_key](outputs.tf#L59) | Private key for the CI/CD deploy SSH key pair. Stored in Secret Manager automatically. | ✓ |
| [spot_vms](outputs.tf#L59) | The outputs of provisioned Spot VMs. |  |
| [sql_instances](outputs.tf#L65) | The outputs of the provisioned Cloud SQL database instances. | ✓ |
| [ssl_certificates](outputs.tf#L73) | The outputs of the SSL Certificates Manager configuration. |  |
| [test_mig](outputs.tf#L89) | The outputs and details of the testing zonal Managed Instance Group. |  |
| [test_region_mig](outputs.tf#L94) | The outputs and details of the testing regional Managed Instance Group. |  |
<!-- END TFDOC -->
