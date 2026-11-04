from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import wave

@dataclass
class WavInfo:
    sample_rate: int
    channels: int
    bit_depth: int

class WavSource:
    def __init__(self, path: str | Path, chunk_ms: int = 20):
        self.path = Path(path)
        self.wav = wave.open(str(self.path), "rb")
        if self.wav.getcomptype() != "NONE":
            raise ValueError("PoC requires uncompressed PCM WAV")
        if self.wav.getsampwidth() != 2:
            raise ValueError("PoC requires 16-bit PCM WAV")
        self.info = WavInfo(self.wav.getframerate(), self.wav.getnchannels(), 16)
        self.frames_per_chunk = max(1, round(self.info.sample_rate * chunk_ms / 1000))

    def read(self) -> bytes:
        return self.wav.readframes(self.frames_per_chunk)

    def close(self) -> None:
        self.wav.close()
