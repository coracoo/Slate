# -*- coding: utf-8 -*-
"""
项目目录规范化（AI 短片分析工作台 阶段二）
用法: python normalize_project.py <项目名> [--apply]
默认 dry-run：打印整理计划（缺哪些标准子目录、根级散落文件归属、命名不合规项）；
--apply 才真正执行（移动前打印清单；只移不删；目标已存在同名则跳过并警告）。
规则见 workbench/docs/项目目录与命名规范.md
退出码: 0=成功 1=项目不存在
"""
import sys, os, shutil
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STANDARD_DIRS = ["拉片素材", "素材", "拉片", "分镜", "白模", "深度", "白模3D", "逐帧", "成片", "创作",
                 "台词", "演员", "推演"]
VIDEO_EXT = (".mp4", ".mov", ".mkv")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
DOC_EXT = (".md", ".txt", ".srt")
KEEP_ROOT = ()   # 允许留在项目根的文件（台词脚本.json 等已归入 台词/）
LINES_FILES = ("台词脚本.json", "台词脚本.bak.json")   # 另含 *_台词角色.json，归入 台词/

def dest_for_file(fn):
    """根级散落文件 -> 目标标准子目录（相对项目根），返回 None 表示保留在根。"""
    low = fn.lower()
    if low.endswith(VIDEO_EXT): return "成片"
    if low.endswith((".blend", ".blend1")) or (low.endswith(".py") and low.startswith("build_")):
        return "白模3D"
    if low.endswith(IMG_EXT): return "逐帧/每秒"
    if low in LINES_FILES or low.endswith("_台词角色.json"): return "台词"
    if low in KEEP_ROOT or low.endswith(DOC_EXT): return None   # 文档保留在根
    return None

def has_space(name):
    return " " in name or "　" in name

def build_plan(d):
    """扫描项目目录，返回 (ops, notes)：
    ops = [(verb, src, dst)] 可执行操作；notes = 纯提示字符串。"""
    ops, notes = [], []
    names = set(os.listdir(d))
    # 1. 缺失标准子目录
    for sub in STANDARD_DIRS:
        if sub not in names:
            ops.append(("创建目录", "", sub))
    # 2. 根级散落文件
    for fn in sorted(names):
        fp = os.path.join(d, fn)
        if not os.path.isfile(fp): continue
        dst = dest_for_file(fn)
        if dst:
            ops.append(("移动", fn, os.path.join(dst, fn)))
        if has_space(fn):
            notes.append(f"[命名] {fn} 含空格，建议用 _ 连接")
    # 3. 根级非标准目录
    for fn in sorted(names):
        fp = os.path.join(d, fn)
        if not os.path.isdir(fp) or fn in STANDARD_DIRS: continue
        if fn == "frames":   # 约定俗成：根级 frames/ -> 逐帧/每秒
            for g in sorted(os.listdir(fp)):
                ops.append(("移动", os.path.join(fn, g), os.path.join("逐帧/每秒", g)))
            notes.append(f"[目录] 根级 frames/ 非常规命名，内容归并到 逐帧/每秒（空目录请自行删除）")
        else:
            inner = [g for g in os.listdir(fp) if os.path.isfile(os.path.join(fp, g))]
            imgs = [g for g in inner if g.lower().endswith(IMG_EXT)]
            if imgs:
                for g in sorted(imgs):
                    ops.append(("移动", os.path.join(fn, g), os.path.join("逐帧/每秒", g)))
                rest = [g for g in inner if g not in imgs]
                note = f"[目录] 根级 {fn}/ 非常规命名，其中图片已归并到 逐帧/每秒"
                note += (f"；其余文件请人工确认: {', '.join(rest)}" if rest else "")
                notes.append(note)
            else:
                notes.append(f"[目录] 根级 {fn}/ 非常规命名，请人工确认归属（未自动处理）")
        if has_space(fn):
            notes.append(f"[命名] 目录 {fn} 含空格，建议用 _ 连接")
    # 4. 逐帧/每秒 缺 manifest
    if os.path.isdir(os.path.join(d, "逐帧", "每秒")) and \
       not os.path.isfile(os.path.join(d, "逐帧", "每秒", "frames_manifest.json")):
        notes.append("[提示] 逐帧/每秒 缺 frames_manifest.json（跑 extract_frames_1fps.py 生成）")
    # 5. 拉片素材目录约定
    sp = os.path.join(d, "拉片素材")
    if os.path.isdir(sp):
        for g in sorted(os.listdir(sp)):
            if os.path.isdir(os.path.join(sp, g)) and not g.startswith("frames_"):
                notes.append(f"[命名] 拉片素材/{g}/ 子目录建议 frames_<源片名> 前缀")
    return ops, notes

def normalize(d, apply=False):
    """规范化项目目录。返回 (ok, plan_str_list)。只移不删，冲突跳过并警告。"""
    d = os.path.abspath(d)
    if not os.path.isdir(d):
        return False, [f"[错误] 项目目录不存在: {d}"]
    ops, notes = build_plan(d)
    lines = [f"# {'执行' if apply else '预览'}计划: {os.path.basename(d)}"]
    for verb, src, dst in ops:
        lines.append(f"[{verb}] {src or '（新建）'} -> {dst}")
    lines += notes
    if not apply:
        lines.append(f"\n共 {len(ops)} 项可执行操作；dry-run 未改动任何文件（加 --apply 执行）")
        return True, lines
    warns = 0
    for verb, src, dst in ops:
        if verb == "创建目录":
            os.makedirs(os.path.join(d, dst), exist_ok=True); continue
        s, t = os.path.join(d, src), os.path.join(d, dst)
        if not os.path.isfile(s):
            lines.append(f"[跳过] 源文件已不存在: {src}"); warns += 1; continue
        os.makedirs(os.path.dirname(t), exist_ok=True)
        if os.path.exists(t):
            lines.append(f"[跳过] 目标已存在同名文件，未覆盖: {dst}"); warns += 1; continue
        shutil.move(s, t)
    lines.append(f"\n执行完成: {len(ops)} 项操作，{warns} 项跳过/警告")
    return True, lines

def main():
    if len(sys.argv) < 2:
        print("用法: python normalize_project.py <项目名> [--apply]"); sys.exit(1)
    proj = sys.argv[1].replace("/", "").replace("\\", "")
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "projects", proj)
    ok, lines = normalize(d, apply="--apply" in sys.argv)
    print("\n".join(lines))
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
