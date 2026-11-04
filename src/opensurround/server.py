from __future__ import annotations
import asyncio
import logging
from pathlib import Path
from uuid import UUID, uuid4

from aiosendspin.noise.keys import Identity
from aiosendspin.noise.trust_store import FileServerPairingStore, InMemoryServerPairingStore
from aiosendspin.server import AudioFormat, SendspinServer

from .config import Config, EndpointConfig
from .routing import ChunkFeeder, gain_int16, split_int16_interleaved
from .wav_source import WavSource

_LOG = logging.getLogger("opensurround")


def _load_or_create_identity(path: Path) -> Identity:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return Identity.from_private_bytes(path.read_bytes())
    identity = Identity.generate()
    path.write_bytes(identity.private_bytes)
    return identity


async def _wait_for_named_clients(server: SendspinServer, config: Config, timeout: float = 120.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        connected = server.connected_clients
        found = {}
        for ep in config.endpoints:
            needle = ep.match_name.casefold()
            matches = [c for c in connected if needle in (c.name or "").casefold()]
            if len(matches) == 1:
                found[ep.name] = matches[0]
        if len(found) == len(config.endpoints):
            return found
        if asyncio.get_running_loop().time() >= deadline:
            visible = [f"{c.name} ({c.client_id})" for c in connected]
            raise TimeoutError(f"Timed out waiting for endpoints. Connected: {visible}")
        await asyncio.sleep(0.5)


def _make_feeder(ep: EndpointConfig, sample_rate: int, frames_per_chunk: int) -> ChunkFeeder:
    """Build a chunk feeder for one endpoint, pre-filled with its delay as silence."""
    bytes_per_frame = 2 * len(ep.source_channels)
    feeder = ChunkFeeder(frames_per_chunk * bytes_per_frame)
    delay_frames = int(round(sample_rate * ep.delay_ms / 1000.0))
    if delay_frames > 0:
        feeder.feed(b"\x00" * (delay_frames * bytes_per_frame))
    return feeder


async def run(config: Config, wav_path: str, state_dir: str = ".opensurround") -> None:
    loop = asyncio.get_running_loop()
    state = Path(state_dir)
    identity = _load_or_create_identity(state / "identity.key")
    if config.persist_pairings:
        pairing_store = await FileServerPairingStore.open(state / "pairings.json")
    else:
        # In-memory pairing store: every run starts clean. Persistent records break
        # subsequent runs with unencrypted (transition-mode) clients such as
        # Kiosk Satellite: once "known", an unencrypted connection claiming that
        # client_id is rejected as a possible identity spoof.
        pairing_store = InMemoryServerPairingStore()
    server = SendspinServer(
        loop,
        identity,
        config.server_name,
        pairing_store=pairing_store,
        allow_noncompliant_clients=True,
        allow_unencrypted=True,
    )
    await server.start_server(port=config.port, host=config.host, discover_clients=True)
    _LOG.info("Sendspin server listening on %s:%s", config.host, config.port)
    _LOG.info("Point Kiosk Satellite Sendspin 'Server' to <this-host>:%s if discovery picks Music Assistant instead.", config.port)

    try:
        clients = await _wait_for_named_clients(server, config)
        if config.auto_trust_unpaired:
            for client in clients.values():
                await server.trust_unpaired(client.client_id, approved_roles={"player"})
            await asyncio.sleep(1.0)

        leader = next(iter(clients.values()))
        group = leader.group
        for client in list(clients.values())[1:]:
            await group.add_client(client)

        channel_ids: dict[str, UUID] = {name: uuid4() for name in clients}
        client_to_channel = {clients[name].client_id: channel_ids[name] for name in clients}
        stream = group.start_stream(channel_resolver=lambda player_id: client_to_channel[player_id])

        source = WavSource(wav_path, config.chunk_ms)
        routes = {ep.name: ep.source_channels for ep in config.endpoints}
        fmts = {
            ep.name: AudioFormat(
                sample_rate=source.info.sample_rate,
                bit_depth=16,
                channels=len(ep.source_channels),
                sample_type="int",
            )
            for ep in config.endpoints
        }
        feeders = {
            ep.name: _make_feeder(ep, source.info.sample_rate, source.frames_per_chunk)
            for ep in config.endpoints
        }
        _LOG.info(
            "Input: %s Hz, %s channels. Routing: %s",
            source.info.sample_rate,
            source.info.channels,
            {ep.name: {"channels": ep.source_channels, "gain_db": ep.gain_db, "delay_ms": ep.delay_ms} for ep in config.endpoints},
        )

        first_start_us: int | None = None
        # Pacing strategy (fixes both observed hardware problems):
        # 1. The client buffers can only hold a few seconds (Kiosk Satellite:
        #    ~800 kB ≈ 4 s of PCM), so dumping the whole file at once plays a few
        #    seconds and then stops.
        # 2. Burst-sleep-burst filling produces ~1 s send gaps that stutter.
        # So: first commit a preroll of buffer_lead_ms of audio as fast as
        # possible (builds the client buffer), then send exactly one chunk per
        # chunk_ms in real time, keeping the client buffer at a steady lead.
        preroll_chunks = max(1, config.buffer_lead_ms // config.chunk_ms)
        sent = 0
        start_pace: float | None = None
        while raw := source.read():
            split = split_int16_interleaved(raw, source.info.channels, routes)
            for name, pcm in split.items():
                ep = next(e for e in config.endpoints if e.name == name)
                pcm = gain_int16(pcm, ep.gain_db)
                feeders[name].feed(pcm)
                chunk = feeders[name].pull()
                if chunk is not None:
                    stream.prepare_audio(chunk, fmts[name], channel_id=channel_ids[name])
            if first_start_us is None:
                first_start_us = await stream.commit_audio()
                _LOG.info("Playback timeline anchored at %d us", first_start_us)
            else:
                await stream.commit_audio()
            sent += 1
            if sent <= preroll_chunks:
                continue  # preroll: fill the client buffer without pausing
            if start_pace is None:
                start_pace = loop.time()
            # Real-time pacing: one chunk every chunk_ms, keeping the preroll
            # lead in the client buffer. If we fall behind (encoding hiccup),
            # do not burst to catch up — that recreates the sawtooth.
            next_due = start_pace + (sent - preroll_chunks) * config.chunk_ms / 1000.0
            now = loop.time()
            if next_due > now:
                await asyncio.sleep(next_due - now)
            else:
                start_pace = now - (sent - preroll_chunks) * config.chunk_ms / 1000.0

        # Flush any remaining partial chunk (per-endpoint delay tail).
        for name, feeder in feeders.items():
            chunk = feeder.pull_final()
            if chunk is not None:
                stream.prepare_audio(chunk, fmts[name], channel_id=channel_ids[name])
        await stream.commit_audio()
        source.close()
        _LOG.info("End of WAV; allowing buffered audio to drain")
        await asyncio.sleep(config.buffer_lead_ms / 1000.0 + 1.0)
        await group.stop()
    finally:
        await server.close()
