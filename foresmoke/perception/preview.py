def draw_preview(frame, detections, t_ms):
    import cv2
    h,w=frame.shape[:2]
    for d in detections:
        x1,y1,x2,y2=d.bbox_xyxy
        p1=(max(0,min(w-1,round(x1*w))),max(0,min(h-1,round(y1*h))))
        p2=(max(0,min(w-1,round(x2*w))),max(0,min(h-1,round(y2*h))))
        cv2.rectangle(frame,p1,p2,(30,200,80),2)
        cv2.putText(frame,f"{d.label} {d.confidence:.2f} / {d.track_id or 'untracked'}",(p1[0],max(18,p1[1]-6)),cv2.FONT_HERSHEY_SIMPLEX,.5,(30,200,80),1)
    cv2.putText(frame,f"t={t_ms} ms",(10,22),cv2.FONT_HERSHEY_SIMPLEX,.6,(255,255,255),2)
    return frame
