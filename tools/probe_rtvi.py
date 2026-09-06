"""Headless dashboard-client probe: connects to the bot exactly like
static/index.html does (authed offer, 'pipecat' datachannel, raw-ping
keep-alive, RTVI client-ready) and prints every RTVI message type received.

Optionally plays a WAV file as the microphone to exercise STT -> VoiceGate ->
gate-decision server-messages.

Usage (bot must be running): venv/bin/python tools/probe_rtvi.py [speech.wav | "message texte"]
- no argument: handshake only (expects bot-ready)
- a .wav path: plays it as the mic, expects a gate-decision server-message
- any other string: sends it as a typed chat message (RTVI send-text,
  audio_response=false) and expects a silent bot-llm-text reply
A test utterance: say -v Thomas "Olympia, quelle heure est-il ?" -o /tmp/u.wav --data-format=LEI16@16000
Session language: MERLIN_PROBE_LANG=en (default fr) — sent as `lang` in the
offer body, like the dashboard toggle.
"""
import os
import asyncio
import fractions
import json
import ssl
import sys
import time
import urllib.request
import wave
from pathlib import Path

import numpy as np
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamTrack
from av import AudioFrame

BASE = "https://localhost:7860"
TOKEN = (Path(__file__).resolve().parent.parent / "data" / "auth-token").read_text().strip()

SAMPLE_RATE = 48000
SAMPLES_20MS = SAMPLE_RATE // 50


class WavThenSilenceTrack(MediaStreamTrack):
    """Plays an optional 16kHz mono WAV (resampled to 48k) after 1s of
    silence, then silence forever. Paced in real time."""

    kind = "audio"

    def __init__(self, wav_path: str | None):
        super().__init__()
        self._ts = 0
        self._start = None
        self._pcm = np.zeros(0, dtype=np.int16)
        if wav_path:
            with wave.open(wav_path) as w:
                assert w.getnchannels() == 1
                rate = w.getframerate()
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
            idx = np.linspace(0, len(pcm) - 1, int(len(pcm) * SAMPLE_RATE / rate))
            resampled = np.interp(idx, np.arange(len(pcm)), pcm.astype(np.float32))
            lead = np.zeros(SAMPLE_RATE, dtype=np.int16)  # 1s of silence first
            self._pcm = np.concatenate([lead, resampled.astype(np.int16)])

    async def recv(self):
        if self._start is None:
            self._start = time.time()
        wait = self._start + self._ts / SAMPLE_RATE - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        chunk = np.zeros(SAMPLES_20MS, dtype=np.int16)
        if self._ts < len(self._pcm):
            avail = self._pcm[self._ts:self._ts + SAMPLES_20MS]
            chunk[:len(avail)] = avail
        frame = AudioFrame.from_ndarray(chunk[None, :], format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = self._ts
        frame.time_base = fractions.Fraction(1, SAMPLE_RATE)
        self._ts += SAMPLES_20MS
        return frame


async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    wav = arg if arg and arg.endswith(".wav") else None
    chat = arg if arg and not wav else None
    received = []
    pc = RTCPeerConnection()
    pc.addTrack(WavThenSilenceTrack(wav))
    pc.addTransceiver("audio", direction="recvonly")
    dc = pc.createDataChannel("pipecat")

    @dc.on("open")
    def on_open():
        dc.send("ping")
        dc.send(json.dumps({
            "label": "rtvi-ai", "type": "client-ready", "id": "cr-1",
            "data": {"version": "1.4.0", "about": {"library": "probe"}},
        }))

    @dc.on("message")
    def on_message(message):
        try:
            msg = json.loads(message)
        except Exception:
            return
        received.append(msg)
        if msg.get("type") in ("bot-llm-text", "metrics"):
            return  # too chatty to print (one message per token)
        extra = ""
        if msg.get("type") == "server-message":
            extra = " " + json.dumps(msg.get("data"), ensure_ascii=False)
        elif msg.get("type") in ("user-transcription", "bot-tts-text"):
            extra = " " + json.dumps((msg.get("data") or {}).get("text"), ensure_ascii=False)
        print(f"<< {msg.get('type')}{extra}")

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    while pc.iceGatheringState != "complete":
        await asyncio.sleep(0.05)

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        BASE + "/api/offer",
        data=json.dumps({
            "sdp": pc.localDescription.sdp, "type": pc.localDescription.type,
            "lang": os.getenv("MERLIN_PROBE_LANG", "fr"),
        }).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    answer = json.loads(urllib.request.urlopen(req, context=ctx).read())
    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))

    async def keepalive():
        while True:
            await asyncio.sleep(1)
            if dc.readyState == "open":
                dc.send("ping")

    ka = asyncio.ensure_future(keepalive())

    if chat:
        for _ in range(100):  # wait for bot-ready before typing
            if any(m.get("type") == "bot-ready" for m in received):
                break
            await asyncio.sleep(0.1)
        dc.send(json.dumps({
            "label": "rtvi-ai", "type": "client-message", "id": "chat-1",
            "data": {"t": "chat", "d": {"text": chat, "speak": False}},
        }))

    await asyncio.sleep(35 if chat else 25 if wav else 8)  # tool turns need 2 LLM runs
    ka.cancel()
    await pc.close()

    types = {m.get("type") for m in received}
    print("\nRECEIVED TYPES:", sorted(types))
    assert "bot-ready" in types, "no bot-ready received"
    if wav:
        gates = [m for m in received if m.get("type") == "server-message"
                 and (m.get("data") or {}).get("event") == "gate-decision"]
        print("GATE DECISIONS:", json.dumps([m["data"] for m in gates], ensure_ascii=False, indent=1))
        assert gates, "no gate-decision received"
    if chat:
        reply = "".join((m.get("data") or {}).get("text") or ""
                        for m in received if m.get("type") == "bot-llm-text")
        print("CHAT REPLY:", json.dumps(reply, ensure_ascii=False))
        assert reply.strip(), "no bot-llm-text reply received"
        assert "bot-tts-started" not in types, "TTS ran despite audio_response=false"
    print("PROBE OK")


asyncio.run(main())
