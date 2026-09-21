# -*- coding: utf-8 -*-
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_tool(name):
    path = ROOT / "tools" / name
    spec = importlib.util.spec_from_file_location(name[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prop_owner_name_becomes_component_parent():
    mod = load_tool("asset_relations.py")
    data, issues = mod.normalize_asset_relations({
        "characters": [{"id": "baixiaozhuxu", "name": "白咲蛛绪"}],
        "scenes": [{"id": "room", "name": "教室"}],
        "props": [{"id": "yinbai", "name": "银白蜘蛛步足", "owner": "白咲蛛绪"}],
    })
    prop = data["props"][0]
    assert not issues
    assert prop["parent_ref"] == "@character:baixiaozhuxu"
    assert prop["relation"] == "component_of"


def test_scene_variant_and_children_index():
    mod = load_tool("asset_relations.py")
    data, issues = mod.normalize_asset_relations({
        "characters": [],
        "scenes": [
            {"id": "room", "name": "教室"},
            {"id": "room_empty", "name": "空教室", "variant_of": "room"},
        ],
        "props": [{"id": "desk", "name": "课桌", "parent": "@scene:room"}],
    })
    assert not issues
    assert data["scenes"][1]["derived_from"] == "@scene:room"
    assert data["scenes"][1]["parent_ref"] == "@scene:room"
    assert data["scenes"][1]["relation"] == "derived_from"
    rows = mod.build_relation_index([
        {"ref": "@scene:room", "parent_ref": None},
        {"ref": "@scene:room_empty", "parent_ref": "@scene:room"},
        {"ref": "@prop:desk", "parent_ref": "@scene:room"},
    ])
    root = next(row for row in rows if row["ref"] == "@scene:room")
    assert root["children_refs"] == ["@prop:desk", "@scene:room_empty"]


def test_parent_cycle_is_broken():
    mod = load_tool("asset_relations.py")
    data, issues = mod.normalize_asset_relations({
        "characters": [
            {"id": "a", "name": "甲", "parent_ref": "@character:b"},
            {"id": "b", "name": "乙", "parent_ref": "@character:a"},
        ],
        "scenes": [],
        "props": [],
    })
    assert issues
    assert not (data["characters"][0].get("parent_ref") == "@character:b" and data["characters"][1].get("parent_ref") == "@character:a")


def test_merge_assets_persists_normalized_relations():
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "主角"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{"id": "sword", "name": "剑", "owner": "hero"}]},
        "E1",
    )
    prop = result["props"][0]
    assert prop["parent_ref"] == "@character:hero"
    assert prop["source_episode_ids"] == ["E1"]


def test_derived_prop_uses_real_prop_parent_instead_of_actor_owner():
    """复合道具由另一道具派生时，不应因为制造者而挂到角色下面。"""
    mod = load_tool("asset_relations.py")
    data, issues = mod.normalize_asset_relations({
        "characters": [{"id": "hero", "name": "女主"}],
        "scenes": [],
        "props": [
            {"id": "bomb", "name": "遥控炸弹", "kind": "叙事"},
            {"id": "cocoon", "name": "蛛丝缓冲茧", "kind": "组件",
             "owner": "hero", "parent_ref": "@character:hero",
             "derived_from": "@prop:bomb", "relation": "component_of"},
        ],
    })
    cocoon = next(item for item in data["props"] if item["id"] == "cocoon")
    assert not issues
    assert cocoon["parent_ref"] == "@prop:bomb"
    assert cocoon["relation"] == "derived_from"

def test_merge_assets_rejects_character_hair_style_as_prop():
    """发型属于人物外观，不应因为有一个轻微动作就生成独立道具。"""
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "女主"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{
            "id": "hero_hair", "name": "白色双马尾", "kind": "组件",
            "owner": "hero", "parent_ref": "@character:hero",
            "actions": ["随动作轻晃"], "asset_required": True,
        }]},
        "E1",
    )
    assert result["props"] == []


def test_attached_antenna_is_kept_only_when_independently_staged():
    """角色触角只有有明确动作和镜头关注时才作为角色子素材保留。"""
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "女主"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{
            "id": "hero_antenna", "name": "族群触角", "kind": "组件",
            "owner": "hero", "parent_ref": "@character:hero",
            "actions": ["紧张时颤动"], "shot_hint": "触角颤动特写",
            "asset_required": True,
        }]},
        "E1",
    )
    prop = result["props"][0]
    assert prop["parent_ref"] == "@character:hero"
    assert prop["relation"] == "component_of"


def test_attached_antenna_without_shot_focus_stays_in_character():
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "女主"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{
            "id": "hero_antenna", "name": "族群触角", "kind": "组件",
            "owner": "hero", "parent_ref": "@character:hero",
            "actions": ["长在头顶"], "asset_required": True,
        }]},
        "E1",
    )
    assert result["props"] == []

def test_unowned_antenna_is_not_a_narrative_prop():
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "女主"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{
            "id": "unknown_antenna", "name": "蚁族触角", "kind": "叙事",
            "actions": ["出现在画面"], "shot_hint": "触角可见",
            "asset_required": True,
        }]},
        "E1",
    )
    assert result["props"] == []

def test_component_with_unknown_owner_is_removed_after_relation_normalization():
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [{"id": "hero", "name": "女主"}], "scenes": [], "props": []},
        {"characters": [], "scenes": [], "props": [{
            "id": "unknown_antenna", "name": "蚁族触角", "kind": "组件",
            "owner": "not_a_character", "actions": ["颤动"],
            "shot_hint": "触角特写", "asset_required": True,
        }]},
        "E1",
    )
    assert result["props"] == []

def test_removed_orphan_component_is_not_left_in_related_refs():
    mod = load_tool("script_repository.py")
    result = mod.merge_assets(
        {"characters": [], "scenes": [], "props": [{
            "id": "device", "name": "装置", "kind": "叙事",
            "actions": ["启动"], "shot_hint": "装置特写",
            "related_refs": ["@prop:orphan_part"],
        }]},
        {"characters": [], "scenes": [], "props": [{
            "id": "orphan_part", "name": "装置旋钮", "kind": "组件",
            "owner": "missing_parent", "actions": ["转动"],
            "shot_hint": "旋钮特写",
        }]},
        "E1",
    )
    device = next(item for item in result["props"] if item["id"] == "device")
    assert "related_refs" not in device


def test_asset_extraction_gate_requires_evidence_and_repairs_derived_parent():
    """提炼候选必须能回指原文，复合道具关系在写库前就被纠正。"""
    import sys
    sys.path.insert(0, str(ROOT / "workbench" / "tools"))
    import creation_pipeline

    catalog = {
        "characters": [{"id": "hero", "name": "女主"}],
        "scenes": [],
        "props": [{"id": "bomb", "name": "遥控炸弹"}],
    }
    data, rejected = creation_pipeline._gate_extracted_data(
        "道具",
        {"props": [
            {"id": "cocoon", "name": "蛛丝缓冲茧", "kind": "组件",
             "owner": "hero", "parent_ref": "@character:hero",
             "derived_from": "@prop:bomb", "evidence_ids": ["EV001"]},
            {"id": "hair", "name": "白色双马尾", "kind": "组件",
             "owner": "hero", "parent_ref": "@character:hero"},
        ]},
        "蛛丝缓冲茧包裹遥控炸弹。",
        catalog,
    )
    assert len(rejected) == 1
    prop = data["props"][0]
    assert prop["parent_ref"] == "@prop:bomb"
    assert prop["relation"] == "derived_from"
    assert prop["evidence_status"] == "verified"
