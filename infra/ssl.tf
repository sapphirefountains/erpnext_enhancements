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

# SSL Certificate Manager Module
locals {
  ssl_config = yamldecode(templatefile("${path.module}/configs/ssl.yaml", {
    domain_name   = var.domain_name
    ssl_cert_name = var.ssl_cert_name
    ssl_map_name  = var.ssl_map_name
  }))
}

module "ssl_certificates" {
  count      = (var.deployment_mode == "shared" || var.deployment_mode == "prod") && var.provision_ssl ? 1 : 0
  source     = "../modules/certificate-manager"
  project_id = module.project.project_id

  certificates = local.ssl_config.certificates
  map          = local.ssl_config.map
}
