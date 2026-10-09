"""
face_engine_server.py — Server-side face recognition engine for Presently.

Uses InsightFace (ONNX) for:
  - Face detection (SCRFD)
  - Face embedding (ArcFace, 512-dim)
  - Liveness/quality checks (fail-closed)

All computation happens server-side. Clients send only raw JPEG frames.
"""

import sys
import os
import base64
import logging
import threading
import numpy as np
import cv2

# Ensure local site-packages is in path if running with external python binary
_SITE_PACKAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py_env", "Lib", "site-packages")
if os.path.isdir(_SITE_PACKAGES) and _SITE_PACKAGES not in sys.path:
    sys.path.insert(0, _SITE_PACKAGES)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------
_engine_instance = None
_engine_lock = threading.Lock()


def get_face_engine():
    """Get or create the singleton FaceEngine instance."""
    global _engine_instance
    if _engine_instance is None:
        with _engine_lock:
            if _engine_instance is None:
                _engine_instance = FaceEngine()
    return _engine_instance


# ---------------------------------------------------------------------------
# Exceptions — callers catch these to return appropriate HTTP error codes
# ---------------------------------------------------------------------------

class FaceEngineError(Exception):
    """Base exception for face engine errors."""
    pass


class NoFaceDetected(FaceEngineError):
    """No face found in the frame."""
    pass


class MultipleFacesDetected(FaceEngineError):
    """More than one face found (used during enrollment)."""
    pass


class LivenessCheckFailed(FaceEngineError):
    """Frame failed liveness/quality checks."""
    pass


class InvalidFrame(FaceEngineError):
    """Frame is invalid, corrupt, or un-decodable."""
    pass


# ---------------------------------------------------------------------------
# Face Engine
# ---------------------------------------------------------------------------

class FaceEngine:
    """Server-side face detection, embedding, matching, and liveness engine.

    Thread-safe. Models are lazy-loaded on first use.
    """

    # ----- tuneable thresholds -----
    MATCH_THRESHOLD = 0.4           # Cosine similarity for positive ID match
    DETECTION_CONFIDENCE = 0.5      # Minimum face detector score
    MIN_FACE_SIZE = 40              # Pixels — smallest bbox dimension accepted
    BLUR_THRESHOLD = 50.0           # Laplacian variance — below = blurry
    MIN_SATURATION = 15.0           # Mean HSV saturation — below = grayscale print
    MIN_FACE_RATIO = 0.02           # Face area / frame area minimum
    LIVENESS_DET_CONFIDENCE = 0.65  # Stricter det confidence for liveness

    def __init__(self, model_name="buffalo_s"):
        self._model_name = model_name
        self._app = None
        self._loaded = False
        self._load_lock = threading.Lock()
        logger.info("FaceEngine created (model=%s, lazy-load)", model_name)

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _ensure_loaded(self):
        """Lazy-load InsightFace models on first use."""
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            try:
                from insightface.app import FaceAnalysis

                self._app = FaceAnalysis(
                    name=self._model_name,
                    providers=["CPUExecutionProvider"],
                )
                self._app.prepare(ctx_id=-1, det_size=(640, 640))
                self._loaded = True
                logger.info("InsightFace model '%s' loaded", self._model_name)
            except Exception as e:
                logger.error("Failed to load InsightFace: %s", e)
                raise FaceEngineError(f"Model loading failed: {e}") from e

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Frame decoding
    # ------------------------------------------------------------------

    @staticmethod
    def decode_frame(image_data_b64: str) -> np.ndarray:
        """Decode a base64 image (or data-URI) to a BGR numpy array.

        Fails closed: raises InvalidFrame on any problem.
        """
        if not image_data_b64:
            raise InvalidFrame("Empty or null image data")

        try:
            # Strip data URI header if present
            b64data = image_data_b64
            if image_data_b64.startswith("data:"):
                parts = image_data_b64.split(",", 1)
                if len(parts) != 2:
                    raise InvalidFrame("Malformed data URI")
                b64data = parts[1]

            img_bytes = base64.b64decode(b64data)
            nparr = np.frombuffer(img_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None:
                raise InvalidFrame("cv2.imdecode returned None (corrupt or unsupported)")
            if frame.size == 0:
                raise InvalidFrame("Decoded image has zero pixels")

            return frame
        except InvalidFrame:
            raise
        except Exception as e:
            raise InvalidFrame(f"Failed to decode image: {e}") from e

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_faces(self, frame: np.ndarray) -> list:
        """Detect faces in a BGR frame.

        Returns a list of ``insightface.app.common.Face`` objects, each
        carrying *bbox*, *det_score*, *normed_embedding*, etc.
        """
        self._ensure_loaded()

        if frame is None or frame.size == 0:
            raise InvalidFrame("Frame is None or empty")

        try:
            return self._app.get(frame)
        except Exception as e:
            raise FaceEngineError(f"Face detection failed: {e}") from e

    # ------------------------------------------------------------------
    # Embedding (enrollment — exactly one face required)
    # ------------------------------------------------------------------

    def compute_embedding(self, frame: np.ndarray) -> np.ndarray:
        """Detect exactly one face and return its 512-dim embedding.

        Raises NoFaceDetected or MultipleFacesDetected as appropriate.
        """
        faces = self.detect_faces(frame)

        if len(faces) == 0:
            raise NoFaceDetected("No face detected in frame")
        if len(faces) > 1:
            raise MultipleFacesDetected(f"Expected 1 face, found {len(faces)}")

        face = faces[0]
        if face.det_score < self.DETECTION_CONFIDENCE:
            raise NoFaceDetected(
                f"Detection confidence too low: {face.det_score:.2f}"
            )

        emb = face.normed_embedding
        if emb is None:
            raise FaceEngineError("Model returned no embedding")

        return np.array(emb, dtype=np.float32)

    # ------------------------------------------------------------------
    # Embedding (verification — best face picked)
    # ------------------------------------------------------------------

    def compute_embedding_for_verification(self, frame: np.ndarray):
        """Pick the strongest face and return *(embedding, face_obj)*.

        For attendance verification where multiple bystanders may appear.
        """
        faces = self.detect_faces(frame)

        if len(faces) == 0:
            raise NoFaceDetected("No face detected in frame")

        best = max(faces, key=lambda f: f.det_score)

        if best.det_score < self.DETECTION_CONFIDENCE:
            raise NoFaceDetected(
                f"Detection confidence too low: {best.det_score:.2f}"
            )

        bbox = best.bbox
        fw, fh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if fw < self.MIN_FACE_SIZE or fh < self.MIN_FACE_SIZE:
            raise NoFaceDetected(
                f"Face too small: {fw:.0f}×{fh:.0f}px (min {self.MIN_FACE_SIZE})"
            )

        emb = best.normed_embedding
        if emb is None:
            raise FaceEngineError("Model returned no embedding")

        return np.array(emb, dtype=np.float32), best

    # ------------------------------------------------------------------
    # Liveness / quality (FAIL CLOSED)
    # ------------------------------------------------------------------

    def check_liveness(self, frame: np.ndarray, face=None) -> dict:
        """Multi-signal liveness / quality gate.  **Fails closed.**

        Returns ``{ is_real, score, checks, reason }``.
        On ANY internal error → ``is_real = False``.
        """
        try:
            checks = {}

            # 1. Detection confidence
            if face is not None:
                det = float(face.det_score)
                checks["detection_confidence"] = det
                if det < self.LIVENESS_DET_CONFIDENCE:
                    return self._reject(
                        checks, f"Low detection confidence: {det:.2f}"
                    )

            # 2. Blur (Laplacian variance)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            lap = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            checks["blur_score"] = lap
            if lap < self.BLUR_THRESHOLD:
                return self._reject(
                    checks, f"Image too blurry: {lap:.1f} (min {self.BLUR_THRESHOLD})"
                )

            # 3. Face-to-frame ratio
            if face is not None:
                bbox = face.bbox
                face_area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
                frame_area = frame.shape[0] * frame.shape[1]
                ratio = face_area / frame_area if frame_area > 0 else 0
                checks["face_ratio"] = float(ratio)
                if ratio < self.MIN_FACE_RATIO:
                    return self._reject(
                        checks,
                        f"Face too small vs frame: {ratio:.1%} (min {self.MIN_FACE_RATIO:.0%})",
                    )

            # 4. Colour saturation (catch grayscale printouts)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            sat = float(np.mean(hsv[:, :, 1]))
            checks["saturation_mean"] = sat
            if sat < self.MIN_SATURATION:
                return self._reject(
                    checks,
                    f"Abnormally low saturation: {sat:.1f} (min {self.MIN_SATURATION})",
                )

            # Aggregate score
            score = 0.5
            if face is not None:
                score += min(0.2, face.det_score * 0.2)
            score += min(0.15, lap / 500.0 * 0.15)
            score += min(0.15, sat / 80.0 * 0.15)
            score = min(1.0, score)

            return {
                "is_real": True,
                "score": round(score, 3),
                "checks": checks,
                "reason": "All checks passed",
            }

        except Exception as e:
            logger.warning("Liveness error (fail closed): %s", e)
            return {
                "is_real": False,
                "score": 0.0,
                "checks": {},
                "reason": f"Liveness check error: {e}",
            }

    @staticmethod
    def _reject(checks: dict, reason: str, score: float = 0.0) -> dict:
        return {
            "is_real": False,
            "score": score,
            "checks": checks,
            "reason": reason,
        }

    # ------------------------------------------------------------------
    # Gallery matching
    # ------------------------------------------------------------------

    def match_against_gallery(
        self,
        query_embedding: np.ndarray,
        gallery: list,
    ) -> tuple:
        """Find the best match in *gallery* for *query_embedding*.

        Args:
            query_embedding: 512-dim L2-normalised vector.
            gallery: list of ``(student_id, embedding_list_or_array)`` tuples.

        Returns:
            ``(student_id, score)`` if score ≥ MATCH_THRESHOLD, else
            ``(None, best_score)``.
        """
        if query_embedding is None or len(gallery) == 0:
            return None, 0.0

        query = np.array(query_embedding, dtype=np.float32).ravel()
        q_norm = np.linalg.norm(query)
        if q_norm == 0:
            return None, 0.0
        query = query / q_norm

        best_id = None
        best_score = -1.0

        for student_id, enrolled_emb in gallery:
            enrolled = np.array(enrolled_emb, dtype=np.float32).ravel()
            e_norm = np.linalg.norm(enrolled)
            if enrolled.size == 0 or e_norm == 0:
                continue
            score = float(np.dot(query, enrolled / e_norm))
            if score > best_score:
                best_score = score
                best_id = student_id

        if best_score >= self.MATCH_THRESHOLD:
            return best_id, round(best_score, 4)

        return None, round(best_score, 4)

    # ------------------------------------------------------------------
    # Thumbnail (privacy-safe low-res preview)
    # ------------------------------------------------------------------

    @staticmethod
    def generate_thumbnail(frame: np.ndarray, size: int = 48) -> str:
        """Return a small blurred JPEG thumbnail as raw base64 (no data URI)."""
        try:
            thumb = cv2.resize(frame, (size, size), interpolation=cv2.INTER_AREA)
            thumb = cv2.GaussianBlur(thumb, (3, 3), 0)
            _, buf = cv2.imencode(".jpg", thumb, [cv2.IMWRITE_JPEG_QUALITY, 60])
            return base64.b64encode(buf).decode("utf-8")
        except Exception as e:
            logger.warning("Thumbnail generation failed: %s", e)
            return ""
# Presently Biometric Attendance System
