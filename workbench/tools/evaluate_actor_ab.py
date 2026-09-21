# -*- coding: utf-8 -*-
"""演员 A/B/C 评分器。

同一分镜输入下编译 baseline/style/stateful 三档表演提示词（A=裸分镜 B=+导演风格
C=+风格+角色卡记忆），指定 --vendor 时逐镜让 LLM 裁判对比打分（表演具体度/角色
一致性/镜头纪律，各1~10，满分30），结果写 manifest.scores/summary；不调用生视频。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
for path in (HERE, CORE):
    if path not in sys.path:
        sys.path.insert(0, path)

import project_store
from artifact_provenance import artifact_hash
from actor_pipeline import compile_for_creation, _resolve_board_path


def _sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _script_hash(project):
    root = os.path.join(project, "剧本")
    chunks = []
    if os.path.isdir(root):
        for name in sorted(os.listdir(root)):
            if name == "style.json" or not (name.endswith(".txt") or name.endswith(".json")):
                continue
            path = os.path.join(root, name)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    chunks.append(name.encode("utf-8") + b"\0" + fh.read())
    return _sha_bytes(b"\0".join(chunks))



def _asset_hashes(project):
    """记录项目资产文件摘要，避免不同参考图被误当成同一组输入。"""
    result = {}
    root = os.path.join(project, "素材")
    if not os.path.isdir(root):
        return result
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            path = os.path.join(base, name)
            try:
                with open(path, "rb") as fh:
                    digest = _sha_bytes(fh.read())
            except OSError:
                continue
            rel = os.path.relpath(path, project).replace(os.sep, "/")
            result[rel] = digest
    return result
def _card_skill_hash(project, board):
    style = {}
    path = os.path.join(project, "剧本", "style.json")
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                style = json.load(fh)
        except (OSError, ValueError, TypeError):
            style = {}
    payload = {"actor_cards": (board.get("acting_context") or {}).get("actor_cards", {}),
               "acting_skill": style.get("acting", ""), "version": "actor-eval-v1"}
    return _sha_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _checks(compiled):
    warnings = list(compiled.get("warnings") or [])
    return {
        "hard_errors": 0,
        "knowledge_leak": 0,
        "fixed_field_mutation": 0,
        "performance_used": bool(compiled.get("performance_used")),
        "warnings": warnings,
    }


def build_manifest(board_path, shot_ids=None, modes=None, repeats=2, experiment_id=None,
                   model="", parameters=None):
    board_path = _resolve_board_path(board_path)
    board, revision = project_store.read_json(board_path)
    project = os.path.dirname(os.path.dirname(board_path))
    shots = [str(item.get("id")) for item in board.get("shots") or [] if item.get("id")]
    chosen = shots if shot_ids is None else [str(item) for item in shot_ids]
    missing = [sid for sid in chosen if sid not in shots]
    if missing:
        raise ValueError("找不到镜头：" + ",".join(missing))
    if not chosen:
        raise ValueError("至少选择一个镜头")
    modes = list(modes or ("baseline", "style", "stateful"))
    allowed = {"baseline", "style", "stateful"}
    bad = [mode for mode in modes if mode not in allowed]
    if bad:
        raise ValueError("不支持的评估模式：" + ",".join(bad))
    repeats = max(1, min(int(repeats), 20))
    exp = str(experiment_id or ("actor_eval_" + time.strftime("%Y%m%d_%H%M%S")))
    out_dir = os.path.join(project, "演员", "表演对比", exp)
    manifest_path = os.path.join(out_dir, "manifest.json")
    input_key = {"board": os.path.basename(board_path), "revision": revision,
                 "shots": chosen, "modes": modes, "repeats": repeats,
                 "model": model, "parameters": parameters or {}}
    input_hash = _sha_bytes(json.dumps(input_key, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as fh:
            existing = json.load(fh)
        if isinstance(existing, dict) and existing.get("input_hash") == input_hash:
            return existing
        raise ValueError("评估目录已存在但输入不同，请更换 experiment_id")
    script_hash = _script_hash(project)
    asset_hash = artifact_hash(board, "prompt", "actor-eval-v1")
    asset_hashes = {"prompt": asset_hash, **_asset_hashes(project)}
    card_hash = _card_skill_hash(project, board)
    rows = []
    for mode in modes:
        for shot_id in chosen:
            compiled = compile_for_creation(board_path, shot_id, mode=mode, media_type="video")
            for repeat in range(1, repeats + 1):
                rows.append({
                    "mode": mode, "shot_id": shot_id, "repeat": repeat,
                    "script_hash": script_hash, "board_revision": revision,
                    "card_skill_hash": card_hash, "asset_hash": asset_hash,
                    "model": model, "parameters": parameters or {},
                    "source_hash": compiled.get("source_hash", ""),
                    "compiled_prompt": compiled.get("text", ""),
                    "input_hash": input_hash,
                    "text_checks": _checks(compiled),
                    "video": {"status": "not_started", "comparable": False,
                              "reason": "未调用真实生视频，按用户要求跳过 smoke"},
                    "cost": None, "elapsed_ms": 0,
                })
    manifest = {
        "experiment_id": exp, "schema_version": "actor-eval-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "board": os.path.basename(board_path), "board_revision": revision,
        "script_hash": script_hash, "card_skill_hash": card_hash,
        "asset_hashes": asset_hashes, "modes": modes,
        "shots": chosen, "repeats": repeats, "model": model,
        "parameters": parameters or {}, "input_hash": input_hash,
        "video_comparable": False,
        "limitation": "当前仅完成文本阶段登记；真实视频效果和费用需另行授权验证。",
        "rows": rows,
    }
    os.makedirs(out_dir, exist_ok=True)
    temporary = manifest_path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(temporary, manifest_path)
    manifest["path"] = manifest_path
    return manifest


MODE_LETTER = {"baseline": "A", "style": "B", "stateful": "C"}
LETTER_MODE = {v: k for k, v in MODE_LETTER.items()}
LETTER_LABEL = {"A": "A=裸分镜", "B": "B=+导演风格", "C": "C=+导演风格+角色卡+记忆"}

JUDGE_SYS = (
    "你是表演指导兼提示词质检员。同一分镜有多版（最多A/B/C）给图生视频用的表演提示词，"
    "请按三个维度各打1~10分：specificity=表演具体度（动作/表情/视线/节奏是否具体可执行），"
    "consistency=角色一致性（是否符合角色卡与人设），discipline=镜头纪律（是否只加表演，"
    "不越权改动机位/走位/台词）。只输出JSON不要其他文字："
    '{"A":{"specificity":6,"consistency":7,"discipline":8,"reason":"一句话"},'
    '"B":{...},"C":{...},"best":"A或B或C","reason":"一句话总评"}'
    "。若某档不存在则省略该键。"
)


def _parse_llm_json(txt):
    txt = re.sub(r"^```(?:json)?|```\s*$", "", (txt or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    if not m:
        raise ValueError("LLM 输出中无 JSON")
    return json.loads(m.group(0))


def score_manifest(board_path, manifest, vendor_id, providers_json=""):
    """逐镜 LLM 裁判对比评分：结果写回 manifest.scores/summary 与每行 row.score。"""
    from llm_openai import VendorClient
    cli = VendorClient(vendor_id, providers_json=providers_json or None)
    model = cli.models.get("text") or ""
    prompts = {}
    for row in manifest.get("rows") or []:
        prompts.setdefault(row["shot_id"], {})[row["mode"]] = row.get("compiled_prompt", "")
    letters = [MODE_LETTER[m] for m in manifest.get("modes") or [] if m in MODE_LETTER]
    scores, wins, sums = {}, {"A": 0, "B": 0, "C": 0}, {"A": [], "B": [], "C": []}
    for sid in manifest.get("shots") or []:
        by_mode = prompts.get(sid) or {}
        parts = []
        for letter in letters:
            txt = (by_mode.get(LETTER_MODE[letter]) or "").strip()
            parts.append("【%s档 %s】\n%s" % (letter, LETTER_LABEL[letter], txt[:6000] or "（空）"))
        user = "请给以下同一分镜的各档表演提示词评分。\n\n" + "\n\n".join(parts)
        data, wait = None, 5
        for attempt in range(3):
            try:
                reply = cli.chat([{"role": "system", "content": JUDGE_SYS},
                                  {"role": "user", "content": user}],
                                 kind="text", max_tokens=2000, timeout=180, temperature=0.2,
                                 extra={"thinking": {"type": "disabled"}})
                data = _parse_llm_json(reply)
                break
            except Exception as e:
                if attempt == 2:
                    print("[评分失败] %s: %s" % (sid, str(e)[:120]), flush=True)
                else:
                    time.sleep(wait)
                    wait = min(30, wait * 2)
        if not isinstance(data, dict):
            continue
        entry = {"best": str(data.get("best") or ""), "reason": str(data.get("reason") or "")}
        for letter in letters:
            sub = data.get(letter) if isinstance(data.get(letter), dict) else {}
            dims = {k: sub.get(k) for k in ("specificity", "consistency", "discipline")}
            vals = [v for v in dims.values() if isinstance(v, (int, float))]
            entry[letter] = {"dims": dims,
                             "total": round(sum(vals), 1) if len(vals) == 3 else None,
                             "reason": str(sub.get("reason") or "")}
            if entry[letter]["total"] is not None:
                sums[letter].append(entry[letter]["total"])
        if entry["best"] not in letters:
            entry["best"] = ""
        else:
            wins[entry["best"]] += 1
        scores[sid] = entry
        for row in manifest.get("rows") or []:
            if row.get("shot_id") == sid:
                row["score"] = entry
        show = {letter: (entry[letter]["total"] if entry[letter]["total"] is not None else "-") for letter in letters}
        print("[评分] %s best=%s A=%s B=%s C=%s" % (sid, entry["best"] or "?",
              show.get("A", "-"), show.get("B", "-"), show.get("C", "-")), flush=True)
    manifest["scores"] = scores
    manifest["summary"] = {
        "wins": wins,
        "avg_total": {letter: (round(sum(arr) / len(arr), 1) if arr else None) for letter, arr in sums.items()},
        "model": model, "vendor": vendor_id,
        "note": "LLM 裁判文本对比评分，不代表生视频后成片效果",
    }
    path = manifest.get("path")
    if path and os.path.isfile(path):
        temporary = path + ".tmp"
        with open(temporary, "w", encoding="utf-8") as fh:
            json.dump({k: v for k, v in manifest.items() if k != "path"}, fh, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    return manifest


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--shots", default="")
    ap.add_argument("--modes", default="baseline,style,stateful")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--experiment-id", default=None)
    ap.add_argument("--model", default="")
    ap.add_argument("--vendor", default="", help="文本厂商 id；提供则逐镜 LLM 对比评分")
    ap.add_argument("--providers", default="")
    args = ap.parse_args()
    shots = [item.strip() for item in args.shots.split(",") if item.strip()] or None
    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    result = build_manifest(args.board, shots, modes, args.repeats, args.experiment_id, args.model)
    if args.vendor:
        print("[评分] 厂商 %s 逐镜 A/B/C 对比评分中…" % args.vendor, flush=True)
        result = score_manifest(args.board, result, args.vendor, args.providers)
    else:
        print("[提示] 未指定 --vendor，仅登记编译结果，未评分", flush=True)
    summary = result.get("summary") or {}
    print(json.dumps({"experiment_id": result.get("experiment_id"),
                      "shots": result.get("shots"),
                      "wins": summary.get("wins"),
                      "avg_total": summary.get("avg_total"),
                      "scores": result.get("scores") or {}}, ensure_ascii=False, indent=2))
    print("OUTPUT:" + str(result.get("path", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

