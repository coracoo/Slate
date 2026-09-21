# -*- coding: utf-8 -*-
"""
模拟创作执行器（AI 短片分析工作台 阶段二 · providers v2）
用法: python create_media.py --type image|video --mode generate|edit --prompt "..." \
        --refs <参考图...> --vendor <厂商id> --providers providers.json \
        --outdir <项目/创作/<item_id>/> --manifest <项目/创作/creation.json> --item-id <id>
流程: 校验厂商与对应能力模型(models[type]) -> 按厂商/模型能力校验参考图数量 ->
      按厂商 endpoints[type] 拼请求 -> 产物存 outdir（img_1.png / vid_1.mp4）->
      回写 manifest 里该 item 的 status="done"/"error" 与 outputs；失败把中文错误写进 item.note。
依赖: llm_openai.py（同目录，VendorClient）
退出码: 0=成功 1=失败
"""
import sys, os, json, argparse, datetime, traceback
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
STORE_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "previs_system", "tools"))
sys.path.insert(0, STORE_DIR)
VIDEO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
import llm_openai
import project_store
try:
    import versions as _versions
except Exception:
    _versions = None

def load_manifest(path):
    if not os.path.isfile(path):
        return {"items": []}
    return project_store.read_json(path)[0]

def save_manifest(path, data):
    """通过共享入口原子保存清单，供兼容调用方使用。"""
    def replace(current):
        current.clear()
        current.update(data)
    return project_store.update_json(
        path, replace, create_default={}, snapshot=_snapshot
    )

def _snapshot(path):
    """注入版本快照，避免 project_store 反向依赖 workbench。"""
    if _versions is None:
        return None
    return _versions.snapshot(path)

def update_item(manifest_path, item_id, **fields):
    """在共享锁内重读并局部更新 manifest 中对应 item。"""
    if not os.path.isfile(manifest_path):
        return False
    class _ItemNotFound(Exception):
        pass
    found = [False]

    def mutate(data):
        items = data.get("items")
        if not isinstance(items, list):
            raise project_store.InvalidDocument("manifest.items 必须是数组")
        for it in items:
            if isinstance(it, dict) and it.get("id") == item_id:
                it.update(fields)
                it["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                found[0] = True
                break
        if not found[0]:
            raise _ItemNotFound()

    try:
        project_store.update_json(manifest_path, mutate, snapshot=_snapshot)
    except _ItemNotFound:
        return False
    return found[0]


# Seedream 5.x：size 只接受 '2k'|'3k'|'4k'（或特定 WIDTHxHEIGHT 枚举），比例走独立 ratio 字段。
ASPECT_TO_RATIO = {"1:1": "1:1", "4:3": "4:3", "3:4": "3:4", "16:9": "16:9", "9:16": "9:16",
                   "3:2": "3:2", "2:3": "2:3", "21:9": "21:9", "9:21": "9:21"}
ASPECT_DEFAULT = "16:9"


def image_size_for_aspect(aspect):
    """兼容旧签名：返回画面档位（分辨率档），比例由 image_ratio_for_aspect 提供。"""
    return "2k"


def image_ratio_for_aspect(aspect):
    """把分镜画幅转成 Seedream 的 ratio 值；缺省影视横构图 16:9。"""
    value = str(aspect or "").strip()
    return ASPECT_TO_RATIO.get(value, ASPECT_DEFAULT)


def output_ref(path):
    """返回可直接交给工作台 /media 的 VIDEO 根相对路径。"""
    path = os.path.realpath(path)
    try:
        if os.path.commonpath([VIDEO_ROOT, path]) == VIDEO_ROOT:
            return os.path.relpath(path, VIDEO_ROOT).replace(os.sep, "/")
    except ValueError:
        pass
    # 临时测试目录或外部调用无法被工作台媒体服务访问，保留文件名兼容离线调用。
    return os.path.basename(path)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", required=True, choices=["image", "video", "music", "speech"])
    ap.add_argument("--voice-id", default="", help="角色绑定的厂商音色 ID")
    ap.add_argument("--mode", choices=["generate", "edit"], default="generate", help="图片模式：generate=生图，edit=改图")
    ap.add_argument("--model", default="", help="可选模型覆盖；通常由模式自动选择能力槽")
    ap.add_argument("--prompt", required=True)
    ap.add_argument("--negative", default="", help="显式负面提示词（分镜执行器装配）")
    ap.add_argument("--compiled", action="store_true", help="提示词已由画格编译器完成，不再追加参考图说明")
    ap.add_argument("--prompt-stage", default="", help="提示词阶段（storyboard_image/video），用于产物追踪")
    ap.add_argument("--prompt-revision", default="", help="提示词修订指纹，用于产物追踪")
    ap.add_argument("--size", default="16:9", help="生图画幅比例，默认 16:9；Seedream 使用 size 字段")
    ap.add_argument('--duration', type=float, default=5, help='视频实际请求时长（秒）')
    ap.add_argument('--video-options', default='', help='经模型能力表校验的视频参数 JSON')
    ap.add_argument("--refs", nargs="*", action="append", default=[], help="参考图路径（数量按当前厂商/模型能力；可重复参数）")
    ap.add_argument("--ref-purpose", action="append", default=[], help="参考图用途，与 refs 顺序对应")
    ap.add_argument("--ref-role", action="append", default=[], help="参考图角色(start/process/end/unspecified)，与 refs 顺序对应")
    ap.add_argument("--ref-time", action="append", default=[], help="参考图目标时间（秒），与 refs 顺序对应")
    ap.add_argument("--vendor", "--provider", dest="vendor", required=True, help="厂商 id（--provider 为兼容别名）")
    ap.add_argument("--providers", default=os.path.join(HERE, "..", "providers.json"))
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--item-id", required=True)
    a = ap.parse_args()

    outdir = os.path.abspath(a.outdir)
    os.makedirs(outdir, exist_ok=True)

    refs = [os.path.abspath(r) for group in (a.refs or []) for r in (group or [])]
    if a.prompt_stage or a.prompt_revision:
        update_item(a.manifest, a.item_id, **({"prompt_stage": a.prompt_stage} if a.prompt_stage else {}),
                    **({"prompt_revision": a.prompt_revision} if a.prompt_revision else {}))
    for r in refs:
        if not os.path.isfile(r):
            msg = f"参考图不存在: {r}"
            update_item(a.manifest, a.item_id, status="error", note=msg)
            print(f"[错误] {msg}"); sys.exit(1)

    try:
        client = llm_openai.VendorClient(a.vendor, a.providers)
        image_kind = "image_edit" if a.type == "image" and a.mode == "edit" else a.type
        if not a.model:
            client.model(image_kind)   # 模型覆盖值不是能力槽名。
        if a.type == "image":
            ref_desc = ""
            if refs:
                purposes = [str(x).strip() for x in (a.ref_purpose or [])]
                roles = [str(x).strip() for x in (a.ref_role or [])]
                times = [str(x).strip() for x in (a.ref_time or [])]
                labels = "、".join(
                    (purposes[i] if i < len(purposes) and purposes[i] else f"参考图{i + 1}")
                    + (f"（{roles[i]}）" if i < len(roles) and roles[i] else "")
                    + (f"，目标时间 {times[i]}s" if i < len(times) and times[i] else "")
                    for i in range(len(refs)))
                ref_desc = f"参考图用途依次为（{labels}），保持主体、场景与构图一致："
            out = os.path.join(outdir, "img_1.png")
            # Seedream 等云端生图可能需要数分钟；等待完整响应，不能沿用旧的 120 秒短超时。
            if a.mode == "edit" and not refs:
                raise ValueError("改图模式至少需要一张参考图")
            client.generate_image((a.prompt if a.compiled else ref_desc + a.prompt), out,
                                  model=a.model or None, mode=a.mode, image_refs=refs, timeout=900,
                                  negative_prompt=a.negative.strip() or None,
                                  strict_negative=a.compiled,
                                  extra={"size": image_size_for_aspect(a.size),
                                         "ratio": image_ratio_for_aspect(a.size)})
            outputs = [output_ref(out)]
        elif a.type == "video":
            out = os.path.join(outdir, "vid_1.mp4")
            prompt = a.prompt + ('\n禁止：' + a.negative if a.negative else '')
            first = next((refs[i] for i, role in enumerate(a.ref_role) if role == 'first_frame' and i < len(refs)), None)
            last = next((refs[i] for i, role in enumerate(a.ref_role) if role == 'last_frame' and i < len(refs)), None)
            options = json.loads(a.video_options) if a.video_options else {'duration': a.duration, 'ratio': a.size}
            client.on_task_submitted = lambda task: update_item(a.manifest, a.item_id, provider_task=task)
            client.generate_video(prompt, image_refs=refs, out_path=out, model=a.model or None,
                                  extra=options, first_frame=first, last_frame=last, poll_max=7200)
            outputs = [output_ref(out)]
        else:
            out = os.path.join(outdir, "music_1.mp3" if a.type == "music" else "speech_1.mp3")
            if a.type == "speech":
                client.generate_speech(a.prompt, out, model=a.model or None,
                                       extra={"voice_setting": {"voice_id": a.voice_id}})
            else:
                client.generate_music(a.prompt, out, model=a.model or None)
            outputs = [output_ref(out)]
        update_item(a.manifest, a.item_id, status="done", outputs=outputs)
        # 生成成功与素材归档分开记录；归档失败不能触发重复生图/生视频。
        try:
            from creation_media import register_output
            item = next(x for x in load_manifest(a.manifest).get("items", []) if x.get("id") == a.item_id)
            fields = register_output(os.path.dirname(os.path.dirname(os.path.abspath(a.manifest))), item, out)
            if fields:
                update_item(a.manifest, a.item_id, **fields, registration_error="")
        except Exception as exc:
            update_item(a.manifest, a.item_id, registration_error=str(exc))
            print(f"[归档提醒] 原始文件已保留：{exc}")
        print(f"完成: {out}")
        print(f"OUTPUT:{out}")
    except Exception as e:
        msg = f"生成失败: {e}"
        update_item(a.manifest, a.item_id, status="error", note=msg)
        print(f"[错误] {msg}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()


