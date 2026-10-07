# -*- coding: utf-8 -*-
"""人物外观字段与提示词编译；不把设计建议提升为故事事实。"""
import copy
import math
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

APPEARANCE_LABELS = {
    "species": "物种与形态", "age": "年龄或年龄段", "face": "脸型与五官",
    "skin": "肤色与皮肤特征", "hair": "发型与发质", "height": "身高",
    "body_type": "体型", "body_proportions": "身材比例", "waist": "腰围",
    "hips": "臀围", "facial_hair": "胡须", "headwear": "头饰",
    "distinctive_features": "可辨识特征", "look": "外貌补充", "outfit": "服装",
}
APPEARANCE_FIELDS = tuple(APPEARANCE_LABELS)
APPEARANCE_SOURCES = ("source", "authored", "proposal", "unknown")


def has_design_value(value):
    return str(value if value is not None else '').strip().lower() not in ('', '未知', '不详', '未明', '待定', 'unknown', 'null', 'none')


def _value(value, label):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f"{label} 必须为文本、数字或空值")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{label} 必须为有限数字")
    if isinstance(value, str) and len(value) > 20000:
        raise ValueError(f"{label} 不能超过 20000 字")
    # 原文中的单位、区间与精度不自动换算或修剪。
    return value


def merge_appearance(previous, patch):
    """校验人工编辑并合并，保留未编辑的旧字段及来源。"""
    if not isinstance(patch, dict) or set(patch) - {*APPEARANCE_FIELDS, "sources", "proposals"}:
        raise ValueError("外观字段不受支持")
    previous = previous if isinstance(previous, dict) else {}
    result = copy.deepcopy(previous)
    sources = dict(result.get("sources")) if isinstance(result.get("sources"), dict) else {}
    for key, value in patch.items():
        if key in APPEARANCE_FIELDS:
            result[key] = _value(value, APPEARANCE_LABELS[key])
            if value != previous.get(key):
                sources[key] = "authored"
    if "sources" in patch:
        incoming = patch["sources"]
        if not isinstance(incoming, dict) or set(incoming) - set(APPEARANCE_FIELDS):
            raise ValueError("外观来源必须按已知字段登记")
        if any(value not in APPEARANCE_SOURCES for value in incoming.values()):
            raise ValueError("外观来源只接受 source、authored、proposal、unknown")
        sources.update(incoming)
    if "proposals" in patch:
        incoming = patch["proposals"]
        if not isinstance(incoming, dict) or set(incoming) - set(APPEARANCE_FIELDS):
            raise ValueError("外观建议必须按已知字段登记")
        proposals = dict(result.get("proposals")) if isinstance(result.get("proposals"), dict) else {}
        proposals.update({key: _value(value, APPEARANCE_LABELS[key]) for key, value in incoming.items()})
        result["proposals"] = proposals
    result["sources"] = sources
    return result


def merge_generated_appearance(previous, incoming, *, fill_only=False, locked_fields=()):
    """U2/素材投影逐字段合并，保留旧扩展、来源、人工采用值与待采用建议。"""
    previous = previous if isinstance(previous, dict) else {}
    if not isinstance(incoming, dict):
        raise ValueError("生成的 appearance 必须为对象")
    result = copy.deepcopy(previous)
    locked = set(locked_fields or ())
    if "appearance" in locked:
        return result
    sources = dict(result.get("sources")) if isinstance(result.get("sources"), dict) else {}
    proposals = dict(result.get("proposals")) if isinstance(result.get("proposals"), dict) else {}
    incoming_sources = incoming.get("sources") if isinstance(incoming.get("sources"), dict) else {}
    for key in APPEARANCE_FIELDS:
        value = incoming.get(key)
        if value in (None, "") or f"appearance.{key}" in locked:
            continue
        value = _value(value, APPEARANCE_LABELS[key])
        source = incoming_sources.get(key)
        if source not in APPEARANCE_SOURCES:
            source = None
        existing = result.get(key)
        if source == "proposal":
            if not fill_only or not has_design_value(proposals.get(key)):
                proposals[key] = value
            continue
        if fill_only and has_design_value(existing) and sources.get(key) != 'unknown':
            continue
        if has_design_value(existing) and (sources.get(key) == "authored" or source == "unknown"):
            continue
        result[key] = value
        if source:
            sources[key] = source
        elif existing != value:
            # 旧输出没有来源字段时保持兼容，但不能把新值冒充旧值的原文来源。
            sources.pop(key, None)
    pending = incoming.get("proposals")
    if isinstance(pending, dict):
        for key, value in pending.items():
            if key in APPEARANCE_FIELDS and value not in (None, "") and f"appearance.{key}" not in locked:
                if not fill_only or not has_design_value(proposals.get(key)):
                    proposals[key] = _value(value, APPEARANCE_LABELS[key])
    if sources or "sources" in previous or "sources" in incoming:
        result["sources"] = sources
    if proposals or "proposals" in previous or "proposals" in incoming:
        result["proposals"] = proposals
    return result


def _human(appearance):
    species = str(appearance.get("species") or "").strip().lower()
    if not species or species in ("人", "真人"):
        return True
    if re.search(r"非人类|非人形|non[- ]human|not (?:a )?human", species):
        return False
    return bool(re.search(r"人类|人形|人身|凡人|\bhuman\b|humanoid", species))


def _adult_human(appearance):
    if not _human(appearance):
        return False
    age = str(appearance.get("age") or "").strip()
    if re.search(r"未成年|儿童|幼儿|少女|少年|child|minor|teen", age, re.I):
        return False
    numbers = re.findall(r"\d+(?:\.\d+)?", age)
    if numbers:
        return min(float(number) for number in numbers) >= 18
    return bool(re.search(r"成年|中年|老年|老人|十八|十九|[二三四五六七八九]十|adult", age, re.I))


def appearance_prompt(record, *, include_outfit=True, identity_only=False):
    """将已采用外观转成可见描述；建议不注入，未知年龄不补精确数字。"""
    appearance = record.get("appearance") if isinstance(record.get("appearance"), dict) else {}
    sources = appearance.get("sources") if isinstance(appearance.get("sources"), dict) else {}
    parts = []
    for key, label in APPEARANCE_LABELS.items():
        value = appearance.get(key)
        if not has_design_value(value) or sources.get(key) in ("proposal", "unknown"):
            continue
        if not include_outfit and key in ("outfit", "headwear"):
            continue
        if identity_only and key in ("outfit", "headwear", "distinctive_features", "look"):
            continue
        if key in ("waist", "hips") and not _adult_human(appearance):
            continue
        parts.append(f"{label}：{value}")
    return "；".join(parts)


def character_image_prompt(record, prompt="", *, state=False):
    """母图与状态图统一读取正式档案，画风留给项目/资产风格层。"""
    parts = []
    from skill_lib import strip_character_layout
    if state:
        description = strip_character_layout(prompt)
        if not description:
            raise ValueError('派生图缺少可见变化，请填写目标服装、持物或伤情，不能只填状态名称。')
        parts.append('【本次图像编辑目标】\n' + description)
        parts.append('以输入的角色母图为身份参考，生成该角色在本状态下的设定图。'
                     '本状态明确指定的服装、持物、伤情必须实际改变，不能原样复制参考图。'
                     '本状态指定新的手持物时，替换母图原手持物，不同时保留相冲突的旧持物。'
                     '新增的可见差异要在对应身体视图中一致呈现。')
        # 自由文本锚点和泛化辨识特征可能混有多集道具；身份从母图继承。
        appearance = appearance_prompt(record, identity_only=True)
        if appearance:
            parts.append('未明确修改的生理外观参考：' + appearance)
        parts.append('未指定变化的脸部身份、身体结构和画风继承母图；'
                     '明确的状态变化优先于母图同部位旧外观。只画一个确定状态，不画前后动作过程。')
        return '\n'.join(parts)
    raw_appearance = record.get("appearance") if isinstance(record.get("appearance"), dict) else {}
    human = _human(raw_appearance)
    structured = any(has_design_value(raw_appearance.get(key)) for key in APPEARANCE_FIELDS) or bool(raw_appearance.get('proposals'))
    identity = str(record.get("identity_anchor") or "").strip()
    if structured and 'identity_anchor' not in (record.get('locked_fields') or []):
        identity = ''
    if record.get('name'):
        parts.append('角色：' + str(record['name']))
    if record.get('gender') in ('男', '女'):
        parts.append('性别：' + str(record['gender']))
    if identity:
        parts.append("身份锚点：" + identity)
    appearance = appearance_prompt(record)
    if appearance:
        parts.append("当前已采用外观：" + appearance)
    description = strip_character_layout(prompt)
    # 正式外观已存在时，历史自动描绘不再参与母图；人工补充和状态差异仍保留。
    if (structured or record.get('_planning_visual')) and 'sheet_prompt' not in (record.get('locked_fields') or []):
        description = ''
    if description:
        parts.append("人物图描绘：" + description)
    if appearance or identity:
        parts.append("若人物图旧描绘与当前已采用外观冲突，以已采用外观为准。" +
                     ("不同角色不得套用同一张模板脸，保持各自五官、发际线和比例差异。" if human else
                      "保持该物种独有的轮廓、附肢、纹理与比例，不套用人类脸型和身材模板。"))
    return "\n".join(parts)


def missing_appearance_fields(record):
    """检查可执行的形象设计；建议算已起草，不代表已采用或可以注入生图。"""
    appearance = record.get("appearance") if isinstance(record.get("appearance"), dict) else {}
    proposals = appearance.get("proposals") if isinstance(appearance.get("proposals"), dict) else {}
    sources = appearance.get("sources") if isinstance(appearance.get("sources"), dict) else {}
    design = {**proposals, **{key: value for key, value in appearance.items() if value not in (None, "")}}
    required = ("face", "hair", "body_type", "outfit") if _human(design) else ("body_type", "distinctive_features", "look")
    return ["appearance." + key for key in required
            if not ((sources.get(key) != "unknown" and has_design_value(appearance.get(key))) or has_design_value(proposals.get(key)))]


def appearance_guidance():
    """供人物提炼、最小单元与投影提示词复用的外观约束。"""
    return """【人物外观独立建档】
biography 只写人物经历、欲望、矛盾与转变；身体数据写 appearance，禁止塞入故事 facts。
identity_anchor 只写稳定生理身份，不写服装、手持物、随身道具或先后剧情。appearance 的 outfit 只描述基准阶段服装；distinctive_features 只写稳定身体辨识特征，不把全剧先后使用的武器、道具和伤情堆入母图。
人物派生图必须对应一个确定时刻的可见外观。look_diff 写“哪一处由什么变为什么”，完整目标状态写 sheet_prompt，两者不得矛盾；明确要替换或移除的旧物。禁止把“持物→脱手”“受伤→恢复”等前后过程合写为一个状态；纯动作、心理、立场变化以及藏在衣内不可见的物品不能单独支撑新人物图，应保留在分镜或剧情中。与母图相同的外观直接复用母图，不为增加派生数量虚构差异。
人物设定阶段必须同时完成可执行的形象设计：人类逐人给出 face、hair、body_type、outfit；非人类给出 species、body_type、distinctive_features、look，按物种说明轮廓与附肢。无毛发等适用情况明确写明，不能用“未知/待定”代替设计。
先从原文、已有 appearance 与旧 sheet_prompt 提取已有外貌。旧人物图描绘的构图和画风词不能当作外貌；旧描绘不是原文证据，不标 source。确无依据的形象细节，根据时代、身份与剧情提出差异化设计，必须写入 appearance.proposals，供用户采用；不得让小传完整但形象设计全空，也不得虚构精确身体尺寸。
appearance 可用字段：species（物种与形态）、age（原文年龄或年龄段）、face（脸型、眼距、眉眼、鼻与下颌）、skin（肤色、皮肤质感）、hair（发际线、发量、发质、分缝、自然碎发）、height（身高）、body_type（体型）、body_proportions（肩、胸、腰、胯、躯干与腿长比例）、waist（腰围）、hips（臀围）、facial_hair（胡须）、headwear（头饰）、distinctive_features（辨识特征）、look（旧外貌补充）、outfit（服装）。
【性别可辨识的形象设计】
先依据剧情和已采用设定确定 gender，不凭名字猜性别；性别不明不自行改为男或女。成年人类角色默认应具有清楚、相互一致的面部、体态与服装剪裁特征，不能只写“男/女”标签、职业或气质。具体描述分别写入 face、body_type、body_proportions、outfit，不另建一份与外观档案竞争的性别提示词。
成年女性：按该角色年龄与体型设计具体眉眼、面颊、下颌轮廓，以及穿衣后的自然胸部轮廓、肩胸腰胯比例；正面和侧面体态一致，衣料与剪裁表达身体结构。军师、战士等身份可以精干有力，职业不能自动把女性外观改成男性。胸部大小和曲线按人物个体设计，不统一夸大、不凭空编造罩杯或围度。
成年男性：按个体设计眉骨、面颊、下颌以及肩胸、腰胯、躯干比例，服装剪裁与体态一致；不把所有男性写成方脸、魁梧、肌肉夸张或有胡须。男女都要有各自的五官和比例差异，不能以同一模板脸加长短发代替设计。
原文或用户明确指定的中性外观、女扮男装、男扮女装及特殊体态优先保留，不为了默认辨识要求覆盖已有设定。未成年角色以符合年龄的面部、发型、体态与衣着辨识，不追加成人胸部或性化曲线；年龄不明不假定成年；非人类按物种特征设计，不强加人类性征。
这些形象细节仍遵守来源规则：原文明示或已采用的值才写正式字段，无依据的新设计写 appearance.proposals，不能冒充剧情事实；已采用后由统一外观字段传给母图和派生图，派生仅改变本状态指定的外观，不重造性别和身体结构。
原文明示的数值、单位、精度和区间原样保存；年龄不明写未知或留空，不凭空填具体岁数。成年女性如原文给出腰臀围须保留；未给数值仅描述已有体态证据，不编造测量数值。未成年或年龄不明者不强调腰臀测量。异兽按物种结构描述，不套人类身材、头发、胡须与腰臀模板。
人类发型与脸部须有可辨识的自然结构，避免整齐塑料发块、所有人同款美型脸；真实感不等于摄影画风，仍服从用户选择的绘画/动漫/其他风格。男性胡须、头饰有证据才定，无证据的补全只能作为建议。跨状态沿用身份锚点，状态只说明有依据的变化，不换脸。
按角色已有身份、经历与体态逐人区分，不能复制一套外貌句式。外观字段来源写 appearance.sources={字段:source|authored|proposal|unknown}：source=原文明示，authored=用户明确采用的虚构设定，proposal=尚未采用的设计建议，unknown=未明。建议放 appearance.proposals={字段:建议值}，不得混入已确认字段、sheet_prompt 或 identity_anchor；只有用户采用后才能用于正式生图。保留旧 look/outfit 与已锁定人工字段。"""
