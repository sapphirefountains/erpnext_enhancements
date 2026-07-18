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

locals {
  # Build unique dns authorization keys from domains
  _dns_auth_keys = [for d in var.domains : "dns-auth-${replace(d, ".", "-")}"]
  _dns_auth_map  = {
    for i, d in var.domains : var.enable_dns_authorization ? local._dns_auth_keys[i] : "" => {
      domain = d
    } if var.enable_dns_authorization
  }

  ssl_config = {
    certificates = {
      (var.ssl_cert_name) = {
        managed = merge(
          { domains = var.domains },
          var.enable_dns_authorization ? { dns_authorizations = local._dns_auth_keys } : {}
        )
      }
    }
    map = {
      name        = var.ssl_map_name
      description = "Managed SSL Certificate Map for Web Applications"
      entries = {
        for d in var.domains :
        replace(d, ".", "-") => {
          certificates = [var.ssl_cert_name]
          hostname     = d
        }
      }
    }
    dns_authorizations = local._dns_auth_map
  }
}

module "ssl_certificates" {
  count      = var.deployment_mode == "shared" && var.provision_ssl ? 1 : 0
  source     = "../modules/certificate-manager"
  project_id = module.project.project_id

  certificates      = local.ssl_config.certificates
  map               = local.ssl_config.map
  dns_authorizations = try(local.ssl_config.dns_authorizations, {})
}
