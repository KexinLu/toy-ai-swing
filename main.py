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

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

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
        self.interval = 2.0
        self.thread = None
        self.running = False

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

    def _swing_loop(self):
        import math
        t = 0.0
        while self.running and self.swing:
            f = 1.0 / self.interval
            self.pan = math.sin(2 * math.pi * f * t)
            left = 0.5 * (1.0 - self.pan) * self.volume
            right = 0.5 * (1.0 + self.pan) * self.volume
            if self.channel:
                self.channel.set_volume(left, right)
            time.sleep(0.01)
            t += 0.01

    def set_pan(self, pan):
        self.swing = False
        self.pan = max(-1.0, min(1.0, pan))
        left = 0.5 * (1.0 - self.pan) * self.volume
        right = 0.5 * (1.0 + self.pan) * self.volume
        if self.channel:
            self.channel.set_volume(left, right)

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


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
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
