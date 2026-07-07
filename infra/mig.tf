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

data "google_compute_zones" "available" {
  project = module.project.project_id
  region  = var.region
  status  = "UP"
}

locals {
  resolved_prod_mig_groups = concat(
    (var.provision_prod_mig && var.use_zonal_mig && length(google_compute_instance_group_manager.prod_mig) > 0) ? [google_compute_instance_group_manager.prod_mig[0].instance_group] : [],
    (var.provision_prod_mig && var.use_regional_mig && length(google_compute_region_instance_group_manager.prod_mig) > 0) ? [google_compute_region_instance_group_manager.prod_mig[0].instance_group] : []
  )
  resolved_test_mig_groups = concat(
    (var.provision_test_mig && var.use_zonal_mig && length(google_compute_instance_group_manager.test_mig) > 0) ? [google_compute_instance_group_manager.test_mig[0].instance_group] : [],
    (var.provision_test_mig && var.use_regional_mig && length(google_compute_region_instance_group_manager.test_mig) > 0) ? [google_compute_region_instance_group_manager.test_mig[0].instance_group] : []
  )
  zone_count = length(data.google_compute_zones.available.names)
}

# ============================================================================
# 1. Dedicated Service Account & IAM Permissions
# ============================================================================

resource "google_service_account" "mig_sa" {
  account_id   = "erpnext-mig-sa"
  display_name = "ERPNext Managed Instance Group Service Account"
  project      = module.project.project_id
}

resource "google_project_iam_member" "mig_sa_logging" {
  project = module.project.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.mig_sa.email}"
}

resource "google_project_iam_member" "mig_sa_monitoring" {
  project = module.project.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.mig_sa.email}"
}

# ============================================================================
# 2. Shared Health Check & Firewall Rules
# ============================================================================

resource "google_compute_health_check" "mig_health_check" {
  count   = (var.provision_prod_mig || var.provision_test_mig) ? 1 : 0
  name    = "erpnext-mig-health-check"
  project = module.project.project_id

  http_health_check {
    port         = var.mig_health_check_port
    request_path = "/"
  }

  check_interval_sec  = 15
  timeout_sec         = 5
  healthy_threshold   = 2
  unhealthy_threshold = 3
}

resource "google_compute_firewall" "allow_lb_to_mig" {
  count   = (var.provision_prod_mig || var.provision_test_mig) ? 1 : 0
  name    = "allow-lb-to-mig"
  network = local.network_name
  project = module.project.project_id

  allow {
    protocol = "tcp"
    ports    = [var.mig_health_check_port]
  }

  source_ranges = ["130.211.0.0/22", "35.191.0.0/16"]
  target_tags   = ["erpnext-mig-node"]
}

# ============================================================================
# 3. Production Environment Resources (1-Year Committed Use Eligible)
# ============================================================================

resource "google_compute_instance_template" "prod_template" {
  count        = var.provision_prod_mig ? 1 : 0
  name_prefix  = "prod-erpnext-template-"
  project      = module.project.project_id
  region       = var.region
  machine_type = var.prod_mig_machine_type

  tags = ["erpnext-mig-node"]

  network_interface {
    network            = local.network_id
    subnetwork         = local.subnetwork_self_link
    subnetwork_project = module.project.project_id
    dynamic "access_config" {
      for_each = local.resolved_vm_ip_external ? [""] : []
      content {}
    }
  }

  # OS/Boot Disk
  disk {
    source_image = "projects/debian-cloud/global/images/family/debian-12"
    auto_delete  = true
    boot         = true
    disk_size_gb = 50
    disk_type    = "pd-balanced"
    type         = "PERSISTENT"
  }

  # Stateful Persistent Disk for Database Data and Frappe Pool
  disk {
    auto_delete  = false
    boot         = false
    device_name  = "erpnext-data"
    disk_size_gb = var.mig_data_disk_size
    disk_type    = "pd-balanced"
    type         = "PERSISTENT"
  }

  # High-Performance Local SSDs (Scratch Disks) for database IOPS optimization
  dynamic "disk" {
    for_each = range(var.mig_local_ssd_count)
    content {
      type         = "SCRATCH"
      disk_type    = "local-ssd"
      interface    = "NVME"
      auto_delete  = true
      boot         = false
      disk_size_gb = 375
    }
  }

  scheduling {
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
    provisioning_model  = "STANDARD"
  }

  service_account {
    email  = google_service_account.mig_sa.email
    scopes = ["cloud-platform"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_instance_group_manager" "prod_mig" {
  count              = (var.provision_prod_mig && var.use_zonal_mig) ? 1 : 0
  name               = "prod-erpnext-mig"
  project            = module.project.project_id
  base_instance_name = "prod-erpnext"
  zone               = var.prod_mig_zone

  version {
    instance_template = google_compute_instance_template.prod_template[0].id
  }

  # Stateful disk configuration guarantees persistent storage preservation
  stateful_disk {
    device_name = "erpnext-data"
    delete_rule = "NEVER"
  }

  # Target size is managed by the autoscaler if autoscaling is enabled
  target_size = var.enable_prod_autoscaling ? null : 1

  named_port {
    name = "http"
    port = var.mig_health_check_port
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.mig_health_check[0].id
    initial_delay_sec = 300
  }

  update_policy {
    type                  = "PROACTIVE"
    minimal_action        = "REPLACE"
    replacement_method    = "RECREATE"
    max_surge_fixed       = 0
    max_unavailable_fixed = 1
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_instance_group_manager" "prod_mig" {
  count              = (var.provision_prod_mig && var.use_regional_mig) ? 1 : 0
  name               = "prod-erpnext-mig"
  project            = module.project.project_id
  base_instance_name = "prod-erpnext"
  region             = var.region

  version {
    instance_template = google_compute_instance_template.prod_template[0].id
  }

  # Stateful disk configuration guarantees persistent storage preservation
  stateful_disk {
    device_name = "erpnext-data"
    delete_rule = "NEVER"
  }

  # Target size is managed by the autoscaler if autoscaling is enabled
  target_size = var.enable_prod_autoscaling ? null : 1

  named_port {
    name = "http"
    port = var.mig_health_check_port
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.mig_health_check[0].id
    initial_delay_sec = 300
  }

  update_policy {
    type                         = "PROACTIVE"
    minimal_action               = "REPLACE" 
    replacement_method           = "RECREATE"
    max_surge_fixed              = 0
    max_unavailable_fixed        = local.zone_count
    instance_redistribution_type = "NONE" # Added to fix the error
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_autoscaler" "prod_autoscaler" {
  count   = (var.provision_prod_mig && var.enable_prod_autoscaling && var.use_zonal_mig) ? 1 : 0
  name    = "prod-erpnext-autoscaler"
  project = module.project.project_id
  zone    = var.prod_mig_zone
  target  = google_compute_instance_group_manager.prod_mig[0].id

  autoscaling_policy {
    max_replicas    = var.prod_autoscaling_max_replicas
    min_replicas    = 1
    cooldown_period = 60

    cpu_utilization {
      target = 0.6
    }
  }
}

resource "google_compute_region_autoscaler" "prod_autoscaler" {
  count   = (var.provision_prod_mig && var.enable_prod_autoscaling && var.use_regional_mig) ? 1 : 0
  name    = "prod-erpnext-autoscaler"
  project = module.project.project_id
  region  = var.region
  target  = google_compute_region_instance_group_manager.prod_mig[0].id

  autoscaling_policy {
    max_replicas    = var.prod_autoscaling_max_replicas
    min_replicas    = 1
    cooldown_period = 60

    cpu_utilization {
      target = 0.6
    }
  }
}

# ============================================================================
# 4. Testing Environment Resources (Spot Preemptible VMs)
# ============================================================================

resource "google_compute_instance_template" "test_template" {
  count        = var.provision_test_mig ? 1 : 0
  name_prefix  = "test-erpnext-template-"
  project      = module.project.project_id
  region       = var.region
  machine_type = var.test_mig_machine_type

  tags = ["erpnext-mig-node"]

  network_interface {
    network            = local.network_id
    subnetwork         = local.subnetwork_self_link
    subnetwork_project = module.project.project_id
    dynamic "access_config" {
      for_each = local.resolved_vm_ip_external ? [""] : []
      content {}
    }
  }

  # OS/Boot Disk
  disk {
    source_image = "projects/debian-cloud/global/images/family/debian-12"
    auto_delete  = true
    boot         = true
    disk_size_gb = 50
    disk_type    = "pd-balanced"
    type         = "PERSISTENT"
  }

  # Stateful Persistent Disk for Database Data and Frappe Pool
  disk {
    auto_delete  = false
    boot         = false
    device_name  = "erpnext-data"
    disk_size_gb = var.mig_data_disk_size
    disk_type    = "pd-balanced"
    type         = "PERSISTENT"
  }

  # High-Performance Local SSDs (Scratch Disks) for database IOPS optimization
  dynamic "disk" {
    for_each = range(var.mig_local_ssd_count)
    content {
      type         = "SCRATCH"
      disk_type    = "local-ssd"
      interface    = "NVME"
      auto_delete  = true
      boot         = false
      disk_size_gb = 375
    }
  }

  scheduling {
    automatic_restart           = false
    on_host_maintenance         = "TERMINATE"
    provisioning_model          = "SPOT"
    preemptible                 = true
    instance_termination_action = "STOP"
  }

  service_account {
    email  = google_service_account.mig_sa.email
    scopes = ["cloud-platform"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_instance_group_manager" "test_mig" {
  count              = (var.provision_test_mig && var.use_zonal_mig) ? 1 : 0
  name               = "test-erpnext-mig"
  project            = module.project.project_id
  base_instance_name = "test-erpnext"
  zone               = var.test_mig_zone

  version {
    instance_template = google_compute_instance_template.test_template[0].id
  }

  # Stateful disk configuration guarantees persistent storage preservation
  stateful_disk {
    device_name = "erpnext-data"
    delete_rule = "NEVER"
  }

  # Target size is managed by the autoscaler if autoscaling is enabled
  target_size = var.enable_test_autoscaling ? null : 1

  named_port {
    name = "http"
    port = var.mig_health_check_port
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.mig_health_check[0].id
    initial_delay_sec = 300
  }

  update_policy {
    type                  = "PROACTIVE"
    minimal_action        = "REPLACE"
    replacement_method    = "RECREATE"
    max_surge_fixed       = 0
    max_unavailable_fixed = 1
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_region_instance_group_manager" "test_mig" {
  count              = (var.provision_test_mig && var.use_regional_mig) ? 1 : 0
  name               = "test-erpnext-mig"
  project            = module.project.project_id
  base_instance_name = "test-erpnext"
  region             = var.region

  version {
    instance_template = google_compute_instance_template.test_template[0].id
  }

  # Stateful disk configuration guarantees persistent storage preservation
  stateful_disk {
    device_name = "erpnext-data"
    delete_rule = "NEVER"
  }

  # Target size is managed by the autoscaler if autoscaling is enabled
  target_size = var.enable_test_autoscaling ? null : 1

  named_port {
    name = "http"
    port = var.mig_health_check_port
  }

  auto_healing_policies {
    health_check      = google_compute_health_check.mig_health_check[0].id
    initial_delay_sec = 300
  }

  update_policy {
    type                         = "PROACTIVE"
    minimal_action               = "REPLACE"
    replacement_method           = "RECREATE"
    max_surge_fixed              = 0
    max_unavailable_fixed        = local.zone_count
    instance_redistribution_type = "NONE" # Added to fix the error
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_autoscaler" "test_autoscaler" {
  count   = (var.provision_test_mig && var.enable_test_autoscaling && var.use_zonal_mig) ? 1 : 0
  name    = "test-erpnext-autoscaler"
  project = module.project.project_id
  zone    = var.test_mig_zone
  target  = google_compute_instance_group_manager.test_mig[0].id

  autoscaling_policy {
    max_replicas    = var.test_autoscaling_max_replicas
    min_replicas    = 1
    cooldown_period = 60

    cpu_utilization {
      target = 0.6
    }
  }
}

resource "google_compute_region_autoscaler" "test_autoscaler" {
  count   = (var.provision_test_mig && var.enable_test_autoscaling && var.use_regional_mig) ? 1 : 0
  name    = "test-erpnext-autoscaler"
  project = module.project.project_id
  region  = var.region
  target  = google_compute_region_instance_group_manager.test_mig[0].id

  autoscaling_policy {
    max_replicas    = var.test_autoscaling_max_replicas
    min_replicas    = 1
    cooldown_period = 60

    cpu_utilization {
      target = 0.6
    }
  }
}
