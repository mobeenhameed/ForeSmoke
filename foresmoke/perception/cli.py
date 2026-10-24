from __future__ import annotations
import argparse, json, logging, sys
from pathlib import Path
from .config import PerceptionConfig
from .pipeline import iter_observations

def main(argv=None):
    p=argparse.ArgumentParser(description="ForeSmoke current-frame perception to JSONL")
    p.add_argument("--source",required=True); p.add_argument("--source-kind",required=True,choices=["live","video","image"])
    p.add_argument("--camera-id",required=True); p.add_argument("--view-id",required=True)
    p.add_argument("--output",default="-"); p.add_argument("--model",default="yolo26n.pt"); p.add_argument("--source-fps",type=float)
    p.add_argument("--conf",type=float,default=.15); p.add_argument("--imgsz",type=int,default=640); p.add_argument("--target-hz",type=float,default=5)
    p.add_argument("--save-frames",action="store_true"); p.add_argument("--run-dir",default="runs/default"); p.add_argument("--preview",action="store_true")
    a=p.parse_args(argv); logging.basicConfig(stream=sys.stderr,level=logging.INFO,format="%(levelname)s %(message)s")
    cfg=PerceptionConfig(source=a.source,source_kind=a.source_kind,camera_id=a.camera_id,view_id=a.view_id,output=a.output,model=a.model,source_fps=a.source_fps,conf=a.conf,imgsz=a.imgsz,target_hz=a.target_hz,save_frames=a.save_frames,preview=a.preview,run_dir=Path(a.run_dir))
    if a.output!="-": Path(a.output).parent.mkdir(parents=True,exist_ok=True)
    out=sys.stdout if a.output=="-" else open(a.output,"w",encoding="utf-8")
    try:
        failed=False
        for obs in iter_observations(cfg):
            out.write(obs.model_dump_json()); out.write("\n"); out.flush()
            if obs.status=="unavailable": logging.error("source/model unavailable (%s)",obs.error); failed=True
        return 2 if failed else 0
    except KeyboardInterrupt: return 130
    finally:
        if out is not sys.stdout: out.close()

if __name__=="__main__": raise SystemExit(main())
