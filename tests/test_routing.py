import struct
import numpy as np
from opensurround.routing import split_int16_interleaved, gain_int16, ChunkFeeder


def test_split_stereo():
    raw = struct.pack("<hhhhhhhh", 1, 2, 3, 4, 5, 6, 7, 8)
    out = split_int16_interleaved(raw, 4, {"front": (0, 1), "rear": (2, 3)})
    assert struct.unpack("<hhhh", out["front"]) == (1, 2, 5, 6)
    assert struct.unpack("<hhhh", out["rear"]) == (3, 4, 7, 8)


def test_split_mono():
    raw = struct.pack("<hhhhhhhh", 1, 2, 3, 4, 5, 6, 7, 8)
    out = split_int16_interleaved(raw, 4, {"center": (2,)})
    assert struct.unpack("<hh", out["center"]) == (3, 7)


def test_gain():
    pcm = struct.pack("<hh", 1000, -1000)
    out = gain_int16(pcm, 20 * np.log10(2))  # exactly factor 2
    l, r = struct.unpack("<hh", out)
    assert abs(l - 2000) <= 1 and abs(r + 2000) <= 1
    assert gain_int16(pcm, 0.0) == pcm


def test_gain_clips():
    pcm = struct.pack("<h", 30000)
    out = gain_int16(pcm, 12.0)  # factor ~4 -> 120000 -> clip
    assert struct.unpack("<h", out)[0] == 32767


def test_feeder_delay_and_chunks():
    f = ChunkFeeder(8)  # 2 stereo frames per chunk (4 bytes/frame)
    f.feed(b"\x00" * 4)  # 1 frame of delay silence
    f.feed(struct.pack("<hh", 11, 22))
    assert f.pull() == b"\x00\x00\x00\x00" + struct.pack("<hh", 11, 22)  # delay then first frame
    assert f.pull() is None
    f.feed(struct.pack("<hh", 33, 44))
    assert f.pull() is None  # only 4 bytes buffered, less than a chunk
    assert f.pull_final() == struct.pack("<hh", 33, 44)
    assert f.pull_final() is None


def test_split_5_1():
    """ITU-R BS.775 / WAV 5.1 order: 0 L, 1 R, 2 C, 3 LFE, 4 Ls, 5 Rs."""
    raw = struct.pack("<hhhhhhhhhhhh", 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
    out = split_int16_interleaved(raw, 6, {"front": (0, 1), "center-sub": (2, 3), "rear": (4, 5)})
    assert struct.unpack("<hhhh", out["front"]) == (1, 2, 7, 8)
    assert struct.unpack("<hhhh", out["center-sub"]) == (3, 4, 9, 10)
    assert struct.unpack("<hhhh", out["rear"]) == (5, 6, 11, 12)


def test_split_out_of_range_6ch():
    import pytest
    raw = struct.pack("<hhhhhh", 1, 2, 3, 4, 5, 6)
    with pytest.raises(ValueError):
        split_int16_interleaved(raw, 6, {"bad": (2, 6)})
