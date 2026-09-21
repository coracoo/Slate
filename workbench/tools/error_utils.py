# -*- coding: utf-8 -*-
"""统一异常脱敏，覆盖标点令牌、URL 鉴权及嵌套任务错误。"""
import re


def scrub_error(value):
    text = str(value)
    text = re.sub(r'(?i)\bBearer\s+[^\s\"\'<>]+', 'Bearer ***', text)
    text = re.sub(r'\bsk-[A-Za-z0-9_-]+', 'sk-***', text)
    text = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1***@', text)
    text = re.sub(r'(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|password|client_secret)[\"\'\s:=]+)[^\s\"\'&,}]+', r'\1***', text)
    return text


def scrub_payload(value, error_context=False):
    if isinstance(value, dict):
        return {k: scrub_payload(v, error_context or k in ('err','error','errors','out','stderr')) for k,v in value.items()}
    if isinstance(value, list):
        return [scrub_payload(v, error_context) for v in value]
    return scrub_error(value) if error_context and isinstance(value, str) else value
