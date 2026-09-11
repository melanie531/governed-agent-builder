#!/bin/bash
set -eu
umask 077
# No shell tracing: authorization callbacks and tokens never enter bootstrap logs.
dnf install -y python3.12 nginx
id studio >/dev/null 2>&1 || useradd --system --home-dir /opt/studio --shell /sbin/nologin studio
install -d -m 0755 /opt/studio
install -d -m 0700 /etc/studio
aws s3 cp 's3://${ArtifactBucket}/${ArtifactKey}' /opt/studio/release.tgz --region '${AWS::Region}' --only-show-errors
printf '%s  %s\n' '${ArtifactSha256}' /opt/studio/release.tgz | sha256sum -c -
tar -xzf /opt/studio/release.tgz -C /opt/studio
python3.12 -m venv /opt/studio/venv
/opt/studio/venv/bin/pip install --no-index --find-links=/opt/studio/wheels --require-hashes -r /opt/studio/requirements.txt
# NVMe serial is the immutable EBS volume ID without its hyphen; never format
# an arbitrary secondary disk. Wait for CloudFormation's attachment separately.
volume='${DataVolumeId}'
serial=$(printf '%s' "$volume" | tr -d '-')
device=''
for attempt in $(seq 1 120); do
  for candidate in /sys/block/nvme*n1; do
    if [ "$(tr -d ' ' < "$candidate/device/serial")" = "$serial" ]; then device="/dev/$(basename "$candidate")"; fi
  done
  [ -n "$device" ] && break
  sleep 5
done
[ -n "$device" ] || exit 1
fstype=$(blkid -o value -s TYPE "$device" || true)
if [ -z "$fstype" ]; then
  # Refuse unknown signatures; only an actually empty newly provisioned volume.
  [ -z "$(wipefs --noheadings --output TYPE "$device")" ] || exit 1
  mkfs.ext4 -q "$device"
else
  [ "$fstype" = ext4 ] || exit 1
fi
install -d -m 0700 /data
uuid=$(blkid -o value -s UUID "$device")
grep -q "UUID=$uuid /data " /etc/fstab || printf 'UUID=%s /data ext4 defaults,nofail 0 2\n' "$uuid" >> /etc/fstab
mountpoint -q /data || mount /data
chown studio:studio /data
chmod 0700 /data
install -d -o studio -g studio -m 0700 /data/backups
# Root-owned application code; only data and backups are writable by runtime.
chown -R root:root /opt/studio
chmod -R a+rX /opt/studio
cat > /etc/systemd/system/studio.service <<'UNIT'
[Unit]
Description=Governed Agent Builder fixture preview
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/data
ConditionPathExists=/etc/studio/runtime.env
[Service]
User=studio
Group=studio
WorkingDirectory=/opt/studio
EnvironmentFile=/etc/studio/runtime.env
ExecStartPre=/usr/bin/mountpoint -q /data
ExecStart=/usr/bin/flock --nonblock /data/instance.lock /opt/studio/venv/bin/python -m backend.hosted
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/data
# Disable request logs; app does not log token exchange bodies.
StandardOutput=journal
StandardError=journal
[Install]
WantedBy=multi-user.target
UNIT
cat > /etc/systemd/system/studio-backup.service <<'UNIT'
[Unit]
Description=SQLite online encrypted-volume backup
RequiresMountsFor=/data
ConditionPathExists=/data/state.sqlite
[Service]
Type=oneshot
User=studio
Group=studio
UMask=0077
ExecStart=/opt/studio/venv/bin/python /opt/studio/infra/backup.py
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/data
UNIT
cat > /etc/systemd/system/studio-backup.timer <<'UNIT'
[Unit]
Description=Daily fixture database backup; seven local copies
[Timer]
OnCalendar=daily
Persistent=true
[Install]
WantedBy=timers.target
UNIT
# Fail closed until the exact CloudFront origin and new Cognito outputs exist.
# Remove default nginx listener by replacing its package-installed initial file,
# on this new dedicated instance only. Subsequent updates preserve this contract.
cat > /etc/nginx/nginx.conf <<'NGINX'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log crit;
pid /run/nginx.pid;
events { worker_connections 256; }
http {
  access_log off;
  server_tokens off;
  include /etc/nginx/conf.d/*.conf;
}
NGINX
cat > /etc/nginx/conf.d/studio.conf <<'NGINX'
server { listen 80 default_server; server_name _; return 503; }
NGINX
nginx -t
systemctl daemon-reload
systemctl enable --now nginx studio-backup.timer
systemctl enable studio
printf 'BOOTSTRAP_READY\n'
