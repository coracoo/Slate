# -*- coding: utf-8 -*-
"""创作台 @ref 引用解析：文本中的引用必须与实际图片参数一一对应。"""
import re

TOKEN_RE = re.compile(r"(?<![\w])@ref\d+\b")


def select_mentioned_refs(prompt, refs):
    tokens = list(dict.fromkeys(TOKEN_RE.findall(str(prompt or ''))))
    by_token = {}
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError('参考图须通过 @ 唤醒，不能只传路径')
        token = str(ref.get('ref_token') or '')
        if not TOKEN_RE.fullmatch(token):
            raise ValueError('参考图缺少有效的 @ref 引用标记')
        if token in by_token:
            raise ValueError('重复的参考图引用标记: ' + token)
        by_token[token] = ref
    missing = [token for token in tokens if token not in by_token]
    if missing:
        raise ValueError('提示词引用的参考图不存在: ' + '、'.join(missing))
    if by_token and not tokens:
        raise ValueError('请在提示词输入 @ 并选择至少一张参考图')
    return [by_token[token] for token in tokens]
