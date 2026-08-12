"""Probe: ask a current-info question, verify web_search fires and answer plays."""
import asyncio
import fractions
import json
import ssl
import time
import urllib.request
from collections import deque
from pathlib import Path
import os

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from av import AudioFrame
from kokoro_onnx import Kokoro

CACHE = Path(os.path.expanduser("~/.cache/kokoro-onnx"))
SERVER = "https://127.0.0.1:7860/api/offer"
SR = 48000
SAMPLES_10MS = SR // 100


class MicTrack(AudioStreamTrack):
    def __init__(self):
        super().__init__()
        self._chunks = deque()
        self._ts = 0
        self._start = None

    def speak(self, pcm48k):
        for i in range(0, len(pcm48k) - SAMPLES_10MS, SAMPLES_10MS):
            self._chunks.append(pcm48k[i:i + SAMPLES_10MS])

    async def recv(self):
        if self._start is None:
            self._start = time.time()
        wait = self._start + self._ts / SR - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        elif wait < -0.1:
            self._start = time.time() - self._ts / SR
        samples = self._chunks.popleft() if self._chunks else np.zeros(SAMPLES_10MS, dtype=np.int16)
        frame = AudioFrame.from_ndarray(samples[None, :], format="s16", layout="mono")
        frame.sample_rate = SR
        frame.pts = self._ts
        frame.time_base = fractions.Fraction(1, SR)
        self._ts += SAMPLES_10MS
        return frame


async def main():
    kokoro = Kokoro(str(CACHE / "kokoro-v1.0.onnx"), str(CACHE / "voices-v1.0.bin"))
    parts = []
    async for s, sr in kokoro.create_stream(
        "Quelles sont les grandes actualités du jour ?", voice="ff_siwis", lang="fr-fr", speed=1.0
    ):
        parts.append(s)
    pcm = np.concatenate(parts)
    q = (np.repeat(pcm, SR // 24000) * 32767 * 0.8).astype(np.int16)

    mic = MicTrack()
    pc = RTCPeerConnection()
    received = []
    t0 = time.time()

    @pc.on("track")
    def on_track(track):
        if track.kind != "audio":
            return

        async def record():
            while True:
                try:
                    frame = await track.recv()
                except Exception:
                    return
                p = frame.to_ndarray().flatten().astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(p ** 2)))
                ch = max(1, len(frame.layout.channels))
                received.append((time.time() - t0, rms, len(p) / ch / frame.sample_rate))

        asyncio.create_task(record())

    pc.addTrack(mic)
    pc.addTransceiver("audio", direction="recvonly")
    dc = pc.createDataChannel("pipecat")

    @dc.on("open")
    def on_open():
        async def ping_loop():
            while dc.readyState == "open":
                dc.send("ping")
                await asyncio.sleep(1)
        asyncio.create_task(ping_loop())

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(SERVER, data=json.dumps({
        "sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
    }).encode(), headers={"Content-Type": "application/json"})
    resp = json.loads(urllib.request.urlopen(req, context=ctx, timeout=10).read())
    await pc.setRemoteDescription(RTCSessionDescription(sdp=resp["sdp"], type=resp["type"]))

    print("asking: Quelles sont les grandes actualités du jour ?")
    mic.speak(q)
    await asyncio.sleep(30)
    await pc.close()

    speech = sum(dur for t, rms, dur in received if rms > 0.01)
    print(f"total bot speech received: {speech:.1f}s")

asyncio.run(main())
