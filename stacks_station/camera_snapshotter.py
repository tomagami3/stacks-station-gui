import os
import time
import threading
from typing import Optional

import cv2
import numpy as np


class CameraSnapshotter:
    """
    Low-latency snapshotter:
      - FFMPEG backend + low-latency flags for RTSP/HTTP streams
      - Buffer size = 1
      - Per-cycle flush keeps only the freshest frame
      - Adjustable snapshot rate (set_rate) and flush window (set_flush)
    """
    def __init__(self, src, hz: float = 1.0, flush_sec: float = 0.25):
        self.src = src
        self.period = max(0.05, 1.0 / max(0.1, hz))
        self.flush_sec = max(0.05, float(flush_sec))
        self.cap = None
        self.last = None
        self.running = False
        self.lock = threading.Lock()
        self.thread = None

    def set_rate(self, hz: float):
        with self.lock:
            self.period = max(0.05, 1.0 / max(0.1, hz))

    def set_flush(self, seconds: float):
        with self.lock:
            self.flush_sec = max(0.05, float(seconds))

    def _open(self):
        backend = cv2.CAP_FFMPEG if isinstance(self.src, str) else 0
        if backend == cv2.CAP_FFMPEG:
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
                "rtsp_transport;udp|fflags;nobuffer|flags;low_delay|"
                "max_delay;0|reorder_queue_size;0|max_interleave_delta;0|buffer_size;1024"
            )
        cap = cv2.VideoCapture(self.src, backend)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap

    def start(self):
        if self.running:
            return
        self.cap = self._open()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
        if self.cap:
            try:
                self.cap.release()
            except Exception:
                pass
        self.cap = None

    def _reopen_if_needed(self):
        if self.cap is None or not self.cap.isOpened():
            time.sleep(0.1)
            try:
                if self.cap:
                    self.cap.release()
            except Exception:
                pass
            self.cap = self._open()

    def _loop(self):
        while self.running:
            self._reopen_if_needed()
            if self.cap is None or not self.cap.isOpened():
                time.sleep(0.25)
                continue

            with self.lock:
                flush_sec = self.flush_sec
                period = self.period

            t_end = time.time() + flush_sec
            last_ok = None
            while time.time() < t_end:
                ok, frame = self.cap.read()
                if ok:
                    last_ok = frame
                else:
                    time.sleep(0.005)

            if last_ok is not None:
                with self.lock:
                    self.last = last_ok

            time.sleep(max(0.0, period - flush_sec))

    def get(self) -> Optional[np.ndarray]:
        with self.lock:
            return None if self.last is None else self.last.copy()
