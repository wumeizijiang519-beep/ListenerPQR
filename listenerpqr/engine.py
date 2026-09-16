from __future__ import annotations

import queue
import threading
import time
from pathlib import Path

from .ai import AIClient, AIError
from .cache import AudioCache
from .store import Store, clock


class Engine:
    def __init__(self, store: Store, cache: AudioCache, client: AIClient, emit):
        self.store, self.cache, self.client, self.emit = store, cache, client, emit
        self.stop_event = client.cancel
        self.tasks: queue.Queue = queue.Queue(maxsize=16)
        self.busy = False
        self.thread = threading.Thread(target=self.run, daemon=True, name="ai-worker")

    def start(self):
        self.thread.start()

    def submit(self, kind, value):
        try:
            self.tasks.put_nowait((kind, value))
        except queue.Full as exc:
            raise ValueError("操作队列已满，请等待当前任务完成。") from exc

    def process(self, chunk: dict):
        cid, sid = chunk["id"], chunk["session_id"]
        path = Path(chunk["path"])
        text = chunk["transcript"]
        if not text:
            try:
                with self.cache.pin(path):
                    self.store.update_chunk(cid, status="transcribing", error="")
                    self.emit("changed", None)
                    text = self.client.transcribe(path)
                    # Commit text BEFORE deleting raw audio, even when summarization fails later.
                    self.store.update_chunk(cid, transcript=text, status="summarizing" if text else "silent")
            except Exception as exc:
                message = str(exc) if isinstance(exc, (AIError, FileNotFoundError)) else "识别失败，请检查服务配置。"
                self.store.update_chunk(cid, status="asr_failed", error=message)
                self.emit("warning", message)
                return
            finally:
                self.cache.delete(path)
                self.emit("changed", None)
        if not text:
            return
        self.store.update_chunk(cid, status="summarizing", error="")
        self.emit("changed", None)
        previous = [c["summary"] for c in self.store.chunks(sid) if c["start"] < chunk["start"] and c["summary"]]
        try:
            summary = self.client.summarize(text, "\n".join(previous[-4:]), self.store.session(sid)["title"])
            self.store.update_chunk(cid, summary=summary, status="done")
        except Exception as exc:
            message = str(exc) if isinstance(exc, AIError) else "总结失败；识别原文已保存，可稍后重试。"
            self.store.update_chunk(cid, status="summary_failed", error=message)
            self.emit("warning", message)
        self.emit("changed", None)

    def make_overview(self, sid):
        chunks = self.store.chunks(sid)
        sections = []
        for c in chunks:
            content = c["summary"] or c["transcript"]
            if not content:
                content = "静音。" if c["status"] == "silent" else "本段资料缺失或尚未处理，不能推测内容。"
            sections.append(f"[{clock(c['start'])}–{clock(c['end'])}]\n{content}")
        marks = self.store.markers(sid)
        if marks:
            sections.append("学生记录的问题（可能尚未在课堂解答）：\n" + "\n".join(m["text"] for m in marks))
        overview = self.client.overview(sections)
        self.store.update_session(sid, overview=overview)
        self.emit("changed", None)
        self.emit("info", "课后复习已生成。")

    def run(self):
        while not self.stop_event.is_set():
            try:
                if self.cache.prune():
                    self.emit("changed", None)
                    self.emit("warning", "过期录音已清理；未识别片段已标记为缺失。")
                pending = self.store.pending()
                if pending:
                    self.busy = True
                    self.process(pending[0])
                else:
                    try:
                        kind, value = self.tasks.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    self.busy = True
                    try:
                        if kind == "retry":
                            for c in self.store.chunks(value):
                                if c["status"] == "summary_failed" and c["transcript"]:
                                    if self.stop_event.is_set():
                                        break
                                    self.process(c)
                        elif kind == "overview":
                            self.make_overview(value)
                    finally:
                        self.tasks.task_done()
            except Exception as exc:
                self.emit("warning", str(exc) if isinstance(exc, (AIError, RuntimeError)) else "处理失败，请检查磁盘空间和服务设置。")
                self.stop_event.wait(1)
            finally:
                self.busy = False
        self.emit("worker_stopped", None)
