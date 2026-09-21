# ComfyUI API 工作流接入

环境页的「局域网 ComfyUI」分为两条路径：

- **生图**：留空使用内置 Z-Image 文生图，适合没有参考图的普通生图。资产或创作镜头带 @ref 时，若选中的模型是 Qwen Image Edit，适配器会自动上传参考图并使用内置 Qwen Image Edit 2511 工作流；不需要另配 JSON。若要覆盖内置链路，可在 ComfyUI 加载自定义工作流，选择「Save (API Format)」导出 JSON，把文件放进本目录，在环境页点“刷新列表”并选择。自定义 API JSON 中，每个参考图的 LoadImage.image 必须改为 {{ref1}}、{{ref2}}……；负面提示词输入位改为 {{negative}}；最终必须有 SaveImage 节点。
- **视频**：当前 `local-comfyui` 的视频槽固定使用内置 MiniMax H3 适配器，最多 3 张参考图。这里选择的生图工作流不会改变视频流程。要使用 SD1.5/SDXL 视频，请先在 ComfyUI 制作对应的视频 API 工作流，再单独接入视频适配器；不能把图像工作流直接当视频工作流使用。

参考图由创作页提示词中的 `@ref1`、`@ref2` 等标记决定，并按实际文件上传到 ComfyUI；不会静默丢弃或降级成无图请求。

## Qwen Image Edit 改图

本地 ComfyUI 的 models.image_edit 默认使用 qwen_image_edit_2511_int8_convrot.safetensors，与 models.image 的 Z-Image Turbo 文生图槽位分开。创作页切到“改图”后必须引用至少一张参考图；资产页则按规则自动判断：素材存在 parent_ref、derived_from、related_refs 或提示词中的 @character/@scene/@prop 时，自动使用 Qwen Image Edit，不需要再手动切模式。内置链路按官方 Qwen Image Edit 2511 图连接线运行（最多三张参考图）；环境页的 image_edit_workflow_path 只用于可选的自定义覆盖。未配置改图模型或引用图片缺失时，后端会在创建任务前直接提示配置/补图，不会错误降级成 Z-Image 文生图。
