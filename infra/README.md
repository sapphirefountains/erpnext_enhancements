# Service Provisioning Playbook

This playbook is a reusable, modular Terraform configuration designed to provision various Google Cloud Platform (GCP) services dynamically. The playbook supports toggling services on/off via `terraform.tfvars`, and handles network configurations dynamically using a global public/private toggling variable (`ip_external`).

## Architecture & Design Principles

The playbook is built on top of Google Cloud Foundation Fabric (CFF) modules. It features:
* **Independent Enablement:** Every resource group is wrapped in conditional statements driven by `provision_*` toggles.
* **Unified Network Toggling:** The `ip_external` variable acts as a master toggle to switch resources between public-facing configurations (assigning public IPs, setting open ingress rules) and private-only configurations.
* **Parameterizable Settings:** All configurable fields are mapped to variables that can be modified directly in the `terraform.tfvars` file, removing any hardcoded environment details.

## Detailed Service Configurations

### 1. Project Management
Manages Google Cloud Project creation or reuse via the `create_project` variable. 
* Enables required APIs dynamically based on the enabled provisioning toggles.
* Connects the project to a billing account if creating a new project.

### 2. IP Addresses (Static IP Allocations)
Allocates regional static IP addresses and global IP addresses.
* If `ip_external` is set to `true`, regional static IP addresses are provisioned as external (public) IPs.
* If `ip_external` is set to `false`, regional static IP addresses are provisioned as internal (private) IPs.

### 3. Managed SSL & Certificate Manager
Provisions managed and self-managed SSL certificates and maps them to a Certificate Map.
* Connects seamlessly with the Application Load Balancer to secure client traffic.

### 4. Cloud Run Services
Deploys Cloud Run (v2) services with customizable revision templates, environment variables, and secrets.
* Ingress settings are set dynamically: `INGRESS_TRAFFIC_ALL` if `ip_external` is true, and `INGRESS_TRAFFIC_INTERNAL_ONLY` if it is false (which can be overridden in `service_config`).

### 5. Cloud Functions (v2)
Provisions v2 Cloud Functions, including custom runtime configuration, memory, bucket sources, and IAM.
* Ingress settings default to `ALLOW_ALL` if `ip_external` is true, and `ALLOW_INTERNAL_ONLY` if false.

### 6. Standard Compute VMs
Provisions standard Compute Engine virtual machines.
* If `ip_external` is set to `true`, the instance's network interfaces are assigned an ephemeral external IP (`nat = true`).
* If `ip_external` is set to `false`, they are provisioned with internal-only access.

### 7. Spot Compute VMs
Provisions ephemeral, cost-efficient Spot VMs.
* Integrates the same `ip_external` public/private IP toggle logic as standard VMs.
* Supports customizable termination actions (`STOP` or `DELETE`).

### 8. Cloud SQL Database Instances
Deploys Cloud SQL database instances dynamically using the `cloudsql-instance` module.
* If `ip_external` is set to `true`, the instance is configured with public IP access (`public_ipv4 = true`).
* If `ip_external` is set to `false`, the instance is configured with private IP access via Private Services Access (PSA) on the specified VPC network.

### 9. External Application Load Balancers
Provisions Global External Application Load Balancers (HTTP/HTTPS) with custom URL maps, backend buckets, Network Endpoint Groups (NEGs), and SSL certificate map bindings.

### 10. CI/CD Cloud Build Triggers
Deploys Cloud Build (v2) connections and triggers securely, utilizing Google Secret Manager to store GitHub Personal Access Tokens (PATs) for repository mirroring and validation.

### 11. Artifact Registry
Provisions Google Cloud Artifact Registry Docker repositories dynamically using the `artifact-registry` module.
* Dynamically parses configurations from `configs/artifact_registry.yaml` to create repositories.
* Grants the `roles/artifactregistry.reader` role on the repository to the Cloud Run Service Agent (`service-${project_number}@serverless-robot-prod.iam.gserviceaccount.com`), enabling Cloud Run to fetch container images securely.

---

## Deployment & Usage

### 1. Customize Variables
Set up your configurations in [terraform.tfvars](terraform.tfvars). Make sure to replace all `<PLACEHOLDERS>` (e.g. `<YOUR_GCP_PROJECT_ID>`, `<YOUR_PREFERRED_REGION>`) with actual target values.

### 2. Initialize Terraform
Run `terraform init` to download required providers (e.g., `google`, `google-beta`, `random`) and load the local fabric modules:
```bash
terraform init
```

### 3. Validate Configuration
Ensure the configurations are syntactically correct and satisfy module requirements:
```bash
terraform validate
```

### 4. Apply Configuration
Run `terraform plan` and `terraform apply` to provision the resources:
```bash
terraform plan
terraform apply
```

---

## Detailed Guide to Enabling and Configuring Services

Each service in this playbook can be toggled independently via boolean variables in `terraform.tfvars`, and its specific options can be tailored via YAML files in the `configs/` directory.

### Enabling Services
Modify [terraform.tfvars](terraform.tfvars) to set the following toggles to `true` or `false`:

| Toggle Variable | Target Service | Configuration File | Key Features Enabled |
|---|---|---|---|
| `provision_artifact_registry` | Artifact Registry | [configs/artifact_registry.yaml](configs/artifact_registry.yaml) | Docker repositories, automated reader IAM roles for Cloud Run |
| `provision_ips` | IP Addresses | [configs/ips.yaml](configs/ips.yaml) | Static public/private IP addresses allocation |
| `provision_ssl` | SSL Certificate Manager | [configs/ssl.yaml](configs/ssl.yaml) | SSL Certificates and Maps |
| `provision_cloud_run` | Cloud Run Services | [configs/cloud_run.yaml](configs/cloud_run.yaml) | Serverless containers, ingress routing, env vars |
| `provision_cloud_function` | Cloud Functions (v2) | [configs/cloud_function.yaml](configs/cloud_function.yaml) | Event-driven code deployment from Cloud Storage |
| `provision_compute_vm` | Standard Compute VMs | [configs/compute_vm.yaml](configs/compute_vm.yaml) | Compute Engine VMs, startup scripts, network interfaces |
| `provision_spot_vm` | Spot VMs | [configs/spot_vm.yaml](configs/spot_vm.yaml) | Cost-effective VM instances with termination actions |
| `provision_sql` | Cloud SQL Instances | [configs/sql.yaml](configs/sql.yaml) | Managed databases, Private Services Access (PSA) |
| `provision_load_balancer` | Application Load Balancer | [configs/load_balancer.yaml](configs/load_balancer.yaml) | Global HTTP/HTTPS Load Balancer, routing rules, backend groups |
| `provision_cloud_build` | Cloud Build CI/CD | [configs/cloud_build.yaml](configs/cloud_build.yaml) | GitHub repository mirroring, webhook validation triggers |

---

### Step-by-Step Scenario: Building, Pushing, and Running a Container Image with Artifact Registry and Cloud Run

To configure Cloud Run to run an image built and stored in your own project's Artifact Registry, follow these steps:

#### 1. Enable Artifact Registry
In `terraform.tfvars`, set the provisioning toggle:
```hcl
provision_artifact_registry = true
```
Ensure your repository configuration in `configs/artifact_registry.yaml` contains:
```yaml
app-images:
  format:
    docker:
      standard: {}
  description: "Docker repository for Cloud Run images"
```

#### 2. Run Terraform Apply
Deploy the project and the registry repository first:
```bash
terraform apply
```

#### 3. Build & Push Your Container Image
Authenticate your local Docker client to your Artifact Registry:
```bash
gcloud auth configure-docker <REGION>-docker.pkg.dev
```
Build your container image:
```bash
docker build -t <REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1 .
```
Push the container image:
```bash
docker push <REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1
```

#### 4. Configure Cloud Run to Pull from the Registry
In `terraform.tfvars`, enable Cloud Run and specify the container image path pointing to your newly created registry:
```hcl
provision_cloud_run = true
cloud_run_image     = "<REGION>-docker.pkg.dev/<PROJECT_ID>/app-images/my-app:v1"
```
During deployment, the playbook automatically:
1. Provisions the registry `app-images`.
2. Creates the Cloud Run service identity.
3. Grants `roles/artifactregistry.reader` permission on the registry to the Cloud Run service identity (`service-${project_number}@serverless-robot-prod.iam.gserviceaccount.com`).
4. Provisions the Cloud Run service pulling your image.

Run the final apply to deploy the Cloud Run service:
```bash
terraform apply
```

---
<!-- BEGIN TFDOC -->
## Variables

| name | description | type | required | default |
|---|---|:---:|:---:|:---:|
| [project_id](variables.tf#L107) | The ID of the project to create or reuse. | <code>string</code> | ✓ |  |
| [api_url](variables.tf#L17) | The API URL used by the frontend service container. | <code>string</code> |  | <code>&#34;https:&#47;&#47;api.example.com&#34;</code> |
| [billing_account_id](variables.tf#L23) | The billing account ID to associate with the created project. | <code>string</code> |  | <code>null</code> |
| [cloud_build_connection](variables.tf#L29) | The name of the Cloud Build connection. | <code>string</code> |  | <code>&#34;github-pipeline-connection&#34;</code> |
| [cloud_build_github_token](variables.tf#L35) | The GitHub Personal Access Token (PAT) for Cloud Build connection. | <code>string</code> |  | <code>&#34;ghp_1234567890abcdefghijklmnopqrstuvwxyz&#34;</code> |
| [cloud_build_installation_id](variables.tf#L41) | The GitHub App installation ID on the repo. | <code>number</code> |  | <code>12345678</code> |
| [cloud_build_repo_uri](variables.tf#L47) | The remote URI of the repository for Cloud Build connection. | <code>string</code> |  | <code>&#34;https:&#47;&#47;github.com&#47;example-org&#47;example-repo.git&#34;</code> |
| [cloud_function_bucket](variables.tf#L53) | Bucket name where Cloud Function source archives are uploaded. | <code>string</code> |  | <code>&#34;demo-function-deploy-bucket&#34;</code> |
| [cloud_run_image](variables.tf#L59) | The container image to deploy to Cloud Run. | <code>string</code> |  | <code>&#34;us-docker.pkg.dev&#47;cloudrun&#47;container&#47;hello&#34;</code> |
| [compute_machine_type](variables.tf#L65) | The machine type for standard Compute Engine VM instances. | <code>string</code> |  | <code>&#34;e2-medium&#34;</code> |
| [create_project](variables.tf#L71) | Whether to create a new project or reuse an existing one. | <code>bool</code> |  | <code>true</code> |
| [domain_name](variables.tf#L77) | The domain name for the managed SSL certificate. | <code>string</code> |  | <code>&#34;app.example.com&#34;</code> |
| [glb_ip_name](variables.tf#L83) | The name of the global external IP address for the load balancer. | <code>string</code> |  | <code>&#34;glb-ip&#34;</code> |
| [ip_external](variables.tf#L89) | Toggle static IPs, Cloud SQL, VMs, and Cloud Run to be external (true) or internal (false). | <code>bool</code> |  | <code>false</code> |
| [network](variables.tf#L95) | The VPC network to deploy resources into. | <code>string</code> |  | <code>&#34;default&#34;</code> |
| [prefix](variables.tf#L101) | An optional prefix applied to created resources. | <code>string</code> |  | <code>null</code> |
| [provision_artifact_registry](variables.tf#L112) | Toggle to enable/disable Artifact Registry setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_build](variables.tf#L118) | Toggle to enable/disable Cloud Build setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_function](variables.tf#L124) | Toggle to enable/disable Cloud Function setup. | <code>bool</code> |  | <code>false</code> |
| [provision_cloud_run](variables.tf#L130) | Toggle to enable/disable Cloud Run setup. | <code>bool</code> |  | <code>false</code> |
| [provision_compute_vm](variables.tf#L136) | Toggle to enable/disable standard Compute Engine VM setup. | <code>bool</code> |  | <code>false</code> |
| [provision_iam](variables.tf#L142) | Toggle to enable/disable IAM permissions setup. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_artifact_registry](variables.tf#L148) | Toggle to enable/disable Artifact Registry IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_cloud_build](variables.tf#L154) | Toggle to enable/disable Cloud Build IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_secret_manager](variables.tf#L160) | Toggle to enable/disable Secret Manager IAM permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_iam_sql](variables.tf#L166) | Toggle to enable/disable Cloud SQL IAM client permissions. | <code>bool</code> |  | <code>true</code> |
| [provision_ips](variables.tf#L172) | Toggle to enable/disable static IP setup. | <code>bool</code> |  | <code>false</code> |
| [provision_load_balancer](variables.tf#L178) | Toggle to enable/disable Load Balancer setup. | <code>bool</code> |  | <code>false</code> |
| [provision_spot_vm](variables.tf#L184) | Toggle to enable/disable Spot VM setup. | <code>bool</code> |  | <code>false</code> |
| [provision_sql](variables.tf#L190) | Toggle to enable/disable Cloud SQL database setup. | <code>bool</code> |  | <code>false</code> |
| [provision_ssl](variables.tf#L196) | Toggle to enable/disable Managed SSL setup. | <code>bool</code> |  | <code>false</code> |
| [region](variables.tf#L202) | The default GCP region to deploy regional resources. | <code>string</code> |  | <code>&#34;us-central1&#34;</code> |
| [spot_machine_type](variables.tf#L208) | The machine type for Spot VM instances. | <code>string</code> |  | <code>&#34;n2-standard-4&#34;</code> |
| [sql_db_version](variables.tf#L214) | The database version for Cloud SQL (e.g. POSTGRES_15). | <code>string</code> |  | <code>&#34;POSTGRES_15&#34;</code> |
| [sql_tier](variables.tf#L220) | The machine tier for the Cloud SQL instance. | <code>string</code> |  | <code>&#34;db-f1-micro&#34;</code> |
| [ssl_cert_name](variables.tf#L226) | The name of the SSL certificate resource. | <code>string</code> |  | <code>&#34;web-ssl-cert&#34;</code> |
| [ssl_map_name](variables.tf#L232) | The name of the Certificate Map. | <code>string</code> |  | <code>&#34;web-ssl-map&#34;</code> |
| [subnetwork](variables.tf#L238) | The subnetwork to deploy resources into. | <code>string</code> |  | <code>&#34;default&#34;</code> |
| [web_ip_name](variables.tf#L244) | The name of the regional external/internal static IP address. | <code>string</code> |  | <code>&#34;web-ip&#34;</code> |

## Outputs

| name | description | sensitive |
|---|---|:---:|
| [artifact_registries](outputs.tf#L17) | The outputs of the provisioned Artifact Registry repositories. |  |
| [cloud_functions](outputs.tf#L23) | The outputs of the provisioned Cloud Functions (v2). |  |
| [cloud_run_services](outputs.tf#L29) | The outputs of the provisioned Cloud Run services (v2). |  |
| [compute_vms](outputs.tf#L35) | The outputs of standard Compute VMs. |  |
| [ips](outputs.tf#L41) | The outputs of the provisioned IP addresses. |  |
| [load_balancers](outputs.tf#L47) | The outputs of the provisioned Load Balancers. |  |
| [project_id](outputs.tf#L53) | The GCP Project ID where resources were provisioned. |  |
| [spot_vms](outputs.tf#L59) | The outputs of provisioned Spot VMs. |  |
| [sql_instances](outputs.tf#L65) | The outputs of the provisioned Cloud SQL database instances. | ✓ |
| [ssl_certificates](outputs.tf#L72) | The outputs of the SSL Certificates Manager configuration. |  |
<!-- END TFDOC -->
