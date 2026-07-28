#!/usr/bin/env python3
"""AI 供应商账务探针 —— 只读账单端点,绝不碰推理端点。

★ 这是整个 status 里**唯一**允许出现 `api.openai.com` 的文件。
  零 Agent 不变量(INV-001)禁止生产运行期调用模型;而查账单不是调模型 ——
  就像查银行余额不等于转账。但「不等于」不能靠口头保证,所以:

    · 域名只在这里出现,别处出现即违规(policy_scan 按文件范围判);
    · 路径走白名单,推理路径(/chat/completions 等)在**任何**文件里出现都算违规;
    · 请求方法只允许 GET —— 推理都是 POST,方法这一层再卡一道。

  放宽一个安全守卫时,必须同时留下「放宽到哪为止」的机器判据,
  否则下一个人只会看到「openai 域名是允许的」。

★ 关于「余额」这个词要如实:
    DeepSeek  有 /user/balance,给的是**真余额**(还剩多少);
    OpenAI    官方没有公开的剩余余额端点,只有 organization costs(**已花费**)。
  所以 OpenAI 这条只报花费,并在 kind 上标明。把花费当余额显示就是假数据。

★ 取不到就如实说取不到,绝不编一个数:
    未配置 key 文件      -> state=unconfigured(不是错误,是还没接)
    HTTP 非 2xx          -> state=failed,带上状态码
    返回结构不认识       -> state=unknown_shape(**不猜字段**)
  这三种都不会显示成一个数字,因为「显示了一个错的余额」比「显示不知道」更糟。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta
import urllib.error
import urllib.request

TIMEOUT = 12

# 只读账务端点白名单。键是供应商,值是 (URL, 结果类型)。
# 结果类型 balance = 还剩多少;spend = 已花多少。两者不可混为一谈。
ENDPOINTS = {
    "deepseek": ("https://api.deepseek.com/user/balance", "balance"),
    "openai": ("https://api.openai.com/v1/organization/costs", "spend"),
}

# 推理路径黑名单 —— 出现在任何文件里都算违规,由 policy_scan 强制。
# 这里同时做运行期自检:万一 URL 被改成推理路径,探针自己先拒绝发请求。
INFERENCE_PATHS = (
    "/chat/completions", "/completions", "/responses", "/embeddings",
    "/images/generations", "/audio/", "/assistants", "/threads", "/fine_tuning",
)

KEY_FILES = {
    "deepseek": os.environ.get("LINZE_DEEPSEEK_KEY_FILE", "/srv/linze/secrets/deepseek_api_key"),
    "openai": os.environ.get("LINZE_OPENAI_ADMIN_KEY_FILE", "/srv/linze/secrets/openai_admin_key"),
}


def _refuse_if_inference(url: str) -> str | None:
    """运行期自检:URL 落到推理路径就拒发。守卫不能只活在测试里。"""
    lowered = url.lower()
    for bad in INFERENCE_PATHS:
        if bad in lowered:
            return f"URL 命中推理路径 {bad},拒绝发送(本探针只允许账单端点)"
    return None


def _read_key(path: str) -> str | None:
    """只读文件路径拿 key。值从不进日志、不进快照、不进返回结构。"""
    try:
        with open(path, encoding="utf-8") as handle:
            value = handle.read().strip()
        return value or None
    except OSError:
        return None


def _get_json(url: str, token: str) -> tuple[int, object]:
    request = urllib.request.Request(url, method="GET")   # 只允许 GET
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception:
        return 0, None


def _parse_deepseek(payload: object) -> tuple[float | None, str | None]:
    """DeepSeek: {"is_available":bool,"balance_infos":[{"currency":"CNY","total_balance":"…"}]}"""
    if not isinstance(payload, dict):
        return None, None
    infos = payload.get("balance_infos")
    if not isinstance(infos, list) or not infos:
        return None, None
    first = infos[0]
    if not isinstance(first, dict):
        return None, None
    raw = first.get("total_balance")
    try:
        return float(raw), str(first.get("currency") or "")
    except (TypeError, ValueError):
        return None, None


def _parse_openai_costs(payload: object) -> tuple[float | None, str | None]:
    """OpenAI costs: {"data":[{"results":[{"amount":{"value":…,"currency":"usd"}}]}]}"""
    if not isinstance(payload, dict):
        return None, None
    buckets = payload.get("data")
    if not isinstance(buckets, list):
        return None, None
    total, currency = 0.0, None
    seen = False
    for bucket in buckets:
        for result in (bucket.get("results") if isinstance(bucket, dict) else None) or []:
            amount = result.get("amount") if isinstance(result, dict) else None
            if not isinstance(amount, dict):
                continue
            try:
                total += float(amount.get("value"))
                seen = True
            except (TypeError, ValueError):
                continue
            currency = currency or str(amount.get("currency") or "")
    return (total, currency) if seen else (None, None)


PARSERS = {"deepseek": _parse_deepseek, "openai": _parse_openai_costs}


def probe_vendor(vendor: str) -> dict:
    url, kind = ENDPOINTS[vendor]
    refusal = _refuse_if_inference(url)
    if refusal:
        return {"vendor": vendor, "kind": kind, "state": "refused", "note": refusal, "amount": None}

    key_path = KEY_FILES[vendor]
    token = _read_key(key_path)
    if token is None:
        return {"vendor": vendor, "kind": kind, "state": "unconfigured", "amount": None,
                "note": f"未配置 · 把 key 放到 {key_path}(值不进仓库)"}

    request_url = url
    if vendor == "openai":
        # costs 端点要时间窗;取本月初到现在
        now = datetime.now(timezone.utc)
        start = int(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
        request_url = f"{url}?start_time={start}&limit=31"

    status, payload = _get_json(request_url, token)
    if status == 0:
        return {"vendor": vendor, "kind": kind, "state": "failed", "amount": None,
                "note": "网络不可达或超时"}
    if not 200 <= status < 300:
        hint = {401: "凭据无效或权限不足", 403: "无权访问该账务端点(OpenAI 需 admin key)",
                404: "端点不存在(供应商 API 可能已变更)", 429: "被限流"}.get(status, "")
        return {"vendor": vendor, "kind": kind, "state": "failed", "amount": None,
                "note": f"HTTP {status}{' · ' + hint if hint else ''}"}

    amount, currency = PARSERS[vendor](payload)
    if amount is None:
        # ★ 不猜字段。结构变了就说结构变了,显示一个错的数字比显示未知更糟。
        return {"vendor": vendor, "kind": kind, "state": "unknown_shape", "amount": None,
                "note": "取到了响应但结构不认识 · 供应商 API 可能已变更,需人工核对"}
    return {"vendor": vendor, "kind": kind, "state": "ok", "amount": round(amount, 4),
            "currency": (currency or "").upper(),
            "note": ("余额" if kind == "balance" else "本月已花费")}


def collect_ai_accounts() -> dict:
    """给采集器调用。整体状态遵循「未知不聚合成绿」。"""
    items = [probe_vendor(v) for v in ENDPOINTS]
    ok = [i for i in items if i["state"] == "ok"]
    return {
        "items": items,
        "checked_at": datetime.now(timezone.utc).astimezone(
            timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M"),
        # 有任何一条不是 ok,整体就不是 ok —— 不许被 ok 的那条盖过去
        "all_ok": len(ok) == len(items) and bool(items),
    }


if __name__ == "__main__":
    print(json.dumps(collect_ai_accounts(), ensure_ascii=False, indent=2))
