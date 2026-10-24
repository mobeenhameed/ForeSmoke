"""One consumer owns reasoning and GUI; capture/inference run independently."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import logging
from pathlib import Path
import queue
import threading
import time
from .perception.config import PerceptionConfig
from .perception.pipeline import iter_observations
from .reasoning import ReasoningEngine, load_config


def now_ms():
    return time.monotonic_ns() // 1_000_000


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', required=True)
    p.add_argument('--source-kind', required=True, choices=['live','video','image'])
    p.add_argument('--zones', required=True)
    p.add_argument('--run-dir', required=True)
    p.add_argument('--preview', action='store_true')
    p.add_argument('--model', default='yolo26n.pt')
    p.add_argument('--imgsz', type=int, default=640)
    p.add_argument('--source-fps', type=float)
    p.add_argument('--target-hz', type=float, default=5.0)
    p.add_argument('--record-video', action='store_true',
                   help='save clean camera footage and synchronized three-panel MP4')
    p.add_argument('--record-panel-videos', action='store_true',
                   help='also save separate detection and final-result MP4 files')
    p.add_argument('--raw-fps', type=float, default=15.0,
                   help='frame rate for the clean camera recording')
    p.add_argument('--panel-width', type=int, default=640)
    a=p.parse_args(argv)
    if a.record_panel_videos: a.record_video=True
    logging.basicConfig(level=logging.INFO)
    config=load_config(a.zones)
    engine=ReasoningEngine(config, live_watchdog=a.source_kind=='live')
    out=Path(a.run_dir); out.mkdir(parents=True,exist_ok=True)
    names=['observations.jsonl','events.jsonl','states.jsonl']
    video_names=['raw_footage.mp4','combined_three_panel.mp4']
    if a.record_panel_videos: video_names += ['detections.mp4','final_results.mp4']
    protected = names + (video_names if a.record_video else [])
    if any((out/n).exists() for n in protected):
        p.error('Run directory already contains results; use a new --run-dir.')
    cfg=PerceptionConfig(source=a.source,source_kind=a.source_kind,camera_id=config.camera_id,
        view_id=config.view_id,run_dir=out,model=a.model,imgsz=a.imgsz,source_fps=a.source_fps,
        target_hz=a.target_hz)
    stop=threading.Event(); q=queue.Queue(maxsize=2); lock=threading.Lock()
    frames={}; done=object(); errors=[]
    raw_recorder=None
    def save_frame(seq, image):
        with lock:
            frames[seq]=image
            while len(frames)>2: frames.pop(next(iter(frames)))
    def offer(item):
        while not stop.is_set():
            try: q.put(item,timeout=.05); return
            except queue.Full:
                if a.source_kind=='live' and item is not done:
                    try: q.get_nowait(); logging.warning('Dropped oldest queued observation')
                    except queue.Empty: pass
    def produce():
        try:
            for packet in iter_observations(
                cfg,
                on_frame=save_frame if (a.preview or a.record_video) else None,
                on_raw_frame=raw_recorder if a.record_video else None,
                stop_event=stop,
            ):
                if stop.is_set(): break
                offer(packet)
        except Exception as exc:
            errors.append(exc)
        finally: offer(done)
    if a.preview or a.record_video:
        import cv2
        import numpy as np
        from .visualization import (
            RawVideoRecorder, VideoBundle, comparison_panel, detection_panel, result_panel,
        )
    worker=threading.Thread(target=produce,daemon=True,name='foresmoke-inference')
    failed=False; latest=None; last_packet=None; latest_event=None
    if a.record_video:
        raw_recorder=RawVideoRecorder(out/'raw_footage.mp4',a.raw_fps)
    video=(VideoBundle(out,a.target_hz,a.panel_width,a.record_panel_videos)
           if a.record_video else None)
    with ExitStack() as stack:
        obs,events,states=[stack.enter_context((out/n).open('x',encoding='utf-8')) for n in names]
        def snapshots():
            current=engine.snapshots()
            for state in current: states.write(state.model_dump_json()+'\n')
            states.flush()
            return current
        worker.start()
        try:
            while True:
                try: item=q.get(timeout=.05 if a.preview else .25)
                except queue.Empty: item=None
                if a.source_kind=='live': engine.tick(now_ms())
                if item is done: break
                if item is not None:
                    obs.write(item.model_dump_json()+'\n'); obs.flush()
                    failed |= item.status=='unavailable'
                    try:
                        for event in engine.process(item,now_ms()):
                            events.write(event.model_dump_json()+'\n')
                            logging.info('%s: %s',event.zone_id,event.event_type)
                            latest_event=f"{event.zone_id}: {event.event_type.replace('_', ' ').upper()} at {event.t_ms / 1000:.1f}s"
                        events.flush()
                    except ValueError as exc:
                        failed=True; logging.error('Rejected observation: %s',exc)
                    last_packet=item
                    if a.preview or a.record_video:
                        with lock: latest=frames.get(item.seq)
                current=snapshots()
                if item is not None and latest is not None and last_packet is not None and last_packet.status=='ok':
                    detected=detection_panel(latest,last_packet)
                    result=result_panel(latest,config.zones,current,latest_event)
                    canvas=(video.write(latest,detected,result,last_packet.t_ms) if video else
                            comparison_panel(latest,detected,result,last_packet.t_ms,a.panel_width))
                    if a.preview:
                        display=canvas
                        if display.shape[1] > 1600:
                            scale=1600/display.shape[1]
                            display=cv2.resize(display,(1600,round(display.shape[0]*scale)))
                        cv2.imshow('ForeSmoke: raw | detections | final result',display)
                        if cv2.waitKey(1)&0xff==ord('q'): break
                elif a.preview:
                    waiting=np.zeros((360,960,3),dtype=np.uint8)
                    cv2.putText(waiting,'Waiting for fresh observation',(20,190),cv2.FONT_HERSHEY_SIMPLEX,
                                .8,(0,180,255),2)
                    cv2.imshow('ForeSmoke: raw | detections | final result',waiting)
                    if cv2.waitKey(1)&0xff==ord('q'): break
        except KeyboardInterrupt:
            pass
        finally:
            stop.set(); engine.close_input(); snapshots(); worker.join(timeout=1)
            if raw_recorder: raw_recorder.close()
            if video: video.close()
            if a.preview: cv2.destroyAllWindows()
    if errors:
        logging.error('Producer failed: %s',errors[0]); failed=True
    if raw_recorder and raw_recorder.error:
        logging.error('Raw video recording failed: %s',raw_recorder.error); failed=True
    return 2 if failed else 0

if __name__=='__main__': raise SystemExit(main())
