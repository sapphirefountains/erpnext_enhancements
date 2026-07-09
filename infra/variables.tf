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

variable "provision_cloud_run" {
  type        = bool
  description = "If true, configures the load balancer to route traffic to Cloud Run serverless backends"
  default     = false
}

variable "cloud_run_service_name" {
  type        = string
  description = "The target deployment name of the Cloud Run service container microapp"
  default     = "sapphire-microservice"
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
  description = "The contextual file directory path inside the repo pointing to cloudbuild.yaml."
  type        = string
  default     = "cloudbuild.yaml"
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

variable "provision_cloud_nat" {
  type        = bool
  description = "Toggle to active or completely tear down the Cloud NAT egress network gateways"
  default     = true # Keeps it enabled by default for active VM work
}