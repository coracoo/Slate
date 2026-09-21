# -*- coding: utf-8 -*-
"""dialogue 分镜 JSON -> 分镜脚本 Excel（汇总表）
sheet1「分镜脚本」: 镜号/时长/景别/运镜/角度/场景/机位/动作/台词/提示词
sheet2「台词汇总」: 镜号/时间(镜内)/角色/台词
默认输出: <分镜目录>/<分镜名>_分镜脚本.xlsx
"""
import sys, json, os
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def main():
    src = sys.argv[1]
    cfg = json.load(open(src, encoding="utf-8"))
    shots = cfg.get("shots") or []
    actors = cfg.get("actors") or {}
    out = sys.argv[2] if len(sys.argv) > 2 else \
        os.path.join(os.path.dirname(src),
                     os.path.splitext(os.path.basename(src))[0] + "_分镜脚本.xlsx")

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="1F2937")
    hdr_font = Font(color="FFFFFF", bold=True, size=10)

    ws = wb.active; ws.title = "分镜脚本"
    cols = ["镜号", "场景", "景别", "时长s", "内容", "动作", "声音", "机位(视角)", "运镜", "光影", "器械", "镜头", "台词", "提示词"]
    ws.append(cols)
    for c in ws[1]:
        c.fill = hdr_fill; c.font = hdr_font
        c.alignment = Alignment(horizontal="center")
    def view_of(s):
        ang = s.get("angle") or "平视"
        if ang == "鸟瞰": return "顶拍·俯瞰"
        pos, look = s.get("pos") or [], s.get("look") or []
        h = ""
        if len(pos) == 3 and len(look) == 3:
            d = look[1] - pos[1]
            h = "仰拍" if d > 0.8 else ("高机位下压" if d < -1.2 else "眼平")
            dist = f"{((look[0]-pos[0])**2 + (look[2]-pos[2])**2) ** 0.5:.1f}m"
            return f"{h or ang}·{dist}"
        return ang
    for s in shots:
        lines = s.get("lines") or []
        dlg = " / ".join(f"【{(actors.get(l.get('speaker'), {}) or {}).get('name', l.get('speaker'))}】{l.get('line','')}"
                         for l in lines)
        ws.append([
            s.get("id", ""), s.get("scene", ""), s.get("shot_size", ""),
            round(float(s.get("dur") or 0), 1),
            s.get("content", ""), s.get("action", ""), s.get("sound", ""),
            view_of(s), s.get("camera_move", ""), s.get("lighting", ""),
            s.get("rig", ""), s.get("lens", ""),
            dlg, s.get("prompt", ""),
        ])
    widths = [7, 7, 8, 7, 30, 28, 22, 12, 9, 22, 8, 8, 36, 40]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i) if i <= 26 else "A"].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    ws2 = wb.create_sheet("台词汇总")
    ws2.append(["镜号", "镜内时间s", "角色", "台词"])
    for c in ws2[1]:
        c.fill = hdr_fill; c.font = hdr_font
    for s in shots:
        for l in s.get("lines") or []:
            ws2.append([s.get("id", ""), l.get("at", 0),
                        (actors.get(l.get("speaker"), {}) or {}).get("name", l.get("speaker")),
                        l.get("line", "")])
    for i, w in enumerate([7, 10, 10, 70], 1):
        ws2.column_dimensions[chr(64 + i)].width = w
    for row in ws2.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(vertical="top", wrap_text=True)

    wb.save(out)
    print(f"[完成] {len(shots)} 镜 -> {out}")
    print("OUTPUT:" + out)


if __name__ == "__main__":
    main()
