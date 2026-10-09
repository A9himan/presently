"""
tests/test_enrollment.py — Integration tests for biometric enrollment and privacy guarantees (Phase 2).
"""

import sys
import os

# Add project root and local virtualenv site-packages to path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)
SITE_PACKAGES = os.path.join(ROOT_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES) and SITE_PACKAGES not in sys.path:
    sys.path.insert(0, SITE_PACKAGES)

import json
import base64
import unittest
import numpy as np
import cv2

# Set test environment
os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
os.environ['DEMO_MODE'] = '0'

from app import app, db, Student, Class, ClassEnrollment


def make_test_jpeg_uri(width=320, height=240, color=(128, 128, 128)):
    """Create a valid JPEG as base64 data URI."""
    img = np.full((height, width, 3), color, dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", img)
    b64 = base64.b64encode(buf).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


class TestEnrollmentPipeline(unittest.TestCase):
    """Test suite for server-verified biometric enrollment (Phase 2)."""

    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()
        with self.app.app_context():
            db.create_all()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_fake_seeded_embedding_removed(self):
        """Rule 3: Dead fake function generate_seeded_embedding must be eliminated."""
        import app as app_module
        self.assertFalse(
            hasattr(app_module, 'generate_seeded_embedding'),
            "generate_seeded_embedding() still exists in app.py!"
        )

    def test_api_config_endpoint(self):
        """Verify /api/config returns model info and demoMode."""
        res = self.client.get('/api/config')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertIn('demoMode', data)
        self.assertEqual(data.get('embeddingDimension'), 512)

    def test_missing_fields_rejected(self):
        """Enrollment must reject requests with missing name/roll_number/email."""
        res = self.client.post('/api/students/enroll', json={
            'name': 'Test Student'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('required', res.get_json()['error'].lower())

    def test_missing_photo_fails_closed_in_production(self):
        """Rule 2: When DEMO_MODE=0, enrollment must fail closed if photo is omitted."""
        import app as app_module
        app_module.DEMO_MODE = False
        res = self.client.post('/api/students/enroll', json={
            'roll_number': 'TEST-001',
            'name': 'Test Student',
            'email': 'test@campus.edu'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('photo is required', res.get_json()['error'].lower())

    def test_corrupt_photo_rejected(self):
        """Rule 2: Corrupt or malformed image data must be rejected with 400."""
        res = self.client.post('/api/students/enroll', json={
            'roll_number': 'TEST-002',
            'name': 'Corrupt Photo Student',
            'email': 'corrupt@campus.edu',
            'photoData': 'data:image/jpeg;base64,not_a_valid_image_bytes!!!'
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('invalid or corrupt', res.get_json()['error'].lower())

    def test_blank_image_no_face_rejected(self):
        """Enrollment must reject images with no detected face."""
        blank_photo = make_test_jpeg_uri(320, 240, color=(150, 150, 150))
        res = self.client.post('/api/students/enroll', json={
            'roll_number': 'TEST-003',
            'name': 'No Face Student',
            'email': 'noface@campus.edu',
            'photoData': blank_photo
        })
        self.assertEqual(res.status_code, 400)
        self.assertIn('no face detected', res.get_json()['error'].lower())

    def test_demo_mode_gated_fallback(self):
        """Rule 4: In DEMO_MODE=1, enrollment allows synthetic embedding fallback."""
        import app as app_module
        app_module.DEMO_MODE = True
        try:
            res = self.client.post('/api/students/enroll', json={
                'roll_number': 'DEMO-001',
                'name': 'Demo Student',
                'email': 'demo@campus.edu'
            })
            self.assertEqual(res.status_code, 201)
            data = res.get_json()
            self.assertTrue(data['success'])

            # Verify database record
            with self.app.app_context():
                stu = Student.query.filter_by(roll_number='DEMO-001').first()
                self.assertIsNotNone(stu)
                # Verify 512-d vector length
                emb = json.loads(stu.face_embedding)
                self.assertEqual(len(emb), 512)
                # Verify raw photo was NOT stored
                self.assertIsNone(stu.photo_data)
        finally:
            app_module.DEMO_MODE = False

    def test_privacy_guarantee_no_raw_photo_stored(self):
        """Privacy check: Student.photo_data must NEVER store raw user photos."""
        import app as app_module
        app_module.DEMO_MODE = True
        try:
            res = self.client.post('/api/students/enroll', json={
                'roll_number': 'PRIV-001',
                'name': 'Privacy Student',
                'email': 'privacy@campus.edu'
            })
            self.assertEqual(res.status_code, 201)

            with self.app.app_context():
                stu = Student.query.filter_by(roll_number='PRIV-001').first()
                self.assertIsNone(stu.photo_data, "Raw photo_data should be None!")

                # Student list endpoint should return privacy-safe response
                list_res = self.client.get('/api/students')
                self.assertEqual(list_res.status_code, 200)
                students = list_res.get_json()
                self.assertTrue(any(s['roll_number'] == 'PRIV-001' for s in students))
        finally:
            app_module.DEMO_MODE = False


if __name__ == '__main__':
    unittest.main(verbosity=2)
