#!/usr/bin/env python3
"""Studio: one photo at a time, with a chat window to Claude.

Left: original. Right: current result. Bottom right: a conversation with
Claude, who sees the current photo every turn and works through tools — the
colour sliders, auto levels, presets, spot healing, skin smoothing, the learned
face models, cropping, undo and save. Everything runs on this machine; only
the chat (with a downscaled view of the photo) goes to the Claude API.

Usage:
  python tools/retouch/studio.py [--folder 촬영본/] [--out 결과/] [--data work/] [--port 8765]

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile) for the chat; the
rest of the studio works without it.
"""

import argparse
import base64
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import color  # noqa: E402
import common as C  # noqa: E402

MODEL = 'claude-opus-5'
PREVIEW_MAX = 1400      # what the browser shows
VIEW_MAX = 1024         # what Claude sees each turn
TOOL_VIEW_MAX = 800     # what Claude sees after each edit
MAX_TOOL_ROUNDS = 12
HISTORY_MESSAGES = 40   # older turns are dropped
IMAGE_TURNS_KEPT = 3    # older user-turn images are replaced by a placeholder

SIZES = {'orig': None, '1600': {'long': 1600}, '1200x630': {'w': 1200, 'h': 630}, '1080x1080': {'w': 1080, 'h': 1080}}

# Hand-tuned starting points, same values as tools/photo-fix/index.html.
BUILT_IN = [
    {'id': 'cvs-night', 'name': '편의점 · 형광등', 'params': {'exposure': 0.15, 'contrast': 1.06, 'shadows': 25, 'highlights': -20, 'temp': -12, 'tint': -6, 'vibrance': 18}},
    {'id': 'street-night', 'name': '야간 거리 · 나트륨등', 'params': {'exposure': 0.2, 'contrast': 1.1, 'shadows': 30, 'highlights': -25, 'temp': -26, 'tint': 4, 'vibrance': 12}},
    {'id': 'food', 'name': '음식 클로즈업', 'params': {'exposure': 0.1, 'contrast': 1.08, 'shadows': 12, 'highlights': -10, 'saturation': 8, 'vibrance': 14, 'sharpen': 35}},
    {'id': 'sign', 'name': '간판 · 가격표', 'params': {'contrast': 1.25, 'shadows': 8, 'highlights': -8, 'saturation': -18, 'sharpen': 45}},
]


def fitted(w, h, mx):
    k = min(1.0, mx / max(w, h))
    return max(1, round(w * k)), max(1, round(h * k))


def downscale(img, mx):
    w, h = fitted(img.shape[1], img.shape[0], mx)
    return img if (w, h) == (img.shape[1], img.shape[0]) else cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)


def jpeg_b64(img, quality=85):
    ok, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return base64.b64encode(buf.tobytes()).decode('ascii')


def image_block(img, mx, quality=80):
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': jpeg_b64(downscale(img, mx), quality)}}


# ---------------------------------------------------------------- session

class Session:
    """The one photo being worked on, its edit history, and the chat."""

    def __init__(self, args):
        self.args = args
        self.lock = threading.RLock()
        self.presets = self._load_presets(args.presets_js)
        self._lm = None
        self._retoucher = None
        self._client = None
        self._thumbs = {}
        self._counts = {}
        self.picked = None      # 사용자가 직접 고른 사진들 (폴더 목록보다 우선)
        self._thumb_lock = threading.Lock()
        self.messages = []
        self.clear()

    def clear(self):
        self.name = self.src_path = None
        self.orig = self.base = None
        self.params = dict(color.DEFAULTS)
        self.auto = False
        self.preset = None
        self.undo_stack = []
        self.pts = None
        self.pts_checked = False
        self.alpha = None

    def subfolders(self):
        """사진이 든 바로 아래 폴더들. 상위 폴더를 골랐을 때 화면에서 고를 수 있게.

        개수는 반드시 **재귀로** 센다. 촬영본은 촬영폴더/원본/학사모 처럼 여러 겹이라,
        직속 사진만 세면 안쪽에만 사진이 있는 촬영 폴더가 목록에서 통째로 사라진다."""
        root = self.args.folder
        if not root or not os.path.isdir(root):
            return []
        out = []
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []
        for name in names:
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            n = self._count_deep(path)
            if n:
                out.append({'name': name, 'count': n})
        return out

    def _count_deep(self, path, cap=5000):
        """하위까지 통틀어 이미지가 몇 장인지. 폴더별로 캐시한다 (네트워크 드라이브일 수 있음)."""
        hit = self._counts.get(path)
        if hit is not None:
            return hit
        n = 0
        try:
            for _, _, files in os.walk(path):
                n += sum(1 for f in files if os.path.splitext(f)[1].lower() in C.IMG_EXT)
                if n >= cap:
                    break
        except OSError:
            pass
        self._counts[path] = n
        return n

    def set_folder(self, path):
        if not os.path.isdir(path):
            raise ValueError('폴더가 없습니다: %s' % path)
        self.args.folder = path
        self.picked = None

    def listing(self):
        """필름스트립에 무엇을 보여 줄지. 사진을 직접 고른 상태면 그 목록이 이긴다."""
        if self.picked:
            return {'folder': os.path.dirname(self.picked[0]),
                    'files': [os.path.basename(p) for p in self.picked],
                    'subfolders': [], 'picked': True}
        folder = self.args.folder
        files = C.list_images(folder) if folder and os.path.isdir(folder) else []
        return {'folder': folder, 'files': files,
                'subfolders': [] if files else self.subfolders(), 'picked': False}

    def path_of(self, name):
        """필름스트립의 이름 하나를 실제 경로로. 고른 사진은 폴더가 제각각일 수 있다."""
        name = os.path.basename(name)
        if self.picked:
            for p in self.picked:
                if os.path.basename(p) == name:
                    return p
        return os.path.join(self.args.folder, name)

    def browse(self, mode):
        """윈도 기본 열기 대화상자. 폴더 파고들기보다 이게 사람 손에 맞는다.

        tkinter 는 만든 스레드에서만 다뤄야 하는데, 여기는 요청 스레드이고 그 안에서
        만들고 부수므로 규칙을 지킨다. 대화상자가 브라우저 뒤로 숨지 않게 topmost."""
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        start = self.args.folder if self.args.folder and os.path.isdir(self.args.folder) else None
        try:
            if mode == 'folder':
                path = filedialog.askdirectory(title='사진이 든 폴더', initialdir=start, parent=root)
                if path:
                    self.set_folder(os.path.normpath(path))
            else:
                paths = filedialog.askopenfilenames(
                    title='사진 고르기 (Ctrl·Shift 로 여러 장)', initialdir=start, parent=root,
                    filetypes=[('사진', '*.jpg *.jpeg *.png *.webp *.tif *.tiff'), ('모든 파일', '*.*')])
                paths = [os.path.normpath(p) for p in (paths or [])]
                if paths:
                    self.picked = paths
                    self.args.folder = os.path.dirname(paths[0])
        finally:
            root.destroy()
        return self.listing()

    def thumb(self, path):
        """필름스트립 썸네일. 원본이 2천만 화소짜리라 매번 디코딩하면 못 쓴다 —
        수백 장이 동시에 요청되므로 한 번 만든 건 메모리에 들고 있는다."""
        key = (path, os.path.getmtime(path))
        with self._thumb_lock:
            hit = self._thumbs.get(key)
        if hit is not None:
            return hit
        # C.imread 는 flags 를 받지 않는다. 썸네일은 1/4 크기로 디코딩해야 (원본이 2천만
        # 화소라) 쓸 만한 속도가 나오므로 여기서 직접 디코딩한다.
        data = np.fromfile(path, np.uint8)                 # 비ASCII 경로 대응
        img = cv2.imdecode(data, cv2.IMREAD_REDUCED_COLOR_4)
        if img is None:
            raise ValueError('열 수 없는 이미지: %s' % path)
        buf = cv2.imencode('.jpg', downscale(img, 160), [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
        with self._thumb_lock:
            if len(self._thumbs) > 2000:
                self._thumbs.clear()
            self._thumbs[key] = buf
        return buf

    # ---- presets
    def _load_presets(self, presets_js):
        out = [dict(p) for p in BUILT_IN]
        path = presets_js or os.path.join(C.HERE, '..', 'photo-fix', 'presets.js')
        try:
            src = open(path, encoding='utf-8').read()
            import re
            m = re.search(r'window\.LEARNED_PRESETS\s*=\s*(\[[\s\S]*\]);', src)
            for p in json.loads(m.group(1)) if m else []:
                out.append({'id': p['id'], 'name': p['name'], 'lut': np.array(p['lut'], np.uint8)[::-1].copy()})
        except (OSError, ValueError):
            pass
        return out

    def preset_lut(self):
        p = next((x for x in self.presets if x['id'] == self.preset), None)
        return p.get('lut') if p else None

    # ---- image
    def open(self, path=None, data=None, name=None):
        if data is not None:
            arr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError('열 수 없는 이미지')
            cache = os.path.join(self.args.out, '.source')
            os.makedirs(cache, exist_ok=True)
            path = os.path.join(cache, name)
            with open(path, 'wb') as f:
                f.write(data)
        else:
            img = C.imread(path)
            name = os.path.basename(path)
        self.clear()
        self.messages = []
        self.name, self.src_path = name, path
        self.orig = self.base = img

    def snapshot(self, label):
        self.undo_stack.append((self.base, dict(self.params), self.auto, self.preset, label))
        del self.undo_stack[:-10]

    def undo(self):
        if not self.undo_stack:
            return None
        self.base, self.params, self.auto, self.preset, label = self.undo_stack.pop()
        self.pts_checked = False
        return label

    def render(self, full=False):
        src = self.base if full else downscale(self.base, PREVIEW_MAX)
        auto = color.auto_lut(downscale(self.base, PREVIEW_MAX)) if self.auto else None
        return color.apply(src, self.params, self.preset_lut(), auto)

    def landmarks(self):
        if not self.pts_checked:
            if self._lm is None:
                self._lm = C.Landmarker()
            self.pts = self._lm.detect(self.base)
            self.pts_checked = True
        return self.pts

    def retoucher(self):
        if self._retoucher is None:
            from retouch import Retoucher
            self._retoucher = Retoucher(self.args.data, self.args.device)
        return self._retoucher

    def state(self):
        if self.base is None:
            return {'open': False}
        changed = {k: v for k, v in self.params.items() if v != color.DEFAULTS[k]}
        return {'open': True, 'name': self.name, 'width': int(self.base.shape[1]), 'height': int(self.base.shape[0]),
                'params': self.params, 'changed': changed, 'auto': self.auto, 'preset': self.preset,
                'undo': [u[4] for u in self.undo_stack], 'face': (None if not self.pts_checked else self.pts is not None),
                'models': os.path.exists(os.path.join(self.args.data, 'tex.pt')) or os.path.exists(os.path.join(self.args.data, 'geom.npz'))}

    def state_text(self):
        s = self.state()
        if not s['open']:
            return '열린 사진 없음'
        bits = [f"{s['name']} {s['width']}×{s['height']}px"]
        bits.append('조정: ' + (', '.join(f'{k}={v:g}' for k, v in s['changed'].items()) or '없음'))
        bits.append('자동 보정: ' + ('켜짐' if s['auto'] else '꺼짐'))
        bits.append('프리셋: ' + (s['preset'] or '없음'))
        bits.append('되돌리기 가능: ' + (' → '.join(s['undo']) if s['undo'] else '없음'))
        bits.append('얼굴: ' + {None: '아직 확인 안 함', True: '검출됨', False: '없음'}[s['face']])
        bits.append('학습된 얼굴 모델: ' + ('있음' if s['models'] else '없음 (face_models 사용 불가)'))
        if self.alpha is not None:
            bits.append('배경 분리됨(투명 PNG 로 저장)')
        if s['face'] and self.pts is not None:
            bits.append(f'눈높이 기울기 {np.degrees(C.face_frame(self.pts)[2]):+.1f}°')
        if self.args.folder and os.path.isdir(self.args.folder):
            others = [f for f in C.list_images(self.args.folder) if f != self.name][:30]
            if others:
                bits.append('폴더의 다른 사진(eyes_from 의 donor 로 쓸 수 있음): ' + ', '.join(others))
        return ' · '.join(bits)

    # ---- edits
    def edit_heal(self, spots, snap=True):
        img = self.base.copy()
        h, w = img.shape[:2]
        done = []
        for s in spots:
            r = max(3, int(round(float(s.get('radius', 0.012)) * w)))
            cx, cy = int(round(float(s['x']) * w)), int(round(float(s['y']) * h))
            if snap:
                cx, cy = self._snap(img, cx, cy, r)
            x0, y0 = max(0, cx - 4 * r), max(0, cy - 4 * r)
            x1, y1 = min(w, cx + 4 * r), min(h, cy + 4 * r)
            roi = img[y0:y1, x0:x1]
            mask = np.zeros(roi.shape[:2], np.uint8)
            cv2.circle(mask, (cx - x0, cy - y0), int(r * 1.4), 255, -1)
            healed = cv2.inpaint(roi, mask, 3, cv2.INPAINT_TELEA)
            soft = cv2.GaussianBlur(mask, (0, 0), max(1.0, r * 0.4)).astype(np.float32)[..., None] / 255
            img[y0:y1, x0:x1] = (roi * (1 - soft) + healed * soft).round().astype(np.uint8)
            done.append((cx / w, cy / h))
        self.snapshot(f'잡티 {len(spots)}곳')
        self.base = img
        return done

    @staticmethod
    def _snap(img, cx, cy, r):
        """Move the point onto the most spot-like blemish within 2r: the pixel
        that differs most from its local median in luminance."""
        h, w = img.shape[:2]
        x0, y0 = max(0, cx - 2 * r), max(0, cy - 2 * r)
        x1, y1 = min(w, cx + 2 * r + 1), min(h, cy + 2 * r + 1)
        roi = img[y0:y1, x0:x1]
        if roi.size == 0:
            return cx, cy
        L = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        k = max(3, (int(r * 2) // 2) * 2 + 1)
        med = cv2.medianBlur(L, min(k, 31))
        diff = cv2.GaussianBlur(cv2.absdiff(L, med).astype(np.float32), (0, 0), max(1.0, r * 0.4))
        y, x = np.unravel_index(int(diff.argmax()), diff.shape)
        if diff[y, x] < 6:
            return cx, cy
        return x0 + int(x), y0 + int(y)

    def edit_smooth(self, amount):
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못해 피부 보정을 할 수 없습니다')
        from retouch import paste_back
        face_size = 1024
        M, side = C.crop_transform(pts, face_size)
        crop = cv2.warpAffine(self.base, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pc = C.apply_affine(M, pts)
        skin, gate = C.face_masks(pc, side, face_size)
        smooth = cv2.bilateralFilter(crop, 0, 28, face_size * 0.012)
        k = (skin * float(amount))[..., None]
        out = (crop * (1 - k) + smooth * k).round().astype(np.uint8)
        self.snapshot(f'피부 {amount:g}')
        self.base = paste_back(self.base, out, M, gate)

    def edit_face_models(self, geom, tex):
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        rt = self.retoucher()
        self.snapshot(f'얼굴 모델 g{geom:g}/t{tex:g}')
        self.base = rt.process(self.base, pts, geom, tex)
        self.pts_checked = False

    def edit_crop(self, x0, y0, x1, y1):
        h, w = self.base.shape[:2]
        X0, X1 = sorted((int(round(x0 * w)), int(round(x1 * w))))
        Y0, Y1 = sorted((int(round(y0 * h)), int(round(y1 * h))))
        X0, Y0 = max(0, X0), max(0, Y0)
        X1, Y1 = min(w, X1), min(h, Y1)
        if X1 - X0 < 16 or Y1 - Y0 < 16:
            raise ValueError('크롭 영역이 너무 작습니다')
        self.snapshot('크롭')
        self.base = np.ascontiguousarray(self.base[Y0:Y1, X0:X1])
        self.pts_checked = False

    # ---- pose: whole-image levelling, head-only tilt, eyes from another shot
    MAX_HEAD_TILT = 12.0

    def edit_straighten(self, angle=None):
        """Rotate the whole photo so the eyes are level (or by `angle` degrees), then
        crop away the empty corners."""
        if angle is None:
            pts = self.landmarks()
            if pts is None:
                raise ValueError('얼굴을 찾지 못해 기울기를 잴 수 없습니다. angle 을 직접 주세요')
            angle = float(np.degrees(C.face_frame(pts)[2]))
        h, w = self.base.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        rot = cv2.warpAffine(self.base, M, (w, h), flags=cv2.INTER_LANCZOS4)
        a = abs(np.radians(angle))
        sa, ca = np.sin(a), np.cos(a)
        long_, short = max(w, h), min(w, h)
        if short <= 2 * sa * ca * long_ or abs(sa - ca) < 1e-10:
            x = 0.5 * short
            wr, hr = (x / sa, x / ca) if w >= h else (x / ca, x / sa)
        else:
            c2 = ca * ca - sa * sa
            wr, hr = (w * ca - h * sa) / c2, (h * ca - w * sa) / c2
        wr, hr = int(wr), int(hr)
        x0, y0 = (w - wr) // 2, (h - hr) // 2
        self.snapshot(f'수평 {angle:+.1f}°')
        self.base = np.ascontiguousarray(rot[y0:y0 + hr, x0:x0 + wr])
        self.pts_checked = False
        return angle

    def edit_head_tilt(self, angle=None):
        """Rotate only the head about a pivot below the chin, warping the pixels
        around it so the neck and hair follow. Small angles only."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        if angle is None:
            angle = float(np.degrees(C.face_frame(pts)[2]))
        if abs(angle) > self.MAX_HEAD_TILT:
            raise ValueError(f'고개만 돌리는 건 ±{self.MAX_HEAD_TILT:g}° 까지입니다 (요청 {angle:+.1f}°). 사진 전체를 돌리는 straighten 을 쓰세요')
        from retouch import paste_back
        face_size = 1024
        M, side = C.crop_transform(pts, face_size, margin=2.6)
        crop = cv2.warpAffine(self.base, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pc = C.apply_affine(M, pts)
        _, fw, _ = C.face_frame(pc)
        pivot = pc[152] + np.array([0, 0.35 * fw], np.float32)          # a bit below the chin
        R = cv2.getRotationMatrix2D((float(pivot[0]), float(pivot[1])), angle, 1.0)
        target = C.apply_affine(R, pc)
        anchors = C.anchor_points(pc, side, ring=2.1)
        warped = C.warp_points(crop, np.vstack([pc, anchors]), np.vstack([target, anchors]), side)
        centre = pc[C.FACE_OVAL].mean(0)
        radius = np.linalg.norm(pc[C.FACE_OVAL] - centre, axis=1).max() * 2.1
        mask = np.zeros((side, side), np.uint8)
        cv2.circle(mask, (int(centre[0]), int(centre[1])), int(radius), 255, -1)
        mask = cv2.GaussianBlur(mask, (0, 0), face_size * 0.05).astype(np.float32) / 255
        self.snapshot(f'고개 {angle:+.1f}°')
        self.base = paste_back(self.base, warped, M, mask)
        self.pts_checked = False
        return angle

    STABLE = [6, 168, 197, 195, 33, 133, 362, 263, 70, 105, 107, 336, 334, 300, 234, 454]

    # Expression warp: which landmarks move, and how much of the full move each
    # takes (Photoshop's face-aware "smile" works the same way — corners up and
    # out, cheeks up, and the strip of lip next to the corner follows).
    LIP_OUTER = {61: 1.0, 146: 0.6, 185: 0.6, 91: 0.3, 40: 0.3, 181: 0.1, 39: 0.1,
                 291: 1.0, 375: 0.6, 409: 0.6, 321: 0.3, 270: 0.3, 405: 0.1, 269: 0.1}
    LIP_INNER = {78: 1.0, 95: 0.6, 191: 0.6, 88: 0.3, 80: 0.3, 308: 1.0, 324: 0.6, 415: 0.6, 318: 0.3, 310: 0.3}
    CHEEKS = [50, 101, 118, 117, 123, 205, 206, 280, 330, 347, 346, 352, 425, 426]
    BROW_INNER = [107, 55, 65, 66, 336, 285, 295, 296]
    BROW_OUTER = [70, 63, 105, 300, 293, 334]

    def edit_expression(self, smile=0.0, relax_brow=0.0):
        """Geometric expression change: a slight smile and/or un-furrowed brows.
        Moves landmarks in face-width units and warps the crop to follow."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        smile, relax_brow = float(np.clip(smile, -1, 1)), float(np.clip(relax_brow, 0, 1))
        from retouch import paste_back, paste_mask
        face_size = 1024
        M, side = C.crop_transform(pts, face_size)
        crop = cv2.warpAffine(self.base, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pc = C.apply_affine(M, pts)
        _, fw, _ = C.face_frame(pc)
        d = np.zeros_like(pc)
        mouth_cx = (pc[61, 0] + pc[291, 0]) / 2
        if smile:
            for group, k in ((self.LIP_OUTER, 1.0), (self.LIP_INNER, 0.9)):
                for i, w in group.items():
                    out = 1.0 if pc[i, 0] > mouth_cx else -1.0
                    d[i] += (out * 0.018 * w * k * smile * fw, -0.032 * w * k * smile * fw)
            for i in self.CHEEKS:
                out = 1.0 if pc[i, 0] > mouth_cx else -1.0
                d[i] += (out * 0.004 * smile * fw, -0.012 * smile * fw)
        if relax_brow:
            for i in self.BROW_INNER:
                out = 1.0 if pc[i, 0] > mouth_cx else -1.0
                d[i] += (out * 0.008 * relax_brow * fw, -0.018 * relax_brow * fw)
            for i in self.BROW_OUTER:
                d[i] += (0.0, -0.008 * relax_brow * fw)
        target = pc + d
        anchors = C.anchor_points(pc, side)
        warped = C.warp_points(crop, np.vstack([pc, anchors]), np.vstack([target, anchors]), side)
        self.snapshot(f'표정 미소{smile:+.1f} 눈썹{relax_brow:.1f}')
        self.base = paste_back(self.base, warped, M, paste_mask(pc, side, face_size))
        self.pts_checked = False

    MOUTH_STABLE = [6, 168, 197, 195, 5, 4, 1, 33, 133, 362, 263, 234, 454, 93, 323]

    # Face-Aware Liquify: the same sliders as Photoshop's panel, each one a
    # landmark move in face-width units. Values are -100..100 like Photoshop.
    EYE_L_IN = C.LEFT_EYE
    EYE_R_IN = C.RIGHT_EYE
    EYE_L_OUT = [226, 247, 30, 29, 27, 28, 56, 190, 243, 112, 26, 22, 23, 24, 110, 25]
    EYE_R_OUT = [446, 467, 260, 259, 257, 258, 286, 414, 463, 341, 256, 252, 253, 254, 339, 255]
    BROW_L = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
    BROW_R = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
    NOSE_MID = [1, 2, 4, 5, 6, 19, 94, 195, 197, 168]
    NOSE_L = [129, 98, 64, 240, 219, 235, 48, 115, 220, 49, 131]
    NOSE_R = [358, 327, 294, 460, 439, 455, 278, 344, 440, 279, 360]
    LIP_UP_OUT = [409, 270, 269, 267, 0, 37, 39, 40, 185]
    LIP_UP_IN = [415, 310, 311, 312, 13, 82, 81, 80, 191]
    LIP_LO_OUT = [146, 91, 181, 84, 17, 314, 405, 321, 375]
    LIP_LO_IN = [95, 88, 178, 87, 14, 317, 402, 318, 324]
    MOUTH_ALL = [61, 291, 78, 308] + LIP_UP_OUT + LIP_UP_IN + LIP_LO_OUT + LIP_LO_IN
    FOREHEAD = [10, 338, 297, 332, 284, 109, 67, 103, 54, 151, 9, 108, 337, 69, 299]
    CHIN = [152, 148, 176, 377, 400, 378, 149, 175, 199, 200, 18, 421, 201]
    JAW_L = [172, 136, 150, 149, 176, 58, 132]
    JAW_R = [397, 365, 379, 378, 400, 288, 361]
    CHEEK_L = [234, 93, 132, 127, 162, 137, 177, 215, 213, 192]
    CHEEK_R = [454, 323, 361, 356, 389, 366, 401, 435, 433, 416]

    FACE_SHAPE_PARAMS = ['eye_size', 'eye_height', 'eye_width', 'eye_tilt', 'eye_distance', 'nose_height', 'nose_width',
                         'smile', 'upper_lip', 'lower_lip', 'mouth_width', 'mouth_height',
                         'forehead', 'chin_height', 'jawline', 'face_width']

    def edit_face_shape(self, eyes='both', **v):
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        v = {k: float(np.clip(v.get(k, 0) or 0, -100, 100)) / 100 for k in self.FACE_SHAPE_PARAMS}
        if not any(v.values()):
            raise ValueError('바꿀 값이 없습니다')
        from retouch import paste_back, paste_mask
        face_size = 1024
        M, side = C.crop_transform(pts, face_size)
        crop = cv2.warpAffine(self.base, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pc = C.apply_affine(M, pts)
        _, fw, _ = C.face_frame(pc)
        d = np.zeros_like(pc)
        face_cx = pc[C.FACE_OVAL][:, 0].mean()

        def scale_about(idx, centre, sx, sy, w=1.0):
            for i in idx:
                d[i] += ((pc[i] - centre) * (np.array([sx, sy]) - 1)) * w

        def rotate_about(idx, centre, deg, w=1.0):
            R = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), deg, 1.0)
            for i in idx:
                d[i] += (C.apply_affine(R, pc[i][None])[0] - pc[i]) * w

        def shift(idx, dx, dy, w=1.0):
            for i in idx:
                d[i] += (dx * fw * w, dy * fw * w)

        # eyes — 'left' is the image-left eye
        for side_name, inner, outer, brow in (('left', self.EYE_L_IN, self.EYE_L_OUT, self.BROW_L), ('right', self.EYE_R_IN, self.EYE_R_OUT, self.BROW_R)):
            if eyes not in ('both', side_name):
                continue
            c = pc[inner].mean(0)
            out = 1.0 if c[0] > face_cx else -1.0
            sx = 1 + 0.18 * v['eye_size'] + 0.18 * v['eye_width']
            sy = 1 + 0.22 * v['eye_size'] + 0.25 * v['eye_height']
            scale_about(inner, c, sx, sy); scale_about(outer, c, sx, sy, 0.45)
            if v['eye_tilt']:
                # positive tilts the outer corners up on both sides, like Photoshop
                rotate_about(inner + outer, c, out * 10 * v['eye_tilt'])
                rotate_about(brow, c, out * 6 * v['eye_tilt'], 0.5)
            if v['eye_distance']:
                shift(inner + outer + brow, out * 0.035 * v['eye_distance'], 0)
        # nose
        if v['nose_height']:
            shift(self.NOSE_MID + self.NOSE_L + self.NOSE_R, 0, -0.03 * v['nose_height'])
        if v['nose_width']:
            shift(self.NOSE_L, -0.03 * v['nose_width'], 0); shift(self.NOSE_R, 0.03 * v['nose_width'], 0)
            shift([1, 4, 2, 19, 94], 0, 0.004 * v['nose_width'])
        # mouth
        mc = pc[self.MOUTH_ALL].mean(0)
        if v['smile']:
            for group, k in ((self.LIP_OUTER, 1.0), (self.LIP_INNER, 0.9)):
                for i, w in group.items():
                    out = 1.0 if pc[i, 0] > mc[0] else -1.0
                    d[i] += (out * 0.018 * w * k * v['smile'] * fw, -0.032 * w * k * v['smile'] * fw)
            for i in self.CHEEKS:
                out = 1.0 if pc[i, 0] > mc[0] else -1.0
                d[i] += (out * 0.004 * v['smile'] * fw, -0.012 * v['smile'] * fw)
        if v['upper_lip']:
            shift(self.LIP_UP_OUT, 0, -0.012 * v['upper_lip']); shift(self.LIP_UP_IN, 0, 0.004 * v['upper_lip'])
        if v['lower_lip']:
            shift(self.LIP_LO_OUT, 0, 0.012 * v['lower_lip']); shift(self.LIP_LO_IN, 0, -0.004 * v['lower_lip'])
        if v['mouth_width'] or v['mouth_height']:
            scale_about(self.MOUTH_ALL, mc, 1 + 0.15 * v['mouth_width'], 1 + 0.15 * v['mouth_height'])
        # face shape
        if v['forehead']:
            shift(self.FOREHEAD, 0, -0.04 * v['forehead'])
        if v['chin_height']:
            shift(self.CHIN, 0, 0.035 * v['chin_height'])
        if v['jawline']:
            shift(self.JAW_L, -0.03 * v['jawline'], 0); shift(self.JAW_R, 0.03 * v['jawline'], 0)
        if v['face_width']:
            shift(self.CHEEK_L, -0.04 * v['face_width'], 0); shift(self.CHEEK_R, 0.04 * v['face_width'], 0)
            shift(self.JAW_L, -0.02 * v['face_width'], 0); shift(self.JAW_R, 0.02 * v['face_width'], 0)

        target = pc + d
        anchors = C.anchor_points(pc, side)
        warped = C.warp_points(crop, np.vstack([pc, anchors]), np.vstack([target, anchors]), side)
        label = ' '.join(f'{k}{int(round(x * 100)):+d}' for k, x in v.items() if x)
        self.snapshot('얼굴 ' + label[:24])
        self.base = paste_back(self.base, warped, M, paste_mask(pc, side, face_size))
        self.pts_checked = False
        return label

    def edit_mouth_from(self, donor_name):
        """Take the mouth from another photo of the same person — the only way
        to get real teeth into a closed-mouth shot. Aligns on nose, cheeks and
        eye corners (things a smile does not move) and blends the mouth region."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        if not self.args.folder:
            raise ValueError('--folder 없이 시작해서 다른 사진을 열 수 없습니다')
        donor = C.imread(os.path.join(self.args.folder, os.path.basename(donor_name)))
        dpts = self._lm.detect(donor)
        if dpts is None:
            raise ValueError(f'{donor_name} 에서 얼굴을 찾지 못했습니다')
        A, _ = cv2.estimateAffinePartial2D(dpts[self.MOUTH_STABLE], pts[self.MOUTH_STABLE], method=cv2.LMEDS)
        if A is None:
            raise ValueError('두 사진의 얼굴을 맞추지 못했습니다')
        h, w = self.base.shape[:2]
        aligned = cv2.warpAffine(donor, A, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        _, fw, _ = C.face_frame(pts)
        lips = C.apply_affine(A, dpts[C.LIPS])                  # the donor mouth, where it lands
        mask = np.zeros((h, w), np.uint8)
        cv2.fillConvexPoly(mask, cv2.convexHull(np.round(lips).astype(np.int32)), 255)
        mask = cv2.dilate(mask, C._disc(0.07 * fw))
        centre = lips.mean(0)
        self.snapshot(f'입 ← {donor_name}')
        self.base = cv2.seamlessClone(aligned, self.base, mask, (int(centre[0]), int(centre[1])), cv2.NORMAL_CLONE)
        self.pts_checked = False

    def edit_eyes_from(self, donor_name, which='both'):
        """Take the eyes from another photo of the same person (same session,
        similar angle), align them on the stable landmarks around the eyes and
        blend them in."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        if not self.args.folder:
            raise ValueError('--folder 없이 시작해서 다른 사진을 열 수 없습니다')
        donor = C.imread(os.path.join(self.args.folder, os.path.basename(donor_name)))
        dpts = self._lm.detect(donor)
        if dpts is None:
            raise ValueError(f'{donor_name} 에서 얼굴을 찾지 못했습니다')
        A, _ = cv2.estimateAffinePartial2D(dpts[self.STABLE], pts[self.STABLE], method=cv2.LMEDS)
        if A is None:
            raise ValueError('두 사진의 얼굴을 맞추지 못했습니다')
        h, w = self.base.shape[:2]
        aligned = cv2.warpAffine(donor, A, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        _, fw, _ = C.face_frame(pts)
        out = self.base.copy()
        eyes = {'left': (33, 133), 'right': (362, 263)}
        for name, (a, b) in eyes.items():
            if which != 'both' and which != name:
                continue
            c = (pts[a] + pts[b]) / 2
            span = float(np.linalg.norm(pts[b] - pts[a]))
            mask = np.zeros((h, w), np.uint8)
            cv2.ellipse(mask, (int(c[0]), int(c[1])), (int(span * 0.95), int(span * 0.62)), 0, 0, 360, 255, -1)
            out = cv2.seamlessClone(aligned, out, mask, (int(c[0]), int(c[1])), cv2.NORMAL_CLONE)
        self.snapshot(f'눈 ← {donor_name}')
        self.base = out
        self.pts_checked = False

    # ---- Photoshop-style tools: curves, HSL, dodge/burn, clone, vignette,
    #      denoise, background, free liquify, perspective
    @staticmethod
    def curve_lut(points):
        """Monotone cubic (Fritsch–Carlson) through (in, out) points, like the
        Curves dialog. Endpoints default to (0,0) and (255,255)."""
        pts = {0: 0.0, 255: 255.0}
        for x, y in points:
            pts[int(np.clip(x, 0, 255))] = float(np.clip(y, 0, 255))
        x = np.array(sorted(pts), np.float64)
        y = np.array([pts[int(v)] for v in x], np.float64)
        n = len(x)
        h = np.diff(x)
        delta = np.diff(y) / h
        m = np.zeros(n)
        m[0], m[-1] = delta[0], delta[-1]
        for k in range(1, n - 1):
            m[k] = 0.0 if delta[k - 1] * delta[k] <= 0 else (delta[k - 1] + delta[k]) / 2
        for k in range(n - 1):
            if delta[k] == 0:
                m[k] = m[k + 1] = 0.0
            else:
                a, b = m[k] / delta[k], m[k + 1] / delta[k]
                s2 = a * a + b * b
                if s2 > 9:
                    t = 3 / np.sqrt(s2)
                    m[k], m[k + 1] = t * a * delta[k], t * b * delta[k]
        out = np.empty(256)
        for k in range(n - 1):
            sel = np.arange(int(x[k]), int(x[k + 1]) + 1)
            t = (sel - x[k]) / h[k]
            h00, h10, h01, h11 = 2 * t ** 3 - 3 * t ** 2 + 1, t ** 3 - 2 * t ** 2 + t, -2 * t ** 3 + 3 * t ** 2, t ** 3 - t ** 2
            out[sel] = h00 * y[k] + h10 * h[k] * m[k] + h01 * y[k + 1] + h11 * h[k] * m[k + 1]
        return np.clip(out, 0, 255).round().astype(np.uint8)

    def edit_curves(self, rgb=None, red=None, green=None, blue=None):
        luts = [self.curve_lut(blue or []), self.curve_lut(green or []), self.curve_lut(red or [])]
        master = self.curve_lut(rgb or [])
        img = np.empty_like(self.base)
        for c in range(3):
            img[..., c] = master[luts[c][self.base[..., c]]]
        self.snapshot('커브')
        self.base = img

    HSL_CENTRES = {'red': 0, 'orange': 30, 'yellow': 60, 'green': 120, 'aqua': 180, 'blue': 240, 'purple': 270, 'magenta': 300}

    def edit_hsl(self, ranges):
        """Camera Raw's HSL panel: per colour range, shift hue (degrees), scale
        saturation and luminance (-100..100). Ranges overlap with soft edges."""
        hsv = cv2.cvtColor(self.base.astype(np.float32) / 255, cv2.COLOR_BGR2HSV)   # H 0..360
        H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        H2, S2, V2 = H.copy(), S.copy(), V.copy()
        for name, adj in ranges.items():
            if name not in self.HSL_CENTRES:
                raise ValueError(f'모르는 색 범위 {name} (가능: {", ".join(self.HSL_CENTRES)})')
            d = np.abs((H - self.HSL_CENTRES[name] + 180) % 360 - 180)
            w = np.clip(1 - d / 45, 0, 1) * np.clip(S * 4, 0, 1)         # grey pixels are not "a colour"
            H2 += float(adj.get('hue', 0)) * w
            S2 *= 1 + float(adj.get('saturation', 0)) / 100 * w
            V2 *= 1 + float(adj.get('luminance', 0)) / 100 * w * 0.6
        hsv = np.stack([H2 % 360, np.clip(S2, 0, 1), np.clip(V2, 0, 1)], -1)
        self.snapshot('HSL')
        self.base = np.clip(cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR) * 255, 0, 255).round().astype(np.uint8)

    def _soft_disc(self, x, y, radius, feather=0.5):
        h, w = self.base.shape[:2]
        r = max(2.0, float(radius) * w)
        cx, cy = float(x) * w, float(y) * h
        X0, Y0 = int(max(0, cx - 2 * r)), int(max(0, cy - 2 * r))
        X1, Y1 = int(min(w, cx + 2 * r + 1)), int(min(h, cy + 2 * r + 1))
        ys, xs = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        d = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / r
        inner = 1 - float(feather)
        m = np.clip((1 - d) / max(1e-3, 1 - inner), 0, 1)
        m = m * m * (3 - 2 * m)
        return (slice(Y0, Y1), slice(X0, X1)), m[..., None]

    def edit_dodge_burn(self, x, y, radius, amount, feather=0.6, only='all'):
        """Local lighten (amount > 0) or darken (< 0) with a soft round brush.
        only='background' lights the backdrop behind the person (studio glow),
        only='person' keeps the brush off the backdrop."""
        sl, m = self._soft_disc(x, y, radius, feather)
        if only in ('background', 'person'):
            pm = self.person_mask()[sl][..., None]
            m = m * (pm if only == 'person' else 1 - pm)
        roi = self.base[sl].astype(np.float32) / 255
        a = float(np.clip(amount, -1, 1))
        # gamma-space nudge that protects the extremes, like Photoshop's midtones mode
        out = roi ** (1 - 0.6 * a * m) if a >= 0 else roi ** (1 + 0.8 * -a * m)
        self.snapshot('닷지' if a >= 0 else '번')
        img = self.base.copy()
        img[sl] = np.clip(out * 255, 0, 255).round().astype(np.uint8)
        self.base = img

    def edit_clone(self, sx, sy, dx, dy, radius, seamless=True):
        """Clone stamp: copy a soft disc from (sx,sy) onto (dx,dy)."""
        h, w = self.base.shape[:2]
        r = max(3, int(float(radius) * w))
        SX, SY, DX, DY = int(float(sx) * w), int(float(sy) * h), int(float(dx) * w), int(float(dy) * h)
        if not (r <= SX < w - r and r <= SY < h - r and r <= DX < w - r and r <= DY < h - r):
            raise ValueError('도장 영역이 사진 밖으로 나갑니다')
        patch = self.base[SY - r:SY + r, SX - r:SX + r]
        mask = np.zeros((2 * r, 2 * r), np.uint8)
        cv2.circle(mask, (r, r), int(r * 0.85), 255, -1)
        self.snapshot('도장')
        if seamless:
            self.base = cv2.seamlessClone(patch, self.base, mask, (DX, DY), cv2.NORMAL_CLONE)
        else:
            soft = cv2.GaussianBlur(mask, (0, 0), r * 0.15).astype(np.float32)[..., None] / 255
            img = self.base.copy()
            roi = img[DY - r:DY + r, DX - r:DX + r]
            img[DY - r:DY + r, DX - r:DX + r] = (patch * soft + roi * (1 - soft)).round().astype(np.uint8)
            self.base = img

    def edit_vignette(self, amount=-0.4, midpoint=0.5, feather=0.6):
        """Post-crop vignette: amount < 0 darkens the corners, > 0 lightens."""
        h, w = self.base.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        d = np.sqrt(((xs - w / 2) / (w / 2)) ** 2 + ((ys - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
        start = float(midpoint) * (1 - float(feather))
        m = np.clip((d - start) / max(1e-3, float(midpoint) + float(feather) * 0.5 - start), 0, 1)
        m = (m * m * (3 - 2 * m))[..., None]
        a = float(np.clip(amount, -1, 1))
        img = self.base.astype(np.float32) / 255
        out = img ** (1 + 1.5 * -a * m) if a < 0 else 1 - (1 - img) ** (1 + 1.5 * a * m)
        self.snapshot('비네팅')
        self.base = np.clip(out * 255, 0, 255).round().astype(np.uint8)

    def edit_denoise(self, strength=8):
        h = float(np.clip(strength, 1, 30))
        self.snapshot('노이즈 제거')
        self.base = cv2.fastNlMeansDenoisingColored(self.base, None, h, h, 7, 21)

    def person_mask(self, refine=True):
        """Person/background matte from MediaPipe's selfie segmenter (0..1)."""
        if getattr(self, '_seg', None) is None:
            import urllib.request
            import mediapipe as mp
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions
            path = os.path.join(C.HERE, 'models', 'selfie_segmenter.tflite')
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                urllib.request.urlretrieve('https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite', path)
            self._mp = mp
            self._seg = vision.ImageSegmenter.create_from_options(
                vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=path), output_confidence_masks=True))
        small = downscale(self.base, 1024)
        res = self._seg.segment(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB)))
        m = res.confidence_masks[0].numpy_view()[..., 0].astype(np.float32)
        if (m > 0.5).mean() < 0.01:
            raise ValueError('사람을 찾지 못해 배경을 분리할 수 없습니다')
        if refine:
            m = self._grabcut_refine(small, m)
        h, w = self.base.shape[:2]
        m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
        return cv2.GaussianBlur(m, (0, 0), max(1.0, w * 0.003))

    @staticmethod
    def _grabcut_refine(img, m):
        """The selfie model knows people, not hats, props or held objects. Let
        GrabCut's colour model reconsider a wide band around the person: on a
        plain studio backdrop it brings hats and props back."""
        sw = img.shape[1]
        gc = np.full(m.shape, cv2.GC_PR_BGD, np.uint8)
        sure = (m > 0.9).astype(np.uint8)
        band = cv2.dilate(sure, Session._disc_px(int(sw * 0.18)))
        gc[band > 0] = cv2.GC_PR_BGD
        gc[m > 0.6] = cv2.GC_PR_FGD
        gc[cv2.erode(sure, Session._disc_px(int(sw * 0.02))) > 0] = cv2.GC_FGD
        gc[band == 0] = cv2.GC_BGD
        bg, fg = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(img, gc, None, bg, fg, 4, cv2.GC_INIT_WITH_MASK)
        except cv2.error:
            return m
        out = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
        # keep only components touching the original person, drop specks
        n, lab, stats, _ = cv2.connectedComponentsWithStats(out)
        keep = np.zeros_like(out)
        for i in range(1, n):
            if (m[lab == i] > 0.5).any() or stats[i, cv2.CC_STAT_AREA] > 0.002 * out.size and (cv2.dilate((lab == i).astype(np.uint8), Session._disc_px(int(sw * 0.02))) * sure).any():
                keep[lab == i] = 1
        soft = cv2.GaussianBlur(keep.astype(np.float32), (0, 0), max(1.0, sw * 0.002))
        return np.maximum(soft, m * (keep > 0))

    @staticmethod
    def _disc_px(r):
        r = max(1, r)
        return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))

    def edit_background(self, mode='blur', strength=0.5, color='#ffffff', refine=True):
        m = self.person_mask(refine)[..., None]
        h, w = self.base.shape[:2]
        if mode == 'blur':
            k = max(3.0, float(strength) * w * 0.04)
            # blur only background pixels (normalised convolution) so the person
            # does not bleed a halo into the blurred backdrop
            hard = cv2.erode((m[..., 0] < 0.3).astype(np.uint8), self._disc_px(int(w * 0.006))).astype(np.float32)
            inv = hard[..., None]
            num = cv2.GaussianBlur(self.base.astype(np.float32) * inv, (0, 0), k)
            den = cv2.GaussianBlur(hard, (0, 0), k)[..., None]
            bg = num / np.maximum(den, 1e-3)
            out = np.clip(self.base * m + bg * (1 - m), 0, 255).round().astype(np.uint8)
            self.snapshot('배경 흐림')
            self.base = out
        elif mode == 'color':
            c = color.lstrip('#')
            bgr = np.array([int(c[4:6], 16), int(c[2:4], 16), int(c[0:2], 16)], np.float32)
            out = (self.base * m + bgr * (1 - m)).round().astype(np.uint8)
            self.snapshot('배경 단색')
            self.base = out
        elif mode == 'transparent':
            self.snapshot('배경 투명')
            self.alpha = m[..., 0]
        else:
            raise ValueError('mode 는 blur | color | transparent')

    def edit_liquify(self, fx, fy, tx, ty, radius):
        """Photoshop's Forward Warp: push pixels from (fx,fy) toward (tx,ty)."""
        h, w = self.base.shape[:2]
        r = max(4.0, float(radius) * w)
        FX, FY, TX, TY = float(fx) * w, float(fy) * h, float(tx) * w, float(ty) * h
        vx, vy = TX - FX, TY - FY
        if np.hypot(vx, vy) > 2 * r:
            raise ValueError('한 번에 반지름의 2배 넘게 밀 수 없습니다. 나눠서 미세요')
        X0, Y0 = int(max(0, min(FX, TX) - 2 * r)), int(max(0, min(FY, TY) - 2 * r))
        X1, Y1 = int(min(w, max(FX, TX) + 2 * r + 1)), int(min(h, max(FY, TY) + 2 * r + 1))
        ys, xs = np.mgrid[Y0:Y1, X0:X1].astype(np.float32)
        d = np.sqrt((xs - TX) ** 2 + (ys - TY) ** 2) / r      # weight around where pixels end up
        wgt = np.clip(1 - d * d, 0, 1) ** 2
        map_x, map_y = xs - vx * wgt, ys - vy * wgt
        roi = cv2.remap(self.base, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        self.snapshot('리퀴파이')
        img = self.base.copy()
        img[Y0:Y1, X0:X1] = roi
        self.base = img

    def edit_perspective(self, corners):
        """Rectify a quadrilateral (TL, TR, BR, BL as 0..1 points) into a
        straight-on rectangle — signs, price tags, screens."""
        h, w = self.base.shape[:2]
        src = np.array([[float(x) * w, float(y) * h] for x, y in corners], np.float32)
        if src.shape != (4, 2):
            raise ValueError('corners 는 네 점 [TL, TR, BR, BL]')
        tw = int(round((np.linalg.norm(src[1] - src[0]) + np.linalg.norm(src[2] - src[3])) / 2))
        th = int(round((np.linalg.norm(src[3] - src[0]) + np.linalg.norm(src[2] - src[1])) / 2))
        if tw < 16 or th < 16:
            raise ValueError('영역이 너무 작습니다')
        dst = np.array([[0, 0], [tw - 1, 0], [tw - 1, th - 1], [0, th - 1]], np.float32)
        Hm = cv2.getPerspectiveTransform(src, dst)
        self.snapshot('원근 보정')
        self.base = cv2.warpPerspective(self.base, Hm, (tw, th), flags=cv2.INTER_LANCZOS4)
        self.pts_checked = False

    # ---- graduation-portrait checks: level hat, level shoulders
    CLASSES = {'background': 0, 'hair': 1, 'body-skin': 2, 'face-skin': 3, 'clothes': 4, 'others': 5}
    MULTI_URL = 'https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_multiclass_256x256/float32/latest/selfie_multiclass_256x256.tflite'

    def classes(self):
        """Per-pixel class map (hair / skin / clothes / hats & props) at full
        resolution, from MediaPipe's multiclass selfie segmenter."""
        if getattr(self, '_cls_key', None) == id(self.base):
            return self._cls
        if getattr(self, '_mseg', None) is None:
            import urllib.request
            import mediapipe as mp
            from mediapipe.tasks.python import vision
            from mediapipe.tasks.python.core.base_options import BaseOptions
            path = os.path.join(C.HERE, 'models', 'selfie_multiclass_256x256.tflite')
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                urllib.request.urlretrieve(self.MULTI_URL, path)
            self._mp = mp
            self._mseg = vision.ImageSegmenter.create_from_options(
                vision.ImageSegmenterOptions(base_options=BaseOptions(model_asset_path=path), output_confidence_masks=True))
        small = downscale(self.base, 1024)
        res = self._mseg.segment(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=cv2.cvtColor(small, cv2.COLOR_BGR2RGB)))
        h, w = self.base.shape[:2]
        conf = np.stack([cv2.resize(cv2.GaussianBlur(m.numpy_view()[..., 0], (0, 0), 2), (w, h), interpolation=cv2.INTER_LINEAR)
                         for m in res.confidence_masks], -1)
        self._cls = conf.argmax(-1).astype(np.uint8)
        self._cls_key = id(self.base)
        return self._cls

    def _hat_mask(self):
        """The mortarboard and cap: the segmenter's 'others' class above the
        brows; falls back to a dark region there. Must be clearly wider than
        the face, or it is hair, not a hat."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        _, fw, _ = C.face_frame(pts)
        brow_y = int(pts[self.BROW_L + self.BROW_R][:, 1].min())
        cls = self.classes()
        hat = (cls == self.CLASSES['others']).astype(np.uint8)
        hat[brow_y + int(0.15 * fw):] = 0
        hat = cv2.morphologyEx(hat, cv2.MORPH_OPEN, self._disc_px(int(fw * 0.02)))
        n, lab, stats, _ = cv2.connectedComponentsWithStats(hat)
        best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA]) if n > 1 else None
        if best is None or stats[best, cv2.CC_STAT_WIDTH] < 1.3 * fw:
            pm = self.person_mask()
            hsv = cv2.cvtColor(self.base, cv2.COLOR_BGR2HSV)
            hat = ((hsv[..., 2] < 90) & (pm > 0.5) & (cls != self.CLASSES['hair'])).astype(np.uint8)
            hat[brow_y:] = 0
            n, lab, stats, _ = cv2.connectedComponentsWithStats(hat)
            best = max(range(1, n), key=lambda i: stats[i, cv2.CC_STAT_AREA]) if n > 1 else None
            if best is None or stats[best, cv2.CC_STAT_WIDTH] < 1.5 * fw:
                raise ValueError('모자 없음')
        return (lab == best).astype(np.uint8), stats[best]

    def hat_angle(self):
        hat, (x0, y0, bw, bh, _) = self._hat_mask()
        top = np.array([(x, np.argmax(hat[:, x] > 0)) for x in range(x0, x0 + bw) if hat[:, x].any()], np.float32)
        # central 60% only: the board's raised corners and rounded ends bias the fit
        mid = top[int(len(top) * 0.2): int(len(top) * 0.8)]
        mid = mid[np.abs(mid[:, 1] - np.median(mid[:, 1])) < 0.1 * bh]
        vx, vy, _, _ = cv2.fitLine(mid, cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        return float(np.degrees(np.arctan2(vy, vx)))

    def edit_level_hat(self, angle=None):
        """Rotate the flat board of a mortarboard so its top edge is level. Only
        the board moves (the cap stays on the head); the backdrop it uncovers is
        filled from the surroundings."""
        hat, (x0, y0, bw, bh, _) = self._hat_mask()
        if angle is None:
            angle = self.hat_angle()
        if abs(angle) < 0.15:
            return angle
        if abs(angle) > 12:
            raise ValueError(f'모자 기울기 {angle:+.1f}° 는 자동 보정 범위(±12°)를 넘습니다')
        widths = hat.sum(1)
        # the board is the wide thin slab: rows near the maximum width, contiguous from the top
        wide = widths > 0.72 * widths.max()
        first = int(np.argmax(wide))
        last = first
        while last + 1 < len(wide) and wide[last + 1]:
            last += 1
        board_rows = np.arange(first, last + 1)
        if len(board_rows) < 4 or len(board_rows) > 0.6 * bh:
            raise ValueError('모자 판을 찾지 못했습니다 (판이 몸통보다 뚜렷이 넓어야 합니다)')
        region = np.zeros_like(hat)
        lo, hi = board_rows.min(), board_rows.max()
        region[lo: hi + 1] = hat[lo: hi + 1]
        region = cv2.dilate(region, self._disc_px(4))
        ys, xs = np.nonzero(region)
        centre = (float(xs.mean()), float(ys.mean()))
        h, w = self.base.shape[:2]
        # fill the board's old place: backdrop estimate where it was against the
        # backdrop, velvet inpainted where it overlapped the cap; then paste the
        # rotated board on top. (Plain inpainting smears the dark cap upward and
        # leaves a ghost of the old board.)
        hole = cv2.dilate(region, self._disc_px(8))
        pm = self.person_mask()
        bg = cv2.erode((pm < 0.3).astype(np.uint8), self._disc_px(int(w * 0.006))).astype(np.float32)
        bg[hat > 0] = 0
        sig = w * 0.012
        est = cv2.GaussianBlur(self.base.astype(np.float32) * bg[..., None], (0, 0), sig) / np.maximum(cv2.GaussianBlur(bg, (0, 0), sig), 1e-3)[..., None]
        filled = self.base.copy()
        filled[hole > 0] = np.clip(est[hole > 0], 0, 255).round().astype(np.uint8)
        cap = hat.copy(); cap[: board_rows.max() + 1] = 0
        cap_hole = (hole > 0) & (cv2.dilate(cap, self._disc_px(14)) > 0)
        if cap_hole.any():
            X0, Y0 = max(0, xs.min() - 60), max(0, ys.min() - 60)
            X1, Y1 = min(w, xs.max() + 60), min(h, ys.max() + 60)
            velvet = cv2.inpaint(self.base[Y0:Y1, X0:X1], cap_hole[Y0:Y1, X0:X1].astype(np.uint8), 9, cv2.INPAINT_TELEA)
            sub = filled[Y0:Y1, X0:X1]; sub[cap_hole[Y0:Y1, X0:X1]] = velvet[cap_hole[Y0:Y1, X0:X1]]
        R = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rot = cv2.warpAffine(self.base, R, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        rm = cv2.warpAffine(region.astype(np.float32), R, (w, h))
        rm = cv2.GaussianBlur(rm, (0, 0), 2)[..., None]
        self.snapshot(f'모자 수평 {angle:+.1f}°')
        self.base = (filled * (1 - rm) + rot * rm).round().astype(np.uint8)
        return angle

    def shoulder_points(self, offset=0.95):
        """Top of the silhouette at equal distances left and right of the face
        centre — the two points an eye compares when judging the shoulder line."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        cls = self.classes()
        body = (cls == self.CLASSES['clothes'])
        if body.mean() < 0.01:                      # no clothes found (bare shoulders?): whole person minus hair
            body = (self.person_mask() > 0.5) & (cls != self.CLASSES['hair'])
        body = body.astype(np.float32)
        c, fw, _ = C.face_frame(pts)
        chin_y = int(pts[152, 1])
        out = {}
        for name, sign in (('left', -1), ('right', 1)):
            x = int(round(c[0] + sign * offset * fw))
            if not 0 <= x < body.shape[1]:
                raise ValueError('어깨 기준점이 사진 밖입니다')
            win = max(2, int(0.015 * body.shape[1]))
            cols = body[chin_y:, max(0, x - win): x + win + 1] > 0.5
            if not cols.any():
                raise ValueError(f'{name} 어깨를 찾지 못했습니다')
            tops = [int(np.argmax(cols[:, j])) for j in range(cols.shape[1]) if cols[:, j].any()]
            out[name] = (x, chin_y + int(np.median(tops)))
        return out

    def edit_level_shoulders(self, angle=None, left=None, right=None):
        """Rotate the body below the neck so the shoulder line is level. Points
        may be given (0..1 coords) instead of auto-detected."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        h, w = self.base.shape[:2]
        c, fw, _ = C.face_frame(pts)
        if angle is None:
            if left and right:
                (xl, yl), (xr, yr) = (left[0] * w, left[1] * h), (right[0] * w, right[1] * h)
            else:
                sp = self.shoulder_points()
                (xl, yl), (xr, yr) = sp['left'], sp['right']
            angle = float(np.degrees(np.arctan2(yr - yl, xr - xl)))
            shoulder_y = (yl + yr) / 2
        else:
            shoulder_y = pts[152, 1] + 0.9 * fw
        if abs(angle) < 0.15:
            return angle
        if abs(angle) > 10:
            raise ValueError(f'어깨선 기울기 {angle:+.1f}° 는 자동 보정 범위(±10°)를 넘습니다')
        pivot = (float(c[0]), float(pts[152, 1] + 0.3 * fw))          # base of the neck
        y_top = int(max(0, pivot[1] - 0.25 * fw))
        ys, xs = np.mgrid[y_top:h, 0:w].astype(np.float32)
        wgt = np.clip((ys - (pivot[1] - 0.15 * fw)) / max(1.0, shoulder_y - pivot[1] + 0.15 * fw), 0, 1)
        wgt = wgt * wgt * (3 - 2 * wgt)
        R = cv2.getRotationMatrix2D(pivot, angle, 1.0)
        rx = R[0, 0] * xs + R[0, 1] * ys + R[0, 2]
        ry = R[1, 0] * xs + R[1, 1] * ys + R[1, 2]
        map_x = (xs - (rx - xs) * wgt).astype(np.float32)
        map_y = (ys - (ry - ys) * wgt).astype(np.float32)
        out = self.base.copy()
        out[y_top:] = cv2.remap(self.base, map_x, map_y, cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        self.snapshot(f'어깨 수평 {angle:+.1f}°')
        self.base = out
        self.pts_checked = False
        return angle

    def outside_backdrop(self, thresh=3.0, regions=None):
        """Pixels that are neither the person nor the backdrop — stand edges,
        floor, wall. Without `regions` it is a conservative colour test
        (clearly off-backdrop colour, or darker than any backdrop, touching the
        border). With `regions` (0..1 polygons roughly covering what to fill),
        GrabCut refines each polygon against the backdrop's colour model."""
        pts = self.landmarks()
        if pts is None:
            raise ValueError('얼굴을 찾지 못했습니다')
        pm = self.person_mask()
        h, w = self.base.shape[:2]
        c, fw, _ = C.face_frame(pts)
        small = downscale(self.base, 1536)
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
        H, W = lab.shape[:2]
        k = W / w
        pm_s = cv2.resize(pm, (W, H))
        ys, xs = np.mgrid[0:H, 0:W]
        ring = (np.hypot(xs - c[0] * k, ys - c[1] * k) < 2.2 * fw * k) & (pm_s < 0.2)
        if ring.sum() < 500:
            raise ValueError('머리 주변에서 배경 표본을 얻지 못했습니다')
        ref = lab[ring]
        mu, icov = ref.mean(0), np.linalg.inv(np.cov(ref.T) + np.eye(3) * 4)
        d = lab - mu
        maha = np.sqrt(np.einsum('...i,ij,...j->...', d, icov, d))
        too_dark = lab[..., 0] < np.percentile(ref[:, 0], 0.5) - 6
        seed = (((maha > thresh) | too_dark) & (pm_s < 0.3)).astype(np.uint8)
        seed = cv2.morphologyEx(seed, cv2.MORPH_OPEN, self._disc_px(int(W * 0.01)))

        if regions:
            poly = np.zeros((H, W), np.uint8)
            for r in regions:
                cv2.fillPoly(poly, [np.round(np.array(r, np.float32) * [W, H]).astype(np.int32)], 1)
            gc = np.full((H, W), cv2.GC_BGD, np.uint8)
            gc[poly > 0] = cv2.GC_PR_FGD
            gc[(poly > 0) & (seed > 0)] = cv2.GC_FGD
            gc[ring] = cv2.GC_BGD
            gc[pm_s > 0.3] = cv2.GC_BGD
            bgm, fgm = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
            try:
                cv2.grabCut(small, gc, None, bgm, fgm, 4, cv2.GC_INIT_WITH_MASK)
                keep = ((gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)).astype(np.uint8)
            except cv2.error:
                keep = poly
            keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE, self._disc_px(int(W * 0.01)))
            keep[pm_s > 0.3] = 0
        else:
            n, lab_, stats, _ = cv2.connectedComponentsWithStats(seed)
            keep = np.zeros_like(seed)
            for i in range(1, n):
                x0, y0, bw, bh, area = stats[i]
                touches = x0 == 0 or y0 == 0 or x0 + bw == W or y0 + bh == H
                if touches and area > 0.002 * seed.size:
                    keep[lab_ == i] = 1
        keep = cv2.dilate(keep, self._disc_px(int(W * 0.012)))
        return cv2.resize(keep, (w, h), interpolation=cv2.INTER_NEAREST)

    def edit_extend_backdrop(self, thresh=3.0, work=2048, regions=None):
        """Fill everything outside the backdrop with the backdrop's own texture:
        the smooth light falloff is extrapolated, and the mottling is tiled in
        from patches of real backdrop whose brightness matches."""
        hole_full = self.outside_backdrop(thresh, regions)
        if hole_full.mean() < 0.001:
            raise ValueError('배경지 바깥 영역이 없습니다 (이미 프레임을 다 채움)')
        pm_full = self.person_mask()
        h, w = self.base.shape[:2]
        k = min(1.0, work / max(h, w))
        img = downscale(self.base, work).astype(np.float32)
        H, W = img.shape[:2]
        hole = cv2.resize(hole_full, (W, H), interpolation=cv2.INTER_NEAREST)
        person = cv2.resize(pm_full, (W, H)) > 0.3
        src = ((hole == 0) & ~person).astype(np.uint8)
        src = cv2.erode(src, self._disc_px(int(W * 0.015)))
        rng = np.random.default_rng(0)

        srcf = src.astype(np.float32)
        def nblur(a, sig):
            return cv2.GaussianBlur(a * srcf[..., None], (0, 0), sig) / np.maximum(cv2.GaussianBlur(srcf, (0, 0), sig), 1e-4)[..., None]
        # light falloff extrapolated into the hole: use progressively wider
        # blurs where the narrow one has no support, and the global mean beyond
        low = nblur(img, W * 0.06)
        for sig in (W * 0.15, W * 0.4):
            den = cv2.GaussianBlur(srcf, (0, 0), sig * 0.4)
            weak = den < 0.05
            low[weak] = nblur(img, sig)[weak]
        den = cv2.GaussianBlur(srcf, (0, 0), W * 0.16)
        low[den < 0.05] = img[src > 0].mean(0)
        patch = max(32, int(W * 0.14))
        low2 = nblur(img, patch * 0.5)
        tex = (img - low2) * 0.85                     # mottling, valid where src; a touch softer than the real thing

        ok = cv2.erode(src, np.ones((patch, patch), np.uint8))          # patch fully inside source
        cand = np.argwhere(ok[:H - patch, :W - patch] > 0)
        if len(cand) < 20:
            raise ValueError('질감을 가져올 배경이 부족합니다')
        cand_sel = cand[rng.choice(len(cand), min(400, len(cand)), replace=False)] if len(cand) > 400 else cand
        cand_mean = np.array([low2[y:y + patch, x:x + patch].mean() for y, x in cand_sel])
        win = np.hanning(patch)[:, None] * np.hanning(patch)[None, :]
        win = (win + 0.02)[..., None].astype(np.float32)
        acc = np.zeros_like(img); wsum = np.zeros((H, W, 1), np.float32)
        step = patch // 2
        for ty in range(0, H, step):
            for tx in range(0, W, step):
                y1, x1 = min(H, ty + patch), min(W, tx + patch)
                if not hole[ty:y1, tx:x1].any():
                    continue
                target = low[ty:y1, tx:x1].mean()
                j = int(np.argmin(np.abs(cand_mean - target) + rng.normal(0, 3, len(cand_mean))))
                sy, sx = cand_sel[j]
                ph, pw = y1 - ty, x1 - tx
                acc[ty:y1, tx:x1] += (low[ty:y1, tx:x1] + tex[sy:sy + ph, sx:sx + pw]) * win[:ph, :pw]
                wsum[ty:y1, tx:x1] += win[:ph, :pw]
        filled = np.where(wsum > 0, acc / np.maximum(wsum, 1e-4), img)
        soft = cv2.GaussianBlur(hole.astype(np.float32), (0, 0), W * 0.012)[..., None]
        soft = np.maximum(soft, hole[..., None].astype(np.float32))       # the hole itself stays fully filled
        outs = img * (1 - soft) + filled * soft
        outs = np.clip(outs, 0, 255).astype(np.uint8)
        # back to full resolution: only the hole pixels change
        up = cv2.resize(outs, (w, h), interpolation=cv2.INTER_CUBIC)
        m = cv2.GaussianBlur(hole_full.astype(np.float32), (0, 0), w * 0.012)[..., None]
        m = np.maximum(m, hole_full[..., None].astype(np.float32))
        m[pm_full[..., None] > 0.5] = 0                                    # never touch the person
        self.snapshot('배경 확장')
        self.base = (self.base * (1 - m) + up * m).round().astype(np.uint8)
        return float(hole_full.mean())

    def pose_report(self):
        pts = self.landmarks()
        if pts is None:
            return '얼굴 없음'
        bits = [f'고개 {np.degrees(C.face_frame(pts)[2]):+.1f}°']
        try:
            bits.append(f'모자 윗선 {self.hat_angle():+.1f}°')
        except ValueError as e:
            bits.append(str(e) if str(e) == '모자 없음' else f'모자: {e}')
        try:
            sp = self.shoulder_points()
            (xl, yl), (xr, yr) = sp['left'], sp['right']
            bits.append(f'어깨선 {np.degrees(np.arctan2(yr - yl, xr - xl)):+.1f}° (좌 {yl}px, 우 {yr}px)')
        except ValueError as e:
            bits.append(f'어깨: {e}')
        return ' · '.join(bits) + '  (양수 = 오른쪽이 낮음)'

    def save(self, size='orig', name=None):
        from retouch import write_with_exif
        out = self.render(full=True)
        spec = SIZES.get(size)
        if spec:
            sh, sw = out.shape[:2]
            if 'long' in spec:
                tw, th = fitted(sw, sh, spec['long'])
            else:
                tw, th = spec['w'], spec['h']
            k = max(tw / sw, th / sh)
            cw, ch = tw / k, th / k
            x0, y0 = (sw - cw) / 2, (sh - ch) / 2
            M = np.array([[k, 0, -x0 * k], [0, k, -y0 * k]], np.float32)
            out = cv2.warpAffine(out, M, (tw, th), flags=cv2.INTER_AREA)
        os.makedirs(self.args.out, exist_ok=True)
        base = name or (os.path.splitext(self.name)[0] + '-fix')
        if self.alpha is not None and not spec:
            path = os.path.join(self.args.out, base + '.png')
            rgba = np.dstack([out, (self.alpha * 255).round().astype(np.uint8)])
            C.imwrite(path, rgba)
            return path
        path = os.path.join(self.args.out, base + '.jpg')
        write_with_exif(self.src_path, path, out, 95)
        return path

    # ---- chat
    def client(self):
        if self._client is None:
            if self.backend() == 'cli':
                import cli_backend
                self._client = cli_backend.CliClient(
                    self.args.data, model=self.args.model, effort=self.args.effort)
            else:
                import anthropic
                self._client = anthropic.Anthropic()
        return self._client

    def backend(self):
        """'api' = ANTHROPIC_API_KEY 로 직접 호출, 'cli' = 설치된 Claude Code 로 우회."""
        choice = getattr(self.args, 'backend', 'auto')
        if choice != 'auto':
            return choice
        if os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN'):
            return 'api'
        import cli_backend
        return 'cli' if cli_backend.available() else 'api'

    def chat(self, text):
        if self.base is None:
            return '먼저 사진을 여세요.', []
        import anthropic
        import cli_backend
        content = [{'type': 'text', 'text': f'[현재 상태] {self.state_text()}\n[현재 결과 미리보기]'},
                   image_block(self.render(), VIEW_MAX),
                   {'type': 'text', 'text': text}]
        self.messages.append({'role': 'user', 'content': content})
        events = []
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                resp = self.client().messages.create(
                    model=self.args.model, max_tokens=8000,
                    system=[{'type': 'text', 'text': self.system_prompt(), 'cache_control': {'type': 'ephemeral'}}],
                    tools=TOOLS, messages=self.messages,
                    thinking={'type': 'adaptive'}, output_config={'effort': self.args.effort})
                self.messages.append({'role': 'assistant', 'content': resp.content})
                if resp.stop_reason == 'refusal':
                    detail = resp.stop_details.explanation if resp.stop_details else ''
                    return f'(이 요청은 처리하지 않았습니다. {detail})', events
                if resp.stop_reason != 'tool_use':
                    break
                results = []
                for b in resp.content:
                    if b.type != 'tool_use':
                        continue
                    events.append({'tool': b.name, 'input': b.input})
                    try:
                        text_out, img = self.run_tool(b.name, b.input)
                        blocks = [{'type': 'text', 'text': text_out}]
                        if img is not None:
                            blocks.append(image_block(img, TOOL_VIEW_MAX))
                        results.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': blocks})
                    except Exception as e:  # tool errors go back to Claude, not to the user as a crash
                        results.append({'type': 'tool_result', 'tool_use_id': b.id, 'content': f'오류: {e}', 'is_error': True})
                        events[-1]['error'] = str(e)
                self.messages.append({'role': 'user', 'content': results})
            reply = '\n'.join(b.text for b in resp.content if b.type == 'text').strip()
            if resp.stop_reason == 'tool_use':
                reply += '\n(도구 호출 한도에 도달해 여기서 멈췄습니다. 이어서 지시해 주세요.)'
        except cli_backend.CliError as e:
            self.messages.pop()
            return f'Claude CLI 경로에서 막혔습니다: {e}', events
        except anthropic.AuthenticationError:
            self.messages.pop()
            return ('API 키가 없거나 잘못됐습니다. ANTHROPIC_API_KEY 를 설정하거나, '
                    'Claude Code 가 깔려 있으면 --backend cli 로 다시 시작하세요.'), events
        except anthropic.APIConnectionError:
            self.messages.pop()
            return '네트워크 오류로 Claude에 연결하지 못했습니다.', events
        except anthropic.APIStatusError as e:
            self.messages.pop()
            return f'API 오류 {e.status_code}: {e.message}', events
        self.trim_history()
        return reply or '(완료)', events

    def trim_history(self):
        # drop whole leading turns until the history is short and starts at a plain user message
        while len(self.messages) > HISTORY_MESSAGES:
            self.messages.pop(0)
            while self.messages and not (self.messages[0]['role'] == 'user' and isinstance(self.messages[0]['content'], list)
                                         and self.messages[0]['content'] and self.messages[0]['content'][0].get('type') == 'text'):
                self.messages.pop(0)
        # keep only the last few user-turn images; older ones become placeholders
        seen = 0
        for m in reversed(self.messages):
            if m['role'] != 'user' or not isinstance(m['content'], list):
                continue
            has_img = any(b.get('type') == 'image' or (b.get('type') == 'tool_result' and isinstance(b.get('content'), list)
                                                      and any(c.get('type') == 'image' for c in b['content'])) for b in m['content'])
            if not has_img:
                continue
            seen += 1
            if seen <= IMAGE_TURNS_KEPT:
                continue
            for b in m['content']:
                if b.get('type') == 'image':
                    b.clear(); b.update({'type': 'text', 'text': '[이전 미리보기 생략]'})
                elif b.get('type') == 'tool_result' and isinstance(b.get('content'), list):
                    b['content'] = [c if c.get('type') != 'image' else {'type': 'text', 'text': '[이전 미리보기 생략]'} for c in b['content']]

    def system_prompt(self):
        presets = ', '.join(f"{p['id']}({p['name']})" for p in self.presets)
        return f"""당신은 사진 보정 프로그램 안에 있는 보정 조수입니다. 사용자는 블로그용 사진을 한 장씩 다듬고 있고, 야간에 혼자 일하므로 답은 짧게, 한국어로 합니다.

매 턴 [현재 상태]와 현재 결과 미리보기를 받습니다. 도구로 사진을 고치면 도구 결과에 새 미리보기가 옵니다. 반드시 그 미리보기로 실제로 바뀌었는지 확인한 뒤 한두 문장으로 보고하세요. 원하는 대로 안 됐으면 다시 시도하되(최대 2번), 안 되는 건 안 된다고 말합니다.

좌표: x, y는 이미지 폭·높이에 대한 0~1 비율, 원점은 왼쪽 위. 작은 대상(잡티, 점)은 위치 확신이 없으면 zoom 으로 먼저 확대해 보고 heal 하세요. heal 은 지정 위치 주변에서 가장 잡티다운 점으로 자동 보정(snap)됩니다.

색 조정(adjust) 값의 뜻: exposure(-2~2, EV) · contrast(0.5~2, 1이 기본) · highlights/shadows(-100~100, 밝은/어두운 영역만) · temp(-100 차갑게 ~ 100 따뜻하게) · tint(-100 녹색 ~ 100 자홍) · saturation · vibrance(이미 진한 색은 덜 올림) · sharpen(0~100). 사용자가 "조금"이라고 하면 작은 단계(노출 0.1~0.2, 나머지 10~20)로 움직입니다.
프리셋: {presets}. none 은 프리셋 해제.
auto_levels 는 채널별 히스토그램을 펴서 색 틀어짐·뿌연 느낌을 잡는 출발점입니다.
smooth_skin 은 얼굴 피부만 부드럽게(0~1). face_models 는 사용자의 보정 쌍으로 학습된 얼굴형·질감 모델이며 [현재 상태]에 "있음"일 때만 씁니다.
crop 은 0~1 비율 상자. 블로그 규격(1200×630, 1080×1080, 가로 1600)은 save 의 size 로 처리되며 가운데 기준으로 잘립니다.

배경지가 프레임을 다 못 채워 스탠드·바닥·벽이 보이면 extend_backdrop 으로 먼저 채운 뒤 crop 합니다(사용자 보정본은 배경을 늘려 넓게 남기는 편).
학사모 사진(주 작업): 기준은 모자 수평 · 고개 수직 · 어깨선 좌우 일치 · 옷매무새. 먼저 pose_check 로 세 각도를 재고, level_hat → head_tilt → level_shoulders 순으로 맞춘 뒤(각 단계 미리보기 확인), 옷 주름·좌우 비대칭은 liquify 로 정리하고, crop → smooth_skin → 색 순서로 마무리합니다. 배경 흐림(background blur)은 기본 순서에 넣지 않습니다 — 사용자가 따로 시킬 때만. 머리 뒤 글로우(dodge_burn only=background)도 요청이 있을 때만.
기울기: 카메라가 기울어 사진 전체가 삐딱하면 straighten, 몸은 바른데 고개만 갸웃하면 head_tilt(±12°까지, 그 이상은 못 한다고 말할 것). 눈 감은 사진은 eyes_from 으로 같은 사람의 다른 사진에서 눈을 가져오는 방법뿐입니다 — 없는 눈을 만들어내지는 못하니, 사용자가 donor 사진을 지정하지 않았으면 폴더의 다른 사진 중 무엇을 쓸지 물어보세요.
얼굴 리퀴파이(face_shape)는 포토샵 얼굴 인식 리퀴파이와 같은 슬라이더입니다. "눈 좀 크게" → eye_size 25, "턱 갸름하게" → jawline -30 face_width -15 식으로 작은 값부터, 결과를 보고 올립니다.
표정: 살짝 미소·인상 풀기는 expression(워핑). 이가 보이는 활짝 웃음은 워핑으로 안 되고 mouth_from 으로 같은 사람의 웃는 컷에서 입을 가져오는 방법뿐입니다. 없는 이를 만들어내지는 못한다고 분명히 말하세요. 표정을 바꾼 뒤엔 미리보기를 보고 부자연스러우면 강도를 낮추거나 undo 합니다.
포토샵식 도구: curves(커브) · hsl(색 범위별 색조/채도/명도) · dodge_burn(국소 밝기) · clone(도장) · vignette · denoise(야간 노이즈) · background(배경 흐림/단색/투명) · liquify(자유 밀기) · perspective(간판·가격표 펴기). 사용자가 포토샵 용어로 말하면 대응되는 도구를 고르고, 국소 도구는 zoom 으로 위치를 확인한 뒤 씁니다.
저장(save)은 사용자가 저장하라고 할 때만 합니다. 되돌리기는 undo. 요청이 애매하면 한 줄로 되묻습니다. 사진 속 인물에 대한 평가는 하지 않습니다."""

    def run_tool(self, name, inp):
        with self.lock:
            if name == 'adjust':
                self.snapshot('색 조정')
                if inp.get('reset'):
                    self.params = dict(color.DEFAULTS)
                for k, v in inp.items():
                    if k in color.RANGES:
                        self.params[k] = v
                self.params = color.clamp_params(self.params)
                return f'조정 적용: {self.state_text()}', self.render()
            if name == 'auto_levels':
                self.snapshot('자동 보정')
                self.auto = bool(inp.get('enabled', True))
                return f"자동 보정 {'켬' if self.auto else '끔'}", self.render()
            if name == 'preset':
                pid = inp.get('id', 'none')
                p = next((x for x in self.presets if x['id'] == pid), None)
                if pid != 'none' and p is None:
                    raise ValueError(f'프리셋 {pid} 없음')
                self.snapshot('프리셋')
                self.preset = None if pid == 'none' else pid
                if p and 'params' in p:
                    self.params = color.clamp_params({**color.DEFAULTS, **p['params']})
                elif pid == 'none':
                    self.params = dict(color.DEFAULTS)
                return f'프리셋 {pid} 적용', self.render()
            if name == 'zoom':
                h, w = self.base.shape[:2]
                size = float(inp.get('size', 0.25))
                cx, cy = float(inp['x']) * w, float(inp['y']) * h
                half = size * w / 2
                x0, y0 = int(max(0, cx - half)), int(max(0, cy - half))
                x1, y1 = int(min(w, cx + half)), int(min(h, cy + half))
                crop = self.render(full=True)[y0:y1, x0:x1]
                return (f'확대 영역: x {x0 / w:.3f}~{x1 / w:.3f}, y {y0 / h:.3f}~{y1 / h:.3f} '
                        f'(이 확대 이미지 안의 위치 u,v 는 전체 좌표 x = {x0 / w:.3f} + u×{(x1 - x0) / w:.3f}, y = {y0 / h:.3f} + v×{(y1 - y0) / h:.3f})'), crop
            if name == 'heal':
                done = self.edit_heal(inp['spots'], inp.get('snap', True))
                return '힐링 위치: ' + ', '.join(f'({x:.3f}, {y:.3f})' for x, y in done), self.render()
            if name == 'smooth_skin':
                self.edit_smooth(float(inp.get('amount', 0.5)))
                return '피부 보정 적용', self.render()
            if name == 'face_models':
                self.edit_face_models(float(inp.get('geom_strength', 1.0)), float(inp.get('tex_strength', 1.0)))
                return '얼굴 모델 적용', self.render()
            if name == 'crop':
                self.edit_crop(float(inp['x0']), float(inp['y0']), float(inp['x1']), float(inp['y1']))
                return f'크롭: {self.base.shape[1]}×{self.base.shape[0]}px', self.render()
            if name == 'extend_backdrop':
                frac = self.edit_extend_backdrop(float(inp.get('threshold', 3.0)), regions=inp.get('regions'))
                return f'배경지 바깥 {frac * 100:.0f}% 를 배경 질감으로 채움', self.render()
            if name == 'pose_check':
                return self.pose_report(), None
            if name == 'level_hat':
                a = self.edit_level_hat(inp.get('angle'))
                return f'모자 판을 {a:+.1f}° 돌려 수평으로', self.render()
            if name == 'level_shoulders':
                a = self.edit_level_shoulders(inp.get('angle'), inp.get('left'), inp.get('right'))
                return f'어깨선 {a:+.1f}° 를 수평으로 (목 아래 몸통 회전)', self.render()
            if name == 'straighten':
                a = self.edit_straighten(inp.get('angle'))
                return f'사진 전체를 {a:+.1f}° 돌려 수평을 맞추고 가장자리를 잘라냈습니다: {self.base.shape[1]}×{self.base.shape[0]}px', self.render()
            if name == 'head_tilt':
                a = self.edit_head_tilt(inp.get('angle'))
                return f'고개만 {a:+.1f}° 돌렸습니다', self.render()
            if name == 'eyes_from':
                self.edit_eyes_from(inp['donor'], inp.get('which', 'both'))
                return f"{inp['donor']} 의 눈을 옮겨 붙였습니다", self.render()
            if name == 'face_shape':
                label = self.edit_face_shape(inp.pop('eyes', 'both'), **inp)
                return f'얼굴 리퀴파이 적용: {label}', self.render()
            if name == 'expression':
                self.edit_expression(float(inp.get('smile', 0)), float(inp.get('relax_brow', 0)))
                return '표정 워핑 적용', self.render()
            if name == 'mouth_from':
                self.edit_mouth_from(inp['donor'])
                return f"{inp['donor']} 의 입을 옮겨 붙였습니다", self.render()
            if name == 'curves':
                self.edit_curves(inp.get('rgb'), inp.get('red'), inp.get('green'), inp.get('blue'))
                return '커브 적용', self.render()
            if name == 'hsl':
                self.edit_hsl(inp['ranges'])
                return 'HSL 적용', self.render()
            if name == 'dodge_burn':
                self.edit_dodge_burn(inp['x'], inp['y'], inp.get('radius', 0.08), inp['amount'], inp.get('feather', 0.6), inp.get('only', 'all'))
                return '닷지/번 적용', self.render()
            if name == 'clone':
                self.edit_clone(inp['from_x'], inp['from_y'], inp['to_x'], inp['to_y'], inp.get('radius', 0.03), inp.get('seamless', True))
                return '도장 적용', self.render()
            if name == 'vignette':
                self.edit_vignette(inp.get('amount', -0.4), inp.get('midpoint', 0.5), inp.get('feather', 0.6))
                return '비네팅 적용', self.render()
            if name == 'denoise':
                self.edit_denoise(inp.get('strength', 8))
                return '노이즈 제거 적용', self.render()
            if name == 'background':
                self.edit_background(inp.get('mode', 'blur'), inp.get('strength', 0.5), inp.get('color', '#ffffff'), inp.get('refine', True))
                return ('배경 분리됨 — 저장하면 투명 PNG' if inp.get('mode') == 'transparent' else '배경 처리 적용'), self.render()
            if name == 'liquify':
                self.edit_liquify(inp['from_x'], inp['from_y'], inp['to_x'], inp['to_y'], inp.get('radius', 0.06))
                return '리퀴파이 적용', self.render()
            if name == 'perspective':
                self.edit_perspective(inp['corners'])
                return f'원근 보정: {self.base.shape[1]}×{self.base.shape[0]}px', self.render()
            if name == 'undo':
                label = self.undo()
                return (f'되돌림: {label}' if label else '되돌릴 것이 없음'), self.render()
            if name == 'save':
                path = self.save(inp.get('size', 'orig'), inp.get('name'))
                return f'저장됨: {path}', None
            raise ValueError(f'모르는 도구 {name}')


PT = {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}}

TOOLS = [
    {'name': 'adjust', 'description': '색·밝기 슬라이더를 바꾼다. 준 항목만 바뀌고 나머지는 유지. reset=true 면 전부 기본값으로.',
     'input_schema': {'type': 'object', 'properties': {
         **{k: {'type': 'number', 'minimum': lo, 'maximum': hi} for k, (lo, hi) in color.RANGES.items()},
         'reset': {'type': 'boolean'}}}},
    {'name': 'auto_levels', 'description': '자동 보정(채널별 레벨 스트레치)을 켜거나 끈다.',
     'input_schema': {'type': 'object', 'properties': {'enabled': {'type': 'boolean'}}, 'required': ['enabled']}},
    {'name': 'preset', 'description': '프리셋을 적용한다. id="none" 이면 해제.',
     'input_schema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}},
    {'name': 'zoom', 'description': '사진 일부를 확대해서 본다(수정 아님). size 는 이미지 폭 대비 한 변 비율.',
     'input_schema': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'size': {'type': 'number', 'default': 0.25}}, 'required': ['x', 'y']}},
    {'name': 'heal', 'description': '잡티·점·작은 얼룩을 주변 질감으로 메운다. radius 는 이미지 폭 대비 비율(기본 0.012). snap=false 면 지정 위치 그대로.',
     'input_schema': {'type': 'object', 'properties': {
         'spots': {'type': 'array', 'items': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'radius': {'type': 'number'}}, 'required': ['x', 'y']}},
         'snap': {'type': 'boolean'}}, 'required': ['spots']}},
    {'name': 'smooth_skin', 'description': '얼굴 피부를 부드럽게 한다. amount 0~1 (0.3 가벼움, 0.6 보통, 1 강함).',
     'input_schema': {'type': 'object', 'properties': {'amount': {'type': 'number', 'minimum': 0, 'maximum': 1}}, 'required': ['amount']}},
    {'name': 'face_models', 'description': '사용자의 보정 쌍으로 학습된 얼굴형(geom)·질감(tex) 모델을 적용한다. 강도 0~1.5, 1이 학습된 그대로.',
     'input_schema': {'type': 'object', 'properties': {'geom_strength': {'type': 'number'}, 'tex_strength': {'type': 'number'}}}},
    {'name': 'crop', 'description': '0~1 비율 상자로 자른다.',
     'input_schema': {'type': 'object', 'properties': {k: {'type': 'number'} for k in ('x0', 'y0', 'x1', 'y1')}, 'required': ['x0', 'y0', 'x1', 'y1']}},
    {'name': 'extend_backdrop', 'description': '배경지가 프레임을 다 못 채운 사진(배경지 가장자리·스탠드·바닥·벽이 보임)에서 그 바깥을 배경지 자체의 질감과 명암으로 채워 프레임 끝까지 늘린다. regions 에 채울 곳을 0~1 다각형 목록으로 대략 그려 주면(모서리 삼각형, 좌우 띠, 아래 띠 등 — 넉넉하게, 인물은 자동 제외) 경계는 색으로 다듬는다. regions 없이 부르면 색으로만 보수적으로 찾는데 배경지의 어두운 테두리와 검은 스탠드가 이어진 사진에선 놓치므로, 미리보기를 보고 남은 곳을 regions 로 다시 부를 것.',
     'input_schema': {'type': 'object', 'properties': {'threshold': {'type': 'number'}, 'regions': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}, 'minItems': 3}}}}},
    {'name': 'pose_check', 'description': '학사모 사진 점검: 고개 기울기, 모자 윗선 기울기, 어깨선 기울기를 잰다(수정 아님). 양수 = 오른쪽이 낮음.',
     'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'level_hat', 'description': '학사모 판을 돌려 윗선을 수평으로. angle 생략 시 자동 측정값. 판만 움직이고 모자 몸통은 그대로.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}}}},
    {'name': 'level_shoulders', 'description': '어깨선을 수평으로: 목 아래 몸통을 회전(머리는 그대로). angle 생략 시 좌우 같은 거리의 실루엣 높이로 자동 측정. left/right 에 [x,y](0~1)를 주면 그 두 점을 어깨선으로 쓴다.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}, 'left': {'type': 'array', 'items': {'type': 'number'}}, 'right': {'type': 'array', 'items': {'type': 'number'}}}}},
    {'name': 'straighten', 'description': '사진 전체를 돌려 눈높이를 수평으로 맞추고(카메라가 기울어진 경우) 빈 모서리를 잘라낸다. angle 을 생략하면 얼굴에서 잰 기울기만큼, 주면 그 각도(도, 양수=반시계)만큼.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}}}},
    {'name': 'head_tilt', 'description': '몸은 그대로 두고 고개만 돌린다(±12° 까지). angle 생략 시 눈높이가 수평이 되게. 목·머리카락 주변이 함께 늘어나므로 작은 각도에서만 자연스럽다.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}}}},
    {'name': 'eyes_from', 'description': '같은 사람의 다른 사진(donor, 폴더 안 파일명)에서 눈을 가져와 붙인다. 눈 감은 사진 구제용. 같은 촬영·비슷한 각도의 사진이어야 한다. which: both | left | right.',
     'input_schema': {'type': 'object', 'properties': {'donor': {'type': 'string'}, 'which': {'type': 'string', 'enum': ['both', 'left', 'right']}}, 'required': ['donor']}},
    {'name': 'face_shape', 'description': '포토샵 얼굴 인식 리퀴파이. 값은 -100~100, 0=그대로. 눈: eye_size · eye_height · eye_width · eye_tilt(+ 눈꼬리 올림) · eye_distance(+ 멀어짐), eyes=both|left|right(left=사진 왼쪽 눈). 코: nose_height(+ 위로) · nose_width. 입: smile · upper_lip(+ 두껍게) · lower_lip · mouth_width · mouth_height. 얼굴형: forehead(+ 이마 높게) · chin_height(+ 턱 길게) · jawline(+ 턱선 넓게, - 갸름) · face_width(+ 넓게, - 갸름). 20~40 정도가 자연스럽고 60 넘으면 티가 난다.',
     'input_schema': {'type': 'object', 'properties': {**{k: {'type': 'number', 'minimum': -100, 'maximum': 100} for k in Session.FACE_SHAPE_PARAMS}, 'eyes': {'type': 'string', 'enum': ['both', 'left', 'right']}}}},
    {'name': 'expression', 'description': '표정을 워핑으로 바꾼다(픽셀을 새로 만들지 않음). smile -1~1: 입꼬리·볼을 올려 살짝 미소(0.3 은은, 0.6 분명, 1 최대 — 그 이상은 부자연). relax_brow 0~1: 찌푸린 눈썹 사이를 벌리고 올려 인상을 푼다. 입을 벌리거나 이를 보이게는 못 한다.',
     'input_schema': {'type': 'object', 'properties': {'smile': {'type': 'number', 'minimum': -1, 'maximum': 1}, 'relax_brow': {'type': 'number', 'minimum': 0, 'maximum': 1}}}},
    {'name': 'mouth_from', 'description': '같은 사람의 다른 사진(donor, 폴더 안 파일명)에서 입을 가져와 붙인다. 이 보이는 웃음은 이 방법뿐(진짜 이가 필요). 같은 촬영·비슷한 각도여야 하고, 볼·눈은 안 바뀌므로 자연스러운지 결과를 꼭 확인할 것.',
     'input_schema': {'type': 'object', 'properties': {'donor': {'type': 'string'}}, 'required': ['donor']}},
    {'name': 'curves', 'description': '커브. 각 채널에 [입력, 출력](0~255) 점 목록. 끝점 (0,0),(255,255) 는 자동. 예: 미드톤 살짝 밝게 rgb=[[128,140]], 검정 들어올리기 rgb=[[0,15]], S자 대비 rgb=[[64,54],[192,202]].',
     'input_schema': {'type': 'object', 'properties': {k: PT for k in ('rgb', 'red', 'green', 'blue')}}},
    {'name': 'hsl', 'description': 'Camera Raw HSL. 색 범위(red orange yellow green aqua blue purple magenta)별로 hue(도, ±30 정도) · saturation(-100~100) · luminance(-100~100). 피부는 orange, 하늘은 blue/aqua.',
     'input_schema': {'type': 'object', 'properties': {'ranges': {'type': 'object', 'additionalProperties': {'type': 'object', 'properties': {'hue': {'type': 'number'}, 'saturation': {'type': 'number'}, 'luminance': {'type': 'number'}}}}}, 'required': ['ranges']}},
    {'name': 'dodge_burn', 'description': '둥근 브러시로 국소 밝기 조정. amount > 0 닷지(밝게), < 0 번(어둡게), -1~1. radius 는 폭 대비 비율(기본 0.08). only=background 면 인물 뒤 배경만 밝힌다(스튜디오 배경 글로우: 머리 뒤에 radius 0.5, amount 0.5 정도), only=person 은 인물만.',
     'input_schema': {'type': 'object', 'properties': {'x': {'type': 'number'}, 'y': {'type': 'number'}, 'radius': {'type': 'number'}, 'amount': {'type': 'number'}, 'feather': {'type': 'number'}, 'only': {'type': 'string', 'enum': ['all', 'background', 'person']}}, 'required': ['x', 'y', 'amount']}},
    {'name': 'clone', 'description': '도장 툴. (from) 위치의 둥근 조각을 (to) 위치에 붙인다. 잡티보다 큰 것(머리카락 한 가닥, 벽의 얼룩)에. seamless=true 면 색을 주변에 맞춤.',
     'input_schema': {'type': 'object', 'properties': {'from_x': {'type': 'number'}, 'from_y': {'type': 'number'}, 'to_x': {'type': 'number'}, 'to_y': {'type': 'number'}, 'radius': {'type': 'number'}, 'seamless': {'type': 'boolean'}}, 'required': ['from_x', 'from_y', 'to_x', 'to_y']}},
    {'name': 'vignette', 'description': '비네팅. amount -1~1 (음수 = 모서리 어둡게, 보통 -0.3~-0.5), midpoint 0~1, feather 0~1.',
     'input_schema': {'type': 'object', 'properties': {'amount': {'type': 'number'}, 'midpoint': {'type': 'number'}, 'feather': {'type': 'number'}}}},
    {'name': 'denoise', 'description': '노이즈 제거(비지역 평균). strength 1~30, 야간 사진은 6~12. 큰 사진은 수십 초 걸린다.',
     'input_schema': {'type': 'object', 'properties': {'strength': {'type': 'number'}}}},
    {'name': 'background', 'description': '사람/배경 분리. mode: blur(배경 흐림, strength 0~1) | color(단색 배경, color "#rrggbb") | transparent(저장 시 투명 PNG). 사람 사진에만.',
     'input_schema': {'type': 'object', 'properties': {'mode': {'type': 'string', 'enum': ['blur', 'color', 'transparent']}, 'strength': {'type': 'number'}, 'color': {'type': 'string'}, 'refine': {'type': 'boolean', 'description': '모자·소품을 색으로 되찾는 GrabCut 보정 (기본 true). 배경이 복잡하면 false'}}, 'required': ['mode']}},
    {'name': 'liquify', 'description': '자유 리퀴파이(앞으로 밀기). (from) 의 픽셀을 (to) 쪽으로 radius(폭 대비, 기본 0.06) 브러시로 민다. 한 번에 반지름의 2배까지. 턱선·어깨·옷 주름 등.',
     'input_schema': {'type': 'object', 'properties': {'from_x': {'type': 'number'}, 'from_y': {'type': 'number'}, 'to_x': {'type': 'number'}, 'to_y': {'type': 'number'}, 'radius': {'type': 'number'}}, 'required': ['from_x', 'from_y', 'to_x', 'to_y']}},
    {'name': 'perspective', 'description': '원근 보정. 네 모서리 [TL, TR, BR, BL] (0~1) 를 정면 직사각형으로 펴고 그 영역만 남긴다. 간판·가격표·화면 촬영에.',
     'input_schema': {'type': 'object', 'properties': {'corners': {'type': 'array', 'items': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}, 'minItems': 4, 'maxItems': 4}}, 'required': ['corners']}},
    {'name': 'undo', 'description': '마지막 수정을 되돌린다.', 'input_schema': {'type': 'object', 'properties': {}}},
    {'name': 'save', 'description': '결과를 저장한다. size: orig | 1600 (가로 1600) | 1200x630 (영문 썸네일) | 1080x1080 (국내 썸네일).',
     'input_schema': {'type': 'object', 'properties': {'size': {'type': 'string', 'enum': list(SIZES)}, 'name': {'type': 'string'}}}},
]


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    session = None
    html = ''

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _previews(self):
        s = self.session
        if s.base is None:
            return {'state': s.state()}
        return {'state': s.state(), 'orig': jpeg_b64(downscale(s.orig, PREVIEW_MAX)), 'out': jpeg_b64(s.render())}

    def _get(self):
        if self.path.split('?')[0] == '/':
            body = self.html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/api/list':
            self._json({**self.session.listing(), 'state': self.session.state(),
                        'presets': [{'id': p['id'], 'name': p['name']} for p in self.session.presets]})
        elif self.path.startswith('/api/thumb?'):
            name = urllib.parse.unquote(self.path.split('?', 1)[1])
            path = self.session.path_of(name)
            try:
                buf = self.session.thumb(path)
            except Exception as e:
                # 한 장이 깨졌다고 연결을 끊으면 필름스트립 전체가 멈춘 것처럼 보인다.
                # 다만 조용히 404 만 내면 원인을 못 찾으므로 콘솔에는 남긴다.
                print('썸네일 실패: %s — %s' % (path, e), file=sys.stderr)
                return self.send_error(404)
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(buf)))
            self.send_header('Cache-Control', 'max-age=3600')
            self.end_headers()
            self.wfile.write(buf)
        else:
            self.send_error(404)

    def do_GET(self):
        # do_POST 와 달리 GET 에는 예외 처리가 없어서, 파일 하나가 깨지면 응답 없이
        # 연결만 닫혔다(브라우저에는 '사이트에 연결할 수 없음'으로 보인다).
        try:
            self._get()
        except Exception as e:
            try:
                self.send_error(500, explain=str(e))
            except Exception:
                pass

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(n) if n else b''
        s = self.session
        try:
            if self.path == '/api/upload':
                name = urllib.parse.unquote(self.headers.get('X-Filename', 'photo.jpg'))
                with s.lock:
                    s.open(data=raw, name=os.path.basename(name))
                return self._json(self._previews())
            body = json.loads(raw.decode('utf-8')) if raw else {}
            if self.path == '/api/folder':
                # 하위 폴더로 내려가거나(name), 상위로 되돌아간다(up=true).
                with s.lock:
                    if body.get('up'):
                        s.set_folder(os.path.dirname(os.path.normpath(s.args.folder)))
                    else:
                        s.set_folder(os.path.join(s.args.folder, os.path.basename(body['name'])))
                    listing = s.listing()
                return self._json({**listing, 'state': s.state()})
            if self.path == '/api/browse':
                with s.lock:
                    listing = s.browse(body.get('mode') or 'folder')
                return self._json({**listing, 'state': s.state()})
            if self.path == '/api/open':
                path = s.path_of(body['name'])
                with s.lock:
                    s.open(path=path)
                return self._json(self._previews())
            if s.base is None:
                return self._json({'error': '열린 사진이 없습니다'}, 400)
            if self.path == '/api/params':
                with s.lock:
                    s.snapshot('색 조정')
                    s.params = color.clamp_params({**s.params, **body.get('params', {})})
                    if 'auto' in body:
                        s.auto = bool(body['auto'])
                    if 'preset' in body:
                        s.run_tool('preset', {'id': body['preset'] or 'none'})
                return self._json(self._previews())
            if self.path == '/api/undo':
                with s.lock:
                    s.undo()
                return self._json(self._previews())
            if self.path == '/api/save':
                with s.lock:
                    path = s.save(body.get('size', 'orig'))
                return self._json({'path': path, **self._previews()})
            if self.path == '/api/chat':
                reply, events = s.chat(body.get('text', '').strip())
                return self._json({'reply': reply, 'events': events, **self._previews()})
            self.send_error(404)
        except Exception as e:
            self._json({'error': str(e)}, 500)


def port_taken(port, host='127.0.0.1'):
    """윈도의 ThreadingHTTPServer 는 allow_reuse_address 때문에 이미 점유된 포트에도
    예외 없이 바인드된다. 그러면 요청이 먼저 붙은 프로그램으로 가는데 아무 신호도 없다.
    그래서 바인드 결과가 아니라 '연결이 되는가'로 점유를 판정한다."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--folder', help='한 장씩 열 사진 폴더 (필름스트립에 나옴)')
    ap.add_argument('--out', default='retouched', help='저장 폴더')
    ap.add_argument('--data', default='work', help='학습 결과 폴더 (geom.npz, tex.pt)')
    ap.add_argument('--presets-js')
    ap.add_argument('--device')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--backend', default='auto', choices=['auto', 'api', 'cli'],
                    help='auto=키가 있으면 API, 없으면 설치된 Claude Code(claude -p)로 우회')
    ap.add_argument('--port', type=int, default=8792)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    if port_taken(args.port):
        sys.exit(f'포트 {args.port} 는 이미 다른 프로그램이 쓰고 있습니다. --port 로 다른 번호를 주세요.\n'
                 f'(윈도에서는 이 충돌이 오류 없이 통과해, 요청이 남의 서버로 가 버립니다.)')

    Handler.session = Session(args)
    Handler.html = open(os.path.join(C.HERE, 'studio.html'), encoding='utf-8').read()
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'스튜디오: {url}   (Ctrl+C 로 종료)')
    backend = Handler.session.backend()
    if backend == 'cli':
        print('대화창: 설치된 Claude Code 로 돌립니다 (API 키 불필요). 한 턴 10~15초 걸리고 구독 사용량을 씁니다.')
    elif os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('ANTHROPIC_AUTH_TOKEN'):
        print('대화창: ANTHROPIC_API_KEY 로 직접 호출합니다.')
    else:
        print('대화창을 쓸 수 없습니다: ANTHROPIC_API_KEY 도 없고 claude 명령도 찾지 못했습니다.')
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
