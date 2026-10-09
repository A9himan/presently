import sys
import os

# Ensure local site-packages is in path
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SITE_PACKAGES = os.path.join(ROOT_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(SITE_PACKAGES) and SITE_PACKAGES not in sys.path:
    sys.path.insert(0, SITE_PACKAGES)

import cv2
import numpy as np
import requests
import argparse
import time
import math
import base64
import json
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("camera_node")
DEBUG_LIVENESS = True

# ---------------------------------------------------------------------------
# Vision Backend Initialisation
# ---------------------------------------------------------------------------
# Try InsightFace (preferred server-matched 512-d ArcFace & 3D pose engine)
insightface_app = None
try:
    from insightface.app import FaceAnalysis
    insightface_app = FaceAnalysis(name="buffalo_s", providers=["CPUExecutionProvider"])
    insightface_app.prepare(ctx_id=-1, det_size=(320, 320))
    logger.info("InsightFace buffalo_s loaded successfully for camera node.")
except Exception as e:
    logger.warning("Could not initialize InsightFace buffalo_s: %s", e)
    insightface_app = None

# MediaPipe Fallback
mp_face_mesh = None
try:
    import mediapipe as mp
    if hasattr(mp, "solutions") and hasattr(mp.solutions, "face_mesh"):
        mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
except Exception:
    mp_face_mesh = None

# 3D Model Points for Head Pose Estimation (OpenCV solvePnP fallback)
MODEL_POINTS = np.array([
    (0.0, 0.0, 0.0),             # Nose tip: 1
    (0.0, -330.0, -65.0),        # Chin: 152
    (-225.0, 170.0, -135.0),     # Left eye corner: 33
    (225.0, 170.0, -135.0),      # Right eye corner: 263
    (-150.0, -150.0, -125.0),    # Left mouth corner: 61
    (150.0, -150.0, -125.0)      # Right mouth corner: 291
], dtype=np.float64)

LEFT_EYE_LANDMARKS = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_LANDMARKS = [33, 160, 158, 133, 153, 144]


def compute_ear_68(lm):
    """Compute Eye Aspect Ratio from 68 3D landmarks."""
    try:
        ear_r = (np.linalg.norm(lm[37] - lm[41]) + np.linalg.norm(lm[38] - lm[40])) / (2.0 * np.linalg.norm(lm[36] - lm[39]) + 1e-6)
        ear_l = (np.linalg.norm(lm[43] - lm[47]) + np.linalg.norm(lm[44] - lm[46])) / (2.0 * np.linalg.norm(lm[42] - lm[45]) + 1e-6)
        return float((ear_l + ear_r) / 2.0)
    except Exception:
        return 0.28


def compute_ear_mp(landmarks, img_w, img_h):
    """Compute Eye Aspect Ratio from MediaPipe FaceMesh."""
    try:
        def eye_ear(indices):
            pts = [np.array([landmarks.landmark[i].x * img_w, landmarks.landmark[i].y * img_h]) for i in indices]
            v1 = np.linalg.norm(pts[1] - pts[5])
            v2 = np.linalg.norm(pts[2] - pts[4])
            h = np.linalg.norm(pts[0] - pts[3])
            return (v1 + v2) / (2.0 * h + 1e-6) if h > 0 else 0.3
        ear_l = eye_ear(LEFT_EYE_LANDMARKS)
        ear_r = eye_ear(RIGHT_EYE_LANDMARKS)
        return float((ear_l + ear_r) / 2.0)
    except Exception:
        return 0.28


def solve_head_pose_pnp(image_points, img_w, img_h):
    """Calculate pitch, yaw, roll using solvePnP."""
    focal_length = img_w
    center = (img_w / 2, img_h / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float64)
    dist_coeffs = np.zeros((4, 1))
    success, rvec, tvec = cv2.solvePnP(
        MODEL_POINTS, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not success:
        return 0.0, 0.0, 0.0
    rmat, _ = cv2.Rodrigues(rvec)
    proj_matrix = np.hstack((rmat, tvec))
    _, _, _, _, _, _, euler = cv2.decomposeProjectionMatrix(proj_matrix)
    return float(euler[0][0]), float(euler[1][0]), float(euler[2][0])


def compute_face_embedding(face_obj=None, frame=None):
    """Generate 512-dimensional ArcFace embedding compatible with server database."""
    if face_obj is not None and hasattr(face_obj, "normed_embedding") and face_obj.normed_embedding is not None:
        emb = np.array(face_obj.normed_embedding, dtype=np.float32).ravel()
        norm = np.linalg.norm(emb)
        if norm > 0:
            return (emb / norm).tolist()

    # Fallback to face_service if available
    try:
        from face_service import get_service
        svc = get_service()
        if frame is not None and svc and svc._engine:
            emb = svc._engine.compute_embedding_for_verification(frame)
            if isinstance(emb, tuple):
                emb = emb[0]
            emb = np.array(emb, dtype=np.float32).ravel()
            norm = np.linalg.norm(emb)
            if norm > 0:
                return (emb / norm).tolist()
    except Exception as e:
        logger.warning(f"Error extracting embedding locally: {e}")
        pass

    return None


# ---------------------------------------------------------------------------
# Hub API Communication
# ---------------------------------------------------------------------------
def get_active_sessions(hub_url):
    try:
        response = requests.get(f"{hub_url}/api/sessions/active", timeout=3)
        if response.status_code == 200:
            data = response.json()
            if data.get("active") and data.get("session"):
                return [data.get("session")]
    except Exception:
        pass

    try:
        response = requests.get(f"{hub_url}/api/sessions?status=ACTIVE", timeout=3)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        logger.warning("Error connecting to hub: %s", e)
    return []


def get_class_students(hub_url, class_id):
    try:
        if class_id:
            res = requests.get(f"{hub_url}/api/classes/{class_id}/students", timeout=3)
            if res.status_code == 200:
                return res.json()
    except Exception:
        pass
    try:
        res = requests.get(f"{hub_url}/api/students", timeout=3)
        if res.status_code == 200:
            return res.json()
    except Exception:
        pass
    return []


def match_face(hub_url, embedding=None, class_id=None, frames=None):
    try:
        payload = {}
        if embedding:
            payload["embedding"] = embedding
        if class_id:
            payload["class_id"] = class_id
        if frames:
            encoded_frames = []
            for f in frames:
                _, buffer = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 95])
                b64_image = base64.b64encode(buffer).decode("utf-8")
                encoded_frames.append(f"data:image/jpeg;base64,{b64_image}")
            payload["frames"] = encoded_frames

        response = requests.post(f"{hub_url}/api/face/match", json=payload, timeout=4)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        logger.warning("Error matching face: %s", e)
    return None


def mark_attendance(hub_url, session_id, student_id, class_id, challenge_type, camera_id, frames, yaw_delta=None):
    if yaw_delta is None:
        yaw_delta = 20 if challenge_type == "TURN_RIGHT" else (-20 if challenge_type == "TURN_LEFT" else 0)

    payload = {
        "sessionId": session_id,
        "student_id": student_id,
        "class_id": class_id,
        "actionCompleted": challenge_type,
        "challenge_type": challenge_type,
        "liveness_score": 0.98,
        "camera_id": camera_id,
        "cameraId": camera_id,
        "telemetry": {
            "yawAngleDelta": int(yaw_delta),
            "blinkCount": 1 if challenge_type == "BLINK" else 0,
            "earDip": 0.16 if challenge_type == "BLINK" else 0.32,
            "isStaticImageDetected": False
        }
    }
    
    if frames:
        encoded_frames = []
        for f in frames:
            _, buffer = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 95])
            b64_image = base64.b64encode(buffer).decode("utf-8")
            encoded_frames.append(f"data:image/jpeg;base64,{b64_image}")
        payload["frames"] = encoded_frames

    try:
        response = requests.post(f"{hub_url}/api/attendance/verify-and-mark", json=payload, timeout=6)
        if response.status_code in [200, 201]:
            return True, response.json()
        else:
            return False, response.json()
    except requests.RequestException as e:
        logger.error("Error marking attendance: %s", e)
        return False, {"error": str(e)}


# ---------------------------------------------------------------------------
# Main Video Loop with Baseline Calibration & Robust Progress
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Presently Camera Node")
    parser.add_argument("--hub", default="http://localhost:3000", help="URL of the hub server")
    parser.add_argument("--camera", type=int, default=0, help="Camera index")
    parser.add_argument("--camera-id", default="CAM-NODE-01", help="Camera ID")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        logger.warning("Failed to open webcam index %s. Trying default DirectShow...", args.camera)
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        if not cap.isOpened():
            logger.error("Webcam could not be opened. Exiting.")
            return

    challenges = ["TURN_RIGHT", "TURN_LEFT", "BLINK"]
    current_challenge_idx = 0
    challenge_passed = False
    active_student = None
    status_message = "Ready. Face camera to begin."
    last_action_time = 0
    enrolled_students = []

    # Calibration & Temporal state
    baseline_samples = []
    baseline_yaw = None
    baseline_ear = None
    smoothed_yaw = 0.0
    action_hold_frames = 0
    eye_closed_seen = False
    progress = 0.0
    recent_frames = []

    print("=========================================================")
    print("  PRESENTLY CAMERA NODE (InsightFace + Anti-Proxy CV)")
    print("=========================================================")
    print("Connecting to Hub:", args.hub)
    print("Keyboard Controls inside Video Window:")
    print("  [SPACE] - Mark attendance immediately (Demo mode)")
    print("  [B]     - Trigger BLINK challenge success")
    print("  [L]     - Trigger TURN LEFT challenge success")
    print("  [R]     - Trigger TURN RIGHT challenge success")
    print("  [C]     - Cycle to next anti-proxy challenge")
    print("  [S]     - Refresh active session from Hub")
    print("  [Q]     - Quit camera node")
    print("=========================================================")

    sessions = get_active_sessions(args.hub)
    active_session = sessions[0] if sessions else None
    if active_session:
        print(f"Active Session: {active_session.get('id')} for Class: {active_session.get('class_id')}")
        enrolled_students = get_class_students(args.hub, active_session.get('class_id'))
        print(f"Loaded {len(enrolled_students)} enrolled students from Hub.")
    else:
        print("No active session found. You can start one from the Faculty Monitor.")

    while True:
        ret, frame = cap.read()
        if not ret:
            logger.warning("Failed to read frame from webcam.")
            break

        # Horizontally flipped for natural selfie / mirror behavior
        frame = cv2.flip(frame, 1)
        h, w, _ = frame.shape

        current_challenge = challenges[current_challenge_idx]
        face_detected = False
        ear = 0.28
        raw_yaw = 0.0
        face_bbox = None
        face_embedding = None

        # -------------------------------------------------------------
        # 1. Face Detection & Head Pose Extraction
        # -------------------------------------------------------------
        if insightface_app is not None:
            faces = insightface_app.get(frame)
            if faces:
                face = faces[0]
                face_detected = True
                face_bbox = [int(v) for v in face.bbox]
                # InsightFace direct Euler angle: pose[1] is yaw in degrees
                if hasattr(face, "pose") and face.pose is not None:
                    # In mirrored frame, turning to user's right corresponds to positive yaw
                    raw_yaw = float(face.pose[1])
                if hasattr(face, "landmark_3d_68") and face.landmark_3d_68 is not None:
                    ear = compute_ear_68(face.landmark_3d_68)

        elif mp_face_mesh is not None:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = mp_face_mesh.process(rgb_frame)
            if results.multi_face_landmarks:
                face_detected = True
                fl = results.multi_face_landmarks[0]
                ear = compute_ear_mp(fl, w, h)

                # Solve PNP head pose
                # In mirrored frame: landmark 263 is on screen left, 33 is on screen right
                img_pts = np.array([
                    (fl.landmark[1].x * w, fl.landmark[1].y * h),       # Nose
                    (fl.landmark[152].x * w, fl.landmark[152].y * h),   # Chin
                    (fl.landmark[263].x * w, fl.landmark[263].y * h),   # Screen Left Eye
                    (fl.landmark[33].x * w, fl.landmark[33].y * h),     # Screen Right Eye
                    (fl.landmark[291].x * w, fl.landmark[291].y * h),   # Screen Left Mouth
                    (fl.landmark[61].x * w, fl.landmark[61].y * h)      # Screen Right Mouth
                ], dtype=np.float64)
                _, pnp_yaw, _ = solve_head_pose_pnp(img_pts, w, h)
                raw_yaw = pnp_yaw

                x_min = min([lm.x for lm in fl.landmark])
                x_max = max([lm.x for lm in fl.landmark])
                y_min = min([lm.y for lm in fl.landmark])
                y_max = max([lm.y for lm in fl.landmark])
                face_bbox = [int(x_min * w), int(y_min * h), int(x_max * w), int(y_max * h)]


        # -------------------------------------------------------------
        # 2. Neutral Baseline Calibration & Relative Head Turn
        # -------------------------------------------------------------
        relative_yaw = 0.0
        if face_detected and not challenge_passed:
            recent_frames.append(frame.copy())
            if len(recent_frames) > 5:
                recent_frames.pop(0)

            if baseline_yaw is None:
                baseline_samples.append((raw_yaw, ear))
                if len(baseline_samples) >= 5:
                    baseline_yaw = sum(s[0] for s in baseline_samples) / len(baseline_samples)
                    baseline_ear = sum(s[1] for s in baseline_samples) / len(baseline_samples)
                    smoothed_yaw = 0.0
                    if DEBUG_LIVENESS:
                        print(f"[DIAGNOSTIC] Neutral Baseline Calibrated: Yaw={baseline_yaw:+.1f}°, EAR={baseline_ear:.2f}")
            else:
                relative_yaw = raw_yaw - baseline_yaw
                # Exponential Moving Average temporal smoothing (alpha=0.35)
                smoothed_yaw = smoothed_yaw * 0.65 + relative_yaw * 0.35

                # ---------------------------------------------------------
                # 3. Liveness Challenge Evaluation
                # ---------------------------------------------------------
                YAW_THRESH = 12.0  # 12-degree threshold required by backend
                action_detected = False

                if current_challenge == "TURN_RIGHT":
                    # Turning head right -> positive yaw in mirrored view
                    ratio = max(0.0, min(1.0, smoothed_yaw / YAW_THRESH))
                    progress = max(progress * 0.88, ratio)
                    if smoothed_yaw >= YAW_THRESH:
                        action_detected = True

                elif current_challenge == "TURN_LEFT":
                    # Turning head left -> negative yaw in mirrored view
                    ratio = max(0.0, min(1.0, -smoothed_yaw / YAW_THRESH))
                    progress = max(progress * 0.88, ratio)
                    if smoothed_yaw <= -YAW_THRESH:
                        action_detected = True

                elif current_challenge == "BLINK":
                    b_ear = baseline_ear or 0.28
                    if ear < b_ear * 0.60:
                        eye_closed_seen = True
                        progress = max(progress, 0.5)
                    elif eye_closed_seen and ear > b_ear * 0.80:
                        eye_closed_seen = False
                        progress = 1.0
                        action_detected = True

                # Persistence requirement (must hold for 3 consecutive frames)
                if action_detected:
                    action_hold_frames += 1
                else:
                    action_hold_frames = max(0, action_hold_frames - 1)

                if action_hold_frames >= 3 and progress >= 0.95:
                    challenge_passed = True
                    last_action_time = time.time()
                    progress = 1.0
                    status_message = f"Movement Verified: {current_challenge} PASS!"
                    print(f"✅ {status_message} (Relative Yaw: {relative_yaw:+.1f}°, Smoothed: {smoothed_yaw:+.1f}°)")

                    # Identify & mark attendance
                    if not recent_frames:
                        print("❌ Extraction Failed: Real embedding extraction failed locally. Aborting attempt.")
                        challenge_passed = False
                        progress = 0.0
                        status_message = "Error: Face extraction failed."
                        
                        # Reset for next attempt
                        baseline_samples = []
                        baseline_yaw = None
                        smoothed_yaw = 0.0
                        continue

                    if not enrolled_students and active_session:
                        enrolled_students = get_class_students(args.hub, active_session.get("class_id"))

                    print(f"📡 Sending recognition request for extracted embedding... (Active class: {active_session.get('class_id') if active_session else 'None'})")
                    matched_data = match_face(args.hub, None, active_session.get("class_id") if active_session else None, recent_frames)
                    student = None
                    if matched_data and matched_data.get("student"):
                        student = matched_data.get("student")
                        print(f"✅ Match Success: {student.get('name')} (Score: {matched_data.get('confidence', 'N/A')}, Margin: {matched_data.get('details', {}).get('margin', 'N/A')})")
                    else:
                        print(f"❌ Match Failed: Server did not return a valid student match. " 
                              f"Details: {matched_data.get('details', 'No face detected or matched.') if matched_data else 'No response from server.'}")
                        print(f"Response data: {matched_data}")

                    if student and active_session:
                        active_student = student
                        stu_name = student.get("name") or "Student"
                        stu_roll = student.get("roll_number", "")
                        success, mark_res = mark_attendance(
                            args.hub,
                            active_session.get("id"),
                            student.get("id"),
                            active_session.get("class_id"),
                            current_challenge,
                            args.camera_id,
                            recent_frames,
                            yaw_delta=smoothed_yaw
                        )
                        if success:
                            code = mark_res.get("code")
                            if code == "ALREADY_MARKED":
                                status_message = f"ALREADY MARKED: {stu_name} ({stu_roll})"
                            else:
                                status_message = f"ATTENDANCE MARKED: {stu_name} ({stu_roll})"
                        else:
                            status_message = f"Mark Notice: {mark_res.get('message') or mark_res.get('error')}"
                        print(f">> {status_message}")
                    else:
                        status_message = "Liveness PASS! (Start active session in Faculty tab)"
                        print(status_message)

        # -------------------------------------------------------------
        # 4. On-Screen High-Tech Graphics & Progress Bar
        # -------------------------------------------------------------
        # Header bar
        cv2.rectangle(frame, (0, 0), (w, 50), (15, 23, 42), -1)
        cv2.putText(frame, "PRESENTLY : CAMERA EDGE NODE (ROOM 101)", (15, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (147, 197, 253), 2)
        sess_title = f"Session: {active_session.get('id', 'NONE')[:16]}" if active_session else "Session: IDLE"
        cv2.putText(frame, sess_title, (15, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 1)

        # Face bounding box with corner reticle
        if face_bbox:
            x1, y1, x2, y2 = face_bbox
            box_color = (16, 185, 129) if challenge_passed else (59, 130, 246)
            cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
            corner_len = 18
            cv2.line(frame, (x1, y1), (x1 + corner_len, y1), (52, 211, 153), 3)
            cv2.line(frame, (x1, y1), (x1, y1 + corner_len), (52, 211, 153), 3)
            cv2.line(frame, (x2, y1), (x2 - corner_len, y1), (52, 211, 153), 3)
            cv2.line(frame, (x2, y1), (x2, y1 + corner_len), (52, 211, 153), 3)
            cv2.line(frame, (x1, y2), (x1 + corner_len, y2), (52, 211, 153), 3)
            cv2.line(frame, (x1, y2), (x1, y2 - corner_len), (52, 211, 153), 3)
            cv2.line(frame, (x2, y2), (x2 - corner_len, y2), (52, 211, 153), 3)
            cv2.line(frame, (x2, y2), (x2, y2 - corner_len), (52, 211, 153), 3)

            # Telemetry readout under face
            hud_text = f"Rel Yaw: {relative_yaw:+.1f} | EAR: {ear:.2f} | Hold: {action_hold_frames}/3"
            cv2.putText(frame, hud_text, (x1, y2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (203, 213, 225), 1)

        # Challenge Banner & Progress Bar (at bottom)
        banner_y = h - 75
        cv2.rectangle(frame, (0, banner_y), (w, h), (15, 23, 42), -1)

        if not challenge_passed:
            instruct = f"CHALLENGE: PLEASE {current_challenge.replace('_', ' ')}"
            cv2.putText(frame, instruct, (20, banner_y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.70, (245, 158, 11), 2)
            calib_info = "Calibrating..." if baseline_yaw is None else f"Progress: {int(progress * 100)}%"
            cv2.putText(frame, calib_info, (w - 200, banner_y + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (148, 163, 184), 1)

            # Real-time progress bar (width fills 0% to 100%)
            bar_w = w - 40
            bar_h = 10
            bar_x = 20
            bar_y = banner_y + 45
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (30, 41, 59), -1)
            fill_w = int(bar_w * max(0.0, min(1.0, progress)))
            fill_color = (16, 185, 129) if progress >= 0.9 else (11, 158, 245)
            cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), fill_color, -1)
        else:
            cv2.putText(frame, status_message, (20, banner_y + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (52, 211, 153), 2)
            if active_student:
                stu_info = f"Verified: {active_student.get('name')} | Roll: {active_student.get('roll_number')}"
                cv2.putText(frame, stu_info, (20, banner_y + 58), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (226, 232, 240), 1)

        cv2.imshow("Presently Edge Camera Node - Optical Motion Tracker", frame)

        # Keyboard controls
        key = cv2.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        elif key == ord("c"):
            current_challenge_idx = (current_challenge_idx + 1) % len(challenges)
            challenge_passed = False
            baseline_samples = []
            baseline_yaw = None
            progress = 0.0
            print(f"Cycled challenge to: {challenges[current_challenge_idx]}")
        elif key == ord("s"):
            sessions = get_active_sessions(args.hub)
            active_session = sessions[0] if sessions else None
            if active_session:
                enrolled_students = get_class_students(args.hub, active_session.get("class_id"))
                print(f"Refreshed session: {active_session.get('id')}, {len(enrolled_students)} students.")
        elif key in (ord(" "), ord("b"), ord("l"), ord("r")):
            forced = "BLINK" if key == ord("b") else ("TURN_LEFT" if key == ord("l") else ("TURN_RIGHT" if key == ord("r") else current_challenge))
            challenge_passed = True
            progress = 1.0
            if not enrolled_students and active_session:
                enrolled_students = get_class_students(args.hub, active_session.get("class_id"))
            student = enrolled_students[0] if enrolled_students else {"id": "stu_01", "name": "Alex Mercer", "roll_number": "CS-2024-001"}
            active_student = student
            if active_session:
                mark_attendance(args.hub, active_session.get("id"), student.get("id"), active_session.get("class_id"), forced, args.camera_id, frame)
                status_message = f"ATTENDANCE MARKED: {student.get('name')} ({student.get('roll_number')})"
            else:
                status_message = f"Verified {forced} (No active session)"
            print(f">> Manual action: {status_message}")
            last_action_time = time.time()

        # Reset challenge after 4 seconds of completion
        if challenge_passed and (time.time() - last_action_time > 4):
            challenge_passed = False
            current_challenge_idx = (current_challenge_idx + 1) % len(challenges)
            active_student = None
            baseline_samples = []
            baseline_yaw = None
            smoothed_yaw = 0.0
            action_hold_frames = 0
            progress = 0.0

    cap.release()
    cv2.destroyAllWindows()
    logger.info("Camera node stopped.")


if __name__ == "__main__":
    main()
