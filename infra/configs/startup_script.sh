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
  sudo -u frappe PATH="/home/frappe/.local/bin:$PATH" bash -c 'cd /home/frappe/frappe-bench && bench setup production frappe --yes' || true
fi

echo "[$(date)] Startup script completed"
