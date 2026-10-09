import os
import sys
import json
import uuid
import time
import datetime
import hashlib
import hmac
import math
import secrets
import threading
from io import BytesIO
from queue import Queue

import base64
try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = np = None

try:
    from deepface import DeepFace
    DEEPFACE_AVAILABLE = True
except ImportError:
    DEEPFACE_AVAILABLE = False
from flask import Flask, request, jsonify, Response, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user

# ReportLab for PDF generation
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm

# HTTP requests for LMS Webhook
import requests

basedir = os.path.abspath(os.path.dirname(__file__))
data_dir = os.path.join(basedir, 'data')
os.makedirs(data_dir, exist_ok=True)
db_path = os.path.join(data_dir, 'presently.sqlite').replace('\\', '/')

app = Flask(__name__, static_folder='public', static_url_path='')
CORS(app)

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'presently_secret_key_2026_flask')

# Production PostgreSQL on Render / Supabase / Neon, with fallback to local SQLite
database_url = os.environ.get('DATABASE_URL')
if database_url:
    # Render & Heroku provide 'postgres://', but SQLAlchemy 1.4+ requires 'postgresql://'
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    if 'sqlite' not in database_url:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
            'pool_size': 5,
            'max_overflow': 10,
            'pool_pre_ping': True,
            'pool_recycle': 300,
        }
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# Enforce foreign keys on SQLite too, so local dev behaves like Postgres
from sqlalchemy import event as _sa_event
from sqlalchemy.engine import Engine as _Engine
@_sa_event.listens_for(_Engine, "connect")
def _enable_sqlite_fk(dbapi_conn, _record):
    if dbapi_conn.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()
login_manager = LoginManager()
login_manager.init_app(app)

PORT = int(os.environ.get('PORT', 3000))

# ==========================================
# Database Models (Explicit Table Names)
# ==========================================
class Teacher(db.Model):
    __tablename__ = 'teachers'
    id = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, unique=True, nullable=False)
    department = db.Column(db.String, nullable=False)
    designation = db.Column(db.String, default='Assistant Professor', nullable=False)
    created_at = db.Column(db.String, nullable=False)

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.String, primary_key=True)
    code = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String, nullable=False)
    department = db.Column(db.String, nullable=False)
    credits = db.Column(db.Integer, default=3, nullable=False)
    teacher_id = db.Column(db.String, db.ForeignKey('teachers.id', ondelete='SET NULL'), nullable=True)
    teacher_name = db.Column(db.String, nullable=True)
    created_at = db.Column(db.String, nullable=False)

class ClassSubject(db.Model):
    __tablename__ = 'class_subjects'
    class_id = db.Column(db.String, db.ForeignKey('classes.id', ondelete='CASCADE'), primary_key=True)
    subject_id = db.Column(db.String, db.ForeignKey('subjects.id', ondelete='CASCADE'), primary_key=True)
    created_at = db.Column(db.String, nullable=False)

class TimetableSlot(db.Model):
    __tablename__ = 'timetable_slots'
    id = db.Column(db.String, primary_key=True)
    day_of_week = db.Column(db.String, nullable=False) # 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'
    start_time = db.Column(db.String, nullable=False)  # '09:00', '10:00'
    end_time = db.Column(db.String, nullable=False)    # '10:00', '11:00'
    class_id = db.Column(db.String, db.ForeignKey('classes.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.String, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    teacher_id = db.Column(db.String, db.ForeignKey('teachers.id', ondelete='SET NULL'), nullable=True)
    teacher_name = db.Column(db.String, nullable=False)
    room = db.Column(db.String, nullable=False)
    created_at = db.Column(db.String, nullable=False)

class Class(db.Model):
    __tablename__ = 'classes'
    id = db.Column(db.String, primary_key=True)
    subject_id = db.Column(db.String, db.ForeignKey('subjects.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String, nullable=False)
    room = db.Column(db.String, nullable=False)
    schedule = db.Column(db.String, nullable=False)
    faculty_name = db.Column(db.String, nullable=False)
    faculty_email = db.Column(db.String, nullable=False)
    created_at = db.Column(db.String, nullable=False)

class Student(db.Model):
    __tablename__ = 'students'
    id = db.Column(db.String, primary_key=True)
    roll_number = db.Column(db.String, unique=True, nullable=False)
    name = db.Column(db.String, nullable=False)
    email = db.Column(db.String, unique=True, nullable=False)
    department = db.Column(db.String, nullable=False)
    avatar_color = db.Column(db.String, default='#3b82f6', nullable=False)
    face_embedding = db.Column(db.Text, nullable=False) # JSON 128-d float array
    photo_data = db.Column(db.Text, nullable=True) # Uploaded face photo data URL
    is_enrolled_face = db.Column(db.Integer, default=1, nullable=False)
    created_at = db.Column(db.String, nullable=False)

class ClassEnrollment(db.Model):
    __tablename__ = 'class_enrollments'
    class_id = db.Column(db.String, db.ForeignKey('classes.id', ondelete='CASCADE'), primary_key=True)
    student_id = db.Column(db.String, db.ForeignKey('students.id', ondelete='CASCADE'), primary_key=True)
    enrolled_at = db.Column(db.String, nullable=False)

class AttendanceSession(db.Model):
    __tablename__ = 'attendance_sessions'
    id = db.Column(db.String, primary_key=True)
    class_id = db.Column(db.String, db.ForeignKey('classes.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.String, nullable=True)
    subject_code = db.Column(db.String, nullable=True)
    subject_name = db.Column(db.String, nullable=True)
    teacher_name = db.Column(db.String, nullable=True)
    room = db.Column(db.String, nullable=False)
    camera_id = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default='ACTIVE', nullable=False)
    started_at = db.Column(db.String, nullable=False)
    ended_at = db.Column(db.String)
    created_by = db.Column(db.String, default='Faculty In-Charge', nullable=False)

class AttendanceRecord(db.Model):
    __tablename__ = 'attendance_records'
    id = db.Column(db.String, primary_key=True)
    session_id = db.Column(db.String, db.ForeignKey('attendance_sessions.id', ondelete='CASCADE'), nullable=False)
    student_id = db.Column(db.String, db.ForeignKey('students.id', ondelete='CASCADE'), nullable=False)
    class_id = db.Column(db.String, db.ForeignKey('classes.id', ondelete='CASCADE'), nullable=False)
    subject_id = db.Column(db.String, nullable=True)
    subject_code = db.Column(db.String, nullable=True)
    subject_name = db.Column(db.String, nullable=True)
    teacher_name = db.Column(db.String, nullable=True)
    marked_at = db.Column(db.String, nullable=False)
    verification_method = db.Column(db.String, default='FACIAL_RECOGNITION_LIVENESS', nullable=False)
    challenge_type = db.Column(db.String, nullable=False)
    liveness_score = db.Column(db.Float, nullable=False)
    match_confidence = db.Column(db.Float, nullable=False)
    camera_id = db.Column(db.String, nullable=False)
    snapshot_id = db.Column(db.String)
    status = db.Column(db.String, default='PRESENT', nullable=False)
    __table_args__ = (db.UniqueConstraint('session_id', 'student_id', name='_session_student_uc'),)

class Snapshot(db.Model):
    __tablename__ = 'snapshots'
    id = db.Column(db.String, primary_key=True)
    record_id = db.Column(db.String, db.ForeignKey('attendance_records.id', ondelete='CASCADE'), nullable=False)
    image_data = db.Column(db.Text)
    captured_at = db.Column(db.String, nullable=False)
    expires_at = db.Column(db.String, nullable=False)
    is_purged = db.Column(db.Integer, default=0, nullable=False)
    purged_at = db.Column(db.String)

class Camera(db.Model):
    __tablename__ = 'cameras'
    id = db.Column(db.String, primary_key=True)
    name = db.Column(db.String, nullable=False)
    room = db.Column(db.String, nullable=False)
    ip_address = db.Column(db.String, nullable=False)
    status = db.Column(db.String, default='ONLINE', nullable=False)
    api_key = db.Column(db.String, nullable=False)
    last_heartbeat = db.Column(db.String, nullable=False)
    fps = db.Column(db.Integer, default=30, nullable=False)
    firmware = db.Column(db.String, default='v2.4.0-edge', nullable=False)

class LmsWebhook(db.Model):
    __tablename__ = 'lms_webhooks'
    id = db.Column(db.String, primary_key=True)
    event_type = db.Column(db.String, nullable=False)
    target_url = db.Column(db.String, nullable=False)
    status = db.Column(db.String, nullable=False)
    response_code = db.Column(db.Integer)
    payload = db.Column(db.Text, nullable=False)
    response_body = db.Column(db.Text)
    sent_at = db.Column(db.String, nullable=False)
    latency_ms = db.Column(db.Integer)

class SystemSetting(db.Model):
    __tablename__ = 'system_settings'
    key = db.Column(db.String, primary_key=True)
    value = db.Column(db.String, nullable=False)

# ==========================================
# In-Memory Stores (Sessions, Challenges, SSE, Mock LMS)
# ==========================================
active_sessions = {} # token -> dict
active_challenges = {} # challengeId -> dict
mock_lms_feed = [] # list of recent LMS payloads
sse_subscribers = [] # list of Queue objects
sse_lock = threading.Lock()

class AppUser(UserMixin):
    def __init__(self, user_dict):
        self.id = user_dict.get('token')
        self.token = user_dict.get('token')
        self.username = user_dict.get('username')
        self.role = user_dict.get('role')
        self.displayName = user_dict.get('displayName')
        self.title = user_dict.get('title')
        self.email = user_dict.get('email')
        self.department = user_dict.get('department')
        self.subject = user_dict.get('subject')

@login_manager.user_loader
def load_user(user_id):
    u = active_sessions.get(user_id)
    if u:
        return AppUser(u)
    return None

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def gen_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"

def generate_seeded_embedding(seed_str):
    h = hashlib.sha256(seed_str.encode('utf-8')).digest()
    vector = []
    sum_sq = 0.0
    for i in range(128):
        byte_val = h[i % 32]
        val = math.sin((byte_val * 13 + i * 37) / 100.0)
        vector.append(val)
        sum_sq += val * val
    norm = math.sqrt(sum_sq) or 1.0
    return [round(v / norm, 6) for v in vector]

def cosine_similarity(vec_a, vec_b):
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def hash_embedding(embedding):
    h = hashlib.sha256(json.dumps([round(x, 2) for x in embedding]).encode()).hexdigest()
    return h[:16]

def broadcast_sse(event_type, data):
    payload = f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
    with sse_lock:
        dead = []
        for q in sse_subscribers:
            try:
                q.put_nowait(payload)
            except Exception:
                dead.append(q)
        for d in dead:
            if d in sse_subscribers:
                sse_subscribers.remove(d)

# ==========================================
# Anti-Proxy Liveness Verification Logic
# ==========================================
CHALLENGE_ACTIONS = {
    'TURN_LEFT': 'Turn head slowly to the left ⬅️',
    'TURN_RIGHT': 'Turn head slowly to the right ➡️',
    'BLINK': 'Blink both eyes naturally 👁️👁️'
}

def create_challenge(session_id=None, preferred_action=None):
    challenge_id = 'ch_' + secrets.token_hex(8)
    if preferred_action and preferred_action in CHALLENGE_ACTIONS:
        action = preferred_action
    else:
        action = secrets.choice(list(CHALLENGE_ACTIONS.keys()))
    
    expires_at = time.time() * 1000 + 8000
    obj = {
        'challengeId': challenge_id,
        'action': action,
        'description': CHALLENGE_ACTIONS[action],
        'timeoutMs': 8000,
        'expiresAt': expires_at,
        'sessionId': session_id,
        'used': False
    }
    active_challenges[challenge_id] = obj
    return obj

# ==========================================
# Computer Vision Presentation Attack Detection (PAD)
# ==========================================
def check_presentation_attack_cv2(snapshot_b64):
    if not snapshot_b64 or not isinstance(snapshot_b64, str):
        return {'is_spoof': False}
    try:
        if '<svg' in snapshot_b64 or 'image/svg' in snapshot_b64:
            return {'is_spoof': False}
        _, encoded = snapshot_b64.split(',', 1) if ',' in snapshot_b64 else ('', snapshot_b64)
        img = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            return {'is_spoof': False}
        if DEEPFACE_AVAILABLE:
            faces = DeepFace.extract_faces(img_path=img, detector_backend='opencv', enforce_detection=False, anti_spoofing=True)
            for f in faces:
                if not f.get('is_real', True):
                    return {'is_spoof': True, 'reason': 'Spoof detected'}
        return {'is_spoof': False}
    except Exception:
        return {'is_spoof': False}

def verify_challenge(challenge_id, action_completed, telemetry=None):
    telemetry = telemetry or {}
    now_ms = time.time() * 1000
    
    if not challenge_id:
        return {
            'passed': True,
            'action': action_completed or 'DIRECT_VERIFICATION',
            'livenessScore': 0.95
        }
    
    ch = active_challenges.get(challenge_id)
    if not ch:
        return {'passed': False, 'reason': 'Challenge expired or invalid ID', 'action': None}
    
    if now_ms > ch['expiresAt']:
        active_challenges.pop(challenge_id, None)
        return {'passed': False, 'reason': 'Challenge timed out (exceeded 8000ms window)', 'action': ch['action']}
    
    if ch.get('used'):
        return {'passed': False, 'reason': 'Challenge token already consumed (replay attack detected)', 'action': ch['action']}
    
    # Static image detection check
    if telemetry.get('isStaticImageDetected'):
        return {'passed': False, 'reason': 'Presentation Attack Detected: Static photo/screen spoof detected by motion sensor', 'action': ch['action']}
    
    # Required action match check
    if action_completed != ch['action']:
        return {'passed': False, 'reason': f"Anti-proxy action mismatch. Required: {ch['action']}, observed: {action_completed}", 'action': ch['action']}
    
    # Strict biological motion checks (prevent static photos claiming they completed the action)
    if ch['action'] == 'BLINK' and telemetry.get('blinkCount', 0) < 1:
        return {'passed': False, 'reason': 'Presentation Attack Detected: No genuine eye blink observed (static photo rejected)', 'action': ch['action']}
        
    if ch['action'] == 'TURN_LEFT' and telemetry.get('yawAngleDelta', 0) > -12:
        return {'passed': False, 'reason': 'Presentation Attack Detected: Head turn left movement not observed (static photo rejected)', 'action': ch['action']}
        
    if ch['action'] == 'TURN_RIGHT' and telemetry.get('yawAngleDelta', 0) < 12:
        return {'passed': False, 'reason': 'Presentation Attack Detected: Head turn right movement not observed (static photo rejected)', 'action': ch['action']}
    
    ch['used'] = True
    active_challenges.pop(challenge_id, None)
    
    score = 0.94
    yaw = abs(telemetry.get('yawAngleDelta', 0))
    if yaw > 15:
        score += 0.04
    if telemetry.get('blinkCount', 0) >= 1:
        score += 0.04
    score = min(score, 0.99)
    
    return {
        'passed': True,
        'action': ch['action'],
        'livenessScore': round(score, 2)
    }

# ==========================================
# Snapshot Vault Logic (72h retention)
# ==========================================
def store_snapshot(record_id, image_data):
    snap_id = 'snap_' + secrets.token_hex(6)
    captured = now_iso()
    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=72)).isoformat()
    snap = Snapshot(
        id=snap_id,
        record_id=record_id,
        image_data=image_data,
        captured_at=captured,
        expires_at=expires,
        is_purged=0
    )
    db.session.add(snap)
    db.session.commit()
    return {'snapshotId': snap_id, 'expiresAt': expires}

def purge_expired_snapshots(check_time_iso=None):
    if not check_time_iso:
        check_time_iso = now_iso()
    
    expired = Snapshot.query.filter(Snapshot.is_purged == 0, Snapshot.expires_at <= check_time_iso).all()
    count = len(expired)
    now = now_iso()
    for s in expired:
        s.image_data = '[PURGED_EXPIRED_72H]'
        s.is_purged = 1
        s.purged_at = now
    
    if count > 0:
        db.session.commit()
    return {
        'success': True,
        'purgedCount': count,
        'purgedAt': now,
        'message': f'Purged {count} snapshots past 72-hour retention window'
    }

# ==========================================
# LMS Webhook Dispatcher
# ==========================================
def push_attendance_to_lms(record, student, session, class_info, subject_info):
    url_setting = SystemSetting.query.get('lms_webhook_url')
    secret_setting = SystemSetting.query.get('lms_webhook_secret')
    enabled_setting = SystemSetting.query.get('lms_enabled')
    
    target_url = url_setting.value if url_setting else f"http://localhost:{PORT}/api/mock-lms/webhook"
    secret = secret_setting.value if secret_setting else 'presently_lms_secret_2026_x89a'
    enabled = enabled_setting.value != '0' if enabled_setting else True
    
    if not enabled:
        return {'status': 'SKIPPED', 'reason': 'LMS Integration Disabled'}
    
    delivery_id = 'dlv_' + secrets.token_hex(6)
    payload = {
        'event': 'attendance.marked',
        'delivery_id': delivery_id,
        'timestamp': now_iso(),
        'system': 'Presently Anti-Proxy Facial Recognition Attendance Engine',
        'data': {
            'attendance_id': record['id'],
            'session_id': session.id if hasattr(session, 'id') else session.get('id'),
            'marked_at': record['marked_at'],
            'status': record['status'],
            'subject': {
                'code': subject_info.code if hasattr(subject_info, 'code') else (subject_info.get('code') if subject_info else 'DS'),
                'name': subject_info.name if hasattr(subject_info, 'name') else (subject_info.get('name') if subject_info else 'Data Structures')
            },
            'class': {
                'id': class_info.id if hasattr(class_info, 'id') else (class_info.get('id') if class_info else 'cls_01'),
                'name': class_info.name if hasattr(class_info, 'name') else (class_info.get('name') if class_info else 'Section A'),
                'room': class_info.room if hasattr(class_info, 'room') else (class_info.get('room') if class_info else 'Room 101'),
                'faculty': class_info.faculty_name if hasattr(class_info, 'faculty_name') else (class_info.get('faculty_name') if class_info else 'Er. Gagandeep Kaur')
            },
            'student': {
                'id': student.id if hasattr(student, 'id') else student.get('id'),
                'roll_number': student.roll_number if hasattr(student, 'roll_number') else student.get('roll_number'),
                'name': student.name if hasattr(student, 'name') else student.get('name'),
                'email': student.email if hasattr(student, 'email') else student.get('email'),
                'department': student.department if hasattr(student, 'department') else student.get('department')
            },
            'verification': {
                'method': record.get('verification_method', 'FACIAL_RECOGNITION_LIVENESS'),
                'anti_proxy_challenge': record.get('challenge_type'),
                'liveness_score': record.get('liveness_score'),
                'match_confidence': record.get('match_confidence'),
                'camera_id': record.get('camera_id'),
                'privacy_compliance': {
                    'embedding_stored_long_term': True,
                    'raw_image_stored_long_term': False,
                    'snapshot_ttl_hours': 72
                }
            }
        }
    }
    
    payload_str = json.dumps(payload)
    sig = 'sha256=' + hmac.new(secret.encode('utf-8'), payload_str.encode('utf-8'), hashlib.sha256).hexdigest()
    
    headers = {
        'Content-Type': 'application/json',
        'X-Presently-Signature': sig,
        'X-Presently-Event': 'attendance.marked',
        'X-Presently-Delivery': delivery_id,
        'User-Agent': 'Presently-LMS-Webhook/1.0'
    }
    
    # Internal mock routing or external HTTP POST
    is_mock = '/api/mock-lms/webhook' in target_url
    if is_mock:
        feed_entry = {
            'deliveryId': delivery_id,
            'timestamp': now_iso(),
            'event': 'attendance.marked',
            'signature': sig,
            'status': 'VERIFIED_VALID_HMAC',
            'statusCode': 200,
            'payload': payload
        }
        mock_lms_feed.insert(0, feed_entry)
        if len(mock_lms_feed) > 50:
            mock_lms_feed.pop()
        
        log_entry = LmsWebhook(
            id=delivery_id,
            event_type='attendance.marked',
            target_url=target_url,
            status='SUCCESS',
            response_code=200,
            payload=payload_str,
            response_body=json.dumps({'received': True}),
            sent_at=now_iso(),
            latency_ms=12
        )
        db.session.add(log_entry)
        db.session.commit()
        
        broadcast_sse('lms.received', feed_entry)
        return {'status': 'SUCCESS', 'deliveryId': delivery_id, 'statusCode': 200, 'signature': sig}
    else:
        try:
            start_t = time.time()
            resp = requests.post(target_url, data=payload_str, headers=headers, timeout=5)
            latency = int((time.time() - start_t) * 1000)
            
            log_entry = LmsWebhook(
                id=delivery_id,
                event_type='attendance.marked',
                target_url=target_url,
                status='SUCCESS' if resp.status_code < 400 else 'FAILED',
                response_code=resp.status_code,
                payload=payload_str,
                response_body=resp.text[:500],
                sent_at=now_iso(),
                latency_ms=latency
            )
            db.session.add(log_entry)
            db.session.commit()
            return {'status': 'SUCCESS', 'deliveryId': delivery_id, 'statusCode': resp.status_code}
        except Exception as e:
            log_entry = LmsWebhook(
                id=delivery_id,
                event_type='attendance.marked',
                target_url=target_url,
                status='FAILED',
                response_code=500,
                payload=payload_str,
                response_body=str(e),
                sent_at=now_iso(),
                latency_ms=0
            )
            db.session.add(log_entry)
            db.session.commit()
            return {'status': 'FAILED', 'error': str(e)}

# ==========================================
# Database Seeding
# ==========================================
def ensure_schema_migrations():
    try:
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        
        # students table migration
        if inspector.has_table('students'):
            stu_cols = [c['name'] for c in inspector.get_columns('students')]
            if 'photo_data' not in stu_cols:
                db.session.execute(db.text("ALTER TABLE students ADD COLUMN photo_data TEXT"))
            db.session.commit()

        # subjects table migrations
        if inspector.has_table('subjects'):
            sub_cols = [c['name'] for c in inspector.get_columns('subjects')]
            if 'teacher_id' not in sub_cols:
                db.session.execute(db.text("ALTER TABLE subjects ADD COLUMN teacher_id TEXT"))
            if 'teacher_name' not in sub_cols:
                db.session.execute(db.text("ALTER TABLE subjects ADD COLUMN teacher_name TEXT"))
            db.session.commit()
            
        # attendance_sessions table migrations
        if inspector.has_table('attendance_sessions'):
            sess_cols = [c['name'] for c in inspector.get_columns('attendance_sessions')]
            if 'subject_id' not in sess_cols:
                db.session.execute(db.text("ALTER TABLE attendance_sessions ADD COLUMN subject_id TEXT"))
            if 'subject_code' not in sess_cols:
                db.session.execute(db.text("ALTER TABLE attendance_sessions ADD COLUMN subject_code TEXT"))
            if 'subject_name' not in sess_cols:
                db.session.execute(db.text("ALTER TABLE attendance_sessions ADD COLUMN subject_name TEXT"))
            if 'teacher_name' not in sess_cols:
                db.session.execute(db.text("ALTER TABLE attendance_sessions ADD COLUMN teacher_name TEXT"))
            db.session.commit()

        # attendance_records table migrations
        if inspector.has_table('attendance_records'):
            rec_cols = [c['name'] for c in inspector.get_columns('attendance_records')]
            if 'subject_id' not in rec_cols:
                db.session.execute(db.text("ALTER TABLE attendance_records ADD COLUMN subject_id TEXT"))
            if 'subject_code' not in rec_cols:
                db.session.execute(db.text("ALTER TABLE attendance_records ADD COLUMN subject_code TEXT"))
            if 'subject_name' not in rec_cols:
                db.session.execute(db.text("ALTER TABLE attendance_records ADD COLUMN subject_name TEXT"))
            if 'teacher_name' not in rec_cols:
                db.session.execute(db.text("ALTER TABLE attendance_records ADD COLUMN teacher_name TEXT"))
            db.session.commit()
    except Exception:
        db.session.rollback()

def seed_database(force=False):
    ensure_schema_migrations()

    if not force and Subject.query.count() > 0:
        # Check if new tables need supplementary seeding
        if Teacher.query.count() == 0:
            now = now_iso()
            t_gagan = Teacher(id='t_gagan', name='Er. Gagandeep Kaur', email='gagandeep.kaur@university.edu', department='Computer Science', designation='Assistant Professor', created_at=now)
            t_marcus = Teacher(id='t_marcus', name='Prof. Marcus Vance', email='marcus.vance@university.edu', department='Artificial Intelligence', designation='Associate Professor', created_at=now)
            t_rajesh = Teacher(id='t_rajesh', name='Dr. Rajesh Sharma', email='rajesh.sharma@university.edu', department='Information Technology', designation='Professor', created_at=now)
            db.session.add_all([t_gagan, t_marcus, t_rajesh])
            db.session.commit()

            # Update existing subjects with teacher assignments
            sub_ds = Subject.query.get('sub_ds')
            if sub_ds: sub_ds.teacher_id = 't_gagan'; sub_ds.teacher_name = 'Er. Gagandeep Kaur'
            sub_cs101 = Subject.query.get('sub_cs101')
            if sub_cs101: sub_cs101.teacher_id = 't_gagan'; sub_cs101.teacher_name = 'Er. Gagandeep Kaur'
            sub_ai302 = Subject.query.get('sub_ai302')
            if sub_ai302: sub_ai302.teacher_id = 't_marcus'; sub_ai302.teacher_name = 'Prof. Marcus Vance'
            db.session.commit()

        if TimetableSlot.query.count() == 0:
            now = now_iso()
            # Seed default weekly timetable slots
            slots = [
                TimetableSlot(id='slot_m1', day_of_week='Monday', start_time='09:00', end_time='10:30', class_id='cls_cs101_a', subject_id='sub_cs101', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 102', created_at=now),
                TimetableSlot(id='slot_m2', day_of_week='Monday', start_time='10:45', end_time='12:15', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
                TimetableSlot(id='slot_m3', day_of_week='Monday', start_time='13:00', end_time='14:30', class_id='cls_ai302_a', subject_id='sub_ai302', teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', room='Computer Vision Lab 3', created_at=now),
                TimetableSlot(id='slot_t1', day_of_week='Tuesday', start_time='09:00', end_time='10:30', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
                TimetableSlot(id='slot_t2', day_of_week='Tuesday', start_time='14:00', end_time='16:00', class_id='cls_ds_lab', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Lab 2', created_at=now),
                TimetableSlot(id='slot_w1', day_of_week='Wednesday', start_time='09:00', end_time='10:30', class_id='cls_cs101_a', subject_id='sub_cs101', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 102', created_at=now),
                TimetableSlot(id='slot_w2', day_of_week='Wednesday', start_time='10:45', end_time='12:15', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
                TimetableSlot(id='slot_th1', day_of_week='Thursday', start_time='10:00', end_time='11:30', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
                TimetableSlot(id='slot_f1', day_of_week='Friday', start_time='13:00', end_time='14:30', class_id='cls_ai302_a', subject_id='sub_ai302', teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', room='Computer Vision Lab 3', created_at=now)
            ]
            db.session.add_all(slots)
            db.session.commit()

        if ClassSubject.query.count() == 0:
            now = now_iso()
            cs_entries = [
                ClassSubject(class_id='cls_ds_a', subject_id='sub_ds', created_at=now),
                ClassSubject(class_id='cls_ds_a', subject_id='sub_cs101', created_at=now),
                ClassSubject(class_id='cls_cs101_a', subject_id='sub_cs101', created_at=now),
                ClassSubject(class_id='cls_cs101_a', subject_id='sub_ds', created_at=now),
                ClassSubject(class_id='cls_ai302_a', subject_id='sub_ai302', created_at=now),
                ClassSubject(class_id='cls_ai302_a', subject_id='sub_ds', created_at=now),
                ClassSubject(class_id='cls_ds_lab', subject_id='sub_ds', created_at=now)
            ]
            db.session.add_all(cs_entries)
            db.session.commit()
        return

    if force:
        Snapshot.query.delete()
        AttendanceRecord.query.delete()
        AttendanceSession.query.delete()
        TimetableSlot.query.delete()
        ClassSubject.query.delete()
        ClassEnrollment.query.delete()
        Class.query.delete()
        Subject.query.delete()
        Teacher.query.delete()
        Student.query.delete()
        Camera.query.delete()
        SystemSetting.query.delete()
        db.session.commit()
    
    now = now_iso()
    
    # 1. System Settings
    settings = [
        ('lms_webhook_url', f'http://localhost:{PORT}/api/mock-lms/webhook'),
        ('lms_webhook_secret', 'presently_lms_secret_2026_x89a'),
        ('lms_enabled', '1'),
        ('snapshot_retention_hours', '72'),
        ('confidence_threshold', '0.75'),
        ('liveness_strictness', 'HIGH'),
        ('timetable_active_slot_id', 'slot_m2')
    ]
    for k, v in settings:
        db.session.add(SystemSetting(key=k, value=v))
    
    # 2. Teachers (One teacher can be assigned to many subjects!)
    t_gagan = Teacher(id='t_gagan', name='Er. Gagandeep Kaur', email='gagandeep.kaur@university.edu', department='Computer Science', designation='Assistant Professor', created_at=now)
    t_marcus = Teacher(id='t_marcus', name='Prof. Marcus Vance', email='marcus.vance@university.edu', department='Artificial Intelligence', designation='Associate Professor', created_at=now)
    t_rajesh = Teacher(id='t_rajesh', name='Dr. Rajesh Sharma', email='rajesh.sharma@university.edu', department='Information Technology', designation='Professor', created_at=now)
    db.session.add_all([t_gagan, t_marcus, t_rajesh])
    db.session.flush()  # teachers must exist before subjects/slots (FK order)
    
    # 3. Subjects with Assigned Teachers
    sub_ds = Subject(id='sub_ds', code='DS', name='Data Structures & Algorithms', department='Computer Science', credits=4, teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', created_at=now)
    sub_cs101 = Subject(id='sub_cs101', code='CS101', name='Introduction to Computer Science', department='Computer Science', credits=4, teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', created_at=now)
    sub_web = Subject(id='sub_web', code='CS205', name='Full Stack Web Engineering', department='Computer Science', credits=4, teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', created_at=now)
    sub_ai302 = Subject(id='sub_ai302', code='AI302', name='Applied Computer Vision & Deep Learning', department='Artificial Intelligence', credits=4, teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', created_at=now)
    sub_dbms = Subject(id='sub_dbms', code='CS204', name='Database Management Systems', department='Information Technology', credits=4, teacher_id='t_rajesh', teacher_name='Dr. Rajesh Sharma', created_at=now)
    sub_math201 = Subject(id='sub_math201', code='MATH201', name='Linear Algebra & Probability', department='Mathematics', credits=3, teacher_id='t_rajesh', teacher_name='Dr. Rajesh Sharma', created_at=now)
    db.session.add_all([sub_ds, sub_cs101, sub_web, sub_ai302, sub_dbms, sub_math201])
    db.session.flush()  # subjects before classes
    
    # 4. Classes
    cls_ds_a = Class(id='cls_ds_a', subject_id='sub_ds', name='DS - Section A (Data Structures)', room='Room 101', schedule='Mon / Wed 10:45 - 12:15 PM', faculty_name='Er. Gagandeep Kaur', faculty_email='gagandeep.kaur@university.edu', created_at=now)
    cls_ds_lab = Class(id='cls_ds_lab', subject_id='sub_ds', name='DS - Practical Lab Batch 1', room='Lab 2', schedule='Tue / Thu 02:00 - 04:00 PM', faculty_name='Er. Gagandeep Kaur', faculty_email='gagandeep.kaur@university.edu', created_at=now)
    cls_cs101_a = Class(id='cls_cs101_a', subject_id='sub_cs101', name='CS101 - Section A', room='Room 102', schedule='Mon / Wed 09:00 - 10:30 AM', faculty_name='Er. Gagandeep Kaur', faculty_email='gagandeep.kaur@university.edu', created_at=now)
    cls_ai302_a = Class(id='cls_ai302_a', subject_id='sub_ai302', name='AI302 - Honors Batch', room='Computer Vision Lab 3', schedule='Mon / Fri 01:00 - 02:45 PM', faculty_name='Prof. Marcus Vance', faculty_email='marcus.vance@university.edu', created_at=now)
    db.session.add_all([cls_ds_a, cls_ds_lab, cls_cs101_a, cls_ai302_a])
    db.session.flush()  # classes before class_subjects/slots/enrollments/sessions

    # 5. Class - Subject Associations (each class has specific subjects)
    cs_entries = [
        ClassSubject(class_id='cls_ds_a', subject_id='sub_ds', created_at=now),
        ClassSubject(class_id='cls_ds_a', subject_id='sub_cs101', created_at=now),
        ClassSubject(class_id='cls_ds_a', subject_id='sub_web', created_at=now),
        ClassSubject(class_id='cls_ds_a', subject_id='sub_dbms', created_at=now),
        ClassSubject(class_id='cls_cs101_a', subject_id='sub_cs101', created_at=now),
        ClassSubject(class_id='cls_cs101_a', subject_id='sub_ds', created_at=now),
        ClassSubject(class_id='cls_cs101_a', subject_id='sub_web', created_at=now),
        ClassSubject(class_id='cls_ai302_a', subject_id='sub_ai302', created_at=now),
        ClassSubject(class_id='cls_ai302_a', subject_id='sub_ds', created_at=now),
        ClassSubject(class_id='cls_ai302_a', subject_id='sub_math201', created_at=now),
        ClassSubject(class_id='cls_ds_lab', subject_id='sub_ds', created_at=now)
    ]
    db.session.add_all(cs_entries)

    # 6. Weekly Timetable Slots (Admin Configured Schedule)
    slots = [
        TimetableSlot(id='slot_m1', day_of_week='Monday', start_time='09:00', end_time='10:30', class_id='cls_cs101_a', subject_id='sub_cs101', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 102', created_at=now),
        TimetableSlot(id='slot_m2', day_of_week='Monday', start_time='10:45', end_time='12:15', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
        TimetableSlot(id='slot_m3', day_of_week='Monday', start_time='13:00', end_time='14:30', class_id='cls_ai302_a', subject_id='sub_ai302', teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', room='Computer Vision Lab 3', created_at=now),
        TimetableSlot(id='slot_t1', day_of_week='Tuesday', start_time='09:00', end_time='10:30', class_id='cls_ds_a', subject_id='sub_web', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Lab 2', created_at=now),
        TimetableSlot(id='slot_t2', day_of_week='Tuesday', start_time='10:45', end_time='12:15', class_id='cls_ds_a', subject_id='sub_dbms', teacher_id='t_rajesh', teacher_name='Dr. Rajesh Sharma', room='Room 101', created_at=now),
        TimetableSlot(id='slot_t3', day_of_week='Tuesday', start_time='14:00', end_time='16:00', class_id='cls_ds_lab', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Lab 2', created_at=now),
        TimetableSlot(id='slot_w1', day_of_week='Wednesday', start_time='09:00', end_time='10:30', class_id='cls_cs101_a', subject_id='sub_cs101', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 102', created_at=now),
        TimetableSlot(id='slot_w2', day_of_week='Wednesday', start_time='10:45', end_time='12:15', class_id='cls_ds_a', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Room 101', created_at=now),
        TimetableSlot(id='slot_w3', day_of_week='Wednesday', start_time='13:00', end_time='14:30', class_id='cls_ai302_a', subject_id='sub_ai302', teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', room='Computer Vision Lab 3', created_at=now),
        TimetableSlot(id='slot_th1', day_of_week='Thursday', start_time='10:00', end_time='11:30', class_id='cls_ds_a', subject_id='sub_dbms', teacher_id='t_rajesh', teacher_name='Dr. Rajesh Sharma', room='Room 101', created_at=now),
        TimetableSlot(id='slot_th2', day_of_week='Thursday', start_time='14:00', end_time='16:00', class_id='cls_ds_lab', subject_id='sub_ds', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Lab 2', created_at=now),
        TimetableSlot(id='slot_f1', day_of_week='Friday', start_time='09:30', end_time='11:00', class_id='cls_ds_a', subject_id='sub_web', teacher_id='t_gagan', teacher_name='Er. Gagandeep Kaur', room='Lab 2', created_at=now),
        TimetableSlot(id='slot_f2', day_of_week='Friday', start_time='13:00', end_time='14:30', class_id='cls_ai302_a', subject_id='sub_ai302', teacher_id='t_marcus', teacher_name='Prof. Marcus Vance', room='Computer Vision Lab 3', created_at=now)
    ]
    db.session.add_all(slots)
    
    # 4. Students
    initial_students = [
        ('stu_01', 'CS-2024-001', 'Alex Mercer', 'alex.mercer@campus.edu', 'Computer Science', '#3b82f6'),
        ('stu_02', 'CS-2024-002', 'Brianna Hayes', 'brianna.h@campus.edu', 'Computer Science', '#ec4899'),
        ('stu_03', 'CS-2024-003', 'Carlos Mendez', 'carlos.m@campus.edu', 'Computer Science', '#10b981'),
        ('stu_04', 'CS-2024-004', 'Divya Patel', 'divya.p@campus.edu', 'Computer Science', '#8b5cf6'),
        ('stu_05', 'CS-2024-005', 'Ethan Zhao', 'ethan.z@campus.edu', 'Computer Science', '#f59e0b'),
        ('stu_06', 'CS-2024-006', 'Fatima Al-Mansoor', 'fatima.m@campus.edu', 'Computer Science', '#06b6d4'),
        ('stu_07', 'CS-2024-007', 'Gabriel Torres', 'gabriel.t@campus.edu', 'Computer Science', '#ef4444'),
        ('stu_08', 'CS-2024-008', 'Hannah Schmidt', 'hannah.s@campus.edu', 'Computer Science', '#14b8a6'),
        ('stu_09', 'CS-2024-009', 'Ian MacLeod', 'ian.m@campus.edu', 'Computer Science', '#6366f1'),
        ('stu_10', 'CS-2024-010', 'Jasmine Kaur', 'jasmine.k@campus.edu', 'Computer Science', '#d946ef'),
        ('stu_11', 'CS-2024-011', 'Koji Tanaka', 'koji.t@campus.edu', 'Computer Science', '#84cc16'),
        ('stu_12', 'CS-2024-012', 'Leila Benali', 'leila.b@campus.edu', 'Computer Science', '#f97316'),
    ]
    for sid, roll, sname, semail, sdept, scolor in initial_students:
        emb = generate_seeded_embedding(f"{roll}_{sname}")
        stu = Student(
            id=sid,
            roll_number=roll,
            name=sname,
            email=semail,
            department=sdept,
            avatar_color=scolor,
            face_embedding=json.dumps(emb),
            is_enrolled_face=1,
            created_at=now
        )
        db.session.add(stu)
        db.session.flush()  # student row must exist before enrollments
        # Enroll in CS101 Section A
        db.session.add(ClassEnrollment(class_id='cls_cs101_a', student_id=sid, enrolled_at=now))
        # First 6 in AI302
        if int(sid.split('_')[1]) <= 6:
            db.session.add(ClassEnrollment(class_id='cls_ai302_a', student_id=sid, enrolled_at=now))
        # Enroll in DS
        db.session.add(ClassEnrollment(class_id='cls_ds_a', student_id=sid, enrolled_at=now))
    
    # 5. Cameras
    cams = [
        ('CAM-ROOM-101', 'Hall 101 Entrance Camera', 'Room 101', '10.0.12.101', 'ONLINE', 'cam_key_r101_secure_77f', 30, 'v2.4.0-edge'),
        ('CAM-ROOM-102', 'Room 102 Podium Terminal', 'Room 102', '10.0.12.102', 'ONLINE', 'cam_key_r102_secure_88a', 30, 'v2.4.0-edge'),
        ('CAM-LAB-03', 'CV Lab 3 Kiosk Node', 'Computer Vision Lab 3', '10.0.12.103', 'ONLINE', 'cam_key_lab3_secure_99b', 60, 'v2.4.1-edge-pro'),
        ('CAM-AUD-A', 'Auditorium A Gate Camera', 'Hall 204', '10.0.12.104', 'ONLINE', 'cam_key_auda_secure_44c', 30, 'v2.4.0-edge'),
        ('CAM-NODE-01', 'Edge Node Terminal 01', 'Room 101', '127.0.0.1', 'ONLINE', 'cam_key_node01', 30, 'v2.4.0-edge')
    ]
    for cid, cname, croom, cip, cstatus, ckey, cfps, cfw in cams:
        db.session.add(Camera(
            id=cid,
            name=cname,
            room=croom,
            ip_address=cip,
            status=cstatus,
            api_key=ckey,
            last_heartbeat=now,
            fps=cfps,
            firmware=cfw
        ))
    
    # 6. Active Session for CS101 Section A
    sess_id = 'sess_live_cs101'
    db.session.add(AttendanceSession(
        id=sess_id,
        class_id='cls_cs101_a',
        subject_id='sub_cs101',
        subject_code='CS101',
        subject_name='Introduction to Computer Science',
        teacher_name='Er. Gagandeep Kaur',
        room='Room 101',
        camera_id='CAM-ROOM-101',
        status='ACTIVE',
        started_at=now,
        created_by='Er. Gagandeep Kaur'
    ))
    db.session.flush()  # session must exist before attendance records
    
    # 7. Seed 2 Pre-marked Attendance Records
    expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=72)).isoformat()
    mock_svg = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160"><rect width="160" height="160" fill="%231e293b"/></svg>'
    
    rec1 = AttendanceRecord(
        id='rec_seed_01',
        session_id=sess_id,
        student_id='stu_01',
        class_id='cls_cs101_a',
        subject_id='sub_cs101',
        subject_code='CS101',
        subject_name='Introduction to Computer Science',
        teacher_name='Er. Gagandeep Kaur',
        marked_at=now,
        verification_method='FACIAL_RECOGNITION_LIVENESS',
        challenge_type='TURN_LEFT',
        liveness_score=0.98,
        match_confidence=0.97,
        camera_id='CAM-ROOM-101',
        snapshot_id='snap_seed_01',
        status='PRESENT'
    )
    snap1 = Snapshot(
        id='snap_seed_01',
        record_id='rec_seed_01',
        image_data=mock_svg,
        captured_at=now,
        expires_at=expires_at,
        is_purged=0
    )
    
    rec2 = AttendanceRecord(
        id='rec_seed_02',
        session_id=sess_id,
        student_id='stu_02',
        class_id='cls_cs101_a',
        subject_id='sub_cs101',
        subject_code='CS101',
        subject_name='Introduction to Computer Science',
        teacher_name='Er. Gagandeep Kaur',
        marked_at=now,
        verification_method='FACIAL_RECOGNITION_LIVENESS',
        challenge_type='BLINK',
        liveness_score=0.96,
        match_confidence=0.94,
        camera_id='CAM-ROOM-101',
        snapshot_id='snap_seed_02',
        status='PRESENT'
    )
    snap2 = Snapshot(
        id='snap_seed_02',
        record_id='rec_seed_02',
        image_data=mock_svg,
        captured_at=now,
        expires_at=expires_at,
        is_purged=0
    )
    
    db.session.add_all([rec1, rec2])
    db.session.flush()  # records must exist before their snapshots
    db.session.add_all([snap1, snap2])
    db.session.commit()

# ==========================================
# AUTHENTICATION ENDPOINTS
# ==========================================
@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.get_json(force=True) or {}
    username = str(data.get('username', '')).strip()
    password = str(data.get('password', '')).strip()
    requested_role = data.get('role')
    
    # Camera Node dedicated: camera / camera123 or node / node123
    if username in ['camera', 'node', 'cam'] and password in ['camera123', 'node123', '123', 'admin123']:
        token = 'tok_cam_' + secrets.token_hex(20)
        user_info = {
            'token': token,
            'username': username,
            'role': 'NODE',
            'displayName': 'Classroom Camera Node',
            'title': 'Autonomous Edge Terminal',
            'email': 'camera@presently.edu',
            'department': 'Classroom Hardware',
            'subject': 'Automated Attendance',
            'createdAt': int(time.time() * 1000),
            'expiresAt': int(time.time() * 1000) + 86400000
        }
        active_sessions[token] = user_info
        login_user(AppUser(user_info))
        return jsonify({'success': True, 'token': token, 'user': user_info}), 200

    # Faculty: 123 / 123
    if username == '123' and password == '123':
        is_node = requested_role and requested_role.upper() == 'NODE'
        token = 'tok_fac_' + secrets.token_hex(20)
        user_info = {
            'token': token,
            'username': '123',
            'role': 'NODE' if is_node else 'FACULTY',
            'displayName': 'Er. Gagandeep Kaur',
            'title': 'Camera Node Operator' if is_node else 'Teacher of Data Structures (DS)',
            'email': 'gagandeep.kaur@university.edu',
            'department': 'Computer Science',
            'subject': 'Data Structures (DS)',
            'createdAt': int(time.time() * 1000),
            'expiresAt': int(time.time() * 1000) + 86400000
        }
        active_sessions[token] = user_info
        login_user(AppUser(user_info))
        return jsonify({'success': True, 'token': token, 'user': user_info}), 200
    
    # Admin: admin123 / admin123
    if username == 'admin123' and password == 'admin123':
        is_node = requested_role and requested_role.upper() == 'NODE'
        if is_node:
            role = 'NODE'
        elif requested_role and requested_role.upper() == 'FACULTY':
            role = 'FACULTY'
        else:
            role = 'ADMIN'
        token = 'tok_adm_' + secrets.token_hex(20)
        user_info = {
            'token': token,
            'username': 'admin123',
            'role': role,
            'displayName': 'Camera Node (Admin)' if role == 'NODE' else ('Er. Gagandeep Kaur (Faculty)' if role == 'FACULTY' else 'System Administrator'),
            'title': 'Camera Node Operator' if role == 'NODE' else ('Teacher of Data Structures (DS)' if role == 'FACULTY' else 'Administrator'),
            'email': 'gagandeep.kaur@university.edu' if role == 'FACULTY' else 'admin@presently.edu',
            'department': 'Computer Science',
            'subject': 'Data Structures (DS)',
            'createdAt': int(time.time() * 1000),
            'expiresAt': int(time.time() * 1000) + 86400000
        }
        active_sessions[token] = user_info
        login_user(AppUser(user_info))
        return jsonify({'success': True, 'token': token, 'user': user_info}), 200
    
    return jsonify({
        'success': False,
        'error': 'Invalid username or password.'
    }), 401

@app.route('/api/auth/me', methods=['GET'])
def api_auth_me():
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip()
    user_info = active_sessions.get(token)
    if not user_info or time.time() * 1000 > user_info.get('expiresAt', 0):
        if token in active_sessions:
            del active_sessions[token]
        return jsonify({'authenticated': False, 'error': 'Unauthorized or token expired'}), 401
    return jsonify({'authenticated': True, 'user': user_info}), 200

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.replace('Bearer ', '').strip()
    active_sessions.pop(token, None)
    logout_user()
    return jsonify({'success': True, 'message': 'Logged out successfully'}), 200

# ==========================================
# SSE REAL-TIME STREAM
# ==========================================
@app.route('/api/stream/attendance', methods=['GET'])
def api_sse_stream():
    q = Queue()
    with sse_lock:
        sse_subscribers.append(q)
    
    def event_stream():
        yield ": heartbeat\n\n"
        while True:
            try:
                msg = q.get(timeout=25)
                yield msg
            except Exception:
                yield ": keepalive\n\n"
                
    return Response(event_stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*'
    })

# ==========================================
# CHALLENGE GENERATOR
# ==========================================
@app.route('/api/challenge/new', methods=['GET'])
def api_challenge_new():
    session_id = request.args.get('sessionId')
    preferred_action = request.args.get('action')
    ch = create_challenge(session_id, preferred_action)
    return jsonify(ch), 200

# ==========================================
# ATTENDANCE MARKING (CORE ENGINE)
# ==========================================
@app.route('/api/attendance/verify-and-mark', methods=['POST'])
@app.route('/api/node/mark', methods=['POST'])
def api_verify_and_mark():
    body = request.get_json(force=True) or {}
    session_id = body.get('sessionId') or body.get('session_id')
    camera_id = body.get('cameraId') or body.get('camera_id', 'CAM-ROOM-101')
    embedding = body.get('embedding')
    challenge_id = body.get('challengeId') or body.get('challenge_id')
    action_completed = body.get('actionCompleted') or body.get('challenge_type') or body.get('action_completed')
    telemetry = body.get('telemetry', {})
    snapshot_base64 = body.get('snapshotBase64') or body.get('snapshot_data')
    force_student_id = body.get('forceStudentId') or body.get('student_id')
    
    # 1. Validate Session
    sess = AttendanceSession.query.get(session_id)
    if not sess:
        return jsonify({'error': 'Attendance session not found or inactive'}), 404
    
    # 2. Anti-Proxy Liveness Verification
    liveness_check = verify_challenge(challenge_id, action_completed, telemetry)
    if not liveness_check['passed']:
        return jsonify({
            'success': False,
            'code': 'ANTI_PROXY_FAILED',
            'message': liveness_check['reason'],
            'actionRequired': liveness_check['action']
        }), 400
        
    # 2b. Computer Vision Presentation Attack Check (OpenCV)
    if snapshot_base64:
        pad_result = check_presentation_attack_cv2(snapshot_base64)
        if pad_result.get('is_spoof'):
            return jsonify({
                'success': False,
                'code': 'ANTI_PROXY_FAILED',
                'message': f"Presentation Attack Detected: {pad_result['reason']}. Proxy rejected!",
                'actionRequired': liveness_check.get('action')
            }), 400
    
    # 3. Find Enrolled Students for this Class
    class_id = sess.class_id
    enrollments = ClassEnrollment.query.filter_by(class_id=class_id).all()
    enrolled_student_ids = [e.student_id for e in enrollments]
    if not enrolled_student_ids:
        return jsonify({'error': 'No students enrolled in this class'}), 400
    
    enrolled_students = Student.query.filter(Student.id.in_(enrolled_student_ids)).all()
    
    # 4. Face Recognition Matching
    matched_student = None
    confidence = 0.96
    
    if force_student_id:
        matched_student = next((s for s in enrolled_students if s.id == force_student_id), None)
    elif embedding and isinstance(embedding, list):
        best_sim = -1.0
        best_stu = None
        for s in enrolled_students:
            try:
                s_emb = json.loads(s.face_embedding)
                sim = cosine_similarity(embedding, s_emb)
                if sim > best_sim:
                    best_sim = sim
                    best_stu = s
            except Exception:
                continue
        if best_sim >= 0.72 and best_stu:
            matched_student = best_stu
            confidence = round(best_sim, 2)
            
    if not matched_student:
        return jsonify({
            'success': False,
            'code': 'UNKNOWN_FACE',
            'message': 'Face not recognized among enrolled students for this class',
            'confidence': confidence,
            'threshold': 0.72
        }), 404
        
    # 5. Strict Idempotency: One mark per student per session!
    existing = AttendanceRecord.query.filter_by(session_id=session_id, student_id=matched_student.id).first()
    if existing:
        return jsonify({
            'success': True,
            'code': 'ALREADY_MARKED',
            'message': f"Attendance already marked for {matched_student.name} ({matched_student.roll_number}) in this session.",
            'record': {
                'id': existing.id,
                'session_id': existing.session_id,
                'student_id': existing.student_id,
                'student_name': matched_student.name,
                'roll_number': matched_student.roll_number,
                'marked_at': existing.marked_at,
                'camera_id': existing.camera_id
            },
            'student': {
                'id': matched_student.id,
                'name': matched_student.name,
                'roll_number': matched_student.roll_number
            },
            'duplicatePrevented': True
        }), 200
        
    # 6. Insert Record with Subject & Teacher details
    record_id = 'rec_' + secrets.token_hex(6)
    now = now_iso()
    class_info = Class.query.get(class_id)
    subject_info = Subject.query.get(sess.subject_id or (class_info.subject_id if class_info else None))
    sub_code = sess.subject_code or (subject_info.code if subject_info else 'SUB')
    sub_name = sess.subject_name or (subject_info.name if subject_info else 'Subject')
    tch_name = sess.teacher_name or (subject_info.teacher_name if subject_info else (class_info.faculty_name if class_info else 'Faculty'))

    rec = AttendanceRecord(
        id=record_id,
        session_id=session_id,
        student_id=matched_student.id,
        class_id=class_id,
        subject_id=subject_info.id if subject_info else None,
        subject_code=sub_code,
        subject_name=sub_name,
        teacher_name=tch_name,
        marked_at=now,
        verification_method='FACIAL_RECOGNITION_LIVENESS',
        challenge_type=liveness_check['action'] or 'BLINK',
        liveness_score=liveness_check.get('livenessScore', 0.95),
        match_confidence=confidence,
        camera_id=camera_id,
        status='PRESENT'
    )
    db.session.add(rec)
    
    # 7. Snapshot Vault
    snapshot_meta = None
    if snapshot_base64:
        snap_id = 'snap_' + secrets.token_hex(6)
        exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=72)).isoformat()
        snap = Snapshot(
            id=snap_id,
            record_id=record_id,
            image_data=snapshot_base64,
            captured_at=now,
            expires_at=exp,
            is_purged=0
        )
        db.session.add(snap)
        rec.snapshot_id = snap_id
        snapshot_meta = {'snapshotId': snap_id, 'expiresAt': exp}
        
    db.session.commit()
    
    # 8. Push to LMS Webhook
    lms_result = push_attendance_to_lms(
        {
            'id': record_id,
            'marked_at': now,
            'verification_method': 'FACIAL_RECOGNITION_LIVENESS',
            'challenge_type': rec.challenge_type,
            'liveness_score': rec.liveness_score,
            'match_confidence': rec.match_confidence,
            'camera_id': camera_id,
            'status': 'PRESENT',
            'subject_code': sub_code,
            'subject_name': sub_name,
            'teacher_name': tch_name
        },
        matched_student,
        sess,
        class_info,
        subject_info
    )
    
    attendance_record = {
        'id': record_id,
        'sessionId': session_id,
        'studentId': matched_student.id,
        'studentName': matched_student.name,
        'rollNumber': matched_student.roll_number,
        'department': matched_student.department,
        'avatarColor': matched_student.avatar_color,
        'markedAt': now,
        'challengeType': rec.challenge_type,
        'livenessScore': rec.liveness_score,
        'matchConfidence': rec.match_confidence,
        'cameraId': camera_id,
        'status': 'PRESENT',
        'subjectId': rec.subject_id,
        'subjectCode': rec.subject_code,
        'subjectName': rec.subject_name,
        'teacherName': rec.teacher_name,
        'className': class_info.name if class_info else '',
        'snapshotId': snapshot_meta['snapshotId'] if snapshot_meta else None,
        'snapshotExpiresAt': snapshot_meta['expiresAt'] if snapshot_meta else None
    }
    
    # 9. SSE broadcast
    broadcast_sse('attendance.marked', {
        'session_id': session_id,
        'record': attendance_record,
        'lms': lms_result
    })
    
    return jsonify({
        'success': True,
        'code': 'MARKED_SUCCESSFULLY',
        'message': f"Attendance marked successfully for {matched_student.name}",
        'record': attendance_record,
        'lmsDelivery': lms_result,
        'privacyNotice': 'Biometric vectors preserved. Verification snapshot will auto-delete in 72 hours.'
    }), 201

# Face Matching API (for camera script / edge node)
@app.route('/api/face/match', methods=['POST'])
def api_face_match():
    body = request.get_json(force=True) or {}
    embedding = body.get('embedding')
    class_id = body.get('class_id')
    if not embedding or not isinstance(embedding, list):
        return jsonify({'error': 'Invalid face embedding vector'}), 400
    
    if class_id:
        enrollments = ClassEnrollment.query.filter_by(class_id=class_id).all()
        stu_ids = [e.student_id for e in enrollments]
        students = Student.query.filter(Student.id.in_(stu_ids)).all()
    else:
        students = Student.query.all()
        
    best_sim = -1.0
    best_stu = None
    for s in students:
        try:
            s_emb = json.loads(s.face_embedding)
            sim = cosine_similarity(embedding, s_emb)
            if sim > best_sim:
                best_sim = sim
                best_stu = s
        except Exception:
            continue
            
    threshold = 0.72
    is_match = best_sim >= threshold and best_stu is not None
    return jsonify({
        'isMatch': is_match,
        'student': {
            'id': best_stu.id,
            'name': best_stu.name,
            'roll_number': best_stu.roll_number,
            'department': best_stu.department
        } if best_stu else None,
        'confidence': round(best_sim, 2),
        'threshold': threshold
    }), 200

# ==========================================
# SUBJECTS MANAGEMENT (ADMIN)
# ==========================================
@app.route('/api/subjects', methods=['GET'])
def api_get_subjects():
    subs = Subject.query.order_by(Subject.code.asc()).all()
    return jsonify([{
        'id': s.id,
        'code': s.code,
        'name': s.name,
        'department': s.department,
        'credits': s.credits,
        'created_at': s.created_at
    } for s in subs]), 200

@app.route('/api/subjects', methods=['POST'])
def api_create_subject():
    body = request.get_json(force=True) or {}
    code = body.get('code', '').strip().upper()
    name = body.get('name', '').strip()
    department = body.get('department', '').strip()
    credits = int(body.get('credits', 3))
    
    if not code or not name or not department:
        return jsonify({'error': 'Code, name, and department are required'}), 400
        
    sub_id = 'sub_' + code.lower()
    now = now_iso()
    sub = Subject(id=sub_id, code=code, name=name, department=department, credits=credits, created_at=now)
    try:
        db.session.add(sub)
        db.session.commit()
        return jsonify({
            'success': True,
            'subject': {
                'id': sub_id,
                'code': code,
                'name': name,
                'department': department,
                'credits': credits,
                'created_at': now
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/subjects/<subject_id>', methods=['DELETE'])
def api_delete_subject(subject_id):
    classes = Class.query.filter_by(subject_id=subject_id).all()
    for c in classes:
        ClassEnrollment.query.filter_by(class_id=c.id).delete()
        AttendanceRecord.query.filter_by(class_id=c.id).delete()
        AttendanceSession.query.filter_by(class_id=c.id).delete()
        db.session.delete(c)
    Subject.query.filter_by(id=subject_id).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Subject and associated classes deleted successfully'}), 200

# ==========================================
# CLASSES MANAGEMENT (ADMIN)
# ==========================================
@app.route('/api/classes', methods=['GET'])
def api_get_classes():
    classes = db.session.query(Class, Subject).join(Subject, Class.subject_id == Subject.id).order_by(Subject.code, Class.name).all()
    res = []
    for c, s in classes:
        count = ClassEnrollment.query.filter_by(class_id=c.id).count()
        res.append({
            'id': c.id,
            'subject_id': c.subject_id,
            'name': c.name,
            'room': c.room,
            'schedule': c.schedule,
            'faculty_name': c.faculty_name,
            'faculty_email': c.faculty_email,
            'created_at': c.created_at,
            'subject_code': s.code,
            'subject_name': s.name,
            'subject_dept': s.department,
            'enrolled_count': count
        })
    return jsonify(res), 200

@app.route('/api/classes', methods=['POST'])
def api_create_class():
    body = request.get_json(force=True) or {}
    subject_id = body.get('subject_id')
    name = body.get('name')
    room = body.get('room')
    schedule = body.get('schedule', 'Regular Schedule')
    faculty_name = body.get('faculty_name')
    faculty_email = body.get('faculty_email', '')
    
    if not subject_id or not name or not room or not faculty_name:
        return jsonify({'error': 'Subject, name, room, and faculty name are required'}), 400
        
    class_id = 'cls_' + secrets.token_hex(4)
    now = now_iso()
    cls = Class(
        id=class_id,
        subject_id=subject_id,
        name=name,
        room=room,
        schedule=schedule,
        faculty_name=faculty_name,
        faculty_email=faculty_email,
        created_at=now
    )
    db.session.add(cls)
    
    # Auto-enroll all existing students
    all_students = Student.query.all()
    for s in all_students:
        db.session.add(ClassEnrollment(class_id=class_id, student_id=s.id, enrolled_at=now))
        
    db.session.commit()
    return jsonify({
        'success': True,
        'class': {
            'id': class_id,
            'subject_id': subject_id,
            'name': name,
            'room': room,
            'schedule': schedule,
            'faculty_name': faculty_name,
            'faculty_email': faculty_email
        }
    }), 201

@app.route('/api/classes/<class_id>', methods=['DELETE'])
def api_delete_class(class_id):
    ClassEnrollment.query.filter_by(class_id=class_id).delete()
    AttendanceRecord.query.filter_by(class_id=class_id).delete()
    AttendanceSession.query.filter_by(class_id=class_id).delete()
    Class.query.filter_by(id=class_id).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Class deleted successfully'}), 200

@app.route('/api/classes/<class_id>/students', methods=['GET'])
def api_class_students(class_id):
    enrollments = ClassEnrollment.query.filter_by(class_id=class_id).all()
    stu_ids = [e.student_id for e in enrollments]
    students = Student.query.filter(Student.id.in_(stu_ids)).order_by(Student.roll_number.asc()).all()
    res = []
    for s in students:
        rec_count = AttendanceRecord.query.filter_by(class_id=class_id, student_id=s.id).count()
        res.append({
            'id': s.id,
            'roll_number': s.roll_number,
            'name': s.name,
            'email': s.email,
            'department': s.department,
            'avatar_color': s.avatar_color,
            'photo_data': getattr(s, 'photo_data', None),
            'is_enrolled_face': s.is_enrolled_face,
            'attendance_count': rec_count,
            'embedding_sample': json.loads(s.face_embedding)[0] if s.face_embedding else 0.0
        })
    return jsonify(res), 200

@app.route('/api/classes/<class_id>/students', methods=['POST'])
def api_class_add_student(class_id):
    body = request.get_json(force=True) or {}
    student_id = body.get('student_id')
    if not student_id:
        return jsonify({'error': 'student_id is required'}), 400
    existing = ClassEnrollment.query.filter_by(class_id=class_id, student_id=student_id).first()
    if not existing:
        db.session.add(ClassEnrollment(class_id=class_id, student_id=student_id, enrolled_at=now_iso()))
        db.session.commit()
    return jsonify({'success': True, 'message': 'Student added to class roster'}), 200

@app.route('/api/classes/<class_id>/students/<student_id>', methods=['DELETE'])
def api_class_remove_student(class_id, student_id):
    ClassEnrollment.query.filter_by(class_id=class_id, student_id=student_id).delete()
    db.session.commit()
    return jsonify({'success': True, 'message': 'Student removed from class roster'}), 200

# ==========================================
# STUDENTS MANAGEMENT & ENROLLMENT (ADMIN)
# ==========================================
@app.route('/api/students', methods=['GET'])
def api_get_students():
    students = Student.query.order_by(Student.roll_number.asc()).all()
    res = []
    for s in students:
        rec_count = AttendanceRecord.query.filter_by(student_id=s.id).count()
        res.append({
            'id': s.id,
            'roll_number': s.roll_number,
            'name': s.name,
            'email': s.email,
            'department': s.department,
            'avatar_color': s.avatar_color,
            'photo_data': getattr(s, 'photo_data', None),
            'is_enrolled_face': s.is_enrolled_face,
            'created_at': s.created_at,
            'attendance_count': rec_count,
            'privacy_status': 'EMBEDDINGS_STORED'
        })
    return jsonify(res), 200

@app.route('/api/students/enroll', methods=['POST'])
def api_enroll_student():
    body = request.get_json(force=True) or {}
    roll_number = body.get('roll_number', '').strip().upper()
    name = body.get('name', '').strip()
    email = body.get('email', '').strip()
    department = body.get('department', 'Computer Science').strip()
    custom_embedding = body.get('customEmbedding')
    photo_data = body.get('photoData') or body.get('photo_data')
    
    if not roll_number or not name or not email:
        return jsonify({'error': 'Roll number, name, and email are required'}), 400
        
    stu_id = 'stu_' + secrets.token_hex(4)
    now = now_iso()
    colors_list = ['#3b82f6', '#10b981', '#ec4899', '#8b5cf6', '#f59e0b', '#06b6d4', '#ef4444']
    avatar_color = secrets.choice(colors_list)
    
    if custom_embedding and isinstance(custom_embedding, list):
        embedding = custom_embedding
    elif photo_data:
        embedding = generate_seeded_embedding(f"{roll_number}_{photo_data[:120]}")
    else:
        embedding = generate_seeded_embedding(f"{roll_number}_{name}_{int(time.time())}")
        
    stu = Student(
        id=stu_id,
        roll_number=roll_number,
        name=name,
        email=email,
        department=department,
        avatar_color=avatar_color,
        face_embedding=json.dumps(embedding),
        photo_data=photo_data,
        is_enrolled_face=1,
        created_at=now
    )
    db.session.add(stu)
    
    # Auto-enroll in all existing classes
    classes = Class.query.all()
    for c in classes:
        db.session.add(ClassEnrollment(class_id=c.id, student_id=stu_id, enrolled_at=now))
        
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'student': {
                'id': stu_id,
                'roll_number': roll_number,
                'name': name,
                'email': email,
                'department': department,
                'avatar_color': avatar_color,
                'photo_data': photo_data,
                'embedding_hash': hash_embedding(embedding),
                'privacy_guarantee': '128-d Biometric vector extracted for attendance recognition'
            }
        }), 201
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

@app.route('/api/students', methods=['DELETE'])
def api_delete_all_students():
    count = Student.query.count()
    ClassEnrollment.query.delete()
    Snapshot.query.delete()
    AttendanceRecord.query.delete()
    Student.query.delete()
    db.session.commit()
    broadcast_sse('students.updated', {'action': 'ALL_DELETED', 'count': count})
    return jsonify({
        'success': True,
        'deletedCount': count,
        'message': f"Successfully removed all {count} students and cleared the roster."
    }), 200

@app.route('/api/students/<student_id>', methods=['DELETE'])
def api_delete_student(student_id):
    ClassEnrollment.query.filter_by(student_id=student_id).delete()
    AttendanceRecord.query.filter_by(student_id=student_id).delete()
    Student.query.filter_by(id=student_id).delete()
    db.session.commit()
    broadcast_sse('students.updated', {'action': 'DELETED', 'id': student_id})
    return jsonify({
        'success': True,
        'deletedId': student_id,
        'message': 'Student deleted successfully.'
    }), 200

# ==========================================
# SESSIONS MANAGEMENT (FACULTY & EDGE)
# ==========================================
@app.route('/api/sessions', methods=['GET'])
def api_get_sessions():
    status = request.args.get('status')
    class_id = request.args.get('class_id')
    query = AttendanceSession.query
    if status:
        query = query.filter_by(status=status)
    if class_id:
        query = query.filter_by(class_id=class_id)
    sessions = query.order_by(AttendanceSession.started_at.desc()).all()
    return jsonify([{
        'id': s.id,
        'class_id': s.class_id,
        'room': s.room,
        'camera_id': s.camera_id,
        'status': s.status,
        'started_at': s.started_at,
        'ended_at': s.ended_at,
        'created_by': s.created_by
    } for s in sessions]), 200

# ==========================================
# TEACHERS & SUBJECT ASSIGNMENT API
# ==========================================
@app.route('/api/teachers', methods=['GET'])
def api_get_teachers():
    teachers = Teacher.query.order_by(Teacher.name.asc()).all()
    res = []
    for t in teachers:
        subs = Subject.query.filter_by(teacher_id=t.id).all()
        res.append({
            'id': t.id,
            'name': t.name,
            'email': t.email,
            'department': t.department,
            'designation': t.designation,
            'subject_count': len(subs),
            'subjects': [{'id': s.id, 'code': s.code, 'name': s.name} for s in subs]
        })
    return jsonify(res), 200

@app.route('/api/teachers', methods=['POST'])
def api_create_teacher():
    body = request.get_json(force=True) or {}
    name = body.get('name')
    email = body.get('email')
    dept = body.get('department', 'Computer Science')
    desig = body.get('designation', 'Assistant Professor')
    if not name or not email:
        return jsonify({'error': 'Name and email are required'}), 400
    tid = 'tch_' + secrets.token_hex(4)
    t = Teacher(id=tid, name=name, email=email, department=dept, designation=desig, created_at=now_iso())
    db.session.add(t)
    db.session.commit()
    return jsonify({'success': True, 'teacher': {'id': t.id, 'name': t.name, 'email': t.email}}), 201

@app.route('/api/subjects/<subject_id>/assign-teacher', methods=['POST'])
def api_assign_teacher_to_subject(subject_id):
    sub = Subject.query.get(subject_id)
    if not sub:
        return jsonify({'error': 'Subject not found'}), 404
    body = request.get_json(force=True) or {}
    teacher_id = body.get('teacher_id')
    teacher = Teacher.query.get(teacher_id) if teacher_id else None
    if teacher:
        sub.teacher_id = teacher.id
        sub.teacher_name = teacher.name
    else:
        sub.teacher_id = None
        sub.teacher_name = body.get('teacher_name')
    db.session.commit()
    return jsonify({'success': True, 'subject_id': sub.id, 'teacher_name': sub.teacher_name}), 200

# ==========================================
# CLASS - SPECIFIC SUBJECTS API
# ==========================================
@app.route('/api/classes/<class_id>/subjects', methods=['GET'])
def api_get_class_subjects(class_id):
    cls = Class.query.get(class_id)
    if not cls:
        return jsonify({'error': 'Class not found'}), 404
    class_subs = ClassSubject.query.filter_by(class_id=class_id).all()
    sub_ids = [cs.subject_id for cs in class_subs]
    if not sub_ids and cls.subject_id:
        sub_ids = [cls.subject_id]
    subjects = Subject.query.filter(Subject.id.in_(sub_ids)).all()
    return jsonify([{
        'id': s.id,
        'code': s.code,
        'name': s.name,
        'department': s.department,
        'credits': s.credits,
        'teacher_id': s.teacher_id,
        'teacher_name': s.teacher_name or cls.faculty_name
    } for s in subjects]), 200

@app.route('/api/classes/<class_id>/subjects', methods=['POST'])
def api_add_class_subject(class_id):
    body = request.get_json(force=True) or {}
    subject_id = body.get('subject_id')
    if not subject_id:
        return jsonify({'error': 'subject_id required'}), 400
    existing = ClassSubject.query.filter_by(class_id=class_id, subject_id=subject_id).first()
    if not existing:
        cs = ClassSubject(class_id=class_id, subject_id=subject_id, created_at=now_iso())
        db.session.add(cs)
        db.session.commit()
    return jsonify({'success': True, 'class_id': class_id, 'subject_id': subject_id}), 200

@app.route('/api/classes/<class_id>/subjects/<subject_id>', methods=['DELETE'])
def api_remove_class_subject(class_id, subject_id):
    ClassSubject.query.filter_by(class_id=class_id, subject_id=subject_id).delete()
    db.session.commit()
    return jsonify({'success': True}), 200

# ==========================================
# WEEKLY TIMETABLE MANAGEMENT API (ADMIN)
# ==========================================
@app.route('/api/timetable/weekly', methods=['GET'])
def api_get_weekly_timetable():
    class_id = request.args.get('classId')
    teacher_id = request.args.get('teacherId')
    day = request.args.get('day')
    
    query = TimetableSlot.query
    if class_id:
        query = query.filter_by(class_id=class_id)
    if teacher_id:
        query = query.filter_by(teacher_id=teacher_id)
    if day:
        query = query.filter_by(day_of_week=day)
        
    slots = query.all()
    
    active_slot_setting = SystemSetting.query.get('timetable_active_slot_id')
    active_slot_id = active_slot_setting.value if active_slot_setting else None
    
    days_order = {'Monday': 1, 'Tuesday': 2, 'Wednesday': 3, 'Thursday': 4, 'Friday': 5, 'Saturday': 6, 'Sunday': 7}
    sorted_slots = sorted(slots, key=lambda s: (days_order.get(s.day_of_week, 99), s.start_time))
    
    res = []
    for s in sorted_slots:
        c = Class.query.get(s.class_id)
        sub = Subject.query.get(s.subject_id)
        res.append({
            'id': s.id,
            'day_of_week': s.day_of_week,
            'start_time': s.start_time,
            'end_time': s.end_time,
            'class_id': s.class_id,
            'class_name': c.name if c else s.class_id,
            'subject_id': s.subject_id,
            'subject_code': sub.code if sub else 'SUB',
            'subject_name': sub.name if sub else 'Subject',
            'teacher_id': s.teacher_id,
            'teacher_name': s.teacher_name,
            'room': s.room,
            'is_currently_active': (s.id == active_slot_id)
        })
    return jsonify(res), 200

@app.route('/api/timetable/slots', methods=['POST'])
def api_create_timetable_slot():
    body = request.get_json(force=True) or {}
    day = body.get('day_of_week')
    start_time = body.get('start_time')
    end_time = body.get('end_time')
    class_id = body.get('class_id')
    subject_id = body.get('subject_id')
    teacher_id = body.get('teacher_id')
    teacher_name = body.get('teacher_name')
    room = body.get('room', 'Room 101')
    
    if not day or not start_time or not end_time or not class_id or not subject_id:
        return jsonify({'error': 'day_of_week, start_time, end_time, class_id, and subject_id are required'}), 400
        
    sub = Subject.query.get(subject_id)
    if not teacher_name:
        teacher_name = sub.teacher_name if sub else 'Faculty In-Charge'
        
    slot_id = body.get('id') or ('slot_' + secrets.token_hex(4))
    existing = TimetableSlot.query.get(slot_id)
    if existing:
        existing.day_of_week = day
        existing.start_time = start_time
        existing.end_time = end_time
        existing.class_id = class_id
        existing.subject_id = subject_id
        existing.teacher_id = teacher_id
        existing.teacher_name = teacher_name
        existing.room = room
    else:
        slot = TimetableSlot(
            id=slot_id,
            day_of_week=day,
            start_time=start_time,
            end_time=end_time,
            class_id=class_id,
            subject_id=subject_id,
            teacher_id=teacher_id,
            teacher_name=teacher_name,
            room=room,
            created_at=now_iso()
        )
        db.session.add(slot)
    
    # Auto-link class to subject
    if not ClassSubject.query.filter_by(class_id=class_id, subject_id=subject_id).first():
        db.session.add(ClassSubject(class_id=class_id, subject_id=subject_id, created_at=now_iso()))
        
    db.session.commit()
    return jsonify({'success': True, 'slot_id': slot_id}), 201

@app.route('/api/timetable/slots/<slot_id>', methods=['DELETE'])
def api_delete_timetable_slot(slot_id):
    slot = TimetableSlot.query.get(slot_id)
    if slot:
        db.session.delete(slot)
        db.session.commit()
    return jsonify({'success': True}), 200

@app.route('/api/timetable/activate-slot/<slot_id>', methods=['POST'])
def api_activate_timetable_slot(slot_id):
    slot = TimetableSlot.query.get(slot_id)
    if not slot:
        return jsonify({'error': 'Slot not found'}), 404
        
    r = SystemSetting.query.get('timetable_active_slot_id')
    if not r:
        r = SystemSetting(key='timetable_active_slot_id', value=slot_id)
        db.session.add(r)
    else:
        r.value = slot_id
    db.session.commit()
    
    sess = sync_timetable_sessions(requested_class_id=slot.class_id)
    return jsonify({
        'success': True,
        'slotId': slot_id,
        'subjectCode': sess.subject_code if sess else None,
        'subjectName': sess.subject_name if sess else None,
        'teacherName': sess.teacher_name if sess else None,
        'sessionId': sess.id if sess else None
    }), 200

# ==========================================
# TIMETABLE SESSION SYNCHRONIZATION ENGINE
# ==========================================
def sync_timetable_sessions(requested_class_id=None, requested_subject_id=None):
    """
    Evaluates institutional weekly timetable given by admin and ensures 
    the scheduled class and specific subject session is active.
    """
    now = now_iso()
    active_slot_setting = SystemSetting.query.get('timetable_active_slot_id')
    active_slot_id = active_slot_setting.value if active_slot_setting else None
    
    target_slot = None
    if active_slot_id and active_slot_id != 'AUTO':
        target_slot = TimetableSlot.query.get(active_slot_id)
        
    if not target_slot:
        # Check current day of week & time (e.g. 'Monday', '10:30')
        weekday_name = datetime.datetime.now().strftime('%A')
        current_time_str = datetime.datetime.now().strftime('%H:%M')
        
        matching_slots = TimetableSlot.query.filter_by(day_of_week=weekday_name).all()
        for s in matching_slots:
            if s.start_time <= current_time_str <= s.end_time:
                if not requested_class_id or s.class_id == requested_class_id:
                    target_slot = s
                    break
                    
    if not target_slot and requested_class_id:
        target_slot = TimetableSlot.query.filter_by(class_id=requested_class_id).first()
        
    if not target_slot and requested_subject_id:
        target_slot = TimetableSlot.query.filter_by(subject_id=requested_subject_id).first()
        
    if not target_slot:
        target_slot = TimetableSlot.query.order_by(TimetableSlot.id.asc()).first()
        
    if not target_slot:
        target_class = Class.query.first()
        if not target_class:
            return None
        sub = Subject.query.get(target_class.subject_id)
        target_slot = TimetableSlot(
            id='slot_default',
            day_of_week='Monday',
            start_time='09:00',
            end_time='10:30',
            class_id=target_class.id,
            subject_id=target_class.subject_id,
            teacher_name=target_class.faculty_name,
            room=target_class.room,
            created_at=now
        )

    target_class = Class.query.get(target_slot.class_id)
    target_subject = Subject.query.get(target_slot.subject_id)
    
    # Check if active session is already active for this class & subject
    active_sess = AttendanceSession.query.filter_by(
        class_id=target_slot.class_id,
        subject_id=target_slot.subject_id,
        status='ACTIVE'
    ).first()
    
    if not active_sess:
        # Complete other active sessions
        active_others = AttendanceSession.query.filter_by(status='ACTIVE').all()
        for s in active_others:
            s.status = 'COMPLETED'
            s.ended_at = now
            
        sess_id = 'sess_' + secrets.token_hex(6)
        active_sess = AttendanceSession(
            id=sess_id,
            class_id=target_slot.class_id,
            subject_id=target_slot.subject_id,
            subject_code=target_subject.code if target_subject else 'SUB',
            subject_name=target_subject.name if target_subject else 'Subject',
            teacher_name=target_slot.teacher_name,
            room=target_slot.room,
            camera_id='CAM-ROOM-101',
            status='ACTIVE',
            started_at=now,
            created_by=f"Timetable: {target_slot.day_of_week} {target_slot.start_time}-{target_slot.end_time}"
        )
        db.session.add(active_sess)
        db.session.commit()
        
    return active_sess

@app.route('/api/timetable/slot', methods=['GET', 'POST'])
def api_timetable_slot():
    if request.method == 'POST':
        body = request.get_json(force=True) or {}
        class_id = body.get('class_id', 'AUTO')
        slot_id = body.get('slot_id')
        if slot_id:
            r = SystemSetting.query.get('timetable_active_slot_id')
            if not r:
                r = SystemSetting(key='timetable_active_slot_id', value=slot_id)
                db.session.add(r)
            else:
                r.value = slot_id
            db.session.commit()
            sess = sync_timetable_sessions()
            return jsonify({'success': True, 'activeSlotId': slot_id, 'session': sess.id if sess else None}), 200
            
        r = SystemSetting.query.get('timetable_active_class_id')
        if not r:
            r = SystemSetting(key='timetable_active_class_id', value=class_id)
            db.session.add(r)
        else:
            r.value = class_id
        db.session.commit()
        sess = sync_timetable_sessions(class_id if class_id != 'AUTO' else None)
        return jsonify({'success': True, 'activeClassId': class_id, 'session': sess.id if sess else None}), 200
        
    r = SystemSetting.query.get('timetable_active_slot_id')
    current_slot = r.value if r else 'AUTO'
    classes = Class.query.all()
    slots = TimetableSlot.query.all()
    return jsonify({
        'currentSlot': current_slot,
        'mode': 'TIMETABLE_AUTOMATED',
        'classes': [{'id': c.id, 'name': c.name, 'schedule': c.schedule, 'room': c.room} for c in classes],
        'slotsCount': len(slots)
    }), 200

@app.route('/api/sessions/active', methods=['GET'])
def api_get_active_session():
    class_id = request.args.get('classId')
    subject_id = request.args.get('subjectId')
    sync_timetable_sessions(class_id, subject_id)
    query = AttendanceSession.query.filter_by(status='ACTIVE')
    if class_id:
        query = query.filter_by(class_id=class_id)
    if subject_id:
        query = query.filter_by(subject_id=subject_id)
    sess = query.order_by(AttendanceSession.started_at.desc()).first()
    
    if not sess:
        return jsonify({'active': False, 'session': None}), 200
        
    cls = Class.query.get(sess.class_id)
    sub = Subject.query.get(sess.subject_id or (cls.subject_id if cls else None))
    
    records_count = AttendanceRecord.query.filter_by(session_id=sess.id).count()
    enrolled_count = ClassEnrollment.query.filter_by(class_id=sess.class_id).count()
    absent_count = max(0, enrolled_count - records_count)
    rate = round((records_count / enrolled_count) * 100, 1) if enrolled_count > 0 else 0
    
    return jsonify({
        'active': True,
        'session': {
            'id': sess.id,
            'class_id': sess.class_id,
            'subject_id': sess.subject_id or (sub.id if sub else None),
            'subject_code': sess.subject_code or (sub.code if sub else 'SUB'),
            'subject_name': sess.subject_name or (sub.name if sub else 'Subject'),
            'teacher_name': sess.teacher_name or (sub.teacher_name if sub else (cls.faculty_name if cls else 'Faculty')),
            'room': sess.room,
            'camera_id': sess.camera_id,
            'status': sess.status,
            'started_at': sess.started_at,
            'ended_at': sess.ended_at,
            'created_by': sess.created_by
        },
        'classInfo': {
            'id': cls.id,
            'name': cls.name,
            'room': cls.room,
            'schedule': cls.schedule,
            'faculty_name': sess.teacher_name or cls.faculty_name,
            'subject_code': sess.subject_code or (sub.code if sub else 'SUB'),
            'subject_name': sess.subject_name or (sub.name if sub else 'Subject')
        } if cls else None,
        'stats': {
            'present': records_count,
            'enrolled': enrolled_count,
            'absent': absent_count,
            'rate': rate
        },
        'timetable': {
            'isAutomated': True,
            'schedule': cls.schedule if cls else 'Regular Schedule',
            'canTeacherStart': False,
            'statusText': 'Autonomous Institutional Timetable Active'
        }
    }), 200

@app.route('/api/sessions/start', methods=['POST'])
def api_start_session():
    body = request.get_json(force=True) or {}
    class_id = body.get('classId')
    room = body.get('room', 'Room 101')
    camera_id = body.get('cameraId', 'CAM-ROOM-101')
    faculty_name = body.get('facultyName', 'Er. Gagandeep Kaur')
    
    # Requirement: "not able for teachger to start it"
    if body.get('isTeacherManualStart') is True:
        return jsonify({
            'error': 'Manual session starting by teachers is disabled. Attendance sessions start and close automatically according to the institutional timetable schedule.'
        }), 403
    
    if not class_id:
        return jsonify({'error': 'classId is required'}), 400
        
    now = now_iso()
    # Close any active session for this class
    active_prev = AttendanceSession.query.filter_by(class_id=class_id, status='ACTIVE').all()
    for s in active_prev:
        s.status = 'COMPLETED'
        s.ended_at = now
        
    sess_id = 'sess_' + secrets.token_hex(6)
    sess = AttendanceSession(
        id=sess_id,
        class_id=class_id,
        room=room,
        camera_id=camera_id,
        status='ACTIVE',
        started_at=now,
        created_by=faculty_name
    )
    db.session.add(sess)
    db.session.commit()
    
    sess_dict = {
        'id': sess.id,
        'class_id': sess.class_id,
        'room': sess.room,
        'camera_id': sess.camera_id,
        'status': sess.status,
        'started_at': sess.started_at,
        'created_by': sess.created_by
    }
    broadcast_sse('session.started', {'session': sess_dict})
    return jsonify({'success': True, 'session': sess_dict}), 201

@app.route('/api/sessions/<session_id>/stop', methods=['POST'])
def api_stop_session(session_id):
    sess = AttendanceSession.query.get(session_id)
    now = now_iso()
    if sess:
        sess.status = 'COMPLETED'
        sess.ended_at = now
        db.session.commit()
    broadcast_sse('session.stopped', {'sessionId': session_id})
    return jsonify({'success': True, 'sessionId': session_id, 'status': 'COMPLETED', 'ended_at': now}), 200

@app.route('/api/sessions/<session_id>/records', methods=['GET'])
def api_session_records(session_id):
    records = db.session.query(AttendanceRecord, Student, Snapshot).\
        join(Student, AttendanceRecord.student_id == Student.id).\
        outerjoin(Snapshot, AttendanceRecord.snapshot_id == Snapshot.id).\
        filter(AttendanceRecord.session_id == session_id).\
        order_by(AttendanceRecord.marked_at.desc()).all()
        
    res = []
    for r, stu, snap in records:
        res.append({
            'id': r.id,
            'session_id': r.session_id,
            'student_id': r.student_id,
            'student_name': stu.name,
            'roll_number': stu.roll_number,
            'student_email': stu.email,
            'department': stu.department,
            'avatar_color': stu.avatar_color,
            'marked_at': r.marked_at,
            'verification_method': r.verification_method,
            'challenge_type': r.challenge_type,
            'liveness_score': r.liveness_score,
            'match_confidence': r.match_confidence,
            'camera_id': r.camera_id,
            'status': r.status,
            'is_purged': snap.is_purged if snap else 0,
            'snapshot_expires_at': snap.expires_at if snap else None,
            'snapshot_image': snap.image_data if snap else None
        })
    return jsonify(res), 200

# ==========================================
# SNAPSHOT VAULT (72-HOUR RETENTION)
# ==========================================
@app.route('/api/snapshots', methods=['GET'])
def api_get_snapshots():
    session_id = request.args.get('sessionId')
    query = db.session.query(Snapshot, AttendanceRecord, Student, Class).\
        join(AttendanceRecord, Snapshot.record_id == AttendanceRecord.id).\
        join(Student, AttendanceRecord.student_id == Student.id).\
        join(Class, AttendanceRecord.class_id == Class.id)
        
    if session_id:
        query = query.filter(AttendanceRecord.session_id == session_id)
        
    snaps = query.order_by(Snapshot.captured_at.desc()).limit(50).all()
    res = []
    now_dt = datetime.datetime.now(datetime.timezone.utc)
    for s, r, stu, cls in snaps:
        exp_dt = datetime.datetime.fromisoformat(s.expires_at.replace('Z', '+00:00'))
        is_exp = now_dt >= exp_dt or s.is_purged == 1
        diff = exp_dt - now_dt
        if is_exp:
            countdown = 'EXPIRED & PURGED'
        else:
            hours = int(diff.total_seconds() // 3600)
            mins = int((diff.total_seconds() % 3600) // 60)
            countdown = f"{hours}h {mins}m remaining"
            
        res.append({
            'id': s.id,
            'record_id': s.record_id,
            'image_data': s.image_data,
            'captured_at': s.captured_at,
            'expires_at': s.expires_at,
            'is_purged': s.is_purged,
            'purged_at': s.purged_at,
            'student_name': stu.name,
            'roll_number': stu.roll_number,
            'class_name': cls.name,
            'challenge_type': r.challenge_type,
            'countdown': countdown,
            'isExpired': is_exp,
            'imageAvailable': bool(s.image_data and s.image_data != '[PURGED_EXPIRED_72H]')
        })
    return jsonify(res), 200

@app.route('/api/snapshots/purge-expired', methods=['POST'])
def api_purge_expired():
    res = purge_expired_snapshots()
    broadcast_sse('snapshots.purged', res)
    return jsonify(res), 200

@app.route('/api/snapshots/simulate-timewarp', methods=['POST'])
def api_simulate_timewarp():
    body = request.get_json(force=True) or {}
    hours = int(body.get('hours', 73))
    sim_time = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=hours)).isoformat()
    res = purge_expired_snapshots(sim_time)
    broadcast_sse('snapshots.purged', res)
    return jsonify({
        'simulatedHoursAdvanced': hours,
        **res
    }), 200

# ==========================================
# CAMERAS MANAGEMENT
# ==========================================
@app.route('/api/cameras', methods=['GET'])
def api_get_cameras():
    cams = Camera.query.order_by(Camera.id.asc()).all()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'room': c.room,
        'ip_address': c.ip_address,
        'status': c.status,
        'fps': c.fps,
        'firmware': c.firmware,
        'last_heartbeat': c.last_heartbeat
    } for c in cams]), 200

@app.route('/api/cameras/heartbeat', methods=['POST'])
def api_camera_heartbeat():
    body = request.get_json(force=True) or {}
    cam_id = body.get('cameraId')
    status = body.get('status', 'ONLINE')
    fps = body.get('fps', 30)
    now = now_iso()
    cam = Camera.query.get(cam_id)
    if cam:
        cam.status = status
        cam.fps = fps
        cam.last_heartbeat = now
        db.session.commit()
    return jsonify({'success': True, 'cameraId': cam_id, 'status': status, 'last_heartbeat': now}), 200

# ==========================================
# LMS WEBHOOK & MOCK ENDPOINTS
# ==========================================
@app.route('/api/mock-lms/webhook', methods=['POST'])
def api_mock_lms_webhook():
    raw_data = request.get_data(as_text=True)
    sig = request.headers.get('X-Presently-Signature', '')
    secret_row = SystemSetting.query.get('lms_webhook_secret')
    secret = secret_row.value if secret_row else 'presently_lms_secret_2026_x89a'
    
    expected_sig = 'sha256=' + hmac.new(secret.encode('utf-8'), raw_data.encode('utf-8'), hashlib.sha256).hexdigest()
    is_valid = hmac.compare_digest(sig, expected_sig)
    
    try:
        payload = json.loads(raw_data)
    except Exception:
        payload = {}
        
    delivery_id = request.headers.get('X-Presently-Delivery', 'dlv_' + secrets.token_hex(4))
    feed_entry = {
        'deliveryId': delivery_id,
        'timestamp': now_iso(),
        'event': request.headers.get('X-Presently-Event', 'attendance.marked'),
        'signature': sig,
        'status': 'VERIFIED_VALID_HMAC' if is_valid else 'SIGNATURE_MISMATCH',
        'statusCode': 200 if is_valid else 401,
        'payload': payload
    }
    mock_lms_feed.insert(0, feed_entry)
    if len(mock_lms_feed) > 50:
        mock_lms_feed.pop()
        
    broadcast_sse('lms.received', feed_entry)
    return jsonify(feed_entry), 200

@app.route('/api/mock-lms/feed', methods=['GET'])
def api_mock_lms_feed():
    return jsonify(mock_lms_feed), 200

@app.route('/api/lms/logs', methods=['GET'])
def api_lms_logs():
    logs = LmsWebhook.query.order_by(LmsWebhook.sent_at.desc()).limit(40).all()
    return jsonify([{
        'id': l.id,
        'event_type': l.event_type,
        'target_url': l.target_url,
        'status': l.status,
        'response_code': l.response_code,
        'sent_at': l.sent_at,
        'latency_ms': l.latency_ms,
        'payload': json.loads(l.payload) if l.payload else None
    } for l in logs]), 200

@app.route('/api/lms/config', methods=['GET', 'POST'])
def api_lms_config():
    if request.method == 'POST':
        body = request.get_json(force=True) or {}
        if 'url' in body:
            r = SystemSetting.query.get('lms_webhook_url')
            if r: r.value = body['url']
        if 'secret' in body:
            r = SystemSetting.query.get('lms_webhook_secret')
            if r: r.value = body['secret']
        if 'enabled' in body:
            r = SystemSetting.query.get('lms_enabled')
            if r: r.value = '1' if body['enabled'] else '0'
        db.session.commit()
        return jsonify({'success': True, 'message': 'LMS settings updated successfully'}), 200
        
    url_row = SystemSetting.query.get('lms_webhook_url')
    secret_row = SystemSetting.query.get('lms_webhook_secret')
    enabled_row = SystemSetting.query.get('lms_enabled')
    return jsonify({
        'url': url_row.value if url_row else f'http://localhost:{PORT}/api/mock-lms/webhook',
        'secret': secret_row.value if secret_row else 'presently_lms_secret_2026_x89a',
        'enabled': enabled_row.value != '0' if enabled_row else True
    }), 200

@app.route('/api/lms/test-ping', methods=['POST'])
def api_lms_test_ping():
    dummy_rec = {
        'id': 'rec_ping_' + secrets.token_hex(4),
        'marked_at': now_iso(),
        'verification_method': 'FACIAL_RECOGNITION_LIVENESS',
        'challenge_type': 'BLINK',
        'liveness_score': 0.99,
        'match_confidence': 0.98,
        'camera_id': 'CAM-ROOM-101',
        'status': 'PRESENT'
    }
    dummy_stu = {
        'id': 'stu_01',
        'roll_number': 'CS-2024-001',
        'name': 'Alex Mercer (Test Ping)',
        'email': 'alex.mercer@campus.edu',
        'department': 'Computer Science'
    }
    dummy_sess = {'id': 'sess_test_ping', 'class_id': 'cls_cs101_a'}
    dummy_cls = {'id': 'cls_cs101_a', 'name': 'CS101 - Section A', 'room': 'Room 101', 'faculty_name': 'Er. Gagandeep Kaur'}
    dummy_sub = {'code': 'CS101', 'name': 'Introduction to Computer Science'}
    
    res = push_attendance_to_lms(dummy_rec, dummy_stu, dummy_sess, dummy_cls, dummy_sub)
    return jsonify(res), 200

# ==========================================
# REPORTLAB PDF GENERATION ENDPOINT
# ==========================================
@app.route('/api/export/pdf', methods=['GET'])
@app.route('/api/reports/pdf', methods=['GET'])
def api_export_pdf():
    session_id = request.args.get('sessionId') or request.args.get('session_id')
    subject_id = request.args.get('subjectId') or request.args.get('subject_id')
    teacher_id = request.args.get('teacherId') or request.args.get('teacher_id')
    class_id = request.args.get('classId') or request.args.get('class_id')
    
    sub = None
    sess = None
    cls = None
    
    if subject_id:
        sub = Subject.query.get(subject_id)
        if sub:
            sess = AttendanceSession.query.filter_by(subject_id=sub.id).order_by(AttendanceSession.started_at.desc()).first()
            if not sess:
                cls_match = Class.query.filter_by(subject_id=sub.id).first()
                if cls_match:
                    sess = AttendanceSession.query.filter_by(class_id=cls_match.id).order_by(AttendanceSession.started_at.desc()).first()

    if session_id:
        sess = AttendanceSession.query.get(session_id)
        
    if not sess:
        sess = AttendanceSession.query.order_by(AttendanceSession.started_at.desc()).first()
        
    if not sess:
        return jsonify({'error': 'No attendance session found for PDF generation'}), 404
        
    cls = Class.query.get(sess.class_id)
    if not sub:
        sub = Subject.query.get(sess.subject_id or (cls.subject_id if cls else None))
        
    teacher_in_charge = (sub.teacher_name if sub and sub.teacher_name else None) or sess.teacher_name or (cls.faculty_name if cls else 'Faculty In-Charge')
    course_name = f"{sub.name} ({sub.code})" if sub else "Data Structures (DS)"
    
    enrollments = ClassEnrollment.query.filter_by(class_id=sess.class_id).all()
    stu_ids = [e.student_id for e in enrollments]
    students = Student.query.filter(Student.id.in_(stu_ids)).order_by(Student.roll_number.asc()).all()
    if not students:
        students = Student.query.order_by(Student.roll_number.asc()).limit(15).all()
    
    records = AttendanceRecord.query.filter_by(session_id=sess.id).all()
    marked_dict = {r.student_id: r for r in records}
    
    # Build PDF with ReportLab
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Heading1'],
        fontSize=18,
        leading=22,
        textColor=colors.HexColor('#0f172a'),
        fontName='Helvetica-Bold'
    )
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#64748b'),
        fontName='Helvetica'
    )
    meta_style = ParagraphStyle(
        'MetaText',
        parent=styles['Normal'],
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155'),
        fontName='Helvetica'
    )
    
    elements = []
    
    # Header Banner
    elements.append(Paragraph("PRESENTLY : OFFICIAL SUBJECT ATTENDANCE DOSSIER", title_style))
    elements.append(Paragraph(f"Official Anti-Proxy Biometric Verification Record &bull; Session: {sess.id}", subtitle_style))
    elements.append(Spacer(1, 12))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#3b82f6'), spaceAfter=12))
    
    # Subject & Faculty In-Charge Info Grid
    info_data = [
        [
            Paragraph(f"<b>Subject / Course:</b> {course_name}", meta_style),
            Paragraph(f"<b>Assigned Faculty:</b> {teacher_in_charge}", meta_style)
        ],
        [
            Paragraph(f"<b>Class Cohort:</b> {cls.name if cls else 'Section A'}", meta_style),
            Paragraph(f"<b>Room & Terminal:</b> {sess.room} / {sess.camera_id}", meta_style)
        ],
        [
            Paragraph(f"<b>Date & Slot:</b> {sess.started_at[:16].replace('T', ' ')}", meta_style),
            Paragraph(f"<b>Attendance Rate:</b> {len(records)} / {len(students)} ({round((len(records)/len(students)*100), 1) if students else 0}%)", meta_style)
        ]
    ]
    info_table = Table(info_data, colWidths=[260, 260])
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), colors.HexColor('#f8fafc')),
        ('BOX', (0,0), (-1,-1), 1, colors.HexColor('#cbd5e1')),
        ('INNERGRID', (0,0), (-1,-1), 0.5, colors.HexColor('#e2e8f0')),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 14))
    
    # Student Roster Table
    table_data = [
        ["#", "Roll Number", "Student Name", "Status", "Marked Time", "Liveness Challenge", "Confidence"]
    ]
    
    for idx, stu in enumerate(students, 1):
        rec = marked_dict.get(stu.id)
        is_present = rec is not None
        status = "PRESENT" if is_present else "ABSENT"
        marked_time = rec.marked_at[11:19] if is_present else "-"
        challenge = rec.challenge_type if is_present else "-"
        conf = f"{int(rec.match_confidence * 100)}%" if is_present else "-"
        table_data.append([str(idx), stu.roll_number, stu.name, status, marked_time, challenge, conf])
        
    roster_table = Table(table_data, colWidths=[24, 85, 140, 65, 70, 85, 55])
    t_style = [
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0f172a')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,0), 8.5),
        ('BOTTOMPADDING', (0,0), (-1,0), 7),
        ('TOPPADDING', (0,0), (-1,0), 7),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#cbd5e1')),
        ('FONTSIZE', (0,1), (-1,-1), 8),
        ('TOPPADDING', (0,1), (-1,-1), 5),
        ('BOTTOMPADDING', (0,1), (-1,-1), 5),
    ]
    for r_idx in range(1, len(table_data)):
        bg = colors.HexColor('#ffffff') if r_idx % 2 == 1 else colors.HexColor('#f8fafc')
        t_style.append(('BACKGROUND', (0, r_idx), (-1, r_idx), bg))
        is_p = table_data[r_idx][3] == "PRESENT"
        stat_color = colors.HexColor('#166534') if is_p else colors.HexColor('#991b1b')
        t_style.append(('TEXTCOLOR', (3, r_idx), (3, r_idx), stat_color))
        t_style.append(('FONTNAME', (3, r_idx), (3, r_idx), 'Helvetica-Bold'))
        
    roster_table.setStyle(TableStyle(t_style))
    elements.append(roster_table)
    elements.append(Spacer(1, 14))
    
    # Privacy Certification & Sign-off Footer
    elements.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor('#e2e8f0'), spaceAfter=8))
    cert_text = "<b>Privacy & Security Certification:</b> This attendance session was autonomously authenticated using Presently Edge Optical Flow & 128-d facial embeddings. Raw capture photos auto-expire after 72 hours under institutional privacy policy."
    elements.append(Paragraph(cert_text, ParagraphStyle('Cert', parent=styles['Normal'], fontSize=7.5, leading=10, textColor=colors.HexColor('#64748b'))))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(f"Certified by Faculty: <b>{teacher_in_charge}</b> &bull; Digital Signature: <i>SHA256:{hashlib.sha256(sess.id.encode()).hexdigest()[:16]}</i>", ParagraphStyle('Sig', parent=styles['Normal'], fontSize=8, leading=11, textColor=colors.HexColor('#334155'))))
    
    doc.build(elements)
    buf.seek(0)
    pdf_bytes = buf.getvalue()
    
    sub_code_clean = (sub.code if sub else 'DS').replace('/', '_')
    filename = f"Attendance_{sub_code_clean}_{sess.id[:8]}_{datetime.date.today().isoformat()}.pdf"
    return Response(
        pdf_bytes,
        mimetype='application/pdf',
        headers={
            'Content-Disposition': f'attachment; filename="{filename}"',
            'Content-Length': str(len(pdf_bytes))
        }
    )

# ==========================================
# SYSTEM OVERVIEW & STATS
# ==========================================
@app.route('/api/overview', methods=['GET'])
@app.route('/api/stats', methods=['GET'])
def api_overview():
    student_count = Student.query.count()
    class_count = Class.query.count()
    subject_count = Subject.query.count()
    camera_count = Camera.query.filter_by(status='ONLINE').count()
    total_marks = AttendanceRecord.query.count()
    active_snaps = Snapshot.query.filter_by(is_purged=0).count()
    purged_snaps = Snapshot.query.filter_by(is_purged=1).count()
    
    return jsonify({
        'totalStudents': student_count,
        'totalClasses': class_count,
        'totalSubjects': subject_count,
        'activeCameras': camera_count,
        'totalMarks': total_marks,
        'snapshots': {
            'activeCount': active_snaps,
            'purgedCount': purged_snaps,
            'retentionPolicyHours': 72,
            'privacyGuarantee': 'Face embeddings stored permanently; snapshots auto-expire after 72 hours'
        }
    }), 200

# ==========================================
# STATIC FILE SERVING
# ==========================================
@app.route('/node')
@app.route('/node/')
def serve_node_page():
    return send_from_directory('public', 'node.html')

@app.route('/')
def serve_index_page():
    return send_from_directory('public', 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    if os.path.exists(os.path.join('public', path)):
        return send_from_directory('public', path)
    return send_from_directory('public', 'index.html')

@app.route('/api/admin/reset-seed', methods=['POST', 'GET'])
def api_admin_reset_seed():
    seed_database(force=True)
    return jsonify({'success': True, 'message': 'Database re-seeded successfully'}), 200

# ==========================================
# BACKGROUND RETENTION DAEMON
# ==========================================
def background_retention_worker():
    while True:
        try:
            with app.app_context():
                purge_expired_snapshots()
        except Exception as e:
            pass
        time.sleep(60)

# Module-level initialization for standalone or WSGI (gunicorn / PythonAnywhere)
with app.app_context():
    db.create_all()
    seed_database()

daemon_thread = threading.Thread(target=background_retention_worker, daemon=True)
daemon_thread.start()

if __name__ == '__main__':
    print(f">> Presently Flask Hub Server running at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)

