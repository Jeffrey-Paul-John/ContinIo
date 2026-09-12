#!/usr/bin/env python3
"""Privacy-safe ContinIo face embedding service.

Images are decoded and processed in RAM only. This service never writes an
uploaded image to disk. It returns a normalized 128-value OpenCV SFace vector.
"""

import base64
import hashlib
import html
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import urllib.error
from pathlib import Path

import cv2
import numpy as np
from flask import Flask, Response, jsonify, redirect, request
import mysql.connector
from werkzeug.middleware.proxy_fix import ProxyFix

ROOT = Path(__file__).resolve().parent
MODEL_DIR = Path(os.getenv("CONTINIO_MODEL_DIR", ROOT / "models"))
DETECTOR_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
ALLOWED_ORIGINS = {
    "http://127.0.0.1:1880",
    "http://localhost:1880",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024
SCAN_SECONDS = float(os.getenv("CONTINIO_SCAN_SECONDS", "6"))
MATCH_THRESHOLD = float(os.getenv("CONTINIO_MATCH_THRESHOLD", "0.45"))
MATCH_MARGIN = float(os.getenv("CONTINIO_MATCH_MARGIN", "0.04"))
CAMERAS = {
    "101": {
        "id": "CAMERA_101",
        "source": os.getenv("CONTINIO_CAMERA_101_URL", "").strip(),
    },
    "102": {
        "id": "CAMERA_102",
        "source": os.getenv("CONTINIO_CAMERA_102_URL", "").strip(),
    },
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024


def require_models():
    missing = [str(p) for p in (DETECTOR_MODEL, RECOGNIZER_MODEL) if not p.exists()]
    if missing:
        raise RuntimeError("Missing face models. Run: python3 download_face_models.py")


require_models()
detector = cv2.FaceDetectorYN.create(str(DETECTOR_MODEL), "", (320, 320), 0.85, 0.3, 5000)
recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_MODEL), "")
PROCESS_LOCK = threading.Lock()
CAMERA_LOCKS = {room: threading.Lock() for room in CAMERAS}
CAMERA_STATE = {room: None for room in CAMERAS}
CAMERA_FRAMES = {room: None for room in CAMERAS}
CAMERA_FRAME_TIME = {room: 0.0 for room in CAMERAS}
CAMERA_FRAME_SEQUENCE = {room: 0 for room in CAMERAS}

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CONTINIO_PUBLIC_URL = os.getenv("CONTINIO_PUBLIC_URL", "").rstrip("/")
CONTINIO_DASHBOARD_URL = os.getenv(
    "CONTINIO_DASHBOARD_URL", "http://127.0.0.1:1880/dashboard/teacher"
).rstrip("/")
DB_CONFIG = {
    "host": os.getenv("CONTINIO_DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("CONTINIO_DB_PORT", "3306")),
    "user": os.getenv("CONTINIO_DB_USER", "root"),
    "password": os.getenv("CONTINIO_DB_PASSWORD", ""),
    "database": os.getenv("CONTINIO_DB_NAME", "continio"),
}
TELEGRAM_READY = all((TELEGRAM_BOT_TOKEN, CONTINIO_PUBLIC_URL))
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}" if TELEGRAM_BOT_TOKEN else ""


@app.after_request
def cors(response):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


def decode_data_url(value):
    if not isinstance(value, str) or not value.startswith("data:image/") or "," not in value:
        raise ValueError("Choose a valid JPEG, PNG or WebP image")
    header, encoded = value.split(",", 1)
    if not any(t in header.lower() for t in ("image/jpeg", "image/png", "image/webp")):
        raise ValueError("Only JPEG, PNG and WebP photos are accepted")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("The selected image could not be decoded") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Photo must be smaller than 5 MB")
    image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("The selected image is damaged or unsupported")
    return image


def embedding_for(image):
    height, width = image.shape[:2]
    if min(height, width) < 160:
        raise ValueError("Photo is too small; use an image at least 160 × 160 pixels")
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    count = 0 if faces is None else len(faces)
    if count == 0:
        raise ValueError("No clear face was detected. Use a bright, front-facing photo")
    if count > 1:
        raise ValueError("Multiple faces were detected. Upload a photo containing only the student")
    aligned = recognizer.alignCrop(image, faces[0])
    feature = recognizer.feature(aligned).flatten().astype(np.float32)
    norm = float(np.linalg.norm(feature))
    if not np.isfinite(norm) or norm < 1e-8:
        raise ValueError("A reliable face embedding could not be generated")
    feature /= norm
    return [round(float(value), 8) for value in feature]


def features_in_frame(image, frame_detector=None, frame_recognizer=None):
    active_detector = frame_detector or detector
    active_recognizer = frame_recognizer or recognizer
    height, width = image.shape[:2]
    active_detector.setInputSize((width, height))
    _, faces = active_detector.detect(image)
    if faces is None:
        return []
    features = []
    for face in faces:
        aligned = active_recognizer.alignCrop(image, face)
        feature = active_recognizer.feature(aligned).flatten().astype(np.float32)
        norm = float(np.linalg.norm(feature))
        if np.isfinite(norm) and norm >= 1e-8:
            features.append(feature / norm)
    return features


def detected_faces(image, frame_detector):
    """Return detector rows without generating any identity embeddings."""
    height, width = image.shape[:2]
    frame_detector.setInputSize((width, height))
    _, faces = frame_detector.detect(image)
    return [] if faces is None else faces


def features_for_faces(image, faces, frame_recognizer):
    features = []
    for face in faces:
        aligned = frame_recognizer.alignCrop(image, face)
        feature = frame_recognizer.feature(aligned).flatten().astype(np.float32)
        norm = float(np.linalg.norm(feature))
        if np.isfinite(norm) and norm >= 1e-8:
            features.append(feature / norm)
    return features


def parse_students(value):
    if not isinstance(value, list) or not value:
        raise ValueError("A non-empty students list is required")
    students = []
    for row in value:
        if not isinstance(row, dict):
            continue
        vector = row.get("embedding")
        if isinstance(vector, str):
            try:
                import json
                vector = json.loads(vector)
            except Exception:
                vector = None
        feature = None
        if isinstance(vector, list) and len(vector) == 128:
            candidate = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(candidate))
            if np.isfinite(norm) and norm >= 1e-8:
                feature = candidate / norm
        students.append({
            "id": int(row["id"]),
            "usn": str(row.get("usn", "")),
            "checked_in": bool(row.get("checked_in", False)),
            "feature": feature,
        })
    if not students:
        raise ValueError("A non-empty valid students list is required")
    return students


def open_camera(source):
    camera = cv2.VideoCapture()
    if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
        camera.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
    if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
        camera.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)
    normalized = str(source).strip()
    if normalized.isdigit():
        camera.open(int(normalized))
    else:
        camera.open(normalized)
    return camera


def camera_worker(room):
    """Keep one capture connection alive and publish only the latest RAM frame."""
    source = CAMERAS[room]["source"]
    while True:
        if not source:
            CAMERA_STATE[room] = False
            time.sleep(5)
            continue
        camera = None
        try:
            camera = open_camera(source)
            if not camera.isOpened():
                raise RuntimeError("could not open source")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
            CAMERA_STATE[room] = True
            print(f"Camera {room} active: {source}")
            while True:
                ok, frame = camera.read()
                if not ok or frame is None:
                    raise RuntimeError("stream stopped returning frames")
                with CAMERA_LOCKS[room]:
                    CAMERA_FRAMES[room] = frame
                    CAMERA_FRAME_TIME[room] = time.monotonic()
                    CAMERA_FRAME_SEQUENCE[room] += 1
        except Exception as exc:
            if CAMERA_STATE.get(room) is not False:
                print(f"Camera {room} offline; retrying: {exc}")
            CAMERA_STATE[room] = False
            with CAMERA_LOCKS[room]:
                CAMERA_FRAMES[room] = None
                CAMERA_FRAME_TIME[room] = 0.0
            time.sleep(2)
        finally:
            if camera is not None:
                camera.release()


def scan_camera(students, room, wave):
    if room not in CAMERAS or not CAMERAS[room]["source"]:
        raise RuntimeError("No camera source is configured for this room")
    frame_detector = cv2.FaceDetectorYN.create(str(DETECTOR_MODEL), "", (320, 320), 0.85, 0.3, 5000)
    frame_recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_MODEL), "")
    checked_in = [student for student in students if student["checked_in"]]
    if not checked_in:
        return [], 0, 0, "no-checkins"
    if CAMERA_STATE.get(room) is not True:
        raise RuntimeError("Camera stream is offline or reconnecting")
    deadline = time.monotonic() + max(2.0, min(SCAN_SECONDS, 15.0))
    best = {}
    frames = 0
    maximum_visible = 0
    last_sequence = -1
    while time.monotonic() < deadline:
        with CAMERA_LOCKS[room]:
            sequence = CAMERA_FRAME_SEQUENCE[room]
            frame = None if CAMERA_FRAMES[room] is None else CAMERA_FRAMES[room].copy()
            frame_time = CAMERA_FRAME_TIME[room]
        if frame is None or sequence == last_sequence or time.monotonic() - frame_time > 3:
            time.sleep(0.04)
            continue
        last_sequence = sequence
        try:
            frames += 1
            faces = detected_faces(frame, frame_detector)
            maximum_visible = max(maximum_visible, len(faces))

            # Every wave first performs only an anonymous count. Identity
            # recognition runs only when fewer faces are visible than check-ins.
            if maximum_visible >= len(checked_in):
                return ([
                    {"student_id": student["id"], "confidence": 1.0}
                    for student in checked_in
                ], frames, maximum_visible, "count-cleared")

            # Identity embeddings are generated only after the count gate says
            # recognition is required.
            for observed in features_for_faces(frame, faces, frame_recognizer):
                ranked = sorted(
                    ((float(np.dot(observed, student["feature"])), student)
                     for student in students
                     if student["checked_in"] and student["feature"] is not None),
                    key=lambda item: item[0],
                    reverse=True,
                )
                if not ranked:
                    continue
                score, student = ranked[0]
                runner_up = ranked[1][0] if len(ranked) > 1 else -1.0
                if score >= MATCH_THRESHOLD and score - runner_up >= MATCH_MARGIN:
                    best[student["id"]] = max(score, best.get(student["id"], -1.0))
        except cv2.error:
            app.logger.exception("Could not process a camera frame for Room %s", room)
    if frames == 0:
        raise RuntimeError("The phone camera connected but returned no frames")
    return [
        {"student_id": student_id, "confidence": round(score, 4)}
        for student_id, score in sorted(best.items())
    ], frames, maximum_visible, "face-recognition"


@app.route("/health", methods=["GET"])
def health():
    usage = camera_usage()
    return jsonify(
        ok=True,
        service="continio-multi-room-face-and-camera",
        cameras={room: {
            "camera_id": camera["id"],
            "configured": bool(camera["source"]),
            "online": CAMERA_STATE[room],
            "in_use": room in usage,
        } for room, camera in CAMERAS.items()},
        stores_images=False,
        telegram_ready=TELEGRAM_READY,
        public_url=CONTINIO_PUBLIC_URL if TELEGRAM_READY else None,
    )


def camera_usage():
    try:
        connection = db()
        cursor = connection.cursor()
        cursor.execute(
            "SELECT DISTINCT r.room_number FROM attendance_sessions ses "
            "JOIN timetable tt ON tt.id=ses.timetable_id "
            "JOIN rooms r ON r.id=tt.room_id "
            "WHERE ses.session_date=CURDATE() AND ses.status='active' "
            "AND NOW() BETWEEN ses.start_time AND ses.end_time"
        )
        rooms = {str(row[0]) for row in cursor.fetchall()}
        cursor.close(); connection.close()
        return rooms
    except Exception:
        app.logger.exception("Could not determine camera usage")
        return set()


def camera_snapshot(room):
    if room not in CAMERAS:
        raise ValueError("Unknown room")
    config = CAMERAS[room]
    if not config["source"]:
        raise RuntimeError("No camera source is configured for this room")
    if room not in camera_usage():
        raise RuntimeError("Preview is available only while this room is in use")
    with CAMERA_LOCKS[room]:
        frame = None if CAMERA_FRAMES[room] is None else CAMERA_FRAMES[room].copy()
        frame_time = CAMERA_FRAME_TIME[room]
    if frame is None or time.monotonic() - frame_time > 3:
        raise RuntimeError("Camera is offline or has no recent frame")
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not ok:
        raise RuntimeError("Could not encode camera preview")
    return encoded.tobytes()


@app.route("/camera/<room>/snapshot", methods=["GET"])
def preview_snapshot(room):
    try:
        image = camera_snapshot(str(room))
        return Response(image, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 404
    except RuntimeError as exc:
        return jsonify(ok=False, error=str(exc)), 503


def preview_stream_frames(room):
    last_usage_check = 0.0
    while True:
        now = time.monotonic()
        if now - last_usage_check >= 2.0:
            if room not in camera_usage():
                break
            last_usage_check = now
        with CAMERA_LOCKS[room]:
            frame = None if CAMERA_FRAMES[room] is None else CAMERA_FRAMES[room].copy()
            frame_time = CAMERA_FRAME_TIME[room]
        if frame is None or now - frame_time > 3:
            time.sleep(0.12)
            continue
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 72])
        if ok:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + encoded.tobytes() + b"\r\n")
        time.sleep(0.10)


@app.route("/camera/<room>/preview", methods=["GET"])
def preview_stream(room):
    room = str(room)
    if room not in CAMERAS:
        return jsonify(ok=False, error="Unknown room"), 404
    if not CAMERAS[room]["source"] or CAMERA_STATE.get(room) is not True:
        return jsonify(ok=False, error="Camera is offline or unconfigured"), 503
    if room not in camera_usage():
        return jsonify(ok=False, error="Preview is available only while this room is in use"), 403
    return Response(
        preview_stream_frames(room),
        mimetype="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.route("/embed", methods=["OPTIONS"])
def embed_options():
    return ("", 204)


@app.route("/embed", methods=["POST"])
def embed():
    try:
        payload = request.get_json(silent=True) or {}
        image = decode_data_url(payload.get("image"))
        with PROCESS_LOCK:
            vector = embedding_for(image)
        del image
        return jsonify(ok=True, embedding=vector, dimensions=len(vector), image_stored=False)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 422
    except Exception:
        app.logger.exception("Embedding generation failed")
        return jsonify(ok=False, error="Face service error; check its Terminal window"), 500


@app.route("/scan", methods=["POST"])
def scan():
    try:
        payload = request.get_json(silent=True) or {}
        requested_camera = str(payload.get("camera_id") or "")
        requested_room = str(payload.get("room") or "")
        if requested_room not in CAMERAS:
            raise ValueError("room must be 101 or 102")
        camera_config = CAMERAS[requested_room]
        if requested_camera and requested_camera != camera_config["id"]:
            raise ValueError(f"Room {requested_room} uses {camera_config['id']}, not {requested_camera}")
        wave = int(payload.get("wave_number", 0))
        if wave not in (1, 2, 3):
            raise ValueError("wave_number must be 1, 2 or 3")
        students = parse_students(payload.get("students"))
        detections, frames, visible_count, scan_mode = scan_camera(students, requested_room, wave)
        CAMERA_STATE[requested_room] = True
        return jsonify(
            ok=True,
            camera_id=camera_config["id"],
            room=requested_room,
            session_id=payload.get("session_id"),
            wave_number=wave,
            detections=detections,
            frames_processed=frames,
            visible_count=visible_count,
            checked_in_count=sum(1 for student in students if student["checked_in"]),
            scan_mode=scan_mode,
            identity_recognition_used=(scan_mode == "face-recognition"),
            images_stored=False,
        )
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 422
    except RuntimeError as exc:
        if str((request.get_json(silent=True) or {}).get("room") or "") in CAMERA_STATE:
            CAMERA_STATE[str((request.get_json(silent=True) or {}).get("room"))] = False
        return jsonify(ok=False, error=str(exc)), 503
    except Exception:
        app.logger.exception("Camera scan failed")
        return jsonify(ok=False, error="Camera scan failed; check the service Terminal"), 500


def db():
    return mysql.connector.connect(**DB_CONFIG)


def token_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def telegram_call(method, payload=None, timeout=30):
    data = urllib.parse.urlencode(payload or {}).encode("utf-8")
    req = urllib.request.Request(f"{TELEGRAM_API}/{method}", data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram {method} HTTP {exc.code}: {details}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Telegram {method} failed: {result.get('description', 'unknown error')}")
    return result.get("result")


def send_telegram(chat_id, body, reply_markup=None):
    payload = {
        "chat_id": str(chat_id),
        "text": body,
        "disable_web_page_preview": "true",
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup, separators=(",", ":"))
    return telegram_call("sendMessage", payload)


def notify_linked_teachers(body):
    connection = db()
    cursor = connection.cursor()
    cursor.execute("SELECT chat_id FROM telegram_teacher_links")
    chat_ids = [row[0] for row in cursor.fetchall()]
    cursor.close(); connection.close()
    for chat_id in chat_ids:
        try:
            send_telegram(chat_id, body)
        except Exception:
            app.logger.exception("Could not send camera status alert")


def check_camera(room, config):
    lock = CAMERA_LOCKS[room]
    if not lock.acquire(blocking=False):
        return None
    try:
        if not config["source"]:
            return False
        camera = open_camera(config["source"])
        ok = camera.isOpened()
        if ok:
            ok, frame = camera.read()
            ok = bool(ok and frame is not None)
        camera.release()
        return ok
    finally:
        lock.release()


def camera_monitor():
    while True:
        for room, config in CAMERAS.items():
            try:
                current = check_camera(room, config)
                if current is None:
                    continue
                previous = CAMERA_STATE[room]
                CAMERA_STATE[room] = current
                if previous is True and current is False:
                    notify_linked_teachers(
                        f"⚠️ ContinIo camera offline\nRoom {room} · {config['id']}\n"
                        "Attendance waves will be sent for technical review until the camera reconnects."
                    )
                elif previous is False and current is True:
                    notify_linked_teachers(f"✅ ContinIo camera restored\nRoom {room} · {config['id']}")
            except Exception:
                app.logger.exception("Camera health check failed for Room %s", room)
        time.sleep(30)


def teacher_chat(connection, teacher_id):
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT chat_id FROM telegram_teacher_links WHERE teacher_id=%s LIMIT 1",
        (teacher_id,),
    )
    row = cursor.fetchone()
    cursor.close()
    return row["chat_id"] if row else None


def review_token_for(connection, session_id, teacher_id):
    raw = secrets.token_urlsafe(32)
    confirm_code = secrets.token_hex(3).upper()
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO telegram_review_tokens "
        "(session_id,teacher_id,token_hash,confirm_code,expires_at) "
        "VALUES (%s,%s,%s,%s,DATE_ADD(NOW(),INTERVAL 6 HOUR)) "
        "ON DUPLICATE KEY UPDATE token_hash=VALUES(token_hash),confirm_code=VALUES(confirm_code),"
        "expires_at=VALUES(expires_at),used_at=NULL",
        (session_id, teacher_id, token_hash(raw), confirm_code),
    )
    cursor.close()
    return raw, confirm_code


def notification_exists(connection, session_id, kind):
    cursor = connection.cursor()
    cursor.execute(
        "SELECT 1 FROM telegram_notifications WHERE session_id=%s AND notification_type=%s LIMIT 1",
        (session_id, kind),
    )
    found = cursor.fetchone() is not None
    cursor.close()
    return found


def record_notification(connection, session_id, kind, message_sid):
    cursor = connection.cursor()
    cursor.execute(
        "INSERT IGNORE INTO telegram_notifications "
        "(session_id,notification_type,provider_message_id,status) VALUES (%s,%s,%s,'sent')",
        (session_id, kind, message_sid),
    )
    cursor.close()


def review_notifications(connection):
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT ses.id session_id,tt.teacher_id,t.name teacher,t.mobile,c.name class_name,"
        "sub.name subject,tt.period_number,COUNT(a.id) review_count "
        "FROM attendance_sessions ses JOIN timetable tt ON tt.id=ses.timetable_id "
        "JOIN teachers t ON t.id=tt.teacher_id JOIN classes c ON c.id=tt.class_id "
        "JOIN subjects sub ON sub.id=tt.subject_id JOIN attendance a ON a.session_id=ses.id "
        "WHERE ses.session_date=CURDATE() AND ses.status IN ('active','teacher_review') "
        "AND NOW()>=TIMESTAMPADD(SECOND,-CEIL(TIMESTAMPDIFF(SECOND,"
        "TIMESTAMP(ses.session_date,ses.start_time),TIMESTAMP(ses.session_date,ses.end_time))*0.05),"
        "TIMESTAMP(ses.session_date,ses.end_time)) "
        "AND NOW()<TIMESTAMP(ses.session_date,ses.end_time) "
        "AND a.automated_status='review' AND EXISTS (SELECT 1 FROM wave_results wr "
        "WHERE wr.session_id=ses.id AND wr.wave_number=3) "
        "GROUP BY ses.id,tt.teacher_id,t.name,t.mobile,c.name,"
        "sub.name,tt.period_number"
    )
    rows = cursor.fetchall()
    cursor.close()
    for row in rows:
        if notification_exists(connection, row["session_id"], "review"):
            continue
        chat_id = teacher_chat(connection, row["teacher_id"])
        if not chat_id:
            continue
        raw, _ = review_token_for(connection, row["session_id"], row["teacher_id"])
        link = f"{CONTINIO_PUBLIC_URL}/review/{raw}"
        student_cursor = connection.cursor(dictionary=True)
        student_cursor.execute(
            "SELECT a.student_id,st.name,st.usn FROM attendance a "
            "JOIN students st ON st.id=a.student_id WHERE a.session_id=%s "
            "AND a.automated_status='review' ORDER BY st.name",
            (row["session_id"],),
        )
        review_students = student_cursor.fetchall()
        student_cursor.close()
        student_list = "\n".join(
            f"{index}. {student['name']} ({student['usn']})"
            for index, student in enumerate(review_students, 1)
        )
        buttons = []
        for student in review_students:
            buttons.append([
                {
                    "text": f"✅ {student['name']} — Present",
                    "callback_data": f"decision:{row['session_id']}:{student['student_id']}:present",
                },
                {
                    "text": "❌ Absent",
                    "callback_data": f"decision:{row['session_id']}:{student['student_id']}:absent",
                },
            ])
        buttons.append([{"text": "Open full review form", "url": link}])
        body = (
            f"ContinIo attendance review\n\n{row['class_name']} · {row['subject']} · "
            f"Period {row['period_number']}\n{row['review_count']} student(s) need confirmation.\n\n"
            f"{student_list}\n\nMark every student Present or Absent below. "
            "You can change a decision by tapping the other option before submission."
        )
        message = send_telegram(chat_id, body, {"inline_keyboard": buttons})
        record_notification(connection, row["session_id"], "review", str(message["message_id"]))
        connection.commit()


def final_notifications(connection):
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT ses.id session_id,tt.teacher_id,t.name teacher,t.mobile,c.name class_name,"
        "sub.name subject,tt.period_number,SUM(COALESCE(a.final_status,a.teacher_status,a.automated_status)='present') present_count,"
        "SUM(COALESCE(a.final_status,a.teacher_status,a.automated_status)='absent') absent_count,"
        "SUM(a.automated_status='review' AND a.teacher_status IS NULL) pending_count "
        "FROM attendance_sessions ses JOIN timetable tt ON tt.id=ses.timetable_id "
        "JOIN teachers t ON t.id=tt.teacher_id JOIN classes c ON c.id=tt.class_id "
        "JOIN subjects sub ON sub.id=tt.subject_id JOIN attendance a ON a.session_id=ses.id "
        "WHERE ses.session_date=CURDATE() AND ses.status='teacher_review' "
        "AND TIMESTAMP(ses.session_date,ses.end_time)<=NOW() "
        "GROUP BY ses.id,tt.teacher_id,t.name,t.mobile,c.name,sub.name,tt.period_number"
    )
    rows = cursor.fetchall()
    cursor.close()
    for row in rows:
        if notification_exists(connection, row["session_id"], "final"):
            continue
        token_cursor = connection.cursor(dictionary=True)
        token_cursor.execute(
            "SELECT confirm_code FROM telegram_review_tokens WHERE session_id=%s AND teacher_id=%s "
            "AND expires_at>NOW() LIMIT 1",
            (row["session_id"], row["teacher_id"]),
        )
        token_row = token_cursor.fetchone()
        token_cursor.close()
        if not token_row:
            _, code = review_token_for(connection, row["session_id"], row["teacher_id"])
        else:
            code = token_row["confirm_code"]
        if int(row["pending_count"] or 0) > 0:
            if notification_exists(connection, row["session_id"], "pending"):
                continue
            body = (
                f"ContinIo cannot submit {row['class_name']} attendance yet. "
                f"{row['pending_count']} review decision(s) are still pending. "
                "Open the review link from the earlier Telegram message."
            )
        else:
            body = (
                f"ContinIo attendance ready\n\n{row['class_name']} · {row['subject']} · "
                f"Period {row['period_number']}\nPresent: {row['present_count']}\nAbsent: {row['absent_count']}\n\n"
                f"Send /confirm {code} to submit and lock attendance.\n"
                f"Send /edit {code} if you need to change a review decision."
            )
        chat_id = teacher_chat(connection, row["teacher_id"])
        if not chat_id:
            continue
        buttons = None
        if int(row["pending_count"] or 0) == 0:
            buttons = {"inline_keyboard": [[
                {"text": "Confirm & Submit", "callback_data": f"confirm:{code}"},
                {"text": "Review Again", "url": f"{CONTINIO_PUBLIC_URL}/teacher-dashboard"},
            ]]}
        message = send_telegram(chat_id, body, buttons)
        record_notification(connection, row["session_id"], "pending" if int(row["pending_count"] or 0) > 0 else "final", str(message["message_id"]))
        connection.commit()


def telegram_scheduler():
    while True:
        try:
            connection = db()
            review_notifications(connection)
            final_notifications(connection)
            connection.close()
        except Exception:
            app.logger.exception("Telegram scheduler error")
        time.sleep(10)


def handle_confirmation(chat_id, command, code):
    connection = db()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT trt.session_id,trt.teacher_id,ttl.chat_id "
        "FROM telegram_review_tokens trt JOIN telegram_teacher_links ttl ON ttl.teacher_id=trt.teacher_id "
        "WHERE trt.confirm_code=%s AND trt.expires_at>NOW() AND trt.used_at IS NULL LIMIT 1",
        (code.upper(),),
    )
    row = cursor.fetchone()
    if not row or str(row["chat_id"]) != str(chat_id):
        cursor.close(); connection.close()
        return "That confirmation code is invalid, expired, or belongs to another teacher."
    if command == "EDIT":
        cursor.close(); connection.close()
        return "Use the secure review link from the earlier ContinIo message."
    cursor.execute(
        "SELECT COUNT(*) pending FROM attendance WHERE session_id=%s "
        "AND automated_status='review' AND teacher_status IS NULL",
        (row["session_id"],),
    )
    if cursor.fetchone()["pending"]:
        cursor.close(); connection.close()
        return "Submission blocked: answer every review student first."
    cursor.execute(
        "UPDATE attendance SET final_status=COALESCE(teacher_status,automated_status),"
        "submitted_at=NOW() WHERE session_id=%s",
        (row["session_id"],),
    )
    cursor.execute("UPDATE attendance_sessions SET status='submitted' WHERE id=%s", (row["session_id"],))
    cursor.execute("UPDATE telegram_review_tokens SET used_at=NOW() WHERE session_id=%s", (row["session_id"],))
    connection.commit(); cursor.close(); connection.close()
    return "Attendance submitted and locked successfully."


def reopen_review(chat_id, code):
    connection = db()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT trt.session_id,trt.teacher_id FROM telegram_review_tokens trt "
        "JOIN telegram_teacher_links ttl ON ttl.teacher_id=trt.teacher_id "
        "WHERE trt.confirm_code=%s AND trt.expires_at>NOW() AND trt.used_at IS NULL "
        "AND ttl.chat_id=%s LIMIT 1",
        (code.upper(), str(chat_id)),
    )
    row = cursor.fetchone()
    cursor.close()
    if not row:
        connection.close()
        return None, None
    raw, new_code = review_token_for(connection, row["session_id"], row["teacher_id"])
    connection.commit(); connection.close()
    return f"{CONTINIO_PUBLIC_URL}/review/{raw}", new_code


def handle_telegram_callback(callback):
    callback_id = callback.get("id")
    chat_id = callback.get("message", {}).get("chat", {}).get("id")
    data = str(callback.get("data") or "")
    parts = data.split(":")
    if len(parts) < 2:
        telegram_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Invalid action"})
        return
    action = parts[0]
    if action == "decision" and len(parts) == 4:
        try:
            session_id, student_id = int(parts[1]), int(parts[2])
            status = parts[3]
            if status not in ("present", "absent"):
                raise ValueError
        except ValueError:
            telegram_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Invalid decision"})
            return
        connection = db()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT st.name FROM attendance a JOIN students st ON st.id=a.student_id "
            "JOIN attendance_sessions ses ON ses.id=a.session_id "
            "JOIN timetable tt ON tt.id=ses.timetable_id "
            "JOIN telegram_teacher_links ttl ON ttl.teacher_id=tt.teacher_id "
            "WHERE a.session_id=%s AND a.student_id=%s AND a.automated_status='review' "
            "AND ses.status<>'submitted' AND ttl.chat_id=%s LIMIT 1",
            (session_id, student_id, str(chat_id)),
        )
        student = cursor.fetchone()
        if not student:
            cursor.close(); connection.close()
            telegram_call("answerCallbackQuery", {
                "callback_query_id": callback_id,
                "text": "This review is invalid, expired, or belongs to another teacher.",
                "show_alert": "true",
            })
            return
        cursor.execute(
            "UPDATE attendance SET teacher_status=%s,final_status=%s,teacher_override=1,confirmed_at=NOW() "
            "WHERE session_id=%s AND student_id=%s AND automated_status='review'",
            (status, status, session_id, student_id),
        )
        connection.commit(); cursor.close(); connection.close()
        telegram_call("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": f"{student['name']} marked {status.title()}",
        })
        return
    code = ":".join(parts[1:])
    if action == "confirm":
        result = handle_confirmation(chat_id, "CONFIRM", code)
        telegram_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": result[:180], "show_alert": "true"})
        send_telegram(chat_id, result)
        return
    telegram_call("answerCallbackQuery", {"callback_query_id": callback_id, "text": "Unknown action"})


def handle_telegram_message(message):
    chat_id = message.get("chat", {}).get("id")
    text = str(message.get("text") or "").strip()
    if not chat_id or not text:
        return
    parts = text.split()
    command = parts[0].split("@", 1)[0].lower()
    if command == "/start":
        send_telegram(chat_id, "ContinIo bot is ready. Send the one-time /link command shown in the ContinIo Terminal.")
        return
    if command in ("/status", "/test"):
        connection = db()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT t.id,t.name FROM telegram_teacher_links ttl "
            "JOIN teachers t ON t.id=ttl.teacher_id WHERE ttl.chat_id=%s LIMIT 1",
            (str(chat_id),),
        )
        teacher = cursor.fetchone()
        if not teacher:
            cursor.close(); connection.close()
            send_telegram(chat_id, "This chat is not linked to a teacher. Send the /link command printed in the ContinIo Terminal.")
            return
        cursor.execute(
            "SELECT COUNT(*) active_count FROM attendance_sessions ses "
            "JOIN timetable tt ON tt.id=ses.timetable_id "
            "WHERE tt.teacher_id=%s AND ses.session_date=CURDATE() "
            "AND ses.status IN ('active','teacher_review')",
            (teacher["id"],),
        )
        active_count = int(cursor.fetchone()["active_count"] or 0)
        cursor.close(); connection.close()
        camera_lines = "\n".join(
            f"Room {room}: {'Active' if CAMERA_STATE.get(room) else 'Offline'} · "
            f"{'In use' if room in camera_usage() else 'Not in use'}"
            for room in sorted(CAMERAS)
        )
        send_telegram(
            chat_id,
            f"✅ ContinIo Telegram is connected to {teacher['name']}.\n"
            f"Open attendance sessions: {active_count}\n\n{camera_lines}\n\n"
            "Review messages are sent after Wave 3 when 5% of class time remains.",
        )
        return
    if command == "/link" and len(parts) == 2:
        connection = db()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT teacher_id FROM telegram_link_codes WHERE code_hash=%s "
            "AND expires_at>NOW() AND used_at IS NULL LIMIT 1",
            (token_hash(parts[1].upper()),),
        )
        row = cursor.fetchone()
        if not row:
            cursor.close(); connection.close()
            send_telegram(chat_id, "That link code is invalid or expired. Restart ContinIo to generate a new one.")
            return
        cursor.execute(
            "INSERT INTO telegram_teacher_links (teacher_id,chat_id,telegram_username) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE chat_id=VALUES(chat_id),telegram_username=VALUES(telegram_username),linked_at=NOW()",
            (row["teacher_id"], str(chat_id), message.get("from", {}).get("username")),
        )
        cursor.execute("UPDATE telegram_link_codes SET used_at=NOW() WHERE teacher_id=%s", (row["teacher_id"],))
        connection.commit(); cursor.close(); connection.close()
        send_telegram(chat_id, "Teacher account linked successfully. ContinIo notifications are enabled.")
        return
    if command in ("/confirm", "/edit") and len(parts) == 2:
        send_telegram(chat_id, handle_confirmation(chat_id, command[1:].upper(), parts[1]))
        return
    send_telegram(chat_id, "Commands: /link <code>, /status, /confirm <code>, or /edit <code>.")


def telegram_polling():
    offset = 0
    while True:
        try:
            updates = telegram_call("getUpdates", {"timeout": "25", "offset": str(offset)}, timeout=35) or []
            for update in updates:
                offset = max(offset, int(update["update_id"]) + 1)
                if "message" in update:
                    handle_telegram_message(update["message"])
                elif "callback_query" in update:
                    handle_telegram_callback(update["callback_query"])
        except Exception:
            app.logger.exception("Telegram polling error")
            time.sleep(3)


def print_pairing_codes():
    connection = db()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT t.id,t.name FROM teachers t LEFT JOIN telegram_teacher_links ttl ON ttl.teacher_id=t.id "
        "WHERE t.active=1 AND ttl.teacher_id IS NULL ORDER BY t.id"
    )
    teachers = cursor.fetchall()
    for teacher in teachers:
        code = secrets.token_hex(3).upper()
        cursor.execute(
            "INSERT INTO telegram_link_codes (teacher_id,code_hash,expires_at,used_at) "
            "VALUES (%s,%s,DATE_ADD(NOW(),INTERVAL 1 HOUR),NULL) "
            "ON DUPLICATE KEY UPDATE code_hash=VALUES(code_hash),expires_at=VALUES(expires_at),used_at=NULL",
            (teacher["id"], token_hash(code)),
        )
        print(f"PAIR {teacher['name']}: /link {code}")
    connection.commit(); cursor.close(); connection.close()


def load_review(raw_token):
    connection = db()
    cursor = connection.cursor(dictionary=True)
    cursor.execute(
        "SELECT wrt.session_id,wrt.teacher_id,wrt.used_at,ses.status,t.name teacher,c.name class_name,"
        "sub.name subject,tt.period_number FROM telegram_review_tokens wrt "
        "JOIN attendance_sessions ses ON ses.id=wrt.session_id JOIN timetable tt ON tt.id=ses.timetable_id "
        "JOIN teachers t ON t.id=wrt.teacher_id JOIN classes c ON c.id=tt.class_id "
        "JOIN subjects sub ON sub.id=tt.subject_id WHERE wrt.token_hash=%s AND wrt.expires_at>NOW() LIMIT 1",
        (token_hash(raw_token),),
    )
    session = cursor.fetchone()
    if not session:
        cursor.close(); connection.close(); return None, [], None
    cursor.execute(
        "SELECT a.student_id,st.name,st.usn,a.teacher_status FROM attendance a "
        "JOIN students st ON st.id=a.student_id WHERE a.session_id=%s "
        "AND a.automated_status='review' ORDER BY st.name",
        (session["session_id"],),
    )
    students = cursor.fetchall()
    cursor.close()
    return session, students, connection


def review_page(session, students, token, message=""):
    rows = "".join(
        f"<fieldset><legend>{html.escape(s['name'])} <small>{html.escape(s['usn'])}</small></legend>"
        f"<label><input required type='radio' name='student_{s['student_id']}' value='present' "
        f"{'checked' if s['teacher_status']=='present' else ''}> Present</label>"
        f"<label><input required type='radio' name='student_{s['student_id']}' value='absent' "
        f"{'checked' if s['teacher_status']=='absent' else ''}> Absent</label></fieldset>"
        for s in students
    )
    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>ContinIo Review</title><style>body{{font-family:Arial;background:#f0f2f5;margin:0;color:#222}}main{{max-width:520px;margin:auto;min-height:100vh;background:#efeae2}}header{{background:#f97316;color:white;padding:22px}}h1{{margin:0}}form{{padding:15px}}fieldset{{border:0;background:white;border-radius:14px;padding:16px;margin:12px 0;box-shadow:0 2px 8px #0001}}legend{{font-weight:800;padding-top:12px}}small{{color:#777}}label{{display:inline-block;margin:12px 20px 4px 0}}button{{width:100%;border:0;border-radius:12px;padding:15px;background:#16a34a;color:white;font-weight:800;font-size:16px}}.msg{{background:#dcfce7;padding:12px;border-radius:10px;margin:15px}}</style></head><body><main><header><h1>ContinIo</h1><div>{html.escape(session['class_name'])} · {html.escape(session['subject'])} · Period {session['period_number']}</div></header>{f'<div class="msg">{html.escape(message)}</div>' if message else ''}<form method='post'>{rows}<button type='submit'>Save decisions</button></form></main></body></html>"""


@app.route("/teacher-dashboard", methods=["GET"])
def teacher_dashboard_redirect():
    return redirect(CONTINIO_DASHBOARD_URL, code=302)


@app.route("/review/<raw_token>", methods=["GET", "POST"])
def review(raw_token):
    session, students, connection = load_review(raw_token)
    if not session:
        return ("This review link is invalid or expired.", 404)
    if session["used_at"] is not None or session["status"] == "submitted":
        connection.close(); return ("Attendance has already been submitted and locked.", 410)
    message = ""
    if request.method == "POST":
        decisions = {}
        for student in students:
            value = request.form.get(f"student_{student['student_id']}")
            if value not in ("present", "absent"):
                connection.close(); return (review_page(session, students, raw_token, "Answer every student before saving."), 422)
            decisions[student["student_id"]] = value
        cursor = connection.cursor()
        for student_id, status in decisions.items():
            cursor.execute(
                "UPDATE attendance SET teacher_status=%s,final_status=%s,teacher_override=1,confirmed_at=NOW() "
                "WHERE session_id=%s AND student_id=%s AND automated_status='review'",
                (status, status, session["session_id"], student_id),
            )
        connection.commit(); cursor.close()
        for student in students:
            student["teacher_status"] = decisions[student["student_id"]]
        message = "All review decisions saved. Return to Telegram for final confirmation."
    page = review_page(session, students, raw_token, message)
    connection.close()
    return page


if __name__ == "__main__":
    for camera_room in CAMERAS:
        threading.Thread(target=camera_worker, args=(camera_room,), daemon=True).start()
    print("Persistent Room 101 and Room 102 camera workers enabled")
    if TELEGRAM_READY:
        print_pairing_codes()
        threading.Thread(target=telegram_scheduler, daemon=True).start()
        threading.Thread(target=telegram_polling, daemon=True).start()
        threading.Thread(
            target=notify_linked_teachers,
            args=("✅ ContinIo service is online. Send /status to test this bot connection.",),
            daemon=True,
        ).start()
        print("ContinIo Telegram scheduler enabled")
        print(f"Secure review base: {CONTINIO_PUBLIC_URL}")
    else:
        print("Telegram scheduler disabled (bot token/public URL environment is incomplete)")
    app.run(host="127.0.0.1", port=5055, debug=False)
