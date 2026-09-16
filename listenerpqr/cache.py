from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from pathlib import Path

from .store import Store


class AudioCache:
    """Owns ONLY UUID.wav / UUID.part files in the application's dedicated cache."""

    def __init__(self, root: Path, store: Store, max_bytes: int, ttl_seconds: int):
        self.root = root / "audio-cache"
        self.root.mkdir(exist_ok=True)
        self.store, self.max_bytes, self.ttl = store, max_bytes, ttl_seconds
        self.lock = threading.RLock()
        self.protected: set[Path] = set()

    def files(self):
        return [p for p in self.root.iterdir() if p.is_file() and p.suffix in {".wav", ".part"}
                and len(p.stem) == 32 and all(c in "0123456789abcdef" for c in p.stem)]

    def size(self) -> int:
        with self.lock:
            return sum(p.stat().st_size for p in self.files())

    def delete(self, path: Path):
        path = Path(path)
        if path.parent.resolve() != self.root.resolve() or path not in self.files():
            return
        with self.lock:
            path.unlink(missing_ok=True)

    def startup_cleanup(self):
        with self.lock:
            for path in self.files():
                path.unlink(missing_ok=True)
        self.store.recover()

    @contextmanager
    def pin(self, path: Path):
        with self.lock:
            if not path.exists():
                raise FileNotFoundError("录音已因缓存时限或容量限制清理。")
            self.protected.add(path)
        try:
            yield
        finally:
            with self.lock:
                self.protected.discard(path)

    def prune(self, reserve=0) -> list[str]:
        dropped = []
        with self.lock:
            files = sorted(self.files(), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            now = time.time()
            for path in files:
                if path in self.protected or path.suffix == ".part":
                    continue
                if now - path.stat().st_mtime > self.ttl or total + reserve > self.max_bytes:
                    size = path.stat().st_size
                    path.unlink()
                    total -= size
                    self.store.update_chunk(path.stem, status="dropped", error="达到录音缓存时限或容量上限；本段未完成识别。")
                    dropped.append(path.stem)
            if total + reserve > self.max_bytes:
                raise RuntimeError("缓存空间不足，录音已停止。请等待当前识别完成或提高缓存上限。")
        return dropped
