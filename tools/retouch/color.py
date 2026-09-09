"""The photo-fix colour pipeline, in numpy.

Same maths as tools/photo-fix/index.html so a look dialled in either place
renders identically here: auto levels -> preset LUT -> white balance ->
exposure -> contrast fold into one per-channel table; shadows/highlights and
saturation/vibrance need the other channels and run per pixel; sharpen last.
Images are BGR uint8."""

import cv2
import numpy as np

DEFAULTS = {'exposure': 0.0, 'contrast': 1.0, 'highlights': 0, 'shadows': 0, 'temp': 0, 'tint': 0,
            'saturation': 0, 'vibrance': 0, 'sharpen': 0}
RANGES = {'exposure': (-2, 2), 'contrast': (0.5, 2), 'highlights': (-100, 100), 'shadows': (-100, 100),
          'temp': (-100, 100), 'tint': (-100, 100), 'saturation': (-100, 100), 'vibrance': (-100, 100),
          'sharpen': (0, 100)}
AUTO_CLIP = 0.005
AUTO_WB = 0.7
_LUMA = np.array([0.0722, 0.7152, 0.2126], np.float32)     # BGR weights


def clamp_params(p):
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if k in RANGES:
            lo, hi = RANGES[k]
            out[k] = float(min(hi, max(lo, float(v))))
    return out


def auto_lut(img):
    """Per-channel levels, pulled part-way back toward the shared endpoints."""
    total = img.shape[0] * img.shape[1]
    cut = max(1, round(total * AUTO_CLIP))
    lo, hi = [], []
    for c in range(3):
        h = np.bincount(img[..., c].ravel(), minlength=256)
        cdf = np.cumsum(h)
        lo.append(int(np.searchsorted(cdf, cut)))
        hi.append(255 - int(np.searchsorted(np.cumsum(h[::-1]), cut)))
    lo_avg, hi_avg = sum(lo) / 3, sum(hi) / 3
    v = np.arange(256, dtype=np.float32)
    lut = np.empty((3, 256), np.uint8)
    for c in range(3):
        l = lo[c] * AUTO_WB + lo_avg * (1 - AUTO_WB)
        h = hi[c] * AUTO_WB + hi_avg * (1 - AUTO_WB)
        span = h - l
        lut[c] = np.clip(v if span < 16 else (v - l) * 255 / span, 0, 255).round()
    return lut


def build_lut(p, preset=None, auto=None):
    """preset/auto are (3,256) uint8 tables in BGR order."""
    wb = [1 - p['temp'] / 300, 1 - p['tint'] / 400, 1 + p['temp'] / 300]
    ev = 2.0 ** p['exposure']
    idx = np.arange(256)
    lut = np.empty((3, 256), np.uint8)
    for c in range(3):
        v = (auto[c][idx] if auto is not None else idx) / 255.0
        if preset is not None:
            v = preset[c][np.round(v * 255).astype(int)] / 255.0
        v = np.maximum(0, v * wb[c])
        if ev != 1:
            v = (v ** 2.2 * ev) ** (1 / 2.2)
        if p['contrast'] != 1:
            v = (v - 0.5) * p['contrast'] + 0.5
        lut[c] = np.clip(v * 255, 0, 255).round()
    return lut


def apply(img, p, preset=None, auto=None):
    lut = build_lut(p, preset, auto)
    out = np.empty(img.shape, np.float32)
    for c in range(3):
        out[..., c] = lut[c][img[..., c]]
    out /= 255.0

    sh, hi = p['shadows'] / 100, p['highlights'] / 100
    if sh or hi:
        L = out @ _LUMA
        if sh:
            m = (1 - L) ** 2.5
            out = (out + (sh * 0.10 * m)[..., None]) * (1 + sh * 0.6 * m)[..., None]
        if hi:
            m = np.clip(L, 0, 1) ** 2.5
            out = out * (1 + hi * 0.7 * m)[..., None]

    sat, vib = p['saturation'] / 100, p['vibrance'] / 100
    if sat or vib:
        k = np.full(out.shape[:2], 1 + sat, np.float32)
        if vib:
            mx, mn = out.max(-1), out.min(-1)
            cur = np.where(mx > 0.004, (mx - mn) / np.maximum(mx, 1e-6), 0)
            k += vib * (1 - cur)
        L = out @ _LUMA
        out = L[..., None] + (out - L[..., None]) * k[..., None]

    out = np.clip(out * 255, 0, 255)
    if p['sharpen'] > 0:
        k = np.array([1, 2, 1], np.float32) / 4
        blur = cv2.sepFilter2D(out, -1, k, k, borderType=cv2.BORDER_REPLICATE)
        out = np.clip(out + p['sharpen'] / 100 * 1.5 * (out - blur), 0, 255)
    return out.round().astype(np.uint8)
