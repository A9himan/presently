"""
face_service.py - server-side face recognition for Presently.

Replaces every fake embedding in the project (sine hashes, 64x64 pixel
projections, landmark-distance vectors) with real ArcFace-style 512-d
embeddings from InsightFace (buffalo_s, ONNX, CPU only).

Design rules
------------
* The server computes everything. Clients send raw JPEG frames only.
* Fail closed: any engine error or doubt becomes REJECT / REVIEW / ERROR,
  never ACCEPT. Only decision == "ACCEPT" may mark attendance.
* Thresholds below are STARTING POINTS. Calibrate them on your own
  photos with eval/calibrate.py before trusting them.
"""
import sys
import base64
import binascii
import logging
import os
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

# Ensure local virtualenv packages are found
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_PACKAGES = os.path.join(ROOT_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES) and SITE_PACKAGES not in sys.path:
    sys.path.insert(0, SITE_PACKAGES)

import cv2
import numpy as np

log = logging.getLogger("presently.face")

MODEL_NAME = os.environ.get("FACE_MODEL", "buffalo_s")
MODEL_VERSION = f"insightface-{MODEL_NAME}"
EMBED_DIM = 512


def _env_f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


@dataclass(frozen=True)
class Config:
    # --- decision thresholds (cosine similarity) ---
    accept_threshold: float = _env_f("FACE_ACCEPT_THRESHOLD", 0.45)
    review_threshold: float = _env_f("FACE_REVIEW_THRESHOLD", 0.35)
    margin: float = _env_f("FACE_MARGIN", 0.08)        # top1 - top2 must exceed this
    min_vote_share: float = _env_f("FACE_MIN_VOTE", 0.6)  # share of frames agreeing
    frame_consistency: float = _env_f("FACE_FRAME_CONSISTENCY", 0.35)  # min pairwise cos across probe frames
    # --- detector ---
    det_size: int = int(_env_f("FACE_DET_SIZE", 640))
    dominant_ratio: float = 2.0     # verify: largest face must be 2x the next
    # --- verification frame quality (looser: people are moving) ---
    verify_min_face_px: int = int(_env_f("FACE_VERIFY_MIN_PX", 80))
    verify_min_det: float = 0.60
    verify_min_blur: float = _env_f("FACE_VERIFY_MIN_BLUR", 40.0)
    verify_max_yaw: float = 0.45
    verify_min_frames: int = int(_env_f("FACE_VERIFY_MIN_FRAMES", 3))
    # --- enrollment frame quality (stricter) ---
    enroll_min_face_px: int = int(_env_f("FACE_ENROLL_MIN_PX", 112))
    enroll_min_det: float = 0.75
    enroll_min_blur: float = _env_f("FACE_ENROLL_MIN_BLUR", 60.0)
    enroll_max_yaw: float = 0.30
    enroll_min_frames: int = int(_env_f("FACE_ENROLL_MIN_FRAMES", 3))
    enroll_max_frames: int = 5
    enroll_consistency: float = 0.4
    # --- shared ---
    min_brightness: float = 50.0
    max_brightness: float = 225.0

_LITE_NAME = MODEL_NAME + "_lite"
_SKIP = ("3d68", "2d106", "genderage")   # models we never use


def _prepare_lite_pack() -> str:
    """
    FaceAnalysis creates an ONNX session for EVERY file in the model pack before it
    discards the ones you did not ask for, which spiked memory to ~650 MB at load
    (measured). We only need detection + recognition, so build a small pack that
    contains just those two files and load that instead (~220 MB total).
    Returns the insightface root directory to use.
    """
    import shutil
    from insightface.utils.storage import ensure_available

    root = os.path.expanduser(os.environ.get("FACE_MODEL_ROOT", "~/.insightface"))
    lite = os.path.join(root, "models", _LITE_NAME)
    if os.path.isdir(lite) and any(f.endswith(".onnx") for f in os.listdir(lite)):
        return root
    full = ensure_available("models", MODEL_NAME, root=root)   # downloads the pack once
    os.makedirs(lite, exist_ok=True)
    for fn in os.listdir(full):
        if fn.endswith(".onnx") and not any(k in fn for k in _SKIP):
            shutil.copyfile(os.path.join(full, fn), os.path.join(lite, fn))
    return root


class EngineUnavailable(RuntimeError):
    """The face model could not be loaded. Callers must fail closed."""


@dataclass
class Face:
    bbox: np.ndarray
    kps: np.ndarray
    det_score: float
    embedding: np.ndarray  # L2-normalised, float32, EMBED_DIM

    @property
    def width(self) -> float:
        return float(self.bbox[2] - self.bbox[0])

    @property
    def area(self) -> float:
        return float(max(self.bbox[2] - self.bbox[0], 0) * max(self.bbox[3] - self.bbox[1], 0))


@dataclass
class Quality:
    ok: bool
    reasons: List[str]
    metrics: Dict[str, float]


@dataclass
class FrameReport:
    index: int
    status: str                 # ok | decode_failed | no_face | multiple_faces | low_quality
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    face: Optional[Face] = None

    def public(self) -> dict:
        return {"index": self.index, "status": self.status,
                "reasons": self.reasons, "metrics": self.metrics}


@dataclass
class EnrollResult:
    ok: bool
    embedding: Optional[List[float]] = None          # fused, store as the main vector
    embeddings: List[List[float]] = field(default_factory=list)  # per frame, store for 1:N max-match
    model_version: str = MODEL_VERSION
    errors: List[str] = field(default_factory=list)
    frames: List[dict] = field(default_factory=list)


@dataclass
class MatchResult:
    decision: str               # ACCEPT | REVIEW | REJECT | ERROR
    reason: str
    student_id: Optional[str] = None
    score: float = 0.0
    runner_up_id: Optional[str] = None
    runner_up_score: float = 0.0
    margin: float = 0.0
    vote_share: float = 0.0
    frames_total: int = 0
    frames_used: int = 0
    model_version: str = MODEL_VERSION
    frames: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        for k in ("score", "runner_up_score", "margin", "vote_share"):
            d[k] = round(float(d[k]), 4)
        return d


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def decode_image(data, max_bytes: int = 6_000_000, max_side: int = 1280) -> Optional[np.ndarray]:
    """base64 string / data-URL / bytes -> BGR image, or None. Rejects SVG and junk."""
    try:
        if isinstance(data, str):
            if "<svg" in data[:200].lower() or "image/svg" in data[:64].lower():
                return None
            if data.startswith("data:") and "," in data:
                data = data.split(",", 1)[1]
            raw = base64.b64decode(data, validate=False)
        elif isinstance(data, (bytes, bytearray)):
            raw = bytes(data)
        else:
            return None
        if not raw or len(raw) > max_bytes:
            return None
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            return None
        h, w = img.shape[:2]
        if max(h, w) > max_side:
            s = max_side / float(max(h, w))
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        return img
    except (binascii.Error, ValueError, cv2.error):
        return None


def l2norm(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def valid_embedding(vec) -> bool:
    """Startup/DB guard: right length, finite, ~unit norm."""
    try:
        a = np.asarray(vec, dtype=np.float32)
        return a.shape == (EMBED_DIM,) and bool(np.isfinite(a).all()) and abs(float(np.linalg.norm(a)) - 1.0) < 0.05
    except (TypeError, ValueError):
        return False


def roc_summary(genuine: Sequence[float], impostor: Sequence[float]) -> dict:
    """FAR/FRR/EER and thresholds from labelled score lists. Pure numpy."""
    g, i = np.asarray(genuine, float), np.asarray(impostor, float)
    if g.size == 0 or i.size == 0:
        raise ValueError("need both genuine and impostor scores")
    ths = np.unique(np.concatenate([g, i, [-1.0, 1.0]]))
    far = np.array([(i >= t).mean() for t in ths])
    frr = np.array([(g < t).mean() for t in ths])
    k = int(np.argmin(np.abs(far - frr)))
    out = {"genuine_n": int(g.size), "impostor_n": int(i.size),
           "eer": float((far[k] + frr[k]) / 2), "eer_threshold": float(ths[k]),
           "genuine_mean": float(g.mean()), "impostor_mean": float(i.mean()),
           "impostor_max": float(i.max()), "genuine_min": float(g.min())}
    for target in (0.01, 0.001):
        ok = np.where(far <= target)[0]
        t = float(ths[ok[0]]) if ok.size else float(i.max()) + 1e-3
        out[f"threshold_far_{target}"] = t
        out[f"frr_at_far_{target}"] = float((g < t).mean())
    return out


# --------------------------------------------------------------------------
# service
# --------------------------------------------------------------------------
class FaceService:
    def __init__(self, config: Optional[Config] = None):
        self.cfg = config or Config()
        self._app = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()
        self.load_error: Optional[str] = None

    # ---- engine ----
    def _ensure(self):
        if self._app is not None:
            return
        with self._load_lock:
            if self._app is not None:
                return
            try:
                from insightface.app import FaceAnalysis
                root = _prepare_lite_pack()
                app = FaceAnalysis(name=_LITE_NAME, root=root, allowed_modules=["detection", "recognition"],
                                   providers=["CPUExecutionProvider"])
                app.prepare(ctx_id=-1, det_size=(self.cfg.det_size, self.cfg.det_size))
                self._app = app
                log.info("face engine ready (%s, det %d)", MODEL_VERSION, self.cfg.det_size)
            except Exception as e:  # model missing, download blocked, OOM...
                self.load_error = repr(e)
                log.error("face engine failed to load: %s", e)
                raise EngineUnavailable(self.load_error) from e

    def available(self) -> bool:
        try:
            self._ensure()
            return True
        except EngineUnavailable:
            return False

    def detect(self, img: np.ndarray) -> List[Face]:
        self._ensure()
        with self._infer_lock:
            found = self._app.get(img)
        faces = []
        for f in found:
            if getattr(f, "normed_embedding", None) is None:
                continue
            faces.append(Face(bbox=np.asarray(f.bbox, float), kps=np.asarray(f.kps, float),
                              det_score=float(f.det_score),
                              embedding=l2norm(f.normed_embedding)))
        return faces

    # ---- quality ----
    def assess(self, img: np.ndarray, face: Face, purpose: str) -> Quality:
        c = self.cfg
        enroll = purpose == "enroll"
        min_px = c.enroll_min_face_px if enroll else c.verify_min_face_px
        min_det = c.enroll_min_det if enroll else c.verify_min_det
        min_blur = c.enroll_min_blur if enroll else c.verify_min_blur
        max_yaw = c.enroll_max_yaw if enroll else c.verify_max_yaw

        h, w = img.shape[:2]
        x1, y1, x2, y2 = [int(round(v)) for v in face.bbox]
        reasons: List[str] = []
        m: Dict[str, float] = {"face_px": round(face.width, 1), "det_score": round(face.det_score, 3)}

        cx1, cy1, cx2, cy2 = max(x1, 0), max(y1, 0), min(x2, w), min(y2, h)
        if cx2 - cx1 < 8 or cy2 - cy1 < 8:
            return Quality(False, ["FACE_OFF_FRAME"], m)
        clipped = (x1 < -0.05 * face.width or y1 < -0.05 * face.width or
                   x2 > w + 0.05 * face.width or y2 > h + 0.05 * face.width)

        gray = cv2.cvtColor(img[cy1:cy2, cx1:cx2], cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (112, 112), interpolation=cv2.INTER_AREA)
        m["blur"] = round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1)
        m["brightness"] = round(float(gray.mean()), 1)

        k = face.kps
        eye_mid = (k[0] + k[1]) / 2.0
        eye_dist = float(np.linalg.norm(k[1] - k[0])) or 1.0
        m["yaw_proxy"] = round(float((k[2][0] - eye_mid[0]) / eye_dist), 3)

        if clipped:
            reasons.append("FACE_CUT_OFF")
        if face.width < min_px:
            reasons.append("FACE_TOO_SMALL")
        if face.det_score < min_det:
            reasons.append("LOW_DETECTION_CONFIDENCE")
        if m["blur"] < min_blur:
            reasons.append("TOO_BLURRY")
        if m["brightness"] < c.min_brightness:
            reasons.append("TOO_DARK")
        if m["brightness"] > c.max_brightness:
            reasons.append("TOO_BRIGHT")
        if abs(m["yaw_proxy"]) > max_yaw:
            reasons.append("HEAD_TURNED")
        return Quality(not reasons, reasons, m)

    # ---- frame analysis ----
    def analyze_frames(self, frames: Sequence, purpose: str) -> List[FrameReport]:
        strict = purpose == "enroll"
        reports: List[FrameReport] = []
        for i, raw in enumerate(frames):
            img = raw if isinstance(raw, np.ndarray) else decode_image(raw)
            if img is None:
                reports.append(FrameReport(i, "decode_failed", ["BAD_IMAGE"]))
                continue
            faces = self.detect(img)
            if not faces:
                reports.append(FrameReport(i, "no_face", ["NO_FACE_DETECTED"]))
                continue
            faces.sort(key=lambda f: f.area, reverse=True)
            if len(faces) > 1:
                dominant = (not strict) and faces[0].area >= self.cfg.dominant_ratio * faces[1].area
                if not dominant:
                    reports.append(FrameReport(i, "multiple_faces", ["MULTIPLE_FACES"]))
                    continue
            q = self.assess(img, faces[0], purpose)
            reports.append(FrameReport(i, "ok" if q.ok else "low_quality", q.reasons, q.metrics,
                                       faces[0] if q.ok else None))
        return reports

    @staticmethod
    def fuse(embs: Sequence[np.ndarray], weights: Optional[Sequence[float]] = None) -> np.ndarray:
        e = np.stack([np.asarray(x, np.float32) for x in embs])
        w = np.ones(len(e), np.float32) if weights is None else np.asarray(weights, np.float32)
        return l2norm((e * w[:, None]).sum(axis=0))

    # ---- enrollment ----
    def enroll(self, frames: Sequence) -> EnrollResult:
        c = self.cfg
        try:
            if len(frames) < c.enroll_min_frames:
                return EnrollResult(False, errors=[f"NEED_AT_LEAST_{c.enroll_min_frames}_FRAMES"])
            reports = self.analyze_frames(list(frames)[: c.enroll_max_frames + 3], "enroll")
        except EngineUnavailable:
            return EnrollResult(False, errors=["ENGINE_UNAVAILABLE"])

        public = [r.public() for r in reports]
        bad = [r for r in reports if r.status != "ok"]
        if any(r.status == "multiple_faces" for r in bad):
            return EnrollResult(False, errors=["MULTIPLE_FACES_IN_FRAME"], frames=public)
        good = [r for r in reports if r.status == "ok"][: c.enroll_max_frames]
        if len(good) < c.enroll_min_frames:
            return EnrollResult(False, errors=[f"ONLY_{len(good)}_USABLE_FRAMES_NEED_{c.enroll_min_frames}"],
                                frames=public)

        embs = [r.face.embedding for r in good]
        fused = self.fuse(embs, [r.face.det_score for r in good])
        worst = min(float(e @ fused) for e in embs)
        pairwise = min(float(a @ b) for i, a in enumerate(embs) for b in embs[i + 1:])
        if pairwise < c.enroll_consistency:
            return EnrollResult(False, errors=["FRAMES_ARE_DIFFERENT_PEOPLE"], frames=public)
        log.info("enroll ok: %d frames, worst-to-mean %.2f, min pairwise %.2f", len(good), worst, pairwise)
        return EnrollResult(True, embedding=fused.tolist(),
                            embeddings=[e.tolist() for e in embs], frames=public)

    # ---- recognition (1:N) ----
    def identify(self, frames: Sequence, gallery: Mapping[str, Sequence]) -> MatchResult:
        """
        gallery: {student_id: [embedding, ...]}  (this class only!)
        Only decision == "ACCEPT" may mark attendance.
        REVIEW means "put it in front of faculty", REJECT / ERROR mean no mark.
        """
        c = self.cfg
        n = len(frames)
        try:
            reports = self.analyze_frames(frames, "verify")
        except EngineUnavailable:
            return MatchResult("ERROR", "ENGINE_UNAVAILABLE", frames_total=n)

        public = [r.public() for r in reports]
        good = [r for r in reports if r.status == "ok"]
        if len(good) < c.verify_min_frames:
            top = sorted({x for r in reports for x in r.reasons})
            return MatchResult("REJECT", "INSUFFICIENT_QUALITY_FRAMES:" + ",".join(top or ["NONE"]),
                               frames_total=n, frames_used=len(good), frames=public)

        embs = [r.face.embedding for r in good]
        fused = self.fuse(embs, [r.face.det_score for r in good])
        # identity must not change during the capture window (person swap).
        # Compare frames to EACH OTHER: comparing to the mean misses a 50/50 mix,
        # because the mean of two different people still sits "between" them.
        E = np.stack(embs)
        sims = E @ E.T
        if float(sims[~np.eye(len(E), dtype=bool)].min()) < c.frame_consistency:
            return MatchResult("REJECT", "IDENTITY_CHANGED_DURING_CAPTURE",
                               frames_total=n, frames_used=len(good), frames=public)

        ids, mats = [], []
        for sid, vecs in gallery.items():
            arr = [np.asarray(v, np.float32) for v in vecs if valid_embedding(v)]
            if arr:
                ids.append(sid)
                mats.append(np.stack(arr))
        if not ids:
            return MatchResult("REJECT", "NO_ENROLLED_FACES_FOR_CLASS",
                               frames_total=n, frames_used=len(good), frames=public)

        def per_student(vec) -> np.ndarray:
            return np.array([float((m @ vec).max()) for m in mats])

        scores = per_student(fused)
        order = np.argsort(-scores)
        best = int(order[0])
        top1 = float(scores[best])
        top2 = float(scores[order[1]]) if len(order) > 1 else -1.0
        margin = top1 - top2 if len(order) > 1 else top1
        votes = np.mean([int(np.argmax(per_student(e))) == best for e in embs])

        base = dict(student_id=ids[best], score=top1,
                    runner_up_id=ids[int(order[1])] if len(order) > 1 else None,
                    runner_up_score=max(top2, 0.0), margin=margin, vote_share=float(votes),
                    frames_total=n, frames_used=len(good), frames=public)

        if top1 < c.review_threshold:
            base["student_id"] = None
            return MatchResult("REJECT", "UNKNOWN_FACE", **base)
        if top1 >= c.accept_threshold and margin >= c.margin and votes >= c.min_vote_share:
            return MatchResult("ACCEPT", "MATCHED", **base)
        why = ("LOW_MARGIN_LOOKALIKE" if top1 >= c.accept_threshold and margin < c.margin
               else "LOW_FRAME_AGREEMENT" if top1 >= c.accept_threshold and votes < c.min_vote_share
               else "SCORE_IN_REVIEW_BAND")
        return MatchResult("REVIEW", why, **base)

    def duplicate_of(self, embedding, gallery: Mapping[str, Sequence], threshold: Optional[float] = None):
        """Enrollment guard: (student_id, score) of an existing near-identical face, else None."""
        t = self.cfg.accept_threshold if threshold is None else threshold
        probe = l2norm(embedding)
        best_id, best = None, -1.0
        for sid, vecs in gallery.items():
            for v in vecs:
                if valid_embedding(v):
                    s = float(probe @ np.asarray(v, np.float32))
                    if s > best:
                        best_id, best = sid, s
        return (best_id, best) if best >= t else None


_service: Optional[FaceService] = None
_service_lock = threading.Lock()


def get_service() -> FaceService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = FaceService()
    return _service
