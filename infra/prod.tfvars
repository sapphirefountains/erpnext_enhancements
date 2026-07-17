# ============================================================================
# PRODUCTION ENVIRONMENT — terraform apply -var-file=prod.tfvars
# Manages: standard VM, prod VM disks
# ============================================================================

deployment_mode = "prod"
project_id      = "erpnext-465317"
create_project  = false
region          = "us-east1"
ip_external     = true
use_default_vpc = true

provision_compute_vm = true
standard_vm_name     = "production-erpnext-standard-vm"
compute_machine_type = "n2d-standard-8"
vm_region            = "us-east4"
vm_network_tags = ["web-frontend", "prod-loadbalancer-target"]

enable_vm_persistence = true
reuse_existing_disks  = true
vm_local_ssd_count    = 0
enable_startup_script = true

boot_disk_source_attach = "projects/erpnext-465317/zones/us-east4-a/disks/prod-erpnext-boot-east4"
data_disk_source_attach = "projects/erpnext-465317/zones/us-east4-a/disks/prod-erpnext-data-east4"

vm_labels = { role = "web-frontend" }
