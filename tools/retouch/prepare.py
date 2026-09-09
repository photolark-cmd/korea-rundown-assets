#!/usr/bin/env python3
"""Turn before/after portrait pairs into training data.

For every pair: find the face in both images, cut the same upright face crop
from each, warp the retouched crop back onto the original's landmarks (undoing
any liquify so the two are pixel-aligned), and record how far each landmark
moved. The aligned crops train the texture model; the landmark moves train the
geometry model.

Usage:
  python tools/retouch/prepare.py <pairs-dir> --out <data-dir> [--face-size 1024] [--preset <id>]

<pairs-dir> layout is the same as learn-preset.mjs:
  before/IMG_1.jpg + after/IMG_1.jpg      or      IMG_1.before.jpg + IMG_1.after.jpg

--preset applies a colour LUT from tools/photo-fix/presets.js to the originals
first, so the texture model only has to learn what the LUT does not cover.
Use the same --preset with retouch.py.
"""

import argparse
import json
import os
import re
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402


def collect_pairs(root):
    # 실제 촬영본은 원본과 보정본이 각자 제 자리에 있고 한 쌍이 수십 MB 다.
    # before/after 폴더로 모으려면 통째로 복사해야 하므로(수백 쌍이면 십수 GB),
    # 짝 목록을 적은 JSON 을 그대로 받는다. [{"before": 경로, "after": 경로, "shoot": 이름}, ...]
    if root.lower().endswith('.json'):
        with open(root, encoding='utf-8') as f:
            items = json.load(f)
        pairs = []
        for i, it in enumerate(items):
            b, a = it['before'], it['after']
            if not (os.path.isfile(b) and os.path.isfile(a)):
                continue
            stem = os.path.splitext(os.path.basename(b))[0]
            key = '%04d_%s_%s' % (i, it.get('shoot', ''), stem)
            pairs.append((key, b, a))
        return sorted(pairs)

    bdir, adir = os.path.join(root, 'before'), os.path.join(root, 'after')
    pairs = []
    if os.path.isdir(bdir) and os.path.isdir(adir):
        afters = {os.path.splitext(f)[0]: os.path.join(adir, f) for f in C.list_images(adir)}
        for f in C.list_images(bdir):
            key = os.path.splitext(f)[0]
            if key in afters:
                pairs.append((key, os.path.join(bdir, f), afters[key]))
    else:
        files = C.list_images(root)
        afters = {re.sub(r'\.after$', '', os.path.splitext(f)[0], flags=re.I): os.path.join(root, f)
                  for f in files if re.search(r'\.after\.[^.]+$', f, re.I)}
        for f in files:
            if re.search(r'\.before\.[^.]+$', f, re.I):
                key = re.sub(r'\.before$', '', os.path.splitext(f)[0], flags=re.I)
                if key in afters:
                    pairs.append((key, os.path.join(root, f), afters[key]))
    return sorted(pairs)


def safe_key(key):
    return re.sub(r'[^\w.-]+', '_', key)


def main():
    C.console_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('pairs')
    ap.add_argument('--out', required=True, help='학습 데이터를 쓸 폴더')
    ap.add_argument('--face-size', type=int, default=1024, help='크롭 안에서 얼굴 폭(px). 기본 1024')
    ap.add_argument('--preset', help='먼저 적용할 색 프리셋 id (tools/photo-fix/presets.js)')
    ap.add_argument('--presets-js', help='presets.js 경로 (기본: tools/photo-fix/presets.js)')
    args = ap.parse_args()

    pairs = collect_pairs(args.pairs)
    if not pairs:
        sys.exit(f'{args.pairs} 에서 before/after 쌍을 찾지 못했습니다.')
    print(f'{len(pairs)}쌍 발견')

    lut = C.load_lut(args.preset, args.presets_js) if args.preset else None
    os.makedirs(args.out, exist_ok=True)
    lm = C.Landmarker()

    records, skipped = [], []
    t0 = time.time()
    for n, (key, bpath, apath) in enumerate(pairs, 1):
        try:
            before, after = C.imread(bpath), C.imread(apath)
        except ValueError as e:
            skipped.append((key, str(e))); continue
        if before.shape != after.shape:
            skipped.append((key, f'크기가 다름 {before.shape[1]}x{before.shape[0]} vs {after.shape[1]}x{after.shape[0]} — 크롭/리사이즈된 쌍'))
            continue
        if lut is not None:
            before = C.apply_lut(before, lut)

        # 보정본은 원본이 찾은 자리에서 다시 찾는다. 각자 독립으로 찾으면 창 스캔이
        # 서로 다른 창에 걸려 두 좌표계가 어긋나고, 변형량이 실제의 수십 배로 나온다.
        lm_b = lm.detect(before)
        lm_a = lm.detect_near(after, lm_b) if lm_b is not None else None
        if lm_b is None or lm_a is None:
            skipped.append((key, '얼굴을 찾지 못함' + ('' if lm_b is not None else ' (원본)') + ('' if lm_a is not None else ' (보정본)')))
            continue

        # same crop frame for both, taken from the original's landmarks
        M, side = C.crop_transform(lm_b, args.face_size)
        crop_b = cv2.warpAffine(before, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        crop_a = cv2.warpAffine(after, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pb, pa = C.apply_affine(M, lm_b), C.apply_affine(M, lm_a)

        # undo the liquify: move the retouched pixels back onto the original's mesh
        anchors = C.anchor_points(pb, side)
        aligned = C.warp_points(crop_a, np.vstack([pa, anchors]), np.vstack([pb, anchors]), side)

        skin, gate = C.face_masks(pb, side, args.face_size)
        _, face_w, _ = C.face_frame(lm_b)
        disp_px = np.linalg.norm(pa - pb, axis=1)
        tex_change = float(np.abs(aligned.astype(np.float32) - crop_b.astype(np.float32)).mean(-1)[gate > 0.5].mean())
        raw_change = float(np.abs(crop_a.astype(np.float32) - crop_b.astype(np.float32)).mean(-1)[gate > 0.5].mean())

        d = os.path.join(args.out, safe_key(key))
        os.makedirs(d, exist_ok=True)
        cv2.imwrite(os.path.join(d, 'before.png'), crop_b)
        cv2.imwrite(os.path.join(d, 'after.png'), aligned)
        cv2.imwrite(os.path.join(d, 'skin.png'), (skin * 255).astype(np.uint8))
        cv2.imwrite(os.path.join(d, 'gate.png'), (gate * 255).astype(np.uint8))

        records.append({
            'key': safe_key(key),
            'norm_before': C.normalise(lm_b).tolist(),
            'disp': (C.normalise(lm_a, ref=lm_b) - C.normalise(lm_b)).tolist(),
            'face_width_px': face_w,
            'liquify_px': float(disp_px.mean()),
            'liquify_max_px': float(disp_px.max()),
            'texture_change': tex_change,
            'raw_change': raw_change,
        })
        el = time.time() - t0
        print(f'  [{n}/{len(pairs)}] {key}  변형 평균 {disp_px.mean():.1f}px  질감 변화 {tex_change:.1f}  ({el:.0f}s)')
    lm.close()

    if not records:
        # 탈락 사유는 아래에서 찍는데 여기서 먼저 죽으면 "왜 안 되는지"가 영영 안 보인다.
        # 전부 탈락했을 때야말로 그 사유가 유일한 단서다.
        print('쓸 수 있는 쌍이 없습니다. 탈락 사유 %d건:' % len(skipped))
        for k, why in skipped:
            print('  %s: %s' % (k, why))
        sys.exit(1)

    with open(os.path.join(args.out, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump({'face_size': args.face_size, 'preset': args.preset, 'records': records}, f, ensure_ascii=False)

    liq = np.array([r['liquify_px'] for r in records])
    tex = np.array([r['texture_change'] for r in records])
    raw = np.array([r['raw_change'] for r in records])
    print(f'\n{len(records)}쌍 준비 완료 → {args.out}')
    print(f'  얼굴형 변형: 평균 {liq.mean():.1f}px (최대 {liq.max():.1f}px) — 원본 얼굴 폭 기준 {100 * liq.mean() / np.mean([r["face_width_px"] for r in records]):.2f}%')
    print(f'  질감·색 변화 (얼굴 안, 0~255): 정렬 전 {raw.mean():.1f} → 정렬 후 {tex.mean():.1f}')
    if liq.mean() < 1.0:
        print('  → 변형이 거의 없습니다. 학습·적용 때 --no-geom 을 써도 됩니다.')
    if skipped:
        print(f'\n건너뜀 {len(skipped)}쌍:')
        for k, why in skipped:
            print(f'  {k}: {why}')


if __name__ == '__main__':
    main()
