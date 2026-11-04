"""Loopback end-to-end test: two simulated Sendspin player clients connect to the
OpenSurround PoC server and receive different stereo pairs from one 4-ch WAV.

Verifies without hardware:
- server handshake, client discovery by name, auto-trust, grouping
- channel routing (client A gets ch 0/1, client B gets ch 2/3)
- both clients receive identical presentation timestamps (shared timeline)
"""
from __future__ import annotations

import asyncio
import logging
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

PORT = 8931


def make_wav(path: Path, seconds: float = 4.0) -> None:
    rate = 48000
    with wave.open(str(path), "wb") as w:
        w.setnchannels(4)
        w.setsampwidth(2)
        w.setframerate(rate)
        for i in range(int(rate * seconds)):
            frame = []
            for ch in range(4):
                # distinct amplitude per channel so routing is provable
                frame.append(((i // 96 + ch + 1) % 100) * 300 - 1500 if ch else 12000)
            w.writeframesraw(struct.pack("<hhhh", *frame))


class Collector:
    def __init__(self) -> None:
        self.chunks: list[tuple[int, bytes]] = []
        self.format: ServerAudioFormat | None = None

    def __call__(self, server_timestamp_us: int, data: bytes, fmt, send_ahead: int) -> None:
        self.chunks.append((server_timestamp_us, data))
        self.format = fmt


async def main() -> None:
    state = Path("/tmp/opensurround-loopback")
    import shutil
    shutil.rmtree(state, ignore_errors=True)

    wav = state / "loop.wav"
    state.mkdir(parents=True, exist_ok=True)
    make_wav(wav)

    config = Config(
        server_name="loopback-test",
        host="127.0.0.1",
        port=PORT,
        chunk_ms=20,
        buffer_lead_ms=500,
        auto_trust_unpaired=True,
        persist_pairings=False,
        endpoints=(
            EndpointConfig("front", "LoopFront", (0, 1)),
            EndpointConfig("rear", "LoopRear", (2, 3)),
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
        # The PoC server auto-trusts unpaired clients; admit that in the test client.
        await client.set_unpaired_access(enabled=True)
        collector = Collector()
        client.add_audio_chunk_listener(collector)
        await client.connect(f"ws://127.0.0.1:{PORT}/sendspin")
        return client, collector

    await asyncio.sleep(1.5)  # let the server come up
    clients = {}
    collectors: dict[str, Collector] = {}
    for name in ("LoopFront", "LoopRear"):
        clients[name], collectors[name] = await make_client(name)
    print("both clients connected")

    # probe time-sync and chunk flow before server streams
    conn_a = clients["LoopFront"]._admitted_connection
    for _ in range(16):
        await asyncio.sleep(0.25)
        synced = conn_a.is_time_synchronized() if conn_a else None
            # probe: clients must sync before audio flows
        print(f"probe: synced={synced} chunks_front={len(collectors['LoopFront'].chunks)} chunks_rear={len(collectors['LoopRear'].chunks)}")

    await asyncio.wait_for(server_task, timeout=60)

    # --- verify routing ---
    for name, (lo, hi) in {"LoopFront": (0, 1), "LoopRear": (2, 3)}.items():
        c = collectors[name]
        assert c.chunks, f"{name}: no audio received"
        fmt = c.format
        print(f"{name}: {len(c.chunks)} chunks, codec={fmt.codec}, pcm={fmt.pcm_format}")
        assert fmt.codec == AudioCodec.PCM
        assert (fmt.pcm_format.sample_rate, fmt.pcm_format.bit_depth, fmt.pcm_format.channels) == (48000, 16, 2)
        # expected stereo bytes for channels lo/hi from the wav
        with wave.open(str(wav), "rb") as w:
            raw = w.readframes(w.getnframes())
        samples = struct.iter_unpack("<hhhh", raw)
        expected = b"".join(struct.pack("<hh", s[lo], s[hi]) for s in samples)
        got = b"".join(data for _, data in c.chunks)
        assert got == expected, f"{name}: routed audio does not match source channels {lo}/{hi} (got {len(got)} vs {len(expected)} bytes)"
        print(f"{name}: audio matches source channels {lo}/{hi} exactly ({len(got)} bytes)")

    # --- verify shared timeline ---
    ts_front = [t for t, _ in collectors["LoopFront"].chunks]
    ts_rear = [t for t, _ in collectors["LoopRear"].chunks]
    assert ts_front == ts_rear, "presentation timestamps differ between clients"
    diffs = [b - a for a, b in zip(ts_front, ts_front[1:])]
    print(f"shared timeline OK: {len(ts_front)} identical timestamps, deltas {min(diffs)}..{max(diffs)} us (nominal 20000)")
    for c in clients.values():
        await c.close() if hasattr(c, "close") else None
    print("LOOPBACK E2E: PASSED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
asyncio.run(main())
