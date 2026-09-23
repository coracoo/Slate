# -*- coding: utf-8 -*-
"""项目级制作规格（ProductionBrief，E05）：projects/<项目>/剧本/brief.json

制片决策（单集时长/画幅/题材基调/对白密度/人物场景上限）是项目级数据，
全流程消费，而不是写死在提示词自由文本里：
  - prompt_modules.outline_prompt / expand_episode_prompt：单集时长文案、冲突节奏、
    对白密度字数档位、题材基调段（proj 传入且 brief.json 存在时生效）
  - production_requests / production_jobs：S/V 生图画幅缺省值（image_options.ratio）
  - production_studio.state：V 总时长超过单集目标时给出提示（只提示，不改分组算法——E06 另案）

字段全部可选：brief.json 缺失或字段缺省时 load_brief 返回完整默认值，消费方行为与旧版一致。
路由：GET/POST /api/script/brief（workbench/server.py）；前端入口在剧本页的「制作规格」卡片。

用法:
  from brief import load_brief, save_brief, has_brief, aspect_ratio_of
  b = load_brief(proj)                    # 缺文件返回完整默认
  save_brief(proj, {"episode_minutes": 7})  # 合并补丁；值 None = 删除该键恢复默认
"""
import sys, os, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
CORE_TOOLS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "previs_system", "tools"))
if CORE_TOOLS not in sys.path:
    sys.path.insert(0, CORE_TOOLS)

import project_store
import versions

# 字段默认值（全部可选；缺省 = 旧版硬编码行为）
DEFAULTS = {
    "episode_minutes": 3,       # 单集目标时长（分钟）
    "total_episodes": None,     # 目标集数（可空）
    "aspect_ratio": "16:9",     # 画幅
    "genre_tone": "",           # 题材与基调（自由文本）
    "dialogue_density": "中",   # 对白密度
    "max_characters": None,     # 主要人物数上限（可空）
    "max_scenes": None,         # 主要场景数上限（可空）
    "plan_refs": True,          # V 视频编译时自动注入平面图参考帧（项目级开关，请求体 plan_refs 可覆盖）
}

EPISODE_MINUTES_RANGE = (0.5, 10)
ASPECT_CHOICES = ("16:9", "9:16", "1:1", "4:3")
DENSITY_CHOICES = ("低", "中", "高")
GENRE_TONE_MAX = 200


def brief_path(proj):
    return os.path.join(os.fspath(proj), "剧本", "brief.json")


def has_brief(proj):
    """brief.json 是否存在——「用户配置过」与「纯默认」的分界（缺省路径保持旧行为）。"""
    return os.path.isfile(brief_path(proj))


def load_brief(proj):
    """读取制作规格并补齐默认值；文件缺失/损坏时返回完整默认（消费方零感知）。
    只认契约内字段：未知键不透出，防止脏数据流进提示词与导出规格。"""
    data = {}
    path = brief_path(proj)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                data = raw
        except (OSError, ValueError):
            data = {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def _number(value, key):
    """数字字段清洗：拒绝布尔与不可解析值，返回 float/int。"""
    if isinstance(value, bool):
        raise ValueError(f"{key} 必须是数字")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 必须是数字") from None


def _validate(patch):
    """逐字段校验补丁；值 None 表示恢复默认（不落盘该键）。非法值抛 ValueError。"""
    clean = {}
    for key, value in patch.items():
        if key not in DEFAULTS:
            raise ValueError(f"制作规格不支持字段：{key}（可选：{'、'.join(DEFAULTS)}）")
        if value is None:
            clean[key] = None
            continue
        if key == "episode_minutes":
            v = _number(value, key)
            if not EPISODE_MINUTES_RANGE[0] <= v <= EPISODE_MINUTES_RANGE[1]:
                raise ValueError(f"episode_minutes 须在 {EPISODE_MINUTES_RANGE[0]:g}~{EPISODE_MINUTES_RANGE[1]:g} 分钟之间")
            clean[key] = int(v) if v.is_integer() else v
        elif key in ("total_episodes", "max_characters", "max_scenes"):
            v = _number(value, key)
            if not v.is_integer() or v < 1:
                raise ValueError(f"{key} 须为 ≥1 的整数（留空恢复默认）")
            clean[key] = int(v)
        elif key == "aspect_ratio":
            v = str(value).strip()
            if v not in ASPECT_CHOICES:
                raise ValueError(f"aspect_ratio 只能是 {'/'.join(ASPECT_CHOICES)}")
            clean[key] = v
        elif key == "dialogue_density":
            v = str(value).strip()
            if v not in DENSITY_CHOICES:
                raise ValueError(f"dialogue_density 只能是 {'/'.join(DENSITY_CHOICES)}")
            clean[key] = v
        elif key == "genre_tone":
            v = str(value).strip()
            if len(v) > GENRE_TONE_MAX:
                raise ValueError(f"genre_tone 不超过 {GENRE_TONE_MAX} 字")
            clean[key] = v
        elif key == "plan_refs":
            clean[key] = bool(value)
    return clean


def save_brief(proj, patch):
    """合并补丁写 brief.json（project_store 锁内原子写 + versions 快照，与既有 JSON 产出同惯例）。
    patch 值为 None 表示删除该键（恢复默认）；返回合并默认值后的完整规格。"""
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")
    clean = _validate(patch)
    path = brief_path(proj)

    def mutate(data):
        for key, value in clean.items():
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
        # 已下线字段（resolution/fps 等旧键）与未知键不再写回
        for key in list(data):
            if key not in DEFAULTS:
                data.pop(key)

    project_store.update_json(path, mutate, create_default={}, snapshot=versions.snapshot)
    return load_brief(proj)


def aspect_ratio_of(proj):
    """生图画幅缺省值：brief 有配置用配置，否则 16:9（与旧版硬编码一致）。"""
    try:
        return str(load_brief(proj).get("aspect_ratio") or "16:9")
    except Exception:
        return "16:9"


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        print(json.dumps(load_brief(sys.argv[1]), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
