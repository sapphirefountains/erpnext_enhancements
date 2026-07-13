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
# Try fstab first (both VMs should have an entry)
mount -a 2>&1 || true

# If nothing mounted yet, auto-detect and mount
DATA_DEV=$(lsblk -dpno NAME | grep -E 'sd[b-z]|nvme[0-9]n[0-9]' | head -1)
if [ -n "$DATA_DEV" ] && ! mount | grep -q "$DATA_DEV"; then
  echo "[$(date)] Data disk $DATA_DEV not mounted — mounting"
  blkid "$DATA_DEV" | grep -q ext4 || mkfs.ext4 -F "$DATA_DEV"

  # Try common mount points
  if [ -d /data ] && [ ! -L /data ]; then
    mount "$DATA_DEV" /data || true
  elif [ -d /mnt/data ]; then
    mount "$DATA_DEV" /mnt/data || true
  else
    mkdir -p /mnt/data
    mount "$DATA_DEV" /mnt/data || true
  fi

  # Add to fstab if not present
  grep -q "$DATA_DEV" /etc/fstab || echo "$DATA_DEV /data ext4 defaults,nofail 0 2" >> /etc/fstab
fi

# ── Create /data symlink if data disk is at /mnt/data ────
if mountpoint -q /mnt/data && [ ! -e /data ]; then
  ln -sf /mnt/data /data
fi

# ── First-boot: system packages & bench install ──────────
if [ "$SKIP_FIRST_BOOT" = false ]; then
  echo "[$(date)] First boot — installing packages"

  apt-get update -qq
  apt-get install -y -qq curl git nginx python3 python3-pip python3-venv pipx

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

# ── Enable & start frappe-bench systemd service ───────────
if [ -f /etc/systemd/system/frappe-bench.service ]; then
  echo "[$(date)] Enabling and starting frappe-bench service"
  systemctl enable frappe-bench 2>/dev/null || true
  systemctl start frappe-bench 2>/dev/null || true
elif [ -d /home/frappe/frappe-bench ]; then
  echo "[$(date)] frappe-bench.service not found — creating"
  cat > /etc/systemd/system/frappe-bench.service << 'SERVICEEOF'
[Unit]
Description=Frappe Bench
After=network.target mariadb.service
Wants=mariadb.service

[Service]
Type=simple
User=frappe
Group=frappe
WorkingDirectory=/home/frappe/frappe-bench
Environment=PATH=/home/frappe/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=/usr/local/bin/honcho start
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF
  systemctl daemon-reload
  systemctl enable frappe-bench
  systemctl start frappe-bench
fi

# ── Setup nginx if bench exists ──────────────────────────
if [ -d /home/frappe/frappe-bench ]; then
  cd /home/frappe/frappe-bench
  sudo -u frappe bench setup production frappe --yes || true
fi

echo "[$(date)] Startup script completed"
