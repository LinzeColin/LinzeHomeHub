# Agent 会话捕获 hook —— 安装与回滚

捕获 hook 是**开发期**零件:它在 provider 每次工具调用后被拉起一次,把事件脱敏归一成
append-only JSONL。生产运行不依赖它,它也永远不是 Verifier。

## 为什么不能直接用任务包里的片段

`status/controlplane/adapters/*.fragment.*` 里的命令是**示意**,原样装上去会出事:

- 用的是相对路径(`status/runtime/...`、`-m status.controlplane.agent_hook`),而 hook 的
  工作目录由 provider 决定,基本不会是仓根。
- `agent_hook` 早期版本缺 `STATUS_AGENT_RUN_ID` 就抛错。装成全局 hook 后,**本机每一次
  工具调用**(包括所有与 status 无关的项目)都会报一次错。

现已修成:不在受治理的 run 里就安静退出 0、不读 stdin、不写盘,且在确认之前不导入
capture/redaction(省掉每次调用的模块导入开销)。守卫见
`tests/status-control-plane/unit/test_agent_hook_is_inert_outside_session.py`。

## Claude Code(已安装)

包装脚本 `~/.claude/hooks/status-agent-hook.sh` —— 三条失败安全:仓/模块不在则退 0;
不在受治理 run 里则由 agent_hook 自己退 0;任何异常一律吞掉,绝不把非零码回传给 provider。

仓路径取 `STATUS_AGENT_REPO_ROOT`,默认指向**主工作树** `~/Documents/Codex/GithubProject/LinzeHomeHub`
(永远停 main、永远存在),**不要**指向 `_scratch/` 下的临时 worktree —— 树被收掉后
hook 会每次调用都找不到模块。

`~/.claude/settings.json` 片段:

```json
{ "hooks": { "PostToolUse": [
  { "matcher": "*", "hooks": [
    { "type": "command", "command": "<HOME>/.claude/hooks/status-agent-hook.sh claude" } ] } ] } }
```

回滚:

```bash
cp ~/.claude/settings.json.bak-20260729-uiux-merge ~/.claude/settings.json
rm -f ~/.claude/hooks/status-agent-hook.sh
```

## Codex(未安装 —— 前置条件不成立)

`codex-hook.fragment.toml` 的头一行就写着「Merge only after `codex --version` and the
installed hook schema are verified」。本机现状:

- `codex` CLI **不在 PATH 上** —— 无法执行 `codex --version`,拿不到版本。
- `~/.codex/config.toml` 里**没有 `[hooks]` 段** —— 没有「已安装的 hook schema」可供合并。

在这两条都不成立时往 config.toml 里写一段未经核验的 `[hooks]`,收益为零(没有任何东西
会去消费它),风险是把 Owner 正在用的 Codex 配置写坏。因此 T02-02 记为 **BLOCKED**,不是
跳过、也不是豁免。

Codex CLI 就位后按这个顺序做:

```bash
codex --version                                    # 1. 先拿到版本
grep -n '^\[hooks\]' ~/.codex/config.toml          # 2. 确认已装 hook schema 的形状
cp ~/.codex/config.toml ~/.codex/config.toml.bak   # 3. 备份
# 4. 按当时的真实 schema 合并,command 指向包装脚本并传 codex:
#    <HOME>/.claude/hooks/status-agent-hook.sh codex
```

验证(两条都要过):

```bash
# 不在受治理 run 里:静默、退 0、不写盘
echo '{"hook_event_name":"PostToolUse"}' | ~/.claude/hooks/status-agent-hook.sh codex; echo $?

# 在受治理 run 里:落一行脱敏事件,植入的秘密不出现
source status/runtime/agent-runs/<run-id>/session.env
~/.claude/hooks/status-agent-hook.sh codex < tests/status-agent-governance/fixtures/codex_event.json
grep -c secret-value-for-redaction status/runtime/agent-hook-events.jsonl   # 必须是 0
```
