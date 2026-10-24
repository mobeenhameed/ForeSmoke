from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import threading
import time

@dataclass
class CapturedFrame:
    image: object
    index: int
    t_ms: int

class OpenCVSource:
    def __init__(self, source: str, kind: str, source_fps: float | None = None):
        try: import cv2
        except ImportError as e: raise RuntimeError("Install perception dependencies: pip install -e '.[perception]'") from e
        self.cv2=cv2; self.kind=kind; self.source=source; self.cap=None; self.image=None; self.index=-1; self.started=None; self.failed=False
        self.period_ms=None; self.dropped_live_frames=0; self._stop=threading.Event(); self._condition=threading.Condition(); self._latest=None; self._finished=False
        if kind=="image":
            self.image=cv2.imread(str(source))
            if self.image is None: raise RuntimeError(f"image_open_failed: {source}")
        else:
            src=int(source) if kind=="live" else source
            self.cap=cv2.VideoCapture(src)
            if not self.cap.isOpened(): self.close(); raise RuntimeError(f"capture_open_failed: {source}")
            fps=source_fps or float(self.cap.get(cv2.CAP_PROP_FPS) or 0)
            if kind=="video" and (not fps or fps<=0): self.close(); raise RuntimeError("invalid_video_fps: pass --source-fps")
            self.period_ms=1000.0/(fps if kind=="video" else 5.0)
        self.started=time.monotonic()

    def frames(self,target_hz=5.0,on_raw_frame=None):
        if self.kind=="image":
            if on_raw_frame is not None: on_raw_frame(self.image,0,0)
            self.index=0; yield CapturedFrame(self.image,0,0); return
        if self.kind=="live":
            def capture_loop():
                while not self._stop.is_set():
                    ok,frame=self.cap.read()
                    if not ok:
                        self.failed=True
                        with self._condition: self._finished=True; self._condition.notify_all()
                        return
                    self.index+=1
                    acquired=time.monotonic()
                    if on_raw_frame is not None:
                        on_raw_frame(frame,self.index,round((acquired-self.started)*1000))
                    with self._condition:
                        self._latest=(frame,self.index,acquired)
                        self._condition.notify_all()
                with self._condition: self._finished=True; self._condition.notify_all()
            worker=threading.Thread(target=capture_loop,name="foresmoke-capture",daemon=True); worker.start()
            interval=1/max(.01,target_hz); last_index=-1; deadline=time.monotonic()
            try:
                while not self._stop.is_set():
                    deadline=max(deadline,time.monotonic())
                    while time.monotonic()<deadline and not self._stop.is_set(): time.sleep(min(.01,deadline-time.monotonic()))
                    with self._condition:
                        while (self._latest is None or self._latest[1]<=last_index) and not self._finished:
                            self._condition.wait(timeout=.1)
                        if self._latest is None or self._latest[1]<=last_index:
                            break
                        frame,index,acquired=self._latest
                    self.dropped_live_frames+=max(0,index-last_index-1) if last_index>=0 else 0
                    last_index=index; deadline=time.monotonic()+interval
                    yield CapturedFrame(frame,index,round((acquired-self.started)*1000))
            finally:
                self._stop.set()
                worker.join(timeout=1)
            return
        next_due=0.0
        while True:
            ok,frame=self.cap.read()
            if not ok:
                self.failed=self.kind=="live"
                break
            self.index+=1
            fps=1000/self.period_ms
            t_ms=round(self.index*1000/fps)
            if on_raw_frame is not None: on_raw_frame(frame,self.index,t_ms)
            if self.kind=="video":
                interval=max(1,round(fps/target_hz))
                if self.index % interval: continue
            yield CapturedFrame(frame,self.index,t_ms)

    def close(self):
        self._stop.set()
        if self.cap is not None: self.cap.release(); self.cap=None
