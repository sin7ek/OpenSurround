from __future__ import annotations
import numpy as np


def split_int16_interleaved(raw: bytes, input_channels: int, routes: dict[str, tuple[int, ...]]) -> dict[str, bytes]:
    """Route source channels of interleaved 16-bit PCM to per-endpoint PCM.

    Each route maps 1 (mono) or 2 (stereo) source channel indexes to one endpoint.
    """
    samples = np.frombuffer(raw, dtype="<i2")
    if samples.size % input_channels:
        raise ValueError("input buffer is not frame-aligned")
    frames = samples.reshape((-1, input_channels))
    out: dict[str, bytes] = {}
    for name, channels in routes.items():
        if not 1 <= len(channels) <= 2:
            raise ValueError(f"route {name}: source_channels must contain 1 or 2 channels")
        if min(channels) < 0 or max(channels) >= input_channels:
            raise ValueError(f"route {name}: channel index out of range")
        routed = np.ascontiguousarray(frames[:, list(channels)], dtype="<i2")
        out[name] = routed.tobytes()
    return out


def gain_int16(pcm: bytes, gain_db: float) -> bytes:
    """Apply gain in dB to interleaved 16-bit PCM with clipping."""
    if gain_db == 0.0:
        return pcm
    factor = 10.0 ** (gain_db / 20.0)
    out = np.frombuffer(pcm, dtype="<i2").astype(np.float64) * factor
    np.clip(out, -32768, 32767, out)
    return out.astype("<i2").tobytes()


class ChunkFeeder:
    """Fixed-size chunk FIFO.

    Used to apply a per-endpoint delay: silence is fed first, real audio after,
    and equal-size chunks come out, keeping every endpoint on the same shared
    timeline (the delayed endpoint simply receives silence where the others
    already play).
    """

    def __init__(self, chunk_bytes: int) -> None:
        self.chunk_bytes = chunk_bytes
        self._buf = bytearray()

    def feed(self, data: bytes) -> None:
        self._buf += data

    def pull(self) -> bytes | None:
        """Return the next full chunk, or None if not enough data is buffered."""
        if len(self._buf) < self.chunk_bytes:
            return None
        out = bytes(self._buf[: self.chunk_bytes])
        del self._buf[: self.chunk_bytes]
        return out

    def pull_final(self) -> bytes | None:
        """Return whatever remains (< one chunk) at end of stream."""
        if not self._buf:
            return None
        out = bytes(self._buf)
        self._buf.clear()
        return out
