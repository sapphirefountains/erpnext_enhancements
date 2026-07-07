# network.tf
# Provisions an isolated Custom VPC Network or utilizes the default VPC

data "google_compute_network" "default_vpc" {
  count   = var.use_default_vpc ? 1 : 0
  name    = "default"
  project = module.project.project_id
}

data "google_compute_subnetwork" "default_subnet" {
  count   = var.use_default_vpc ? 1 : 0
  name    = "default"
  region  = var.region
  project = module.project.project_id
}

resource "google_compute_network" "custom_vpc" {
  count                   = var.use_default_vpc ? 0 : 1
  name                    = var.network
  project                 = module.project.project_id
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "custom_subnet" {
  count         = var.use_default_vpc ? 0 : 1
  name          = var.subnetwork
  project       = module.project.project_id
  region        = var.region
  network       = google_compute_network.custom_vpc[0].id
  ip_cidr_range = "10.0.0.0/24"
}

locals {
  network_id              = var.use_default_vpc ? data.google_compute_network.default_vpc[0].id : google_compute_network.custom_vpc[0].id
  network_name            = var.use_default_vpc ? data.google_compute_network.default_vpc[0].name : google_compute_network.custom_vpc[0].name
  subnetwork_self_link    = var.use_default_vpc ? data.google_compute_subnetwork.default_subnet[0].self_link : google_compute_subnetwork.custom_subnet[0].self_link
  resolved_vm_ip_external = var.vm_ip_external != null ? var.vm_ip_external : var.ip_external
}