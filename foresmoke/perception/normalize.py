from __future__ import annotations
import math
from .detector import RawDetection
from foresmoke.shared.contracts import Detection

def normalize_detections(items: list[RawDetection], width: int, height: int):
    result=[]; rejected=0
    for d in items:
        x1,y1,x2,y2=d.xyxy
        if not all(math.isfinite(x) for x in (*d.xyxy,d.confidence)) or width<=0 or height<=0:
            rejected+=1; continue
        vals=(max(0,min(1,x1/width)),max(0,min(1,y1/height)),max(0,min(1,x2/width)),max(0,min(1,y2/height)))
        if not (vals[0]<vals[2] and vals[1]<vals[3]): rejected+=1; continue
        label=d.label.strip().lower().replace(" ","_")
        try: result.append(Detection(label=label,confidence=max(0,min(1,d.confidence)),bbox_xyxy=list(vals),track_id=d.track_id))
        except Exception: rejected+=1
    return result,rejected
