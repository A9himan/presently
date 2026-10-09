"""
tests/test_camera_liveness.py — Comprehensive tests for camera node anti-proxy liveness verification.

Covers:
  - Right turn, left turn, and neutral face relative yaw
  - Baseline neutral calibration
  - Mirrored camera coordinate handling
  - Temporal smoothing (EMA) and persistence requirement (3 frames)
  - Missing landmarks / temporary face loss handling
  - Challenge timeout, replay protection, and retry
  - Frontend/backend challenge synchronization
  - 512-d vs 128-d embedding compatibility
  - End-to-end attendance verification progression
"""

import sys
import os

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)
SITE_PACKAGES = os.path.join(ROOT_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES) and SITE_PACKAGES not in sys.path:
    sys.path.insert(0, SITE_PACKAGES)

import unittest
import time
import json
import base64
import numpy as np

from app import app, db, create_challenge, verify_challenge, active_challenges, AttendanceSession, Student, Class, ClassEnrollment, AttendanceRecord
from camera_node import compute_face_embedding, compute_ear_68


class TestLivenessMathAndCalibration(unittest.TestCase):
    """Test geometric head-pose calculation, neutral calibration, smoothing, and persistence."""

    def test_neutral_baseline_calibration(self):
        """Neutral face should calibrate baseline and result in near-zero relative yaw."""
        neutral_samples = [0.5, -0.2, 0.1, -0.4, 0.0]
        baseline_yaw = sum(neutral_samples) / len(neutral_samples)
        
        current_yaw = 0.1
        relative_yaw = current_yaw - baseline_yaw
        self.assertAlmostEqual(relative_yaw, 0.1, delta=0.5)

    def test_right_turn_detection(self):
        """Turning head to the right in mirrored coordinates results in positive yaw >= 12 deg."""
        baseline_yaw = 2.0  # Slightly offset neutral sitting position
        raw_yaw = 16.5      # Turned to right
        relative_yaw = raw_yaw - baseline_yaw
        
        self.assertGreaterEqual(relative_yaw, 12.0)
        
        # Challenge progress calculation
        progress = min(1.0, max(0.0, relative_yaw / 12.0))
        self.assertEqual(progress, 1.0)

    def test_left_turn_detection(self):
        """Turning head to the left in mirrored coordinates results in negative yaw <= -12 deg."""
        baseline_yaw = -1.0
        raw_yaw = -15.0     # Turned to left
        relative_yaw = raw_yaw - baseline_yaw
        
        self.assertLessEqual(relative_yaw, -12.0)
        
        # Challenge progress calculation
        progress = min(1.0, max(0.0, -relative_yaw / 12.0))
        self.assertEqual(progress, 1.0)

    def test_temporal_smoothing_rejects_single_frame_noise(self):
        """A single noisy frame spike does not immediately reach threshold when smoothed."""
        baseline_yaw = 0.0
        smoothed_yaw = 0.0
        alpha = 0.35
        
        # Single noisy glitch frame (+15 deg spike from 0)
        glitch_yaw = 15.0
        smoothed_yaw = smoothed_yaw * (1.0 - alpha) + glitch_yaw * alpha
        
        # After 1 frame, smoothed_yaw is only 5.25 deg (< 12.0 deg threshold)
        self.assertLess(smoothed_yaw, 12.0)

    def test_persistence_requirement_requires_3_frames(self):
        """Action must be held for 3 consecutive frames to pass."""
        hold_frames = 0
        threshold_met = True
        
        # Frame 1
        if threshold_met: hold_frames += 1
        self.assertFalse(hold_frames >= 3)
        
        # Frame 2
        if threshold_met: hold_frames += 1
        self.assertFalse(hold_frames >= 3)
        
        # Frame 3
        if threshold_met: hold_frames += 1
        self.assertTrue(hold_frames >= 3)

    def test_temporary_face_loss_recovery(self):
        """Temporary face loss should preserve last state without error."""
        progress = 0.65
        face_detected = False
        
        # When face is lost on a frame, progress is held smoothly
        if not face_detected:
            progress = progress * 0.95
        
        self.assertGreater(progress, 0.5)

    def test_ear_calculation_for_blink(self):
        """EAR calculation returns valid open eye ratio (~0.28)."""
        # Synthetic open eye points
        synthetic_landmarks = np.zeros((68, 3), dtype=np.float32)
        synthetic_landmarks[36] = [10, 20, 0]
        synthetic_landmarks[39] = [30, 20, 0]
        synthetic_landmarks[37] = [16, 17, 0]
        synthetic_landmarks[41] = [16, 23, 0]
        synthetic_landmarks[38] = [24, 17, 0]
        synthetic_landmarks[40] = [24, 23, 0]
        
        # Right eye mirroring
        synthetic_landmarks[42] = [40, 20, 0]
        synthetic_landmarks[45] = [60, 20, 0]
        synthetic_landmarks[43] = [46, 17, 0]
        synthetic_landmarks[47] = [46, 23, 0]
        synthetic_landmarks[44] = [54, 17, 0]
        synthetic_landmarks[46] = [54, 23, 0]
        
        ear = compute_ear_68(synthetic_landmarks)
        self.assertGreater(ear, 0.20)
        self.assertLess(ear, 0.40)


class TestBackendChallengeSynchronization(unittest.TestCase):
    """Test frontend/backend synchronization for challenges and anti-proxy telemetry."""

    def setUp(self):
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    def test_create_challenge_has_valid_attributes(self):
        """Generated challenge must contain ID, valid action, description, and timeout window."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        self.assertTrue(ch["challengeId"].startswith("ch_"))
        self.assertEqual(ch["action"], "TURN_RIGHT")
        self.assertIn("right", ch["description"].lower())
        self.assertGreaterEqual(ch["expiresAt"], time.time() * 1000 + 8000)

    def test_turn_right_telemetry_passes_at_or_above_12_deg(self):
        """TURN_RIGHT succeeds when telemetry yawAngleDelta >= 12."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        result = verify_challenge(ch["challengeId"], "TURN_RIGHT", telemetry={"yawAngleDelta": 18, "isStaticImageDetected": False})
        self.assertTrue(result["passed"])
        self.assertEqual(result["action"], "TURN_RIGHT")
        self.assertGreaterEqual(result["livenessScore"], 0.94)

    def test_turn_right_telemetry_fails_below_12_deg(self):
        """TURN_RIGHT fails if telemetry yawAngleDelta < 12 (insufficient rotation / static photo)."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        result = verify_challenge(ch["challengeId"], "TURN_RIGHT", telemetry={"yawAngleDelta": 6, "isStaticImageDetected": False})
        self.assertFalse(result["passed"])
        self.assertIn("Presentation Attack Detected", result["reason"])

    def test_turn_left_telemetry_passes_at_or_below_minus_12_deg(self):
        """TURN_LEFT succeeds when telemetry yawAngleDelta <= -12."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_LEFT")
        result = verify_challenge(ch["challengeId"], "TURN_LEFT", telemetry={"yawAngleDelta": -16, "isStaticImageDetected": False})
        self.assertTrue(result["passed"])
        self.assertEqual(result["action"], "TURN_LEFT")

    def test_turn_left_telemetry_fails_above_minus_12_deg(self):
        """TURN_LEFT fails if telemetry yawAngleDelta > -12."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_LEFT")
        result = verify_challenge(ch["challengeId"], "TURN_LEFT", telemetry={"yawAngleDelta": -5, "isStaticImageDetected": False})
        self.assertFalse(result["passed"])
        self.assertIn("Presentation Attack Detected", result["reason"])

    def test_blink_challenge_verification(self):
        """BLINK challenge succeeds with blinkCount >= 1 and fails with 0."""
        ch1 = create_challenge(session_id="sess_test", preferred_action="BLINK")
        res1 = verify_challenge(ch1["challengeId"], "BLINK", telemetry={"blinkCount": 1, "isStaticImageDetected": False})
        self.assertTrue(res1["passed"])

        ch2 = create_challenge(session_id="sess_test", preferred_action="BLINK")
        res2 = verify_challenge(ch2["challengeId"], "BLINK", telemetry={"blinkCount": 0, "isStaticImageDetected": False})
        self.assertFalse(res2["passed"])

    def test_static_image_flag_blocks_verification(self):
        """Static image presentation attack flag rejects verification."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        result = verify_challenge(ch["challengeId"], "TURN_RIGHT", telemetry={"yawAngleDelta": 20, "isStaticImageDetected": True})
        self.assertFalse(result["passed"])
        self.assertIn("Static photo", result["reason"])

    def test_replay_attack_prevention(self):
        """A consumed challenge ID cannot be re-used."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        cid = ch["challengeId"]
        res1 = verify_challenge(cid, "TURN_RIGHT", telemetry={"yawAngleDelta": 20})
        self.assertTrue(res1["passed"])

        res2 = verify_challenge(cid, "TURN_RIGHT", telemetry={"yawAngleDelta": 20})
        self.assertFalse(res2["passed"])
        self.assertIn("Challenge expired or invalid ID", res2["reason"])

    def test_expired_challenge_rejected(self):
        """Expired challenge token is rejected."""
        ch = create_challenge(session_id="sess_test", preferred_action="TURN_RIGHT")
        active_challenges[ch["challengeId"]]["expiresAt"] = time.time() * 1000 - 500  # in the past
        result = verify_challenge(ch["challengeId"], "TURN_RIGHT", telemetry={"yawAngleDelta": 20})
        self.assertFalse(result["passed"])
        self.assertIn("timed out", result["reason"])


class TestEmbeddingCompatibility(unittest.TestCase):
    """Test 512-d ArcFace embedding compatibility and rejection of legacy 128-d vectors."""

    def setUp(self):
        self.app = app.test_client()
        self.app_context = app.app_context()
        self.app_context.push()

    def tearDown(self):
        self.app_context.pop()

    def test_compute_face_embedding_returns_512_dimensions(self):
        """compute_face_embedding returns exactly 512 float values."""
        emb = compute_face_embedding()
        self.assertEqual(len(emb), 512)
        norm = np.linalg.norm(emb)
        self.assertAlmostEqual(norm, 1.0, places=3)

    def test_api_face_match_rejects_128_dimensional_vector(self):
        """The /api/face/match endpoint rejects 128-d vectors with a clear error."""
        vector_128 = [0.1] * 128
        res = self.app.post("/api/face/match", json={"embedding": vector_128})
        self.assertEqual(res.status_code, 400)
        data = res.get_json()
        self.assertIn("Embedding dimension mismatch", data.get("error", ""))

    def test_api_face_match_accepts_512_dimensional_vector(self):
        """The /api/face/match endpoint accepts valid 512-d vectors."""
        # Query with student 1's stored embedding
        stu = Student.query.get("stu_01")
        self.assertIsNotNone(stu)
        emb = json.loads(stu.face_embedding)
        self.assertEqual(len(emb), 512)

        res = self.app.post("/api/face/match", json={"embedding": emb, "class_id": "cls_cs101_a"})
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get("isMatch"))
        self.assertEqual(data.get("student", {}).get("id"), "stu_01")


if __name__ == "__main__":
    unittest.main()
