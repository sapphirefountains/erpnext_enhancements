# network.tf
# Provisions an isolated Custom VPC Network for your services

resource "google_compute_network" "custom_vpc" {
  name                    = var.network
  project                 = module.project.project_id
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "custom_subnet" {
  name          = var.subnetwork
  project       = module.project.project_id
  region        = var.region
  network       = google_compute_network.custom_vpc.id
  ip_cidr_range = "10.0.0.0/24"
}