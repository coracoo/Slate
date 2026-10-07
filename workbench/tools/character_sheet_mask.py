# -*- coding: utf-8 -*-
"""白底五视图设定图的头部圆形遮盖；原图由版本快照保留。"""
import os
import sys
import tempfile
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass


def _runs(values):
    padded = np.pad(values.astype(np.int8), (1, 1))
    edges = np.diff(padded)
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)))


def _five_view_bands(ink):
    """以躯干区的空白间隔划分五格，容许特写与全身视图宽度不同。"""
    height, width = ink.shape
    columns = ink[int(height * .18):int(height * .90)].sum(axis=0) > height * .015
    groups = []
    for left, right in _runs(columns):
        if groups and left - groups[-1][1] < width * .005:
            groups[-1] = (groups[-1][0], right)
        else:
            groups.append((left, right))
    groups = [(left, right) for left, right in groups if right - left > width * .025]
    if len(groups) != 5:
        return []
    bodies = groups[2:]
    if any(right - left > width * .24 for left, right in bodies):
        return []
    centers = [(left + right) / 2 for left, right in bodies]
    if centers[0] < width * .38 or min(np.diff(centers)) < width * .08:
        return []
    return groups


def locate_circles(image):
    """利用下部的三个全身轮廓定位视图，颈部以下起框的视图直接保留。"""
    rgb = np.asarray(image.convert('RGB'))
    height, width = rgb.shape[:2]
    if width < 400 or height < 200:
        return [], '图片尺寸不足以可靠定位五视图'
    edges = np.concatenate((rgb[:3].reshape(-1, 3), rgb[-3:].reshape(-1, 3)))
    if np.mean(np.all(edges > 235, axis=1)) < .85:
        return [], '背景不符合白底五视图，需检查头部位置'
    ink = np.min(rgb, axis=2) < 220
    bands = _five_view_bands(ink)
    columns = ink[int(height * .82):int(height * .98)].sum(axis=0) > max(2, height * .008)
    groups = []
    for left, right in _runs(columns):
        if groups and left - groups[-1][1] < width * .035:
            groups[-1] = (groups[-1][0], right)
        else:
            groups.append((left, right))
    groups = [(left, right) for left, right in groups if right - left > width * .025]
    while len(groups) > 3:
        gaps = [groups[i + 1][0] - groups[i][1] for i in range(len(groups) - 1)]
        nearest = int(np.argmin(gaps))
        if gaps[nearest] > width * .075:
            break
        groups[nearest:nearest + 2] = [(groups[nearest][0], groups[nearest + 1][1])]
    # 分开的双脚可能与相邻侧身距离更近；上方三个小头部提供独立布局证据。
    head_columns = ink[:int(height * .12)].sum(axis=0) > max(2, height * .006)
    head_columns[:int(width * .42)] = False
    heads = [(left, right) for left, right in _runs(head_columns)
             if width * .025 < right - left < width * .12]
    if bands:
        centers = [(left + right) / 2 for left, right in bands[2:]]
    elif len(heads) == 3 and ink[int(height * .2):int(height * .65), :int(width * .4)].mean() > .08:
        centers = [(left + right) / 2 for left, right in heads]
    elif len(groups) == 3:
        centers = [(left + right) / 2 for left, right in groups]
    else:
        return [], '未可靠识别右侧三个全身视图，需检查头部位置'
    if centers[0] < width * .38 or min(np.diff(centers)) < width * .08:
        return [], '五视图布局不明确，需检查头部位置'
    circles = []
    for index in (0, 1):
        left = int(centers[0] - (centers[1] - centers[0]) * .40) if index == 0 else int((centers[0] + centers[1]) / 2)
        right = int((centers[index] + centers[index + 1]) / 2)
        if bands:
            panel = index + 2
            left = int((bands[panel - 1][1] + bands[panel][0]) / 2)
            right = int((bands[panel][1] + bands[panel + 1][0]) / 2)
        left = max(0, left)
        crop = ink[:int(height * .24), left:right]
        widths = crop.sum(axis=1)
        occupied = np.flatnonzero(widths > max(3, width * .004))
        if not len(occupied):
            continue
        top = int(occupied[0])
        if top > height * .13:
            continue
        lower = int(top + height * .09)
        upper = min(int(top + height * .17), len(widths))
        candidates = np.arange(lower, upper)
        candidates = candidates[widths[candidates] > 4]
        if not len(candidates):
            return [], '头部与颈部边界不明确，需检查头部位置'
        neck = int(candidates[np.argmin(widths[candidates])])
        ys, xs = np.nonzero(crop[top:neck])
        if not len(xs):
            return [], '头部轮廓不明确，需检查头部位置'
        x0, x1 = int(xs.min()) + left, int(xs.max()) + left
        if x1 - x0 > width * .14 or neck - top < height * .065:
            return [], '头部轮廓超出可靠范围，需检查头部位置'
        cx, cy = (x0 + x1) / 2, (top + neck) / 2
        radius = float(np.hypot((x1 - x0) / 2, (neck - top) / 2) + max(2, height * .018))
        safe_left = bands[index + 1][1] + 2 if bands else left
        safe_right = bands[index + 3][0] - 2 if bands else right
        if cx - radius < safe_left or cx + radius > safe_right:
            return [], '圆形遮盖可能触及相邻视图，需检查头部位置'
        circles.append({'panel': index + 3, 'cx': round(cx, 2), 'cy': round(cy, 2), 'radius': round(radius, 2)})
    return circles, ''


def mask_character_sheet(path):
    """原子保存白色圆形遮盖结果；重复处理已遮盖图不再修改。"""
    path = Path(path)
    with Image.open(path) as source:
        source.load()
        image = source.convert('RGB')
        if source.info.get('slate_head_mask') == hashlib.sha256(image.tobytes()).hexdigest():
            return {'status': 'unchanged', 'circles': [], 'reason': '已完成圆形遮盖'}
        circles, reason = locate_circles(image)
        if not circles:
            report = {'status': 'needs_review' if reason else 'unchanged', 'circles': [], 'reason': reason}
            if reason:
                print('[后处理未完成] 头部遮盖：' + reason + '；图片已保留', flush=True)
            return report
        draw = ImageDraw.Draw(image)
        for circle in circles:
            x, y, radius = circle['cx'], circle['cy'], circle['radius']
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill='white')
        import versions
        original = versions.snapshot(str(path))
        fd, temporary = tempfile.mkstemp(prefix='.head-mask.', suffix=path.suffix, dir=path.parent)
        os.close(fd)
        try:
            options = {}
            if (source.format or 'PNG') == 'PNG':
                metadata = PngInfo()
                for key, value in source.info.items():
                    if isinstance(value, str) and key != 'slate_head_mask':
                        metadata.add_text(key, value)
                metadata.add_text('slate_head_mask', hashlib.sha256(image.tobytes()).hexdigest())
                options['pnginfo'] = metadata
            image.save(temporary, format=source.format or 'PNG', **options)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    print('[后处理] 人物图白色圆形遮盖：' + '、'.join('第%d格' % c['panel'] for c in circles), flush=True)
    return {'status': 'masked', 'circles': circles, 'original': original}


if __name__ == '__main__':
    import json
    print(json.dumps(mask_character_sheet(sys.argv[1]), ensure_ascii=False))
