from __future__ import annotations
import logging
import time
from collections import deque
from uuid import uuid4
from pathlib import Path
from foresmoke.shared.contracts import FrameMeta, FrameObservation
from .config import PerceptionConfig
from .detector import UltralyticsDetector
from .normalize import normalize_detections
from .sources import OpenCVSource

log=logging.getLogger("foresmoke.perception")

def iter_observations(config: PerceptionConfig, *, on_frame=None, on_raw_frame=None, stop_event=None):
    """Yield validated observations. Model and capture errors become unavailable."""
    run_id=str(config.run_id or uuid4()); seq=0; epoch=0
    try:
        source=OpenCVSource(config.source,config.source_kind,config.source_fps)
    except Exception as e:
        yield FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=0,tracker_epoch=epoch,status="unavailable",frame=None,detections=[],error="capture_failed")
        log.error("source initialization failed: %s",e); return
    detector=None; inference_ms=deque(maxlen=2000); started=None; emitted=0
    try:
        try: detector=UltralyticsDetector(config.model,config.conf,config.imgsz)
        except Exception as e:
            yield FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=0,tracker_epoch=epoch,status="unavailable",frame=None,detections=[],error="model_unavailable")
            log.error("model initialization failed: %s",e); return
        log.info("model classes: %s",detector.names)
        started=time.monotonic()
        last_t=-1
        for captured in source.frames(config.target_hz,on_raw_frame=on_raw_frame):
            if stop_event is not None and stop_event.is_set(): break
            t0=time.perf_counter(); h,w=captured.image.shape[:2]
            try:
                raw=detector.infer(captured.image)
                inference_ms.append((time.perf_counter()-t0)*1000)
                detections,rejected=normalize_detections(raw,w,h)
                if rejected: log.warning("rejected %d invalid detections",rejected)
                if config.preview:
                    from .preview import draw_preview
                    import cv2
                    cv2.imshow("ForeSmoke perception",draw_preview(captured.image.copy(),detections,captured.t_ms))
                    if cv2.waitKey(1)&0xFF==ord("q"): break
                frame_ref=None
                if config.save_frames:
                    import cv2
                    out=Path(config.run_dir)/"frames"; out.mkdir(parents=True,exist_ok=True)
                    rel=f"frames/frame_{seq:06d}.jpg"; cv2.imwrite(str(Path(config.run_dir)/rel),captured.image); frame_ref=rel
                packet=FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=max(captured.t_ms,last_t),tracker_epoch=epoch,status="ok",frame=FrameMeta(index=captured.index,width=w,height=h,frame_ref=frame_ref),detections=detections,error=None)
                if on_frame is not None: on_frame(packet.seq, captured.image.copy())
                last_t=packet.t_ms
                log.debug("seq=%d source_t=%d inference_ms=%.1f",seq,packet.t_ms,(time.perf_counter()-t0)*1000)
                emitted+=1
                yield packet; seq+=1
            except Exception as e:
                log.exception("inference failed at source frame %d",captured.index)
                yield FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=max(captured.t_ms,last_t,0),tracker_epoch=epoch,status="unavailable",frame=None,detections=[],error="inference_failed")
                seq+=1; return
        if config.source_kind=="live" and source.failed:
            yield FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=max(last_t,0),tracker_epoch=epoch,status="unavailable",frame=None,detections=[],error="capture_failed")
        elif config.source_kind!="live":
            yield FrameObservation(schema_version="1.0",run_id=run_id,camera_id=config.camera_id,view_id=config.view_id,source_kind=config.source_kind,seq=seq,t_ms=max(last_t,0),tracker_epoch=epoch,status="eof",frame=None,detections=[],error=None)
    finally:
        source.close()
        if inference_ms and started is not None:
            ordered=sorted(inference_ms)
            p50=ordered[int(.50*(len(ordered)-1))]; p95=ordered[int(.95*(len(ordered)-1))]
            elapsed=max(time.monotonic()-started,1e-9)
            log.info("run metrics: observations=%d observed_hz=%.2f inference_p50_ms=%.1f inference_p95_ms=%.1f live_frames_dropped=%d",emitted,emitted/elapsed,p50,p95,source.dropped_live_frames)
        if config.preview:
            try:
                import cv2
                cv2.destroyAllWindows()
            except ImportError: pass
