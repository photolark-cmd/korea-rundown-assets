# -*- coding: utf-8 -*-
"""학사모에 술을 합성한다.

규칙은 사용자가 준 가이드라인과 완성본 4장에서 읽은 것:
  · 술은 **모자 판의 오른쪽 모서리**에 걸린다
  · 위 끝은 판 윗면 높이, **아래 끝(술 뭉치 끝)은 입선 높이**
  · 모자가 기울어 있으면 술도 같은 각도로 기운다
  · 크기는 위 두 높이 사이로 정해지므로 아이마다 자동으로 맞는다

소재는 assets/tassels/current.json 이 가리킨다. 의상은 해마다 바뀌므로
색·파일명을 코드에 박지 않는다 — 새 PNG 을 넣고 그 파일만 고치면 된다.
"""

import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets', 'tassels')


def load_manifest():
    p = os.path.join(ASSETS, 'current.json')
    if not os.path.exists(p):
        raise ValueError('술 소재가 없습니다: %s' % p)
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def load_tassel(variant='blur', scale_hint=1.0):
    """variant: 'blur'(기본) | 'sharp'.

    **블러본이 기본이다.** 선명본은 아이 얼굴보다 술이 더 눈에 띄어서 못 쓴다
    (사용자 판정, 2026-09-10). 해상도로 자동 선택하던 것은 없앴다 —
    자동화에서는 '튀지 않는 쪽'이 항상 맞다.
    """
    man = load_manifest()
    v = man['variants']
    if variant in (None, '', 'auto'):
        variant = 'blur'
    if variant not in v:
        raise ValueError('없는 종류: %s (있는 것: %s)' % (variant, ', '.join(v)))
    path = os.path.join(ASSETS, v[variant]['file'])
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None or img.shape[2] < 4:
        raise ValueError('술 소재를 못 읽었거나 알파가 없습니다: %s' % path)
    return img, variant, man


def paste_rgba(base, rgba, cx, cy, angle_deg=0.0, height=None, dry=False):
    """rgba 를 base 위 (cx, cy) **위쪽 끝** 기준으로 얹는다. height 는 목표 높이(px)."""
    h0, w0 = rgba.shape[:2]
    k = (height / h0) if height else 1.0
    w, h = max(1, int(round(w0 * k))), max(1, int(round(h0 * k)))
    small = cv2.resize(rgba, (w, h), interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)

    if abs(angle_deg) > 0.05:
        # 회전 중심은 술이 걸리는 지점 = 위쪽 끝 가운데
        M = cv2.getRotationMatrix2D((w / 2, 0.0), -angle_deg, 1.0)
        cos, sin = abs(M[0, 0]), abs(M[0, 1])
        nw, nh = int(w * cos + h * sin), int(w * sin + h * cos)
        M[0, 2] += nw / 2 - w / 2
        M[1, 2] += 0
        small = cv2.warpAffine(small, M, (nw, nh), flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
        w, h = nw, nh

    x0 = int(round(cx - w / 2))
    y0 = int(round(cy))
    H, W = base.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x0 + w - sx0), min(H, y0 + h - sy0)
    if x1 <= x0 or y1 <= y0:
        raise ValueError('술이 사진 밖으로 나갑니다')

    if dry:                       # 어디에 놓일지만 알려 준다 (튀어나옴 검사용)
        return (x0, y0, x1, y1)
    patch = small[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
    a = patch[..., 3:4].astype(np.float32) / 255.0
    roi = base[y0:y1, x0:x1].astype(np.float32)
    base[y0:y1, x0:x1] = (patch[..., :3].astype(np.float32) * a + roi * (1 - a)).astype(np.uint8)
    return (x0, y0, x1, y1)


PRINT_WIDTH_CM = 25.4          # 10x13 인화의 가로 10인치. '1cm 이동' 을 화소로 옮길 때 쓴다


def cm_to_px(img, cm):
    """인화물 기준 cm 를 화소로. 10인치 폭에 인화한다고 보고 환산한다."""
    return cm * img.shape[1] / PRINT_WIDTH_CM


def board_corner(hat_mask, side='right'):
    """모자 판의 **실제 꼭짓점**을 찾는다.

    술은 사각형 상자의 변이 아니라 판이 뾰족하게 뻗은 모서리 끝에 매달린다.
    판은 위쪽에 있으므로 마스크 상단 35% 안에서 가장 바깥으로 나간 점을 고른다.
    (상자 오른쪽 변을 쓰면 모자가 기울었을 때 엉뚱한 높이가 나온다.)
    """
    ys, xs = np.nonzero(hat_mask)
    if not len(xs):
        raise ValueError('모자 마스크가 비었습니다')
    top, bot = ys.min(), ys.max()
    band = ys <= top + (bot - top) * 0.35
    if band.sum() < 10:
        band = slice(None)
    bx, by = xs[band], ys[band]
    i = int(np.argmax(bx)) if side == 'right' else int(np.argmin(bx))
    return float(bx[i]), float(by[i])


def place(img, hat_stats, hat_angle_deg, mouth_y, variant='blur',
          dx=0.0, dy=0.0, dx_cm=0.0, length_scale=1.0, side='right', hat_mask=None):
    """술을 얹고 (놓인 상자, 쓴 종류) 를 돌려준다.

    dx·dy 는 모자 폭 대비 미세 조정, length_scale 은 길이 배율.
    side 는 'right'(기본) 또는 'left'.
    """
    hx, hy, hw, hh = hat_stats[:4]
    # 걸리는 지점 = 판의 실제 꼭짓점. 완성본을 보면 끈이 그 점에서 수직으로 떨어진다.
    if hat_mask is not None:
        anchor_x, anchor_y = board_corner(hat_mask, side)
    else:
        anchor_x = (hx + hw) if side == 'right' else hx
        anchor_y = hy
    length = max(8.0, (mouth_y - anchor_y) * length_scale)

    tassel, used, _ = load_tassel(variant, scale_hint=length / 866.0)
    anchor_x += dx * hw + cm_to_px(img, dx_cm)
    anchor_y += dy * hh

    # 술이 모자 판 **위로 튀어나오면 안 된다**. 기울여 붙이면 회전한 상자가
    # 위로 자라므로, 붙인 뒤 판 윗선 위로 올라간 만큼 그대로 내린다.
    # 술은 중력으로 떨어지므로 모자가 기울어도 **수직**이다 (완성본에서 확인).
    box = paste_rgba(img, tassel, anchor_x, anchor_y, angle_deg=0.0,
                     height=length, dry=True)
    over = hy - box[1]
    if over > 0:
        anchor_y += over
    box = paste_rgba(img, tassel, anchor_x, anchor_y, angle_deg=0.0, height=length)
    return box, used, length
