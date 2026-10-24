from types import SimpleNamespace
from unittest.mock import patch
from foresmoke.perception.config import PerceptionConfig
from foresmoke.perception.detector import RawDetection
from foresmoke.perception.pipeline import iter_observations
from foresmoke.reasoning import ReasoningEngine, load_config

class Source:
    failed=False
    dropped_live_frames=0
    def __init__(self,*args): pass
    def frames(self,*args,**kwargs):
        for i in range(51):
            yield SimpleNamespace(image=SimpleNamespace(shape=(720,1280,3),positive=i<=30),index=i*6,t_ms=i*200)
    def close(self): pass
class Detector:
    names={0:'chair'}
    def __init__(self,*args): pass
    def infer(self,frame):
        return [RawDetection('chair',.9,(486.4,201.6,716.8,604.8),'7')] if frame.positive else []

def test_perception_to_reasoning_lifecycle():
    engine=ReasoningEngine(load_config('tests/fixtures/zones.json'),live_watchdog=False)
    cfg=PerceptionConfig('fixture','video','cam01','exit-view-v1')
    events=[]
    with patch('foresmoke.perception.pipeline.OpenCVSource',Source),patch('foresmoke.perception.pipeline.UltralyticsDetector',Detector):
        for p in iter_observations(cfg): events.extend(engine.process(p,p.seq))
    assert [(e.event_type,e.t_ms) for e in events]==[('incident_confirmed',5000),('incident_resolved',9200)]
    assert events[0].incident_id==events[1].incident_id

def test_inference_failure_is_unavailable():
    cfg=PerceptionConfig('fixture','video','cam01','exit-view-v1')
    with patch('foresmoke.perception.pipeline.OpenCVSource',Source),patch('foresmoke.perception.pipeline.UltralyticsDetector',Detector),patch.object(Detector,'infer',side_effect=RuntimeError('model failure')):
        packets=list(iter_observations(cfg))
    assert len(packets)==1 and packets[0].status=='unavailable'
    assert packets[0].error=='inference_failed'

def test_integrated_runner_writes_lifecycle(tmp_path):
    import json
    from foresmoke.demo import main
    with patch('foresmoke.perception.pipeline.OpenCVSource',Source),patch('foresmoke.perception.pipeline.UltralyticsDetector',Detector):
        result=main(['--source','fixture','--source-kind','video','--zones','tests/fixtures/zones.json','--run-dir',str(tmp_path)])
    assert result==0
    events=[json.loads(x) for x in (tmp_path/'events.jsonl').read_text().splitlines()]
    assert [e['t_ms'] for e in events]==[5000,9200]
    states=[json.loads(x) for x in (tmp_path/'states.jsonl').read_text().splitlines()]
    assert states[-1]['observation_reason']=='source_ended'

def test_runner_logs_unknown_during_live_stall(tmp_path):
    import json,time
    from foresmoke.demo import main
    from foresmoke.shared.contracts import FrameObservation
    def stalled(*args,**kwargs):
        yield FrameObservation(schema_version='1.0',run_id='01234567-89ab-4cde-8fab-0123456789ab',camera_id='cam01',view_id='exit-view-v1',source_kind='live',seq=0,t_ms=0,tracker_epoch=0,status='ok',frame={'index':0,'width':1280,'height':720,'frame_ref':None},detections=[],error=None)
        time.sleep(2.3)
    with patch('foresmoke.demo.iter_observations',stalled):
        assert main(['--source','0','--source-kind','live','--zones','tests/fixtures/zones.json','--run-dir',str(tmp_path)])==0
    states=[json.loads(x) for x in (tmp_path/'states.jsonl').read_text().splitlines()]
    assert any(s['observation_reason']=='stale_input' and s['observation_state']=='unknown' for s in states)
    assert not (tmp_path/'events.jsonl').read_text()
