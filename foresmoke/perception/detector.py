from __future__ import annotations
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class RawDetection:
    label: str
    confidence: float
    xyxy: tuple[float,float,float,float]
    track_id: str | None = None

def _extract(result, names, tracked=False):
    boxes=getattr(result,"boxes",None)
    if boxes is None: return []
    xyxy=boxes.xyxy.cpu().tolist(); conf=boxes.conf.cpu().tolist(); cls=boxes.cls.cpu().tolist()
    ids=None
    if tracked and getattr(boxes,"id",None) is not None: ids=boxes.id.cpu().tolist()
    out=[]
    for i,(box,score,cid) in enumerate(zip(xyxy,conf,cls)):
        label=str(names[int(cid)]).strip().lower().replace(" ","_")
        tid=None if ids is None else str(int(ids[i]))
        out.append(RawDetection(label,float(score),tuple(float(x) for x in box),tid))
    return out

class UltralyticsDetector:
    """YOLO adapter preserving raw current detections and matching ByteTrack IDs.

    Uses a raw predict plus track call because tracker output can suppress current
    unmatched detections. This deliberately favors contract correctness over speed.
    """
    def __init__(self, weights="yolo26n.pt", conf=.15, imgsz=640, tracker="bytetrack.yaml", device="cpu"):
        try: from ultralytics import YOLO
        except ImportError as e: raise RuntimeError("Install ultralytics with pip install -e '.[perception]'") from e
        self.raw_model=YOLO(weights); self.model=YOLO(weights); self.conf=conf; self.imgsz=imgsz; self.tracker=tracker; self.device=device
        self.names=self.model.names; self.resets=0

    @staticmethod
    def _iou(a,b):
        x1=max(a[0],b[0]); y1=max(a[1],b[1]); x2=min(a[2],b[2]); y2=min(a[3],b[3])
        inter=max(0,x2-x1)*max(0,y2-y1)
        area_a=max(0,a[2]-a[0])*max(0,a[3]-a[1]); area_b=max(0,b[2]-b[0])*max(0,b[3]-b[1])
        return inter/(area_a+area_b-inter) if area_a+area_b>inter else 0

    def infer(self, frame):
        raw_result=self.raw_model.predict(frame,conf=self.conf,imgsz=self.imgsz,verbose=False,device=self.device)[0]
        tracked_result=self.model.track(frame,persist=True,tracker=self.tracker,conf=self.conf,imgsz=self.imgsz,verbose=False,device=self.device)[0]
        raw=_extract(raw_result,self.names); tracked=_extract(tracked_result,self.names,tracked=True)
        # Deterministic highest-IoU-first, same-class, one-to-one matching.
        pairs=sorted(((self._iou(a.xyxy,b.xyxy),i,j) for i,a in enumerate(raw) for j,b in enumerate(tracked) if a.label==b.label),reverse=True)
        used_a=set(); used_b=set(); ids={}
        for score,i,j in pairs:
            if score<.5: break
            if i not in used_a and j not in used_b:
                used_a.add(i); used_b.add(j)
                if tracked[j].track_id is not None: ids[i]=tracked[j].track_id
        return [RawDetection(d.label,d.confidence,d.xyxy,ids.get(i)) for i,d in enumerate(raw)]

    def reset(self):
        # A new model instance guarantees a fresh per-source tracker.
        self.model.reset_callbacks() if hasattr(self.model,"reset_callbacks") else None
        self.resets+=1
