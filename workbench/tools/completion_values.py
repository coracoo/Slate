# -*- coding: utf-8 -*-
"""补全流程的有效值口径；零和否定值是正式值，空白不是。"""


def has_text(value):
    return isinstance(value, str) and bool(value.strip())


def has_text_list(value):
    return isinstance(value, list) and bool(value) and all(has_text(item) for item in value)


def has_content(value):
    if isinstance(value, str):
        return has_text(value)
    if isinstance(value, (list, tuple)):
        return any(has_content(item) for item in value)
    if isinstance(value, dict):
        return any(has_content(item) for item in value.values())
    return value is not None
