import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from listenerpqr.ui import MainWindow, SettingsDialog


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_demo_navigation_and_notes_do_not_cross_sessions(app, tmp_path):
    window = MainWindow(tmp_path, demo=True, enumerate_devices=False)
    sid = window.view_sid
    assert "状态" in window.summary_view.toPlainText()
    window.memo.setPlainText("课堂 A 的手写笔记")
    window.save_memo()
    window.new_class()
    sid2 = window.store.new_session("课堂 B")
    window.view_sid = sid2
    window.refresh_sessions(select=sid2)
    assert window.memo.toPlainText() == ""
    assert window.store.session(sid)["memo"] == "课堂 A 的手写笔记"
    window.mark_input.setText("为什么？")
    window.mark_question()
    assert window.store.markers(sid2)[0]["text"] == "为什么？"
    window.timer.stop(); window.closing = True; window.close()


def test_settings_mode_and_presets(app, tmp_path):
    window = MainWindow(tmp_path, enumerate_devices=False)
    dialog = SettingsDialog(window.settings, window.secrets, tmp_path)
    dialog.mode.setCurrentIndex(1)
    assert dialog.local_model.isEnabled() and not dialog.asr_base.isEnabled()
    dialog.preset(2)
    assert dialog.summary_base.text() == "https://api.deepseek.com"
    dialog.close(); window.timer.stop(); window.closing = True; window.close()
