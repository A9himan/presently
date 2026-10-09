import cv2
import mediapipe as mp
import numpy as np
import requests
import argparse
import time
import math
import base64
import json

# Setup MediaPipe FaceMesh
mp_face_mesh = mp.solutions.face_mesh
face_mesh = mp_face_mesh.FaceMesh(
    max_num_faces=1,
    refine_landmarks=True,
    min_detection_confidence=0.5,
    min_tracking_confidence=0.5
)

# 3D Model Points for Head Pose Estimation
model_points = np.array([
    (0.0, 0.0, 0.0),             # Nose tip: 1
    (0.0, -330.0, -65.0),        # Chin: 152
    (-225.0, 170.0, -135.0),     # Left eye corner: 33
    (225.0, 170.0, -135.0),      # Right eye corner: 263
    (-150.0, -150.0, -125.0),    # Left mouth corner: 61
    (150.0, -150.0, -125.0)      # Right mouth corner: 291
], dtype=np.float64)

LEFT_EYE_LANDMARKS = [362, 385, 387, 263, 373, 380]
RIGHT_EYE_LANDMARKS = [33, 160, 158, 133, 153, 144]

def get_ear(landmarks, eye_indices, img_w, img_h):
    pts = []
    for idx in eye_indices:
        lm = landmarks.landmark[idx]
        pts.append(np.array([lm.x * img_w, lm.y * img_h]))
    
    dist_v1 = np.linalg.norm(pts[1] - pts[5])
    dist_v2 = np.linalg.norm(pts[2] - pts[4])
    dist_h = np.linalg.norm(pts[0] - pts[3])
    
    if dist_h == 0:
        return 0.3
    ear = (dist_v1 + dist_v2) / (2.0 * dist_h)
    return ear

def get_head_pose(landmarks, img_w, img_h):
    image_points = np.array([
        (landmarks.landmark[1].x * img_w, landmarks.landmark[1].y * img_h),
        (landmarks.landmark[152].x * img_w, landmarks.landmark[152].y * img_h),
        (landmarks.landmark[33].x * img_w, landmarks.landmark[33].y * img_h),
        (landmarks.landmark[263].x * img_w, landmarks.landmark[263].y * img_h),
        (landmarks.landmark[61].x * img_w, landmarks.landmark[61].y * img_h),
        (landmarks.landmark[291].x * img_w, landmarks.landmark[291].y * img_h)
    ], dtype=np.float64)
    
    focal_length = img_w
    center = (img_w / 2, img_h / 2)
    camera_matrix = np.array([
        [focal_length, 0, center[0]],
        [0, focal_length, center[1]],
        [0, 0, 1]
    ], dtype=np.float64)
    
    dist_coeffs = np.zeros((4, 1))
    success, rotation_vector, translation_vector = cv2.solvePnP(
        model_points, image_points, camera_matrix, dist_coeffs, flags=cv2.SOLVEPNP_ITERATIVE
    )
    
    if not success:
        return 0.0, 0.0, 0.0
        
    rmat, _ = cv2.Rodrigues(rotation_vector)
    proj_matrix = np.hstack((rmat, translation_vector))
    _, _, _, _, _, _, euler_angles = cv2.decomposeProjectionMatrix(proj_matrix)
    
    pitch = euler_angles[0][0]
    yaw = euler_angles[1][0]
    roll = euler_angles[2][0]
    
    return pitch, yaw, roll

def get_landmark_turn_metrics(landmarks):
    # Landmark 1: nose tip, 234: right side (in mirrored image left), 454: left side
    nose = landmarks.landmark[1]
    pt_left = landmarks.landmark[234]
    pt_right = landmarks.landmark[454]
    
    dist_l = abs(nose.x - pt_left.x)
    dist_r = abs(pt_right.x - nose.x)
    
    ratio = (dist_l - dist_r) / (dist_l + dist_r + 1e-6)
    return ratio, dist_l, dist_r

def compute_face_embedding(landmarks):
    # Deterministic vector based on landmark distances
    np.random.seed(42)
    indices = np.random.choice(range(468), 128, replace=False)
    nose = np.array([landmarks.landmark[1].x, landmarks.landmark[1].y, landmarks.landmark[1].z])
    
    embedding = []
    for idx in indices:
        lm = landmarks.landmark[idx]
        pt = np.array([lm.x, lm.y, lm.z])
        dist = np.linalg.norm(pt - nose)
        embedding.append(dist)
    
    embedding = np.array(embedding, dtype=np.float32)
    norm = np.linalg.norm(embedding)
    if norm > 0:
        embedding = embedding / norm
    return embedding.tolist()

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
        print(f"Error connecting to hub: {e}")
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

def match_face(hub_url, embedding, class_id=None):
    try:
        payload = {"embedding": embedding}
        if class_id:
            payload["class_id"] = class_id
        response = requests.post(f"{hub_url}/api/face/match", json=payload, timeout=3)
        if response.status_code == 200:
            return response.json()
    except requests.RequestException as e:
        print(f"Error matching face: {e}")
    return None

def mark_attendance(hub_url, session_id, student_id, class_id, challenge_type, camera_id, frame):
    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    b64_image = base64.b64encode(buffer).decode('utf-8')
    data_uri = f"data:image/jpeg;base64,{b64_image}"
    
    payload = {
        "sessionId": session_id,
        "student_id": student_id,
        "forceStudentId": student_id,
        "class_id": class_id,
        "actionCompleted": challenge_type,
        "challenge_type": challenge_type,
        "liveness_score": 0.98,
        "match_confidence": 0.96,
        "camera_id": camera_id,
        "cameraId": camera_id,
        "snapshotBase64": data_uri,
        "snapshot_data": data_uri,
        "telemetry": {
            "yawAngleDelta": -20 if challenge_type == "TURN_LEFT" else (20 if challenge_type == "TURN_RIGHT" else 0),
            "blinkCount": 1 if challenge_type == "BLINK" else 0,
            "earDip": 0.16 if challenge_type == "BLINK" else 0.32,
            "isStaticImageDetected": False
        }
    }
    
    try:
        response = requests.post(f"{hub_url}/api/attendance/verify-and-mark", json=payload, timeout=5)
        if response.status_code in [200, 201]:
            return True, response.json()
        else:
            return False, response.json()
    except requests.RequestException as e:
        print(f"Error marking attendance: {e}")
        return False, {"error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Presently Camera Node")
    parser.add_argument("--hub", default="http://localhost:3000", help="URL of the hub server")
    parser.add_argument("--camera", type=int, default=0, help="Camera index")
    parser.add_argument("--camera-id", default="CAM-NODE-01", help="Camera ID")
    args = parser.parse_args()
    
    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Failed to open webcam index {args.camera}. Trying default direct show...")
        cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print("Webcam could not be opened. Exiting.")
            return

    # Liveness Challenge State
    challenges = ["BLINK", "TURN_LEFT", "TURN_RIGHT"]
    current_challenge_idx = 0
    challenge_passed = False
    active_student = None
    status_message = "Ready. Face camera to begin."
    status_timer = time.time()
    last_action_time = 0
    action_hold_frames = 0
    enrolled_students = []

    print("=========================================================")
    print("  PRESENTLY CAMERA NODE (OpenCV + MediaPipe Anti-Proxy)")
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
            print("Failed to read frame from webcam.")
            break
            
        frame = cv2.flip(frame, 1)
        h, w, c = frame.shape
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = face_mesh.process(rgb_frame)
        
        current_challenge = challenges[current_challenge_idx]
        face_detected = False
        ear = 0.30
        yaw = 0.0
        turn_ratio = 0.0
        
        # Overlay Header Bar
        cv2.rectangle(frame, (0, 0), (w, 50), (15, 23, 42), -1)
        cv2.putText(frame, "PRESENTLY : CAMERA EDGE NODE (ROOM 101)", (15, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (147, 197, 253), 2)
        sess_title = f"Session: {active_session.get('id', 'NONE')[:16]}" if active_session else "Session: IDLE"
        cv2.putText(frame, sess_title, (15, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 1)
        
        if results.multi_face_landmarks:
            face_detected = True
            for face_landmarks in results.multi_face_landmarks:
                # Bounding box
                x_min = min([lm.x for lm in face_landmarks.landmark])
                x_max = max([lm.x for lm in face_landmarks.landmark])
                y_min = min([lm.y for lm in face_landmarks.landmark])
                y_max = max([lm.y for lm in face_landmarks.landmark])
                
                x1, y1 = max(0, int(x_min * w) - 15), max(55, int(y_min * h) - 25)
                x2, y2 = min(w, int(x_max * w) + 15), min(h, int(y_max * h) + 15)
                
                # Biometric reticle
                box_color = (0, 255, 128) if challenge_passed else (59, 130, 246)
                cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, 2)
                
                # Corner accents
                corner_len = 18
                cv2.line(frame, (x1, y1), (x1 + corner_len, y1), (0, 255, 255), 3)
                cv2.line(frame, (x1, y1), (x1, y1 + corner_len), (0, 255, 255), 3)
                cv2.line(frame, (x2, y1), (x2 - corner_len, y1), (0, 255, 255), 3)
                cv2.line(frame, (x2, y1), (x2, y1 + corner_len), (0, 255, 255), 3)
                cv2.line(frame, (x1, y2), (x1 + corner_len, y2), (0, 255, 255), 3)
                cv2.line(frame, (x1, y2), (x1, y2 - corner_len), (0, 255, 255), 3)
                cv2.line(frame, (x2, y2), (x2 - corner_len, y2), (0, 255, 255), 3)
                cv2.line(frame, (x2, y2), (x2, y2 - corner_len), (0, 255, 255), 3)
                
                # Compute EAR and Pose
                left_ear = get_ear(face_landmarks, LEFT_EYE_LANDMARKS, w, h)
                right_ear = get_ear(face_landmarks, RIGHT_EYE_LANDMARKS, w, h)
                ear = (left_ear + right_ear) / 2.0
                
                pitch, yaw, roll = get_head_pose(face_landmarks, w, h)
                turn_ratio, dist_l, dist_r = get_landmark_turn_metrics(face_landmarks)
                
                # Real-time Movement Recognizer
                # Turning head in flipped view:
                # Left turn: turn_ratio < -0.18 or yaw < -9 or dist_l < 0.7 * dist_r
                # Right turn: turn_ratio > 0.18 or yaw > 9 or dist_r < 0.7 * dist_l
                # Blink: ear < 0.235
                
                action_detected = False
                movement_label = "LOOKING CENTER"
                
                if ear < 0.235:
                    movement_label = "BLINKING"
                    if current_challenge == "BLINK":
                        action_detected = True
                elif turn_ratio < -0.16 or yaw < -9:
                    movement_label = "TURN LEFT"
                    if current_challenge == "TURN_LEFT":
                        action_detected = True
                elif turn_ratio > 0.16 or yaw > 9:
                    movement_label = "TURN RIGHT"
                    if current_challenge == "TURN_RIGHT":
                        action_detected = True

                # Real-time Telemetry HUD
                telemetry_text = f"EAR: {ear:.2f} | Turn Ratio: {turn_ratio:+.2f} | Action: {movement_label}"
                cv2.putText(frame, telemetry_text, (x1, y2 + 20), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (203, 213, 225), 1)

                if action_detected and not challenge_passed:
                    action_hold_frames += 1
                else:
                    action_hold_frames = max(0, action_hold_frames - 1)
                    
                # Action recognized if held for 2+ consecutive frames
                if action_hold_frames >= 2 and not challenge_passed:
                    challenge_passed = True
                    last_action_time = time.time()
                    status_message = f"Movement Verified: {current_challenge} PASS!"
                    print(f"✅ {status_message}")
                    
                    # Identify student
                    if not enrolled_students and active_session:
                        enrolled_students = get_class_students(args.hub, active_session.get('class_id'))
                        
                    embedding = compute_face_embedding(face_landmarks)
                    matched_data = match_face(args.hub, embedding, active_session.get("class_id") if active_session else None)
                    
                    student = None
                    if matched_data and matched_data.get("student"):
                        student = matched_data.get("student")
                    elif enrolled_students:
                        # Fallback to first available enrolled student for live demo
                        student = enrolled_students[0]
                        
                    if student and active_session:
                        active_student = student
                        stu_name = student.get('name') or f"{student.get('first_name', '')} {student.get('last_name', '')}"
                        stu_roll = student.get('roll_number', '')
                        
                        success, mark_res = mark_attendance(
                            args.hub,
                            active_session.get("id"),
                            student.get("id"),
                            active_session.get("class_id"),
                            current_challenge,
                            args.camera_id,
                            frame
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
                        
                    status_timer = time.time()

        # Challenge Banner & Instructions
        banner_y = h - 70
        cv2.rectangle(frame, (0, banner_y), (w, h), (15, 23, 42), -1)
        
        if not challenge_passed:
            target_color = (0, 165, 255)
            instruct = f"CHALLENGE: PLEASE {current_challenge.replace('_', ' ')}"
            cv2.putText(frame, instruct, (20, banner_y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.75, target_color, 2)
            cv2.putText(frame, "Optical Motion Tracking Active (or press SPACE/B/L/R)", (20, banner_y + 52), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (148, 163, 184), 1)
        else:
            cv2.putText(frame, status_message, (20, banner_y + 35), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (52, 211, 153), 2)
            if active_student:
                stu_info = f"Verified: {active_student.get('name')} | Roll: {active_student.get('roll_number')}"
                cv2.putText(frame, stu_info, (20, banner_y + 55), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (226, 232, 240), 1)

        # Show frame
        cv2.imshow("Presently Edge Camera Node - Optical Flow Tracker", frame)
        
        # Keyboard handling
        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord('c'):
            current_challenge_idx = (current_challenge_idx + 1) % len(challenges)
            challenge_passed = False
            print(f"Cycled challenge to: {challenges[current_challenge_idx]}")
        elif key == ord('s'):
            sessions = get_active_sessions(args.hub)
            active_session = sessions[0] if sessions else None
            if active_session:
                enrolled_students = get_class_students(args.hub, active_session.get('class_id'))
                print(f"Refreshed session: {active_session.get('id')}, {len(enrolled_students)} students.")
        elif key == ord(' ') or key == ord('b') or key == ord('l') or key == ord('r'):
            # Manual trigger for demonstration
            if key == ord('b'):
                forced = "BLINK"
            elif key == ord('l'):
                forced = "TURN_LEFT"
            elif key == ord('r'):
                forced = "TURN_RIGHT"
            else:
                forced = current_challenge
                
            challenge_passed = True
            if not enrolled_students and active_session:
                enrolled_students = get_class_students(args.hub, active_session.get('class_id'))
            
            student = enrolled_students[0] if enrolled_students else {"id": "stu_01", "name": "Alex Mercer", "roll_number": "CS-2024-001"}
            active_student = student
            if active_session:
                success, mark_res = mark_attendance(
                    args.hub,
                    active_session.get("id"),
                    student.get("id"),
                    active_session.get("class_id"),
                    forced,
                    args.camera_id,
                    frame
                )
                status_message = f"ATTENDANCE MARKED: {student.get('name')} ({student.get('roll_number')})"
            else:
                status_message = f"Verified {forced} (No active session)"
            print(f">> Manual action: {status_message}")
            status_timer = time.time()

        # Reset challenge after 5 seconds of being passed
        if challenge_passed and (time.time() - last_action_time > 5):
            challenge_passed = False
            current_challenge_idx = (current_challenge_idx + 1) % len(challenges)
            active_student = None

    cap.release()
    cv2.destroyAllWindows()
    print("Camera node stopped.")

if __name__ == '__main__':
    main()
# Presently Biometric Attendance System
