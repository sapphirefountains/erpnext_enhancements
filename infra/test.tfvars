# ============================================================================
# TEST ENVIRONMENT — terraform apply -var-file=test.tfvars
# Manages: spot VM, spot VM disks, disk snapshots
# ============================================================================

deployment_mode = "test"
project_id      = "erpnext-465317"
create_project  = false
region          = "us-east1"
ip_external     = true
use_default_vpc = true

provision_spot_vm = true
spot_vm_name      = "test-erpnext-spot-vm"
spot_machine_type = "n2-standard-2"
spot_vm_region    = "us-central1"
spot_vm_network_tags = ["web-frontend", "test-loadbalancer-target"]

enable_vm_persistence       = true
reuse_existing_disks        = true
vm_local_ssd_count          = 0
enable_startup_script       = true
restore_spot_vm_from_snapshot = true

enable_spot_vm_snapshot_schedule = true

spot_vm_labels = { role = "batch-processor" }

