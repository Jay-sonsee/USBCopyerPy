"""复制后校验。

三档，从最快到最可靠：

- `size`   ：只比大小（+ 目标可读），几乎零成本，能抓出截断 / 短写
- `sample` ：比大小 + 采样哈希：头、中、尾各取 16KB 计算 BLAKE2b，
             大文件只多读约 0.05%，能抓出内容被篡改 / 清零（默认）
- `full`   ：比大小 + 全量 BLAKE2b，最可靠，代价是多读一整遍

为什么用 BLAKE2b 而不是 SHA256：同样安全强度下 BLAKE2b 在纯软件实现上更快，
而这里 CPU 不是瓶颈、但少一点开销总归好。
"""

from __future__ import annotations

import hashlib
import os

VERIFY_NONE = "none"
VERIFY_SIZE = "size"
VERIFY_SAMPLE = "sample"
VERIFY_FULL = "full"
VERIFY_CHOICES = (VERIFY_NONE, VERIFY_SIZE, VERIFY_SAMPLE, VERIFY_FULL)

SAMPLE_CHUNK = 16 * 1024      # 采样块大小
SMALL_FILE_LIMIT = 64 * 1024  # 小于此值只比大小：为几 KB 的文件开两次文件算哈希，不划算
FULL_CHUNK = 1024 * 1024      # 全量校验的读取块
DIGEST_SIZE = 20


def _read_at(path: str, offset: int, length: int) -> bytes:
    with open(path, "rb", buffering=0) as handle:
        handle.seek(offset)
        return handle.read(length)


def _hash_full(path: str) -> bytes:
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    with open(path, "rb", buffering=0) as handle:
        while True:
            block = handle.read(FULL_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.digest()


def _hash_samples(path: str, size: int) -> bytes:
    middle = max(0, size // 2 - SAMPLE_CHUNK // 2)
    tail = max(0, size - SAMPLE_CHUNK)
    digest = hashlib.blake2b(digest_size=DIGEST_SIZE)
    for position in (0, middle, tail):
        digest.update(_read_at(path, position, SAMPLE_CHUNK))
    return digest.digest()


def verify_file(src: str, dst: str, mode: str, src_size: int) -> tuple[bool, str]:
    """校验目标文件与源是否一致，返回 (是否通过, 失败原因)。"""
    if mode == VERIFY_NONE:
        return True, ""

    try:
        dst_size = os.path.getsize(dst)
    except OSError as exc:
        return False, f"目标文件不可读：{exc}"

    if dst_size != src_size:
        return False, f"大小不一致（源 {src_size} B，目标 {dst_size} B）"

    if mode == VERIFY_SIZE:
        return True, ""

    try:
        if mode == VERIFY_SAMPLE:
            if src_size <= SMALL_FILE_LIMIT:
                # 小文件：写入基本是原子的，短写必然改变大小；再开两次文件算哈希不划算
                return True, ""
            same = _hash_samples(src, src_size) == _hash_samples(dst, src_size)
        else:
            same = _hash_full(src) == _hash_full(dst)
    except OSError as exc:
        return False, f"校验读取失败：{exc}"

    return (True, "") if same else (False, "内容哈希不一致")