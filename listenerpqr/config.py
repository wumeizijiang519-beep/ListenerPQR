from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_data_path


def data_dir() -> Path:
    root = user_data_path("ListenerPQR", appauthor=False)
    root.mkdir(parents=True, exist_ok=True)
    return root


def endpoint(base: str, suffix: str) -> str:
    parsed = urlsplit(base.strip())
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("API 地址必须是完整的 https:// 地址。")
    if parsed.scheme == "http" and not local:
        raise ValueError("远程 API 请使用 HTTPS；HTTP 仅允许本机服务。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API 地址不能包含密码、查询参数或 #。")
    clean = base.strip().rstrip("/")
    if clean.endswith(("/chat/completions", "/audio/transcriptions")):
        raise ValueError("请填写 API 根地址，例如 https://api.openai.com/v1。")
    return clean + "/" + suffix


@dataclass
class Settings:
    asr_mode: str = "cloud"
    asr_base: str = "https://api.openai.com/v1"
    asr_model: str = "whisper-1"
    summary_base: str = "https://api.openai.com/v1"
    summary_model: str = "gpt-4o-mini"
    local_model: str = "small"
    language: str = "zh"
    segment_seconds: int = 60
    cache_mb: int = 64
    cache_minutes: int = 15
    silence_db: int = -50

    def validate(self):
        if self.asr_mode not in {"cloud", "local"}:
            raise ValueError("未知的语音识别模式。")
        if not 30 <= self.segment_seconds <= 300:
            raise ValueError("分段时长应为 30–300 秒。")
        if not 32 <= self.cache_mb <= 512 or not 2 <= self.cache_minutes <= 60:
            raise ValueError("缓存应为 32–512 MB，保存时限为 2–60 分钟。")
        if not -80 <= self.silence_db <= -20:
            raise ValueError("静音阈值应为 -80 至 -20 dB。")
        endpoint(self.summary_base, "chat/completions")
        if self.asr_mode == "cloud":
            endpoint(self.asr_base, "audio/transcriptions")
            if not self.asr_model.strip():
                raise ValueError("请填写语音识别模型。")
        elif not self.local_model.strip():
            raise ValueError("请填写本地模型名称或目录。")
        if not self.summary_model.strip():
            raise ValueError("请填写总结模型。")

    @classmethod
    def load(cls, root: Path):
        path = root / "settings.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        result.validate()
        return result

    def save(self, root: Path):
        self.validate()
        path = root / "settings.json"
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


class Secrets:
    """Keys are held in memory; optional persistence uses the OS credential vault."""

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str:
        if name not in self.values:
            try:
                import keyring
                self.values[name] = keyring.get_password("ListenerPQR", name) or ""
            except Exception:
                self.values[name] = ""
        return self.values[name]

    def set(self, name: str, value: str, remember: bool):
        import keyring
        self.values[name] = value.strip()
        if remember and value.strip():
            try:
                keyring.set_password("ListenerPQR", name, value.strip())
            except Exception as exc:
                raise ValueError("系统凭据库不可用；密钥仅在本次运行保留。") from exc
        else:
            try:
                keyring.delete_password("ListenerPQR", name)
            except keyring.errors.PasswordDeleteError:
                pass
            except Exception as exc:
                raise ValueError("无法删除凭据库中的旧密钥，请检查系统凭据管理器。") from exc
