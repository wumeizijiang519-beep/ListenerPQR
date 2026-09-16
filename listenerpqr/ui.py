from __future__ import annotations

import importlib.util
import queue
import threading
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextDocument, QTextCursor, QTextBlockFormat
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QProgressBar, QTabWidget, QTextBrowser,
    QPlainTextEdit, QSplitter, QFileDialog, QMessageBox, QDialog, QFormLayout,
    QSpinBox, QCheckBox, QDialogButtonBox, QGroupBox, QScrollArea,
)

from .ai import AIClient
from .audio import Recorder, input_devices
from .cache import AudioCache
from .config import Settings, Secrets
from .engine import Engine
from .store import Store, STATUS, clock, stamp


STYLE = """
QMainWindow, QDialog { background: #f4f6fa; color: #17243b; }
QWidget { font-family: 'Microsoft YaHei UI', 'Noto Sans CJK SC', sans-serif; font-size: 14px; }
QWidget#sidebar { background: #12243d; }
QWidget#sidebar QLabel { color: #ccd7e9; }
QLabel#brand { color: #ffffff; font-size: 25px; font-weight: 700; }
QLabel#headline { font-size: 26px; font-weight: 700; color: #17243b; }
QLabel#subtle { color: #6e7f94; }
QLabel#pill { background: #e0f1ea; color: #217756; border-radius: 12px; padding: 7px 14px; }
QLabel#notice { background: #e8eef9; color: #3b5178; padding: 10px 14px; border-radius: 8px; }
QLabel#warning { background: #fff0dc; color: #875209; padding: 10px 14px; border-radius: 8px; }
QPushButton { background: #ffffff; border: 1px solid #dce2ed; border-radius: 7px; padding: 9px 15px; color: #28405f; }
QPushButton:hover { background: #edf2fb; border-color: #a9bbd8; }
QPushButton:disabled { color: #9aa7b7; background: #eef1f5; border-color: #e3e7ed; }
QPushButton#primary { background: #3769dc; color: #ffffff; border: none; font-weight: 600; }
QPushButton#primary:hover { background: #2656c4; }
QPushButton#primary:disabled { background: #a3b6df; }
QPushButton#danger { color: #a24b43; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit { background: #ffffff; color: #23354e; border: 1px solid #dce2ed; border-radius: 6px; padding: 7px; selection-background-color: #d5e3ff; }
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #6c95ec; }
QListWidget { background: #ffffff; border: 1px solid #e2e7ef; border-radius: 8px; padding: 5px; outline: none; }
QListWidget::item { padding: 12px 9px; border-radius: 5px; }
QListWidget::item:selected { background: #e6edfc; color: #254e9e; }
QWidget#sidebar QListWidget { background: transparent; border: none; color: #c7d3e5; }
QWidget#sidebar QListWidget::item:selected { background: #293f60; color: #ffffff; }
QTextBrowser { background: #ffffff; border: 1px solid #e2e7ef; border-radius: 8px; padding: 19px; color: #23354e; }
QTabWidget::pane { border: none; }
QTabBar::tab { color: #77869b; padding: 12px 16px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #3769dc; border-bottom: 2px solid #3769dc; font-weight: 600; }
QProgressBar { border: none; background: #e5eaf2; border-radius: 4px; max-height: 7px; }
QProgressBar::chunk { background: #4ba888; border-radius: 4px; }
QGroupBox { border: 1px solid #dce2ed; border-radius: 8px; margin-top: 13px; padding: 17px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; }
QCheckBox { spacing: 7px; }
QStatusBar { color: #708198; background: #f4f6fa; }
QSplitter::handle { background: transparent; width: 12px; }
"""


def button(text, callback, primary=False):
    result = QPushButton(text)
    if primary:
        result.setObjectName("primary")
    result.clicked.connect(callback)
    return result


def label(text, name=None):
    result = QLabel(text)
    if name:
        result.setObjectName(name)
    return result


class SafeBrowser(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.document().setDefaultStyleSheet("h1 {font-size:24px;} h2 {font-size:20px;} h3 {font-size:17px;} p,li {line-height:150%;} blockquote {color:#79889c;}")

    def loadResource(self, resource_type, name):
        return None  # AI-provided Markdown cannot fetch remote images or local files.

    def show_markdown(self, text):
        self.document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                                    QTextDocument.MarkdownFeature.MarkdownNoHTML)
        block = self.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            fmt = block.blockFormat()
            fmt.setLineHeight(125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setBottomMargin(3)
            if fmt.headingLevel():
                fmt.setTopMargin(8)
            cursor.setBlockFormat(fmt)
            block = block.next()
        self.setTextCursor(QTextCursor(self.document()))
        self.verticalScrollBar().setValue(0)


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, secrets: Secrets, root: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API 与录音设置")
        self.resize(720, 800)
        self.root, self.secrets, self.result_settings = root, secrets, None
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        body.addWidget(label("让笔记适合你的课堂", "headline"))
        hint = label("云端模式上传分段音频；本地模式只把识别文字发给总结服务。\n请确认课堂允许录音，并核对 API 服务商的数据处理政策。", "subtle")
        hint.setWordWrap(True)
        body.addWidget(hint)
        self.mode = QComboBox()
        self.mode.addItem("云端语音识别 · OpenAI 兼容接口", "cloud")
        self.mode.addItem("本地语音识别 · faster-whisper / CPU", "local")
        self.mode.setCurrentIndex(0 if settings.asr_mode == "cloud" else 1)
        self.asr_base, self.asr_model = QLineEdit(settings.asr_base), QLineEdit(settings.asr_model)
        self.asr_key = QLineEdit(secrets.get("asr"))
        self.asr_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.asr_key.setPlaceholderText("同地址时可留空，沿用总结密钥")
        self.local_model = QLineEdit(settings.local_model)
        self.local_model.setPlaceholderText("small 或本地 CTranslate2 模型目录")
        self.language = QLineEdit(settings.language)
        self.language.setPlaceholderText("zh 中文；en 英语；留空自动检测")
        asr = QGroupBox("1   听清老师说了什么")
        form = QFormLayout(asr)
        form.addRow("识别方式", self.mode)
        form.addRow("语音 API 根地址", self.asr_base)
        form.addRow("识别模型", self.asr_model)
        form.addRow("语音 API Key", self.asr_key)
        form.addRow("本地模型", self.local_model)
        model_note = label("本地模型首次使用需下载；模型文件不属于录音缓存，不会自动删除。", "subtle")
        model_note.setWordWrap(True)
        form.addRow(model_note)
        form.addRow("课堂语言", self.language)
        body.addWidget(asr)
        summary = QGroupBox("2   帮你抓住知识点")
        form = QFormLayout(summary)
        presets = QComboBox()
        presets.addItems(["选择预设（保留当前设置）", "OpenAI", "DeepSeek", "自定义 / 中转站"])
        self.summary_base, self.summary_model = QLineEdit(settings.summary_base), QLineEdit(settings.summary_model)
        self.summary_key = QLineEdit(secrets.get("summary"))
        self.summary_key.setEchoMode(QLineEdit.EchoMode.Password)
        presets.currentIndexChanged.connect(self.preset)
        form.addRow("总结服务", presets)
        form.addRow("总结 API 根地址", self.summary_base)
        form.addRow("总结模型", self.summary_model)
        form.addRow("总结 API Key", self.summary_key)
        summary_note = label("总结接口使用 /chat/completions。文本模型不能代替语音识别接口；\n可组合本地识别 + DeepSeek，或语音服务 + 任意兼容的总结服务。", "subtle")
        summary_note.setWordWrap(True)
        form.addRow(summary_note)
        self.remember = QCheckBox("在系统凭据库中保存密钥（不会写入配置文件）")
        self.remember.setChecked(True)
        form.addRow(self.remember)
        body.addWidget(summary)
        cache = QGroupBox("3   录音节奏与空间管理")
        form = QFormLayout(cache)
        self.seconds = QSpinBox(); self.seconds.setRange(30, 300); self.seconds.setSuffix(" 秒"); self.seconds.setValue(settings.segment_seconds)
        self.mb = QSpinBox(); self.mb.setRange(32, 512); self.mb.setSuffix(" MB"); self.mb.setValue(settings.cache_mb)
        self.minutes = QSpinBox(); self.minutes.setRange(2, 60); self.minutes.setSuffix(" 分钟"); self.minutes.setValue(settings.cache_minutes)
        self.silence = QSpinBox(); self.silence.setRange(-80, -20); self.silence.setSuffix(" dBFS"); self.silence.setValue(settings.silence_db)
        form.addRow("每段录音时长", self.seconds)
        form.addRow("录音缓存容量上限", self.mb)
        form.addRow("待识别音频保存时限", self.minutes)
        form.addRow("静音判定阈值", self.silence)
        note = label("识别成功后立即删音频；网络错误最多尝试 3 次后也会删除。\n容量不足时先删除最旧的待处理片段，并在时间线标记缺失。", "subtle")
        note.setWordWrap(True); form.addRow(note)
        body.addWidget(cache)
        scroll.setWidget(content); outer.addWidget(scroll)
        self.error = label("", "warning"); self.error.setWordWrap(True); self.error.hide(); outer.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.save); buttons.rejected.connect(self.reject); outer.addWidget(buttons)
        self.mode.currentIndexChanged.connect(self.toggle_mode); self.toggle_mode()

    def toggle_mode(self):
        cloud = self.mode.currentData() == "cloud"
        for field in (self.asr_base, self.asr_model, self.asr_key):
            field.setEnabled(cloud)
        self.local_model.setEnabled(not cloud)

    def preset(self, index):
        if index == 1:
            self.summary_base.setText("https://api.openai.com/v1")
            self.summary_model.setText("gpt-4o-mini")
        elif index == 2:
            self.summary_base.setText("https://api.deepseek.com")
            self.summary_model.setText("deepseek-chat")

    def save(self):
        try:
            result = Settings(asr_mode=self.mode.currentData(), asr_base=self.asr_base.text().strip(),
                              asr_model=self.asr_model.text().strip(), summary_base=self.summary_base.text().strip(),
                              summary_model=self.summary_model.text().strip(), local_model=self.local_model.text().strip(),
                              language=self.language.text().strip(), segment_seconds=self.seconds.value(),
                              cache_mb=self.mb.value(), cache_minutes=self.minutes.value(), silence_db=self.silence.value())
            result.validate()
            self.secrets.set("asr", self.asr_key.text(), self.remember.isChecked())
            self.secrets.set("summary", self.summary_key.text(), self.remember.isChecked())
            result.save(self.root)
            self.result_settings = result
            self.accept()
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc)); self.error.show()


class MainWindow(QMainWindow):
    def __init__(self, root: Path, demo=False, enumerate_devices=True):
        super().__init__()
        self.root = root
        self.setWindowTitle("ListenerPQR · 听课助手")
        self.resize(1280, 860)
        self.setMinimumSize(1040, 720)
        self.setStyleSheet(STYLE)
        self.settings_error = ""
        try:
            self.settings = Settings.load(root)
        except Exception:
            self.settings = Settings()
            self.settings_error = "配置文件无法读取，已载入默认设置；请重新检查 API 设置。"
        self.secrets, self.store = Secrets(), Store(root)
        self.cache = AudioCache(root, self.store, self.settings.cache_mb * 1024 ** 2, self.settings.cache_minutes * 60)
        self.cache.startup_cleanup()
        self.events: queue.Queue = queue.Queue(maxsize=300)
        self.last_level = 0
        self.engine: Engine | None = None
        self.recorder: Recorder | None = None
        self.active_sid = None
        self.view_sid = None
        self.origin = 0.0
        self.end_requested = False
        self.closing = False
        self.close_at = 0.0
        self.memo_loading = False
        self.memo_sid = None
        self.build_ui()
        if enumerate_devices:
            self.refresh_devices()
        if demo:
            self.seed_demo()
        self.refresh_sessions()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(200)
        if self.settings_error:
            self.notice(self.settings_error, True)

    def build_ui(self):
        central = QWidget(); layout = QHBoxLayout(central); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)
        sidebar = QWidget(); sidebar.setObjectName("sidebar"); sidebar.setFixedWidth(245)
        side = QVBoxLayout(sidebar); side.setContentsMargins(20, 30, 20, 24); side.setSpacing(16)
        side.addWidget(label("ListenerPQR", "brand")); side.addWidget(label("专注听讲 · 知识随行"))
        self.new_btn = button("＋  新建课堂", self.new_class, True); side.addWidget(self.new_btn)
        side.addSpacing(10); side.addWidget(label("我的课堂"))
        self.session_list = QListWidget(); self.session_list.currentItemChanged.connect(self.select_session)
        self.session_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.session_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        side.addWidget(self.session_list, 1)
        side.addWidget(button("API 与录音设置", self.open_settings))
        side.addWidget(button("打开笔记文件夹", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))))
        foot = label("录音及时清理\n文字笔记自动保存"); foot.setWordWrap(True); side.addWidget(foot)
        layout.addWidget(sidebar)
        main = QWidget(); body = QVBoxLayout(main); body.setContentsMargins(30, 25, 30, 20); body.setSpacing(13)
        row = QHBoxLayout(); row.addWidget(label("让笔记跟上课堂", "headline")); row.addStretch()
        self.pill = label("准备就绪", "pill"); row.addWidget(self.pill); body.addLayout(row)
        body.addWidget(label("听懂当下，课后有据可循。", "subtle"))
        row = QHBoxLayout(); self.title_input = QLineEdit(); self.title_input.setPlaceholderText("课程名称，例如：现代控制理论 · 第 3 讲")
        row.addWidget(self.title_input, 1); self.elapsed = label("00:00:00", "headline"); row.addWidget(self.elapsed); body.addLayout(row)
        row = QHBoxLayout(); row.addWidget(label("录音来源")); self.devices = QComboBox(); self.devices.setMinimumWidth(220); row.addWidget(self.devices, 1)
        self.refresh_btn = button("刷新", self.refresh_devices); row.addWidget(self.refresh_btn)
        self.start_btn = button("开始听课", self.start_class, True); row.addWidget(self.start_btn)
        self.pause_btn = button("暂停", self.pause_class); self.pause_btn.setEnabled(False); row.addWidget(self.pause_btn)
        self.end_btn = button("结束课堂", self.end_class); self.end_btn.setEnabled(False); self.end_btn.setObjectName("danger"); row.addWidget(self.end_btn)
        body.addLayout(row)
        self.level = QProgressBar(); self.level.setRange(0, 100); self.level.setTextVisible(False); self.level.setValue(0); body.addWidget(self.level)
        self.banner = label("首次使用请配置 API。每 60 秒整理一段，笔记会在识别和总结完成后出现。", "notice")
        self.banner.setWordWrap(True); body.addWidget(self.banner)
        self.tabs = QTabWidget()
        live = QWidget(); live_layout = QHBoxLayout(live); live_layout.setContentsMargins(0, 8, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.chunk_list = QListWidget(); self.chunk_list.setMinimumWidth(170); self.chunk_list.currentItemChanged.connect(self.select_chunk); split.addWidget(self.chunk_list)
        self.summary_view = SafeBrowser(); self.summary_view.show_markdown("# 等待课堂开始\n\n选择麦克风并开始听课。\n\n- **抓重点**：自动梳理知识点和讲授逻辑。\n- **有疑问**：一键标记当前时间，课后回看。\n- **可核对**：保留识别原文，不保留整堂录音。")
        split.addWidget(self.summary_view); split.setSizes([200, 700]); live_layout.addWidget(split)
        self.tabs.addTab(live, "课堂笔记")
        self.transcript_view = SafeBrowser(); self.tabs.addTab(self.transcript_view, "识别原文")
        self.review_view = SafeBrowser(); self.tabs.addTab(self.review_view, "课后复习")
        self.memo = QPlainTextEdit(); self.memo.setPlaceholderText("补充板书、公式或自己的理解；离开输入框前后都会自动保存。")
        self.memo_timer = QTimer(self); self.memo_timer.setSingleShot(True); self.memo_timer.timeout.connect(self.save_memo)
        self.memo.textChanged.connect(lambda: self.memo_timer.start(500) if not self.memo_loading else None)
        self.tabs.addTab(self.memo, "我的补充")
        body.addWidget(self.tabs, 1)
        row = QHBoxLayout(); self.mark_input = QLineEdit(); self.mark_input.setPlaceholderText("记下问题，例如：为什么这里可以忽略高阶项？")
        row.addWidget(self.mark_input, 1); self.mark_btn = button("标记疑问", self.mark_question); row.addWidget(self.mark_btn); body.addLayout(row)
        row = QHBoxLayout(); self.cache_label = label("录音缓存 0 MB", "subtle"); row.addWidget(self.cache_label); row.addStretch()
        self.retry_btn = button("重试失败总结", self.retry_summary); row.addWidget(self.retry_btn)
        self.review_btn = button("生成课后复习", self.review); row.addWidget(self.review_btn)
        row.addWidget(button("导出笔记", self.export)); body.addLayout(row)
        layout.addWidget(main, 1); self.setCentralWidget(central)
        self.statusBar().showMessage("本地保存文字笔记 · AI 内容请结合讲授核对")

    def emit(self, kind, value):
        if kind == "level":
            self.last_level = value
            return
        # Worker events are lightweight; meter updates never occupy this queue.
        try:
            self.events.put_nowait((kind, value))
        except queue.Full:
            pass

    def notice(self, text, warning=False):
        self.banner.setObjectName("warning" if warning else "notice")
        self.banner.setText(text)
        self.banner.style().unpolish(self.banner); self.banner.style().polish(self.banner)

    def refresh_devices(self):
        self.devices.clear(); self.devices.addItem("系统默认麦克风", None)
        try:
            for index, name in input_devices():
                self.devices.addItem(name, index)
        except Exception:
            self.notice("暂时无法枚举录音设备。请在 Windows 设置中允许桌面应用使用麦克风。", True)

    def open_settings(self):
        if self.active_sid or (self.engine and (self.engine.busy or not self.engine.tasks.empty())) or self.store.pending():
            self.notice("请结束课堂并等待当前任务完成后修改设置。", True); return
        dialog = SettingsDialog(self.settings, self.secrets, self.root, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if self.engine:
                self.engine.stop_event.set(); self.engine.thread.join(timeout=1); self.engine = None
            self.settings = dialog.result_settings
            self.cache.max_bytes, self.cache.ttl = self.settings.cache_mb * 1024 ** 2, self.settings.cache_minutes * 60
            self.notice("设置已保存。开始听课后会使用新配置。")

    def ensure_engine(self):
        if self.engine:
            return
        self.settings.validate()
        summary_key = self.secrets.get("summary")
        asr_key = self.secrets.get("asr")
        if not asr_key and self.settings.asr_base.rstrip("/") == self.settings.summary_base.rstrip("/"):
            asr_key = summary_key
        if not summary_key:
            raise ValueError("请先在「API 与录音设置」填写总结 API Key。")
        if self.settings.asr_mode == "cloud" and not asr_key:
            raise ValueError("请填写语音 API Key；使用不同服务时需要分别填写。")
        if self.settings.asr_mode == "local" and not importlib.util.find_spec("faster_whisper"):
            raise ValueError("未安装本地识别组件，请运行 install-local.bat 或切换云端识别。")
        client = AIClient(self.settings, asr_key, summary_key)
        self.engine = Engine(self.store, self.cache, client, self.emit); self.engine.start()

    def new_class(self):
        if self.active_sid:
            self.notice("请先结束当前课堂。", True); return
        self.save_memo(); self.session_list.clearSelection(); self.view_sid = None
        self.memo_sid = None
        self.title_input.clear(); self.title_input.setFocus(); self.chunk_list.clear()
        self.summary_view.show_markdown("# 新的一课\n\n输入课程名称，选择麦克风，然后开始听课。")
        self.transcript_view.clear(); self.review_view.clear()
        self.memo_loading = True; self.memo.clear(); self.memo_loading = False

    def start_class(self):
        try:
            self.ensure_engine()
            title = self.title_input.text().strip() or "未命名课堂 " + time.strftime("%m-%d %H:%M")
            self.save_memo()
            self.active_sid = self.store.new_session(title)
            self.view_sid, self.origin, self.end_requested = self.active_sid, time.monotonic(), False
            self.refresh_sessions(select=self.active_sid)
            self.title_input.setText(title)
            self.start_recording()
        except (ValueError, OSError) as exc:
            self.notice(str(exc), True)

    def start_recording(self):
        self.recorder = Recorder(self.store, self.cache, self.active_sid, self.devices.currentData(),
                                 self.settings.segment_seconds, self.settings.silence_db, self.origin, self.emit)
        self.recorder.start()
        self.start_btn.setEnabled(False); self.title_input.setEnabled(False); self.new_btn.setEnabled(False)
        self.pause_btn.setEnabled(False); self.end_btn.setEnabled(True); self.devices.setEnabled(False); self.refresh_btn.setEnabled(False)
        self.pill.setText("正在启动麦克风")

    def pause_class(self):
        if self.recorder and self.recorder.thread.is_alive():
            self.pause_btn.setEnabled(False); self.pill.setText("正在暂停")
            self.recorder.stop()
        elif self.active_sid:
            self.start_recording()

    def end_class(self):
        self.end_requested = True; self.end_btn.setEnabled(False); self.pause_btn.setEnabled(False)
        if self.recorder and self.recorder.thread.is_alive():
            self.recorder.stop(); self.pill.setText("正在结束")
        else:
            self.complete_class()

    def complete_class(self):
        if self.active_sid:
            self.store.update_session(self.active_sid, ended=stamp())
        self.active_sid = None
        self.start_btn.setEnabled(True); self.new_btn.setEnabled(True); self.title_input.setEnabled(True)
        self.end_btn.setEnabled(False); self.pause_btn.setEnabled(False); self.pause_btn.setText("暂停")
        self.devices.setEnabled(True); self.refresh_btn.setEnabled(True); self.pill.setText("课堂已结束")
        self.notice("录音已结束，后台将继续处理剩余片段。完成后可生成课后复习或导出笔记。")
        self.refresh_sessions()

    def refresh_sessions(self, select=None):
        current = select or self.view_sid
        self.session_list.blockSignals(True); self.session_list.clear()
        chosen = None
        for session in self.store.sessions():
            title = session["title"]
            item = QListWidgetItem(title + "\n" + session["created"][:16].replace("T", " "))
            item.setData(Qt.ItemDataRole.UserRole, session["id"]); item.setToolTip(title)
            self.session_list.addItem(item)
            if session["id"] == current:
                chosen = item
        if chosen is None and self.session_list.count():
            chosen = self.session_list.item(0)
        if chosen:
            self.session_list.setCurrentItem(chosen)
        self.session_list.blockSignals(False)
        if chosen:
            self.select_session(chosen)

    def select_session(self, item, previous=None):
        if item is None:
            return
        self.save_memo()
        self.view_sid = item.data(Qt.ItemDataRole.UserRole)
        session = self.store.session(self.view_sid)
        if not self.active_sid:
            self.title_input.setText(session["title"])
        self.memo_loading = True; self.memo.setPlainText(session["memo"]); self.memo_loading = False
        self.memo_sid = self.view_sid
        self.refresh_content()

    def refresh_content(self):
        if not self.view_sid:
            return
        selected = self.chunk_list.currentItem()
        old_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        was_last = self.chunk_list.currentRow() == self.chunk_list.count() - 1
        self.chunk_list.blockSignals(True); self.chunk_list.clear()
        chosen = None
        chunks = self.store.chunks(self.view_sid)
        for c in chunks:
            item = QListWidgetItem(f"{clock(c['start'])} – {clock(c['end'])}\n{STATUS.get(c['status'], c['status'])}")
            item.setData(Qt.ItemDataRole.UserRole, c["id"]); item.setToolTip(c["error"])
            self.chunk_list.addItem(item)
            if c["id"] == old_id:
                chosen = item
        if chunks and (was_last or chosen is None):
            chosen = self.chunk_list.item(len(chunks) - 1)
        if chosen:
            self.chunk_list.setCurrentItem(chosen)
        self.chunk_list.blockSignals(False)
        if chosen:
            self.select_chunk(chosen)
        else:
            self.summary_view.show_markdown("# 正在等待片段\n\n录音满设定时长后自动开始识别；暂停或结束也会提交不足一段的录音。")
        original = "\n\n".join(f"### {clock(c['start'])}–{clock(c['end'])}\n\n{c['transcript'] or STATUS.get(c['status'], c['status'])}" for c in chunks)
        self.transcript_view.show_markdown(original or "尚无识别文字。")
        session = self.store.session(self.view_sid)
        marks = self.store.markers(self.view_sid)
        review = session["overview"] or "# 课后复习\n\n课堂结束、片段处理完成后，点击下方「生成课后复习」。"
        if marks:
            review += "\n\n## 我的课堂标记\n\n" + "\n".join(f"- **{clock(m['at'])}** {m['text']}" for m in marks)
        self.review_view.show_markdown(review)

    def select_chunk(self, item, previous=None):
        if item is None:
            return
        c = self.store.chunk(item.data(Qt.ItemDataRole.UserRole))
        text = c["summary"] or f"## {STATUS.get(c['status'], c['status'])}\n\n{c['error']}"
        if c["status"] == "summary_failed":
            text += "\n\n识别原文已保留，可点击「重试失败总结」。"
        self.summary_view.show_markdown(f"**{clock(c['start'])} – {clock(c['end'])}**\n\n" + text)

    def save_memo(self):
        self.memo_timer.stop()
        if self.view_sid and self.memo_sid == self.view_sid and not self.memo_loading:
            self.store.update_session(self.view_sid, memo=self.memo.toPlainText())

    def mark_question(self):
        if not self.view_sid:
            self.notice("请先开始或选择一堂课。", True); return
        text = self.mark_input.text().strip() or "这里没听懂，课后核对。"
        if self.active_sid == self.view_sid:
            at = time.monotonic() - self.origin
        elif self.chunk_list.currentItem():
            at = self.store.chunk(self.chunk_list.currentItem().data(Qt.ItemDataRole.UserRole))["start"]
        else:
            at = 0
        self.store.mark(self.view_sid, at, text); self.mark_input.clear(); self.refresh_content()
        self.notice(f"已标记 {clock(at)} 的疑问，可在「课后复习」查看。")

    def retry_summary(self):
        if not self.view_sid:
            return
        try:
            self.ensure_engine(); self.engine.submit("retry", self.view_sid)
            self.notice("已安排重试；会优先处理新录音。")
        except ValueError as exc:
            self.notice(str(exc), True)

    def review(self):
        if not self.view_sid:
            return
        if self.view_sid == self.active_sid:
            self.notice("请结束课堂后再生成整堂课复习。", True); return
        if not any(c["transcript"] for c in self.store.chunks(self.view_sid)):
            self.notice("还没有可用于复习的识别文字。", True); return
        try:
            self.ensure_engine(); self.engine.submit("overview", self.view_sid)
            self.tabs.setCurrentIndex(2); self.notice("正在等待剩余录音处理，并整理知识脉络与自测题。")
        except ValueError as exc:
            self.notice(str(exc), True)

    def export(self):
        if not self.view_sid:
            self.notice("请先选择一堂课。", True); return
        self.save_memo()
        path, selected_filter = QFileDialog.getSaveFileName(self, "导出课堂笔记", "课堂笔记.md", "Markdown (*.md);;纯文本 (*.txt)")
        if path:
            try:
                Path(path).write_text(self.store.markdown(self.view_sid), encoding="utf-8")
                self.notice("课堂笔记已导出（包含总结、原文和个人标记）。")
            except OSError:
                self.notice("导出失败，请检查文件是否被占用以及保存位置权限。", True)

    def tick(self):
        changed = False
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "changed":
                changed = True
            elif kind in {"warning", "record_error"}:
                self.notice(value, True)
            elif kind == "info":
                self.notice(value)
            elif kind == "recording":
                self.pill.setText("● 正在听课"); self.pause_btn.setText("暂停"); self.pause_btn.setEnabled(True)
                self.notice(f"正在录音 · 每 {self.settings.segment_seconds} 秒整理一段；可随时标记疑问。")
            elif kind == "stopped":
                self.last_level = 0
                if self.end_requested:
                    self.complete_class()
                else:
                    self.pill.setText("已暂停"); self.pause_btn.setText("继续听课"); self.pause_btn.setEnabled(bool(self.active_sid))
                    self.devices.setEnabled(True); self.refresh_btn.setEnabled(True)
        if changed:
            self.refresh_content()
        if self.active_sid:
            self.elapsed.setText(clock(time.monotonic() - self.origin))
        self.level.setValue(self.last_level)
        if self.engine and self.engine.busy:
            self.statusBar().showMessage(f"AI 正在处理 · 等待识别 {len(self.store.pending())} 段 · 文字笔记自动保存")
        else:
            self.statusBar().showMessage("本地保存文字笔记 · AI 内容请结合讲授核对")
        try:
            self.cache_label.setText(f"录音缓存 {self.cache.size() / 1024 ** 2:.1f} / {self.settings.cache_mb} MB")
        except OSError:
            pass
        if self.closing:
            recorder_done = not self.recorder or not self.recorder.thread.is_alive()
            worker_done = not self.engine or not self.engine.thread.is_alive()
            if recorder_done and worker_done:
                self.cache.startup_cleanup()
                self.timer.stop(); self.close()
            elif time.monotonic() - self.close_at > 5:
                self.timer.stop(); self.close()

    def closeEvent(self, event):
        if self.closing:
            event.accept(); return
        busy = self.active_sid or (self.engine and (self.engine.busy or not self.engine.tasks.empty())) or self.store.pending()
        if busy:
            choice = QMessageBox.question(self, "退出听课助手", "文字笔记已自动保存。退出会停止录音和处理，未识别录音将被清理。确定退出？")
            if choice != QMessageBox.StandardButton.Yes:
                event.ignore(); return
        self.save_memo()
        if self.active_sid:
            self.store.update_session(self.active_sid, ended=stamp())
        if self.recorder:
            self.recorder.stop()
        if self.engine:
            self.engine.stop_event.set()
        self.closing, self.close_at = True, time.monotonic()
        self.setEnabled(False); self.notice("正在停止并清理录音缓存…")
        event.ignore()

    def seed_demo(self):
        sid = self.store.new_session("现代控制理论 · 状态空间模型（演示）")
        import uuid
        examples = [
            (0, 60, "老师介绍状态变量：能够和未来输入一起决定系统未来状态的一组最小变量。状态不是输出，输出取决于测量方式。",
             "### 本段在讲什么\n建立状态变量的直觉，区分系统内部状态与可观测输出。\n\n### 核心知识\n- **状态**：配合未来输入，足以确定系统未来演化。\n- **最小性**：状态变量之间不能冗余。\n- **状态 ≠ 输出**：输出由传感器与测量方式决定。\n\n### 跟上课堂\n接下来要把这些变量写成动态方程。\n\n**自查**：只知道小车的位置，能确定它接下来怎么运动吗？"),
            (60, 120, "以质量弹簧阻尼系统为例，选位移和速度作为状态，输入为外力。将二阶微分方程写成两个一阶方程。",
             "### 本段在讲什么\n用质量—弹簧—阻尼系统说明，如何把物理方程改写为状态空间形式。\n\n### 核心知识\n- 选择 **位移、速度** 作为两个状态。\n- 外力作为系统输入。\n- 一个二阶微分方程，等价写成两个一阶方程。\n\n### 例子 / 推导\n位移的导数为速度；速度的导数由力平衡关系给出。\n\n> 待核对：本段未给出具体参数与矩阵，不补造数值。\n\n### 跟上课堂\n这承接了上一段「状态决定未来」的定义。\n\n**自查**：为什么只选位移一个状态不够？"),
        ]
        for start, end, transcript, summary in examples:
            cid = self.store.add_chunk(sid, start, end, self.cache.root / (uuid.uuid4().hex + ".wav"), time.time(), "done")
            self.store.update_chunk(cid, transcript=transcript, summary=summary)
        self.store.update_session(sid, ended=stamp())
        self.store.mark(sid, 93, "状态变量的选择是否唯一？")
        self.view_sid = sid
        self.notice("演示课堂：使用内置示例文字，不录音、不调用 API。")
