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

# Cloud SQL Module: Provisions Cloud SQL database instances dynamically
locals {
  sql_config = yamldecode(templatefile("${path.module}/configs/sql.yaml", {
    db_tier    = var.sql_tier
    db_version = var.sql_db_version
    project_id = module.project.project_id
    network    = local.network_name
  }))
}

module "sql" {
  for_each         = var.provision_sql ? local.sql_config : {}
  source           = "../modules/cloudsql-instance"
  project_id       = module.project.project_id
  region           = var.region
  prefix           = var.prefix
  name             = each.key
  database_version = try(each.value.database_version, null)
  tier             = try(each.value.tier, null)

  network_config = {
    connectivity = {
      public_ipv4 = coalesce(try(each.value.ip_external, null), var.ip_external)
      psa_config = coalesce(try(each.value.ip_external, null), var.ip_external) ? null : {
        private_network = try(each.value.network, null)
      }
    }
  }

  depends_on = [
    google_service_networking_connection.psa_connection
  ]
}

# Reserve a global internal IP range for Private Services Access (PSA)
resource "google_compute_global_address" "psa_range" {
  count         = var.provision_sql && !var.ip_external ? 1 : 0
  project       = module.project.project_id
  name          = "${var.prefix == null ? "" : "${var.prefix}-"}psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = local.network_id
}

# Create a private connection for Service Networking
resource "google_service_networking_connection" "psa_connection" {
  count                   = var.provision_sql && !var.ip_external ? 1 : 0
  network                 = local.network_id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa_range[0].name]
  depends_on              = [module.project]
}




