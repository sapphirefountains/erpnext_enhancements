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

# Cloud Run Module: Provisions Cloud Run services dynamically
locals {
  cloud_run_config = yamldecode(templatefile("${path.module}/configs/cloud_run.yaml", {
    api_url         = var.api_url
    container_image = var.cloud_run_image
    region          = var.region
  }))
}

module "cloud_run" {
  for_each            = var.provision_cloud_run ? local.cloud_run_config : {}
  source              = "../modules/cloud-run-v2"
  project_id          = module.project.project_id
  region              = coalesce(try(each.value.region, null), var.region)
  name                = each.key
  containers          = each.value.containers
  deletion_protection = try(each.value.deletion_protection, null)
  iam                 = try(each.value.iam, null)

  # Clean default pass restored
  revision = try(each.value.revision, null)

  # 🎯 THE EXACT SCHEMA FIX: Feed the boolean flag required by the module's internal logic
  service_config = {
    ingress                    = var.ip_external ? "INGRESS_TRAFFIC_ALL" : "INGRESS_TRAFFIC_INTERNAL_ONLY"
    gen2_execution_environment = true # 🚀 This triggers EXECUTION_ENVIRONMENT_GEN2 internally!
  }

  volumes = try(each.value.volumes, null)
}
