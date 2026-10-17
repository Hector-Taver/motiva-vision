import argparse
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vision"))
from classifier import recommend  # noqa: E402
from publisher import EventPublisher  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--tipo", default="TRASEIRA")
ap.add_argument("--conf", type=float, default=0.8)
ap.add_argument("--parada", action="store_true", help="sudden-stop indicator")
ap.add_argument("--id", default=None, help="reuse an id to test duplicate handling")
ap.add_argument("--update", action="store_true", help="mark as an update of the same event")
a = ap.parse_args()

sinais = {"decel": 1.0 if a.parada else 0.5}
tz = timezone(timedelta(hours=-3))
ev = {
    "event_id": a.id or f"EVT-TEST-{uuid.uuid4().hex[:6]}",
    "timestamp": datetime.now(tz).isoformat(timespec="seconds"),
    "camera_id": "CAM-01", "local": "Rodovia Ficticia km 42",
    "tipo": a.tipo, "confianca": a.conf,
    "indicadores": {"veiculos": 2, "faixa_obstruida": "desconhecido",
                    "parada_brusca": a.parada, "sinais": sinais},
    "recomendacao": recommend(a.tipo, a.conf, sinais),
    "estado": "SUSPEITA",
    "evidencia": {"video": "fake.mp4", "segundo": 12.4, "frame": "evidence/frames/fake.jpg"},
    "atualizacao": a.update,
}
pub = EventPublisher()
pub.publish(ev)
pub.close()
print("Published", ev["event_id"])
