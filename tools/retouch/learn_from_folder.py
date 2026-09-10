# -*- coding: utf-8 -*-
"""사용자가 직접 고른 원본/보정본으로 학습한다.

자동 짝짓기보다 이 길이 정확하다 — 어느 것이 진짜 보정본인지는 사람이 안다.
(자동으로 묶었더니 번호가 의상 세트마다 다시 시작해서 같은 아이의 다른 사진이
짝이 됐고, 그걸 눈으로 보기 전까지 몰랐다.)

쓰는 법: 바탕화면 '보정견본' 폴더의 두 칸에 같은 이름으로 넣고 학습시작.cmd 더블클릭.
  1_원본\\아무개.jpg      ← 손대기 전
  2_보정본\\아무개.jpg     ← 선생님이 끝낸 것 (파일 이름 같아야 함)
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import common as C  # noqa: E402

C.console_utf8()
PY = os.path.join(HERE, '.venv', 'Scripts', 'python.exe')
if not os.path.exists(PY):
    PY = sys.executable


def stems(folder):
    """하위 폴더까지 훑는다. 사용자는 촬영(유치원)별로 나눠 넣으므로
    1_원본\명일유치원\아무개.jpg 처럼 한 겹 더 들어간다.
    짝은 **상대경로**로 맞춘다 — 파일명만 쓰면 다른 유치원의 동명이인이 섞인다."""
    out = {}
    if not os.path.isdir(folder):
        return out
    for dirpath, _, files in os.walk(folder):
        for n in files:
            if os.path.splitext(n)[1].lower() not in C.IMG_EXT:
                continue
            p = os.path.join(dirpath, n)
            rel = os.path.relpath(p, folder)
            out.setdefault(os.path.splitext(rel)[0].strip().lower(), p)
    return out


def real_size(path):
    """EXIF 회전을 적용한 실제 크기. 원본은 회전 플래그만 붙어 있고 보정본은
    이미 돌려서 저장돼 있어, 이걸 안 보면 같은 사진이 '다른 크기'로 보인다."""
    from PIL import Image, ExifTags
    orient = next(k for k, v in ExifTags.TAGS.items() if v == 'Orientation')
    try:
        with Image.open(path) as im:
            w, h = im.size
            try:
                o = im.getexif().get(orient)
            except Exception:
                o = None
        return (h, w) if o in (5, 6, 7, 8) else (w, h)
    except Exception:
        return None


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.expanduser('~'), 'OneDrive', '바탕 화면', '보정견본')
    before_dir = os.path.join(root, '1_원본')
    after_dir = os.path.join(root, '2_보정본')
    work = os.path.join(root, '_학습결과')

    print('견본 폴더 : %s' % root)
    b, a = stems(before_dir), stems(after_dir)
    print('  1_원본   : %d장' % len(b))
    print('  2_보정본 : %d장' % len(a))

    if not b or not a:
        print('\n두 폴더에 사진을 넣고 다시 실행하세요.')
        print('  %s' % before_dir)
        print('  %s' % after_dir)
        return 1

    pairs, unmatched, mismatched = [], [], []
    for k, ap in sorted(a.items()):
        bp = b.get(k)
        if not bp:
            unmatched.append(os.path.basename(ap))
            continue
        # 잘린 보정본도 쓴다 — prepare 가 얼굴 기준으로 각각 정렬한다.
        # 다만 원본보다 화소가 크게 줄어든 것은 '흐리게 만들라'를 가르치므로 여기서 뺀다.
        sb, sa = real_size(bp), real_size(ap)
        if sb and sa and (sa[0] * sa[1]) < 0.72 * (sb[0] * sb[1]):
            mismatched.append('%s  %dx%d → %dx%d (축소 저장)' % (os.path.basename(ap), *sb, *sa))
            continue
        pairs.append({'shoot': os.path.dirname(k) or '견본', 'before': bp, 'after': ap})

    print('\n짝 %d 쌍' % len(pairs))
    if unmatched:
        print('  이름이 안 맞아 뺌 %d장: %s' % (len(unmatched), ', '.join(unmatched[:5])))
    if mismatched:
        print('  축소 저장이라 뺌 %d장 (원본보다 화소가 크게 줄어든 것):' % len(mismatched))
        for m in mismatched[:5]:
            print('     %s' % m)
    if len(pairs) < 5:
        print('\n짝이 %d쌍뿐입니다. 최소 20쌍은 있어야 학습이 의미 있습니다.' % len(pairs))
        if not pairs:
            return 1

    os.makedirs(work, exist_ok=True)
    manifest = os.path.join(work, 'pairs.json')
    with open(manifest, 'w', encoding='utf-8') as f:
        json.dump(pairs, f, ensure_ascii=False, indent=1)

    data = os.path.join(work, 'data')
    steps = [
        ('학습 자료 만들기', [PY, os.path.join(HERE, 'prepare.py'), manifest,
                        '--out', data, '--no-color']),
        ('얼굴형 학습', [PY, os.path.join(HERE, 'train_geom.py'), data]),
        ('질감 학습 (GPU, 오래 걸립니다)', [PY, os.path.join(HERE, 'train_tex.py'), data]),
    ]
    for title, cmd in steps:
        print('\n' + '=' * 60)
        print('▶ %s' % title)
        print('=' * 60)
        if subprocess.call(cmd, cwd=HERE) != 0:
            print('\n[%s] 에서 멈췄습니다.' % title)
            return 1

    print('\n끝났습니다. 결과: %s' % data)
    print('미리보기(원본|모델|선생님): %s' % os.path.join(data, 'previews'))
    return 0


if __name__ == '__main__':
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    input('\n창을 닫으려면 Enter 를 누르세요.')
    sys.exit(code)
