from __future__ import annotations

import queue
import threading
import time
import uuid
import wave
from pathlib import Path

import numpy as np

from .cache import AudioCache
from .store import Store


def input_devices() -> list[tuple[int, str]]:
    import sounddevice as sd
    return [(i, f"{d['name']} · {sd.query_hostapis(d['hostapi'])['name']}")
            for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]


class Recorder:
    def __init__(self, store: Store, cache: AudioCache, sid: str, device: int | None,
                 seconds: int, silence_db: int, origin: float, emit):
        self.store, self.cache, self.sid = store, cache, sid
        self.device, self.seconds, self.silence_db = device, seconds, silence_db
        self.origin, self.emit = origin, emit
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True, name="audio-recorder")
        self.frames: queue.Queue = queue.Queue(maxsize=50)
        self.overrun = threading.Event()
        self.file = None
        self.path: Path | None = None
        self.count = 0
        self.energy = 0.0
        self.start_at = 0.0
        self.next_at: float | None = None

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def callback(self, data, frames, times, status):
        if status:
            self.overrun.set()
        try:
            self.frames.put_nowait(bytes(data))
        except queue.Full:
            self.overrun.set()

    def open_segment(self, rate: int, start_at: float):
        reserve = rate * 2 * self.seconds + 44
        with self.cache.lock:
            dropped = self.cache.prune(reserve=reserve)
            if dropped:
                self.emit("changed", None)
                self.emit("warning", "部分待识别音频已达到缓存上限并清理，时间线已标记缺失。")
            self.path = self.cache.root / (uuid.uuid4().hex + ".part")
            self.file = wave.open(str(self.path), "wb")
            self.file.setnchannels(1)
            self.file.setsampwidth(2)
            self.file.setframerate(rate)
            self.start_at, self.count, self.energy = start_at, 0, 0.0

    def finish_segment(self, rate: int):
        if self.file is None:
            return
        with self.cache.lock:
            self.file.close()
            self.file = None
            if self.count == 0:
                self.cache.delete(self.path)
                return
            rms = np.sqrt(self.energy / self.count) / 32768
            silent = 20 * np.log10(max(rms, 1e-9)) < self.silence_db
            target = self.path.with_suffix(".wav")
            self.path.rename(target)
            self.store.add_chunk(self.sid, self.start_at, self.start_at + self.count / rate,
                                 target, time.time(), "silent" if silent else "queued")
            self.next_at = self.start_at + self.count / rate
            if silent:
                self.cache.delete(target)
        self.emit("changed", None)

    def consume(self, data: bytes, rate: int):
        offset = 0
        while offset < len(data):
            if self.file is None:
                if self.next_at is None:
                    self.next_at = time.monotonic() - self.origin
                self.open_segment(rate, self.next_at)
            available = (rate * self.seconds - self.count) * 2
            piece = data[offset:offset + available]
            values = np.frombuffer(piece, dtype=np.int16).astype(np.float64)
            with self.cache.lock:
                self.file.writeframesraw(piece)
            self.count += len(values)
            self.energy += float(np.dot(values, values))
            level = min(100, int(np.sqrt(np.mean(values ** 2)) / 32768 * 500))
            self.emit("level", level)
            offset += len(piece)
            if self.count == rate * self.seconds:
                self.finish_segment(rate)

    def run(self):
        rate = 16000
        try:
            import sounddevice as sd
            # Prefer 16 kHz. If unsupported, use the device's native rate (up to 96 kHz).
            try:
                sd.check_input_settings(device=self.device, samplerate=rate, channels=1, dtype="int16")
            except sd.PortAudioError:
                rate = int(sd.query_devices(self.device, "input")["default_samplerate"])
            if not 8000 <= rate <= 96000 or rate * self.seconds * 2 > 24_000_000:
                raise RuntimeError("设备采样率过高，请缩短分段时长或在 Windows 声音设置中选择 48 kHz。")
            with sd.RawInputStream(device=self.device, samplerate=rate, channels=1, dtype="int16",
                                   blocksize=rate // 10, callback=self.callback) as stream:
                self.next_at = time.monotonic() - self.origin
                self.emit("recording", None)
                last_data = time.monotonic()
                while not self.stop_event.is_set():
                    if self.overrun.is_set():
                        raise RuntimeError("录音设备报告丢帧或处理过慢，已停止录音；请重新选择设备并恢复。")
                    try:
                        block = self.frames.get(timeout=0.2)
                    except queue.Empty:
                        if not stream.active or time.monotonic() - last_data > 5:
                            raise RuntimeError("麦克风已断开或没有传入数据。")
                        continue
                    self.consume(block, rate)
                    last_data = time.monotonic()
                stream.stop()
            while not self.frames.empty():
                self.consume(self.frames.get_nowait(), rate)
        except Exception as exc:
            message = str(exc) if isinstance(exc, RuntimeError) else "无法录音；请检查麦克风权限、设备连接与占用情况。"
            self.emit("record_error", message)
        finally:
            try:
                self.finish_segment(rate)
            except Exception:
                self.emit("warning", "录音写入失败，请检查磁盘剩余空间；本段可能不完整。")
            self.emit("stopped", None)
