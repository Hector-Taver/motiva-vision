import json
import os
import argparse
from collections import defaultdict, deque
import cv2
from ultralytics import YOLO
from classifier import EventManager
from publisher import EventPublisher

VEHICLE_CLASSES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
HISTORY_SECONDS = 3.0
MIN_TRACK_S = 1.5
MIN_WIDTH_FRAC = 0.08


def track_features(history, fps):
    """history: deque of (t, cx, cy, w, h). Returns None if not enough data."""
    if len(history) < int(fps * MIN_TRACK_S):
        return None
    pts = list(history)
    n_half = int(fps * 0.5)
    recent, before = pts[-n_half:], pts[-2 * n_half:-n_half]
    if len(before) < 2 or len(recent) < 2:
        return None

    def motion(seg):
        dt = seg[-1][0] - seg[0][0]
        if dt <= 0:
            return 0.0, 0.0, 0.0
        size = max(seg[-1][3], 1)
        vx = (seg[-1][1] - seg[0][1]) / dt / size
        vy = (seg[-1][2] - seg[0][2]) / dt / size
        return (vx**2 + vy**2) ** 0.5, vx, vy

    def aspect(seg):
        return sum(p[4] / max(p[3], 1) for p in seg) / len(seg)

    sn, vxn, vyn = motion(recent)
    sb, vxb, vyb = motion(before)
    return {
        "speed_now": sn, "speed_before": sb,
        "vel_now": (vxn, vyn), "vel_before": (vxb, vyb),
        "aspect_now": aspect(recent), "aspect_before": aspect(before),
        "cx": pts[-1][1], "cy": pts[-1][2], "w": pts[-1][3], "h": pts[-1][4],
    }


def process(video_path, show=True, publish=True):
    model = YOLO("yolov8n.pt")
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    maxlen = int(fps * HISTORY_SECONDS)
    tracks = defaultdict(lambda: deque(maxlen=maxlen))
    mgr = EventManager(os.path.basename(video_path))
    pub = EventPublisher() if publish else None
    events=[]
    frame_idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_idx / fps
        result = model.track(
            frame, persist=True, classes=list(VEHICLE_CLASSES),
            tracker="bytetrack.yaml", verbose=False,
        )[0]

        frame_feats = {}
        if result.boxes.id is not None:
            boxes = result.boxes.xyxy.cpu().numpy().astype(float)
            ids = result.boxes.id.int().cpu().tolist()
            for (x1, y1, x2, y2), tid in zip(boxes, ids):
                tracks[tid].append((t, (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1))
                feats = track_features(tracks[tid], fps)
                if feats and (x2 - x1) >= MIN_WIDTH_FRAC * frame.shape[1]:
                    frame_feats[tid] = feats
                if show:
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    label = f"#{tid}"
                    if feats:
                        label += f" v={feats['speed_now']:.1f} ar={feats['aspect_now']:.2f}"
                    cv2.putText(frame, label, (int(x1), int(y1) - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

        ev = mgr.update(t, frame, frame_feats)
        if ev:
            events.append(ev)
            print(json.dumps(ev, ensure_ascii=False, indent=2, default=float))
            pub.publish(ev)

        if frame_idx % 15 == 0:
            print(f"t={t:6.2f}s  tracked={len(frame_feats)}")

        if show:
            cv2.imshow("Motiva Vision", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        frame_idx += 1

    cap.release()
    cv2.destroyAllWindows()
    if pub:
        pub.close()
    if mgr.count == 0:
        print("Resultado: SEM_ACIDENTE")
    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--no-show", action="store_true")
    args = ap.parse_args()
    process(args.video, show=not args.no_show)
