# shellcheck shell=sh
# Common helpers for vpnctl. Sourced by /usr/libexec/vpnctl and lib modules.
# Target: OpenWrt busybox sh + jq.

CONF_DIR="/etc/vpn-control"
CONFIG_FILE="$CONF_DIR/config.json"
STATE_FILE="$CONF_DIR/state.json"
SUB_URL_FILE="$CONF_DIR/subscription.url"
SUB_CACHE="$CONF_DIR/subscription.cache"
SUB_STAMP="$CONF_DIR/subscription.stamp"

LIB_DIR="/var/lib/vpn-control"
SERVERS_FILE="$LIB_DIR/servers.json"
OUTBOUNDS_FILE="$LIB_DIR/outbounds.json"
CHECKS_FILE="$LIB_DIR/checks.json"
RUNTIME_FILE="$LIB_DIR/runtime.json"
OPLOG_FILE="$LIB_DIR/log.jsonl"
LOCK_FILE="/var/lock/vpn-control.lock"
TMP_DIR="/tmp/vpn-control"

# Legacy files from the Codex-era vpn-subscription-manager (read-only fallback
# until phase-3 cutover).
LEGACY_WORK_DIR="/etc/sing-box/subscription"
LEGACY_TAG_FILE="$LEGACY_WORK_DIR/current-tag"
LEGACY_STAMP_FILE="$LEGACY_WORK_DIR/last-update"
LEGACY_OUTBOUNDS="$LEGACY_WORK_DIR/outbounds.json"

XRAY_BIN="/usr/bin/xray"
XRAY_CONFIG="/var/etc/xray-subscription-client.json"
XRAY_SERVICE="xray-subscription-client"
SINGBOX_SERVICE="singbox-sb-tun"

ACTOR="${VPNCTL_ACTOR:-cli}"

ensure_dirs() {
  mkdir -p "$CONF_DIR" "$LIB_DIR" "$TMP_DIR" /var/lock /var/etc
  chmod 700 "$CONF_DIR" "$TMP_DIR"
}

now() { date +%s; }

cfg() {
  # cfg <jq-path> <default>
  jq -r "$1 // \"$2\"" "$CONFIG_FILE" 2>/dev/null || echo "$2"
}

fail() {
  # fail <code> <message>
  jq -cn --arg e "$1" --arg m "$2" '{ok: false, error: $e, message: $m}'
  exit 1
}

atomic_write() {
  # atomic_write <target> <mode>; content on stdin
  _tgt="$1"; _mode="${2:-644}"
  _tmp="$_tgt.tmp.$$"
  cat > "$_tmp" || { rm -f "$_tmp"; return 1; }
  chmod "$_mode" "$_tmp"
  mv "$_tmp" "$_tgt"
}

acquire_lock() {
  # Takes the global mutation lock on fd 9 (released on process exit).
  exec 9>"$LOCK_FILE"
  flock -n 9 || fail busy "another vpn-control operation is in progress"
}

acquire_lock_wait() {
  exec 9>"$LOCK_FILE"
  flock -w "${1:-120}" 9 || fail busy "could not acquire lock"
}

lock_is_free() {
  ( exec 8>"$LOCK_FILE"; flock -n 8 ) 2>/dev/null
}

oplog() {
  # oplog <action> <detail>
  logger -t vpnctl "actor=$ACTOR action=$1 $2"
  _entry="$(jq -cn --arg a "$ACTOR" --arg ac "$1" --arg d "$2" \
    '{ts: (now | floor), actor: $a, action: $ac, detail: $d}')"
  {
    [ -f "$OPLOG_FILE" ] && tail -n 199 "$OPLOG_FILE"
    printf '%s\n' "$_entry"
  } | atomic_write "$OPLOG_FILE" 600
}

state_json() {
  # Effective desired state; synthesizes from legacy files when state.json is
  # absent (pre-cutover read-only phase).
  if [ -s "$STATE_FILE" ]; then
    cat "$STATE_FILE"
    return
  fi
  _tag=""
  [ -s "$LEGACY_TAG_FILE" ] && _tag="$(cat "$LEGACY_TAG_FILE" 2>/dev/null)"
  jq -cn --arg sel "$_tag" \
    '{enabled: true, mode: "auto-sticky", selected: (if $sel == "" then null else $sel end), legacy: true}'
}

save_state() {
  # save_state <enabled:true|false> <mode> <selected-or-empty>
  jq -cn --argjson en "$1" --arg mode "$2" --arg sel "$3" \
    '{enabled: $en, mode: $mode, selected: (if $sel == "" then null else $sel end)}' \
    | atomic_write "$STATE_FILE" 600
}

runtime_get() {
  [ -s "$RUNTIME_FILE" ] && cat "$RUNTIME_FILE" || echo '{}'
}

runtime_set() {
  # runtime_set <jq-merge-object>, e.g. runtime_set '{"busy":"check"}'
  runtime_get | jq -c ". + $1" | atomic_write "$RUNTIME_FILE" 600
}

servers_json() {
  # Server metadata list [{id,name,address,port}]; falls back to legacy
  # outbounds.json (id=tag, name=tag, address/port from vnext).
  if [ -s "$SERVERS_FILE" ]; then
    cat "$SERVERS_FILE"
  elif [ -s "$LEGACY_OUTBOUNDS" ]; then
    jq -c '[.[] | {id: .tag, name: .tag, address: .settings.vnext[0].address,
                   port: .settings.vnext[0].port}]' "$LEGACY_OUTBOUNDS"
  else
    echo '[]'
  fi
}

outbounds_json_path() {
  if [ -s "$OUTBOUNDS_FILE" ]; then
    echo "$OUTBOUNDS_FILE"
  else
    echo "$LEGACY_OUTBOUNDS"
  fi
}

checks_json() {
  [ -s "$CHECKS_FILE" ] && cat "$CHECKS_FILE" || echo '{}'
}

last_refresh_ts() {
  if [ -s "$SUB_STAMP" ]; then
    cat "$SUB_STAMP"
  elif [ -s "$LEGACY_STAMP_FILE" ]; then
    cat "$LEGACY_STAMP_FILE"
  else
    echo 0
  fi
}

service_state() {
  # service_state <initd-name> -> running|stopped|missing
  if [ ! -x "/etc/init.d/$1" ]; then
    echo missing
  elif "/etc/init.d/$1" running >/dev/null 2>&1; then
    echo running
  else
    echo stopped
  fi
}

pbr_state() {
  if [ "$(uci -q get pbr.config.enabled)" = "1" ]; then
    echo enabled
  else
    echo disabled
  fi
}

pbr_sb_tun_policies() {
  # Section names of pbr policies routed via sb_tun.
  uci -q show pbr | sed -n "s/^pbr\.\(@policy\[[0-9]*\]\|[a-zA-Z0-9_]*\)\.interface='sb_tun'$/\1/p"
}
