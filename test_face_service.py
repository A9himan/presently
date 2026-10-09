"""
Pipeline-logic tests for face_service.py.

They use the 6 people in InsightFace's bundled sample photo. Augmented copies of
the SAME photo stand in for "same person, different shot", so these tests prove
the decision logic (quality gates, fail-closed, swap detection, margins) works.
They do NOT measure real-world accuracy: use eval/calibrate.py with real photos.
"""
import sys
import os

_CURR_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PKGS = os.path.join(_CURR_DIR, "py_env", "Lib", "site-packages")
if os.path.isdir(_ENV_PKGS) and _ENV_PKGS not in sys.path:
    sys.path.insert(0, _ENV_PKGS)

import cv2
import numpy as np
import pytest

import face_service as fs

pytest.importorskip("insightface")
from insightface.data import get_image as ins_get_image  # noqa: E402

SVC = fs.FaceService(fs.Config())
if not SVC.available():
    pytest.skip("face engine could not load (model download blocked?)", allow_module_level=True)

# Filled in by the `people` fixture: the most head-turned face cannot be enrolled
# (pose rule), so it doubles as the stranger who is not in the gallery.
GALLERY_IDX = []
UNKNOWN_IDX = -1


def _crop(img, f, up=2.0):
    x1, y1, x2, y2 = f.bbox
    cx, cy, s = (x1 + x2) / 2, (y1 + y2) / 2, max(x2 - x1, y2 - y1) * 0.96
    X1, Y1 = int(max(cx - s, 0)), int(max(cy - s, 0))
    X2, Y2 = int(min(cx + s, img.shape[1])), int(min(cy + s, img.shape[0]))
    return cv2.resize(img[Y1:Y2, X1:X2], None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)


def _jpeg(img, q):
    return cv2.imdecode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])[1], 1)


def _rot(img, deg):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)


def enroll_shots(c):
    return [c, np.clip(c * 1.12, 0, 255).astype(np.uint8), _jpeg(c, 60)]


def probe_shots(c):
    return [cv2.flip(c, 1), _rot(c, 3), np.clip(c * 0.88, 0, 255).astype(np.uint8),
            _jpeg(c, 45), cv2.resize(cv2.resize(c, None, fx=0.9, fy=0.9), (c.shape[1], c.shape[0])), c.copy()]


@pytest.fixture(scope="module")
def people():
    img = ins_get_image("t1")
    faces = sorted(SVC.detect(img), key=lambda f: f.bbox[0])
    assert len(faces) == 6
    crops = [_crop(img, f) for f in faces]
    yaws = [abs(SVC.assess(c, max(SVC.detect(c), key=lambda f: f.area), "enroll").metrics["yaw_proxy"])
            for c in crops]
    global UNKNOWN_IDX
    UNKNOWN_IDX = int(np.argmax(yaws))
    GALLERY_IDX[:] = [i for i in range(6) if i != UNKNOWN_IDX]
    return crops, img


@pytest.fixture(scope="module")
def gallery(people):
    crops, _ = people
    g = {}
    for i in GALLERY_IDX:
        r = SVC.enroll(enroll_shots(crops[i]))
        assert r.ok, (i, r.errors, r.frames)
        g[f"stu_{i}"] = r.embeddings
    return g


# ---------------- embeddings ----------------
def test_embedding_is_real_512d_unit_vector(people):
    f = SVC.detect(people[0][0])[0]
    assert f.embedding.shape == (fs.EMBED_DIM,)
    assert abs(np.linalg.norm(f.embedding) - 1) < 1e-3
    assert fs.valid_embedding(f.embedding)


def test_old_128d_vectors_are_rejected():
    assert not fs.valid_embedding([0.1] * 128)
    assert not fs.valid_embedding([float("nan")] * fs.EMBED_DIM)


def test_same_person_scores_far_above_different_people(people):
    crops, _ = people
    e = [SVC.detect(c)[0].embedding for c in crops]
    same = float(e[1] @ SVC.detect(cv2.flip(crops[1], 1))[0].embedding)
    diff = max(float(e[i] @ e[j]) for i in range(6) for j in range(i + 1, 6))
    assert same > 0.6 and diff < 0.3 and same - diff > 0.4


# ---------------- enrollment ----------------
def test_enroll_ok_returns_fused_and_per_frame(people):
    r = SVC.enroll(enroll_shots(people[0][1]))
    assert r.ok and len(r.embeddings) == 3 and fs.valid_embedding(r.embedding)


def test_enroll_needs_enough_frames(people):
    assert not SVC.enroll(enroll_shots(people[0][0])[:2]).ok


def test_enroll_rejects_turned_head(people):
    r = SVC.enroll(enroll_shots(people[0][UNKNOWN_IDX]))
    assert not r.ok and any("USABLE" in e for e in r.errors)


def test_enroll_rejects_group_photo(people):
    r = SVC.enroll([people[1]] * 3)
    assert not r.ok and "MULTIPLE_FACES_IN_FRAME" in r.errors


def test_enroll_rejects_mixed_people(people):
    c = people[0]
    r = SVC.enroll([c[GALLERY_IDX[0]], c[GALLERY_IDX[1]], c[GALLERY_IDX[2]]])
    assert not r.ok and "FRAMES_ARE_DIFFERENT_PEOPLE" in r.errors


def test_enroll_rejects_no_face_and_garbage():
    noise = (np.random.RandomState(0).rand(300, 300, 3) * 255).astype(np.uint8)
    assert not SVC.enroll([noise, noise, noise]).ok
    assert not SVC.enroll(["not-an-image", "x", "y"]).ok


def test_duplicate_identity_guard(people, gallery):
    i0, i1 = GALLERY_IDX[0], GALLERY_IDX[1]
    r = SVC.enroll(probe_shots(people[0][i0])[:3])          # same person again
    assert SVC.duplicate_of(r.embedding, gallery)[0] == f"stu_{i0}"
    other = SVC.enroll(enroll_shots(people[0][i1]))
    assert SVC.duplicate_of(other.embedding, {f"stu_{i0}": gallery[f"stu_{i0}"]}) is None


# ---------------- recognition ----------------
def test_genuine_probe_is_accepted(people, gallery):
    for i in GALLERY_IDX:
        m = SVC.identify(probe_shots(people[0][i]), gallery)
        assert m.decision == "ACCEPT" and m.student_id == f"stu_{i}", (i, m.to_dict())
        assert m.margin >= SVC.cfg.margin and m.vote_share >= SVC.cfg.min_vote_share


def test_stranger_is_never_accepted(people, gallery):
    m = SVC.identify(probe_shots(people[0][UNKNOWN_IDX]), gallery)
    assert m.decision in ("REJECT", "REVIEW") and m.decision != "ACCEPT", m.to_dict()


def test_person_swap_during_capture_is_rejected(people, gallery):
    ia, ib = GALLERY_IDX[0], GALLERY_IDX[1]
    a, b = probe_shots(people[0][ia]), probe_shots(people[0][ib])
    m = SVC.identify(a[:3] + b[:3], gallery)
    assert m.decision == "REJECT" and m.reason == "IDENTITY_CHANGED_DURING_CAPTURE"


def test_too_few_frames_is_rejected(people, gallery):
    m = SVC.identify(probe_shots(people[0][0])[:2], gallery)
    assert m.decision == "REJECT" and m.reason.startswith("INSUFFICIENT_QUALITY_FRAMES")


def test_blurry_and_dark_frames_are_rejected(people, gallery):
    c = people[0][0]
    blur = [cv2.GaussianBlur(x, (0, 0), 6) for x in probe_shots(c)]
    dark = [(x * 0.2).astype(np.uint8) for x in probe_shots(c)]
    for frames in (blur, dark):
        assert SVC.identify(frames, gallery).decision == "REJECT"


def test_lookalike_goes_to_review_not_accept(people, gallery):
    # a gallery containing two near-identical identities has no safe margin
    i0 = GALLERY_IDX[0]
    twin = {"a": gallery[f"stu_{i0}"], "a_twin": gallery[f"stu_{i0}"]}
    m = SVC.identify(probe_shots(people[0][i0]), twin)
    assert m.decision == "REVIEW" and m.reason == "LOW_MARGIN_LOOKALIKE"


def test_empty_gallery_rejects(people):
    assert SVC.identify(probe_shots(people[0][0]), {}).decision == "REJECT"


def test_wrong_dimension_gallery_entries_are_ignored(people):
    m = SVC.identify(probe_shots(people[0][0]), {"old": [[0.1] * 128]})
    assert m.decision == "REJECT" and m.reason == "NO_ENROLLED_FACES_FOR_CLASS"


def test_fails_closed_when_engine_is_down(people, gallery):
    dead = fs.FaceService()
    dead._ensure = lambda: (_ for _ in ()).throw(fs.EngineUnavailable("boom"))
    m = dead.identify(probe_shots(people[0][0]), gallery)
    assert m.decision == "ERROR" and m.reason == "ENGINE_UNAVAILABLE"
    assert not dead.enroll(enroll_shots(people[0][0])).ok


# ---------------- input hygiene ----------------
def test_decode_image_rejects_svg_and_junk():
    assert fs.decode_image("data:image/svg+xml;utf8,<svg></svg>") is None
    assert fs.decode_image("AAAA") is None
    assert fs.decode_image(b"") is None
    assert fs.decode_image(12345) is None
    assert fs.decode_image(b"x" * 7_000_000) is None


def test_roc_summary_math():
    r = fs.roc_summary([0.8, 0.9, 0.7, 0.85], [0.1, 0.2, 0.15, 0.3])
    assert r["eer"] == 0 and r["impostor_max"] == 0.3
    assert r["threshold_far_0.001"] > 0.3 and r["frr_at_far_0.001"] == 0
