#!/usr/bin/env python3
"""Learn how the face shape is changed (the liquify part) from prepare.py output.

Input: normalised landmarks of the original face. Output: where each landmark
should move, in units of face width. Two candidates are fitted — a constant
average move, and a ridge regression that lets the move depend on the face —
and cross-validation picks whichever predicts held-out pairs better.

Usage:
  python tools/retouch/train_geom.py <data-dir> [--out <data-dir>/geom.npz]
"""

import argparse
import json
import os

import numpy as np


def ridge_fit(X, Y, alpha):
    xm, ym = X.mean(0), Y.mean(0)
    Xc, Yc = X - xm, Y - ym
    W = np.linalg.solve(Xc.T @ Xc + alpha * np.eye(X.shape[1]), Xc.T @ Yc)
    return xm, ym, W


def ridge_predict(model, X):
    xm, ym, W = model
    return ym + (X - xm) @ W


def cv_rms(X, Y, alpha, folds=5, seed=0):
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(X))
    errs = []
    for f in range(folds):
        test = order[f::folds]
        train = np.setdiff1d(order, test)
        if alpha is None:
            pred = np.repeat(Y[train].mean(0, keepdims=True), len(test), 0)
        else:
            pred = ridge_predict(ridge_fit(X[train], Y[train], alpha), X[test])
        errs.append(((pred - Y[test]) ** 2).mean())
    return float(np.sqrt(np.mean(errs)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('data')
    ap.add_argument('--out')
    args = ap.parse_args()
    out = args.out or os.path.join(args.data, 'geom.npz')

    index = json.load(open(os.path.join(args.data, 'index.json'), encoding='utf-8'))
    recs = index['records']
    X = np.array([np.ravel(r['norm_before']) for r in recs], np.float64)
    Y = np.array([np.ravel(r['disp']) for r in recs], np.float64)
    face_px = float(np.mean([r['face_width_px'] for r in recs]))
    n = len(recs)
    print(f'{n}쌍 · 평균 얼굴 폭 {face_px:.0f}px')

    zero = float(np.sqrt((Y ** 2).mean()))
    print(f'  아무것도 안 했을 때 오차: {zero * face_px:.2f}px')

    scores = {'mean': cv_rms(X, Y, None)}
    if n >= 10:
        for a in (0.03, 0.1, 0.3, 1.0, 3.0):
            scores[f'ridge{a}'] = cv_rms(X, Y, a)
    for k, v in scores.items():
        print(f'  {k:10s} 교차검증 오차: {v * face_px:.2f}px')
    best = min(scores, key=scores.get)

    if best == 'mean':
        xm, ym, W = X.mean(0), Y.mean(0), np.zeros((X.shape[1], Y.shape[1]))
        print('→ 평균 이동량 모델 채택 (얼굴마다 거의 같은 방식으로 변형함)')
    else:
        alpha = float(best[5:])
        xm, ym, W = ridge_fit(X, Y, alpha)
        print(f'→ 회귀 모델 채택 (alpha={alpha}; 얼굴 생김새에 따라 변형량이 달라짐)')

    np.savez(out, x_mean=xm, y_mean=ym, W=W, face_size=index['face_size'], n_pairs=n)
    print(f'저장: {out}')


if __name__ == '__main__':
    main()
