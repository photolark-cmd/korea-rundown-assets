#!/usr/bin/env python3
"""Batch-retouch portraits with the models learned from your own pairs.

Per photo: optional colour LUT → find the face → reshape it the way the
geometry model learned → run the texture model on the face → paste back.
Photos without a detectable face get the LUT only and are listed at the end.

Usage:
  python tools/retouch/retouch.py <in-dir> --out <out-dir> --data <data-dir> [--preset <id>]
      [--geom-strength 1.0] [--tex-strength 1.0] [--no-geom] [--no-tex] [--device cuda|cpu]

--data is the folder prepare.py / train_*.py wrote (geom.npz and tex.pt live there).
"""

import argparse
import os
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402


def load_geom(path):
    z = np.load(path)
    return {'x_mean': z['x_mean'], 'y_mean': z['y_mean'], 'W': z['W']}


def predict_disp(geom, norm_pts):
    x = norm_pts.reshape(-1)
    d = geom['y_mean'] + (x - geom['x_mean']) @ geom['W']
    return d.reshape(-1, 2).astype(np.float32)


def load_tex(path, device):
    import torch
    from unet import UNet
    ck = torch.load(path, map_location=device)
    model = UNet().to(device).eval()
    model.load_state_dict(ck['model'])
    return model, ck


def run_tex(model, device, crop, skin, gate, strength):
    import torch
    from unet import retouch
    with torch.no_grad():
        img = torch.from_numpy(crop).permute(2, 0, 1).float()[None].to(device) / 255
        s = torch.from_numpy(skin)[None, None].to(device)
        g = torch.from_numpy(gate)[None, None].to(device) * strength
        with torch.autocast(device.type, enabled=device.type == 'cuda'):
            out = retouch(model, img, s, g)
        return (out[0].float().permute(1, 2, 0).cpu().numpy() * 255).round().astype(np.uint8)


def paste_mask(pts, side, face_size):
    """Feathered disc covering everything the face steps may have changed."""
    centre = pts[C.FACE_OVAL].mean(0)
    radius = np.linalg.norm(pts[C.FACE_OVAL] - centre, axis=1).max() * C.ANCHOR_RING
    m = np.zeros((side, side), np.uint8)
    cv2.circle(m, (int(centre[0]), int(centre[1])), int(radius), 255, -1)
    m = cv2.GaussianBlur(m, (0, 0), face_size * 0.04)
    return m.astype(np.float32) / 255


def paste_back(img, crop, M, mask_crop):
    """Put a processed face crop back into the full image through the inverse affine."""
    Minv = C.invert_affine(M)
    h, w = img.shape[:2]
    back = cv2.warpAffine(crop, Minv, (w, h), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_TRANSPARENT, dst=img.copy())
    mask = cv2.warpAffine(mask_crop, Minv, (w, h))[..., None]
    return (back.astype(np.float32) * mask + img.astype(np.float32) * (1 - mask)).round().astype(np.uint8)


class Retoucher:
    """The learned face steps, loadable once and applied to many images."""

    def __init__(self, data_dir, device=None, use_geom=True, use_tex=True):
        self.geom = load_geom(os.path.join(data_dir, 'geom.npz')) if use_geom and os.path.exists(os.path.join(data_dir, 'geom.npz')) else None
        self.tex = self.device = None
        self.face_size = 1024
        self.preset = None
        tex_path = os.path.join(data_dir, 'tex.pt')
        if use_tex and os.path.exists(tex_path):
            import torch
            self.device = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
            self.tex, ck = load_tex(tex_path, self.device)
            self.face_size = int(ck['face_size'])
            self.preset = ck.get('preset')
        elif self.geom is not None:
            self.face_size = int(np.load(os.path.join(data_dir, 'geom.npz'))['face_size'])
        if self.geom is None and self.tex is None:
            raise FileNotFoundError(f'{data_dir} 에 geom.npz / tex.pt 가 없습니다')

    def process(self, img, pts, geom_strength=1.0, tex_strength=1.0):
        """img: BGR full image, pts: its landmarks. Returns the retouched image."""
        M, side = C.crop_transform(pts, self.face_size)
        crop = cv2.warpAffine(img, M, (side, side), flags=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_REFLECT)
        pc = C.apply_affine(M, pts)
        cur = pc
        if self.geom is not None and geom_strength:
            _, fw, _ = C.face_frame(pc)
            target = pc + predict_disp(self.geom, C.normalise(pc)) * fw * geom_strength
            anchors = C.anchor_points(pc, side)
            crop = C.warp_points(crop, np.vstack([pc, anchors]), np.vstack([target, anchors]), side)
            cur = target
        if self.tex is not None and tex_strength:
            skin, gate = C.face_masks(cur, side, self.face_size)
            crop = run_tex(self.tex, self.device, crop, skin, gate, tex_strength)
        return paste_back(img, crop, M, paste_mask(cur, side, self.face_size))


def write_with_exif(src_path, dst_path, img, quality):
    """Keep the camera EXIF but reset orientation: OpenCV already applied it."""
    C.imwrite(dst_path, img, quality)
    try:
        from PIL import Image
        exif = Image.open(src_path).getexif()
        if len(exif):
            exif[274] = 1
            out = Image.open(dst_path)
            out.save(dst_path, quality=quality, exif=exif.tobytes())
    except Exception:
        pass


def main():
    C.console_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('input')
    ap.add_argument('--out', required=True)
    ap.add_argument('--data', required=True, help='prepare.py 출력 폴더 (geom.npz, tex.pt)')
    ap.add_argument('--preset', help='먼저 적용할 색 프리셋 id (학습 때 쓴 것과 같게)')
    ap.add_argument('--presets-js')
    ap.add_argument('--geom-strength', type=float, default=1.0)
    ap.add_argument('--tex-strength', type=float, default=1.0)
    ap.add_argument('--no-geom', action='store_true')
    ap.add_argument('--no-tex', action='store_true')
    ap.add_argument('--device', default=None)
    ap.add_argument('--quality', type=int, default=95)
    args = ap.parse_args()

    try:
        rt = Retoucher(args.data, args.device, use_geom=not args.no_geom, use_tex=not args.no_tex)
    except FileNotFoundError as e:
        sys.exit(str(e))
    if rt.preset and not args.preset:
        print(f'참고: 질감 모델은 --preset {rt.preset} 으로 학습됐습니다. 같은 프리셋을 주는 편이 맞습니다.')
    lut = C.load_lut(args.preset, args.presets_js) if args.preset else None

    files = C.list_images(args.input)
    if not files:
        sys.exit(f'{args.input} 에 이미지가 없습니다.')
    os.makedirs(args.out, exist_ok=True)
    lm = C.Landmarker()
    no_face = []
    t0 = time.time()
    for n, f in enumerate(files, 1):
        src = os.path.join(args.input, f)
        dst = os.path.join(args.out, os.path.splitext(f)[0] + '.jpg')
        img = C.imread(src)
        if lut is not None:
            img = C.apply_lut(img, lut)
        pts = lm.detect(img)
        if pts is None:
            no_face.append(f)
            write_with_exif(src, dst, img, args.quality)
            print(f'  [{n}/{len(files)}] {f}  얼굴 없음 — 색 보정만')
            continue

        out = rt.process(img, pts, args.geom_strength, args.tex_strength)
        write_with_exif(src, dst, out, args.quality)
        print(f'  [{n}/{len(files)}] {f}  ({time.time() - t0:.0f}s)')
    lm.close()

    print(f'\n{len(files)}장 → {args.out}')
    if no_face:
        print(f'얼굴을 못 찾아 색 보정만 한 사진 {len(no_face)}장:')
        for f in no_face:
            print('  ' + f)


if __name__ == '__main__':
    main()
