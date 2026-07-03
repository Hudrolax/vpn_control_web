#!/bin/sh
# Deploy the vpnctl control plane to the corporate OpenWrt.
# Usage: ./deploy.sh [phase]   (phase: 1, 2 or 3; default keeps current)
#
# Run from the repo root on a machine that can reach the router via the
# Proxmox jump (see README). The script only copies files and sets the phase;
# it never touches cron, pbr or running services.

set -eu

JUMP="${OPENWRT_JUMP:-root@192.168.2.20}"
ROUTER="${OPENWRT_HOST:-root@192.168.253.112}"
PHASE="${1:-}"

HERE="$(cd "$(dirname "$0")" && pwd)"

run() { ssh -o BatchMode=yes "$JUMP" "ssh -o BatchMode=yes $ROUTER '$*'"; }
push() { # push <local> <remote>
  ssh -o BatchMode=yes "$JUMP" "ssh -o BatchMode=yes $ROUTER 'cat > $2'" < "$1"
}

echo "== creating directories"
run "mkdir -p /usr/lib/vpn-control /etc/vpn-control /var/lib/vpn-control"

echo "== pushing lib modules"
for f in common parser checker renderer worker; do
  push "$HERE/usr/lib/vpn-control/$f.sh" "/usr/lib/vpn-control/$f.sh"
done

echo "== pushing vpnctl + vpnctl-ssh"
push "$HERE/usr/libexec/vpnctl" /usr/libexec/vpnctl
push "$HERE/usr/libexec/vpnctl-ssh" /usr/libexec/vpnctl-ssh
run "chmod 755 /usr/libexec/vpnctl /usr/libexec/vpnctl-ssh"

echo "== pushing boot script"
push "$HERE/etc/init.d/vpn-control-boot" /etc/init.d/vpn-control-boot
run "chmod 755 /etc/init.d/vpn-control-boot && /etc/init.d/vpn-control-boot enable"

echo "== pushing config.json (only if absent, to keep local edits)"
if ! run "[ -s /etc/vpn-control/config.json ]"; then
  push "$HERE/etc/vpn-control/config.json" /etc/vpn-control/config.json
fi

if [ -n "$PHASE" ]; then
  echo "== setting phase to $PHASE"
  run "echo $PHASE > /etc/vpn-control/phase"
fi

echo "== smoke test"
run "/usr/libexec/vpnctl status" | head -c 400
echo ""
echo "done"
