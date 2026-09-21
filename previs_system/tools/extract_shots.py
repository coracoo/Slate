# -*- coding: utf-8 -*-
"""拉片：接触印片 + 自动切点检测（opencv，无AI）。
用法: python extract_shots.py <video> [--start S] [--end E] [--step 3] [--thresh 9] [--merge 0.6]
产物: <video同目录>/frames_<名>/contact_*.jpg ；stdout 打印切点区间（需人工核对归并）。"""
import sys, os, argparse, cv2, numpy as np
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video"); ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=1e9); ap.add_argument("--step", type=float, default=3.0)
    ap.add_argument("--thresh", type=float, default=9.0); ap.add_argument("--merge", type=float, default=0.6)
    a = ap.parse_args()
    a.video = os.path.abspath(a.video)  # Windows 下 cv2 用相对+中文路径会读到错误的帧数
    base = os.path.splitext(os.path.basename(a.video))[0]
    out = os.path.join(os.path.dirname(os.path.abspath(a.video)), "frames_" + base)
    os.makedirs(out, exist_ok=True)
    cap = cv2.VideoCapture(a.video); fps = cap.get(cv2.CAP_PROP_FPS) or 24
    t0 = max(0.0, a.start); t1 = min(a.end, (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps)
    thumbs = []; t = t0
    while t < t1:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); r, f = cap.read()
        if r:
            th = cv2.resize(f, (222, 120)); cv2.rectangle(th, (0, 0), (46, 16), (0, 0, 0), -1)
            cv2.putText(th, "%.0f" % t, (3, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
            thumbs.append(th)
        t += a.step
    cols = 8
    for gi in range(0, len(thumbs), 40):
        ch = thumbs[gi:gi + 40]; rows = (len(ch) + cols - 1) // cols
        sheet = np.full((rows * 120, cols * 222, 3), 20, np.uint8)
        for i, im in enumerate(ch):
            r, c = divmod(i, cols); sheet[r*120:(r+1)*120, c*222:(c+1)*222] = im
        ok, enc = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 86])
        if ok:  # cv2.imwrite 在 Windows 中文路径下静默失败，必须 imencode 后自写文件
            with open(os.path.join(out, "contact_%d.jpg" % (gi // 40)), "wb") as fo:
                fo.write(enc.tobytes())
    f0, f1 = int(t0 * fps), int(t1 * fps); cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    prev = None; cuts = []; idx = f0
    while idx < f1:
        r, f = cap.read()
        if not r: break
        g = cv2.cvtColor(cv2.resize(f, (160, 90)), cv2.COLOR_BGR2GRAY)
        if prev is not None and np.abs(g.astype(int) - prev).mean() > a.thresh:
            cuts.append(idx / fps)
        prev = g; idx += 1
    cap.release()
    m = []
    for ts in cuts:
        if m and ts - m[-1] < a.merge: continue
        m.append(ts)
    bounds = [t0] + m + [t1]
    with open(os.path.join(out, "cuts.txt"), "w", encoding="utf-8") as fo:
        for i in range(len(bounds) - 1):
            fo.write("%.2f\t%.2f\t%.2f\n" % (bounds[i], bounds[i+1], bounds[i+1] - bounds[i]))
    print("# %s  %.1f-%.1fs  contact -> %s" % (base, t0, t1, out))
    print("# %d cuts (thresh %.1f, merge %.1fs) - verify/merge on contact sheet:" % (len(m), a.thresh, a.merge))
    for i in range(len(bounds) - 1):
        print("  S%d: %.1f-%.1f  (%.1fs)" % (i + 1, bounds[i], bounds[i+1], bounds[i+1] - bounds[i]))
if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8")
    except Exception: pass
    main()
