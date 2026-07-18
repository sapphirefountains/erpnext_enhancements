# GCP Infrastructure — Sapphire Fountains ERPNext

Project-specific reference. For generic playbook → [README.md](../README.md). For day-to-day workflow → [WORKFLOW.md](../WORKFLOW.md).

---

## Topology

```
Cloudflare (DNS-only)
    │
    ├── erp.sapphirefountains.com  ──► 136.68.113.208
    │                                   └── production-glb (LB, us-east4)
    │                                       └── production-erpnext-standard-vm:80
    │                                           └── nginx → frappe-bench:8000
    │
    └── beta.erp.sapphirefountains.com  ──► 34.149.67.36
                                            └── spot-glb (LB, us-central1)
                                                └── test-erpnext-spot-vm:80
                                                    └── nginx → frappe-bench:8000
```

Both LBs: HTTPS termination at edge, HTTP→HTTPS redirect on port 80, Google-managed SSL certs, backend protocol HTTP.

---

## Quick Reference

| Item | Production | Spot/Test |
|------|-----------|-----------|
| LB name | `production-glb` | `spot-glb` |
| LB IP | `136.68.113.208` | `34.149.67.36` |
| Domain | `erp.sapphirefountains.com` | `beta.erp.sapphirefountains.com` |
| SSL cert | `production-glb-web-ssl-cert` (ACTIVE) | `spot-glb-web-ssl-cert` (ACTIVE) |
| Backend service | `production-glb-production-vm-backend` (HTTP:80) | `spot-glb-spot-vm-backend` (HTTP:80) |
| VM name | `production-erpnext-standard-vm` | `test-erpnext-spot-vm` |
| Zone | `us-east4-a` | `us-central1-a` |
| Machine type | `n2d-standard-8` (8 vCPU, 32 GB) | `n2d-standard-2` (2 vCPU, 8 GB) |
| Boot disk | 50 GB (attached from `prod-erpnext-boot-east4`) | 50 GB (restored from snapshot) |
| Data disk | 200 GB (attached from `prod-erpnext-data-east4`) | 200 GB (restored from snapshot) |
| Provisioning | Standard | SPOT (preemptible, delete on term) |
| Health | HEALTHY | HEALTHY |

---

## Load Balancer Details

### Frontends

| LB | Port | Target | Purpose |
|----|------|--------|---------|
| production-glb | 443 | target-https-proxy → url-map → backend | HTTPS traffic |
| production-glb | 80 | target-http-proxy → redirect url-map | 301 → HTTPS |
| spot-glb | 443 | target-https-proxy → url-map → backend | HTTPS traffic |
| spot-glb | 80 | target-http-proxy → redirect url-map | 301 → HTTPS |

### Backends

| Backend service | Protocol | Port | Health check | Instance group |
|----------------|----------|------|-------------|----------------|
| `production-glb-production-vm-backend` | HTTP | 80 | HTTP `/` (10s) | `production-erpnext-standard-vm` |
| `spot-glb-spot-vm-backend` | HTTP | 80 | HTTP `/` (10s) | `test-erpnext-spot-vm` |

### SSL

Management is handled by `ssl_certificates.managed_configs` in `configs/load_balancer.yaml`. No Certificate Manager.

---

## DNS (Cloudflare)

| Record | Type | Value | Proxy |
|--------|------|-------|-------|
| `erp.sapphirefountains.com` | A | `136.68.113.208` | DNS-only |
| `beta.erp.sapphirefountains.com` | A | `34.149.67.36` | DNS-only |

Cloudflare is DNS-only (gray cloud). Traffic goes directly to GCP LBs. No origin pull certificates needed.

---

## SSH Access

```bash
# Production
gcloud compute ssh production-erpnext-standard-vm \
  --zone=us-east4-a --project=erpnext-465317 --tunnel-through-iap

# Spot/Test
gcloud compute ssh test-erpnext-spot-vm \
  --zone=us-central1-a --project=erpnext-465317 --tunnel-through-iap
```

Requires IAP tunnel access (`iap_tunnel_members` in `shared.tfvars`).

---

## Health Checks & Diagnostics

```bash
# Backend health
gcloud compute backend-services get-health production-glb-production-vm-backend --global
gcloud compute backend-services get-health spot-glb-spot-vm-backend --global

# SSL cert status
gcloud compute ssl-certificates list --global

# Test end-to-end (bypass local DNS)
curl -sI --resolve erp.sapphirefountains.com:443:136.68.113.208 https://erp.sapphirefountains.com/ | head -5
curl -sI --resolve beta.erp.sapphirefountains.com:443:34.149.67.36 https://beta.erp.sapphirefountains.com/ | head -5
```

---

## Firewall

| Rule | Source | Ports | Target |
|------|--------|-------|--------|
| `allow-lb-to-production-vm` | `130.211.0.0/22`, `35.191.0.0/16` | 80, 443, 8000 | tag `web-frontend` |
| `allow-iap-ssh-to-vms` | `35.235.240.0/20` | 22 | all instances |

---

## Known Quirks

- **This WSL machine** has `127.0.0.1 erp.sapphirefountains.com` in the Windows hosts file — remove it to access prod
- **Backend protocol** must be `HTTP` (not `HTTPS`). The LB terminates TLS at the edge, sends plain HTTP to the VM
- **Spot VM snapshots** daily at 02:00 UTC, 7-day retention. Restore by tainting disk resources in test state
- **State lock** can be forced: `terraform force-unlock <LOCK_ID>`
