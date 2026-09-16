# Third-party components

ListenerPQR source is MIT licensed. This does not relicense its dependencies.
Windows distributions dynamically load their bundled libraries. Keep the entire
`_internal` folder; users may replace Qt/PySide libraries with compatible builds.
Reverse engineering for debugging modifications to LGPL components is permitted.

| Component | License | Source |
| --- | --- | --- |
| PySide6 / Qt | LGPL-3.0 / GPL / commercial (module dependent) | https://code.qt.io/pyside/pyside-setup.git / https://code.qt.io/qt/ |
| sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio | MIT-style | https://github.com/PortAudio/portaudio |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| HTTPX | BSD-3-Clause | https://github.com/encode/httpx |
| keyring | MIT | https://github.com/jaraco/keyring |
| platformdirs | MIT | https://github.com/tox-dev/platformdirs |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| tokenizers | Apache-2.0 | https://github.com/huggingface/tokenizers |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| PyAV / FFmpeg | BSD / LGPL or GPL depending on build | https://github.com/PyAV-Org/PyAV / https://ffmpeg.org |
| PyInstaller bootloader | GPL with distribution exception | https://github.com/pyinstaller/pyinstaller |

The build records installed versions in `BUILD-DEPENDENCIES.txt` and copies
installed package license metadata to `third-party-licenses`. Model files are
downloaded separately and remain subject to their own licenses.
