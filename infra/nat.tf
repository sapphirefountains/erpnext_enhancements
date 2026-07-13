# Cloud Routers (per region)
resource "google_compute_router" "nat_router" {
  for_each = var.provision_cloud_nat ? toset(var.nat_regions) : toset([])
  name     = "${var.nat_name_prefix}-nat-router-${each.key}"
  network  = local.network_id
  region   = each.key
  project  = module.project.project_id
}

# Cloud NAT Gateways (per region)
resource "google_compute_router_nat" "cloud_nat" {
  for_each                           = var.provision_cloud_nat ? toset(var.nat_regions) : toset([])
  name                               = "${var.nat_name_prefix}-cloud-nat-${each.key}"
  router                             = google_compute_router.nat_router[each.key].name
  region                             = each.key
  project                            = module.project.project_id
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
