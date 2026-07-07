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

# IPS Module: Handles static and ephemeral IP address allocations
locals {
  ips_config = yamldecode(templatefile("${path.module}/configs/ips.yaml", {
    glb_ip_name = var.glb_ip_name
    region      = var.region
    subnetwork  = local.subnetwork_self_link
    web_ip_name = var.web_ip_name
  }))
}

module "ips" {
  count      = var.provision_ips ? 1 : 0
  source     = "../modules/net-address"
  project_id = module.project.project_id

  external_addresses = merge(
    var.ip_external ? local.ips_config.addresses : {},
    local.ips_config.external_addresses
  )
  internal_addresses = merge(
    var.ip_external ? {} : local.ips_config.addresses,
    local.ips_config.internal_addresses
  )
  global_addresses = local.ips_config.global_addresses
}
