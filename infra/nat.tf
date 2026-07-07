# 1. Cloud Router (Conditional Trigger)
resource "google_compute_router" "nat_router" {
  # 🔄 Dynamic conditional count evaluation
  count   = var.provision_cloud_nat ? 1 : 0
  
  name    = "erpnext-nat-router"
  network = local.network_id # Binds straight to playbook-vpc
  region  = var.region
  project = module.project.project_id
}

# 2. Cloud NAT Gateway Configuration (Conditional Trigger)
resource "google_compute_router_nat" "cloud_nat" {
  # 🔄 Dynamic conditional count evaluation
  count                              = var.provision_cloud_nat ? 1 : 0

  name                               = "erpnext-cloud-nat"
  router                             = google_compute_router.nat_router[0].name
  region                             = var.region
  project                            = module.project.project_id
  nat_ip_allocate_option             = "AUTO_ONLY" 
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}