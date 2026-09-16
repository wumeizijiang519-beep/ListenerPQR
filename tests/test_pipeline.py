import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path

import httpx
import numpy as np
import pytest

from listenerpqr.ai import AIClient, AIError
from listenerpqr.audio import Recorder
from listenerpqr.cache import AudioCache
from listenerpqr.config import Settings, endpoint
from listenerpqr.engine import Engine
from listenerpqr.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path)
    sid = store.new_session("控制理论")
    cache = AudioCache(tmp_path, store, 1024 * 1024, 900)
    return store, sid, cache


def chunk(state, size=100):
    store, sid, cache = state
    path = cache.root / (uuid.uuid4().hex + ".wav")
    path.write_bytes(b"a" * size)
    cid = store.add_chunk(sid, 0, 60, path, time.time())
    return store.chunk(cid), path


def test_success_commits_text_then_deletes_audio_before_summary(state):
    store, sid, cache = state
    item, path = chunk(state)

    class Client:
        cancel = threading.Event()

        def transcribe(self, p):
            assert p.exists()
            return "状态包含位移和速度。"

        def summarize(self, text, context, title):
            assert not path.exists()
            assert store.chunk(item["id"])["transcript"] == text
            return "两个状态：位移、速度。"

    Engine(store, cache, Client(), lambda *args: None).process(item)
    assert store.chunk(item["id"])["status"] == "done"
    assert "位移" in store.markdown(sid)


def test_summary_failure_preserves_transcript_and_retry_uses_no_audio(state):
    store, sid, cache = state
    item, path = chunk(state)

    class Client:
        cancel = threading.Event()
        calls = 0

        def transcribe(self, p):
            self.calls += 1
            return "老师讲的真实内容"

        def summarize(self, *args):
            raise AIError("网络超时")

    client = Client()
    worker = Engine(store, cache, client, lambda *args: None)
    worker.process(item)
    failed = store.chunk(item["id"])
    assert failed["status"] == "summary_failed"
    assert failed["transcript"] and not path.exists()
    client.summarize = lambda *args: "补充总结"
    worker.process(failed)
    assert store.chunk(item["id"])["status"] == "done"
    assert client.calls == 1


def test_asr_failure_deletes_cache_and_exposes_gap(state):
    store, sid, cache = state
    item, path = chunk(state)

    class Client:
        cancel = threading.Event()

        def transcribe(self, p):
            raise AIError("API 401：密钥无效或已过期。")

    Engine(store, cache, Client(), lambda *args: None).process(item)
    assert not path.exists()
    assert store.chunk(item["id"])["status"] == "asr_failed"
    assert "401" in store.markdown(sid)


def test_cache_capacity_evicts_oldest_while_pinned_survives(state):
    store, sid, cache = state
    first, p1 = chunk(state, 400)
    second, p2 = chunk(state, 400)
    cache.max_bytes = 900
    with cache.pin(p1):
        assert cache.prune(reserve=400) == [second["id"]]
        assert p1.exists() and not p2.exists()
        with pytest.raises(RuntimeError):
            cache.prune(reserve=600)
    assert store.chunk(second["id"])["status"] == "dropped"


def test_cache_ttl_and_startup_recovery_are_scoped(state, tmp_path):
    import os
    store, sid, cache = state
    item, path = chunk(state)
    outside = tmp_path / "class.wav"; outside.write_bytes(b"keep")
    unknown = cache.root / "user.wav"; unknown.write_bytes(b"keep")
    os.utime(path, (1, 1))
    assert cache.prune() == [item["id"]]
    item2, path2 = chunk(state)
    store.update_chunk(item2["id"], status="summarizing", transcript="已保存")
    part = cache.root / (uuid.uuid4().hex + ".part"); part.write_bytes(b"raw")
    cache.startup_cleanup()
    assert not path2.exists() and not part.exists()
    assert outside.exists() and unknown.exists()
    assert store.chunk(item2["id"])["status"] == "summary_failed"
    assert store.session(sid)["ended"] is not None


def test_segment_boundaries_and_final_partial_are_lossless(state):
    import wave
    store, sid, cache = state
    recorder = Recorder(store, cache, sid, None, 1, -60, time.monotonic(), lambda *args: None)
    rate = 16000
    data = np.full(rate * 2 + rate // 2, 2000, dtype=np.int16).tobytes()
    recorder.consume(data, rate)
    recorder.finish_segment(rate)
    chunks = store.chunks(sid)
    assert len(chunks) == 3
    assert chunks[0]["end"] == chunks[1]["start"]
    assert chunks[1]["end"] == chunks[2]["start"]
    actual = bytearray()
    for c in chunks:
        with wave.open(c["path"]) as f:
            assert f.getnchannels() == 1 and f.getsampwidth() == 2
            actual.extend(f.readframes(f.getnframes()))
    assert actual == data


def test_silence_skips_api_and_leaves_no_wav(state):
    store, sid, cache = state
    recorder = Recorder(store, cache, sid, None, 1, -50, time.monotonic(), lambda *args: None)
    recorder.consume(b"\0" * 32000, 16000)
    recorder.finish_segment(16000)
    assert store.chunks(sid)[0]["status"] == "silent"
    assert cache.size() == 0


def test_audio_callback_queue_is_bounded(state):
    store, sid, cache = state
    recorder = Recorder(store, cache, sid, None, 60, -50, time.monotonic(), lambda *args: None)
    for _ in range(60):
        recorder.callback(b"a" * 3200, 1600, None, None)
    assert recorder.frames.qsize() == 50
    assert recorder.overrun.is_set()


def test_http_multipart_and_summary_contract(tmp_path):
    path = tmp_path / "segment.wav"; path.write_bytes(b"RIFF fake audio")
    requests = []

    def serve(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer fake-key"
        if request.url.path.endswith("/audio/transcriptions"):
            assert b'filename="segment.wav"' in request.read()
            return httpx.Response(200, json={"text": "阻尼使系统能量耗散。"})
        assert b'"stream":false' in request.read()
        return httpx.Response(200, json={"choices": [{"message": {"content": "阻尼与能量"}, "finish_reason": "stop"}]})

    client = AIClient(Settings(), "fake-key", "fake-key", transport=httpx.MockTransport(serve))
    assert "阻尼" in client.transcribe(path)
    assert client.summarize("text", "", "course") == "阻尼与能量"
    assert len(requests) == 2


def test_api_errors_do_not_leak_server_echoed_secrets():
    client = AIClient(Settings(), "secret", "secret", transport=httpx.MockTransport(
        lambda r: httpx.Response(401, text="echo secret bearer token")))
    with pytest.raises(AIError) as err:
        client.chat("test", "test")
    assert "secret" not in str(err.value)
    assert "401" in str(err.value)


def test_bounded_retry_and_cancel():
    class InstantEvent:
        def is_set(self): return False
        def wait(self, _): return False

    client = AIClient(Settings(), "", "", cancel=InstantEvent())
    count = 0

    def fail():
        nonlocal count
        count += 1
        raise AIError("network", True)

    with pytest.raises(AIError):
        client.retry(fail)
    assert count == 3
    stop = threading.Event(); stop.set()
    canceled = AIClient(Settings(), "", "", cancel=stop)
    with pytest.raises(AIError, match="取消"):
        canceled.chat("", "")


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://a.com/v1?key=x", "file:///tmp", "https://a.com/chat/completions"])
def test_reject_invalid_endpoints(url):
    with pytest.raises(ValueError):
        endpoint(url, "chat/completions")


def test_settings_never_contain_credentials(tmp_path):
    Settings().save(tmp_path)
    saved = (tmp_path / "settings.json").read_text()
    assert "key" not in saved.lower()
    assert Settings.load(tmp_path) == Settings()


def test_overview_includes_every_segment():
    client = AIClient(Settings(), "", "")
    calls = []
    def chat(system, content, max_tokens):
        calls.append(content)
        return "compressed"
    client.chat = chat
    result = client.overview([f"SEGMENT-{i}:" + "文" * 2000 for i in range(30)])
    assert result == "compressed"
    for i in range(30):
        assert f"SEGMENT-{i}:" in "".join(calls)


def test_recording_and_worker_threads_process_final_partial(state, monkeypatch):
    import sys
    import types
    store, sid, cache = state
    delivered = threading.Event()
    events = []

    class FakeStream:
        active = True

        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]

        def __enter__(self):
            def send_audio():
                for _ in range(25):
                    self.callback(np.full(1600, 2000, dtype=np.int16).tobytes(), 1600, None, None)
                    time.sleep(0.005)
                delivered.set()
            self.thread = threading.Thread(target=send_audio)
            self.thread.start()
            return self

        def stop(self):
            self.thread.join()
            self.active = False

        def __exit__(self, *args):
            self.stop()

    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(
        check_input_settings=lambda **kwargs: None, RawInputStream=FakeStream, PortAudioError=RuntimeError))

    class Client:
        def __init__(self): self.cancel = threading.Event()
        def transcribe(self, path): return "课堂内容"
        def summarize(self, *args): return "课堂总结"

    emit = lambda *event: events.append(event)
    worker = Engine(store, cache, Client(), emit)
    recorder = Recorder(store, cache, sid, None, 1, -50, time.monotonic(), emit)
    worker.start(); recorder.start()
    try:
        assert delivered.wait(3)
        recorder.stop(); recorder.thread.join(timeout=3)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            chunks = store.chunks(sid)
            if len(chunks) == 3 and all(c["status"] == "done" for c in chunks):
                break
            time.sleep(0.02)
        assert len(chunks) == 3 and all(c["status"] == "done" for c in chunks)
        assert chunks[-1]["end"] - chunks[-1]["start"] == pytest.approx(0.5)
        assert cache.size() == 0
        assert not any(e[0] == "record_error" for e in events)
    finally:
        recorder.stop(); recorder.thread.join(timeout=3)
        worker.stop_event.set(); worker.thread.join(timeout=3)
