from __future__ import annotations
import math
import struct
import wave
from pathlib import Path

RATE = 48000
DURATION = 30
FREQS = [440.0, 554.37, 659.25, 880.0]
AMP = 0.12
CLICK_AMP = 0.8

out = Path(__file__).resolve().parents[1] / "test_4ch.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(4)
    w.setsampwidth(2)
    w.setframerate(RATE)
    for i in range(RATE * DURATION):
        frame = []
        for ch, freq in enumerate(FREQS):
            v = AMP * math.sin(2 * math.pi * freq * i / RATE)
            # Same-sample impulse on all channels once per second: useful for sync measurements.
            if i % RATE < 8:
                v += CLICK_AMP * (1.0 - (i % RATE) / 8)
            v = max(-1.0, min(1.0, v))
            frame.append(int(v * 32767))
        w.writeframesraw(struct.pack("<hhhh", *frame))
print(out)
