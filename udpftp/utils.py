import hashlib
import os
import time
from typing import Optional


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def md5_bytes(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def now_ms() -> int:
    return time.monotonic_ns() // 1_000_000


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def log(tag: str, msg: str) -> None:
    ts = time.strftime('%H:%M:%S')
    print(f"[{ts}][{tag}] {msg}")


def readable_size(n: int) -> str:
    for unit in ['B','KB','MB','GB']:
        if n < 1024.0:
            return f"{n:.2f}{unit}"
        n /= 1024.0
    return f"{n:.2f}TB"