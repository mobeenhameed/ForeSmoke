import unittest
from uuid import UUID
from foresmoke.shared.contracts import Detection, FrameMeta, FrameObservation, Zone
from foresmoke.perception.detector import RawDetection
from foresmoke.perception.normalize import normalize_detections

class ContractTests(unittest.TestCase):
    def test_normalization_known_rectangle(self):
        got,bad=normalize_detections([RawDetection("person",.8,(320,180,640,360))],1280,720)
        self.assertEqual(bad,0); self.assertEqual(got[0].bbox_xyxy,[.25,.25,.5,.5])

    def test_invalid_or_outside_boxes_rejected(self):
        got,bad=normalize_detections([RawDetection("person",.8,(1,1,1,2)),RawDetection("person",.8,(1300,400,1400,600))],1280,720)
        self.assertEqual(got,[]); self.assertEqual(bad,2)

    def test_valid_and_empty_observation(self):
        common=dict(schema_version="1.0",run_id="01234567-89ab-4cde-8fab-0123456789ab",camera_id="cam01",view_id="exit-view-v1",source_kind="video",seq=0,t_ms=0,tracker_epoch=0,status="ok",frame=FrameMeta(index=0,width=1280,height=720,frame_ref=None),error=None)
        self.assertEqual(FrameObservation(**common,detections=[]).detections,[])
        with self.assertRaises(ValueError): FrameObservation(**{**common,"frame":None},detections=[])

    def test_bad_bbox_and_polygon_rejected(self):
        with self.assertRaises(ValueError): Detection(label="chair",confidence=.8,bbox_xyxy=(.5,.5,.4,.8),track_id=None)
        base=dict(zone_id="z",hazard_type="blocked_exit",candidate_labels=("chair",),min_confidence=.35,footprint_height_fraction=.2,enter_overlap=.3,exit_overlap=.15,person_occlusion_ratio=.2,confirm_ms=5000,candidate_clear_ms=1000,resolve_ms=3000)
        with self.assertRaises(ValueError): Zone(**base,polygon=((.1,.1),(.9,.9),(.1,.9),(.9,.1)))

if __name__=="__main__": unittest.main()
