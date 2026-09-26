# -*- coding: utf-8 -*-
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workbench" / "tools"))


class ReferenceLimitTests(unittest.TestCase):
    def test_expanded_cloud_models_allow_ten(self):
        from reference_limits import reference_limit
        self.assertEqual(reference_limit("doubao", "doubao-seedream-5.0-pro", "image"), 10)
        self.assertEqual(reference_limit("doubao", "doubao-seedance-1.5-pro", "video"), 10)
        self.assertEqual(reference_limit("openai-compat", "gpt-image-2", "image"), 10)
        self.assertEqual(reference_limit("gpt2.5", "custom", "image"), 10)

    def test_explicit_config_and_comfyui(self):
        from reference_limits import reference_limit
        self.assertEqual(reference_limit("openai-compat", "custom", "image", {"reference_limit": 12}), 12)
        self.assertEqual(reference_limit("local-comfyui", "anything", "image"), 3)

    def test_comfyui_limit_follows_the_workflow_not_the_vendor(self):
        """上限由具体工作流决定：2511 只有三个 LoadImage 位，2.1 按张数动态建节点，Z-Image 没有输入位。
        以前对 local-comfyui 一律回 3，⑦ 的「整 V 直出宫格」（>4 张参考）还没出门就被本地拒了。"""
        from reference_limits import reference_limit
        self.assertEqual(reference_limit("local-comfyui", "qwen_image_edit_plus.safetensors", "image"), 3,
                         '2511 是真有三个槽位，多给会丢图，必须继续拦着')
        self.assertEqual(reference_limit("local-comfyui", "qwen_image_2.1_int8_convrot.safetensors", "image"), 16,
                         '2.1 的 TextEncodeQwenImage21 是 autogrow image_1~16（object_info 实测），不该被厂商级常量卡死')
        self.assertEqual(reference_limit("local-comfyui", "z_image_turbo_int8.safetensors", "image"), 0,
                         'Z-Image 这条根本没有参考图输入位')
        self.assertEqual(reference_limit("local-comfyui", "qwen_image_2.1.safetensors", "image",
                                         {"reference_limit": 4}), 4,
                         '2.1 节点真实上限只有本机 ComfyUI 知道，配置要能下调')

    def test_two_builtins_have_different_real_caps(self):
        """两条内置链的真实上限本来就不同（2511 硬拦三张；2.1 的 images 输入是自动增长列表）。
        09-25 实查本机 ComfyUI：/object_info/TextEncodeQwenImage21 → required.images 类型
        COMFY_AUTOGROW_V3，模板只声明一份 {image: IMAGE} 由 ComfyUI 按连线数量生长，
        所以三张这个数从来不属于 2.1——它被厂商级常量误伤过（见 reference_limit 里的分支）。"""
        import comfyui_client
        with self.assertRaises(comfyui_client.ComfyUIError) as cm:
            comfyui_client.build_qwen_image_edit_workflow('p', 'n', 'm', width=1024, height=576,
                                                         seed=1, reference_images=['a', 'b', 'c', 'd'])
        self.assertIn('最多支持 3 张参考图', str(cm.exception))
        graph = comfyui_client.build_qwen21_workflow('p', 'n', 'qwen_image_2.1.safetensors', width=1024,
                                                    height=576, seed=1,
                                                    reference_images=['a', 'b', 'c', 'd', 'e'])
        loads = [k for k, node in graph.items() if node.get('class_type') == 'LoadImage']
        self.assertEqual(len(loads), 5, '2.1 该按张数建槽位，多一张就多一个 LoadImage 节点')
        wired = [k for k in graph['4']['inputs'] if str(k).startswith('images.image_')]
        self.assertEqual(len(wired), 5, '每张参考图都必须真接进编码节点，不能建了节点不接线')


if __name__ == "__main__":
    unittest.main()
