"""
tests/test_face_engine.py — Unit + integration tests for face_engine_server.py

Unit tests (no model needed): decode_frame, check_liveness, match_against_gallery, generate_thumbnail
Integration tests (need insightface): detect_faces, compute_embedding, full pipeline
"""

import sys
import os

# Add project root and local virtualenv site-packages to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
SITE_PACKAGES = os.path.join(ROOT_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES) and SITE_PACKAGES not in sys.path:
    sys.path.insert(0, SITE_PACKAGES)

import base64
import unittest
import numpy as np
import cv2

from face_engine_server import (
    FaceEngine,
    FaceEngineError,
    NoFaceDetected,
    MultipleFacesDetected,
    InvalidFrame,
    LivenessCheckFailed,
)

# Check if insightface is available for integration tests
try:
    import insightface
    HAS_INSIGHTFACE = True
except ImportError:
    HAS_INSIGHTFACE = False


def make_test_jpeg(width=320, height=240, color=(128, 128, 128)):
    """Create a valid JPEG as base64 data URI."""
    img = np.full((height, width, 3), color, dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}", img


def make_synthetic_face_frame(width=320, height=240):
    """Create a frame with a synthetic 'face-like' oval for liveness checks.

    NOT a real face — won't pass InsightFace detection, but provides
    reasonable values for blur/saturation/size checks.
    """
    img = np.full((height, width, 3), (200, 180, 160), dtype=np.uint8)
    # Draw skin-colored oval in center
    center = (width // 2, height // 2)
    axes = (50, 70)
    cv2.ellipse(img, center, axes, 0, 0, 360, (180, 150, 130), -1)
    # Add some texture (noise) so blur check passes
    noise = np.random.randint(0, 20, img.shape, dtype=np.uint8)
    img = cv2.add(img, noise)
    return img


class FakeFace:
    """Mock insightface Face object for unit tests."""

    def __init__(self, det_score=0.95, bbox=None, normed_embedding=None):
        self.det_score = det_score
        self.bbox = bbox if bbox is not None else np.array([50, 30, 200, 200])
        self.normed_embedding = normed_embedding


# ======================================================================
# Unit Tests — no insightface model required
# ======================================================================


class TestDecodeFrame(unittest.TestCase):
    """Test FaceEngine.decode_frame() — static method, no model needed."""

    def setUp(self):
        self.engine = FaceEngine.__new__(FaceEngine)

    def test_valid_jpeg_data_uri(self):
        data_uri, original = make_test_jpeg()
        frame = FaceEngine.decode_frame(data_uri)
        self.assertEqual(frame.shape[2], 3)  # BGR
        self.assertGreater(frame.size, 0)

    def test_valid_jpeg_raw_base64(self):
        data_uri, _ = make_test_jpeg()
        raw_b64 = data_uri.split(",")[1]
        frame = FaceEngine.decode_frame(raw_b64)
        self.assertIsNotNone(frame)
        self.assertGreater(frame.size, 0)

    def test_empty_string_raises(self):
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame("")

    def test_none_raises(self):
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame(None)

    def test_invalid_base64_raises(self):
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame("not-valid-base64!!!")

    def test_corrupt_image_raises(self):
        corrupt_b64 = base64.b64encode(b"this is not an image").decode()
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame(corrupt_b64)

    def test_malformed_data_uri_raises(self):
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame("data:image/jpeg;base64")  # no comma

    def test_svg_rejected(self):
        """SVG data URIs should fail (not silently pass like the old code)."""
        svg_uri = "data:image/svg+xml;base64," + base64.b64encode(
            b"<svg></svg>"
        ).decode()
        with self.assertRaises(InvalidFrame):
            FaceEngine.decode_frame(svg_uri)


class TestCheckLiveness(unittest.TestCase):
    """Test FaceEngine.check_liveness() — no model needed, uses cv2 only."""

    def setUp(self):
        self.engine = FaceEngine.__new__(FaceEngine)
        # Copy thresholds from class
        self.engine.BLUR_THRESHOLD = FaceEngine.BLUR_THRESHOLD
        self.engine.MIN_FACE_RATIO = FaceEngine.MIN_FACE_RATIO
        self.engine.MIN_SATURATION = FaceEngine.MIN_SATURATION
        self.engine.LIVENESS_DET_CONFIDENCE = FaceEngine.LIVENESS_DET_CONFIDENCE

    def test_good_frame_passes(self):
        frame = make_synthetic_face_frame()
        face = FakeFace(det_score=0.95, bbox=np.array([60, 20, 260, 220]))
        result = self.engine.check_liveness(frame, face)
        self.assertTrue(result["is_real"], f"Rejected: {result['reason']}")
        self.assertGreater(result["score"], 0.5)

    def test_low_detection_confidence_rejects(self):
        frame = make_synthetic_face_frame()
        face = FakeFace(det_score=0.3)
        result = self.engine.check_liveness(frame, face)
        self.assertFalse(result["is_real"])
        self.assertIn("confidence", result["reason"].lower())

    def test_blurry_frame_rejects(self):
        # Create a very blurry (uniform) image
        frame = np.full((240, 320, 3), 128, dtype=np.uint8)
        result = self.engine.check_liveness(frame, face=None)
        self.assertFalse(result["is_real"])
        self.assertIn("blurry", result["reason"].lower())

    def test_tiny_face_rejects(self):
        frame = make_synthetic_face_frame(640, 480)
        # Face is only 10x10 in a 640x480 frame
        face = FakeFace(det_score=0.95, bbox=np.array([100, 100, 110, 110]))
        result = self.engine.check_liveness(frame, face)
        self.assertFalse(result["is_real"])
        self.assertIn("small", result["reason"].lower())

    def test_grayscale_printout_rejects(self):
        # Grayscale image = very low saturation
        gray = np.random.randint(50, 200, (240, 320), dtype=np.uint8)
        frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        face = FakeFace(det_score=0.95, bbox=np.array([50, 30, 270, 210]))
        result = self.engine.check_liveness(frame, face)
        self.assertFalse(result["is_real"])
        self.assertIn("saturation", result["reason"].lower())

    def test_error_fails_closed(self):
        """Any exception inside check_liveness should return is_real=False."""
        result = self.engine.check_liveness(None, face=None)
        self.assertFalse(result["is_real"])
        self.assertIn("error", result["reason"].lower())

    def test_no_face_object_still_checks_image(self):
        """When face=None, skip face-specific checks but still check image."""
        frame = make_synthetic_face_frame()
        result = self.engine.check_liveness(frame, face=None)
        self.assertTrue(result["is_real"])
        self.assertIn("blur_score", result["checks"])


class TestMatchAgainstGallery(unittest.TestCase):
    """Test FaceEngine.match_against_gallery() — pure numpy, no model."""

    def setUp(self):
        self.engine = FaceEngine.__new__(FaceEngine)
        self.engine.MATCH_THRESHOLD = FaceEngine.MATCH_THRESHOLD

    def _random_embedding(self, dim=512):
        e = np.random.randn(dim).astype(np.float32)
        return e / (np.linalg.norm(e) + 1e-10)

    def test_exact_match(self):
        emb = self._random_embedding()
        gallery = [("stu_1", emb.tolist())]
        sid, score = self.engine.match_against_gallery(emb, gallery)
        self.assertEqual(sid, "stu_1")
        self.assertAlmostEqual(score, 1.0, places=3)

    def test_no_match_below_threshold(self):
        query = self._random_embedding()
        gallery = [("stu_1", self._random_embedding().tolist())]
        sid, score = self.engine.match_against_gallery(query, gallery)
        # Random 512-dim vectors have near-zero cosine similarity
        self.assertIsNone(sid)

    def test_best_of_multiple(self):
        target = self._random_embedding()
        # Create a slightly perturbed version (high similarity)
        similar = target + np.random.randn(512).astype(np.float32) * 0.005
        similar = similar / np.linalg.norm(similar)
        gallery = [
            ("stu_1", self._random_embedding().tolist()),
            ("stu_2", similar.tolist()),
            ("stu_3", self._random_embedding().tolist()),
        ]
        sid, score = self.engine.match_against_gallery(target, gallery)
        self.assertEqual(sid, "stu_2")
        self.assertGreater(score, 0.9)

    def test_empty_gallery(self):
        sid, score = self.engine.match_against_gallery(
            self._random_embedding(), []
        )
        self.assertIsNone(sid)
        self.assertEqual(score, 0.0)

    def test_none_query(self):
        sid, score = self.engine.match_against_gallery(
            None, [("s1", self._random_embedding().tolist())]
        )
        self.assertIsNone(sid)

    def test_zero_embedding_query(self):
        zero = np.zeros(512, dtype=np.float32)
        gallery = [("s1", self._random_embedding().tolist())]
        sid, score = self.engine.match_against_gallery(zero, gallery)
        self.assertIsNone(sid)

    def test_zero_embedding_in_gallery_skipped(self):
        query = self._random_embedding()
        gallery = [
            ("s1", np.zeros(512).tolist()),  # should be skipped
            ("s2", query.tolist()),           # exact match
        ]
        sid, score = self.engine.match_against_gallery(query, gallery)
        self.assertEqual(sid, "s2")


class TestGenerateThumbnail(unittest.TestCase):
    """Test FaceEngine.generate_thumbnail() — static method."""

    def test_valid_frame(self):
        frame = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
        b64 = FaceEngine.generate_thumbnail(frame)
        self.assertIsInstance(b64, str)
        self.assertGreater(len(b64), 0)
        # Verify it's valid base64 that decodes to a JPEG
        decoded = base64.b64decode(b64)
        self.assertTrue(decoded[:2] == b"\xff\xd8")  # JPEG magic bytes

    def test_custom_size(self):
        frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)
        b64 = FaceEngine.generate_thumbnail(frame, size=96)
        self.assertGreater(len(b64), 0)

    def test_invalid_frame_returns_empty(self):
        b64 = FaceEngine.generate_thumbnail(None)
        self.assertEqual(b64, "")


# ======================================================================
# Integration Tests — require insightface + model download
# ======================================================================


@unittest.skipUnless(HAS_INSIGHTFACE, "insightface not installed")
class TestFaceEngineIntegration(unittest.TestCase):
    """Full pipeline tests with real InsightFace model."""

    @classmethod
    def setUpClass(cls):
        """Load engine once for all integration tests."""
        cls.engine = FaceEngine(model_name="buffalo_s")
        # Force model load
        cls.engine._ensure_loaded()

    def test_model_loads(self):
        self.assertTrue(self.engine.is_loaded)

    def test_no_face_in_blank_image(self):
        blank = np.full((480, 640, 3), 200, dtype=np.uint8)
        with self.assertRaises(NoFaceDetected):
            self.engine.compute_embedding(blank)

    def test_detect_faces_returns_list(self):
        blank = np.full((480, 640, 3), 200, dtype=np.uint8)
        faces = self.engine.detect_faces(blank)
        self.assertIsInstance(faces, list)
        # Blank image should have 0 faces
        self.assertEqual(len(faces), 0)

    def test_embedding_dimensionality(self):
        """If we had a real face image, embedding should be 512-dim.
        This test is a placeholder — real face images can be added later."""
        pass  # Requires a real face photo asset

    def test_full_pipeline_no_face_rejects(self):
        """Full pipeline: decode → detect → reject (no face)."""
        data_uri, _ = make_test_jpeg(320, 240, color=(200, 200, 200))
        frame = FaceEngine.decode_frame(data_uri)
        with self.assertRaises(NoFaceDetected):
            self.engine.compute_embedding(frame)


if __name__ == "__main__":
    unittest.main(verbosity=2)
# Presently Biometric Attendance System
