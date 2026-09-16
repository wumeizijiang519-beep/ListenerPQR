from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clock(seconds: float) -> str:
    value = max(0, int(seconds))
    return f"{value // 3600:02}:{value // 60 % 60:02}:{value % 60:02}"


class Store:
    def __init__(self, root: Path):
        self.path = root / "notes.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL,
                    ended TEXT, overview TEXT NOT NULL DEFAULT '', memo TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    start REAL NOT NULL, end REAL NOT NULL, path TEXT NOT NULL,
                    status TEXT NOT NULL, transcript TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS chunks_session ON chunks(session_id, start);
                CREATE TABLE IF NOT EXISTS markers (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(id),
                    at REAL NOT NULL, text TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def new_session(self, title: str) -> str:
        sid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO sessions(id,title,created) VALUES(?,?,?)", (sid, title, stamp()))
        return sid

    def session(self, sid: str) -> dict:
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())

    def sessions(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY created DESC, rowid DESC")]

    def update_session(self, sid: str, **fields):
        if not fields or not fields.keys() <= {"title", "ended", "overview", "memo"}:
            raise ValueError("Invalid session update")
        with self.connect() as db:
            db.execute("UPDATE sessions SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                       (*fields.values(), sid))

    def add_chunk(self, sid: str, start: float, end: float, path: Path, created: float,
                  status="queued") -> str:
        cid = path.stem
        with self.connect() as db:
            db.execute("INSERT INTO chunks(id,session_id,start,end,path,status,created) VALUES(?,?,?,?,?,?,?)",
                       (cid, sid, start, end, str(path), status, created))
        return cid

    def update_chunk(self, cid: str, **fields):
        if not fields or not fields.keys() <= {"status", "transcript", "summary", "error"}:
            raise ValueError("Invalid chunk update")
        with self.connect() as db:
            db.execute("UPDATE chunks SET " + ",".join(k + "=?" for k in fields) + " WHERE id=?",
                       (*fields.values(), cid))

    def chunk(self, cid: str) -> dict:
        with self.connect() as db:
            return dict(db.execute("SELECT * FROM chunks WHERE id=?", (cid,)).fetchone())

    def chunks(self, sid: str) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM chunks WHERE session_id=? ORDER BY start", (sid,))]

    def pending(self) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM chunks WHERE status='queued' ORDER BY created")]

    def recover(self):
        with self.connect() as db:
            db.execute("""UPDATE chunks SET status=CASE WHEN transcript<>'' THEN 'summary_failed' ELSE 'dropped' END,
                       error='上次运行中断；录音缓存已清理，有文字的片段可重试总结。'
                       WHERE status IN ('queued','transcribing','summarizing')""")
            db.execute("UPDATE sessions SET ended=? WHERE ended IS NULL", (stamp(),))

    def mark(self, sid: str, at: float, text: str):
        with self.connect() as db:
            db.execute("INSERT INTO markers(session_id,at,text) VALUES(?,?,?)", (sid, at, text))

    def markers(self, sid: str) -> list[dict]:
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM markers WHERE session_id=? ORDER BY at", (sid,))]

    def markdown(self, sid: str, include_transcript=True) -> str:
        session = self.session(sid)
        lines = [f"# {session['title']}", f"\n{session['created']}\n", "> AI 辅助笔记，请结合教师讲授核对术语、公式与结论。\n"]
        if session["overview"]:
            lines += ["## 课后复习\n", session["overview"], ""]
        lines += ["## 课堂时间线\n"]
        for c in self.chunks(sid):
            lines += [f"### {clock(c['start'])}–{clock(c['end'])}\n"]
            if c["summary"]:
                lines += [c["summary"], ""]
            elif c["status"] == "silent":
                lines += ["静音片段，已跳过。\n"]
            else:
                lines += [f"状态：{STATUS.get(c['status'], c['status'])}。{c['error']}\n"]
            if include_transcript and c["transcript"]:
                lines += ["**识别原文**\n", c["transcript"], ""]
        marks = self.markers(sid)
        if marks:
            lines += ["## 我的课堂标记\n"]
            lines.extend(f"- {clock(m['at'])} · {m['text']}" for m in marks)
        if session["memo"]:
            lines += ["\n## 我的补充笔记\n", session["memo"]]
        return "\n".join(lines) + "\n"


STATUS = {"queued": "等待识别", "transcribing": "正在识别", "summarizing": "正在总结",
          "done": "已完成", "silent": "静音跳过", "dropped": "录音已清理 / 内容缺失",
          "asr_failed": "识别失败 / 录音已清理", "summary_failed": "总结失败 / 原文已保存"}
