"""分类规则解析、目录名清洗、扩展名过滤。

原 C# 版的规则解析有两个越界隐患（`charIndex + 1` 越界、未闭合引号死循环），
这里用带边界检查的顺序扫描器重写，非法输入一律抛 ValueError 而不是静默出错。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 文件夹名里不允许出现的字符（与 Windows 限制一致）
ILLEGAL_CHARS = set('\\/:*?"<>|')

RULE_TOKENS = {
    "name": "卷标",
    "serial": "磁盘序列号",
    "letter": "盘符",
    "type": "驱动器类型",
    "fs": "文件系统",
}


@dataclass(frozen=True)
class RulePart:
    kind: str          # "text" | "token"
    value: str         # 字面量内容 或 token 名


def parse_folder_rule(rule: str) -> list[RulePart]:
    """把 "[serial]" / 'Backup-"[name]' 之类解析成片段列表。

    - `[name] [serial] [letter] [type] [fs]` 是占位符
    - 双引号内为字面量，`@"` 表示字面量里的双引号，`@@` 表示字面量里的 @
    - 文本里的 `[[` 表示字面量 `[`
    """
    if rule is None:
        raise ValueError("规则不能为空")
    parts: list[RulePart] = []
    buf: list[str] = []
    i = 0
    n = len(rule)

    def flush() -> None:
        if buf:
            parts.append(RulePart("text", "".join(buf)))
            buf.clear()

    while i < n:
        ch = rule[i]

        if ch == '"':
            flush()
            i += 1
            text: list[str] = []
            closed = False
            while i < n:
                c = rule[i]
                if c == "@" and i + 1 < n and rule[i + 1] in ('"', "@"):
                    text.append(rule[i + 1])
                    i += 2
                    continue
                if c == '"':
                    closed = True
                    i += 1
                    break
                text.append(c)
                i += 1
            if not closed:
                raise ValueError("引号没有闭合")
            parts.append(RulePart("text", "".join(text)))
            continue

        if ch == "[":
            if i + 1 < n and rule[i + 1] == "[":
                buf.append("[")
                i += 2
                continue
            flush()
            end = rule.find("]", i + 1)
            if end < 0:
                raise ValueError("方括号没有闭合")
            token = rule[i + 1:end].strip().lower()
            if token not in RULE_TOKENS:
                raise ValueError(f"未知占位符 [{token}]，可用：{', '.join(RULE_TOKENS)}")
            parts.append(RulePart("token", token))
            i = end + 1
            continue

        buf.append(ch)
        i += 1

    flush()
    if not parts:
        raise ValueError("规则不能为空")
    return parts


def render_folder_rule(parts: list[RulePart], volume) -> str:
    """按卷信息渲染规则，返回未经清洗的目录名。"""
    out: list[str] = []
    for part in parts:
        if part.kind == "text":
            out.append(part.value)
            continue
        if part.value == "name":
            out.append(volume.label or "UDisk")
        elif part.value == "serial":
            out.append(volume.serial)
        elif part.value == "letter":
            out.append(volume.letter)
        elif part.value == "type":
            out.append(volume.type_label)
        elif part.value == "fs":
            out.append(volume.filesystem or "UNKNOWN")
    return "".join(out)


def sanitize_name(name: str, fallback: str = "UDisk") -> str:
    """清洗为合法的单层目录名。"""
    cleaned = "".join("_" if c in ILLEGAL_CHARS or ord(c) < 32 else c for c in name)
    cleaned = cleaned.strip().rstrip(". ")
    return cleaned or fallback


def build_folder_name(rule: str, volume) -> str:
    """解析 + 渲染 + 清洗，一步到位。"""
    return sanitize_name(render_folder_rule(parse_folder_rule(rule), volume))


# ---------------------------------------------------------------- 扩展名

_EXT_SPLIT = re.compile(r"[,\s;]+")


def parse_ext_list(text: str) -> set[str]:
    """'jpg, .png;mp4' -> {'jpg','png','mp4'}（统一小写、去点）。"""
    result: set[str] = set()
    for item in _EXT_SPLIT.split(text or ""):
        item = item.strip().lstrip(".").lower()
        if item:
            result.add(item)
    return result


def file_ext(path: str) -> str:
    """返回不带点的小写扩展名；无扩展名返回空串。"""
    name = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    idx = name.rfind(".")
    if idx <= 0 or idx == len(name) - 1:
        return ""
    return name[idx + 1:].lower()


def ext_allowed(path: str, ext_mode: str, black_exts: str, white_exts: str, copy_no_ext: bool) -> bool:
    """扩展名过滤决策。空扩展名由 copy_no_ext 决定，不参与黑白名单。"""
    ext = file_ext(path)
    if not ext:
        return bool(copy_no_ext)
    if ext_mode == "black":
        return ext not in parse_ext_list(black_exts)
    if ext_mode == "white":
        return ext in parse_ext_list(white_exts)
    return True
