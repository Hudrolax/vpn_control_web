# shellcheck shell=sh
# Health checks: temporary xray SOCKS on the probe port -> HTTPS GET check_url.
# Results land in checks.json: { "<id>": {status, latency_ms, last_error, last_checked} }

probe_socks() {
  # probe_socks <port> -> prints seconds (float) on success
  _url="$(cfg '.check_url' 'https://www.gstatic.com/generate_204')"
  _to="$(cfg '.check_timeout_sec' 8)"
  curl --socks5-hostname "127.0.0.1:$1" -4 -fsS --connect-timeout 6 --max-time "$_to" \
    -o /dev/null -w '%{time_total}' "$_url" 2>/dev/null
}

record_check() {
  # record_check <id> <status> <latency_ms-or-empty> <error-or-empty>
  checks_json | jq -c --arg id "$1" --arg st "$2" --arg ms "$3" --arg err "$4" \
    '.[$id] = {status: $st,
               latency_ms: (if $ms == "" then null else ($ms | tonumber) end),
               last_error: (if $err == "" then null else $err end),
               last_checked: (now | floor)}' \
    | atomic_write "$CHECKS_FILE" 600
}

check_candidate() {
  # check_candidate <id> -> prints latency ms on success; uses probe port
  _id="$1"
  _port="$(cfg '.probe_port' 20808)"
  _conf="$TMP_DIR/probe-$_id.json"
  _log="$TMP_DIR/probe-$_id.log"
  render_config "$_id" "$_port" "$_conf" || return 1
  "$XRAY_BIN" run -test -config "$_conf" >/dev/null 2>"$_log" || { rm -f "$_conf"; return 1; }
  "$XRAY_BIN" run -config "$_conf" >/dev/null 2>"$_log" &
  _pid="$!"
  sleep 2
  _sec="$(probe_socks "$_port")"
  _rc="$?"
  kill "$_pid" >/dev/null 2>&1 || true
  wait "$_pid" >/dev/null 2>&1 || true
  rm -f "$_conf" "$_log"
  [ "$_rc" -eq 0 ] && [ -n "$_sec" ] || return 1
  awk -v s="$_sec" 'BEGIN { printf "%d", s * 1000 }'
}

check_one() {
  # check_one <id>; updates checks.json, prints nothing, returns 0 if server ok
  if _ms="$(check_candidate "$1")"; then
    record_check "$1" ok "$_ms" ""
    return 0
  fi
  record_check "$1" down "" "connect/probe failed"
  return 1
}

check_current_via_main() {
  # Health of the *active* server through the live SOCKS (no temp xray).
  _id="$1"
  _port="$(cfg '.socks_port' 10808)"
  [ "$(service_state "$XRAY_SERVICE")" = "running" ] || { record_check "$_id" down "" "xray not running"; return 1; }
  if _sec="$(probe_socks "$_port")" && [ -n "$_sec" ]; then
    _ms="$(awk -v s="$_sec" 'BEGIN { printf "%d", s * 1000 }')"
    record_check "$_id" ok "$_ms" ""
    return 0
  fi
  record_check "$_id" down "" "probe via main socks failed"
  return 1
}

check_all() {
  # Checks every server in servers.json sequentially (candidate probes).
  # The active server is probed through the live tunnel instead, so the
  # probe port never disturbs the office traffic.
  _sel="$(state_json | jq -r '.selected // ""')"
  for _id in $(servers_json | jq -r '.[].id'); do
    if [ "$_id" = "$_sel" ] && [ "$(service_state "$XRAY_SERVICE")" = "running" ]; then
      check_current_via_main "$_id" || true
    else
      check_one "$_id" || true
    fi
  done
}

best_ok_server() {
  # Lowest-latency server with status ok from checks.json; prints id or fails.
  checks_json | jq -re 'to_entries | map(select(.value.status == "ok"))
    | sort_by(.value.latency_ms) | .[0].key // empty'
}
