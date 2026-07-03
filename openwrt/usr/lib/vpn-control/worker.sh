# shellcheck shell=sh
# Cron worker: refresh subscription (TTL-respecting) and run the auto-mode
# selection logic. Replaces the legacy vpn-subscription-manager cron entry at
# phase-3 cutover.

worker_run() {
  _state="$(state_json)"
  _enabled="$(printf '%s' "$_state" | jq -r '.enabled')"
  _mode="$(printf '%s' "$_state" | jq -r '.mode')"
  _sel="$(printf '%s' "$_state" | jq -r '.selected // ""')"

  if [ "$_enabled" != "true" ]; then
    jq -cn '{ok: true, action: "noop", reason: "vpn disabled"}'
    return 0
  fi

  do_refresh "" >/dev/null 2>&1 || true

  # Selected server vanished from the subscription: treat as dead.
  if [ -n "$_sel" ] && ! servers_json | jq -e --arg id "$_sel" '.[] | select(.id == $id)' >/dev/null; then
    logger -t vpnctl "selected $_sel disappeared from subscription"
    _sel=""
  fi

  _current_ok=false
  if [ -n "$_sel" ] && check_current_via_main "$_sel"; then
    _current_ok=true
  fi

  case "$_mode" in
    manual)
      if [ "$_current_ok" = "true" ]; then
        jq -cn --arg s "$_sel" '{ok: true, action: "keep", server: $s}'
      else
        logger -t vpnctl "manual mode: selected $_sel is down; not switching"
        jq -cn --arg s "$_sel" '{ok: true, action: "keep-down", server: $s}'
      fi
      return 0
      ;;
    auto-best)
      _min_hold="$(cfg '.auto_best.min_hold_sec' 1800)"
      _last_switch="$(runtime_get | jq -r '.last_switch // 0')"
      _held=$(( $(now) - _last_switch ))
      if [ "$_current_ok" = "true" ] && [ "$_held" -lt "$_min_hold" ]; then
        jq -cn --arg s "$_sel" '{ok: true, action: "keep", server: $s, reason: "min_hold"}'
        return 0
      fi
      check_all
      if [ "$_current_ok" = "true" ]; then
        _cur_ms="$(checks_json | jq -r --arg id "$_sel" '.[$id].latency_ms // empty')"
        _best="$(best_ok_server || true)"
        [ -n "$_best" ] && [ "$_best" != "$_sel" ] || {
          jq -cn --arg s "$_sel" '{ok: true, action: "keep", server: $s}'; return 0; }
        _best_ms="$(checks_json | jq -r --arg id "$_best" '.[$id].latency_ms // empty')"
        _improve="$(cfg '.auto_best.improvement_pct' 35)"
        _degrade="$(cfg '.auto_best.degrade_ms' 400)"
        # Switch only when the current server is degraded AND the best one is
        # clearly better (hysteresis against flapping).
        if [ -n "$_cur_ms" ] && [ -n "$_best_ms" ] \
           && [ "$_cur_ms" -gt "$_degrade" ] \
           && [ $(( _best_ms * 100 )) -lt $(( _cur_ms * (100 - _improve) )) ]; then
          apply_server "$_best"
          save_state true "$_mode" "$_best"
          runtime_set "{\"last_switch\": $(now)}"
          oplog auto-switch "auto-best: $_sel(${_cur_ms}ms) -> $_best(${_best_ms}ms)"
          jq -cn --arg f "$_sel" --arg t "$_best" '{ok: true, action: "switch", from: $f, to: $t}'
          return 0
        fi
        jq -cn --arg s "$_sel" '{ok: true, action: "keep", server: $s}'
        return 0
      fi
      ;;
    *) # auto-sticky: keep while alive
      if [ "$_current_ok" = "true" ]; then
        jq -cn --arg s "$_sel" '{ok: true, action: "keep", server: $s}'
        return 0
      fi
      check_all
      ;;
  esac

  # Current server is dead (or none selected): fail over to the best live one.
  _best="$(best_ok_server || true)"
  if [ -z "$_best" ]; then
    logger -t vpnctl "no working candidate found; keeping current config"
    jq -cn '{ok: false, error: "no_candidates", message: "no working server found"}'
    return 1
  fi
  apply_server "$_best"
  save_state true "$_mode" "$_best"
  runtime_set "{\"last_switch\": $(now)}"
  oplog failover "${_sel:-none} -> $_best"
  jq -cn --arg f "${_sel:-}" --arg t "$_best" '{ok: true, action: "failover", from: $f, to: $t}'
}
