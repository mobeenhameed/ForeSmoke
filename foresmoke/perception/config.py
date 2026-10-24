from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

@dataclass(frozen=True)
class PerceptionConfig:
    source: str
    source_kind: Literal["live","video","image"]
    camera_id: str
    view_id: str
    output: str = "-"
    model: str = "yolo26n.pt"
    conf: float = 0.15
    imgsz: int = 640
    target_hz: float = 5.0
    source_fps: float | None = None
    run_id: UUID | None = None
    save_frames: bool = False
    preview: bool = False
    run_dir: Path = Path("runs/default")

    def __post_init__(self):
        if self.source_kind not in ("live","video","image"): raise ValueError("source_kind must be live, video, or image")
        if not 0 <= self.conf <= 1 or self.imgsz < 32 or self.target_hz <= 0: raise ValueError("invalid inference settings")
        if self.source_fps is not None and self.source_fps <= 0: raise ValueError("source_fps must be positive")
        if self.source_kind=="live" and not self.source.isdecimal(): raise ValueError("live source must be a numeric camera index")
