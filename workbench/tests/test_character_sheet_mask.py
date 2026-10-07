# -*- coding: utf-8 -*-
"""验证圆形遮盖的视图边界、重复处理与原图保留。"""
import sys
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from character_sheet_mask import mask_character_sheet, locate_circles


def sheet(heads=True):
    image = Image.new('RGB', (1200, 600), 'white')
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 70, 220, 430), fill='navy')
    draw.rectangle((265, 70, 450, 430), fill='navy')
    for x in (600, 840, 1050):
        if heads or x == 1050:
            draw.ellipse((x - 35, 20, x + 35, 92), fill='black')
        draw.rectangle((x - 12, 93, x + 12, 112), fill='tan')
        draw.rectangle((x - 55, 113, x + 55, 560), fill='navy')
    return image


class CharacterSheetMaskTests(unittest.TestCase):
    def test_tall_portraits_and_unequal_widths_do_not_become_body_panels(self):
        image = Image.new('RGB', (1024, 576), 'white')
        draw = ImageDraw.Draw(image)
        # 两张半身特写延伸至底部；第二格发髻也进入上方头部检测带。
        draw.rectangle((24, 35, 238, 535), fill='navy')
        draw.rectangle((259, 65, 474, 535), fill='navy')
        draw.ellipse((404, 35, 470, 90), fill='black')
        for center, half_width in ((599, 73), (765, 39), (920, 73)):
            draw.ellipse((center - 25, 30, center + 25, 103), fill='black')
            draw.rectangle((center - 8, 104, center + 8, 123), fill='tan')
            draw.rectangle((center - half_width, 124, center + half_width, 420), fill='navy')
            draw.rectangle((center - half_width, 421, center - half_width + 35, 547), fill='navy')
            draw.rectangle((center + half_width - 35, 421, center + half_width, 547), fill='navy')
        circles, reason = locate_circles(image)
        self.assertEqual(reason, '')
        self.assertEqual([c['panel'] for c in circles], [3, 4])
        for circle, center in zip(circles, (599, 765)):
            self.assertAlmostEqual(circle['cx'], center, delta=2)
            self.assertGreater(circle['cy'] + circle['radius'], 103)
            self.assertLess(circle['cx'] - circle['radius'], center - 25)
        self.assertGreater(circles[0]['cx'] - circles[0]['radius'], 474)
        self.assertLess(circles[1]['cx'] + circles[1]['radius'], 847)

    def test_web_asset_import_masks_before_registering_result(self):
        import chatgpt_queue
        import chatgpt_import
        with tempfile.TemporaryDirectory() as folder:
            assets = Path(folder) / '素材'
            assets.mkdir()
            (assets / '人物.json').write_text(json.dumps({'characters': [{'id': 'hero', 'sheet_prompt': '蓝衣'}]}), encoding='utf-8')
            job = chatgpt_queue.queue_assets(folder, ['@character:hero'])[0]
            spec = job['output_spec']
            content = io.BytesIO()
            sheet().save(content, format='PNG')
            manifest = {'schema_version': '1.0', 'generator': 'chatgpt', 'project': Path(folder).name,
                        'assets': [{'job_id': job['id'], 'task_type': 'asset_image', 'version': spec['version'], 'file': 'images/' + spec['filename']}]}
            chatgpt_import.import_package(folder, manifest, {'images/' + spec['filename']: content.getvalue()})
            result = np.asarray(Image.open(assets / '人物' / 'hero.png'))
            self.assertTrue(np.all(result[55, 600] == 255))
            self.assertTrue(np.all(result[55, 840] == 255))
            index = json.loads((assets / '素材图.json').read_text(encoding='utf-8'))
            self.assertEqual(index['人物']['hero']['head_mask']['status'], 'masked')

    def test_masks_third_and_fourth_preserves_portraits_back_and_original(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sheet.png'
            sheet().save(path)
            original = path.read_bytes()
            before = np.asarray(Image.open(path)).copy()
            report = mask_character_sheet(path)
            self.assertEqual(report['status'], 'masked')
            self.assertEqual([c['panel'] for c in report['circles']], [3, 4])
            after = np.asarray(Image.open(path))
            self.assertTrue(np.all(after[55, 600] == 255))
            self.assertTrue(np.all(after[55, 840] == 255))
            self.assertTrue(np.array_equal(before[:, :480], after[:, :480]))
            self.assertTrue(np.array_equal(before[:, 950:], after[:, 950:]))
            self.assertTrue(np.array_equal(before[190:], after[190:]))
            self.assertEqual(Path(report['original']).read_bytes(), original)
            once = path.read_bytes()
            self.assertEqual(mask_character_sheet(path)['status'], 'unchanged')
            self.assertEqual(path.read_bytes(), once)

    def test_neck_framed_body_views_are_not_masked(self):
        circles, reason = locate_circles(sheet(heads=False))
        self.assertEqual(circles, [])
        self.assertEqual(reason, '')

    def test_unknown_background_reports_review_without_editing(self):
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sheet.png'
            Image.new('RGB', (1200, 600), 'grey').save(path)
            before = path.read_bytes()
            log = io.StringIO()
            with redirect_stdout(log):
                self.assertEqual(mask_character_sheet(path)['status'], 'needs_review')
            self.assertEqual(path.read_bytes(), before)
            self.assertIn('[后处理未完成]', log.getvalue())
