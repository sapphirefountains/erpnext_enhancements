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

# # Secret Manager Module: Store the Github PAT safely
# locals {
#   cloud_build_config = yamldecode(templatefile("${path.module}/configs/cloud_build.yaml", {
#     connection_name            = var.cloud_build_connection
#     github_app_installation_id = var.cloud_build_installation_id
#     github_token_secret        = var.cloud_build_github_token
#     region                     = var.region
#     repo_remote_uri            = var.cloud_build_repo_uri
#   }))
# }

# module "cloud_build_secret" {
#   count      = var.provision_cloud_build ? 1 : 0
#   source     = "../modules/secret-manager"
#   project_id = module.project.project_id

#   secrets = {
#     github-token = {
#       versions = {
#         v1 = {
#           data = local.cloud_build_config.github_token_secret
#           data_config = {
#             write_only_version = 1
#           }
#         }
#       }
#     }
#   }
# }

# # Cloud Build Connection Module: Create connection and repos/triggers
# module "cloud_build_connection" {
#   count      = var.provision_cloud_build ? 1 : 0
#   source     = "../modules/cloud-build-v2-connection"
#   project_id = module.project.project_id
#   name       = local.cloud_build_config.connection_name
#   location   = local.cloud_build_config.location

#   connection_config = {
#     github = {
#       authorizer_credential_secret_version = module.cloud_build_secret[0].version_ids["github-token/v1"]
#       app_installation_id                  = local.cloud_build_config.github_app_installation_id
#     }
#   }

#   # 🎯 THE FIX: Forces the connection resource to wait for the secret mapping metadata to settle
#   depends_on = [
#     module.project,
#     module.cloud_build_secret
#   ]

#   repositories = local.cloud_build_config.repositories
# }


/**
 * Copyright 2026 Google LLC
 */

# Context variables to cleanly map your execution roles
locals {
  # Dynamically defaults to the custom Terraform provisioner service account if IAM and Cloud Build are enabled,
  # otherwise falls back to the standard Compute Engine default service account.
  cb_service_account = var.cloudbuild_service_account != null ? var.cloudbuild_service_account : (
    (var.provision_iam && var.provision_cloud_build)
    ? "projects/${module.project.project_id}/serviceAccounts/sa-terraform-provisioner@${module.project.project_id}.iam.gserviceaccount.com"
    : "projects/${var.project_id}/serviceAccounts/${module.project.number}-compute@developer.gserviceaccount.com"
  )

  cloud_build_config = yamldecode(templatefile("${path.module}/configs/cloud_build.yaml", {
    connection_name            = var.cloud_build_connection
    github_app_installation_id = var.cloud_build_installation_id
    github_token_secret        = var.cloud_build_github_token
    region                     = var.region
    repo_remote_uri            = var.cloud_build_repo_uri
  }))
}

# ============================================================================
# 1. THE REMOTE STATE STORAGE BUCKET
# ============================================================================
resource "google_storage_bucket" "tf_state" {
  count                       = var.deployment_mode == "shared" ? 1 : 0
  name                        = var.state_bucket_name
  project                     = module.project.project_id
  location                    = var.state_bucket_region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  force_destroy               = true

  versioning {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

# ============================================================================
# 2. EXPLICIT COOLDOWN (PREVENTS RE-CREATION API RACE CONDITIONS)
# ============================================================================
# Pause execution to give GCP control plane backend systems time to spin up identities completely
resource "time_sleep" "wait_for_api_provisioning" {
  count           = var.deployment_mode == "shared" ? 1 : 0
  create_duration = "45s"

  depends_on = [
    module.project
  ]
}

# ============================================================================
# 3. SECRET MANAGER CONFIGURATION
# ============================================================================
module "cloud_build_secret" {
  count      = var.deployment_mode == "shared" && var.provision_cloud_build ? 1 : 0
  source     = "../modules/secret-manager"
  project_id = module.project.project_id

  secrets = {
    github-token = {
      versions = {
        v1 = {
          data = local.cloud_build_config.github_token_secret
          data_config = {
            write_only_version = 1
          }
        }
      }
    }
    # Add your dynamic authorizer secret token container block here
    github-authorizer-credential = {
      versions = {
        v1 = {
          data = var.github_token_secret
          data_config = {
            write_only_version = 1
          }
        }
      }
    }
  }

  depends_on = [
    time_sleep.wait_for_api_provisioning[0]
  ]
}

# ============================================================================
# 4. NATIVE CLOUD BUILD V2 CONNECTION & TRIGGERS
# ============================================================================
module "cloud_build_connection" {
  count      = var.deployment_mode == "shared" && var.provision_cloud_build ? 1 : 0
  source     = "../modules/cloud-build-v2-connection"
  project_id = module.project.project_id
  name       = local.cloud_build_config.connection_name
  location   = var.region

  connection_config = {
    github = {
      authorizer_credential_secret_version = module.cloud_build_secret[0].version_ids["github-authorizer-credential/v1"]
      app_installation_id                  = var.github_app_installation_id
    }
  }

  depends_on = [
    module.project,
    module.cloud_build_secret
  ]

  # Merges your existing automated trigger paths with your standard manual pipelines
  repositories = {
    "infra-repo" = {
      remote_uri = var.github_repo_url
      triggers = {
        # --- Manual Infra Triggers (3 environments × apply/destroy/refresh) ---
        "infra-shared-apply" = {
          description     = "Apply shared infrastructure state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "shared"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "apply"
          }
          tags = ["infra", "shared", "apply"]
        }
        "infra-shared-destroy" = {
          description     = "Destroy shared infrastructure"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "shared"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "destroy"
          }
          tags = ["infra", "shared", "destroy"]
        }
        "infra-shared-refresh" = {
          description     = "Refresh shared infrastructure state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "shared"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "refresh"
          }
          tags = ["infra", "shared", "refresh"]
        }
        "infra-test-apply" = {
          description     = "Apply test environment state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "test"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "apply"
          }
          tags = ["infra", "test", "apply"]
        }
        "infra-test-destroy" = {
          description     = "Destroy test environment"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "test"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "destroy"
          }
          tags = ["infra", "test", "destroy"]
        }
        "infra-test-refresh" = {
          description     = "Refresh test environment state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "test"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "refresh"
          }
          tags = ["infra", "test", "refresh"]
        }
        "infra-prod-apply" = {
          description     = "Apply production environment state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "prod"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "apply"
          }
          tags = ["infra", "production", "apply"]
        }
        "infra-prod-destroy" = {
          description     = "Destroy production environment"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "prod"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "destroy"
          }
          tags = ["infra", "production", "destroy"]
        }
        "infra-prod-refresh" = {
          description     = "Refresh production environment state"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET"    = var.state_bucket_name
            "_DEPLOYMENT_MODE" = "prod"
            "_PROJECT_ID"      = module.project.project_id
            "_ACTION"          = "refresh"
          }
          tags = ["infra", "production", "refresh"]
        }

        # --- App Deploy Triggers ---
        "app-deploy-test" = {
          description     = "Deploy erpnext_enhancements app to test VM"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_deploy_yaml_path
          push = {
            branch = var.cloud_build_deploy_branch
          }
          substitutions = {
            _VM_NAME    = var.spot_vm_name
            _VM_ZONE    = local.spot_vm_zone
            _ALLOW_SKIP = "true"
          }
          tags = ["app", "deploy", "test"]
        }

        "app-deploy-prod" = {
          description     = "Deploy erpnext_enhancements app to production VM"
          disabled        = false
          service_account = local.cb_service_account
          filename        = var.cloudbuild_deploy_yaml_path
          push = {
            branch = var.cloud_build_deploy_branch
          }
          substitutions = {
            _VM_NAME    = var.standard_vm_name
            _VM_ZONE    = local.standalone_vm_zone
            _ALLOW_SKIP = "false"
          }
          tags = ["app", "deploy", "production"]
        }

        # --- App Upstream Update Triggers (manual) ---
        "app-upstream-test" = {
          description     = "Update upstream apps on test VM"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_upstream_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            _VM_NAME    = var.spot_vm_name
            _VM_ZONE    = local.spot_vm_zone
            _ALLOW_SKIP = "true"
          }
          tags = ["app", "upstream", "test"]
        }

        "app-upstream-prod" = {
          description     = "Update upstream apps on production VM"
          service_account = local.cb_service_account
          filename        = var.cloudbuild_upstream_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            _VM_NAME    = var.standard_vm_name
            _VM_ZONE    = local.standalone_vm_zone
            _ALLOW_SKIP = "false"
          }
          tags = ["app", "upstream", "production"]
        }
      }
    }
  }
}

# ============================================================================
# 5. DEPLOY SSH KEY FOR APP CI/CD
# ============================================================================
resource "tls_private_key" "deploy_ssh_key" {
  count     = var.deployment_mode == "shared" && var.provision_cloud_build ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "google_secret_manager_secret" "deploy_ssh_key" {
  count     = var.deployment_mode == "shared" && var.provision_cloud_build ? 1 : 0
  project   = module.project.project_id
  secret_id = "DEPLOY_SSH_KEY"
  replication {
    auto {}
  }

  depends_on = [
    module.project
  ]
}

resource "google_secret_manager_secret_version" "deploy_ssh_key" {
  count       = var.deployment_mode == "shared" && var.provision_cloud_build ? 1 : 0
  secret      = google_secret_manager_secret.deploy_ssh_key[0].id
  secret_data = tls_private_key.deploy_ssh_key[0].private_key_openssh

  depends_on = [
    google_secret_manager_secret.deploy_ssh_key
  ]
}

# Note: The deploy SSH public key is NOT managed via Terraform to avoid
# overwriting existing project-level SSH keys. After apply, run:
#   terraform output deploy_ssh_public_key >> ~/.ssh/deploy_key.pub
# Then manually add the public key to project metadata:
#   gcloud compute project-info add-metadata \
#     --metadata-from-file ssh-keys=<(echo "deploy:$(cat ~/.ssh/deploy_key.pub)")