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
  create_duration = "45s"

  depends_on = [
    module.project
  ]
}

# ============================================================================
# 3. SECRET MANAGER CONFIGURATION
# ============================================================================
module "cloud_build_secret" {
  count      = var.provision_cloud_build ? 1 : 0
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
    time_sleep.wait_for_api_provisioning
  ]
}

# ============================================================================
# 4. NATIVE CLOUD BUILD V2 CONNECTION & TRIGGERS
# ============================================================================
module "cloud_build_connection" {
  count      = var.provision_cloud_build ? 1 : 0
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
        # Automated pipeline trigger on pushing modifications to main branch
        "push-trigger" = {
          description     = "Trigger to run apply on merge to main"
          service_account = local.cb_service_account
          filename        = "cloudbuild.yaml"
          push = {
            branch = replace(var.deploy_branch_regex, "refs/heads/", "")
          }
          substitutions = {
            "_ENVIRONMENT" = "production"
          }
        }

        # --- Manual On-Demand Automation Triggers ---
        # 🎯 THE FIX: Satisfies the module's validation constraint by assigning a clean, non-matching string
        "terraform-apply-pipeline" = {
          description     = "Manual on-demand execution of terraform apply."
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only" # 👈 Satisfies variable check safely
          }
          substitutions = {
            "_STATE_BUCKET" = google_storage_bucket.tf_state.name
            "_PROJECT_ID"   = module.project.project_id
            "_ACTION"       = "apply"
          }
        }

        "terraform-destroy-pipeline" = {
          description     = "Manual on-demand execution of terraform destroy."
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET" = google_storage_bucket.tf_state.name
            "_PROJECT_ID"   = module.project.project_id
            "_ACTION"       = "destroy"
          }
        }

        "terraform-refresh-pipeline" = {
          description     = "Manual on-demand execution of terraform refresh."
          service_account = local.cb_service_account
          filename        = var.cloudbuild_yaml_path
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            "_STATE_BUCKET" = google_storage_bucket.tf_state.name
            "_PROJECT_ID"   = module.project.project_id
            "_ACTION"       = "refresh"
          }
        }

        # --- App Deploy Triggers ---
        "deploy-test" = {
          description     = "Deploy erpnext_enhancements app to test VM"
          service_account = local.cb_service_account
          filename        = "cloudbuild-deploy.yaml"
          push = {
            branch = replace(var.deploy_branch_regex, "refs/heads/", "")
          }
          substitutions = {
            _VM_NAME     = var.test_vm_name
            _VM_ZONE     = var.test_vm_zone
            _ALLOW_SKIP  = "true"
          }
          tags = ["deploy", "test"]
        }

        "deploy-prod" = {
          description     = "Deploy erpnext_enhancements app to production VM"
          service_account = local.cb_service_account
          filename        = "cloudbuild-deploy.yaml"
          push = {
            branch = replace(var.deploy_branch_regex, "refs/heads/", "")
          }
          substitutions = {
            _VM_NAME     = var.production_vm_name
            _VM_ZONE     = var.production_vm_zone
            _ALLOW_SKIP  = "false"
          }
          tags = ["deploy", "production"]
        }

        # --- Upstream Update Triggers (manual) ---
        "upstream-test" = {
          description     = "Update upstream apps on test VM"
          service_account = local.cb_service_account
          filename        = "cloudbuild-upstream.yaml"
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            _VM_NAME     = var.test_vm_name
            _VM_ZONE     = var.test_vm_zone
            _ALLOW_SKIP  = "true"
          }
          tags = ["upstream", "test"]
        }

        "upstream-prod" = {
          description     = "Update upstream apps on production VM"
          service_account = local.cb_service_account
          filename        = "cloudbuild-upstream.yaml"
          push = {
            branch = "manual-trigger-only"
          }
          substitutions = {
            _VM_NAME     = var.production_vm_name
            _VM_ZONE     = var.production_vm_zone
            _ALLOW_SKIP  = "false"
          }
          tags = ["upstream", "production"]
        }
      }
    }
  }
}

# ============================================================================
# 5. DEPLOY SSH KEY FOR APP CI/CD
# ============================================================================
resource "tls_private_key" "deploy_ssh_key" {
  count     = var.provision_cloud_build ? 1 : 0
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "google_secret_manager_secret" "deploy_ssh_key" {
  count     = var.provision_cloud_build ? 1 : 0
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
  count       = var.provision_cloud_build ? 1 : 0
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