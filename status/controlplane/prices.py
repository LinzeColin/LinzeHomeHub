"""Strict validation for the rebuildable status prices projection."""
from __future__ import annotations
from datetime import date
import math
from typing import Any

CURRENCIES = {"AUD", "USD", "CNY", "EUR", "SGD", "GBP", "HKD", "JPY"}
ALLOWED_ITEM_KEYS = {"name", "amount", "currency", "cadence", "auto_renew", "purchase", "track_renew", "note"}


def _strict_bool(value: Any, field: str, default: bool) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} 必须是布尔值")
    return value


def validate_prices(payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("请求必须是对象")
    if set(payload) - {"items", "schema_version", "note", "revision"}:
        raise ValueError("请求包含未允许的顶层字段")
    items = payload.get("items")
    if not isinstance(items, list) or len(items) > 50:
        raise ValueError("开支项必须是最多 50 项的数组")
    clean=[]; seen=set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("开支项必须是对象")
        unknown=set(item)-ALLOWED_ITEM_KEYS
        if unknown:
            raise ValueError("开支项包含未允许字段："+",".join(sorted(unknown)))
        name=str(item.get("name","")).strip()
        if not name or len(name)>60:
            raise ValueError("名称不能为空且最多 60 字")
        folded=name.casefold()
        if folded in seen:
            raise ValueError("开支项名称不得重复")
        seen.add(folded)
        raw_amount=item.get("amount",0)
        if isinstance(raw_amount,bool):
            raise ValueError("金额必须是数字")
        try:
            amount=round(float(raw_amount),4)
        except (TypeError,ValueError) as exc:
            raise ValueError("金额必须是数字") from exc
        if not math.isfinite(amount) or amount<0 or amount>10_000_000:
            raise ValueError("金额超出允许范围")
        currency=str(item.get("currency","AUD")).upper()
        if currency not in CURRENCIES:
            raise ValueError("币种不在允许列表")
        cadence=str(item.get("cadence","monthly"))
        if cadence not in {"monthly","yearly"}:
            raise ValueError("周期必须是 monthly 或 yearly")
        purchase=str(item.get("purchase","")).strip()
        if purchase:
            try: date.fromisoformat(purchase)
            except ValueError as exc: raise ValueError("购买日必须是真实 YYYY-MM-DD 日期") from exc
        note=str(item.get("note","")).strip()
        if len(note)>80:
            raise ValueError("备注最多 80 字")
        row={"name":name,"amount":amount,"currency":currency,"cadence":cadence,
             "auto_renew":_strict_bool(item.get("auto_renew"),"auto_renew",False)}
        if purchase:
            row["purchase"]=purchase
            row["track_renew"]=_strict_bool(item.get("track_renew"),"track_renew",True)
        elif "track_renew" in item and item.get("track_renew") is not None:
            _strict_bool(item.get("track_renew"),"track_renew",True)
        if note: row["note"]=note
        clean.append(row)
    return {"schema_version":1,"note":"月度开支价格库；长期完成事实同步到 Private-Database，本地仅为可重建投影。","items":clean}
