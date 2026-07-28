#!/usr/bin/env bash
# 自愈看门狗的「不许谎报已恢复」测试(冻结验收 OP-002)。
#
# 为什么必须跑**真脚本**:被测对象是 status/deploy/linze-selfheal.sh 本身。
# 如果先 sed 出一份改过的副本再测,测的就不是线上跑的那份代码 —— 那是装饰性测试。
# 所以脚本里留了三个测试接缝(LINZE_STATUS_APP / LINZE_SELFHEAL_MON /
# LINZE_SELFHEAL_PROBE_*),这里用假的 docker/curl 顶在 PATH 前面,真脚本原样执行。
#
# 需要 GNU 工具(stat -c / tac),macOS 上自动跳过 —— 生产就跑在 Ubuntu 上。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
SCRIPT="$REPO/status/deploy/linze-selfheal.sh"

if ! stat -c %Y "$SCRIPT" >/dev/null 2>&1 || ! command -v tac >/dev/null 2>&1; then
  echo "SKIP 需要 GNU stat/tac(生产平台 Ubuntu);当前平台跳过"
  exit 0
fi
[ -r "$SCRIPT" ] || { echo "FAIL 找不到被测脚本 $SCRIPT"; exit 1; }

PASS=0; FAIL=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }

# 造一个隔离的 APP 目录 + 假 docker/curl。
# 假 curl 的返回码由 FAKE_HTTP_SEQ 逐次弹出,弹完了重复最后一个 ——
# 这样才能表达「第一次探测失败、复探成功」和「一路都失败」两种剧本。
setup(){
  T="$(mktemp -d)"; BIN="$T/bin"; mkdir -p "$BIN" "$T/app/data/.selfheal"
  cat > "$BIN/curl" <<'EOF'
#!/usr/bin/env bash
f="$FAKE_SEQ_FILE"
code="$(head -1 "$f" 2>/dev/null)"; [ -z "$code" ] && code=000
if [ "$(wc -l < "$f")" -gt 1 ]; then tail -n +2 "$f" > "$f.t" && mv "$f.t" "$f"; fi
echo -n "$code"
EOF
  cat > "$BIN/docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  ps)      echo "linze-status" ;;
  restart) exit "${FAKE_RESTART_RC:-0}" ;;
  *)       exit 0 ;;
esac
EOF
  chmod +x "$BIN/curl" "$BIN/docker"
  FAKE_SEQ_FILE="$T/seq"
  # 预置失败计数=1:FAIL_TRIP=2,本轮再失败一次就到阈值,看门狗会动手
  echo 1 > "$T/app/data/.selfheal/fail_linze-status"
}
teardown(){ rm -rf "$T"; }

run_selfheal(){  # $1=curl 返回码序列(换行分隔) $2=docker restart 退出码
  printf '%s\n' "$1" > "$FAKE_SEQ_FILE"
  PATH="$BIN:$PATH" \
  FAKE_SEQ_FILE="$FAKE_SEQ_FILE" \
  FAKE_RESTART_RC="${2:-0}" \
  LINZE_STATUS_APP="$T/app" \
  LINZE_SELFHEAL_MON='https://example.invalid|linze-status|测试服务' \
  LINZE_SELFHEAL_PROBE_TRIES=2 \
  LINZE_SELFHEAL_PROBE_GAP=0 \
    bash "$SCRIPT" >/dev/null 2>&1
  cat "$T/app/data/selfheal.json"
}

field(){ python3 -c "
import json,sys
d=json.load(sys.stdin)
r=[x for x in d['rules'] if x['key']=='$1'][0]
print(r.get('$2') or '')
"; }

echo "=== 正控:重启后复探通过 → 必须报「已恢复」 ==="
setup
OUT="$(run_selfheal $'000\n200' 0)"
S="$(printf '%s' "$OUT" | field watchdog state)"
M="$(printf '%s' "$OUT" | field watchdog last_action)"
[ "$S" = "acted" ] && ok "state=acted" || bad "state 应为 acted,实得 '$S'"
case "$M" in *已恢复*) ok "文案含「已恢复」";; *) bad "文案未说明已恢复:$M";; esac
[ "$(cat "$T/app/data/.selfheal/fail_linze-status")" = "0" ] \
  && ok "复探通过后失败计数已清零" || bad "复探通过却没清零失败计数"
teardown

echo
echo "=== 负控 1:重启成功但复探一直不通 → 绝不能报「已恢复」 ==="
setup
OUT="$(run_selfheal $'000\n000\n000\n000' 0)"
S="$(printf '%s' "$OUT" | field watchdog state)"
M="$(printf '%s' "$OUT" | field watchdog last_action)"
[ "$S" = "failed" ] && ok "state=failed" || bad "state 应为 failed,实得 '$S'"
case "$M" in *未恢复*) ok "文案明说「未恢复」";; *) bad "文案没说未恢复:$M";; esac
case "$M" in *已恢复*) bad "★ 谎报已恢复:$M";; *) ok "文案未出现「已恢复」";; esac
FC="$(cat "$T/app/data/.selfheal/fail_linze-status")"
[ "$FC" != "0" ] && ok "失败计数保留($FC),下一轮到点会再动手" \
  || bad "★ 没修好却把失败计数清零了 —— 下一轮要重新攒够阈值"
teardown

echo
echo "=== 负控 2:连 docker restart 都失败 → 如实记录,不复探 ==="
setup
OUT="$(run_selfheal $'000\n000' 1)"
S="$(printf '%s' "$OUT" | field watchdog state)"
M="$(printf '%s' "$OUT" | field watchdog last_action)"
[ "$S" = "failed" ] && ok "state=failed" || bad "state 应为 failed,实得 '$S'"
case "$M" in *重启指令失败*) ok "文案指明是重启指令本身失败";; *) bad "文案不准确:$M";; esac
case "$M" in *已恢复*) bad "★ 谎报已恢复:$M";; *) ok "文案未出现「已恢复」";; esac
teardown

echo
echo "=== 汇总 通过 $PASS · 失败 $FAIL ==="
[ "$FAIL" -eq 0 ] || exit 1
echo "SELFHEAL_POST_PROBE_PASS"
