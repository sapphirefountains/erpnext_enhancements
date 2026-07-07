# Service Account Security & Granularity Report

This report provides a detailed analysis of the IAM permissions configured for service accounts in the service provisioning playbook (`infra/` directory), identifies security concerns, and documents the resolution steps implemented to achieve a granular, least-privilege configuration.

---

## 1. Implemented Service Account & IAM Security Resolution

The playbook has been hardened by moving away from default service accounts with broad permissions to a dedicated custom runner identity with granular role assignments.

### A. Custom Terraform Provisioner Service Account (New)
* **Identity:** `sa-terraform-provisioner@${module.project.project_id}.iam.gserviceaccount.com`
* **Configuration Location:** [iam.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf#L121-L163) and [cloud_build.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_build.tf#L78-L84)
* **Status:** **Implemented & Active**
* **Security Evaluation:**
  > [!NOTE]
  > **Best Practice**: The infrastructure deployment now executes under this dedicated, low-privilege custom service account.
  > Instead of `roles/editor`, it is only granted the minimum necessary roles required to provision the specific resources declared in the Terraform configurations.

---

### B. Cloud Build Default Service Account (Remediated)
* **Identity:** `${module.project.number}@cloudbuild.gserviceaccount.com`
* **Configuration Location:** Removed from [iam.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf)
* **Status:** **Remediated**
* **Remediation Details:** 
  > [!IMPORTANT]
  > All broad project-level permissions (`roles/editor` and `roles/resourcemanager.projectIamAdmin`) have been **removed** from this default service account to prevent unauthorized access or privilege escalation.

---

### C. Compute Engine Default Service Account (Remediated)
* **Identity:** `${module.project.number}-compute@developer.gserviceaccount.com`
* **Configuration Location:** [cloud_build.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_build.tf#L78-L84)
* **Status:** **Remediated**
* **Remediation Details:**
  > [!TIP]
  > The Cloud Build pipeline execution has been decoupled from the default Compute Engine service account. 
  > Triggers are now configured to run as `sa-terraform-provisioner` by default. This isolates Compute Engine workloads from build execution privileges.

---

### D. Cloud Build Service Agent
* **Identity:** `service-${module.project.number}@gcp-sa-cloudbuild.iam.gserviceaccount.com`
* **Configuration Location:** [iam.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf#L147-L164) and [iam.tf:L157-163](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf#L157-L163)
* **Assigned Roles:**
  * `roles/secretmanager.secretAccessor` (Project-level, restricted with IAM Conditions to GitHub credentials)
  * `roles/iam.serviceAccountUser` (On `sa-terraform-provisioner` to allow acting as the runner)
* **Status:** **Scoped and Scrutinized**
* **Security Evaluation:**
  > [!NOTE]
  > Highly secure. Access to secrets is restricted using precise Resource Name IAM Conditions. Access to the provisioner account is restricted exclusively to this agent.

---

## 2. Granular IAM Roles Mapping

Below is the mapping of provisioned resources to the specific granular IAM roles granted to the `sa-terraform-provisioner` service account:

| Category / Resource | Target File(s) | IAM Role Granted | Justification / Description |
|---|---|---|---|
| **Terraform State Storage** | [cloud_build.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_build.tf) | `roles/storage.admin` | Setup and manage the GCS bucket (`tf-state-v8`) for remote state storage. |
| **API Enablement** | [project.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/project.tf) | `roles/serviceusage.serviceUsageAdmin` | Dynamically enable required Google Cloud APIs. |
| **Networking & IPs** | [network.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/network.tf), [ips.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/ips.tf) | `roles/compute.networkAdmin` | Provision static IPs, VPC networks, and subnets. |
| **Load Balancers** | [load_balancer.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/load_balancer.tf) | `roles/compute.loadBalancerAdmin` | Configure backend buckets, URL maps, and proxy load balancers. |
| **Compute VMs** | [compute.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/compute.tf) | `roles/compute.instanceAdmin.v1` | Provision and manage standard and Spot virtual machines. |
| **Cloud Run** | [cloud_run.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_run.tf) | `roles/run.admin` | Deploy and configure Cloud Run services. |
| **Cloud Functions** | [cloud_function.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_function.tf) | `roles/cloudfunctions.admin` | Build and deploy Cloud Functions v2. |
| **Cloud SQL** | [sql.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/sql.tf) | `roles/cloudsql.admin` | Create and manage Cloud SQL database instances. |
| **Artifact Registry** | [artifact_registry.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/artifact_registry.tf) | `roles/artifactregistry.admin` | Setup and manage Docker repositories. |
| **Secret Manager** | [cloud_build.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/cloud_build.tf) | `roles/secretmanager.admin` | Manage application secrets. |
| **Certificate Manager** | [ssl.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/ssl.tf) | `roles/certificatemanager.owner` | Manage SSL certificates and certificate map configurations. |
| **IAM Policy Setup** | [iam.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf) | `roles/resourcemanager.projectIamAdmin` | Grant Cloud SQL Client and Artifact Registry Reader/Writer roles to runtime workloads. |
| **Service Account Usage** | [iam.tf](file:///mnt/c/Users/sinji/Downloads/tf/d3v/sapphire/erpnext_enhancements/infra/iam.tf) | `roles/iam.serviceAccountUser` | Assign workloads (VMs, Cloud Run, Cloud Functions) their runtime service accounts. |

---

## 3. Best Practices & Ongoing Security Measures

1. **Automatic IAM Grants Constraint:**
   Ensure the organization policy `constraints/iam.automaticIamGrantsForDefaultServiceAccounts` is active on the project. This stops GCP from automatically granting the Project Editor role to default service accounts upon activation.
2. **Workload Runtime Service Accounts:**
   Always assign specific, dedicated service accounts to workloads (e.g. VMs configured in `configs/compute_vm.yaml` and Cloud Run containers) instead of defaulting to standard Compute Engine default service accounts.
