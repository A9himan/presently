# Presently: Attendance That Takes Itself

> **Autonomous Biometric Attendance Engine with Real-Time Anti-Proxy Liveness Verification, Privacy-First Embeddings, 72-Hour Snapshot Retention, and Instant LMS Webhook Integration.**

Manual roll call wastes 5–10 minutes per lecture and enables proxy attendance. **Presently** replaces manual roll call entirely by using live classroom camera terminals to recognise students via facial recognition and mark attendance automatically—enforcing **strictly one mark per student per session**.

---

## 🌟 Key Architecture & Capabilities

### 1. 🏛️ Clear Separation: Central Hub vs. Camera Edge Nodes
- **Central Hub (`http://localhost:3000`):**
  - Manages SQLite database, subjects, classes, privacy face embeddings, LMS webhooks, 72-hour snapshot retention, and official PDF exports.
  - Role-based portal for **Admin** and **Faculty** with credentials `admin123` / `admin123`.
  - Receives edge telemetry and broadcasts attendance live via Server-Sent Events (SSE).
- **Camera Edge Node (`http://localhost:3000/node`):**
  - Standalone classroom edge device client (e.g. `CAM-NODE-01`).
  - Accesses classroom camera / webcam to track faces in real time.
  - Prompts student with dynamic anti-proxy liveness challenges (`Turn Left`, `Turn Right`, `Blink`).
  - Extracts 128-d face embedding and transmits verified marks to Central Hub with round-trip latency tracking.
  - Shows judges live edge telemetry: 30 FPS processing, optical motion vectors, and Hub synchronization state.

### 2. 🔐 Authentication: Admin & Faculty Login
- **Username:** `admin123`
- **Password:** `admin123`
- **Admin Role:** Full management of Subjects, Classes under subjects, Privacy Face Enrollment Studio, Camera Hub Device Monitoring, and LMS Webhook configuration.
- **Faculty Role:** Class section selection, Real-Time Live Attendance Monitor with live SSE streaming, 72-Hour Snapshot Review Gallery, and One-Click PDF Dossier Export.
- **One-Click Quick Login:** Includes quick-fill buttons for both Admin and Faculty.

### 3. 👨‍⚖️ How to Demonstrate to Judges (Side-by-Side Live Demo)
1. Launch the system with **`start.bat`** (or `node server.js`).
2. Two windows/tabs will open automatically:
   - **Window 1 (Central Hub):** Login as **Faculty** or **Admin** (`admin123` / `admin123`) and select the active session (`CS101 - Section A`).
   - **Window 2 (Camera Edge Node):** The dedicated camera terminal running at `http://localhost:3000/node`.
3. In Window 2 (Edge Node), select a student or activate webcam:
   - Click **Trigger Anti-Proxy Challenge** &rarr; The Node prompts: *"Turn head slowly to the left ⬅️"*.
   - Execute the action or test spoofing &rarr; The Node validates the action live and pushes the mark to the Hub.
4. Watch Window 1 (Central Hub): The student instantly appears on the Faculty ledger via SSE, the Present counter ticks up, the LMS webhook fires, and the 72-hour audit snapshot is recorded!

### 2. ⚡ Strict Session Idempotency (One Mark Per Student Per Session)
- Attendance records enforce a unique constraint `(session_id, student_id)`.
- If an already-marked student walks in front of the camera again, Presently detects their identity, informs them that they are already checked in (`ALREADY_MARKED` with original timestamp), and prevents duplicate counting or duplicate LMS pushes.

### 3. 🔒 Privacy-By-Design & 72-Hour Snapshot Vault
- **No Permanent Photo Storage:** Enrolled students have **only 512-dimensional normalized mathematical ArcFace vector embeddings** (via server-side InsightFace ONNX) stored in the database. Raw photos are discarded immediately after vector extraction and thumbnail generation.
- **Fail-Closed Biometrics:** All image decoding, face detection (SCRFD), ArcFace embedding extraction, and multi-signal quality/liveness checks execute server-side in `face_engine_server.py`. Clients are untrusted and only transmit raw image frames.
- **72-Hour Audit Retention:** Temporary audit snapshots captured during attendance are tagged with `expires_at = captured_at + 72 hours`.
- **Automated Retention Purge Daemon:** Background workers automatically wipe expired images, replacing payloads with `[PURGED_EXPIRED_72H]` while preserving audit timestamps and metadata.
- **Time-Warp Verification:** An interactive simulation button lets administrators test and observe the 72-hour auto-purge in real time.

### 4. 👨‍🏫 Faculty Real-Time Attendance Monitor & PDF Export
- **Live Stream via Server-Sent Events (SSE):** Faculty watch attendance update live on their dashboard without refreshing.
- **Live KPI Metrics:** Dynamic counters for Total Enrolled, Present (Count & %), and Absent.
- **One-Click PDF Dossier Export:** Generates an official, publication-quality A4 academic attendance report (`application/pdf`) with institutional headers, metrics box, student ledger, anti-proxy audit log, and faculty sign-off seal.

### 5. 📡 Central Hub & Multi-Room Camera Scaling
- Classroom camera terminals connect to the central hub from multiple rooms:
  - `CAM-ROOM-101`: Hall 101 Entrance Camera
  - `CAM-ROOM-102`: Room 102 Podium Terminal
  - `CAM-LAB-03`: CV Lab 3 Kiosk Node
  - `CAM-AUD-A`: Auditorium A Gate Camera
- Telemetry monitoring: FPS, latency, IP address, and heartbeat pings.

### 6. 🌐 Instant LMS Webhook & REST API
- **Real-Time Webhook Push:** Every marked attendance dispatches an HTTP POST event `attendance.marked` within milliseconds to external LMS platforms (Canvas, Moodle, Blackboard, Google Classroom).
- **HMAC-SHA256 Signatures:** Webhook payloads include the `X-Presently-Signature: sha256=<hash>` header for tamper-proof verification.
- **Mock LMS Feed:** Includes a built-in webhook receiver and visual payload inspector so users can view webhook deliveries live without external configuration.

---

## 🚀 Quick Start Guide

### 1. Launch the Server
To start the Presently server, run:
```cmd
start.bat
```
or run directly via Node:
```cmd
node server.js
```

The system will start on: **`http://localhost:3000`**

### 2. Run Python Biometric Engine & Enrollment Tests
To execute the complete unit and integration test suite (biometrics, liveness, enrollment, and privacy guarantees):
```cmd
python -m unittest discover tests -v
```

### 3. Run End-to-End Test Suite
To execute the automated end-to-end test suite:
```cmd
node test_e2e.js
```

---

## 🖥️ System Modules & User Guide

| Module | Route / Tab | Description |
| :--- | :--- | :--- |
| **Camera Terminal** | `#kiosk` | Classroom entrance kiosk with live face detection, reticle HUD, and mark attendance action. |
| **Faculty Live Monitor** | `#faculty` | Real-time session control, live attendance feed via SSE, search/filter, and one-click PDF Dossier export. |
| **72h Snapshot Vault** | `#snapshots` | Review audit snapshots with live countdown badges (`71h 58m remaining`), and simulate the +72h auto-deletion lifecycle. |
| **Admin Portal** | `#admin` | Create Subjects and Classes, enroll students with 512-d ArcFace face embeddings (privacy guarantee, raw photos discarded), manage faculty, and monitor room cameras. |
| **Camera Hub** | `#cameras` | Multi-room camera device health, FPS telemetry, IP mapping, and heartbeat pinging. |
| **LMS Webhook & API** | `#lms` | Real-time LMS webhook ingestion feed, HMAC-SHA256 signature verification, configuration, and REST API docs. |

---

## 🔌 REST API Endpoints

- `GET /api/config` - System mode (`demoMode`), model architecture, and embedding dimension (512).
- `GET /api/overview` - System KPIs, enrollment stats, active cameras, and retention policy.
- `GET /api/subjects` - List all academic subjects.
- `POST /api/students` - Biometric face enrollment: extracts 512-d ArcFace vector, generates 48×48 blurred thumbnail, and discards raw photo.
- `POST /api/subjects` - Create a subject (`code`, `name`, `department`, `credits`).
- `GET /api/classes` - List all classes with subject and room details.
- `POST /api/classes` - Create a class section under a subject.
- `GET /api/classes/:id/students` - Class roster with enrolled student records.
- `GET /api/sessions/active` - Fetch currently active lecture session and live counts.
- `POST /api/sessions/start` - Start attendance session for a class.
- `POST /api/sessions/:id/stop` - Conclude attendance session.
- `GET /api/challenge/new` - Request a random anti-proxy challenge (`TURN_LEFT`, `TURN_RIGHT`, `BLINK`).
- `POST /api/attendance/verify-and-mark` - Verify face + anti-proxy challenge, mark attendance, store temporary snapshot, and push to LMS webhook.
- `GET /api/export/pdf?sessionId=:id` - Download official A4 Attendance PDF report.
- `GET /api/snapshots` - Review verification audit snapshots with 72h countdown.
- `POST /api/snapshots/purge-expired` - Execute 72h snapshot purge.
- `POST /api/snapshots/simulate-timewarp` - Advance clock by 72+ hours and purge expired snapshots.
- `POST /api/mock-lms/webhook` - Mock LMS webhook receiver.
- `GET /api/mock-lms/feed` - Live incoming webhook payload inspector.

---

## 📄 License
MIT License. Built for modern institutional attendance and privacy compliance.

