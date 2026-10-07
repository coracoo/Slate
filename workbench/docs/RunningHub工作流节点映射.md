# RunningHub 工作流节点映射

图片模型槽可填写 `workflow:<AI应用ID>`。在同一能力槽的 `extra.api_parameters` 中声明节点映射；`image` 和 `image_edit` 分别读取各自的配置。仅配置 `image` 且该工作流接收参考图时，沿用该槽的映射。

下例中的节点 ID 和字段名都是占位说明，须替换成当前 AI 应用实际公开的输入节点。Slate 不按名称猜测远端节点类型，不自动将画幅换算成宽高，不替换请求值。

```json
{
  "api_parameters": {
    "image": {
      "prompt_node": {"nodeId": "实际提示词节点ID", "fieldName": "实际文本字段名"},
      "image_nodes": [
        {"nodeId": "实际参考图节点ID", "fieldName": "实际图片字段名"}
      ],
      "parameter_nodes": {
        "ratio": {"nodeId": "实际画幅节点ID", "fieldName": "实际画幅字段名"},
        "size": {"nodeId": "实际尺寸节点ID", "fieldName": "实际尺寸字段名"}
      }
    }
  }
}
```

`api_parameters` 可保存为 JSON 对象，也兼容环境配置中的 JSON 文本。示例需要放在厂商的 `extra` 内；在环境页直接编辑附加参数时，只填写 `api_parameters` 对应的对象。

## 提交前检查

- `prompt_node` 接收当前请求提示词，负面词以“禁止”段并入该提示词。
- `image_nodes` 按参考图顺序逐项绑定，数量决定该工作流的图片参考上限；`audio_nodes`、`video_nodes` 使用同样的映射格式。
- `parameter_nodes` 的键是请求参数名，值是目标节点。例如请求带 `ratio: "9:16"`、`size: "1600x2848"`，两个字段都必须声明映射，值原样传入节点。其他请求参数也遵循这一规则。
- 每个映射都必须显式填写非空 `nodeId` 和 `fieldName`，没有默认远端字段名。缺少映射、参考输入多于节点、节点格式无效或多个输入写到同一节点字段时，在上传素材与提交任务前拒绝，并指出缺项。
- `instance_type`、`use_personal_queue` 是工作流提交控制值；`poll_interval`、`poll_max` 是图片任务查询控制值，这些字段不进入节点映射。
- `node_info_list` 只提供节点初值，当前请求的显式映射会覆盖同节点初值。`extra_fields` 提供固定节点值，不能与当前请求的映射重复。

工作台素材图、关键帧和宫格请求都会携带实际画幅与尺寸。选用工作流前须补齐这两个参数的映射，固定节点初值不能代替本次请求参数。

视频制作线仍需要工作流自己的节点与能力契约（时长、模式、画幅及参考媒体数量）。目前可通过 RH 原生请求入口明确提交视频工作流；通用制作入口会提示缺少契约。
