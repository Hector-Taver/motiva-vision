import math
import os
from datetime import datetime, timedelta, timezone

import cv2

# ---- Tunable constants (documented in the report) ----
MOVING = 0.4          # min speed (body widths/s) to count as "was moving"
DROP = 0.6            # speed drop of 60% = full deceleration signal
ASPECT_JUMP = 1.5     # change in height/width ratio = full rollover shape signal
PARALLEL_DEG = 30     # velocity angle below this = same direction
TURN_DEG = 45         # velocity angle above this = crossing paths
PROX = 1.2            # "near" = centres within 1.2 average body widths
THRESH = 0.60         # confidence >= THRESH -> classified event
LOW = 0.35            # LOW <= confidence < THRESH -> INDETERMINADO
COOLDOWN = 6.0        # seconds without signal before an event is closed
ABS_DROP = 0.3        # speed must also fall by at least this much (body widths/s)
DEPTH_RATIO = 1.6     # vehicles must have similar box widths to count as "near"
PERSIST = 0.5         # seconds a signal must hold before an event is created
TZ = timezone(timedelta(hours=-3))


def clip01(x):
    return max(0.0, min(1.0, x))


def angle(a, b):
    na, nb = math.hypot(*a), math.hypot(*b)
    if na < 1e-6 or nb < 1e-6:
        return None
    c = (a[0] * b[0] + a[1] * b[1]) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def decel(f):
    """0..1: how strongly a vehicle that WAS moving has slowed down."""
    if f["speed_before"] < MOVING or f["speed_before"] - f["speed_now"] < ABS_DROP:
        return 0.0
    return clip01((1 - f["speed_now"] / f["speed_before"]) / DROP)


def closeness(a, b):
    ratio = max(a["w"], b["w"]) / max(min(a["w"], b["w"]), 1)
    if ratio > DEPTH_RATIO:
        return 0.0  # different depth: image overlap is not real proximity
    d = math.hypot(a["cx"] - b["cx"], a["cy"] - b["cy"])
    return clip01(1 - d / (PROX * (a["w"] + b["w"]) / 2))


def analyse(feats):
    """Returns candidate list: {tipo, score, ids, signals}."""
    cands = []
    ids = list(feats)

    # Rollover: single vehicle, shape change + abrupt deceleration
    for tid in ids:
        f = feats[tid]
        ratio = f["aspect_now"] / max(f["aspect_before"], 1e-6)
        jump = max(ratio, 1 / ratio)
        shape = clip01((jump - 1) / (ASPECT_JUMP - 1))
        d = decel(f)
        if shape > 0.5 and d > 0:
            cands.append({"tipo": "CAPOTAMENTO", "score": 0.5 * shape + 0.5 * d,
                          "ids": [tid], "signals": {"shape": shape, "decel": d}})

    # Pairs: rear-end vs side collision
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = feats[ids[i]], feats[ids[j]]
            c = closeness(a, b)
            da, db = decel(a), decel(b)
            if c <= 0 or max(da, db) <= 0:
                continue  # proximity alone is not evidence of a collision
            ang = angle(a["vel_before"], b["vel_before"])
            penalty = 1.0
            if ang is None:  # one vehicle was stopped: assume parallel, lower confidence
                ang, penalty = 0.0, 0.85
            hi, lo = max(da, db), min(da, db)
            if ang >= TURN_DEG:
                s = 0.4 * hi + 0.3 * clip01(ang / 90) + 0.3 * c
                tipo = "LATERAL"
            elif ang <= PARALLEL_DEG:
                s = 0.4 * hi + 0.3 * lo + 0.3 * c
                tipo = "TRASEIRA"
            else:  # ambiguous angle
                s = 0.5 * (0.5 * hi + 0.5 * c)
                tipo = "INDETERMINADO"
            cands.append({"tipo": tipo, "score": s * penalty, "ids": [ids[i], ids[j]],
                          "signals": {"decel": hi, "angle": round(ang, 1), "closeness": c}})
    return cands

def recommend(tipo, conf, sinais):
    """Fictional recommendation matrix (not a medical protocol)."""
    if tipo == "INDETERMINADO" or conf < THRESH:
        return {"recurso": "Revisao do operador, sem concluir tipo ou gravidade",
                "regra": "R4", "justificativa": "Imagem ambigua ou baixa confianca"}
    if tipo == "CAPOTAMENTO":
        return {"recurso": "Avaliacao prioritaria + recurso adicional de resgate (simulado)",
                "regra": "R3", "justificativa": "Capotamento identificado"}
    if sinais.get("decel", 0) >= 0.99:
        return {"recurso": "Ambulancia (simulado)", "regra": "R2",
                "justificativa": f"Colisao {tipo.lower()} com parada brusca observada"}
    return {"recurso": "Solicitar avaliacao da equipe operacional", "regra": "R1",
            "justificativa": "Evento sem indicadores adicionais de risco"}

class EventManager:
    """Turns per-frame candidates into ONE event per accident."""

    def __init__(self, video_name, camera_id="CAM-01", local="Rodovia Ficticia km 42",
                 out_dir="evidence/frames"):
        self.video, self.camera_id, self.local = video_name, camera_id, local
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.run_id = datetime.now(TZ).strftime("%Y%m%d-%H%M%S")
        self.count = 0
        self.active = None
        self.sig_since = None
        self.last_sig = None

    def _make(self, best, t, frame, update=False):
        if self.active is None:
            self.count += 1
        event_id = (self.active["event_id"] if self.active
                    else f"EVT-{self.run_id}-{self.count:02d}")
        tipo = best["tipo"] if best["score"] >= THRESH else "INDETERMINADO"
        frame_path = os.path.join(self.out_dir, f"{event_id}.jpg")
        cv2.imwrite(frame_path, frame)
        sinais = best["signals"]
        ev = {
            "event_id": event_id,
            "timestamp": datetime.now(TZ).isoformat(timespec="seconds"),
            "camera_id": self.camera_id,
            "local": self.local,
            "tipo": tipo,
            "confianca": round(best["score"], 2),
            "indicadores": {"veiculos": len(best["ids"]), "track_ids": best["ids"],
                "faixa_obstruida": "desconhecido",
                "parada_brusca": bool(sinais.get("decel", 0) >= 0.99),
                "sinais": sinais},
            "recomendacao": recommend(tipo, best["score"], sinais),
            "estado": "SUSPEITA",
            "evidencia": {"video": self.video, "segundo": round(t, 2), "frame": frame_path},
            "atualizacao": update,
        }
        self.active = {"event_id": event_id, "last_t": t, "tipo": tipo, "conf": best["score"]}
        return ev

    def update(self, t, frame, feats):
        """Returns an event dict when a NEW event (or an upgrade) should be published."""
        cands = analyse(feats)
        best = max(cands, key=lambda c: c["score"], default=None)
        has_signal = best is not None and best["score"] >= LOW

        if self.active and t - self.active["last_t"] > COOLDOWN:
            self.active = None  # event ended

        if not has_signal:
            return None

        # persistence: the signal must hold continuously (gaps up to 0.3 s tolerated)
        if self.last_sig is None or t - self.last_sig > 0.3:
            self.sig_since = t
        self.last_sig = t

        if self.active is None:
            if t - self.sig_since < PERSIST:
                return None
            return self._make(best, t, frame)

        self.active["last_t"] = t
        if self.active["tipo"] == "INDETERMINADO" and best["score"] >= THRESH:
            return self._make(best, t, frame, update=True)
        return None
