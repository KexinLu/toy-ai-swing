from fastapi import FastAPI, Request, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydub import AudioSegment
from pydub.playback import play
import yt_dlp
import threading
import time
import os
import glob
import asyncio
from enum import StrEnum

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

class SwingPattern(StrEnum):
    SINE = "sine"
    PARABOLA = "parabola"
    CUBIC = "cubic"
    TRIANGLE = "triangle"
    EXPONENTIAL = "exponential"
    LOGARITHMIC = "logarithmic"

class SwingPlayer:
    def __init__(self):
        import pygame
        pygame.mixer.init()
        self.pygame = pygame
        self.channel = None
        self.sound = None
        self.swing = False
        self.loop = False
        self.pan = 0.0
        self.volume = 1.0
        self.min_volume = 0.2  # Default minimum volume
        self.interval = 2.0
        self.thread = None
        self.running = False
        self.websocket = None
        self.pattern = SwingPattern.SINE  # Default pattern

    def load(self, path):
        self.sound = self.pygame.mixer.Sound(path)

    def play(self):
        if self.channel is None or not self.channel.get_busy():
            loops = -1 if self.loop else 0
            self.channel = self.sound.play(loops=loops)
            self.channel.set_volume(self.volume, self.volume)
            self.running = True
            self.thread = threading.Thread(target=self._swing_loop)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.channel:
            self.channel.stop()

    def _get_pattern_value(self, t, f):
        import math
        if self.pattern == SwingPattern.SINE:
            return math.sin(2 * math.pi * f * t)
        elif self.pattern == SwingPattern.PARABOLA:
            # Parabola pattern: slow in middle, fast at edges
            x = math.sin(2 * math.pi * f * t)
            return x * abs(x)  # This creates a parabolic curve
        elif self.pattern == SwingPattern.CUBIC:
            # Cubic pattern: fast in middle, slow at edges
            x = math.sin(2 * math.pi * f * t)
            return x * x * x
        elif self.pattern == SwingPattern.TRIANGLE:
            # Triangle wave: linear transitions
            x = (2 * math.pi * f * t) % (2 * math.pi)
            return 2 * abs(x / math.pi - 1) - 1
        elif self.pattern == SwingPattern.EXPONENTIAL:
            # Exponential pattern: very slow at edges, fast in middle
            x = math.sin(2 * math.pi * f * t)
            return math.copysign(abs(x) ** 0.5, x)
        elif self.pattern == SwingPattern.LOGARITHMIC:
            # Logarithmic pattern: very fast at edges, slow in middle
            x = math.sin(2 * math.pi * f * t)
            return math.copysign(abs(x) ** 2, x)
        else:
            return math.sin(2 * math.pi * f * t)  # Default to sine

    def _swing_loop(self):
        import math
        import asyncio
        t = 0.0
        while self.running and self.swing:
            f = 1.0 / self.interval
            self.pan = self._get_pattern_value(t, f)
            # Calculate base volumes with smooth transition to min volume
            raw_left = 0.5 * (1.0 - self.pan) * self.volume
            raw_right = 0.5 * (1.0 + self.pan) * self.volume
            # Smooth transition to min volume using a sigmoid-like function
            min_vol = self.min_volume * self.volume
            left = min_vol + (1 - min_vol) * (raw_left ** 2)
            right = min_vol + (1 - min_vol) * (raw_right ** 2)
            
            if self.channel:
                self.channel.set_volume(left, right)
                # Send volume updates through WebSocket
                if hasattr(self, 'websocket') and self.websocket:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self.websocket.send_json({
                                "action": "volume_update",
                                "left": float(left),  # Ensure we're sending float values
                                "right": float(right)
                            }),
                            self.loop
                        )
                    except Exception as e:
                        print(f"Error sending volume update: {e}")
            time.sleep(0.01)
            t += 0.01

    def set_pan(self, pan):
        import asyncio
        self.swing = False
        self.pan = max(-1.0, min(1.0, pan))
        # Calculate base volumes with smooth transition to min volume
        raw_left = 0.5 * (1.0 - self.pan) * self.volume
        raw_right = 0.5 * (1.0 + self.pan) * self.volume
        # Smooth transition to min volume using a sigmoid-like function
        min_vol = self.min_volume * self.volume
        left = min_vol + (1 - min_vol) * (raw_left ** 2)
        right = min_vol + (1 - min_vol) * (raw_right ** 2)
        
        if self.channel:
            self.channel.set_volume(left, right)
            # Send volume updates through WebSocket
            if hasattr(self, 'websocket') and self.websocket:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.websocket.send_json({
                            "action": "volume_update",
                            "left": float(left),  # Ensure we're sending float values
                            "right": float(right)
                        }),
                        self.loop
                    )
                except Exception as e:
                    print(f"Error sending volume update: {e}")

    def enable_auto_swing(self, interval=2.0):
        self.swing = True
        self.interval = interval
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._swing_loop)
            self.thread.start()

    def disable_auto_swing(self):
        self.swing = False

    def set_loop(self, loop: bool):
        self.loop = loop

    def set_volume(self, volume: float):
        self.volume = max(0.0, min(1.0, volume))
        # Apply volume update with current pan
        self.set_pan(self.pan)

    def set_pattern(self, pattern: str) -> bool:
        try:
            self.pattern = SwingPattern(pattern)
            return True
        except ValueError:
            return False


audio_controller = SwingPlayer()


def download_audio(url: str, name: str):
    output_file = f"downloads/{name}.%(ext)s"
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': output_file,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'quiet': True
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])


def list_downloads():
    return [os.path.basename(f) for f in glob.glob("downloads/*.mp3")]


@app.get("/", response_class=HTMLResponse)
def get_ui():
    with open("static/index.html") as f:
        return f.read()


@app.get("/download")
def api_download(url: str, name: str):
    os.makedirs("downloads", exist_ok=True)
    download_audio(url, name)
    return {"status": "downloaded", "files": list_downloads()}


@app.get("/list")
def api_list():
    return {"files": list_downloads()}


@app.get("/load")
def api_load(file: str):
    path = os.path.join("downloads", file)
    if not os.path.exists(path):
        return {"error": "File not found"}
    audio_controller.load(path)
    return {"status": "loaded"}


@app.get("/play")
def api_play():
    audio_controller.play()
    return {"status": "playing"}


@app.get("/stop")
def api_stop():
    audio_controller.stop()
    return {"status": "stopped"}


@app.get("/set_pan")
def api_set_pan(value: float = Query(..., ge=-1.0, le=1.0)):
    audio_controller.set_pan(value)
    return {"status": "pan set", "value": value}


@app.get("/toggle_swing")
def api_toggle_swing():
    audio_controller.swing = not audio_controller.swing
    return {"swinging": audio_controller.swing}


@app.get("/set_loop")
def api_set_loop(enable: bool):
    audio_controller.set_loop(enable)
    return {"loop": audio_controller.loop}


@app.get("/set_volume")
def api_set_volume(value: float = Query(..., ge=0.0, le=1.0)):
    audio_controller.set_volume(value)
    return {"status": "volume set", "value": value}


@app.get("/set_min_volume")
def api_set_min_volume(value: float = Query(..., ge=0.0, le=1.0)):
    audio_controller.min_volume = value
    return {"status": "min volume set", "value": value}


@app.get("/set_pattern")
def api_set_pattern(pattern: str):
    if audio_controller.set_pattern(pattern):
        return {"status": "pattern set", "value": pattern}
    return {"error": "invalid pattern"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        audio_controller.websocket = websocket
        audio_controller.loop = asyncio.get_event_loop()
        while True:
            msg = await websocket.receive_json()
            action = msg.get("action")

            if action == "pan":
                audio_controller.set_pan(float(msg["value"]))
            elif action == "swing":
                interval = float(msg.get("interval", 0.1))
                audio_controller.enable_auto_swing(interval)
            elif action == "stop_swing":
                audio_controller.disable_auto_swing()
    except WebSocketDisconnect:
        audio_controller.disable_auto_swing()
