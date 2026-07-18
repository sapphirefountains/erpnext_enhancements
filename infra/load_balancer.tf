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
# Load Balancer Module: Manages external application load balancer setups
locals {
  prod_mig_groups     = local.resolved_prod_mig_groups
  test_mig_groups     = local.resolved_test_mig_groups
  mig_health_check_id = length(google_compute_health_check.erpnext_standalone_health_check) > 0 ? google_compute_health_check.erpnext_standalone_health_check[0].id : ""

  has_spot_vm_backend  = var.provision_spot_vm_lb_backend

  standalone_vm_zone = var.vm_zone != null ? var.vm_zone : (
    var.vm_region != null
    ? data.google_compute_zones.compute_vm_available[0].names[0]
    : data.google_compute_zones.available.names[0]
  )

  spot_vm_zone = var.spot_vm_zone != null ? var.spot_vm_zone : (
    var.spot_vm_region != null
    ? data.google_compute_zones.spot_vm_available[0].names[0]
    : data.google_compute_zones.available.names[0]
  )

  _lb_template = templatefile("${path.module}/configs/load_balancer.yaml", {
    glb_ip_name                      = var.glb_ip_name
    spot_glb_ip_name                 = var.spot_glb_ip_name
    spot_lb_name                     = var.spot_lb_name
    production_lb_name               = var.production_lb_name
    region                           = var.region
    provision_prod_mig               = var.provision_prod_mig
    provision_test_mig               = var.provision_test_mig
    provision_compute_vm             = var.provision_compute_vm
    provision_standard_vm_lb_backend = var.provision_standard_vm_lb_backend
    provision_spot_vm_lb_backend     = local.has_spot_vm_backend
    provision_cloud_run              = var.provision_cloud_run
    standalone_vm_neg_name           = format("projects/%s/zones/%s/instanceGroups/%s", var.project_id, local.standalone_vm_zone, var.standard_vm_name)
    spot_vm_neg_name                 = format("projects/%s/zones/%s/instanceGroups/%s", var.project_id, local.spot_vm_zone, var.spot_vm_name)
    spot_domain                      = try(var.domains[1], "beta.erp.sapphirefountains.com")
    production_domain                = try(var.domains[0], "erp.sapphirefountains.com")
  })

  load_balancer_config = try(yamldecode(local._lb_template), tomap({}))

  # 1. Decoupled IP Address Resolution
  glb_addresses = (
    var.provision_ips && length(module.ips) > 0
    ? {
      for k, v in module.ips[0].global_addresses :
      "$$addresses:global:${k}" => v.address
    }
    : {}
  )

}

module "load_balancer" {
  for_each               = local.load_balancer_config
  source                 = "../modules/net-lb-app-ext"
  project_id             = module.project.project_id
  name                   = each.key
  backend_buckets_config = try(each.value.backend_buckets_config, {})
  backend_service_configs = {
    for bs_k, bs_v in try(each.value.backend_service_configs, {}) :
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
  health_check_configs = try(each.value.health_check_configs, {})
  neg_configs          = try(each.value.neg_configs, {})
  protocol             = try(each.value.protocol, null)
  urlmap_config        = try(each.value.urlmap_config, {})
  use_classic_version  = try(each.value.use_classic_version, false)

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

  # Pass ssl certificates configs (managed_configs creates google_compute_managed_ssl_certificate)
  ssl_certificates = {
    certificate_ids = try(each.value.ssl_certificates.certificate_ids, [])
    create_configs  = try(each.value.ssl_certificates.create_configs, {})
    managed_configs = try(each.value.ssl_certificates.managed_configs, {})
  }

  depends_on = [
    module.spot_vm,
    module.compute_vm
  ]
}

# HTTP-to-HTTPS redirect resources
locals {
  http_redirect_configs = var.provision_ips && length(module.ips) > 0 ? {
    for k, v in local.load_balancer_config : k => {
      ip_address = k == var.spot_lb_name
        ? module.ips[0].global_addresses[var.spot_glb_ip_name].address
        : module.ips[0].global_addresses[var.glb_ip_name].address
    }
  } : {}
}

resource "google_compute_url_map" "http_redirect" {
  for_each    = local.http_redirect_configs
  provider    = google-beta
  project     = var.project_id
  name        = "${each.key}-http-redirect"
  description = "HTTP to HTTPS redirect for ${each.key}"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  for_each = local.http_redirect_configs
  provider = google-beta
  project  = var.project_id
  name     = "${each.key}-http-proxy"
  url_map  = google_compute_url_map.http_redirect[each.key].id
}

resource "google_compute_global_forwarding_rule" "http_redirect" {
  for_each    = local.http_redirect_configs
  provider    = google-beta
  project     = var.project_id
  name        = "${each.key}-http-rule"
  ip_address  = each.value.ip_address
  ip_protocol = "TCP"
  port_range  = "80"
  target      = google_compute_target_http_proxy.redirect[each.key].id
}