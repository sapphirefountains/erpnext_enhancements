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

output "artifact_registries" {
  description = "The outputs of the provisioned Artifact Registry repositories."
  value       = module.artifact_registry
  depends_on  = [module.artifact_registry]
}

output "cloud_functions" {
  description = "The outputs of the provisioned Cloud Functions (v2)."
  value       = module.cloud_function
  depends_on  = [module.cloud_function]
}

output "cloud_run_services" {
  description = "The outputs of the provisioned Cloud Run services (v2)."
  value       = module.cloud_run
  depends_on  = [module.cloud_run]
}

output "compute_vms" {
  description = "The outputs of standard Compute VMs."
  value       = module.compute_vm
  depends_on  = [module.compute_vm]
  sensitive   = true
}

output "ips" {
  description = "The outputs of the provisioned IP addresses."
  value       = try(module.ips[0], null)
  depends_on  = [module.ips]
}

output "load_balancers" {
  description = "The outputs of the provisioned Load Balancers."
  value       = module.load_balancer
  depends_on  = [module.load_balancer]
}

output "project_id" {
  description = "The GCP Project ID where resources were provisioned."
  value       = module.project.project_id
  depends_on  = [module.project]
}

output "spot_vms" {
  description = "The outputs of provisioned Spot VMs."
  value       = module.spot_vm
  depends_on  = [module.spot_vm]
  sensitive   = true
}

output "sql_instances" {
  description = "The outputs of the provisioned Cloud SQL database instances."
  value       = module.sql
  sensitive   = true
  depends_on  = [module.sql]
}

output "ssl_certificates" {
  description = "The outputs of the SSL Certificates Manager configuration."
  value       = try(module.ssl_certificates[0], null)
  depends_on  = [module.ssl_certificates]
}

output "prod_mig" {
  description = "The outputs and details of the production zonal Managed Instance Group."
  value       = var.provision_prod_mig && length(google_compute_instance_group_manager.prod_mig) > 0 ? google_compute_instance_group_manager.prod_mig[0] : null
}

output "prod_region_mig" {
  description = "The outputs and details of the production regional Managed Instance Group."
  value       = var.provision_prod_mig && length(google_compute_region_instance_group_manager.prod_mig) > 0 ? google_compute_region_instance_group_manager.prod_mig[0] : null
}

output "test_mig" {
  description = "The outputs and details of the testing zonal Managed Instance Group."
  value       = var.provision_test_mig && length(google_compute_instance_group_manager.test_mig) > 0 ? google_compute_instance_group_manager.test_mig[0] : null
}

output "test_region_mig" {
  description = "The outputs and details of the testing regional Managed Instance Group."
  value       = var.provision_test_mig && length(google_compute_region_instance_group_manager.test_mig) > 0 ? google_compute_region_instance_group_manager.test_mig[0] : null
}

output "deploy_ssh_public_key" {
  description = "The public key for the deploy user used by Cloud Build CI/CD. Add this to project SSH metadata."
  value       = var.provision_cloud_build ? try(tls_private_key.deploy_ssh_key[0].public_key_openssh, "") : ""
  sensitive   = false
}

output "deploy_ssh_private_key" {
  description = "The private key for the deploy user. Also stored in Secret Manager as DEPLOY_SSH_KEY."
  value       = var.provision_cloud_build ? try(tls_private_key.deploy_ssh_key[0].private_key_openssh, "") : ""
  sensitive   = true
}

