# -*- coding: utf-8 -*-
"""平面推演的几何与出图一致性。"""
import sys
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import plan_adapt
import plan_frames
import shot_diagram
import strategy_map


class PlanRenderingTests(unittest.TestCase):
    def test_opening_is_a_segment_along_the_wall(self):
        plan = {'canvas': {'w': 10, 'h': 8}, 'room': {
            'walls': [[0, 0], [10, 0], [10, 8], [0, 8]],
            'openings': [{'wall': 1, 'offset': 2, 'width': 2, 'kind': 'window'}]}}
        opening = plan_adapt.plan_openings_world(plan)[0]
        self.assertEqual(opening['start'], [5, 2])
        self.assertEqual(opening['end'], [5, 0])
        self.assertEqual(opening['kind'], 'window')

    def test_rotated_prop_has_a_shared_world_polygon(self):
        plan = {'canvas': {'w': 10, 'h': 8}, 'props': [
            {'id': 'table', 'center': [5, 4], 'size': [4, 2], 'rot': 90}]}
        item = plan_adapt.plan_props_world(plan)[0]
        xs, zs = zip(*item['outline'])
        self.assertAlmostEqual(max(xs) - min(xs), 2)
        self.assertAlmostEqual(max(zs) - min(zs), 4)

    def test_png_rotates_table_and_distinguishes_window_from_wall(self):
        image = Image.new('RGB', (400, 400), 'white')
        dr = ImageDraw.Draw(image, 'RGBA')
        mapping = lambda x, z: (200 + x * 40, 200 - z * 40)
        plan = {'canvas': {'w': 10, 'h': 8},
                'props': [{'id': 'table', 'center': [5, 4], 'size': [4, 1], 'rot': 90}],
                'room': {'walls': [[1, 1], [9, 1], [9, 7], [1, 7]],
                         'openings': [{'wall': 1, 'offset': 2, 'width': 2, 'kind': 'window'}]}}
        shot_diagram.draw_plan_base(dr, mapping, plan_adapt.plan_base_world(plan))
        self.assertNotEqual(image.getpixel((200, 260)), (255, 255, 255))
        self.assertEqual(image.getpixel((260, 200)), (255, 255, 255))
        pixel = image.getpixel((360, 200))
        self.assertGreater(pixel[2], pixel[0])

    def test_base_png_contains_openings(self):
        with tempfile.TemporaryDirectory() as directory:
            plan = {'canvas': {'w': 10, 'h': 8}, 'room': {
                'walls': [[0, 0], [10, 0], [10, 8], [0, 8]],
                'openings': [{'wall': 1, 'offset': 2, 'width': 2, 'kind': 'window'}]}}
            path = Path(directory) / 'plan.png'
            plan_frames.render_plan_base_png(plan, str(path), 640, 480)
            with Image.open(path) as image:
                pixels = image.tobytes()
                self.assertTrue(any(pixels[i+2] > pixels[i]+50 for i in range(0, len(pixels), 3)))

    @unittest.skipUnless(shutil.which('node'), '需要 Node 执行画布脚本')
    def test_browser_canvas_preserves_metre_scale_radius_and_zoom(self):
        plan = {'name': '样例', 'canvas': {'w': 10, 'h': 8}, 'props': [
            {'id': 'p', 'shape': 'circle', 'center': [5, 4], 'size': [1]}]}
        data = {'shots': [{'id': '总览', '_scene_id': 'test', '_actor_positions': {}}],
                'actors': {}, 'scenes': {'test': plan_adapt.plan_scene(plan)}}
        script = strategy_map.render_strategy_html(data).split('<script>')[1].split('</script>')[0]
        harness = '''
const arcs = [];
const drawing = new Proxy({measureText: t => ({width:String(t).length*7}), arc: (...v) => arcs.push(v)}, {get:(o,k) => k in o ? o[k] : () => {}});
const elements = {cv:{getContext:()=>drawing,style:{},addEventListener:()=>{},setPointerCapture:()=>{}},stage:{clientWidth:800},slider:{},trail:{},info:{},sceneName:{},pos:{},cnt:{}};
globalThis.document = {getElementById:id=>elements[id]}; globalThis.window={devicePixelRatio:2};
'''
        checks = '''
const scaleX=MX(1)-MX(0), scaleZ=MZ(0)-MZ(1);
const tableRadius=arcs[0][2]; const before=scaleX;
setZoom(1.25); const after=MX(1)-MX(0); resetView();
console.log(JSON.stringify({scaleX,scaleZ,tableRadius,before,after,reset:MX(1)-MX(0),backing:cv.width}));
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'canvas.js'
            path.write_text(harness + script + checks, encoding='utf-8')
            proc = subprocess.run([shutil.which('node'), str(path)], capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        result = json.loads(proc.stdout)
        self.assertAlmostEqual(result['scaleX'], result['scaleZ'])
        self.assertAlmostEqual(result['tableRadius'], result['scaleX'])
        self.assertAlmostEqual(result['after'], result['before'] * 1.25)
        self.assertAlmostEqual(result['reset'], result['before'])
        self.assertEqual(result['backing'], 1600)


if __name__ == '__main__':
    unittest.main()
