# -*- coding: utf-8 -*-
"""项目目录布局（与侧边栏一一对应）+ 存量项目惰性迁移。

新布局（projects/<项目>/ 下）：

  制作线                        拉片线
  ───────────────────────       ───────────────────────
  剧本/    ① 剧本生成            拉片/       ① 拉片结构
  分镜/    ② 分镜生成            台词/       ② 台词分析
  素材/    ③ 素材生成            逐帧/       ③ 逐帧拉片
  演员/    ④ 演员表现            深度/       ④ 深度动作
  创作/    ⑤ 创作生成            拉片素材/   源视频·字幕
  推演/    ⑥ 平面推演

  系统：白模/（辅助·白模）、白模3D/（辅助·Blender）

旧 → 新 迁移规则（migrate_project，幂等，可反复调用）：
  素材/          → 拉片素材/        （先执行，腾出"素材"名）
  资产/          → 素材/            （含 人物/场景/道具 子目录与 .versions）
  资产图.json    → 素材图.json      （目录迁移后改名，.versions 快照同步改干名）
  剧本/{人物,场景,道具}.json → 素材/  （三件套归入素材目录，快照跟随）
  帧/            → 逐帧/
  台词脚本.json / 台词脚本.bak.json / AI归属_台词角色.json → 台词/（快照跟随）
  创作/表演草稿_*、创作/表演对比 → 演员/
  创作/创作包_*、创作/平面图_*、创作/战略图_* → 推演/

冲突策略：新旧并存时不合并、不覆盖，记日志跳过（下次访问自动重试其余步骤）。

字符串引用改写：目录/文件迁移发生的同一轮，会把项目内所有 *.json（.versions 除外）
与 推演/战略图_*.html 里的旧路径字符串改成新路径（资产/→素材/、素材/→拉片素材/、
帧/每秒→逐帧/每秒、创作/表演*→演员/、创作/{创作包_,平面图_,战略图_}→推演/）。
改写只在对应迁移动作发生的那一轮执行（避免把新"素材/"资产路径误改成拉片素材）；
老项目若是早期版本迁移过的，可手动补跑：
  python workbench/tools/project_layout.py <项目目录> --fix-strings
--fix-strings 补改除 素材/→拉片素材/ 以外的全部规则：裸 `素材/` 在目录已迁移的项目里
就是资产路径，无法靠字面量区分，强制改写只会把可用路径打断。源视频路径请人工核对。

调用点：server.proj_dir（每次 API 访问惰性迁移）；CLI 直接跑旧项目前可先执行
  python workbench/tools/project_layout.py <项目目录>
"""
from __future__ import annotations

import os
import re
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

# 目录标准名（单一真源，其它模块应引用这里而非硬编码）
DIR_SCRIPT = "剧本"
DIR_BOARD = "分镜"
DIR_ASSET = "素材"          # ③ 素材生成（旧：资产/）
DIR_ACTOR = "演员"          # ④ 演员表现（旧：创作/表演*）
DIR_CREATE = "创作"         # ⑤ 创作生成
DIR_DEDUCE = "推演"         # ⑥ 平面推演（旧：创作/创作包_*、平面图_*、战略图_*）
DIR_LAPIAN = "拉片"         # ① 拉片结构
DIR_LINES = "台词"          # ② 台词分析（旧：根目录散文件）
DIR_FRAMES = "逐帧"         # ③ 逐帧拉片（旧：帧/）
DIR_DEPTH = "深度"          # ④ 深度动作
DIR_SRC = "拉片素材"        # 源视频/字幕（旧：素材/）
DIR_WHITE = "白模"
DIR_WHITE3D = "白模3D"

ASSET_INDEX = "素材图.json"  # 旧：资产图.json
ASSET_DOCS = ("人物.json", "场景.json", "道具.json")  # 三件套（旧：剧本/ 下）
LINES_FILES = ("台词脚本.json", "台词脚本.bak.json", "AI归属_台词角色.json")


def _rename_dir(proj: str, old: str, new: str, log: list) -> None:
    src, dst = os.path.join(proj, old), os.path.join(proj, new)
    if old == new or not os.path.isdir(src):
        return
    if os.path.exists(dst):
        log.append(f"[跳过] {old}/ 与 {new}/ 并存，未合并（请人工处理）")
        return
    os.rename(src, dst)
    log.append(f"[迁移] {old}/ → {new}/")


def _move_file(src: str, dst: str, log: list) -> bool:
    if not os.path.isfile(src):
        return False
    if os.path.exists(dst):
        log.append(f"[跳过] 目标已存在，未覆盖：{dst}")
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.move(src, dst)
    log.append(f"[迁移] {src} → {dst}")
    return True


def _move_snapshots(src_dir: str, dst_dir: str, old_stem: str, log: list, new_stem: str = "") -> None:
    """把 <src_dir>/.versions/<old_stem>.* 快照移到 <dst_dir>/.versions/<new_stem|old_stem>.*。"""
    sv = os.path.join(src_dir, ".versions")
    if not os.path.isdir(sv):
        return
    for fn in sorted(os.listdir(sv)):
        if not fn.startswith(old_stem + "."):
            continue
        dv = os.path.join(dst_dir, ".versions")
        os.makedirs(dv, exist_ok=True)
        dst = os.path.join(dv, (new_stem or old_stem) + fn[len(old_stem):])
        if not os.path.exists(dst):
            shutil.move(os.path.join(sv, fn), dst)


def _rewrite_strings(proj: str, did_src: bool, did_asset: bool, did_frames: bool,
                     did_actor: bool, did_deduce: bool, log: list) -> None:
    """把项目内 JSON/战略图 HTML 里的旧路径字符串改写为新布局。

    只在对应迁移动作发生的这一轮执行（由 migrate_project 按动作门控）——新数据里
    "素材/" 已是资产目录，无条件重写会把新路径误改成 拉片素材/。
    """
    rules = []
    if did_asset:
        rules.append(("资产/", "\x00A1/"))      # 先占位，最后还原为 素材/
        rules.append(("资产\\\\", "\x00A1\\\\"))
    if did_src:
        rules.append(("__SRC_SLASH__", ""))     # 占位标记，真正规则在下面用正则
    if did_frames:
        rules.append(("帧/每秒", "逐帧/每秒"))
        rules.append(("帧\\\\每秒", "逐帧\\\\每秒"))
    if did_actor:
        rules.append(("创作/表演草稿_", "演员/表演草稿_"))
        rules.append(("创作\\\\表演草稿_", "演员\\\\表演草稿_"))
        rules.append(("创作/表演对比", "演员/表演对比"))
        rules.append(("创作\\\\表演对比", "演员\\\\表演对比"))
    if did_deduce:
        for stem in ("创作包_", "平面图_", "战略图_"):
            rules.append(("创作/" + stem, "推演/" + stem))
            rules.append(("创作\\\\" + stem, "推演\\\\" + stem))

    re_src = re.compile(r"(?<![拉片])素材(?=/|\\\\)") if did_src else None

    def transform(text: str) -> str:
        for old, new in rules:
            if old == "__SRC_SLASH__":
                continue
            text = text.replace(old, new)
        if re_src is not None:
            text = re_src.sub("拉片素材", text)
        return text.replace("\x00A1", "素材")

    changed = 0
    for base, dirs, files in os.walk(proj):
        dirs[:] = [x for x in dirs if x != ".versions"]
        for fn in files:
            if not (fn.endswith(".json") or (fn.endswith(".html") and fn.startswith("战略图_"))):
                continue
            path = os.path.join(base, fn)
            try:
                raw = open(path, encoding="utf-8").read()
            except (OSError, UnicodeDecodeError):
                continue
            new = transform(raw)
            if new == raw:
                continue
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import versions as _V
                _V.snapshot(path)
            except Exception:
                pass
            open(path, "w", encoding="utf-8", newline="").write(new)
            changed += 1
    if changed:
        log.append(f"[迁移] 改写路径字符串引用：{changed} 个文件")


def migrate_project(proj: str, fix_strings: bool = False) -> list:
    """把旧布局项目迁移到新布局；幂等，返回执行日志（无变更时为空）。"""
    log: list = []
    if not proj or not os.path.isdir(proj):
        return log
    try:
        # 1) 目录重命名（顺序敏感：先 素材→拉片素材，再 资产→素材）。
        # 素材→拉片素材 需排除"已是新资产目录"的情况（迁移后再跑时 素材/ 已是资产），
        # 判定标记：索引/三件套 json 或 人物/场景/道具 子目录存在即为资产目录。
        did_src = did_asset = did_frames = did_actor = did_deduce = False
        src_dir = os.path.join(proj, "素材")
        if os.path.isdir(src_dir):
            markers = [ASSET_INDEX, "资产图.json"] + list(ASSET_DOCS)
            looks_asset = any(os.path.isfile(os.path.join(src_dir, m)) for m in markers) \
                or any(os.path.isdir(os.path.join(src_dir, m)) for m in ("人物", "场景", "道具"))
            if not looks_asset:
                before = len(log)
                _rename_dir(proj, "素材", DIR_SRC, log)
                did_src = len(log) > before
        before = len(log)
        _rename_dir(proj, "资产", DIR_ASSET, log)
        did_asset = len(log) > before
        before = len(log)
        _rename_dir(proj, "帧", DIR_FRAMES, log)
        did_frames = len(log) > before

        # 2) 素材图索引改名（快照干名同步）+ 三件套从 剧本/ 归入 素材/
        asset_dir = os.path.join(proj, DIR_ASSET)
        if os.path.isdir(asset_dir):
            if _move_file(os.path.join(asset_dir, "资产图.json"),
                          os.path.join(asset_dir, ASSET_INDEX), log):
                _move_snapshots(asset_dir, asset_dir, "资产图", log, new_stem="素材图")
            for name in ASSET_DOCS:
                stem = name[:-5]
                if _move_file(os.path.join(proj, DIR_SCRIPT, name),
                              os.path.join(asset_dir, name), log):
                    _move_snapshots(os.path.join(proj, DIR_SCRIPT), asset_dir, stem, log)

        # 3) 台词文件归入 台词/
        for name in LINES_FILES:
            if _move_file(os.path.join(proj, name),
                          os.path.join(proj, DIR_LINES, name), log):
                _move_snapshots(proj, os.path.join(proj, DIR_LINES), name[:-5], log)

        # 4) 演员表现产物：创作/表演* → 演员/；5) 平面推演产物：创作/{创作包_,平面图_,战略图_}* → 推演/
        create_dir = os.path.join(proj, DIR_CREATE)
        for src_root, dst_root, prefixes, flag in (
            (create_dir, os.path.join(proj, DIR_ACTOR), ("表演草稿_", "表演对比"), "actor"),
            (create_dir, os.path.join(proj, DIR_DEDUCE), ("创作包_", "平面图_", "战略图_"), "deduce"),
        ):
            if not os.path.isdir(src_root):
                continue
            for fn in sorted(os.listdir(src_root)):
                if not fn.startswith(prefixes):
                    continue
                src = os.path.join(src_root, fn)
                dst = os.path.join(dst_root, fn)
                if os.path.exists(dst):
                    log.append(f"[跳过] {src} 与 {dst} 并存，未合并")
                    continue
                os.makedirs(dst_root, exist_ok=True)
                shutil.move(src, dst)
                log.append(f"[迁移] {src} → {dst}")
                if flag == "actor":
                    did_actor = True
                else:
                    did_deduce = True
        # 6) 字符串引用改写（本轮发生过对应迁移才改；--fix-strings 强制补改其余规则）
        if fix_strings:
            # did_src 故意不强制：裸 `素材/`→`拉片素材/` 有歧义。目录早已迁移的项目里
            # 素材/ 就是资产目录，强制改写会把正确的资产路径打断（09_蜘女 实测 380 条），
            # 还会顺带改到指向别的项目的绝对路径。源视频路径只在本轮真发生重命名时改。
            did_asset = did_frames = did_actor = did_deduce = True
        if any((did_src, did_asset, did_frames, did_actor, did_deduce)):
            _rewrite_strings(proj, did_src, did_asset, did_frames, did_actor, did_deduce, log)
    except OSError as exc:
        log.append(f"[警告] 迁移中断（下次访问自动续跑）：{exc}")
    return log


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    fix = "--fix-strings" in sys.argv
    if not args:
        print("用法: python project_layout.py <项目目录> [<项目目录> ...] [--fix-strings]")
        print("  --fix-strings  强制补做路径字符串改写（早期版本已迁移目录但未改字符串时用）")
        sys.exit(1)
    code = 0
    for target in args:
        if not os.path.isdir(target):
            print(f"[错误] 项目目录不存在: {target}")
            code = 1
            continue
        actions = migrate_project(os.path.abspath(target), fix_strings=fix)
        print(f"{target}:")
        print("\n".join("  " + a for a in actions) if actions else "  已是新布局，无需迁移")
    sys.exit(code)
