"""Shared pieces of the portrait retouch pipeline.

Face landmarks (MediaPipe FaceLandmarker), a normalised face frame, skin masks,
piecewise-affine warping and loading of the global colour LUTs produced by
tools/learn-preset.mjs. Everything works on BGR uint8 images (OpenCV order).
"""

import json
import os
import re
import sys
import urllib.request

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_URL = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
             'face_landmarker/float16/1/face_landmarker.task')
MODEL_PATH = os.path.join(HERE, 'models', 'face_landmarker.task')

N_LANDMARKS = 468          # mesh points; the 10 iris points after them move with gaze
DETECT_EDGE = 1280         # full-frame detection runs on a copy this size
REFINE_EDGE = 1536         # second pass on the face crop for precise landmarks
CROP_MARGIN = 1.6          # crop side = face width * this
ANCHOR_RING = 1.35         # warps fade to identity at this multiple of the face radius

# MediaPipe face-mesh index sets (from mediapipe's face_mesh_connections).
FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379,
             378, 400, 377, 152, 148, 176, 149, 150, 136, 172, 58, 132, 93, 234, 127,
             162, 21, 54, 103, 67, 109]
LEFT_EYE = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
RIGHT_EYE = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]
LIPS = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185]
LEFT_BROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
RIGHT_BROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]
CHEEK_L, CHEEK_R = 234, 454      # face width reference
EYE_L, EYE_R = 33, 263           # outer eye corners: roll reference

IMG_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff')


def list_images(folder):
    return sorted(f for f in os.listdir(folder) if f.lower().endswith(IMG_EXT))


def console_utf8():
    """윈도 콘솔은 기본이 cp949 라, 출력에 — 같은 글자가 하나 섞이면
    UnicodeEncodeError 로 스크립트가 통째로 죽는다 — 계산이 다 끝난 뒤 마지막
    print 에서 죽으므로 결과까지 같이 날아간다. 진입점마다 먼저 부른다."""
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def imread(path):
    data = np.fromfile(path, np.uint8)      # handles non-ASCII paths on Windows
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f'열 수 없는 이미지: {path}')
    return img


def imwrite(path, img, quality=95):
    ext = os.path.splitext(path)[1].lower() or '.jpg'
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in ('.jpg', '.jpeg') else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise ValueError(f'인코딩 실패: {path}')
    buf.tofile(path)


# ---------------------------------------------------------------- landmarks

def ensure_model():
    if os.path.exists(MODEL_PATH):
        return MODEL_PATH
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    print(f'얼굴 랜드마크 모델 내려받는 중 → {MODEL_PATH}', file=sys.stderr)
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    return MODEL_PATH


class Landmarker:
    """Two-pass landmark detection: find the face on a downscaled frame, then
    re-detect on a tight crop so the points are accurate at full resolution."""

    def __init__(self, max_faces=4):
        import mediapipe as mp
        from mediapipe.tasks.python import vision
        from mediapipe.tasks.python.core.base_options import BaseOptions
        self._mp = mp
        opts = vision.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=ensure_model()),
            num_faces=max_faces, min_face_detection_confidence=0.4)
        self._lm = vision.FaceLandmarker.create_from_options(opts)

    def close(self):
        try:
            self._lm.close()
        except Exception:
            pass

    def _detect_raw(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        res = self._lm.detect(self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb))
        h, w = bgr.shape[:2]
        faces = []
        for f in res.face_landmarks:
            pts = np.array([[p.x * w, p.y * h] for p in f[:N_LANDMARKS]], np.float32)
            faces.append(pts)
        return faces

    def _scan_windows(self, bgr):
        """전신 입상 사진 대비. 6720px 세로 사진에서 얼굴은 300px 남짓인데,
        전체를 DETECT_EDGE 로 줄이면 얼굴이 50px 이 되고 검출기 입력에서는 10px 이
        되어 절대 안 잡힌다. 프레임을 겹치는 창으로 나눠 각 창에서 다시 찾는다.
        반환은 원본 좌표계의 가장 큰 얼굴."""
        h, w = bgr.shape[:2]
        best = None
        for frac in (0.5, 0.33):
            wh, ww = int(h * frac), int(w * frac)
            if wh < 32 or ww < 32:
                continue
            ys = sorted({0, *range(0, max(1, h - wh + 1), max(1, wh // 2)), h - wh})
            xs = sorted({0, *range(0, max(1, w - ww + 1), max(1, ww // 2)), w - ww})
            for y in ys:
                for x in xs:
                    win = bgr[y:y + wh, x:x + ww]
                    kk = min(1.0, DETECT_EDGE / max(win.shape[:2]))
                    ws = cv2.resize(win, None, fx=kk, fy=kk, interpolation=cv2.INTER_AREA) if kk < 1 else win
                    got = self._detect_raw(ws)
                    for p in got:
                        p = p / kk + np.array([x, y], np.float32)
                        if best is None or np.ptp(p[:, 0]) > np.ptp(best[:, 0]):
                            best = p
            if best is not None:
                return best                       # 큰 창에서 찾았으면 더 잘게 볼 필요 없다
        return best

    def detect_near(self, bgr, ref_pts, grow=2.2):
        """ref_pts(다른 이미지에서 찾은 같은 얼굴) 주변만 보고 검출한다.

        원본/보정본처럼 화소가 정렬된 두 장을 각자 독립으로 검출하면, 창 스캔이
        서로 다른 창에 걸려 좌표계가 통째로 어긋난다 — 변형량이 얼굴 폭의 수십 %로
        나오는 원인이 그것이었다. 한쪽에서 찾은 자리를 다른 쪽에 물려주면
        두 검출이 같은 출발점을 갖는다."""
        h, w = bgr.shape[:2]
        x0, y0 = ref_pts.min(0)
        x1, y1 = ref_pts.max(0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        side = max(x1 - x0, y1 - y0) * grow
        X0, Y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        X1, Y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        crop = bgr[Y0:Y1, X0:X1]
        if crop.size == 0:
            return None
        k = min(1.0, REFINE_EDGE / max(crop.shape[:2]))
        cs = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else crop
        faces = self._detect_raw(cs)
        if not faces:
            return None
        faces.sort(key=lambda p: -np.ptp(p[:, 0]))
        return faces[0] / k + np.array([X0, Y0], np.float32)

    def detect(self, bgr):
        """Return landmarks (468, 2) of the largest face in image pixels, or None."""
        h, w = bgr.shape[:2]
        k = min(1.0, DETECT_EDGE / max(h, w))
        small = cv2.resize(bgr, None, fx=k, fy=k, interpolation=cv2.INTER_AREA) if k < 1 else bgr
        faces = self._detect_raw(small)
        if faces:
            faces.sort(key=lambda p: -np.ptp(p[:, 0]))
            pts = faces[0] / k
        else:
            pts = self._scan_windows(bgr)
            if pts is None:
                return None

        # Second pass on a crop around the face at up to REFINE_EDGE pixels.
        x0, y0 = pts.min(0); x1, y1 = pts.max(0)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        side = max(x1 - x0, y1 - y0) * 1.8
        X0, Y0 = int(max(0, cx - side / 2)), int(max(0, cy - side / 2))
        X1, Y1 = int(min(w, cx + side / 2)), int(min(h, cy + side / 2))
        crop = bgr[Y0:Y1, X0:X1]
        if crop.size == 0:
            return pts
        k2 = min(1.0, REFINE_EDGE / max(crop.shape[:2]))
        crop_s = cv2.resize(crop, None, fx=k2, fy=k2, interpolation=cv2.INTER_AREA) if k2 < 1 else crop
        faces2 = self._detect_raw(crop_s)
        if not faces2:
            return pts
        faces2.sort(key=lambda p: -np.ptp(p[:, 0]))
        return faces2[0] / k2 + np.array([X0, Y0], np.float32)


# ---------------------------------------------------------------- face frame

def face_frame(pts):
    """Similarity transform of the face: centre, width (cheek to cheek) and roll."""
    centre = pts[FACE_OVAL].mean(0)
    width = float(np.linalg.norm(pts[CHEEK_R] - pts[CHEEK_L]))
    e = pts[EYE_R] - pts[EYE_L]
    roll = float(np.arctan2(e[1], e[0]))
    return centre, width, roll


def normalise(pts, ref=None):
    """Landmarks in a frame centred on the face, eyes level, face width = 1.
    The frame is taken from `ref` when given, so a before/after pair can be
    expressed in the same coordinates (a slimmed face must not renormalise)."""
    centre, width, roll = face_frame(pts if ref is None else ref)
    c, s = np.cos(-roll), np.sin(-roll)
    R = np.array([[c, -s], [s, c]], np.float32)
    return (pts - centre) @ R.T / width


def crop_transform(pts, face_size, margin=CROP_MARGIN):
    """Affine (2x3) taking image pixels to a square crop where the face is
    upright, centred, and `face_size` pixels wide. Returns (M, side)."""
    centre, width, roll = face_frame(pts)
    side = int(round(face_size * margin))
    scale = face_size / width
    M = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), np.degrees(roll), scale)
    M[:, 2] += np.array([side / 2, side / 2]) - centre
    return M, side


def apply_affine(M, pts):
    return pts @ M[:, :2].T + M[:, 2]


def invert_affine(M):
    return cv2.invertAffineTransform(M)


# ---------------------------------------------------------------- masks

def _poly(mask, pts, idx, value=255):
    cv2.fillPoly(mask, [np.round(pts[idx]).astype(np.int32)], value)


def _hull(mask, pts, idx, value=255):
    hull = cv2.convexHull(np.round(pts[idx]).astype(np.int32))
    cv2.fillConvexPoly(mask, hull, value)


def face_masks(pts, side, face_size):
    """Return (skin, gate) as float32 [0,1] arrays of shape (side, side).

    skin: face oval minus eyes, brows and lips — a hint channel for the network.
    gate: the whole face plus a margin, feathered — where any change is allowed."""
    k = face_size / 1000.0        # sizes below are in pixels at face width 1000
    skin = np.zeros((side, side), np.uint8)
    _poly(skin, pts, FACE_OVAL)
    skin = cv2.erode(skin, _disc(6 * k))
    holes = np.zeros_like(skin)
    _poly(holes, pts, LEFT_EYE); _poly(holes, pts, RIGHT_EYE)
    _poly(holes, pts, LIPS)
    holes = cv2.dilate(holes, _disc(7 * k))
    brows = np.zeros_like(skin)
    _hull(brows, pts, LEFT_BROW); _hull(brows, pts, RIGHT_BROW)
    holes = np.maximum(holes, cv2.dilate(brows, _disc(4 * k)))
    skin[holes > 0] = 0
    skin = cv2.GaussianBlur(skin, (0, 0), 4 * k + 1)

    gate = np.zeros((side, side), np.uint8)
    _poly(gate, pts, FACE_OVAL)
    gate = cv2.dilate(gate, _disc(70 * k))
    gate = cv2.GaussianBlur(gate, (0, 0), 25 * k + 1)
    return skin.astype(np.float32) / 255, gate.astype(np.float32) / 255


def _disc(r):
    r = max(1, int(round(r)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


# ---------------------------------------------------------------- warping

def anchor_points(pts, side, ring=ANCHOR_RING):
    """Fixed points so the warp fades to identity: a ring around the face plus
    the crop border."""
    centre = pts[FACE_OVAL].mean(0)
    radius = np.linalg.norm(pts[FACE_OVAL] - centre, axis=1).max() * ring
    ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
    ring = centre + radius * np.stack([np.cos(ang), np.sin(ang)], 1)
    ring = np.clip(ring, 1, side - 2)
    edge = np.linspace(0, side - 1, 5)
    border = [(x, 0) for x in edge] + [(x, side - 1) for x in edge] + \
             [(0, y) for y in edge[1:-1]] + [(side - 1, y) for y in edge[1:-1]]
    return np.vstack([ring, np.array(border, np.float32)]).astype(np.float32)




def _triangulate_np(pts, side):
    # Subdiv2D 는 사각형 밖의 점을 받으면 예외를 던진다(-211 out of range).
    # 얼굴이 사진 가장자리에 걸리면 크롭 밖으로 나가는 랜드마크가 생기므로,
    # 사각형을 점들이 다 들어갈 만큼 넓혀서 잡는다. 삼각형 색인만 쓰고
    # 좌표는 원래 값을 그대로 쓰므로 넓히는 것 자체는 결과를 바꾸지 않는다.
    lo = float(min(0.0, pts.min() - 1.0))
    hi = float(max(side + 1.0, pts.max() + 2.0))
    sub = cv2.Subdiv2D((int(np.floor(lo)), int(np.floor(lo)),
                        int(np.ceil(hi - lo)) + 1, int(np.ceil(hi - lo)) + 1))
    for p in pts:
        sub.insert((float(p[0]), float(p[1])))
    tris = sub.getTriangleList().reshape(-1, 3, 2)
    d = ((tris[:, :, None, :] - pts[None, None, :, :]) ** 2).sum(-1)   # (T,3,N)
    ids = d.argmin(-1)
    keep = (ids[:, 0] != ids[:, 1]) & (ids[:, 1] != ids[:, 2]) & (ids[:, 0] != ids[:, 2])
    return ids[keep].astype(np.int32)


def warp_points(img, src_pts, dst_pts, side):
    """Piecewise-affine warp of a `side`x`side` crop: content at src_pts ends up
    at dst_pts. Both arrays are (N,2) in crop pixels and must include anchors."""
    tris = _triangulate_np(dst_pts, side)
    label = np.full((side, side), -1, np.int32)
    for i, t in enumerate(tris):
        cv2.fillConvexPoly(label, np.round(dst_pts[t]).astype(np.int32), int(i))
    # affine per triangle, dst -> src
    A = np.zeros((len(tris), 2, 3), np.float32)
    for i, t in enumerate(tris):
        A[i] = cv2.getAffineTransform(dst_pts[t].astype(np.float32), src_pts[t].astype(np.float32))
    ys, xs = np.mgrid[0:side, 0:side].astype(np.float32)
    lab = label.copy()
    lab[lab < 0] = 0                                  # outside all triangles: identity below
    a = A[lab]                                        # (side, side, 2, 3)
    map_x = a[..., 0, 0] * xs + a[..., 0, 1] * ys + a[..., 0, 2]
    map_y = a[..., 1, 0] * xs + a[..., 1, 1] * ys + a[..., 1, 2]
    outside = label < 0
    map_x[outside] = xs[outside]; map_y[outside] = ys[outside]
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


# ---------------------------------------------------------------- colour LUT

def load_lut(preset_id, presets_js=None):
    """Read one learned preset (3 x 256 table) from tools/photo-fix/presets.js."""
    presets_js = presets_js or os.path.join(HERE, '..', 'photo-fix', 'presets.js')
    src = open(presets_js, encoding='utf-8').read()
    m = re.search(r'window\.LEARNED_PRESETS\s*=\s*(\[[\s\S]*\]);', src)
    if not m:
        raise ValueError(f'{presets_js} 에서 프리셋을 읽지 못했습니다')
    for p in json.loads(m.group(1)):
        if p['id'] == preset_id:
            lut = np.array(p['lut'], np.uint8)          # (3,256) RGB order
            return lut[::-1].copy()                      # BGR for OpenCV
    raise ValueError(f'프리셋 "{preset_id}" 없음')


def apply_lut(img, lut):
    out = np.empty_like(img)
    for c in range(3):
        out[..., c] = lut[c][img[..., c]]
    return out
