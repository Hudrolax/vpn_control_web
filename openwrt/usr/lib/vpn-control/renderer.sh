# shellcheck shell=sh
# Renders the runtime xray client config for a given server id and applies it
# (test -> mv -> restart services). Routing stays with pbr; this only swaps the
# xray outbound.

render_config() {
  # render_config <id> <socks_port> <output>
  _src="$(outbounds_json_path)"
  [ -s "$_src" ] || return 1
  _selected="$(jq -c --arg tag "$1" 'map(select(.tag == $tag))[0]' "$_src")"
  [ -n "$_selected" ] && [ "$_selected" != "null" ] || return 1
  jq -n --argjson selected "$_selected" --arg port "$2" '{
    log: {loglevel: "warning"},
    inbounds: [
      {tag: "socks-in", listen: "127.0.0.1", port: ($port | tonumber), protocol: "socks",
       settings: {auth: "noauth", udp: true},
       sniffing: {enabled: true, destOverride: ["http", "tls", "quic"]}},
      {tag: "http-in", listen: "127.0.0.1", port: (($port | tonumber) + 1), protocol: "http",
       settings: {}, sniffing: {enabled: true, destOverride: ["http", "tls"]}}
    ],
    outbounds: [
      $selected,
      {tag: "direct", protocol: "freedom"},
      {tag: "block", protocol: "blackhole"}
    ],
    routing: {
      domainStrategy: "AsIs",
      rules: [
        {type: "field", protocol: ["bittorrent"], outboundTag: "block"}
      ]
    }
  }' > "$3"
}

apply_server() {
  # apply_server <id>: render, test, install, restart. Caller holds the lock
  # and has validated the id against servers.json.
  _id="$1"
  _port="$(cfg '.socks_port' 10808)"
  _tmp="/var/etc/xray-subscription-client.next.json"
  render_config "$_id" "$_port" "$_tmp" || fail render "cannot render config for $_id"
  "$XRAY_BIN" run -test -config "$_tmp" >/dev/null 2>"$TMP_DIR/xray-apply-test.log" \
    || { rm -f "$_tmp"; fail xray_test "xray config test failed for $_id"; }
  mv "$_tmp" "$XRAY_CONFIG"
  chmod 600 "$XRAY_CONFIG"
  /etc/init.d/"$XRAY_SERVICE" enable >/dev/null 2>&1 || true
  /etc/init.d/"$XRAY_SERVICE" restart >/dev/null 2>&1 || fail service "failed to restart $XRAY_SERVICE"
  /etc/init.d/"$SINGBOX_SERVICE" enable >/dev/null 2>&1 || true
  /etc/init.d/"$SINGBOX_SERVICE" restart >/dev/null 2>&1 || fail service "failed to restart $SINGBOX_SERVICE"
  # Keep the legacy state file in sync until the old manager is retired, so a
  # still-active legacy cron does not fight the new controller.
  if [ -d "$LEGACY_WORK_DIR" ]; then
    printf '%s\n' "$_id" > "$LEGACY_TAG_FILE"
    chmod 600 "$LEGACY_TAG_FILE"
  fi
}

vpn_enable() {
  _state="$(state_json)"
  _sel="$(printf '%s' "$_state" | jq -r '.selected // ""')"
  [ -n "$_sel" ] || fail no_server "no server selected; run vpnctl select first"
  apply_server "$_sel"
  for _pol in $(pbr_sb_tun_policies); do
    uci set "pbr.$_pol.enabled=1"
  done
  uci commit pbr
  /etc/init.d/pbr reload >/dev/null 2>&1 || true
  save_state true "$(printf '%s' "$_state" | jq -r '.mode')" "$_sel"
  oplog enable "server=$_sel"
}

vpn_disable() {
  _state="$(state_json)"
  # Order matters (fail-open): stop marking traffic first, then stop tunnels.
  save_state false "$(printf '%s' "$_state" | jq -r '.mode')" \
    "$(printf '%s' "$_state" | jq -r '.selected // ""')"
  for _pol in $(pbr_sb_tun_policies); do
    uci set "pbr.$_pol.enabled=0"
  done
  uci commit pbr
  /etc/init.d/pbr reload >/dev/null 2>&1 || true
  /etc/init.d/"$SINGBOX_SERVICE" stop >/dev/null 2>&1 || true
  /etc/init.d/"$SINGBOX_SERVICE" disable >/dev/null 2>&1 || true
  /etc/init.d/"$XRAY_SERVICE" stop >/dev/null 2>&1 || true
  /etc/init.d/"$XRAY_SERVICE" disable >/dev/null 2>&1 || true
  oplog disable ""
}
