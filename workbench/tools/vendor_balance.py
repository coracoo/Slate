# -*- coding: utf-8 -*-
"""厂商账户余额查询（仅支持官方提供余额 API 的厂商；其余返回不支持说明）。

已接入：
- runninghub：POST /uc/openapi/accountStatus（apiKey 放 body；企业/共享 key 扣企业钱包，
  remainCoins 是个人 RH 币，与 812 报错的钱包可能不是同一个池子——见 data.apiType）。
- openai-compat 系：GET {base}/dashboard/billing/credit_grants（OpenAI 早期开放，
  多数兼容网关不实现；total_usage_migrated 之类缺失按不支持处理）。
"""
import json
import urllib.request
import urllib.error


def _error(msg):
    raise ValueError(msg)


def _request(url, *, method="GET", headers=None, body=None, timeout=15):
    data = json.dumps(body).encode() if body is not None and method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def query(vendor_cfg):
    """返回 {ok, text, detail?}；不支持余额 API 的厂商给出控制台指引。"""
    vid = str(vendor_cfg.get("id") or "")
    key = str(vendor_cfg.get("api_key") or "").strip()
    base = str(vendor_cfg.get("base_url") or "").rstrip("/")
    if vid == "runninghub":
        if not key:
            return {"ok": False, "text": "未配置 API Key"}
        d = _request("https://www.runninghub.ai/uc/openapi/accountStatus",
                     method="POST", body={"apiKey": key},
                     headers={"Authorization": "Bearer " + key})
        data = d.get("data") or {}
        code = d.get("code")
        if code not in (0, "0"):
            hint = {"811": "该端点只支持个人 API Key；当前是企业/共享 key，无法查询（扣费走企业钱包，余额在网页控制台 Tasks & Billing 查看）",
                    "812": "企业钱包余额不足（与生图 812 同源，需控制台充值）"}.get(str(code), "")
            return {"ok": False, "text": f"查询失败：{d.get('msg')}" + (f"——{hint}" if hint else "")}
        coins = data.get("remainCoins")
        money = data.get("remainMoney")
        api_type = data.get("apiType") or ""
        parts = []
        if coins is not None:
            parts.append(f"个人 RH 币 {coins}")
        if money is not None:
            parts.append(f"现金 {money}")
        text = " · ".join(parts) or "无余额数据"
        if api_type == "SHARED":
            text += "（该查询为个人钱包；企业 key 的扣费走企业钱包，812=企业钱包不足）"
        return {"ok": True, "text": text, "detail": data}
    if vid in ("doubao", "doubao-api", "minimax", "qwen", "aliyun", "gemini",
               "kling", "agnes", "kimi", "glm", "deepseek"):
        console = {
            "doubao": "火山引擎控制台-费用中心", "doubao-api": "火山引擎控制台-费用中心",
            "minimax": "MiniMax 平台-账户中心", "qwen": "阿里云百炼-费用账单",
            "aliyun": "阿里云百炼-费用账单", "gemini": "Google AI Studio- Billing",
            "kling": "可灵平台-账户中心", "agnes": "Agnes 平台控制台",
            "kimi": "Moonshot 平台-财务", "glm": "智谱开放平台-财务中心",
            "deepseek": "DeepSeek 平台-充值",
        }.get(vid, "厂商控制台")
        return {"ok": False, "text": f"该厂商未开放余额 API，请在 {console} 查看"}
    if vid == "openai-compat" and base and key:
        try:
            d = _request(base + "/dashboard/billing/credit_grants",
                         headers={"Authorization": "Bearer " + key})
            total = d.get("total_granted") or (d.get("total_available") or {}).get("value")
            if total is None:
                return {"ok": False, "text": "该网关未实现余额查询（dashboard/billing）"}
            return {"ok": True, "text": f"额度 {total}", "detail": d}
        except (urllib.error.HTTPError, ValueError, KeyError):
            return {"ok": False, "text": "该网关未实现余额查询（dashboard/billing）"}
    return {"ok": False, "text": "该厂商未开放余额 API"}
