#!/usr/bin/env bash
set -Eeuo pipefail
PATH='/usr/sbin:/usr/bin:/sbin:/bin'

readonly CONFIG='/etc/vmrack-traffic-reset/reset.conf'
readonly TOKEN_FILE="${CREDENTIALS_DIRECTORY:-/etc/vmrack-traffic-reset}/api-token"
readonly EXPECTED_TIMEZONE='Asia/Singapore'
readonly LOCK_DIR="${RUNTIME_DIRECTORY:-/run/vmrack-traffic-reset}"
readonly STATE_DIR="${STATE_DIRECTORY:-/var/lib/vmrack-traffic-reset}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

test "$#" -eq 1 \
  || fail 'usage: vmrack-3xui-traffic-reset --check|--run'
MODE="$1"
case "$MODE" in
  --check|--run) ;;
  *) fail 'usage: vmrack-3xui-traffic-reset --check|--run' ;;
esac

test "$(id -u)" -eq 0 || fail 'must run as root'
for command_name in chmod curl date flock id install jq mv sleep stat tr; do
  command -v "$command_name" >/dev/null 2>&1 \
    || fail "missing dependency: $command_name"
done
test -r "$CONFIG" || fail "missing $CONFIG"
test -r "$TOKEN_FILE" || fail 'API token credential is unavailable'
test "$(stat -c '%U:%G %a' "$CONFIG")" = 'root:root 600' \
  || fail "$CONFIG must be root:root mode 0600"
case "$(stat -c '%U:%G %a' "$TOKEN_FILE")" in
  'root:root 400'|'root:root 600') ;;
  *) fail 'API token must be root:root mode 0400 or 0600' ;;
esac
# shellcheck disable=SC1090
source "$CONFIG"

: "${PANEL_BASE_URL:?missing PANEL_BASE_URL}"
: "${PANEL_RESOLVE:?missing PANEL_RESOLVE}"
: "${CLIENT_EMAIL:?missing CLIENT_EMAIL}"
: "${INBOUND_REMARK:?missing INBOUND_REMARK}"
: "${EXPECTED_TOTAL_BYTES:?missing EXPECTED_TOTAL_BYTES}"

case "$PANEL_BASE_URL" in
  https://*'<'*|https://*'>'*) fail 'replace the panel-path placeholder' ;;
  https://*/) fail 'PANEL_BASE_URL must not end with slash' ;;
  https://*) ;;
  *) fail 'PANEL_BASE_URL must use HTTPS' ;;
esac
[[ "$CLIENT_EMAIL" =~ ^[A-Za-z0-9._@+-]+$ ]] || fail 'unsafe client email'
[[ "$EXPECTED_TOTAL_BYTES" =~ ^[0-9]+$ ]] || fail 'invalid byte quota'
test "$EXPECTED_TOTAL_BYTES" = '644245094400' || fail 'unexpected quota contract'
test "$PANEL_RESOLVE" = 'panel.example.invalid:8443:127.0.0.1' \
  || fail 'unexpected loopback resolve contract'

API_TOKEN="$(tr -d '\r\n' <"$TOKEN_FILE")"
[[ "$API_TOKEN" =~ ^[A-Za-z0-9]{48}$ ]] || fail 'invalid API token format'

install -d -o root -g root -m 0700 "$LOCK_DIR" "$STATE_DIR"
exec 9>"${LOCK_DIR}/reset.lock"
flock -n 9 || fail 'another reset process is already running'

BILLING_CYCLE="$(TZ="$EXPECTED_TIMEZONE" date '+%Y-%m')"
CLIENT_MARKER="${STATE_DIR}/client-reset.done"
INBOUND_MARKER="${STATE_DIR}/inbound-reset.done"

marker_value() {
  local marker="$1"
  test -r "$marker" && tr -d '\r\n' <"$marker" || true
}

validate_marker() {
  local marker="$1"
  local value
  value="$(marker_value "$marker")"
  test -z "$value" && return 0
  [[ "$value" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]] \
    || fail "invalid state marker: $marker"
  [[ "$value" > "$BILLING_CYCLE" ]] \
    && fail "future state marker: $marker"
  return 0
}

marker_is_current() {
  test "$(marker_value "$1")" = "$BILLING_CYCLE"
}

write_marker() {
  local marker="$1"
  local temporary="${marker}.tmp.$$"
  printf '%s\n' "$BILLING_CYCLE" >"$temporary"
  chmod 0600 "$temporary"
  mv -f -- "$temporary" "$marker"
}

validate_marker "$CLIENT_MARKER"
validate_marker "$INBOUND_MARKER"

api_request() {
  local method="$1"
  local path="$2"
  printf 'header = "Authorization: Bearer %s"\n' "$API_TOKEN" \
    | curl --config - \
        --silent --show-error --fail-with-body \
        --proto '=https' --tlsv1.2 \
        --resolve "$PANEL_RESOLVE" \
        --connect-timeout 5 --max-time 30 \
        --request "$method" \
        --header 'Accept: application/json' \
        "${PANEL_BASE_URL}${path}"
}

require_success() {
  local label="$1"
  local payload="$2"
  jq -e '.success == true' <<<"$payload" >/dev/null \
    || fail "$label returned unsuccessful JSON"
}

INBOUNDS_JSON="$(api_request GET '/panel/api/inbounds/list')"
require_success 'inbound discovery' "$INBOUNDS_JSON"

MATCH_COUNT="$(
  jq -r --arg remark "$INBOUND_REMARK" \
    '[.obj[] | select(.remark == $remark)] | length' <<<"$INBOUNDS_JSON"
)"
test "$MATCH_COUNT" = '1' \
  || fail "expected exactly one inbound remark match, got $MATCH_COUNT"

INBOUND_ID="$(
  jq -r --arg remark "$INBOUND_REMARK" \
    '.obj[] | select(.remark == $remark) | .id' <<<"$INBOUNDS_JSON"
)"
[[ "$INBOUND_ID" =~ ^[1-9][0-9]*$ ]] || fail 'invalid inbound id'

jq -e \
  --arg remark "$INBOUND_REMARK" \
  --arg email "$CLIENT_EMAIL" \
  --argjson expected "$EXPECTED_TOTAL_BYTES" '
    .obj[]
    | select(.remark == $remark)
    | .enable == true
      and .trafficReset == "never"
      and .total == $expected
      and (.settings.clients | any(.email == $email and .enable == true))
  ' <<<"$INBOUNDS_JSON" >/dev/null \
  || fail 'inbound quota, reset mode, enable state, or client association is wrong'

CLIENT_JSON="$(api_request GET "/panel/api/clients/get/${CLIENT_EMAIL}")"
require_success 'client lookup' "$CLIENT_JSON"
jq -e \
  --arg email "$CLIENT_EMAIL" \
  --argjson inbound_id "$INBOUND_ID" \
  --argjson expected "$EXPECTED_TOTAL_BYTES" '
    .obj.client.email == $email
      and .obj.client.enable == true
      and .obj.client.totalGB == $expected
      and .obj.client.reset == 0
      and (.obj.inboundIds | index($inbound_id) != null)
  ' <<<"$CLIENT_JSON" >/dev/null \
  || fail 'client email, quota, reset mode, enable state, or inbound association is wrong'

if test "$MODE" = '--check'; then
  printf 'PREFLIGHT=PASS client=%s inbound_id=%s timezone=%s cycle=%s client_done=%s inbound_done=%s\n' \
    "$CLIENT_EMAIL" "$INBOUND_ID" "$EXPECTED_TIMEZONE" "$BILLING_CYCLE" \
    "$(marker_is_current "$CLIENT_MARKER" && printf yes || printf no)" \
    "$(marker_is_current "$INBOUND_MARKER" && printf yes || printf no)"
  exit 0
fi

BILLING_LOCAL_MINUTE="$(TZ="$EXPECTED_TIMEZONE" date '+%d %H:%M')"
case "$BILLING_LOCAL_MINUTE" in
  '10 09:50'|'10 09:51'|'10 09:52'|'10 09:53'|'10 09:54') ;;
  *) fail "--run is allowed only during day 10 09:50:00-09:54:59 ${EXPECTED_TIMEZONE}" ;;
esac

reset_with_retry() {
  local label="$1"
  local path="$2"
  local attempt payload
  for attempt in 1 2 3; do
    if payload="$(api_request POST "$path")" \
      && jq -e '.success == true' <<<"$payload" >/dev/null; then
      printf 'STEP=PASS name=%s attempt=%s cycle=%s\n' \
        "$label" "$attempt" "$BILLING_CYCLE"
      return 0
    fi
    printf 'STEP=RETRY name=%s attempt=%s cycle=%s\n' \
      "$label" "$attempt" "$BILLING_CYCLE" >&2
    test "$attempt" -lt 3 && sleep 1
  done
  fail "$label failed after 3 attempts; rerun the service only inside the billing window"
}

if marker_is_current "$CLIENT_MARKER"; then
  printf 'STEP=SKIP name=client-reset reason=already-complete cycle=%s\n' \
    "$BILLING_CYCLE"
else
  reset_with_retry 'client-reset' \
    "/panel/api/clients/resetTraffic/${CLIENT_EMAIL}"
  write_marker "$CLIENT_MARKER"
fi

if marker_is_current "$INBOUND_MARKER"; then
  printf 'STEP=SKIP name=inbound-reset reason=already-complete cycle=%s\n' \
    "$BILLING_CYCLE"
else
  reset_with_retry 'inbound-reset' \
    "/panel/api/inbounds/${INBOUND_ID}/resetTraffic"
  write_marker "$INBOUND_MARKER"
fi

CLIENT_TRAFFIC_JSON="$(
  api_request GET "/panel/api/clients/traffic/${CLIENT_EMAIL}"
)"
require_success 'client post-reset readback' "$CLIENT_TRAFFIC_JSON"
CLIENT_POST_BYTES="$(jq -r '(.obj.up // 0) + (.obj.down // 0)' \
  <<<"$CLIENT_TRAFFIC_JSON")"

INBOUND_AFTER_JSON="$(
  api_request GET "/panel/api/inbounds/get/${INBOUND_ID}"
)"
require_success 'inbound post-reset readback' "$INBOUND_AFTER_JSON"
INBOUND_POST_BYTES="$(jq -r '(.obj.up // 0) + (.obj.down // 0)' \
  <<<"$INBOUND_AFTER_JSON")"

[[ "$CLIENT_POST_BYTES" =~ ^[0-9]+$ ]] \
  || fail 'invalid client post-reset counter'
[[ "$INBOUND_POST_BYTES" =~ ^[0-9]+$ ]] \
  || fail 'invalid inbound post-reset counter'

printf 'RESET=PASS client=%s inbound_id=%s client_post_bytes=%s inbound_post_bytes=%s timezone=%s\n' \
  "$CLIENT_EMAIL" "$INBOUND_ID" "$CLIENT_POST_BYTES" \
  "$INBOUND_POST_BYTES" "$EXPECTED_TIMEZONE"
