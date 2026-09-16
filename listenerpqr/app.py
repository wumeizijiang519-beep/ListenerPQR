from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import data_dir
from .ui import MainWindow


def main():
    parser = argparse.ArgumentParser(description="ListenerPQR 听课助手")
    parser.add_argument("--demo", action="store_true", help="展示示例课堂，不录音、不调用 API")
    parser.add_argument("--smoke-test", action="store_true", help="离线界面启动检查，成功写入 smoke-ok.txt")
    parser.add_argument("--screenshot", help="保存演示界面截图并退出")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    app.setApplicationName("ListenerPQR")
    app.setOrganizationName("ListenerPQR")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    temp = tempfile.TemporaryDirectory(prefix="listenerpqr-demo-") if args.demo or args.smoke_test else None
    root = Path(temp.name) if temp else data_dir()
    lock = QLockFile(str(root / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "ListenerPQR", "听课助手已在运行，请使用已打开的窗口。")
        return 0
    try:
        window = MainWindow(root, demo=args.demo or args.smoke_test,
                            enumerate_devices=not (args.demo or args.smoke_test))
        window.show()
        if args.smoke_test or args.screenshot:
            def finish():
                if args.screenshot:
                    if not window.grab().save(args.screenshot):
                        raise RuntimeError("截图保存失败")
                if args.smoke_test:
                    Path("smoke-ok.txt").write_text("ListenerPQR UI ready", encoding="utf-8")
                app.quit()
            QTimer.singleShot(800, finish)
        return app.exec()
    finally:
        lock.unlock()
        if temp:
            temp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
