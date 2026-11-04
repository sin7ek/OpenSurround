"""Loopback end-to-end test for 5.1: three simulated Sendspin player clients
connect to the OpenSurround PoC server and each receives its stereo pair from
one 6-channel (ITU-R BS.775 order) WAV.

Verifies without hardware:
- server handshake, discovery by name, auto-trust, grouping with 3 clients
- 5.1 routing: front L/R (ch 0/1), center+LFE (ch 2/3), rear Ls/Rs (ch 4/5)
- all clients receive identical presentation timestamps (shared timeline)
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import struct
import wave
from pathlib import Path

from aiosendspin.client.client import SendspinClient
from aiosendspin.models.player import ClientHelloPlayerSupport, SupportedAudioFormat
from aiosendspin.models.types import AudioCodec, Roles
from aiosendspin.noise.keys import Identity
from aiosendspin.noise.trust_store import InMemoryClientPairingStore
from aiosendspin.server import AudioFormat as ServerAudioFormat

from opensurround.config import EndpointConfig, Config
from opensurround.server import run

PORT = 8932
ROUTES = {"LoopFront": (0, 1), "LoopCenterSub": (2, 3), "LoopRear": (4, 5)}


def make_wav(path: Path, seconds: float = 4.0) -> None:
    rate = 48000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(6)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(int(rate * seconds)):
            frame = [((i // 96 + ch + 1) % 100) * 300 - 1500 for ch in range(6)]
            frame[0] = 12000  # make channel 0 trivially identifiable
            w.writeframesraw(struct.pack("<hhhhhh", *frame))


class Collector:
    def __init__(self) -> None:
        self.chunks: list[tuple[int, bytes]] = []
        self.format: ServerAudioFormat | None = None

    def __call__(self, server_timestamp_us: int, data: bytes, fmt, send_ahead: int) -> None:
        self.chunks.append((server_timestamp_us, data))
        self.format = fmt


async def main() -> None:
    state = Path("/tmp/opensurround-loopback-6ch")
    shutil.rmtree(state, ignore_errors=True)
    state.mkdir(parents=True, exist_ok=True)
    wav = state / "loop6.wav"
    make_wav(wav)

    config = Config(
        server_name="loopback-test-6ch",
        host="127.0.0.1",
        port=PORT,
        chunk_ms=20,
        buffer_lead_ms=500,
        auto_trust_unpaired=True,
        persist_pairings=False,
        endpoints=(
            EndpointConfig("front", "LoopFront", ROUTES["LoopFront"]),
            EndpointConfig("center-sub", "LoopCenterSub", ROUTES["LoopCenterSub"]),
            EndpointConfig("rear", "LoopRear", ROUTES["LoopRear"]),
        ),
    )

    server_task = asyncio.create_task(run(config, str(wav), str(state / "srv")))

    async def make_client(name: str) -> tuple[SendspinClient, Collector]:
        identity = Identity.generate()
        store = InMemoryClientPairingStore()
        support = ClientHelloPlayerSupport(
            supported_formats=[SupportedAudioFormat(codec=AudioCodec.PCM, channels=2, sample_rate=48000, bit_depth=16)],
            buffer_capacity=5_000_000,
        )
        client = SendspinClient(identity, name, [Roles.PLAYER], pairing_store=store, player_support=support)
        await client.set_unpaired_access(enabled=True)
        collector = Collector()
        client.add_audio_chunk_listener(collector)
        await client.connect(f"ws://127.0.0.1:{PORT}/sendspin")
        return client, collector

    await asyncio.sleep(1.5)
    clients: dict[str, SendspinClient] = {}
    collectors: dict[str, Collector] = {}
    for name in ROUTES:
        clients[name], collectors[name] = await make_client(name)
    print("all three clients connected")

    await asyncio.wait_for(server_task, timeout=60)

    with wave.open(str(wav), "rb") as w:
        raw = w.readframes(w.getnframes())

    for name, (lo, hi) in ROUTES.items():
        c = collectors[name]
        assert c.chunks, f"{name}: no audio received"
        fmt = c.format
        assert fmt.codec == AudioCodec.PCM
        assert (fmt.pcm_format.sample_rate, fmt.pcm_format.bit_depth, fmt.pcm_format.channels) == (48000, 16, 2)
        samples = struct.iter_unpack("<hhhhhh", raw)
        expected = b"".join(struct.pack("<hh", s[lo], s[hi]) for s in samples)
        got = b"".join(data for _, data in c.chunks)
        assert got == expected, f"{name}: routed audio does not match source channels {lo}/{hi}"
        print(f"{name}: audio matches source channels {lo}/{hi} exactly ({len(got)} bytes)")

    timelines = [[t for t, _ in collectors[n].chunks] for n in ROUTES]
    for other in timelines[1:]:
        assert timelines[0] == other, "presentation timestamps differ between clients"
    ts = timelines[0]
    diffs = [b - a for a, b in zip(ts, ts[1:])]
    print(f"shared timeline OK: {len(ts)} identical timestamps across 3 clients, deltas {min(diffs)}..{max(diffs)} us")
    for c in clients.values():
        if hasattr(c, "close"):
            await c.close()
    print("LOOPBACK E2E 6CH (5.1): PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
