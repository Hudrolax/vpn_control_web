# shellcheck shell=sh
# Subscription download + vless:// parsing -> servers.json + outbounds.json.
# Adapted from the original vpn-subscription-manager (Codex).

SUB_UA="v2rayN/7.0"

urldecode() {
  printf '%b' "$(printf '%s' "$1" | sed 's/+/ /g; s/%/\\x/g')"
}

query_get() {
  key="$1"
  printf '%s' "$QUERY" | tr '&' '\n' | sed -n "s/^$key=//p" | head -n 1 \
    | while IFS= read -r value; do urldecode "$value"; done
}

sanitize_tag() {
  printf '%s' "$1" | tr -c 'A-Za-z0-9_.-' '-' | sed 's/--*/-/g; s/^-//; s/-$//' | cut -c 1-80
}

subscription_age() {
  _last="$(cat "$SUB_STAMP" 2>/dev/null || echo 0)"
  echo $(( $(now) - _last ))
}

download_subscription() {
  # download_subscription [force]; honours refresh_min_interval_sec unless forced.
  [ -s "$SUB_URL_FILE" ] || fail no_subscription_url "no $SUB_URL_FILE; run vpnctl migrate first"
  _url="$(cat "$SUB_URL_FILE")"
  _ttl="$(cfg '.refresh_min_interval_sec' 86400)"
  if [ "$1" != "force" ] && [ -s "$SUB_CACHE" ] && [ "$(subscription_age)" -lt "$_ttl" ]; then
    return 2  # cache is fresh, nothing downloaded
  fi
  _tmp="$SUB_CACHE.tmp"
  if curl -fsSL -A "$SUB_UA" --connect-timeout 10 --max-time 45 --retry 2 --retry-delay 2 \
      "$_url" -o "$_tmp" && [ -s "$_tmp" ]; then
    mv "$_tmp" "$SUB_CACHE"
    chmod 600 "$SUB_CACHE"
    now > "$SUB_STAMP"
    runtime_set '{"subscription_error": null}'
    return 0
  fi
  rm -f "$_tmp"
  runtime_set '{"subscription_error": "download failed"}'
  if [ -s "$SUB_CACHE" ]; then
    logger -t vpnctl "subscription update failed, keeping cached copy"
    return 2
  fi
  fail subscription_download "subscription update failed and no cached copy exists"
}

decode_subscription() {
  # SUB_CACHE -> $TMP_DIR/links
  _tmp="$TMP_DIR/links"
  if grep -qa '://' "$SUB_CACHE"; then
    tr '\r' '\n' < "$SUB_CACHE" > "$_tmp"
  else
    base64 -d "$SUB_CACHE" 2>/dev/null | tr '\r' '\n' > "$_tmp" \
      || fail subscription_decode "subscription base64 decode failed"
  fi
  sed -i '/^[[:space:]]*$/d' "$_tmp"
}

parse_vless_line() {
  # parse_vless_line <link> <idx> -> {meta:{...}, outbound:{...}} on stdout
  line="$1"
  idx="$2"
  rest="${line#vless://}"
  frag=""
  case "$rest" in *#*) frag="${rest#*#}"; rest="${rest%%#*}";; esac
  QUERY=""
  case "$rest" in *\?*) QUERY="${rest#*\?}"; main="${rest%%\?*}";; *) main="$rest";; esac

  uuid="${main%%@*}"
  hostport="${main#*@}"
  [ -n "$uuid" ] && [ "$uuid" != "$main" ] || return 1
  port="${hostport##*:}"
  server="${hostport%:*}"
  server="${server#[}"
  server="${server%]}"
  echo "$port" | grep -Eq '^[0-9]+$' || return 1

  name="$(urldecode "$frag")"

  net="$(query_get type)"; [ -n "$net" ] || net="tcp"
  security="$(query_get security)"; [ -n "$security" ] || security="none"
  flow="$(query_get flow)"
  pbk="$(query_get pbk)"
  fp="$(query_get fp)"; [ -n "$fp" ] || fp="chrome"
  sni="$(query_get sni)"
  sid="$(query_get sid)"
  spx="$(query_get spx)"
  path="$(query_get path)"
  host="$(query_get host)"
  mode="$(query_get mode)"
  service_name="$(query_get serviceName)"

  tag_base="$(sanitize_tag "$server")"
  [ -n "$tag_base" ] || tag_base="server"
  tag="sub-$(printf '%03d' "$idx")-$tag_base"

  jq -n \
    --arg tag "$tag" --arg name "$name" --arg server "$server" --arg port "$port" --arg uuid "$uuid" \
    --arg net "$net" --arg security "$security" --arg flow "$flow" \
    --arg pbk "$pbk" --arg fp "$fp" --arg sni "$sni" --arg sid "$sid" --arg spx "$spx" \
    --arg path "$path" --arg host "$host" --arg mode "$mode" --arg serviceName "$service_name" '
      {
        meta: {id: $tag, name: (if $name == "" then $tag else $name end),
               address: $server, port: ($port | tonumber)},
        outbound: {
          tag: $tag,
          protocol: "vless",
          settings: {
            vnext: [{
              address: $server,
              port: ($port | tonumber),
              users: [({id: $uuid, encryption: "none"} + (if $flow != "" then {flow: $flow} else {} end))]
            }]
          },
          streamSettings: (
            {network: $net, security: $security}
            + (if $security == "reality" then
                {realitySettings: ({serverName: $sni, fingerprint: $fp, publicKey: $pbk, shortId: $sid} + (if $spx != "" then {spiderX: $spx} else {} end))}
              elif $security == "tls" then
                {tlsSettings: {serverName: $sni, allowInsecure: false}}
              else {} end)
            + (if $net == "ws" then
                {wsSettings: ({path: (if $path == "" then "/" else $path end)} + (if $host != "" then {headers: {Host: $host}} else {} end))}
              elif $net == "grpc" then
                {grpcSettings: {serviceName: $serviceName}}
              elif $net == "xhttp" then
                {xhttpSettings: {path: (if $path == "" then "/" else $path end), host: $host, mode: (if $mode == "" then "auto" else $mode end), extra: {scMaxConcurrentPosts: 1, scMinPostsIntervalMs: 30, scMaxEachPostBytes: 1000000}}}
              else {} end)
          )
        }
      }'
}

build_server_files() {
  # $TMP_DIR/links -> SERVERS_FILE + OUTBOUNDS_FILE
  _ndjson="$TMP_DIR/parsed.ndjson"
  : > "$_ndjson"
  idx=0
  while IFS= read -r line; do
    case "$line" in
      vless://*)
        idx=$((idx + 1))
        parse_vless_line "$line" "$idx" >> "$_ndjson" \
          || logger -t vpnctl "skipped malformed vless link #$idx"
        ;;
      *)
        logger -t vpnctl "skipped unsupported subscription line"
        ;;
    esac
  done < "$TMP_DIR/links"

  [ -s "$_ndjson" ] || fail no_servers "no supported outbounds found in subscription"

  jq -s 'unique_by(.outbound.tag) | map(.outbound)' "$_ndjson" | atomic_write "$OUTBOUNDS_FILE" 600
  jq -s 'unique_by(.meta.id) | map(.meta)' "$_ndjson" | atomic_write "$SERVERS_FILE" 600
  rm -f "$_ndjson" "$TMP_DIR/links"
}

do_refresh() {
  # do_refresh [force] -> prints refresh result JSON
  _old_ids='[]'
  [ -s "$SERVERS_FILE" ] && _old_ids="$(jq -c '[.[].id]' "$SERVERS_FILE")"

  download_subscription "$1"
  _downloaded=$?

  decode_subscription
  build_server_files

  _new_ids="$(jq -c '[.[].id]' "$SERVERS_FILE")"
  _count="$(jq 'length' "$SERVERS_FILE")"
  runtime_set "{\"last_refresh_run\": $(now)}"

  # Warn (but never auto-switch here) when the selected server left the list.
  _sel="$(state_json | jq -r '.selected // ""')"
  _warning=null
  if [ -n "$_sel" ] && ! jq -e --arg id "$_sel" '.[] | select(.id == $id)' "$SERVERS_FILE" >/dev/null; then
    _warning="\"selected server $_sel is no longer in the subscription\""
    runtime_set "{\"warning\": $_warning}"
  else
    runtime_set '{"warning": null}'
  fi

  oplog refresh "count=$_count downloaded=$([ $_downloaded -eq 0 ] && echo yes || echo cached)"
  jq -cn --argjson old "$_old_ids" --argjson new "$_new_ids" --argjson count "$_count" \
    --argjson dl "$([ $_downloaded -eq 0 ] && echo true || echo false)" \
    --argjson warning "$_warning" \
    '{ok: true, downloaded: $dl, server_count: $count,
      added: ($new - $old), removed: ($old - $new),
      changed: (($new - $old) + ($old - $new) | length > 0),
      warning: $warning}'
}
