import os
import sys
import random
import ctypes

import cv2

from PySide6.QtCore import Qt, QTimer, QObject, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget, QLabel
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput

# Global hotkey lib
try:
    import keyboard
except ImportError:
    keyboard = None


# =========================
# CONFIG
# =========================

ROLL_MAX = 10000  # test value

GREEN_MIN = 80       # minimum G value to count as "maybe green"
GREEN_DIFF = 30      # how much higher G must be than R and B

STARTUP_VBS_NAME = "FoxyJumpscare.vbs"

# Are we running with an attached console?
IS_CONSOLE = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

# Log file path (next to script/exe)
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_PATH = os.path.join(BASE_DIR, "bullshit_log.txt")


def log(msg: str):
    """Log to file and to console if present."""
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass

    if IS_CONSOLE:
        print(msg)
        sys.stdout.flush()


# =========================
# UTILITIES
# =========================

def resource_path(relative_path: str) -> str:
    """
    Get path to resource, works for dev and PyInstaller frozen exe.
    """
    if hasattr(sys, "_MEIPASS"):
        base_path = sys._MEIPASS  # type: ignore[attr-defined]
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def ensure_startup_vbs():
    """
    Create a VBS file in the user's Startup folder that launches THIS exe hidden.
    Only runs on Windows. Does nothing if file already exists.
    """
    if os.name != "nt":
        return

    appdata = os.getenv("APPDATA")
    if not appdata:
        return

    startup_dir = os.path.join(
        appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    os.makedirs(startup_dir, exist_ok=True)

    vbs_path = os.path.join(startup_dir, STARTUP_VBS_NAME)
    if os.path.exists(vbs_path):
        return

    exe_path = sys.executable
    exe_path_escaped = exe_path.replace('"', '""')

    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run "{exe_path_escaped}", 0, False\n'
    )

    try:
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(vbs_content)
        log(f"Created startup VBS at: {vbs_path}")
    except Exception as e:
        log(f"Failed to create startup VBS: {e}")


# =========================
# WIN32 CLICK-THROUGH
# =========================

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020

if os.name == "nt":
    user32 = ctypes.windll.user32
    GetWindowLong = user32.GetWindowLongW
    SetWindowLong = user32.SetWindowLongW
else:
    user32 = None
    GetWindowLong = None
    SetWindowLong = None


def make_window_clickthrough(hwnd: int):
    """
    Make the given window handle click-through.
    Mouse events pass through to whatever is behind.
    """
    if os.name != "nt" or GetWindowLong is None:
        return

    style = GetWindowLong(hwnd, GWL_EXSTYLE)
    style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
    SetWindowLong(hwnd, GWL_EXSTYLE, style)


# =========================
# JUMPSCARE WINDOW
# =========================

class JumpscareWindow(QWidget):
    def __init__(self, video_path: str, parent=None):
        super().__init__(parent)

        self.video_path = video_path
        self.cap = None
        self.timer = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowFlag(Qt.Tool, True)

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)

    def start(self):
        """
        Start playback: make fullscreen, click-through, then start video+audio.
        """
        log("JumpscareWindow.start called")

        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

        self.showFullScreen()
        self.raise_()

        hwnd = int(self.winId())
        make_window_clickthrough(hwnd)

        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            log(f"Failed to open video: {self.video_path}")
            self.cleanup()
            return

        fps = self.cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0

        interval_ms = int(1000 / fps)

        url = QUrl.fromLocalFile(self.video_path)
        self.player.setSource(url)
        self.audio_output.setVolume(1.0)
        self.player.play()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)
        self.timer.start(interval_ms)

    def _next_frame(self):
        if self.cap is None:
            self._end_video()
            return

        ret, frame = self.cap.read()
        if not ret:
            self._end_video()
            return

        rgba = self._apply_chroma_key(frame)

        h, w, ch = rgba.shape
        bytes_per_line = ch * w
        qimg = QImage(
            rgba.data, w, h, bytes_per_line, QImage.Format_RGBA8888
        ).copy()

        pixmap = QPixmap.fromImage(qimg)

        pixmap = pixmap.scaled(
            self.size(),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation
        )

        self.label.setPixmap(pixmap)
        self.label.resize(self.size())

    def _apply_chroma_key(self, frame):
        """
        Take a BGR frame, return RGBA with green-ish pixels fully transparent.
        """
        b, g, r = cv2.split(frame)

        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)

        mask = (g > GREEN_MIN) & (g > r + GREEN_DIFF) & (g > b + GREEN_DIFF)

        rgba[mask, 3] = 0
        rgba[~mask, 3] = 255

        return rgba

    def _end_video(self):
        """
        Called when video finishes or fails.
        Hide frame, then close window and clean up.
        """
        log("Ending jumpscare")

        if self.timer is not None:
            self.timer.stop()
            self.timer = None

        if self.cap is not None:
            self.cap.release()
            self.cap = None

        try:
            self.player.stop()
        except Exception:
            pass

        self.label.clear()
        self.label.hide()

        self.hide()

        self.deleteLater()

    def cleanup(self):
        """
        In case of early failure.
        """
        log("JumpscareWindow.cleanup called")
        if self.cap is not None:
            self.cap.release()
            self.cap = None

        if self.timer is not None:
            self.timer.stop()
            self.timer = None

        try:
            self.player.stop()
        except Exception:
            pass

        self.hide()
        self.deleteLater()


# =========================
# CONTROLLER
# =========================

class AppController(QObject):
    forceTriggerRequested = Signal()

    def __init__(self, video_path: str):
        super().__init__()
        self.video_path = video_path
        self.current_jumpscare = None

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

        self.forceTriggerRequested.connect(self.trigger_jumpscare)

        # Only use keyboard global hotkey in console/dev mode
        if IS_CONSOLE and keyboard is not None:
            try:
                keyboard.add_hotkey("ctrl+alt+shift+j", self._on_hotkey)
                log("Hotkey registered: CTRL+ALT+SHIFT+J (force jumpscare)")
            except Exception as e:
                log(f"Failed to register hotkey: {e}")
        elif IS_CONSOLE:
            log("keyboard module not available, no global hotkey")
        else:
            # No console: skip keyboard completely for stability
            log("No console detected, skipping global hotkey registration")

    def _on_hotkey(self):
        self.forceTriggerRequested.emit()

    def _tick(self):
        roll = random.randint(1, ROLL_MAX)

        log(f"Roll: {roll}")

        if roll == 1:
            self.trigger_jumpscare()

    def trigger_jumpscare(self):
        if self.current_jumpscare is not None:
            return

        log("Triggering jumpscare")

        win = JumpscareWindow(self.video_path)
        self.current_jumpscare = win

        def on_destroyed():
            self.current_jumpscare = None
            log("Jumpscare window destroyed")

        win.destroyed.connect(on_destroyed)
        win.start()


# =========================
# MAIN
# =========================

def main():
    log("Bullshit daemon starting")
    ensure_startup_vbs()

    video_path = resource_path(os.path.join("assets", "jump.mp4"))
    log(f"Video path resolved to: {video_path}")

    app = QApplication(sys.argv)

    controller = AppController(video_path)
    return app.exec()


if __name__ == "__main__":
    try:
        rc = main()
        log(f"Bullshit daemon exited with code {rc}")
    except Exception as e:
        # Log any unexpected fatal error so no console build is not completely blind
        import traceback
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        log("FATAL ERROR:\n" + tb)
        # Re-raise in console mode so you still see it
        if IS_CONSOLE:
            raise
