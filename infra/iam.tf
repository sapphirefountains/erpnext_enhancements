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

# Transform iteration maps in locals to keep resource blocks clean
locals {
  _artifact_registry_iam = (
    (
      var.provision_iam &&
      var.provision_iam_artifact_registry &&
      var.provision_artifact_registry
    )
    ? flatten([
      for repo_key, repo_val in local.artifact_registry_config : concat(
        # Standard Reader role for serverless robot
        [
          {
            repo   = repo_key
            role   = "roles/artifactregistry.reader"
            member = "serviceAccount:service-${module.project.number}@serverless-robot-prod.iam.gserviceaccount.com"
          }
        ],
        # Standard Writer roles for Cloud Build and Compute VM service accounts
        (
          (var.provision_cloud_build || var.provision_cloud_function)
          ? [
            {
              repo   = repo_key
              role   = "roles/artifactregistry.writer"
              member = "serviceAccount:${module.project.number}@cloudbuild.gserviceaccount.com"
            },
            {
              repo   = repo_key
              role   = "roles/artifactregistry.writer"
              member = "serviceAccount:${module.project.number}-compute@developer.gserviceaccount.com"
            }
          ]
          : []
        ),
        # Custom IAM configured in the YAML configs
        flatten([
          for role, members in try(repo_val.iam, {}) : [
            for member in members : {
              repo   = repo_key
              role   = role
              member = member
            }
          ]
        ])
      )
    ])
    : []
  )
  _artifact_registry_iam_map = {
    for x in distinct(local._artifact_registry_iam) :
    "${x.repo}-${x.role}-${x.member}" => x
  }

  _sql_client_cloudfunctions = (
    (var.provision_iam && var.provision_iam_sql)
    ? {
      for k, v in module.cloud_function : k => v
      if(
        var.provision_sql &&
        var.provision_cloud_function &&
        v.service_account_iam_email != "serviceAccount:"
      )
    }
    : {}
  )
  _sql_client_cloudrun = (
    (var.provision_iam && var.provision_iam_sql)
    ? {
      for k, v in module.cloud_run : k => v
      if(
        var.provision_sql &&
        var.provision_cloud_run &&
        v.service_account_iam_email != "serviceAccount:"
      )
    }
    : {}
  )
  _sql_client_compute_vm = (
    (var.provision_iam && var.provision_iam_sql)
    ? {
      for k, v in module.compute_vm : k => v
      if(
        var.provision_sql &&
        var.provision_compute_vm &&
        v.service_account_iam_email != null
      )
    }
    : {}
  )
  _sql_client_spot_vm = (
    (var.provision_iam && var.provision_iam_sql)
    ? {
      for k, v in module.spot_vm : k => v
      if(
        var.provision_sql &&
        var.provision_spot_vm &&
        v.service_account_iam_email != null
      )
    }
    : {}
  )
}

# Create dedicated Custom Service Account for Terraform Provisioner (CI/CD runner)
resource "google_service_account" "terraform_provisioner" {
  count        = var.provision_iam && var.provision_cloud_build ? 1 : 0
  account_id   = "sa-terraform-provisioner"
  display_name = "Terraform Provisioner Service Account"
  project      = module.project.project_id
}

# Grant granular roles to the Custom Terraform Provisioner Service Account
resource "google_project_iam_member" "terraform_provisioner_roles" {
  for_each = (var.provision_iam && var.provision_cloud_build) ? toset([
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageAdmin",
    "roles/compute.networkAdmin",
    "roles/compute.loadBalancerAdmin",
    "roles/compute.instanceAdmin.v1",
    "roles/run.admin",
    "roles/cloudfunctions.admin",
    "roles/cloudsql.admin",
    "roles/artifactregistry.admin",
    "roles/secretmanager.admin",
    "roles/certificatemanager.owner",
    "roles/resourcemanager.projectIamAdmin",
    "roles/iam.serviceAccountUser"
  ]) : []
  project  = module.project.project_id
  role     = each.value
  member   = "serviceAccount:sa-terraform-provisioner@${module.project.project_id}.iam.gserviceaccount.com"

  depends_on = [
    google_service_account.terraform_provisioner
  ]
}

# Grant the Cloud Build service agent permission to act as the Terraform provisioner service account
resource "google_service_account_iam_member" "cloudbuild_service_agent_user" {
  count              = var.provision_iam && var.provision_cloud_build ? 1 : 0
  service_account_id = "projects/${module.project.project_id}/serviceAccounts/sa-terraform-provisioner@${module.project.project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:service-${module.project.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"

  depends_on = [
    google_service_account.terraform_provisioner
  ]
}

# # Grant Secret Manager secretAccessor role for github token
# resource "google_secret_manager_secret_iam_member" "cloudbuild_secret_accessor" {
#   count     = var.provision_iam && var.provision_iam_secret_manager && var.provision_cloud_build ? 1 : 0
#   project   = module.project.project_id
#   secret_id = var.secret_manager_secret_id
#   role      = "roles/secretmanager.secretAccessor"
#   member    = "serviceAccount:service-${module.project.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"
# }


# Grant Secret Manager secretAccessor role at the project level supporting all pipeline secrets
resource "google_project_iam_member" "cloudbuild_secret_accessor_project_level" {
  count   = var.provision_iam && var.provision_iam_secret_manager && var.provision_cloud_build ? 1 : 0
  project = module.project.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:service-${module.project.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com"

  # 🎯 THE ULTIMATE FIX: Dynamically grants access to BOTH secret paths across both project identifier formats
  condition {
    title       = "restrict_to_pipeline_secrets"
    description = "Allow access to github-token and github-authorizer-credential variants across ID patterns"
    expression  = "(resource.name.startsWith('projects/${module.project.project_id}/secrets/github-token') || resource.name.startsWith('projects/${module.project.number}/secrets/github-token')) || (resource.name.startsWith('projects/${module.project.project_id}/secrets/github-authorizer-credential') || resource.name.startsWith('projects/${module.project.number}/secrets/github-authorizer-credential'))"
  }

  depends_on = [
    module.project,
    module.cloud_build_secret
  ]
}


# Grant Artifact Registry reader/writer roles for robot, compute, and build service accounts safely
resource "google_artifact_registry_repository_iam_member" "artifact_registry" {
  # 🎯 THE FIX: Completely skips processing if artifact registry provisioning is disabled
  for_each   = var.provision_artifact_registry ? local._artifact_registry_iam_map : {}
  project    = module.project.project_id
  location   = var.region
  repository = module.artifact_registry[each.value.repo].name
  role       = each.value.role
  member     = each.value.member
}

# Automate Cloud SQL Client IAM permissions for compute and serverless services

# Grant Cloud SQL Client permission to Cloud Run service accounts
resource "google_project_iam_member" "cloudsql_client_cloudrun" {
  for_each = local._sql_client_cloudrun
  project  = module.project.project_id
  role     = "roles/cloudsql.client"
  member   = each.value.service_account_iam_email
}

# Grant Cloud SQL Client permission to Cloud Functions service accounts
resource "google_project_iam_member" "cloudsql_client_cloudfunctions" {
  for_each = local._sql_client_cloudfunctions
  project  = module.project.project_id
  role     = "roles/cloudsql.client"
  member   = each.value.service_account_iam_email
}

# Grant Cloud SQL Client permission to standard Compute VMs
resource "google_project_iam_member" "cloudsql_client_compute_vm" {
  for_each = local._sql_client_compute_vm
  project  = module.project.project_id
  role     = "roles/cloudsql.client"
  member   = each.value.service_account_iam_email
}

# Grant Cloud SQL Client permission to Spot Compute VMs
resource "google_project_iam_member" "cloudsql_client_spot_vm" {
  for_each = local._sql_client_spot_vm
  project  = module.project.project_id
  role     = "roles/cloudsql.client"
  member   = each.value.service_account_iam_email
}
