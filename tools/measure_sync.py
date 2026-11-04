#!/usr/bin/env python3
"""Sync analysis for OpenSurround Sample Data Export files (Audacity, dB export, stereo).

Measures, per export file:
  1. Click onset per mic channel (sample-precise) and L/R onset skew.
  2. Per-phone DAC clock skew via the strongest single-phone spectral
     components of the test WAV (test_4ch.wav):
       - Nokia Phone (front, ch0/1): tones 440 + 554.37 Hz
           -> harmonic component 1760 Hz (4 x 440, strong in recordings)
       - Nokia Q (rear, ch2/3): tones 659.25 + 880 Hz
           -> intermodulation component 1539.25 Hz (880 + 659.25, strong)
     The phase-slope (demodulation) method gives frequency estimates with
     ~0.01 Hz resolution from a 1-second export.
  3. Phone-to-phone clock difference A-B in ppm (recorder clock error cancels)
     and projected drift for 30 s / 10 min / 1 h.

Usage:
    python measure_sync.py sample-data5.txt sample-data6.txt

Assumes 48 kHz export (Audacity project rate). If your project rate differs,
change FS. The absolute ppm includes the recorder clock error; only the
A-B difference is meaningful for phone sync.
"""
from __future__ import annotations

import json
import sys

import numpy as np

FS = 48000.0
COMPONENTS = [
    (1760.00, "Nokia Phone (front)", 440.0),     # 4 x 440 harmonic
    (1539.25, "Nokia Q (rear)", 659.25 + 880.0),  # 880 + 659.25 intermod
]


def load(path: str) -> np.ndarray:
    """Load Audacity Sample Data Export (dB, stereo, tab-separated)."""
    rows = []
    with open(path) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            vals = []
            for p in parts:
                p = p.strip().lstrip("[").rstrip("]")
                vals.append(-120.0 if p in ("-inf", "inf") else float(p))
            rows.append(vals)
    return 10 ** (np.array(rows) / 20)  # linear amplitude


def tone_freq(x: np.ndarray, f: float, nseg: int = 12):
    """Precise frequency of component f via demodulation + phase slope.

    Returns (df_hz, sd_hz, amp_db): deviation from nominal, bootstrap sd, level.
    """
    N = len(x)
    L = N // nseg
    n = np.arange(N)
    z = x * np.exp(-2j * np.pi * f * n / FS)
    ph, amps = [], []
    for k in range(nseg):
        m = z[k * L:(k + 1) * L].mean()
        ph.append(np.angle(m))
        amps.append(abs(m))
    ph = np.array(ph)
    amps = np.array(amps)
    unw = ph.copy()
    for i in range(1, len(unw)):
        unw[i] = unw[i - 1] + ((ph[i] - ph[i - 1] + np.pi) % (2 * np.pi) - np.pi)
    t = (np.arange(nseg) + 0.5) * L / FS
    w = amps ** 2
    w /= w.sum()
    sx, sy = (w * t).sum(), (w * unw).sum()
    sxx, sxy = (w * t * t).sum(), (w * t * unw).sum()
    slope = (sxy - sx * sy) / (sxx - sx * sx)
    df = slope / (2 * np.pi)
    rng = np.random.default_rng(42)
    bs = []
    for _ in range(200):
        idx = rng.integers(0, nseg, nseg)
        tb, ub = t[idx], unw[idx]
        wb = amps[idx] ** 2
        wb /= wb.sum()
        sxb, syb = (wb * tb).sum(), (wb * ub).sum()
        sxxb, sxyb = (wb * tb * tb).sum(), (wb * tb * ub).sum()
        bs.append(((sxyb - sxb * syb) / (sxxb - sxb * sxb)) / (2 * np.pi))
    return df, float(np.std(bs)), 20 * np.log10(amps.mean() + 1e-12)


def click_onset(x: np.ndarray, lo: int, hi: int, factor: float = 8.0):
    """First sample of 5 consecutive envelope values above factor x pre-click base."""
    env = np.convolve(np.abs(x), np.ones(4) / 4, mode="same")
    base = np.median(env[lo:lo + 400])
    for i in range(lo, hi):
        if all(env[i + j] > base * factor for j in range(5)):
            return i
    return None


def main(paths):
    per_file = {}
    for path in paths:
        lin = load(path)
        print(f"\n=== {path} ({len(lin)} samples, {len(lin)/FS:.2f} s) ===")
        # click onset per channel
        onsets = []
        # locate click globally first: strongest 4-sample rise
        rise = np.abs(np.diff(lin, axis=0)).max(axis=1)
        click = int(np.argmax(rise))
        lo = max(0, click - 1000)
        for c in range(2):
            o = click_onset(lin[:, c], lo, click + 500)
            onsets.append(o)
            print(f"  mic ch{c}: click onset at sample {o}")
        if all(o is not None for o in onsets):
            skew = (onsets[0] - onsets[1]) / FS * 1000
            print(f"  L-R onset skew: {skew:+.3f} ms (mic geometry, not phone sync)")
        # per-phone clock skew
        est = {}
        for f, label, _src in COMPONENTS:
            dfs = []
            for c in range(2):
                x = lin[:, c] - lin[:, c].mean()
                df, sd, amp_db = tone_freq(x, f)
                dfs.append(df)
                print(f"  {label} @ {f:8.2f} Hz ch{c}: df={df:+.4f} Hz  "
                      f"({df/f*1e6:+7.1f} ppm)  level={amp_db:.1f} dB  sd={sd:.4f} Hz")
            est[label] = float(np.mean(dfs) / f * 1e6)
        if len(est) == 2:
            (a, b) = est.values()
            d = a - b
            print(f"  clock skew: {list(est)[0]} {a:+.1f} ppm, {list(est)[1]} {b:+.1f} ppm")
            print(f"  >>> PHONE-TO-PHONE DIFFERENCE: {d:+.1f} ppm")
            for t_s, lbl in [(30, "30 s"), (600, "10 min"), (3600, "1 h")]:
                print(f"      projected drift over {lbl}: {abs(d)*1e-6*t_s*1000:.2f} ms")
            per_file[path] = {"ppm": est, "A_minus_B_ppm": d, "onsets": onsets}
    if len(per_file) >= 2:
        vals = [v["A_minus_B_ppm"] for v in per_file.values()]
        print(f"\nMean A-B over {len(vals)} files: {np.mean(vals):+.1f} ppm "
              f"(spread {np.ptp(vals):.1f} ppm = measurement noise)")
    json.dump(per_file, open("sync_results.json", "w"), indent=2)
    print("\nDetails written to sync_results.json")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
