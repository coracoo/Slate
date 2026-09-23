# -*- coding: utf-8 -*-
"""平面图判官（plan v1 第四节：语义闸，TypeSafe/JEV 三型裁决）

validate_plan 管"合不合法"，本模块管"像不像、合不合理"。三型问题清单：
  Noul（是/否）：路径不穿家具 / 门窗与场景描述一致 / 陈设不悬空不违反常识
  Choice（枚举）：整体布局 = 合理 / 勉强 / 混乱
  Score（有序档）：与场景描述一致度 = 低 / 中 / 高
裁决：任一 Noul=False 或 Choice=混乱 或 Score=低 → 打回（带原因），由 gen_plan 封顶重生成。

双后端（可插拔）：
  ① typesafe：配了 TYPESAFE_API_KEY 且 typesafe-sdk 可导入时走 SDK 的 system_one
     （三型原生）。SDK 未装/导入失败/调用失败一律降级默认后端并在返回 note 注明。
  ② vendor（默认）：providers.json 第一个已启用 text 厂商（llm_openai VendorClient），
     关思考 extra + 提示词内 JSON schema 约束 + parse_structured 容错（creation_pipeline 模式）。

用法:
  from judge_plan import judge
  r = judge(plan, scene_desc="军帐，长案居中……")   # -> {"ok","backend","result","reasons","note"}
"""
import sys, os, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

FAST_THINK = {"thinking": {"type": "disabled"}}   # 结构化 JSON 任务关思考（creation_pipeline 同款）

# 三型问题清单（id 稳定，后端输出按 id 对齐）
QUESTIONS = {
    "noul": [
        {"id": "path_clear", "q": "所有运动路径是否没有穿过家具陈设？"},
        {"id": "openings_match", "q": "门窗（openings）位置与数量是否与场景描述一致？"},
        {"id": "props_grounded", "q": "陈设是否都落地合理、没有悬空或明显违反常识的摆放？"},
    ],
    "choice": {"id": "layout", "q": "整体布局评价", "options": ["合理", "勉强", "混乱"]},
    "score": {"id": "consistency", "q": "与场景描述的一致度", "options": ["低", "中", "高"]},
}

NOUL_PASS = True          # Noul 通过值（True=没问题）
CHOICE_REJECT = "混乱"    # Choice 打回值
SCORE_REJECT = "低"       # Score 打回值


def verdict(result):
    """三型裁决：result={"noul":{id:bool}, "choice":{"layout":str}, "score":{"consistency":str}}。
    返回 {"ok": bool, "reasons": [中文原因]}。非法/缺失值从严按打回处理。"""
    reasons = []
    noul = result.get("noul") or {}
    for item in QUESTIONS["noul"]:
        if noul.get(item["id"]) is not NOUL_PASS:
            reasons.append(f"Noul[{item['id']}] 未通过：{item['q']}")
    layout = ((result.get("choice") or {}).get("layout"))
    if layout != "合理" and layout != "勉强":
        reasons.append(f"Choice[layout] 打回：整体布局={layout!r}（期望 合理/勉强）")
    consistency = ((result.get("score") or {}).get("consistency"))
    if consistency not in ("中", "高"):
        reasons.append(f"Score[consistency] 打回：一致度={consistency!r}（期望 中/高）")
    return {"ok": not reasons, "reasons": reasons}


def pick_vendor(vendor_id=None):
    """providers.json 第一个已启用且配置 text 模型的厂商（有 key 优先）。"""
    from llm_openai import load_vendors, VendorError
    vs = [v for v in load_vendors() if v.get("enabled") and (v.get("models") or {}).get("text")]
    if vendor_id:
        vs = [v for v in vs if v["id"] == vendor_id]
    vs.sort(key=lambda v: not v.get("api_key"))
    if not vs:
        raise VendorError("没有已启用且配置 text 模型的厂商（检查 providers.json / ⑦ 环境页）")
    return vs[0]["id"]


def _question_spec_text():
    """把三型清单渲染成提示词文本 + 输出 JSON schema 示例。"""
    lines = ["逐题判定下面的平面图："]
    for item in QUESTIONS["noul"]:
        lines.append(f"- Noul[{item['id']}]（true/false，true=通过）：{item['q']}")
    c = QUESTIONS["choice"]
    lines.append(f"- Choice[{c['id']}]（{'/'.join(c['options'])} 选一）：{c['q']}")
    s = QUESTIONS["score"]
    lines.append(f"- Score[{s['id']}]（{'/'.join(s['options'])} 选一）：{s['q']}")
    schema = ('{"noul":{"path_clear":true,"openings_match":true,"props_grounded":true},'
              '"choice":{"layout":"合理"},"score":{"consistency":"高"},"reasons":["不通过项的具体原因，通过则为空数组"]}')
    return "\n".join(lines), schema


def _normalize(result):
    """后端输出归一：只留清单内 id；reasons 兜底为字符串数组。"""
    out = {"noul": {}, "choice": {}, "score": {}, "reasons": []}
    if not isinstance(result, dict):
        return out
    noul = result.get("noul") or {}
    for item in QUESTIONS["noul"]:
        v = noul.get(item["id"])
        out["noul"][item["id"]] = True if v is True else (False if v is False else None)
    choice = result.get("choice") or {}
    out["choice"]["layout"] = choice.get(QUESTIONS["choice"]["id"])
    score = result.get("score") or {}
    out["score"]["consistency"] = score.get(QUESTIONS["score"]["id"])
    rs = result.get("reasons")
    if isinstance(rs, list):
        out["reasons"] = [str(r) for r in rs][:8]
    return out


def _judge_typesafe(plan, scene_desc):
    """TypeSafe 后端：typesafe-sdk 的 system_one（三型原生：Noul/Choice/Score）。
    约定单次调用传入问题清单与平面图 JSON，返回三型结果 dict；
    typesafe-sdk 属可选依赖（本仓库不安装），真接入时按实际 SDK 签名微调本函数。"""
    import typesafe  # noqa: F401 可插拔：未安装抛 ImportError，由上层降级
    payload = {"scene_desc": scene_desc, "plan": plan}
    if hasattr(typesafe, "system_one"):
        return typesafe.system_one(questions=QUESTIONS, subject=payload)
    client = typesafe.Client(api_key=os.environ.get("TYPESAFE_API_KEY", ""))
    return client.system_one(questions=QUESTIONS, subject=payload)


def _judge_vendor(plan, scene_desc, chat_fn=None, vendor=None):
    """默认后端：providers.json text 厂商，关思考 + JSON schema 约束 + parse_structured 容错。"""
    spec, schema = _question_spec_text()
    sys_prompt = ("你是平面图评审判官。只输出 JSON，不要解释、不要 markdown 代码块。"
                  "按给定问题清单逐题判定，输出 schema：\n" + schema)
    user = (f"场景描述：{scene_desc or '（无）'}\n\n"
            f"平面图 plan.json：\n{json.dumps(plan, ensure_ascii=False)}\n\n{spec}")
    messages = [{"role": "system", "content": sys_prompt},
                {"role": "user", "content": user}]
    if chat_fn is None:
        from llm_openai import VendorClient
        cli = VendorClient(pick_vendor(vendor))
        chat_fn = lambda msgs: cli.chat(msgs, kind="text", max_tokens=1200, timeout=180,
                                        temperature=0.2, extra=FAST_THINK)
    txt = chat_fn(messages)
    from llm_result import parse_structured
    parsed = parse_structured(str(txt))
    if not parsed.get("complete") or not isinstance(parsed.get("data"), dict):
        raise ValueError("判官输出 JSON 不完整: " + "; ".join(parsed.get("repair_notes") or []))
    return parsed["data"]


def select_backend():
    """选后端：(backend, note)。配了 TYPESAFE_API_KEY 但 SDK 不可用时降级并注明。"""
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            import typesafe  # noqa: F401
            return "typesafe", None
        except Exception as exc:
            return "vendor", f"TYPESAFE_API_KEY 已配置但 typesafe-sdk 不可用（{type(exc).__name__}），降级为厂商后端"
    return "vendor", None


def judge(plan, scene_desc="", backend=None, chat_fn=None, vendor=None, log=None):
    """三型裁决入口。返回 {"ok","backend","result","reasons","note"}；
    每次判定依据经 log 输出（入任务日志）。"""
    note = None
    if backend is None:
        backend, note = select_backend()
    if backend == "typesafe":
        try:
            raw = _judge_typesafe(plan, scene_desc)
        except Exception as exc:
            note = f"typesafe 后端调用失败（{exc}），降级为厂商后端"
            backend = "vendor"
            raw = _judge_vendor(plan, scene_desc, chat_fn=chat_fn, vendor=vendor)
    else:
        raw = _judge_vendor(plan, scene_desc, chat_fn=chat_fn, vendor=vendor)
    result = _normalize(raw)
    v = verdict(result)
    reasons = v["reasons"] or (result.get("reasons") if v["ok"] else []) or []
    out = {"ok": v["ok"], "backend": backend, "result": result,
           "reasons": v["reasons"], "note": note}
    if log:
        log(f"[判官/{backend}] {'通过' if v['ok'] else '打回'}"
            + (f"：{'；'.join(v['reasons'])}" if v["reasons"] else ""))
    return out


if __name__ == "__main__":
    # CLI 冒烟：python judge_plan.py <plan.json> [--desc 场景描述]
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--desc", default="")
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    r = judge(plan, scene_desc=a.desc, log=print)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    sys.exit(0 if r["ok"] else 1)
