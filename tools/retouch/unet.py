"""Small residual U-Net for the texture model (about 2M parameters).

Input: 4 channels (BGR in 0..1 plus the skin hint). Output: a 3-channel
residual. The caller adds it to the input inside the gate mask."""

import torch
import torch.nn as nn
import torch.nn.functional as F


def block(cin, cout):
    return nn.Sequential(
        nn.Conv2d(cin, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.SiLU(inplace=True),
        nn.Conv2d(cout, cout, 3, padding=1), nn.GroupNorm(8, cout), nn.SiLU(inplace=True))


class UNet(nn.Module):
    def __init__(self, cin=4, cout=3, base=32):
        super().__init__()
        ch = [base, base * 2, base * 4, base * 8]
        self.enc = nn.ModuleList([block(cin, ch[0]), block(ch[0], ch[1]), block(ch[1], ch[2]), block(ch[2], ch[3])])
        self.up = nn.ModuleList([block(ch[3] + ch[2], ch[2]), block(ch[2] + ch[1], ch[1]), block(ch[1] + ch[0], ch[0])])
        self.out = nn.Conv2d(ch[0], cout, 1)
        nn.init.zeros_(self.out.weight); nn.init.zeros_(self.out.bias)   # start as identity

    def forward(self, x):
        h, w = x.shape[-2:]
        pad_h, pad_w = (-h) % 8, (-w) % 8
        if pad_h or pad_w:
            x = F.pad(x, (0, pad_w, 0, pad_h), mode='reflect')
        skips = []
        for i, e in enumerate(self.enc):
            x = e(x)
            if i < len(self.enc) - 1:
                skips.append(x)
                x = F.avg_pool2d(x, 2)
        for u in self.up:
            x = F.interpolate(x, scale_factor=2, mode='bilinear', align_corners=False)
            x = u(torch.cat([x, skips.pop()], 1))
        x = self.out(x)
        return x[..., :h, :w]


def retouch(model, img, skin, gate):
    """img/skin/gate are tensors (B,3/1/1,H,W) in 0..1. Returns the retouched image."""
    res = model(torch.cat([img, skin], 1))
    return (img + res * gate).clamp(0, 1)
