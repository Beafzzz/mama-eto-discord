import asyncio
import json
import threading
import tkinter as tk
from tkinter import ttk
import cv2
import numpy as np
from PIL import Image, ImageTk
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack, AudioStreamTrack, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay, AudioFrame, VideoFrame
import websockets
import pyaudio


class CameraVideoStreamTrack(VideoStreamTrack):
    """
    Класс для захвата видео с веб-камеры.
    """
    def __init__(self, camera_index=0):
        super().__init__()
        self.cap = cv2.VideoCapture(camera_index)  # Открываем веб-камеру
        self.relay = MediaRelay()

    async def recv(self):
        ret, frame = self.cap.read()
        if not ret:
            raise Exception("Не удалось получить кадр с веб-камеры")

        # Преобразуем кадр в RGB (OpenCV использует BGR по умолчанию)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Создаем объект VideoFrame из aiortc
        video_frame = VideoFrame.from_ndarray(frame, format="rgb24")
        video_frame.pts = self._timestamp
        video_frame.time_base = self._time_base

        return video_frame


class MicrophoneAudioStreamTrack(AudioStreamTrack):
    """
    Класс для захвата аудио с микрофона с использованием pyaudio.
    """
    def __init__(self):
        super().__init__()
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=48000,
            input=True,
            frames_per_buffer=960
        )

    async def recv(self):
        data = self.stream.read(960, exception_on_overflow=False)
        audio_frame = AudioFrame(format="s16", layout="mono", samples=960)
        audio_frame.planes[0].update(data)
        audio_frame.pts = self._timestamp
        audio_frame.time_base = self._time_base
        return audio_frame


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("WebRTC Chat")

        # Переменные состояния
        self.enable_camera = False
        self.enable_microphone = True
        self.pc = None
        self.websocket = None
        self.user_list = []
        self.remote_video_label = None  # Метка для отображения видео другого участника

        # Интерфейс
        self.create_widgets()

        # Запуск WebSocket-клиента в фоновом потоке
        self.websocket_thread = threading.Thread(target=self.run_websocket, daemon=True)
        self.websocket_thread.start()

    def create_widgets(self):
        # Видео с веб-камеры
        self.video_label = tk.Label(self.root, text="Ваша камера")
        self.video_label.pack()

        # Кнопки управления
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=10)

        self.camera_button = tk.Button(control_frame, text="Включить камеру", command=self.toggle_camera)
        self.camera_button.pack(side=tk.LEFT, padx=5)

        self.microphone_button = tk.Button(control_frame, text="Включить микрофон", command=self.toggle_microphone)
        self.microphone_button.pack(side=tk.LEFT, padx=5)

        # Список пользователей
        user_list_frame = tk.Frame(self.root)
        user_list_frame.pack(pady=10)

        tk.Label(user_list_frame, text="Подключенные пользователи:").pack()
        self.user_listbox = tk.Listbox(user_list_frame, height=5)
        self.user_listbox.pack()

        # Окно для отображения видео другого участника
        self.remote_video_label = tk.Label(self.root, text="Камера друга", width=640, height=480)
        self.remote_video_label.pack()

    def toggle_camera(self):
        self.enable_camera = not self.enable_camera
        self.camera_button.config(text="Выключить камеру" if self.enable_camera else "Включить камеру")

    def toggle_microphone(self):
        self.enable_microphone = not self.enable_microphone
        self.microphone_button.config(text="Выключить микрофон" if self.enable_microphone else "Включить микрофон")

    def update_user_list(self, users):
        self.user_listbox.delete(0, tk.END)
        for user in users:
            self.user_listbox.insert(tk.END, user)

    def run_websocket(self):
        asyncio.run(self.websocket_main())

    async def websocket_main(self):
        uri = "ws://localhost:8765"
        room_id = "test_room"

        async with websockets.connect(uri) as websocket:
            self.websocket = websocket
            await websocket.send(json.dumps({"type": "join", "room": room_id}))

            # Создание RTCPeerConnection с STUN-сервером
            self.pc = RTCPeerConnection(
                RTCConfiguration(iceServers=[RTCIceServer("stun:stun.l.google.com:19302")])
            )

            # Добавление видеопотока (если камера включена)
            if self.enable_camera:
                camera_track = CameraVideoStreamTrack()
                self.pc.addTrack(camera_track)

            # Добавление аудиопотока (если микрофон включен)
            if self.enable_microphone:
                microphone_track = MicrophoneAudioStreamTrack()
                self.pc.addTrack(microphone_track)

            # Обработка ICE-кандидатов
            @self.pc.on("icecandidate")
            async def on_icecandidate(candidate):
                if candidate:
                    print(f"Отправка ICE-кандидата: {candidate}")
                    await websocket.send(json.dumps({
                        "type": "candidate",
                        "room": room_id,
                        "candidate": candidate.to_dict()
                    }))

            # Проверка наличия активных потоков перед созданием предложения
            if self.enable_camera or self.enable_microphone:
                # Создание предложения
                offer = await self.pc.createOffer()
                await self.pc.setLocalDescription(offer)
                await websocket.send(json.dumps({
                    "type": "offer",
                    "room": room_id,
                    "sdp": self.pc.localDescription.sdp
                }))
            else:
                print("Невозможно создать предложение: нет активных потоков (камера или микрофон).")

            # Обработка входящих медиапотоков
            @self.pc.on("track")
            def on_track(track):
                if track.kind == "video":
                    print("Получен видеопоток!")
                    asyncio.ensure_future(self.display_remote_video(track))

            # Обработка входящих сообщений
            async for message in websocket:
                data = json.loads(message)
                if data["type"] == "user_list":
                    self.update_user_list(data["users"])
                elif data["type"] == "offer":
                    await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="offer"))
                    answer = await self.pc.createAnswer()
                    await self.pc.setLocalDescription(answer)
                    await websocket.send(json.dumps({
                        "type": "answer",
                        "room": room_id,
                        "sdp": self.pc.localDescription.sdp
                    }))
                elif data["type"] == "answer":
                    await self.pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type="answer"))
                elif data["type"] == "candidate":
                    candidate = data["candidate"]
                    print(f"Получен ICE-кандидат: {candidate}")
                    await self.pc.addIceCandidate(candidate)

    async def display_remote_video(self, track):
        while True:
            frame = await track.recv()
            print("Получен кадр!")
            img = frame.to_ndarray(format="rgb24")
            img = cv2.resize(img, (640, 480))  # Изменяем размер для удобства отображения
            img = Image.fromarray(img)
            imgtk = ImageTk.PhotoImage(image=img)
            self.remote_video_label.imgtk = imgtk
            self.remote_video_label.configure(image=imgtk)

    def update_video(self):
        if self.enable_camera:
            cap = cv2.VideoCapture(0)
            ret, frame = cap.read()
            if ret:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)
                imgtk = ImageTk.PhotoImage(image=img)
                self.video_label.imgtk = imgtk
                self.video_label.configure(image=imgtk)
            cap.release()
        self.root.after(10, self.update_video)


# Запуск приложения
if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    app.update_video()
    root.mainloop()