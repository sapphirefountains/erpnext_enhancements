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

# Compute VM Module: Provisions standard VM instances
# locals {
#   # 1. Dynamically construct the disk list array purely in HCL logic
#   raw_attached_disks = var.enable_vm_persistence ? concat(
#     [
#       {
#         device_name  = "erpnext-data"
#         disk_size_gb = var.vm_data_disk_size
#         disk_type    = "pd-balanced"
#         mode         = "READ_WRITE"
#         type         = "PERSISTENT"
#       }
#     ],
#     [
#       for i in range(var.vm_local_ssd_count) : {
#         device_name  = "local-ssd-${i}"
#         disk_size_gb = 375
#         disk_type    = "local-ssd"
#         interface    = "NVME"
#         mode         = "READ_WRITE"
#         type         = "SCRATCH"
#       }
#     ]
#   ) : []

#   # 2. Inject configuration keys and pre-encoded strings cleanly into templates
#   compute_vm_config = yamldecode(templatefile("${path.module}/configs/compute_vm.yaml", {
#     compute_machine_type      = var.compute_machine_type
#     standard_vm_name          = var.standard_vm_name
#     nat_ip_resolved           = var.enable_standard_public_ip ? "true" : "null"
#     region                    = var.region
#     network                   = local.network_id
#     subnetwork                = local.subnetwork_self_link
#     attached_disks_json       = jsonencode(local.raw_attached_disks) 
#   }))

#   spot_vm_config = yamldecode(templatefile("${path.module}/configs/spot_vm.yaml", {
#     region                = var.region
#     spot_machine_type     = var.spot_machine_type
#     spot_vm_name          = var.spot_vm_name
#     nat_ip_resolved       = var.enable_spot_public_ip ? "true" : "null"
#     network               = local.network_id
#     subnetwork            = local.subnetwork_self_link
#     attached_disks_json   = jsonencode(local.raw_attached_disks) 
#   }))
# }

# module "compute_vm" {
#   for_each     = var.provision_compute_vm ? local.compute_vm_config : {}
#   source       = "../modules/compute-vm"
#   project_id   = module.project.project_id
#   zone         = coalesce(try(each.value.zone, null), "${var.region}-b")
#   name         = each.key
#   machine_type = try(each.value.machine_type, null)
#   network_interfaces = [
#     for ni in try(each.value.network_interfaces, []) : {
#       network    = ni.network
#       subnetwork = ni.subnetwork
#       nat        = coalesce(try(ni.nat, null), local.resolved_vm_ip_external)
#       addresses  = try(ni.addresses, null)
#     }
#   ]
#   boot_disk       = try(each.value.boot_disk, null)
#   attached_disks  = try(each.value.attached_disks, null)
#   service_account = try(each.value.service_account, null)
#   metadata        = try(each.value.metadata, null)
#   labels          = try(each.value.labels, null)
# }

# # Spot VM Module: Provisions ephemeral VM instances using Spot pricing
# module "spot_vm" {
#   for_each     = var.provision_spot_vm ? local.spot_vm_config : {}
#   source       = "../modules/compute-vm"
#   project_id   = module.project.project_id
#   zone         = coalesce(try(each.value.zone, null), "${var.region}-b")
#   name         = each.key
#   machine_type = try(each.value.machine_type, null)
#   # network_interfaces = [
#   #   for ni in try(each.value.network_interfaces, []) : {
#   #     network    = ni.network
#   #     subnetwork = ni.subnetwork
#   #     nat        = coalesce(try(ni.nat, null), var.ip_external)
#   #     addresses  = try(ni.addresses, null)
#   #   }
#   # ]
#   # 🎯 THE FIX: Revert to the module's standard parameter schema signature
#   network_interfaces = [
#     for ni in try(each.value.network_interfaces, []) : {
#       network    = ni.network
#       subnetwork = ni.subnetwork
#       nat        = coalesce(try(ni.nat, null), local.resolved_vm_ip_external)
#       addresses  = try(ni.addresses, null)
#     }
#   ]
#   boot_disk       = try(each.value.boot_disk, null)
#   attached_disks  = try(each.value.attached_disks, null)
#   service_account = try(each.value.service_account, null)
#   metadata        = try(each.value.metadata, null)
#   labels          = try(each.value.labels, null)

#   scheduling_config = {
#     provisioning_model = "SPOT"
#     termination_action = try(each.value.termination_action, null)
#   }
# }


/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

# Compute VM Module: Provisions standard VM instances
locals {
  # 1. Construct disks as a structured MAP of objects instead of a list using merge()
  raw_attached_disks = var.enable_vm_persistence ? merge(
    {
      "data-disk" = {
        device_name  = "erpnext-data"
        disk_size_gb = var.vm_data_disk_size
        disk_type    = "pd-balanced"
        mode         = "READ_WRITE"
        type         = "PERSISTENT"
      }
    },
    {
      for i in range(var.vm_local_ssd_count) : "local-ssd-${i}" => {
        device_name  = "local-ssd-${i}"
        disk_size_gb = 375
        disk_type    = "local-ssd"
        interface    = "NVME"
        mode         = "READ_WRITE"
        type         = "SCRATCH"
      }
    }
  ) : {}

  # 2. Inject configuration keys and pre-encoded strings cleanly into templates
  compute_vm_config = yamldecode(templatefile("${path.module}/configs/compute_vm.yaml", {
    compute_machine_type      = var.compute_machine_type
    standard_vm_name          = var.standard_vm_name
    nat_ip_resolved           = var.enable_standard_public_ip ? "true" : "null"
    region                    = var.region
    network                   = local.network_id
    subnetwork                = local.subnetwork_self_link
    attached_disks_json       = jsonencode(local.raw_attached_disks) 
  }))

  spot_vm_config = yamldecode(templatefile("${path.module}/configs/spot_vm.yaml", {
    region                = var.region
    spot_machine_type     = var.spot_machine_type
    spot_vm_name          = var.spot_vm_name
    nat_ip_resolved       = var.enable_spot_public_ip ? "true" : "null"
    network               = local.network_id
    subnetwork            = local.subnetwork_self_link
    attached_disks_json   = jsonencode(local.raw_attached_disks) 
  }))
}

module "compute_vm" {
  for_each     = var.provision_compute_vm ? local.compute_vm_config : {}
  source       = "../modules/compute-vm"
  project_id   = module.project.project_id
  zone         = coalesce(try(each.value.zone, null), "${var.region}-b")
  name         = each.key
  machine_type = try(each.value.machine_type, null)

  # 🎯 RECONCILED FIX: Build a clean list of objects to satisfy type validation constraints
  network_interfaces = [
    for ni in try(each.value.network_interfaces, []) : {
      network    = ni.network
      subnetwork = ni.subnetwork
      nat        = ni.nat == "true" ? true : false  # Translates string literal into real HCL boolean
      addresses  = try(ni.addresses, null)
    }
  ]

  boot_disk       = try(each.value.boot_disk, null)
  attached_disks  = try(each.value.attached_disks, null)
  service_account = try(each.value.service_account, null)
  metadata        = try(each.value.metadata, null)
  labels          = try(each.value.labels, null)
}

# Spot VM Module: Provisions ephemeral VM instances using Spot pricing
module "spot_vm" {
  for_each     = var.provision_spot_vm ? local.spot_vm_config : {}
  source       = "../modules/compute-vm"
  project_id   = module.project.project_id
  zone         = coalesce(try(each.value.zone, null), "${var.region}-b")
  name         = each.key
  machine_type = try(each.value.machine_type, null)

  # 🎯 RECONCILED FIX: Build a clean list of objects to satisfy type validation constraints
  network_interfaces = [
    for ni in try(each.value.network_interfaces, []) : {
      network    = ni.network
      subnetwork = ni.subnetwork
      nat        = ni.nat == "true" ? true : false  # Translates string literal into real HCL boolean
      addresses  = try(ni.addresses, null)
    }
  ]

  boot_disk       = try(each.value.boot_disk, null)
  attached_disks  = try(each.value.attached_disks, null)
  service_account = try(each.value.service_account, null)
  metadata        = try(each.value.metadata, null)
  labels          = try(each.value.labels, null)

  scheduling_config = {
    provisioning_model = "SPOT"
    termination_action = try(each.value.termination_action, null)
  }
}