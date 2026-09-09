#!/usr/bin/env python3
"""Train the texture model on prepare.py output.

Learns blemish removal, skin smoothing and any other pixel edit that stays in
place, as a residual added inside the face gate. Runs on the GPU when there is
one (a 3080 Ti does 300 pairs in roughly 1–2 hours at the defaults).

Usage:
  python tools/retouch/train_tex.py <data-dir> [--epochs 40] [--patch 512] [--batch 6] [--perceptual]

Writes <data-dir>/tex.pt (best validation checkpoint) and preview images to
<data-dir>/previews/ so you can judge the result by eye: original | model | yours.
"""

import argparse
import json
import os
import random
import sys
import time

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unet import UNet, retouch  # noqa: E402


class Pairs(Dataset):
    def __init__(self, root, keys, patch, samples_per_image, train):
        self.root, self.keys, self.patch, self.spi, self.train = root, keys, patch, samples_per_image, train

    def __len__(self):
        return len(self.keys) * self.spi

    def __getitem__(self, i):
        d = os.path.join(self.root, self.keys[i // self.spi])
        before = cv2.imread(os.path.join(d, 'before.png'))
        after = cv2.imread(os.path.join(d, 'after.png'))
        skin = cv2.imread(os.path.join(d, 'skin.png'), cv2.IMREAD_GRAYSCALE)
        gate = cv2.imread(os.path.join(d, 'gate.png'), cv2.IMREAD_GRAYSCALE)
        side = before.shape[0]
        p = min(self.patch, side)
        if self.train:
            ys, xs = np.nonzero(gate > 128)
            j = random.randrange(len(ys))
            y = int(np.clip(ys[j] - p // 2, 0, side - p)); x = int(np.clip(xs[j] - p // 2, 0, side - p))
        else:
            rng = random.Random(i)                      # fixed crops for a stable validation number
            ys, xs = np.nonzero(gate > 128)
            j = rng.randrange(len(ys))
            y = int(np.clip(ys[j] - p // 2, 0, side - p)); x = int(np.clip(xs[j] - p // 2, 0, side - p))
        sl = (slice(y, y + p), slice(x, x + p))
        before, after, skin, gate = before[sl], after[sl], skin[sl], gate[sl]
        if self.train and random.random() < 0.5:
            before, after, skin, gate = [np.ascontiguousarray(a[:, ::-1]) for a in (before, after, skin, gate)]
        b = torch.from_numpy(before).permute(2, 0, 1).float() / 255
        a = torch.from_numpy(after).permute(2, 0, 1).float() / 255
        if self.train:                                   # same photometric jitter on both sides
            gain = 1 + (random.random() - 0.5) * 0.2
            b, a = (b * gain).clamp(0, 1), (a * gain).clamp(0, 1)
        return b, a, torch.from_numpy(skin).float()[None] / 255, torch.from_numpy(gate).float()[None] / 255


def laplacian(x):
    k = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=x.dtype, device=x.device).view(1, 1, 3, 3).repeat(3, 1, 1, 1)
    return F.conv2d(x, k, padding=1, groups=3)


class Perceptual(torch.nn.Module):
    def __init__(self):
        super().__init__()
        from torchvision.models import vgg16, VGG16_Weights
        vgg = vgg16(weights=VGG16_Weights.DEFAULT).features[:16].eval()
        for p in vgg.parameters():
            p.requires_grad = False
        self.vgg = vgg
        self.register_buffer('mean', torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer('std', torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, x, y):
        x = (x.flip(1) - self.mean) / self.std        # BGR -> RGB
        y = (y.flip(1) - self.mean) / self.std
        return F.l1_loss(self.vgg(x), self.vgg(y))


def save_preview(path, b, pred, a):
    rows = []
    for i in range(min(4, b.shape[0])):
        row = torch.cat([b[i], pred[i], a[i]], 2)
        rows.append((row.permute(1, 2, 0).cpu().numpy() * 255).astype(np.uint8))
    cv2.imwrite(path, np.concatenate(rows, 0))


def main():
    C.console_utf8()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('data')
    ap.add_argument('--epochs', type=int, default=40)
    ap.add_argument('--patch', type=int, default=512)
    ap.add_argument('--batch', type=int, default=6)
    ap.add_argument('--lr', type=float, default=2e-4)
    ap.add_argument('--samples-per-image', type=int, default=8, help='한 사진에서 에폭당 뽑는 패치 수')
    ap.add_argument('--perceptual', action='store_true', help='VGG 지각 손실 추가 (더 자연스러운 결, 느려짐)')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--device', default='cuda' if torch.cuda.is_available() else 'cpu')
    ap.add_argument('--resume', help='이어서 학습할 tex.pt')
    args = ap.parse_args()

    index = json.load(open(os.path.join(args.data, 'index.json'), encoding='utf-8'))
    keys = [r['key'] for r in index['records']]
    random.Random(0).shuffle(keys)
    n_val = max(1, len(keys) // 10)
    val_keys, train_keys = keys[:n_val], keys[n_val:]
    print(f'학습 {len(train_keys)}쌍 · 검증 {n_val}쌍 · {args.device}')

    dl_train = DataLoader(Pairs(args.data, train_keys, args.patch, args.samples_per_image, True),
                          batch_size=args.batch, shuffle=True, num_workers=args.workers, drop_last=True,
                          persistent_workers=args.workers > 0)
    dl_val = DataLoader(Pairs(args.data, val_keys, args.patch, 4, False),
                        batch_size=args.batch, shuffle=False, num_workers=min(2, args.workers))

    dev = torch.device(args.device)
    model = UNet().to(dev)
    if args.resume:
        model.load_state_dict(torch.load(args.resume, map_location=dev)['model'])
    percep = Perceptual().to(dev) if args.perceptual else None
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=args.lr, total_steps=args.epochs * len(dl_train), pct_start=0.05)
    use_amp = dev.type == 'cuda'
    scaler = torch.amp.GradScaler(enabled=use_amp)

    os.makedirs(os.path.join(args.data, 'previews'), exist_ok=True)
    best = float('inf')
    ckpt_path = os.path.join(args.data, 'tex.pt')
    for epoch in range(1, args.epochs + 1):
        model.train()
        t0, tot, n = time.time(), 0.0, 0
        for b, a, skin, gate in dl_train:
            b, a, skin, gate = b.to(dev), a.to(dev), skin.to(dev), gate.to(dev)
            with torch.autocast(dev.type, enabled=use_amp):
                pred = retouch(model, b, skin, gate)
                loss = F.l1_loss(pred, a) + 0.5 * F.l1_loss(laplacian(pred), laplacian(a))
                if percep is not None:
                    loss = loss + 0.05 * percep(pred, a)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt); scaler.update(); sched.step()
            tot += loss.item() * b.shape[0]; n += b.shape[0]

        model.eval()
        v_model, v_base, vn = 0.0, 0.0, 0
        with torch.no_grad():
            for i, (b, a, skin, gate) in enumerate(dl_val):
                b, a, skin, gate = b.to(dev), a.to(dev), skin.to(dev), gate.to(dev)
                pred = retouch(model, b, skin, gate)
                v_model += F.l1_loss(pred, a).item() * b.shape[0]
                v_base += F.l1_loss(b, a).item() * b.shape[0]
                vn += b.shape[0]
                if i == 0:
                    save_preview(os.path.join(args.data, 'previews', f'epoch{epoch:03d}.jpg'), b, pred, a)
        v_model, v_base = 255 * v_model / vn, 255 * v_base / vn
        mark = ''
        if v_model < best:
            best = v_model
            torch.save({'model': model.state_dict(), 'face_size': index['face_size'], 'preset': index.get('preset'),
                        'val_l1': v_model, 'epoch': epoch}, ckpt_path)
            mark = ' ← 저장'
        print(f'epoch {epoch:3d}  학습 손실 {tot / n:.4f}  검증 오차 {v_model:.2f}  (보정 안 했을 때 {v_base:.2f})  {time.time() - t0:.0f}s{mark}')

    print(f'\n완료. 최고 검증 오차 {best:.2f}/255 → {ckpt_path}')
    print(f'미리보기: {os.path.join(args.data, "previews")}  (왼쪽 원본 · 가운데 모델 · 오른쪽 직접 보정)')


if __name__ == '__main__':
    main()
