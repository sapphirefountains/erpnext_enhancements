#!/bin/bash
# shellcheck disable=SC2317
exec > /var/log/startup-script.log 2>&1
echo "[$(date)] Startup script started"

# ── Is this a first-boot or a reboot? ────────────────────
if command -v bench &>/dev/null; then
  echo "[$(date)] Bench already installed — reboot path"
  SKIP_FIRST_BOOT=true
else
  SKIP_FIRST_BOOT=false
fi

# ── Mount data disk ──────────────────────────────────────
# Identify the root disk from the kernel device name
RDEV=$(findmnt -n -o SOURCE /)             # e.g. /dev/sdb1
ROOT_PKNAME=$(lsblk -ndo PKNAME "$RDEV")  # e.g. sdb

# Find the unmounted disk that is NOT the root disk
DATA_DEV=$(lsblk -dpno NAME,TYPE,MOUNTPOINT | awk -v root="/dev/$ROOT_PKNAME" '$2=="disk" && $3=="" && $1!=root {print $1; exit}')

# If none unmounted, try any non-root disk with ext4
if [ -z "$DATA_DEV" ]; then
  for dev in /dev/sd* /dev/nvme*n*; do
    [ -b "$dev" ] || continue
    [ "$dev" = "/dev/$ROOT_PKNAME" ] && continue
    if blkid "$dev" 2>/dev/null | grep -q ext4 && ! mount | grep -q "$dev"; then
      DATA_DEV=$dev
      break
    fi
  done
fi

# Try fstab first (if it has a UUID entry it may already be correct)
mount -a 2>&1 || true

if [ -n "$DATA_DEV" ] && ! mount | grep -q "$DATA_DEV"; then
  echo "[$(date)] Data disk $DATA_DEV not mounted — mounting"
  blkid "$DATA_DEV" | grep -q ext4 || mkfs.ext4 -F "$DATA_DEV"

  DATA_UUID=$(blkid -s UUID -o value "$DATA_DEV")
  # Try common mount points
  if [ -d /data ] && [ ! -L /data ]; then
    mount UUID="$DATA_UUID" /data || true
  elif [ -d /mnt/data ]; then
    mount UUID="$DATA_UUID" /mnt/data || true
  else
    mkdir -p /mnt/data
    mount UUID="$DATA_UUID" /mnt/data || true
  fi

  # Add to fstab with UUID (remove any stale device-name entry for the same disk first)
  sed -i "\|$DATA_DEV|d" /etc/fstab
  grep -q "$DATA_UUID" /etc/fstab || echo "UUID=$DATA_UUID /data ext4 defaults,nofail 0 2" >> /etc/fstab
fi

if [ -z "$DATA_DEV" ]; then
  echo "[$(date)] WARNING: Could not find a data disk to mount"
fi

# ── Create /data symlink if data disk is at /mnt/data ────
if mountpoint -q /mnt/data && [ ! -e /data ]; then
  ln -sf /mnt/data /data
fi

# ── First-boot: system packages & bench install ──────────
if [ "$SKIP_FIRST_BOOT" = false ]; then
  echo "[$(date)] First boot — installing packages"

  apt-get update -qq
  apt-get install -y -qq ${packages}

  id -u frappe &>/dev/null || useradd -m -s /bin/bash frappe
  sudo -u frappe pipx install --system-site-packages frappe-bench
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/frappe/.bashrc

  # Add PATH to root too for safe sudo
  echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/frappe/.profile
fi

# ── Start MariaDB ─────────────────────────────────────────
if command -v mysqld &>/dev/null; then
  echo "[$(date)] Starting MariaDB"
  systemctl enable mariadb 2>/dev/null || true
  systemctl start mariadb 2>/dev/null || true
fi

# ── Bench processes (systemd) ─────────────────────────────
# frappe-bench.service is the bench's handle, and a deploy restarts it by that name. On a
# VM cut over to the production units (infra/docs/production-web-tier.md) it is an
# umbrella over gunicorn, socketio, the workers, the scheduler and both Redis instances,
# and the units are refreshed from the app checkout on every boot, so the repo stays the
# source of truth; a refreshed definition applies at the next restart. A VM still on the
# old `honcho start` unit -- whose Procfile runs `bench serve`, the Werkzeug development
# server -- is left on it: a cutover is a watched step, not something a reboot should do.
# A VM with no unit at all gets the production units, never the development server.
# (No brace-style shell variables here: this file is a Terraform templatefile.)
UNITS_INSTALLER=/home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/install_bench_units.sh
UNITS_MARKER="# erpnext_enhancements: production process units"
if [ -f /etc/systemd/system/frappe-bench.service ] && grep -qF "$UNITS_MARKER" /etc/systemd/system/frappe-bench.service; then
  echo "[$(date)] Refreshing the production bench units"
  bash "$UNITS_INSTALLER" || echo "[$(date)] WARNING: refreshing the bench units failed; the installed ones stay"
elif [ -f /etc/systemd/system/frappe-bench.service ]; then
  echo "[$(date)] frappe-bench.service still runs the development Procfile; see infra/docs/production-web-tier.md"
elif [ -f "$UNITS_INSTALLER" ]; then
  echo "[$(date)] frappe-bench.service not found — installing the production units"
  bash "$UNITS_INSTALLER" || echo "[$(date)] WARNING: could not install the production bench units"
fi
if [ -f /etc/systemd/system/frappe-bench.service ]; then
  echo "[$(date)] Enabling and starting frappe-bench service"
  systemctl enable frappe-bench 2>/dev/null || true
  systemctl start frappe-bench 2>/dev/null || true
fi

# ── Ensure deploy user exists for CI/CD SSH access ──────
id ${deploy_user} &>/dev/null || useradd -m -s /bin/bash ${deploy_user}
echo "${deploy_user} ALL=(ALL) NOPASSWD: ${deploy_user_sudo_command}" \
  > /etc/sudoers.d/${deploy_user}

# ── Trust the load balancer for the client address ───────
# GCLB -> nginx -> bench. Without this file nginx sees a Google front end
# (35.191.x) as every visitor, frappe records that as request_ip, and every
# IP-keyed rate limit becomes one global bucket (TASK-2026-01478). Installed on
# every boot from the app checkout, so the repo stays the source of truth and a
# rebuilt VM gets it back. A candidate that fails `nginx -t` is rolled back
# rather than left to stop nginx. It lives in conf.d, not in bench's own
# nginx.conf, because `bench setup nginx` regenerates that file. The reload
# below picks it up.
# (No brace-style shell variables here: this file is a Terraform templatefile.)
REALIP_SRC=/home/frappe/frappe-bench/apps/erpnext_enhancements/infra/configs/nginx-realip.conf
REALIP_DST=/etc/nginx/conf.d/00-realip.conf
if [ -f "$REALIP_SRC" ] && command -v nginx &>/dev/null; then
  rm -f "$REALIP_DST.bak"
  [ -f "$REALIP_DST" ] && cp -p "$REALIP_DST" "$REALIP_DST.bak"
  install -m 0644 "$REALIP_SRC" "$REALIP_DST"
  if nginx -t; then
    echo "[$(date)] Installed $REALIP_DST"
    rm -f "$REALIP_DST.bak"
  else
    echo "[$(date)] WARNING: $REALIP_DST failed nginx -t; restoring the previous copy"
    if [ -f "$REALIP_DST.bak" ]; then mv -f "$REALIP_DST.bak" "$REALIP_DST"; else rm -f "$REALIP_DST"; fi
  fi
fi

# ── nginx server block for the bench ─────────────────────
# bench's generated block serves /assets and /files from disk and proxies the rest to
# 127.0.0.1:8000. gunicorn serves neither path (`bench serve` did), so without it the
# Desk loads with no CSS or JS. It is generated only on a VM that has no frappe block
# yet: an existing one is never regenerated, so whatever was added to it by hand stays.
# This replaces `bench setup production frappe --yes`, which, whenever it got past its
# prerequisites, also installed supervisor and had it start a second copy of every bench
# process beside the systemd units.
if [ -d /home/frappe/frappe-bench ] && command -v nginx &>/dev/null; then
  if ! grep -rqsE '127\.0\.0\.1:8000|@webserver' /etc/nginx/conf.d /etc/nginx/sites-enabled; then
    echo "[$(date)] No frappe server block in nginx — generating one"
    sudo -u frappe PATH="/home/frappe/.local/bin:$PATH" bash -c 'cd /home/frappe/frappe-bench && bench setup nginx --yes' \
      && ln -sfn /home/frappe/frappe-bench/config/nginx.conf /etc/nginx/conf.d/frappe-bench.conf \
      && rm -f /etc/nginx/sites-enabled/default
  fi
  if nginx -t; then
    systemctl reload nginx 2>/dev/null || true
  else
    echo "[$(date)] WARNING: nginx -t failed; nginx was not reloaded"
  fi
fi

echo "[$(date)] Startup script completed"
