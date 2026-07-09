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

# 


/**
 * Copyright 2026 Google LLC
 */

# Load Balancer Module: Manages external application load balancer setups
locals {
  prod_mig_groups     = local.resolved_prod_mig_groups
  test_mig_groups     = local.resolved_test_mig_groups
  # mig_health_check_id = length(google_compute_health_check.mig_health_check) > 0 ? google_compute_health_check.mig_health_check[0].id : ""
  mig_health_check_id = length(google_compute_health_check.erpnext_standalone_health_check) > 0 ? google_compute_health_check.erpnext_standalone_health_check[0].id : ""

  default_service = (
    var.provision_prod_mig ? "prod-mig-backend" :
    (var.provision_test_mig ? "test-mig-backend" : "frontend-neg")
  )

  load_balancer_config = yamldecode(templatefile("${path.module}/configs/load_balancer.yaml", {
    glb_ip_name          = var.glb_ip_name
    region               = var.region
    ssl_map_name         = var.ssl_map_name
    provision_prod_mig   = var.provision_prod_mig
    provision_test_mig   = var.provision_test_mig
    provision_cloud_run  = var.provision_cloud_run
    default_service      = local.default_service
  }))

  # 1. Decoupled IP Address Resolution
  glb_addresses = (
    var.provision_ips && length(module.ips) > 0
    ? {
      for k, v in module.ips[0].global_addresses :
      "$$addresses:global:${k}" => v.address
    }
    : {}
  )

  # 2. Decoupled SSL Certificate Map Resolution Pattern
  constructed_fallback_map_id = "//certificatemanager.googleapis.com/projects/${var.project_id}/locations/global/certificateMaps/${var.ssl_map_name}"

  cert_maps = (
    var.provision_ssl && length(module.ssl_certificates) > 0 && try(module.ssl_certificates[0].map, null) != null
    ? {
      "$$ssl_certificates:${var.ssl_map_name}" = "//certificatemanager.googleapis.com/${module.ssl_certificates[0].map_id}"
    }
    : {
      # 🎯 THE FIX: If provision_ssl is false, mapping fallback resolves gracefully to the pattern instead of crashing
      "$$ssl_certificates:${var.ssl_map_name}" = local.constructed_fallback_map_id
    }
  )
}

module "load_balancer" {
  for_each                = var.provision_load_balancer ? local.load_balancer_config : {}
  source                  = "../modules/net-lb-app-ext"
  project_id              = module.project.project_id
  name                    = each.key
  backend_buckets_config  = try(each.value.backend_buckets_config, {})
  backend_service_configs = {
    for bs_k, bs_v in coalesce(try(each.value.backend_service_configs, {}), {}) :
    bs_k => merge(bs_v, {
      backends = flatten([
        for b in try(bs_v.backends, []) : (
          b.backend == "$$prod_mig_group" ? [
            for mig in local.prod_mig_groups : merge(b, { backend = mig })
          ] :
          b.backend == "$$test_mig_group" ? [
            for mig in local.test_mig_groups : merge(b, { backend = mig })
          ] : [b]
        )
      ])
      health_checks = [
        for hc in try(bs_v.health_checks, []) : (
          hc == "$$mig_health_check_id" ? local.mig_health_check_id : hc
        )
      ]
    })
  }
  health_check_configs    = try(each.value.health_check_configs, {})
  neg_configs             = try(each.value.neg_configs, {})
  protocol                = try(each.value.protocol, null)
  urlmap_config           = try(each.value.urlmap_config, {})
  use_classic_version     = try(each.value.use_classic_version, false)

  # Resolve forwarding rule addresses using lookup
  forwarding_rules_config = {
    for r_k, r_v in try(each.value.forwarding_rules_config, {}) :
    r_k => merge(r_v, {
      address = (
        r_v.address == null
        ? null
        : lookup(local.glb_addresses, r_v.address, r_v.address)
      )
    })
  }

  # Pass https_proxy_config and safely resolve the certificate map ID
  https_proxy_config = {
    certificate_map = (
      try(each.value.ssl_certificates.certificate_map, null) == null
      ? null
      : lookup(
        local.cert_maps,
        each.value.ssl_certificates.certificate_map,
        local.constructed_fallback_map_id # 🎯 THE FIX: Safer generic fallback alternative default
      )
    )
  }

  # Pass other ssl certificates configs
  ssl_certificates = {
    certificate_ids = try(each.value.ssl_certificates.certificate_ids, [])
    create_configs  = try(each.value.ssl_certificates.create_configs, {})
    managed_configs = try(each.value.ssl_certificates.managed_configs, {})
  }
}