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
        path = os.path.join(self.args.out, base + '.jpg')
        write_with_exif(self.src_path, path, out, 95)
        return path

    # ---- chat
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def chat(self, text):
        if self.base is None:
            return '먼저 사진을 여세요.', []
        import anthropic
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
        except anthropic.AuthenticationError:
            self.messages.pop()
            return 'API 키가 없거나 잘못됐습니다. 터미널에서 ANTHROPIC_API_KEY 를 설정하고 studio.py 를 다시 시작하세요.', events
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

기울기: 카메라가 기울어 사진 전체가 삐딱하면 straighten, 몸은 바른데 고개만 갸웃하면 head_tilt(±12°까지, 그 이상은 못 한다고 말할 것). 눈 감은 사진은 eyes_from 으로 같은 사람의 다른 사진에서 눈을 가져오는 방법뿐입니다 — 없는 눈을 만들어내지는 못하니, 사용자가 donor 사진을 지정하지 않았으면 폴더의 다른 사진 중 무엇을 쓸지 물어보세요.
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
            if name == 'straighten':
                a = self.edit_straighten(inp.get('angle'))
                return f'사진 전체를 {a:+.1f}° 돌려 수평을 맞추고 가장자리를 잘라냈습니다: {self.base.shape[1]}×{self.base.shape[0]}px', self.render()
            if name == 'head_tilt':
                a = self.edit_head_tilt(inp.get('angle'))
                return f'고개만 {a:+.1f}° 돌렸습니다', self.render()
            if name == 'eyes_from':
                self.edit_eyes_from(inp['donor'], inp.get('which', 'both'))
                return f"{inp['donor']} 의 눈을 옮겨 붙였습니다", self.render()
            if name == 'undo':
                label = self.undo()
                return (f'되돌림: {label}' if label else '되돌릴 것이 없음'), self.render()
            if name == 'save':
                path = self.save(inp.get('size', 'orig'), inp.get('name'))
                return f'저장됨: {path}', None
            raise ValueError(f'모르는 도구 {name}')


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
    {'name': 'straighten', 'description': '사진 전체를 돌려 눈높이를 수평으로 맞추고(카메라가 기울어진 경우) 빈 모서리를 잘라낸다. angle 을 생략하면 얼굴에서 잰 기울기만큼, 주면 그 각도(도, 양수=반시계)만큼.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}}}},
    {'name': 'head_tilt', 'description': '몸은 그대로 두고 고개만 돌린다(±12° 까지). angle 생략 시 눈높이가 수평이 되게. 목·머리카락 주변이 함께 늘어나므로 작은 각도에서만 자연스럽다.',
     'input_schema': {'type': 'object', 'properties': {'angle': {'type': 'number'}}}},
    {'name': 'eyes_from', 'description': '같은 사람의 다른 사진(donor, 폴더 안 파일명)에서 눈을 가져와 붙인다. 눈 감은 사진 구제용. 같은 촬영·비슷한 각도의 사진이어야 한다. which: both | left | right.',
     'input_schema': {'type': 'object', 'properties': {'donor': {'type': 'string'}, 'which': {'type': 'string', 'enum': ['both', 'left', 'right']}}, 'required': ['donor']}},
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

    def do_GET(self):
        if self.path.split('?')[0] == '/':
            body = self.html.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == '/api/list':
            folder = self.session.args.folder
            files = C.list_images(folder) if folder and os.path.isdir(folder) else []
            self._json({'folder': folder, 'files': files, 'state': self.session.state(),
                        'presets': [{'id': p['id'], 'name': p['name']} for p in self.session.presets]})
        elif self.path.startswith('/api/thumb?'):
            name = urllib.parse.unquote(self.path.split('?', 1)[1])
            path = os.path.join(self.session.args.folder, os.path.basename(name))
            img = C.imread(path)
            buf = cv2.imencode('.jpg', downscale(img, 160), [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/jpeg')
            self.send_header('Content-Length', str(len(buf)))
            self.end_headers()
            self.wfile.write(buf)
        else:
            self.send_error(404)

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
            if self.path == '/api/open':
                path = os.path.join(s.args.folder, os.path.basename(body['name']))
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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--folder', help='한 장씩 열 사진 폴더 (필름스트립에 나옴)')
    ap.add_argument('--out', default='retouched', help='저장 폴더')
    ap.add_argument('--data', default='work', help='학습 결과 폴더 (geom.npz, tex.pt)')
    ap.add_argument('--presets-js')
    ap.add_argument('--device')
    ap.add_argument('--model', default=MODEL)
    ap.add_argument('--effort', default='medium', choices=['low', 'medium', 'high', 'xhigh', 'max'])
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--no-browser', action='store_true')
    args = ap.parse_args()

    Handler.session = Session(args)
    Handler.html = open(os.path.join(C.HERE, 'studio.html'), encoding='utf-8').read()
    srv = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    url = f'http://127.0.0.1:{args.port}/'
    print(f'스튜디오: {url}   (Ctrl+C 로 종료)')
    if not os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('ANTHROPIC_AUTH_TOKEN'):
        print('참고: ANTHROPIC_API_KEY 가 없으면 대화창은 동작하지 않습니다 (ant auth login 프로필이 있으면 됩니다).')
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
