# -*- coding: utf-8 -*-
"""旁白保留标识：故事管线和镜头装配共用，避免将声音轨当作人物。"""
NARRATOR_ALIASES = {"narrator", "narration", "vo", "旁白", "旁白者", "叙述者", "叙述", "画外音", "解说", "解说词"}


def is_narrator(value, record=None):
    if isinstance(record, dict) and any(is_narrator(record.get(key)) for key in ("id", "name", "ref")):
        return True
    raw = str(value or "").strip().lower()
    if raw.startswith(("@character:", "character:", "@actor:", "actor:", "@人物:", "人物:", "@角色:", "角色:")):
        raw = raw.split(":", 1)[1].strip()
    return raw in NARRATOR_ALIASES
