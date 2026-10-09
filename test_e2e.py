import requests
import json
import time

BASE_URL = 'http://localhost:3000'

def run_tests():
    print('--- STARTING PRESENTLY E2E INTEGRATION TEST SUITE ---')
    passed = 0
    failed = 0

    def assert_test(cond, msg):
        nonlocal passed, failed
        if cond:
            print(f'  [PASS]: {msg}')
            passed += 1
        else:
            print(f'  [FAIL]: {msg}')
            failed += 1

    try:
        # 1. Overview Test
        res = requests.get(f'{BASE_URL}/api/overview')
        assert_test(res.status_code == 200, 'GET /api/overview returns 200')
        data = res.json()
        assert_test(data.get('totalStudents', 0) >= 12, f"Total students seeded: {data.get('totalStudents')}")
        assert_test(data.get('snapshots', {}).get('retentionPolicyHours') == 72, 'Snapshot retention policy is 72 hours')

        # 2. Subjects Test
        res = requests.get(f'{BASE_URL}/api/subjects')
        assert_test(res.status_code == 200, 'GET /api/subjects returns 200')
        subjects = res.json()
        assert_test(any(s.get('code') in ['CS101', 'DS'] for s in subjects), 'Subject catalog populated')

        # 3. Classes Test
        res = requests.get(f'{BASE_URL}/api/classes')
        assert_test(res.status_code == 200, 'GET /api/classes returns 200')
        classes = res.json()
        assert_test(len(classes) > 0, f"Classes configured: {len(classes)}")

        # 4. Challenge Generator Test
        res = requests.get(f'{BASE_URL}/api/challenge/new')
        assert_test(res.status_code == 200, 'GET /api/challenge/new returns 200')
        ch = res.json()
        assert_test(ch.get('action') in ['TURN_LEFT', 'TURN_RIGHT', 'BLINK'], f"Anti-Proxy challenge generated: {ch.get('action')}")

        # 5. Anti-Proxy Spoof Rejection Test
        spoof_payload = {
            'sessionId': 'sess_live_cs101',
            'cameraId': 'CAM-ROOM-101',
            'challengeId': ch.get('challengeId'),
            'actionCompleted': 'NO_ACTION',
            'telemetry': {'isStaticImageDetected': True, 'yawAngleDelta': 0}
        }
        res_spoof = requests.post(f'{BASE_URL}/api/attendance/verify-and-mark', json=spoof_payload)
        assert_test(res_spoof.status_code == 400, 'Anti-proxy spoof attempt successfully blocked (HTTP 400)')

        # 5b. Verify Teachers cannot manually start sessions (Timetable rule)
        res_teacher_block = requests.post(f'{BASE_URL}/api/sessions/start', json={
            'classId': classes[0]['id'],
            'isTeacherManualStart': True
        })
        assert_test(res_teacher_block.status_code == 403, 'Teacher manual session start blocked by timetable policy (HTTP 403)')

        # 6. Session start via Timetable Engine & Legitimate Verification
        res_sess = requests.post(f'{BASE_URL}/api/sessions/start', json={
            'classId': classes[0]['id'],
            'room': 'Room 101',
            'facultyName': 'Autonomous Timetable Engine'
        })
        assert_test(res_sess.status_code == 201, 'POST /api/sessions/start by Timetable returns 201')
        sess_data = res_sess.json()
        session_id = sess_data['session']['id']

        # Get fresh challenge
        res_fresh_ch = requests.get(f'{BASE_URL}/api/challenge/new?sessionId={session_id}&action=BLINK')
        fresh_ch = res_fresh_ch.json()

        legit_payload = {
            'sessionId': session_id,
            'cameraId': 'CAM-ROOM-101',
            'challengeId': fresh_ch['challengeId'],
            'actionCompleted': 'BLINK',
            'telemetry': {'blinkCount': 1, 'earDip': 0.16, 'yawAngleDelta': 0},
            'forceStudentId': 'stu_05',
            'snapshotBase64': 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg"><rect fill="green"/></svg>'
        }
        res_legit = requests.post(f'{BASE_URL}/api/attendance/verify-and-mark', json=legit_payload)
        assert_test(res_legit.status_code == 201, 'Legitimate attendance marked successfully (HTTP 201)')
        legit_data = res_legit.json()
        assert_test(legit_data.get('code') == 'MARKED_SUCCESSFULLY', 'Response code is MARKED_SUCCESSFULLY')

        # 7. Deduplication Test (One mark per session)
        dup_payload = dict(legit_payload)
        dup_payload['challengeId'] = None
        res_dup = requests.post(f'{BASE_URL}/api/attendance/verify-and-mark', json=dup_payload)
        dup_data = res_dup.json()
        assert_test(dup_data.get('duplicatePrevented') is True or dup_data.get('code') == 'ALREADY_MARKED',
                    'Deduplication enforced: strictly one mark per student per session!')

        # 8. Snapshot Timewarp & 72h Purge
        res_warp = requests.post(f'{BASE_URL}/api/snapshots/simulate-timewarp', json={'hours': 73})
        assert_test(res_warp.status_code == 200, 'Simulate timewarp 73h executed')

        # 9. PDF Export Test
        res_pdf = requests.get(f'{BASE_URL}/api/export/pdf?sessionId={session_id}')
        assert_test(res_pdf.status_code == 200, 'GET /api/export/pdf returns 200')
        assert_test(res_pdf.headers.get('Content-Type') == 'application/pdf', 'PDF Content-Type confirmed')
        assert_test(len(res_pdf.content) > 1000, f"PDF generated successfully ({len(res_pdf.content)} bytes)")

        # 10. LMS Webhook Ping Test
        res_ping = requests.post(f'{BASE_URL}/api/lms/test-ping')
        assert_test(res_ping.status_code == 200, 'LMS webhook ping returned HTTP 200')

        # 11. Photo Upload & Biometric Enrollment Test
        sample_photo_b64 = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
        ts_suffix = str(int(time.time() * 1000))[-6:]
        res_enroll_photo = requests.post(f'{BASE_URL}/api/students/enroll', json={
            'roll_number': f'TEST-PHOTO-{ts_suffix}',
            'name': 'Photo Test Student',
            'email': f'photo.test.{ts_suffix}@campus.edu',
            'department': 'Computer Science',
            'photoData': sample_photo_b64
        })
        assert_test(res_enroll_photo.status_code == 201, 'Student enrolled with uploaded photo data (HTTP 201)')
        photo_stu_data = res_enroll_photo.json()
        assert_test(photo_stu_data.get('student', {}).get('photo_data') is not None, 'Photo data persisted with student record')

        # 12. Class Roster Management Test
        test_cls_id = classes[0]['id']
        test_stu_id = photo_stu_data['student']['id']
        res_add_roster = requests.post(f'{BASE_URL}/api/classes/{test_cls_id}/students', json={'student_id': test_stu_id})
        assert_test(res_add_roster.status_code == 200, 'Student added to class roster via Admin API (HTTP 200)')

        # 13. Teachers Catalog & Multi-Subject Assignment Test
        res_teachers = requests.get(f'{BASE_URL}/api/teachers')
        assert_test(res_teachers.status_code == 200, 'GET /api/teachers returns 200')
        teachers = res_teachers.json()
        assert_test(len(teachers) >= 2, f"Teachers populated: {len(teachers)}")
        gagan_teacher = next((t for t in teachers if 'Gagandeep' in t['name']), None)
        assert_test(gagan_teacher and gagan_teacher.get('subject_count', 0) >= 2,
                    f"Faculty assigned to multiple subjects (Er. Gagandeep Kaur has {gagan_teacher.get('subject_count', 0)} subjects)")

        # 14. Class-Specific Subjects Mapping Test
        res_cls_subs = requests.get(f'{BASE_URL}/api/classes/{test_cls_id}/subjects')
        assert_test(res_cls_subs.status_code == 200, f'GET /api/classes/{test_cls_id}/subjects returns 200')
        cls_subs = res_cls_subs.json()
        assert_test(len(cls_subs) >= 1, f"Class has specific enrolled subjects: {[s['code'] for s in cls_subs]}")

        # 15. Weekly Timetable API Test
        res_tt = requests.get(f'{BASE_URL}/api/timetable/weekly')
        assert_test(res_tt.status_code == 200, 'GET /api/timetable/weekly returns 200')
        tt_slots = res_tt.json()
        assert_test(len(tt_slots) >= 5, f"Weekly timetable slots configured: {len(tt_slots)}")

        # 16. Timetable Slot Activation & Subject Binding Test
        first_slot = tt_slots[0]
        res_act = requests.post(f'{BASE_URL}/api/timetable/activate-slot/{first_slot["id"]}')
        assert_test(res_act.status_code == 200, f'POST /api/timetable/activate-slot/{first_slot["id"]} returns 200')
        act_data = res_act.json()
        assert_test(act_data.get('subjectCode') is not None, f"Active slot bound to subject: {act_data.get('subjectCode')}")
        assert_test(act_data.get('teacherName') is not None, f"Active slot bound to teacher: {act_data.get('teacherName')}")

        # 17. Teacher Subject Attendance PDF Export Test
        res_subj_pdf = requests.get(f'{BASE_URL}/api/export/pdf?subjectId=sub_ds')
        assert_test(res_subj_pdf.status_code == 200, 'GET /api/export/pdf?subjectId=sub_ds returns 200')
        assert_test(res_subj_pdf.headers.get('Content-Type') == 'application/pdf', 'Subject PDF Content-Type confirmed')
        assert_test(len(res_subj_pdf.content) > 1000, f"Subject Attendance PDF generated ({len(res_subj_pdf.content)} bytes)")

    except Exception as e:
        print(f'  [EXCEPTION]: {e}')
        failed += 1

    print('----------------------------------------------------')
    print(f'Test Suite Results: {passed} PASSED, {failed} FAILED')
    print('----------------------------------------------------')
    return failed == 0

if __name__ == '__main__':
    run_tests()
# Presently Biometric Attendance System
