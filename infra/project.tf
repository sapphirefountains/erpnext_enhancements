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

# Dynamically gather service APIs that must be enabled
locals {
  base_apis = [
    "compute.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iap.googleapis.com",
    "serviceusage.googleapis.com",
    "secretmanager.googleapis.com"
  ]

  artifact_registry_apis = var.provision_artifact_registry ? [
    "artifactregistry.googleapis.com"
  ] : []

  cloud_build_apis = var.provision_cloud_build ? [
    "cloudbuild.googleapis.com"
  ] : []

  cloud_function_apis = var.provision_cloud_function ? [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com"
  ] : []

  cloud_run_apis = var.provision_cloud_run ? [
    "run.googleapis.com"
  ] : []

  ssl_apis = var.provision_ssl ? [
    "certificatemanager.googleapis.com"
  ] : []

  sqladmin_apis = var.provision_sql ? [
    "sqladmin.googleapis.com",
    "servicenetworking.googleapis.com"
  ] : []

  # Deduplicate final API list
  all_apis = distinct(concat(
    local.base_apis,
    local.artifact_registry_apis,
    local.cloud_build_apis,
    local.cloud_function_apis,
    local.cloud_run_apis,
    local.ssl_apis,
    local.sqladmin_apis
  ))
}

# Project Module: Manages Google Cloud Project creation or reuse
module "project" {
  source          = "../modules/project"
  name            = var.project_id
  project_reuse   = var.create_project ? null : {}
  billing_account = var.create_project ? var.billing_account_id : null
  prefix          = var.prefix
  services        = local.all_apis
}
