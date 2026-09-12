#!/usr/bin/env python3
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}

MODEL_DIR.mkdir(exist_ok=True)
for name, url in MODELS.items():
    destination = MODEL_DIR / name
    if destination.exists() and destination.stat().st_size > 100_000:
        print(f"Already present: {name}")
        continue
    print(f"Downloading {name}...")
    with urlopen(url, timeout=90) as response, destination.open("wb") as output:
        output.write(response.read())
print("Face models ready.")
