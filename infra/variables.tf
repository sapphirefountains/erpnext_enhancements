/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

variable "deployment_mode" {
  description = "Controls which resources are managed by this state: 'shared' (project, VPC, NAT, IAM, CB connection, IPs, SSL), 'test' (spot VM, test LB/triggers), or 'prod' (standard VM, prod LB, SSL, prod triggers)."
  type        = string
  default     = "shared"
}

variable "api_url" {
  description = "The API URL used by the frontend service container."
  type        = string
  default     = "https://api.example.com"
}

variable "billing_account_id" {
  description = "The billing account ID to associate with the created project."
  type        = string
  default     = null
}

variable "cloud_build_connection" {
  description = "The name of the Cloud Build connection."
  type        = string
  default     = "github-pipeline-connection"
}

variable "cloud_build_github_token" {
  description = "The GitHub Personal Access Token (PAT) for Cloud Build connection."
  type        = string
  default     = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
}

variable "cloud_build_installation_id" {
  description = "The GitHub App installation ID on the repo."
  type        = number
  default     = 12345678
}

variable "cloud_build_repo_uri" {
  description = "The remote URI of the repository for Cloud Build connection."
  type        = string
  default     = "https://github.com/example-org/example-repo.git"
}

variable "cloud_function_bucket" {
  description = "Bucket name where Cloud Function source archives are uploaded."
  type        = string
  default     = "demo-function-deploy-bucket"
}

variable "cloud_run_image" {
  description = "The container image to deploy to Cloud Run."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "compute_machine_type" {
  description = "The machine type for standard Compute Engine VM instances."
  type        = string
  default     = "e2-medium"
}

variable "create_project" {
  description = "Whether to create a new project or reuse an existing one."
  type        = bool
  default     = true
}

variable "domain_name" {
  description = "The domain name for the managed SSL certificate."
  type        = string
  default     = "app.example.com"
}

variable "glb_ip_name" {
  description = "The name of the global external IP address for the load balancer."
  type        = string
  default     = "glb-ip"
}

variable "ip_external" {
  description = "Toggle static IPs, Cloud SQL, VMs, and Cloud Run to be external (true) or internal (false)."
  type        = bool
  default     = false
}

variable "vm_ip_external" {
  description = "Toggle to assign external (public) IPs to VMs (Compute Engine instances, Spot VMs, and MIG templates). If null, defaults to the global ip_external setting."
  type        = bool
  default     = false
}

variable "enable_standard_public_ip" {
  type        = bool
  description = "If true, assigns a public external IP to the standard VM"
  default     = false
}

variable "enable_spot_public_ip" {
  type        = bool
  description = "If true, assigns a public external IP to the spot VM"
  default     = false
}

variable "use_default_vpc" {
  description = "Toggle to use the pre-existing default VPC and default subnet in the project instead of creating a custom VPC."
  type        = bool
  default     = false
}

variable "network" {
  description = "The VPC network to deploy resources into."
  type        = string
  default     = "default"
}

variable "prefix" {
  description = "An optional prefix applied to created resources."
  type        = string
  default     = null
}

variable "project_id" {
  description = "The ID of the project to create or reuse."
  type        = string
}

variable "provision_artifact_registry" {
  description = "Toggle to enable/disable Artifact Registry setup."
  type        = bool
  default     = false
}

variable "provision_cloud_build" {
  description = "Toggle to enable/disable Cloud Build setup."
  type        = bool
  default     = false
}

variable "provision_cloud_function" {
  description = "Toggle to enable/disable Cloud Function setup."
  type        = bool
  default     = false
}

variable "provision_cloud_run" {
  description = "Toggle to enable/disable Cloud Run setup."
  type        = bool
  default     = false
}

variable "provision_compute_vm" {
  description = "Toggle to enable/disable standard Compute Engine VM setup."
  type        = bool
  default     = false
}

variable "enable_vm_persistence" {
  type        = bool
  description = "If true, attaches a secondary data disk and local SSD scratch disk to the VMs"
  default     = false
}

variable "vm_data_disk_size" {
  type        = number
  description = "The capacity in GB for the persistent storage block"
  default     = 200
}

variable "vm_boot_disk_size" {
  type        = number
  description = "The capacity in GB for the VM boot disk"
  default     = 50
}

variable "reuse_existing_disks" {
  type        = bool
  description = "When true, boot disk uses an independent resource that persists across VM recreation and gets reattached. When false, a new boot disk is created each time the VM is replaced."
  default     = false
}

variable "boot_disk_auto_delete" {
  type        = bool
  description = "Whether to automatically delete the boot disk when the instance is deleted."
  default     = false
}

variable "vm_local_ssd_count" {
  type        = number
  description = "The number of local SSD scratch arrays to provision"
  default     = 1
}

variable "enable_standalone_health_check" {
  type        = bool
  description = "Toggles the creation of the global HTTP health check for backend load balancing"
  default     = true
}

variable "vm_network_tags" {
  type        = list(string)
  description = "Network tags applied to standard VMs for firewall rule targeting."
  default     = ["web-frontend"]
}

variable "spot_vm_network_tags" {
  type        = list(string)
  description = "Network tags applied to Spot VMs for firewall rule targeting."
  default     = ["web-frontend"]
}

variable "vm_region" {
  type        = string
  description = "Region for standard VMs. If null, defaults to var.region."
  default     = null
}

variable "vm_zone" {
  type        = string
  description = "Explicit zone for the standard VM. Overrides auto-selection from vm_region."
  default     = null
}

variable "spot_vm_region" {
  type        = string
  description = "Region for Spot VMs. If null, defaults to var.region."
  default     = null
}

variable "spot_vm_zone" {
  type        = string
  description = "Explicit zone for the Spot VM. Overrides auto-selection from spot_vm_region."
  default     = null
}

variable "boot_disk_source_attach" {
  type        = string
  description = "Self-link of an existing disk to attach as the boot disk. When set, overrides boot disk creation and initialize_params are ignored."
  default     = null
}

variable "data_disk_source_attach" {
  type        = string
  description = "Self-link of an existing disk to attach as the persistent data disk. When set, overrides data disk creation."
  default     = null
}

variable "vm_custom_image" {
  type        = string
  description = "Custom image self-link or family for the boot disk (e.g. projects/my-project/global/images/family/my-image). If null, defaults to debian-12."
  default     = null
}

variable "health_check_port" {
  type        = number
  description = "The target port the load balancer will query (80 for production NGINX)"
  default     = 80
}

variable "enable_lb_firewall" {
  type        = bool
  description = "If true, provisions the firewall rule allowing external HTTP/HTTPS load balancer traffic and health checks"
  default     = true
}

variable "enable_iap_ssh_firewall" {
  type        = bool
  description = "If true, provisions the firewall rule allowing secure SSH tunneling via Google Identity-Aware Proxy (IAP)"
  default     = true
}

# variable "provision_cloud_run" {
#   type        = bool
#   description = "If true, configures the load balancer to route traffic to Cloud Run serverless backends"
#   default     = false
# }

variable "cloud_run_service_name" {
  type        = string
  description = "The target deployment name of the Cloud Run service container microapp"
  default     = "sapphire-microservice"
}

variable "spot_lb_name" {
  description = "The name of the spot VM load balancer."
  type        = string
  default     = "spot-glb"
}

variable "production_lb_name" {
  description = "The name of the production VM load balancer."
  type        = string
  default     = "production-glb"
}

variable "spot_glb_ip_name" {
  description = "The name of the global external IP address for the spot VM load balancer."
  type        = string
  default     = "spot-glb-ip"
}

variable "standalone_vm_neg_name" {
  type        = string
  description = "The identifier name for the unmanaged/zonal network endpoint group wrapping the production VM"
  default     = "production-vm-neg"
}
variable "spot_vm_neg_name" {
  type        = string
  description = "The network endpoint group identifier name for the staging spot VM routing block"
  default     = "test-erpnext-spot-vm-neg"
}


variable "standard_vm_name" {
  type        = string
  description = "The deployment name for the standard compute VM instance"
  default     = "standard-vm"
}

variable "spot_vm_name" {
  type        = string
  description = "The deployment name for the ephemeral spot VM instance"
  default     = "spot-vm"
}

variable "provision_iam" {
  description = "Toggle to enable/disable IAM permissions setup."
  type        = bool
  default     = true
}

variable "provision_iam_artifact_registry" {
  description = "Toggle to enable/disable Artifact Registry IAM permissions."
  type        = bool
  default     = true
}

variable "provision_iam_cloud_build" {
  description = "Toggle to enable/disable Cloud Build IAM permissions."
  type        = bool
  default     = true
}

variable "provision_iam_secret_manager" {
  description = "Toggle to enable/disable Secret Manager IAM permissions."
  type        = bool
  default     = true
}

variable "provision_iam_sql" {
  description = "Toggle to enable/disable Cloud SQL IAM client permissions."
  type        = bool
  default     = true
}

variable "provision_ips" {
  description = "Toggle to enable/disable static IP setup."
  type        = bool
  default     = false
}

variable "provision_load_balancer" {
  description = "Toggle to enable/disable Load Balancer setup."
  type        = bool
  default     = false
}

variable "provision_spot_vm" {
  description = "Toggle to enable/disable Spot VM setup."
  type        = bool
  default     = false
}

variable "provision_sql" {
  description = "Toggle to enable/disable Cloud SQL database setup."
  type        = bool
  default     = false
}

variable "provision_ssl" {
  description = "Toggle to enable/disable Managed SSL setup."
  type        = bool
  default     = false
}

variable "region" {
  description = "The default GCP region to deploy regional resources."
  type        = string
  default     = "us-central1"
}

variable "spot_machine_type" {
  description = "The machine type for Spot VM instances."
  type        = string
  default     = "n2-standard-4"
}

variable "sql_db_version" {
  description = "The database version for Cloud SQL (e.g. POSTGRES_15)."
  type        = string
  default     = "POSTGRES_15"
}

variable "sql_tier" {
  description = "The machine tier for the Cloud SQL instance."
  type        = string
  default     = "db-f1-micro"
}

variable "ssl_cert_name" {
  description = "The name of the SSL certificate resource."
  type        = string
  default     = "web-ssl-cert"
}

variable "ssl_map_name" {
  description = "The name of the Certificate Map."
  type        = string
  default     = "web-ssl-map"
}

variable "subnetwork" {
  description = "The subnetwork to deploy resources into."
  type        = string
  default     = "default"
}

variable "web_ip_name" {
  description = "The name of the regional external/internal static IP address."
  type        = string
  default     = "web-ip"
}


variable "secret_manager_secret_id" {
  description = "The short ID string of the Secret Manager secret container."
  type        = string
  default     = "github-token"
}

variable "certificate_map_id" {
  description = "The fully qualified resource URI for the Certificate Manager map."
  type        = string
  default     = ""
}

#--- Pipeline Automation Variables ---
variable "state_bucket_name" {
  description = "The globally unique name of the GCS bucket for remote state storage."
  type        = string
}

variable "state_bucket_region" {
  description = "The GCP region/location where the state GCS bucket is located."
  type        = string
  default     = "us-central1"
}

variable "github_repo_url" {
  description = "The target GitHub repository remote URL link."
  type        = string
}

variable "github_token_secret" {
  description = "The payload value of the GitHub PAT to store securely in Secret Manager."
  type        = string
  sensitive   = true
}

variable "github_app_installation_id" {
  description = "The unique numerical identifier of the GitHub App on your repo."
  type        = number
}

variable "cloudbuild_yaml_path" {
  description = "The file path inside the repo pointing to the infra cloudbuild.yaml."
  type        = string
  default     = "infra/cloudbuild.yaml"
}

variable "cloudbuild_deploy_yaml_path" {
  description = "The file path inside the repo pointing to cloudbuild-deploy.yaml."
  type        = string
  default     = "infra/cloudbuild-deploy.yaml"
}

variable "cloudbuild_upstream_yaml_path" {
  description = "The file path inside the repo pointing to cloudbuild-upstream.yaml."
  type        = string
  default     = "infra/cloudbuild-upstream.yaml"
}

variable "cloudbuild_service_account" {
  description = "Custom service account string to run the build pipelines. If null, uses compute engine agent default."
  type        = string
  default     = null
}

variable "deploy_branch_regex" {
  description = "The regex pattern matching the branch used for infrastructure creation."
  type        = string
  default     = "^main$"
}

variable "cloud_build_deploy_branch" {
  description = "Branch name that triggers CI/CD app deployment (deploy-test, deploy-prod)."
  type        = string
  default     = "main"
}

variable "destroy_branch_regex" {
  description = "The regex pattern matching the branch used for infrastructure destruction."
  type        = string
  default     = "^destroy-env$"
}

# --- Managed Instance Group (MIG) & Autoscaling Variables ---
variable "provision_prod_mig" {
  description = "Toggle to enable/disable the Production Managed Instance Group."
  type        = bool
  default     = false
}

variable "provision_test_mig" {
  description = "Toggle to enable/disable the Testing Managed Instance Group."
  type        = bool
  default     = false
}

variable "use_zonal_mig" {
  description = "Toggle to use zonal Managed Instance Groups (and zonal autoscalers)."
  type        = bool
  default     = true
}

variable "use_regional_mig" {
  description = "Toggle to use regional Managed Instance Groups (and regional autoscalers)."
  type        = bool
  default     = false
}

variable "prod_mig_machine_type" {
  description = "Machine type for the production MIG instances (N2D AMD family recommended for Committed Use Discounts)."
  type        = string
  default     = "n2d-standard-8"
}

variable "test_mig_machine_type" {
  description = "Machine type for the testing MIG instances (N2D AMD family with SPOT pricing)."
  type        = string
  default     = "n2d-standard-8"
}

variable "prod_mig_zone" {
  description = "The zone to provision the production MIG in."
  type        = string
  default     = "us-central1-a"
}

variable "test_mig_zone" {
  description = "The zone to provision the testing MIG in."
  type        = string
  default     = "us-central1-b"
}

variable "enable_prod_autoscaling" {
  description = "Toggle to enable/disable autoscaling for the production MIG."
  type        = bool
  default     = false
}

variable "enable_test_autoscaling" {
  description = "Toggle to enable/disable autoscaling for the testing MIG."
  type        = bool
  default     = false
}

variable "prod_autoscaling_max_replicas" {
  description = "The maximum number of instances for the production MIG autoscaler (defaulted to 1 to cap costs)."
  type        = number
  default     = 1
}

variable "test_autoscaling_max_replicas" {
  description = "The maximum number of instances for the testing MIG autoscaler (defaulted to 1 to cap costs)."
  type        = number
  default     = 1
}

variable "mig_data_disk_size" {
  description = "The storage capacity in GB for the stateful Balanced Persistent Disk attached to MIG instances."
  type        = number
  default     = 200
}

variable "mig_local_ssd_count" {
  description = "Number of high-performance Local SSDs to attach to each MIG instance (each is 375 GB)."
  type        = number
  default     = 1
}

variable "mig_health_check_port" {
  description = "The application/health-check port for the ERPNext instances."
  type        = number
  default     = 8000
}

variable "provision_spot_vm_lb_backend" {
  type        = bool
  description = "Toggle to include the Spot VM as a backend in the load balancer. Set false for a standalone spot VM without LB routing."
  default     = false
}

variable "provision_standard_vm_lb_backend" {
  type        = bool
  description = "Toggle to include the standard VM as a backend in the load balancer. Requires a NEG or instance group for the VM."
  default     = false
}

variable "provision_cloud_nat" {
  type        = bool
  description = "Toggle to active or completely tear down the Cloud NAT egress network gateways"
  default     = true # Keeps it enabled by default for active VM work
}

variable "nat_regions" {
  type        = list(string)
  description = "List of regions to provision Cloud NAT gateways. Each region gets its own router and NAT."
  default     = ["us-central1", "us-east1"]
}

variable "nat_name_prefix" {
  type        = string
  description = "Prefix for NAT router and gateway names. Resources will be named {prefix}-nat-router-{region} and {prefix}-cloud-nat-{region}."
  default     = "erpnext"
}

variable "enable_startup_script" {
  type        = bool
  description = "If true, attaches the startup script to both VMs (standard and spot) to auto-configure bench and nginx."
  default     = false
}

variable "restore_spot_vm_from_snapshot" {
  type        = bool
  description = "If true, finds the latest snapshot matching the spot VM disk names, creates disks from them in the target zone, and attaches them to the new VM. Useful when recreating the VM in a different zone/region."
  default     = false
}

variable "iap_tunnel_members" {
  type        = list(string)
  description = "List of members (users/groups/SAs) to grant IAP tunnel access for SSH. Each entry should be in the format 'user:email@example.com' or 'group:group@example.com' or 'serviceAccount:sa@project.iam.gserviceaccount.com'"
  default     = []
}

# ============================================================================
# Health Check Configuration
# ============================================================================
variable "health_check_interval_sec" {
  type        = number
  description = "The interval in seconds between health check probes."
  default     = 10
}

variable "health_check_timeout_sec" {
  type        = number
  description = "The timeout in seconds for each health check probe."
  default     = 5
}

variable "health_check_healthy_threshold" {
  type        = number
  description = "The number of consecutive successes to mark a VM as healthy."
  default     = 2
}

variable "health_check_unhealthy_threshold" {
  type        = number
  description = "The number of consecutive failures to mark a VM as unhealthy."
  default     = 3
}

variable "health_check_request_path" {
  type        = string
  description = "The URL path used by health checks to probe VM readiness."
  default     = "/"
}

# ============================================================================
# Firewall & Network Security Configuration
# ============================================================================
variable "lb_firewall_ports" {
  type        = list(string)
  description = "List of TCP ports opened to the load balancer probe source ranges."
  default     = ["80", "443", "8000"]
}

variable "lb_source_ranges" {
  type        = list(string)
  description = "Source IP ranges for Google Cloud Load Balancer health probes and traffic."
  default     = ["130.211.0.0/22", "35.191.0.0/16"]
}

variable "iap_source_range" {
  type        = string
  description = "Source IP range for IAP SSH tunneling."
  default     = "35.235.240.0/20"
}

# ============================================================================
# Disk Configuration
# ============================================================================
variable "disk_type" {
  type        = string
  description = "Default disk type for VM boot and data disks (e.g. pd-balanced, pd-ssd, pd-standard)."
  default     = "pd-balanced"
}

variable "local_ssd_size" {
  type        = number
  description = "Size in GB for each local SSD scratch disk."
  default     = 375
}

# ============================================================================
# VM Labels
# ============================================================================
variable "vm_labels" {
  type        = map(string)
  description = "Labels applied to the standard VM."
  default = {
    role = "web-frontend"
  }
}

variable "spot_vm_labels" {
  type        = map(string)
  description = "Labels applied to the spot VM."
  default = {
    role = "batch-processor"
  }
}

# ============================================================================
# Startup Script Configuration
# ============================================================================
variable "startup_script_packages" {
  type        = list(string)
  description = "List of APT packages to install on first boot."
  default     = ["curl", "git", "nginx", "python3", "python3-pip", "python3-venv", "pipx"]
}

variable "deploy_user" {
  type        = string
  description = "Username for the CI/CD deploy user created on VMs."
  default     = "deploy"
}

variable "deploy_user_sudo_command" {
  type        = string
  description = "The specific sudo command allowed for the deploy user without a password."
  default     = "/usr/bin/systemctl restart frappe-bench"
}

# ============================================================================
# Snapshot Schedule Configuration
# ============================================================================
variable "enable_spot_vm_snapshot_schedule" {
  type        = bool
  description = "If true, attaches a daily snapshot schedule to the spot VM's boot and data disks. Snapshots are created in the same region as the disk."
  default     = null
}

variable "snapshot_schedule_start_time" {
  type        = string
  description = "The start time (HH:MM) in UTC for the daily snapshot schedule."
  default     = "02:00"
}

variable "snapshot_schedule_retention_days" {
  type        = number
  description = "Number of days to retain automated snapshots."
  default     = 7
}

variable "snapshot_schedule_storage_location" {
  type        = string
  description = "The GCS storage location (region) for snapshot data, e.g. 'us', 'us-east1', 'us-central1'."
  default     = "us"
}