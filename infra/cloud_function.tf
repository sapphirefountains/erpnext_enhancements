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

# Cloud Function Module: Provisions Cloud Functions (v2) dynamically
locals {
  cloud_function_config = yamldecode(templatefile("${path.module}/configs/cloud_function.yaml", {
    cloud_function_bucket = var.cloud_function_bucket
    region                = var.region
  }))
}

module "cloud_function" {
  for_each       = var.provision_cloud_function ? local.cloud_function_config : {}
  source         = "../modules/cloud-function-v2"
  project_id     = module.project.project_id
  region         = coalesce(try(each.value.region, null), var.region)
  name           = each.key
  bucket_name    = try(each.value.bucket_name, null)
  bucket_config  = try(each.value.bucket_config, {})
  bundle_config  = try(each.value.bundle_config, null)
  iam            = try(each.value.iam, null)
  trigger_config = try(each.value.trigger_config, null)
  vpc_connector  = try(each.value.vpc_connector, null)
  ingress_settings = coalesce(
    try(each.value.ingress_settings, null),
    var.ip_external ? "ALLOW_ALL" : "ALLOW_INTERNAL_ONLY"
  )
}
