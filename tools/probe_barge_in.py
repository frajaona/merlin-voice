"""Multi-turn probe: question -> barge-in mid-answer -> question again.
Measures per-turn received speech and burstiness after an interruption."""
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
    """Silence by default; speak() queues PCM to send. Paced at 10ms."""

    def __init__(self):
        super().__init__()
        self._chunks = deque()
        self._ts = 0
        self._start = None

    def speak(self, pcm48k: np.ndarray):
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


async def tts(kokoro, text):
    parts = []
    async for samples, sr in kokoro.create_stream(text, voice="ff_siwis", lang="fr-fr", speed=1.0):
        parts.append((samples, sr))
    pcm = np.concatenate([p[0] for p in parts])
    ratio = SR // parts[0][1]
    return (np.repeat(pcm, ratio) * 32767 * 0.8).astype(np.int16)


async def main():
    kokoro = Kokoro(str(CACHE / "kokoro-v1.0.onnx"), str(CACHE / "voices-v1.0.bin"))
    q1 = await tts(kokoro, "Bonjour Merlin, explique-moi en détail tout ce que tu sais faire pour moi.")
    barge = await tts(kokoro, "Attends, attends, j'ai une autre question pour toi.")
    q2 = await tts(kokoro, "Peux-tu me décrire longuement comment tu contrôles la maison ?")

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
                pcm = frame.to_ndarray().flatten().astype(np.float32) / 32768.0
                rms = float(np.sqrt(np.mean(pcm ** 2)))
                # stereo-safe media duration
                ch = max(1, len(frame.layout.channels))
                received.append((time.time() - t0, rms, frame.samples / frame.sample_rate if hasattr(frame, "samples") else len(pcm) / ch / frame.sample_rate))

        asyncio.create_task(record())

    pc.addTrack(mic)
    # Mimic the browser client exactly: extra recvonly audio transceiver
    pc.addTransceiver("audio", direction="recvonly")
    dc = pc.createDataChannel("pipecat")

    @dc.on("open")
    def on_open():
        async def ping_loop():
            while dc.readyState == "open":
                dc.send("ping")
                await asyncio.sleep(1)  # match fixed browser client
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

    THRESH = 0.01

    def bot_speaking_now():
        recent = [r for r in received if r[0] > time.time() - t0 - 0.5]
        return any(r[1] > THRESH for r in recent)

    print("turn 1: asking question...")
    mic.speak(q1)
    # wait for bot to start speaking
    t_wait = time.time()
    while not bot_speaking_now() and time.time() - t_wait < 20:
        await asyncio.sleep(0.1)
    print(f"[{time.time()-t0:6.2f}s] bot started answering; barging in after 1.5s of speech")
    await asyncio.sleep(1.5)
    mic.speak(barge)  # real 5+ word interruption
    await asyncio.sleep(8)

    print(f"[{time.time()-t0:6.2f}s] turn 3: asking long question post-interruption...")
    t_q2 = time.time() - t0
    mic.speak(q2)
    await asyncio.sleep(25)
    await pc.close()

    # Report: burstiness and speech after t_q2
    speech_after = sum(dur for t, rms, dur in received if t > t_q2 and rms > THRESH)
    print(f"\nspeech received after final question: {speech_after:.1f}s")
    buckets = {}
    for t, rms, dur in received:
        if rms > THRESH:
            buckets.setdefault(int(t / 0.25), 0.0)
            buckets[int(t / 0.25)] += dur
    worst = sorted(buckets.items(), key=lambda kv: -kv[1])[:6]
    print("worst 250ms buckets by SPEECH media received:")
    for k, m in worst:
        print(f"  t={k*0.25:7.2f}s  speech_media={m:.2f}s {'  <-- BURST' if m > 0.4 else ''}")
    # continuity timeline of speech
    segs = []
    cur = None
    for t, rms, dur in received:
        sp = rms > THRESH
        if cur and cur[2] == sp and t - cur[1] < 0.3:
            cur[1] = t
        else:
            if cur:
                segs.append(cur)
            cur = [t, t, sp]
    if cur:
        segs.append(cur)
    print("speech timeline:")
    for a, b, s in segs:
        if s and b - a > 0.3:
            print(f"  {a:7.2f}s -> {b:7.2f}s ({b-a:.2f}s)")

asyncio.run(main())
