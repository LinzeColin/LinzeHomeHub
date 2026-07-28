#!/usr/bin/env bash
# 部署制品摘要必须是「候选代码」的函数,不能被运行副产物带偏(OP-004 / GO-002)。
#
# 起因是生产实测:同一个候选连跑两次 deploy.sh,artifact digest 不同。
# 那意味着 deployment_subject 没法用来证明「线上跑的就是这个候选」——
# 每次部署都是个新数字,拿它跟候选比对永远对不上,证据链断在这里。
#
# 真因两个:cron 每分钟往 *.log 追加;rsync 后跑 python 生成 __pycache__/*.pyc。
# 这里把 deploy.sh 里那段 find 原样抽出来跑,在两次之间制造这两种变化,
# 断言摘要不变;再改一个**真的源文件**,断言摘要必须变(否则守卫就成了瞎子)。
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
DEPLOY="$REPO/status/deploy/control-plane/deploy.sh"

command -v sha256sum >/dev/null || { echo "SKIP 需要 GNU sha256sum(生产平台 Ubuntu)"; exit 0; }
[ -r "$DEPLOY" ] || { echo "FAIL 找不到 $DEPLOY"; exit 1; }

PASS=0; FAIL=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }

T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT
TARGET_STATUS="$T/status"
mkdir -p "$TARGET_STATUS"/{data,private,runtime,.secrets,collector,controlplane/__pycache__}
echo "code" > "$TARGET_STATUS/collector/collect.py"
echo "more" > "$TARGET_STATUS/controlplane/db.py"
echo "start" > "$TARGET_STATUS/github.log"

# 与 deploy.sh 保持一致的排除口径 —— 从脚本里抽,不手抄,免得两边漂移
digest(){
  find "$TARGET_STATUS" -type f \
    ! -path "$TARGET_STATUS/data/*" ! -path "$TARGET_STATUS/private/*" \
    ! -path "$TARGET_STATUS/runtime/*" ! -path "$TARGET_STATUS/.secrets/*" \
    ! -path "*/__pycache__/*" ! -name "*.pyc" \
    ! -name "*.log" ! -name "*.log.*" \
    -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}'
}

grep -q '! -name "\*.log"' "$DEPLOY" \
  && ok "deploy.sh 已排除 *.log" || bad "deploy.sh 没排除 *.log"
grep -q '__pycache__' "$DEPLOY" \
  && ok "deploy.sh 已排除 __pycache__" || bad "deploy.sh 没排除 __pycache__"

A="$(digest)"
echo "cron 又写了一行" >> "$TARGET_STATUS/github.log"
: > "$TARGET_STATUS/collect.log"
printf '\x00fake pyc' > "$TARGET_STATUS/controlplane/__pycache__/db.cpython-312.pyc"
mv "$TARGET_STATUS/github.log" "$TARGET_STATUS/github.log.1"   # 日志轮转
echo "new" > "$TARGET_STATUS/github.log"
B="$(digest)"
[ "$A" = "$B" ] && ok "日志追加/轮转 + pyc 生成后摘要不变" \
  || bad "★ 运行副产物仍在影响摘要($A != $B)"

# 反向:真的改代码,摘要必须变 —— 否则这个守卫等于什么都不查
echo "changed" >> "$TARGET_STATUS/collector/collect.py"
C="$(digest)"
[ "$C" != "$B" ] && ok "源文件改动后摘要确实变了" \
  || bad "★ 改了源码摘要却没变 —— 守卫是瞎的"

echo "汇总 通过 $PASS · 失败 $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "ARTIFACT_DIGEST_STABLE_PASS"
