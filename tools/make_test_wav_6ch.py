from __future__ import annotations
import math
import struct
import wave
from pathlib import Path

RATE = 48000
DURATION = 30  # set to 600 for the 10-minute drift test
AMP = 0.12
CLICK_AMP = 0.8
LFE_AMP = 0.25  # extra headroom: low tones lose a lot on small phone speakers

# Standard 5.1 channel order (ITU-R BS.775 / WAV convention).
# index: ITU label / Dolby label  / test tone
#   0:   L   / FL    / 440.00 Hz (A4)
#   1:   R   / FR    / 554.37 Hz (C#5)
#   2:   C   / C     / 659.25 Hz (E5)
#   3:   LFE / LFE   /  73.42 Hz (D2 - a real sub tone; barely audible on
#                                  phone speakers, that is expected)
#   4:   Ls  / SL    / 783.99 Hz (G5)
#   5:   Rs  / SR    / 987.77 Hz (B5)
CHANNELS = [
    ("L", "FL", 440.00, AMP),
    ("R", "FR", 554.37, AMP),
    ("C", "C", 659.25, AMP),
    ("LFE", "LFE", 73.42, LFE_AMP),
    ("Ls", "SL", 783.99, AMP),
    ("Rs", "SR", 987.77, AMP),
]

out = Path(__file__).resolve().parents[1] / "test_6ch.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(6)
    w.setsampwidth(2)
    w.setframerate(RATE)
    for i in range(RATE * DURATION):
        frame = []
        for _, _, freq, amp in CHANNELS:
            v = amp * math.sin(2 * math.pi * freq * i / RATE)
            # Same-sample impulse on all channels once per second: useful for sync measurements.
            if i % RATE < 8:
                v += CLICK_AMP * (1.0 - (i % RATE) / 8)
            v = max(-1.0, min(1.0, v))
            frame.append(int(v * 32767))
        w.writeframesraw(struct.pack("<hhhhhh", *frame))
print(f"{out} ({RATE} Hz, 6 ch, {DURATION} s)")
print("channel order: " + ", ".join(f"{i}:{lab}({dolby}) {freq:g}Hz" for i, (lab, dolby, freq, _) in enumerate(CHANNELS)))
