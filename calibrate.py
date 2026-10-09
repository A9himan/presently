"""
Calibrate recognition thresholds on YOUR photos.

Folder layout (use your team / classmates, 5+ photos each, different days,
lighting and angles; get their consent):

    eval/data/people/
        alice/ 1.jpg 2.jpg 3.jpg ...
        bob/   1.jpg 2.jpg ...

Run from the project root:
    python eval/calibrate.py                       # default folder
    python eval/calibrate.py path/to/people --out eval/results.json

It prints FAR / FRR / EER and recommended values for the FACE_* env vars.
"""
import argparse
import json
import os
import sys
from itertools import combinations

import cv2
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import face_service as fs  # noqa: E402

EXTS = (".jpg", ".jpeg", ".png", ".webp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=os.path.join("eval", "data", "people"))
    ap.add_argument("--out", default=os.path.join("eval", "results.json"))
    a = ap.parse_args()

    svc = fs.FaceService()
    if not svc.available():
        sys.exit(f"Face engine unavailable: {svc.load_error}")

    embs, skipped = {}, []
    for person in sorted(os.listdir(a.root)):
        d = os.path.join(a.root, person)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if not fn.lower().endswith(EXTS):
                continue
            img = cv2.imread(os.path.join(d, fn))
            faces = svc.detect(img) if img is not None else []
            if len(faces) != 1:
                skipped.append(f"{person}/{fn}: {len(faces)} faces")
                continue
            q = svc.assess(img, faces[0], "verify")
            if not q.ok:
                skipped.append(f"{person}/{fn}: {','.join(q.reasons)}")
                continue
            embs.setdefault(person, []).append(faces[0].embedding)

    embs = {p: v for p, v in embs.items() if len(v) >= 2}
    if len(embs) < 2:
        sys.exit("Need at least 2 people with 2+ usable photos each.")

    genuine = [float(x @ y) for v in embs.values() for x, y in combinations(v, 2)]
    impostor = [float(x @ y) for (_, va), (_, vb) in combinations(embs.items(), 2) for x in va for y in vb]
    r = fs.roc_summary(genuine, impostor)

    # nearest-neighbour margin: how far the best wrong person is from the right one
    margins = []
    for p, v in embs.items():
        others = [e for q, ev in embs.items() if q != p for e in ev]
        for i, e in enumerate(v):
            rest = [x for j, x in enumerate(v) if j != i]
            if rest:
                margins.append(max(float(e @ x) for x in rest) - max(float(e @ o) for o in others))

    accept = max(r["threshold_far_0.001"], r["eer_threshold"])
    rec = {
        "FACE_ACCEPT_THRESHOLD": round(accept, 2),
        # review band: starts at the 1%-FAR point, but always at least 0.05 below accept
        "FACE_REVIEW_THRESHOLD": round(min(r["threshold_far_0.01"], accept - 0.05), 2),
        "FACE_MARGIN": round(max(0.05, float(np.percentile(margins, 5)) if margins else 0.08), 2),
    }
    out = {"people": len(embs), "photos": sum(len(v) for v in embs.values()),
           "skipped": skipped, "model": fs.MODEL_VERSION, "roc": r, "recommended_env": rec}
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=2)

    print(f"people={out['people']} photos={out['photos']} skipped={len(skipped)}")
    print(f"genuine pairs={r['genuine_n']} mean={r['genuine_mean']:.3f} min={r['genuine_min']:.3f}")
    print(f"impostor pairs={r['impostor_n']} mean={r['impostor_mean']:.3f} max={r['impostor_max']:.3f}")
    print(f"EER={r['eer']*100:.2f}% at threshold {r['eer_threshold']:.3f}")
    print(f"FAR<=1%   -> threshold {r['threshold_far_0.01']:.3f}  FRR {r['frr_at_far_0.01']*100:.1f}%")
    print(f"FAR<=0.1% -> threshold {r['threshold_far_0.001']:.3f}  FRR {r['frr_at_far_0.001']*100:.1f}%")
    print("Recommended environment variables:")
    for k, v in rec.items():
        print(f"  {k}={v}")
    for s in skipped[:10]:
        print("  skipped:", s)
    if len(embs) < 8 or r["impostor_n"] < 200:
        print("NOTE: small sample. Treat these numbers as indicative, not final.")


if __name__ == "__main__":
    main()
