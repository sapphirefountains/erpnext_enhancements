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

data "google_compute_zones" "compute_vm_available" {
  count   = var.vm_region != null ? 1 : 0
  project = module.project.project_id
  region  = var.vm_region
  status  = "UP"
}

data "google_compute_zones" "spot_vm_available" {
  count   = var.spot_vm_region != null ? 1 : 0
  project = module.project.project_id
  region  = var.spot_vm_region
  status  = "UP"
}

# --- Spot VM boot/data disks (persistent named disks) ---
# Both disks always exist (count = 1) and are always referenced by the spot VM.
# restore_spot_vm_from_snapshot controls the source:
#   true  → create/populate from latest snapshot
#   false → create from standard image (boot) / empty (data)
# snapshot is ignored on subsequent applies (ignore_changes) so daily snapshots
# don't trigger recreation. To restore from a newer snapshot, taint the disks.
data "google_compute_snapshot" "spot_boot_snapshot" {
  count       = var.provision_spot_vm && var.restore_spot_vm_from_snapshot ? 1 : 0
  project     = module.project.project_id
  filter      = "sourceDisk eq \".*${var.spot_vm_name}-boot.*\""
  most_recent = true
}

data "google_compute_snapshot" "spot_data_snapshot" {
  count       = var.provision_spot_vm && var.restore_spot_vm_from_snapshot && var.enable_vm_persistence ? 1 : 0
  project     = module.project.project_id
  filter      = "sourceDisk eq \".*${var.spot_vm_name}-data-disk.*\""
  most_recent = true
}

resource "google_compute_disk" "spot_boot_from_snapshot" {
  count    = var.provision_spot_vm ? 1 : 0
  project  = module.project.project_id
  name     = "${var.spot_vm_name}-boot-restored"
  zone     = local.spot_vm_zone
  type     = var.disk_type
  image    = var.restore_spot_vm_from_snapshot ? null : var.vm_custom_image != null ? var.vm_custom_image : "projects/debian-cloud/global/images/family/debian-12"
  snapshot = var.restore_spot_vm_from_snapshot ? data.google_compute_snapshot.spot_boot_snapshot[0].self_link : null
  lifecycle {
    ignore_changes = [snapshot]
  }
}

resource "google_compute_disk" "spot_data_from_snapshot" {
  count    = var.provision_spot_vm && var.enable_vm_persistence ? 1 : 0
  project  = module.project.project_id
  name     = "${var.spot_vm_name}-data-restored"
  zone     = local.spot_vm_zone
  type     = var.disk_type
  snapshot = var.restore_spot_vm_from_snapshot ? data.google_compute_snapshot.spot_data_snapshot[0].self_link : null
  size     = var.restore_spot_vm_from_snapshot ? null : var.vm_data_disk_size
  lifecycle {
    ignore_changes = [snapshot]
  }
}

locals {
  # 1. Construct disks as a structured MAP of objects instead of a list using merge()
  raw_attached_disks = var.enable_vm_persistence ? merge(
    {
      "data-disk" = merge(
        {
          device_name = "erpnext-data"
          mode        = "READ_WRITE"
          type        = "PERSISTENT"
        },
        var.data_disk_source_attach != null ? {
          source = {
            attach = var.data_disk_source_attach
          }
          } : {
          initialize_params = {
            size = var.vm_data_disk_size
            type = var.disk_type
          }
        }
      )
    },
    {
      for i in range(var.vm_local_ssd_count) : "local-ssd-${i}" => {
        device_name = "local-ssd-${i}"
        mode        = "READ_WRITE"
        type        = "SCRATCH"
        interface   = "NVME"
        initialize_params = {
          size = var.local_ssd_size
          type = "local-ssd"
        }
      }
    }
  ) : {}

  spot_vm_attached_disks = var.enable_vm_persistence ? merge(
    {
      "data-disk" = merge(
        {
          device_name = "erpnext-data"
          mode        = "READ_WRITE"
          type        = "PERSISTENT"
        },
        var.enable_vm_persistence && var.provision_spot_vm ? {
          source = {
            attach = "projects/${module.project.project_id}/zones/${local.spot_vm_zone}/disks/${google_compute_disk.spot_data_from_snapshot[0].name}"
          }
          } : {
          initialize_params = {
            size = var.vm_data_disk_size
            type = var.disk_type
          }
        },
        var.enable_spot_vm_snapshot_schedule ? {
          snapshot_schedule = ["daily-snapshots"]
        } : {}
      )
    },
    {
      for i in range(var.vm_local_ssd_count) : "local-ssd-${i}" => {
        device_name = "local-ssd-${i}"
        mode        = "READ_WRITE"
        type        = "SCRATCH"
        interface   = "NVME"
        initialize_params = {
          size = var.local_ssd_size
          type = "local-ssd"
        }
      }
    }
  ) : {}

  # 2. Inject configuration keys and pre-encoded strings cleanly into templates
  startup_script_raw = templatefile("${path.module}/configs/startup_script.sh", {
    packages                 = join(" ", var.startup_script_packages)
    deploy_user              = var.deploy_user
    deploy_user_sudo_command = var.deploy_user_sudo_command
  })
  startup_script_yaml = indent(6, "\n${local.startup_script_raw}")

  compute_vm_config = yamldecode(templatefile("${path.module}/configs/compute_vm.yaml", {
    compute_machine_type    = var.compute_machine_type
    standard_vm_name        = var.standard_vm_name
    nat_ip_resolved         = var.enable_standard_public_ip ? "true" : "null"
    vm_zone                 = var.vm_zone != null ? var.vm_zone : (var.vm_region != null ? data.google_compute_zones.compute_vm_available[0].names[0] : data.google_compute_zones.available.names[0])
    vm_network_tags         = jsonencode(var.vm_network_tags)
    network                 = local.network_id
    subnetwork              = local.compute_vm_subnet
    attached_disks_json     = jsonencode(local.raw_attached_disks)
    vm_boot_disk_size       = var.vm_boot_disk_size
    reuse_existing_disks    = var.reuse_existing_disks
    boot_disk_auto_delete   = var.boot_disk_auto_delete ? "true" : "false"
    boot_disk_source_attach = var.boot_disk_source_attach != null ? var.boot_disk_source_attach : ""
    vm_boot_disk_image      = var.vm_custom_image != null ? var.vm_custom_image : "projects/debian-cloud/global/images/family/debian-12"
    enable_startup_script   = var.enable_startup_script
    startup_script          = var.enable_startup_script ? local.startup_script_yaml : ""
    boot_disk_type          = var.disk_type
    vm_labels_json          = jsonencode(var.vm_labels)
  }))

  spot_vm_config = yamldecode(templatefile("${path.module}/configs/spot_vm.yaml", {
    vm_zone                            = var.spot_vm_zone != null ? var.spot_vm_zone : (var.spot_vm_region != null ? data.google_compute_zones.spot_vm_available[0].names[0] : data.google_compute_zones.available.names[0])
    spot_machine_type                  = var.spot_machine_type
    spot_vm_name                       = var.spot_vm_name
    nat_ip_resolved                    = var.enable_spot_public_ip ? "true" : "null"
    vm_network_tags                    = jsonencode(var.spot_vm_network_tags)
    network                            = local.network_id
    subnetwork                         = local.spot_vm_subnet
    attached_disks_json                = jsonencode(local.spot_vm_attached_disks)
    vm_boot_disk_size                  = var.vm_boot_disk_size
    reuse_existing_disks               = var.reuse_existing_disks
    boot_disk_auto_delete              = var.boot_disk_auto_delete ? "true" : "false"
    boot_disk_source_attach            = var.provision_spot_vm ? "projects/${module.project.project_id}/zones/${local.spot_vm_zone}/disks/${google_compute_disk.spot_boot_from_snapshot[0].name}" : ""
    vm_boot_disk_image                 = var.vm_custom_image != null ? var.vm_custom_image : "projects/debian-cloud/global/images/family/debian-12"
    enable_startup_script              = var.enable_startup_script
    startup_script                     = var.enable_startup_script ? local.startup_script_yaml : ""
    boot_disk_type                     = var.disk_type
    spot_vm_labels_json                = jsonencode(var.spot_vm_labels)
    enable_spot_vm_snapshot_schedule   = coalesce(var.enable_spot_vm_snapshot_schedule, false)
    snapshot_schedule_start_time       = var.snapshot_schedule_start_time
    snapshot_schedule_retention_days   = var.snapshot_schedule_retention_days
    snapshot_schedule_storage_location = var.snapshot_schedule_storage_location
  }))
}

module "compute_vm" {
  for_each     = { for k, v in local.compute_vm_config : k => v if var.provision_compute_vm }
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
      nat        = ni.nat == "true" ? true : false # Translates string literal into real HCL boolean
      addresses  = try(ni.addresses, null)
    }
  ]

  boot_disk       = try(each.value.boot_disk, null)
  attached_disks  = try(each.value.attached_disks, null)
  service_account = try(each.value.service_account, null)
  metadata        = try(each.value.metadata, null)
  labels          = try(each.value.labels, null)
  tags            = try(each.value.tags, [])
  group           = { named_ports = { http = var.health_check_port } }
}

# Spot VM Module: Provisions ephemeral VM instances using Spot pricing
module "spot_vm" {
  for_each     = { for k, v in local.spot_vm_config : k => v if var.provision_spot_vm }
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
      nat        = ni.nat == "true" ? true : false # Translates string literal into real HCL boolean
      addresses  = try(ni.addresses, null)
    }
  ]

  boot_disk          = try(each.value.boot_disk, null)
  attached_disks     = try(each.value.attached_disks, null)
  service_account    = try(each.value.service_account, null)
  metadata           = try(each.value.metadata, null)
  labels             = try(each.value.labels, null)
  tags               = try(each.value.tags, [])
  group              = { named_ports = { http = var.health_check_port } }
  snapshot_schedules = try(each.value.snapshot_schedules, {})

  scheduling_config = {
    provisioning_model = "SPOT"
    termination_action = try(each.value.termination_action, null)
  }
}

# ============================================================================
# 3. Decoupled Health Check & Firewall Infrastructure
# ============================================================================

resource "google_compute_health_check" "erpnext_standalone_health_check" {
  # 🎯 DECOUPLED: Managed completely by its own standalone variable block toggle
  count   = var.deployment_mode == "shared" && var.enable_standalone_health_check ? 1 : 0
  name    = "erpnext-standalone-health-check"
  project = module.project.project_id

  http_health_check {
    port         = var.health_check_port
    request_path = var.health_check_request_path
  }

  check_interval_sec  = var.health_check_interval_sec
  timeout_sec         = var.health_check_timeout_sec
  healthy_threshold   = var.health_check_healthy_threshold
  unhealthy_threshold = var.health_check_unhealthy_threshold
}

# 🌐 Load Balancer Ingress (Targets Standalone Web Frontends)
resource "google_compute_firewall" "allow_lb_to_vm" {
  count     = var.deployment_mode == "shared" && var.enable_lb_firewall ? 1 : 0
  name      = "allow-lb-to-production-vm"
  network   = local.network_id
  project   = module.project.project_id
  direction = "INGRESS"

  allow {
    protocol = "tcp"
    ports    = var.lb_firewall_ports
  }

  # Standard Google Load Balancer probing ranges
  source_ranges = var.lb_source_ranges
  target_tags   = ["web-frontend"]
}

# 🔒 Secure SSH Tunneling (Targets both Prod and Staging Spot VMs securely via IAP)
resource "google_compute_firewall" "allow_iap_ssh" {
  count     = var.deployment_mode == "shared" && var.enable_iap_ssh_firewall ? 1 : 0
  name      = "allow-iap-ssh-to-vms"
  network   = local.network_id
  project   = module.project.project_id
  direction = "INGRESS"
  priority  = 1000

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = [var.iap_source_range]
}