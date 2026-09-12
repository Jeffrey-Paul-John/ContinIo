# Setup guide

Run commands from the repository root unless a step says otherwise. The `.command` helpers are written for macOS/zsh. Keep the demonstration services on a trusted network.

## 1. Restore the base database

The archive did not include the base MySQL schema. **Do not run these migrations against an empty database.** First restore a compatible existing `continio` database. See [database requirements](../database/README.md).

Back up an existing database before reviewing and applying the supplied upgrades in order:

1. `database/migrations/continio_v12_migration.sql`
2. `database/migrations/continio_v13_2_attendance_policy.sql`
3. `database/migrations/continio_v16_telegram_migration.sql`

The v12 script references `students.photo_path`; inspect the actual schema first. The v13 script recalculates unsubmitted attendance. The v16 script creates Telegram tables. These are upgrade scripts, not a bootstrap schema.

## 2. Configure Node-RED

Install Node-RED following its [official instructions](https://nodered.org/docs/getting-started/). In **Manage palette → Install**, add:

- [`@flowfuse/node-red-dashboard`](https://dashboard.flowfuse.com/getting-started)
- [`node-red-node-mysql`](https://flows.nodered.org/node/node-red-node-mysql)

Prepare local configuration:

```sh
cp node-red/node-red.env.example node-red/node-red.env
```

Edit `node-red/node-red.env` and set independent, long random values for `CONTINIO_DEVICE_KEY` and `CONTINIO_ADMIN_CODE`. The ESP32 uses the same device key. The admin code controls admin registration; it is not an account password.

Start Node-RED from a shell that has loaded these values:

```sh
source node-red/node-red.env
node-red
```

If Node-RED is managed as a service, set these variables in that service's environment and restart it. Sourcing them in an unrelated terminal will not change an already-running process.

Import `node-red/flows.json`. Configure the **ContinIo MySQL — SET PASSWORD** database node locally with your MySQL host, user, password and database. Deploy the flow once; disable older duplicate ContinIo flows first.

The public flow intentionally rejects device requests and admin registrations when the corresponding environment variable is empty. It no longer accepts the archive's shared default codes.

Dashboard pages:

- Login: `http://localhost:1880/dashboard/home`
- Admin: `http://localhost:1880/dashboard/admin`
- Teacher: `http://localhost:1880/dashboard/teacher`

## 3. Prepare the face service

Use a Python installation for which the packages in `continio_face_requirements.txt` are available. The original bundle contained a Python 3.14 virtual environment; recreate dependencies locally rather than copying that environment.

```sh
python3 -m venv face_env
face_env/bin/python -m pip install -r continio_face_requirements.txt
face_env/bin/python download_face_models.py
cp continio_telegram.env.example continio_telegram.env
```

Edit `continio_telegram.env` with the MySQL credentials and camera sources. It uses shell `export` statements, not a Python dotenv loader. It is ignored by Git.

The model downloader fetches OpenCV Zoo's YuNet and SFace model files into `models/`. Internet access is needed for this step. Model versions are named in the script, but downloads use upstream `main` URLs rather than a pinned commit.

Camera sources can be `0`, `1`, etc. for local cameras, or a reachable network video URL. Room 101 uses `CONTINIO_CAMERA_101_URL`; Room 102 uses `CONTINIO_CAMERA_102_URL`. Configure database room records consistently with `CAMERA_101` and `CAMERA_102`.

For a local run without Telegram:

```sh
source continio_telegram.env
face_env/bin/python continio_face_service.py
```

Leave the Telegram token/public URL empty to disable Telegram. The service listens on `127.0.0.1:5055`; check `/health` from the same computer. The browser's dashboard code calls this local address, so remote-browser deployments need additional endpoint/CORS configuration.

## 4. Optional Telegram demonstration

The provided macOS setup helper prompts for credentials without echoing passwords:

```sh
./Setup_ContinIo_Telegram.command
./Start_ContinIo_Telegram.command
```

The launcher installs missing Python dependencies, downloads missing models, and starts a temporary Cloudflare tunnel. It can install `cloudflared` through Homebrew if needed. **That tunnel exposes the Flask service to the internet**, including routes that lack authentication; use the local-only start above unless you have intentionally assessed this demo exposure. See [SECURITY.md](../SECURITY.md).

With both `TELEGRAM_BOT_TOKEN` and `CONTINIO_PUBLIC_URL` configured, the service enables its Telegram scheduler and polling worker. It prints a one-time `/link` command for each unlinked teacher. Send each command to the bot from the correct teacher account. `/status` reports the linked teacher and service state. Keep printed link codes private.

The configured teacher dashboard URL must be reachable from the teacher's device. Near the end of a period, Telegram provides review controls; final confirmation submits the session.

## 5. Configure and flash the ESP32

Open `firmware/ContinIo_Final_ESP32_API/ContinIo_Final_ESP32_API.ino` in the Arduino IDE. Install an ESP32 board package and these sketch dependencies through the appropriate Arduino managers:

- ArduinoJson
- MFRC522
- Adafruit GFX Library
- Adafruit ILI9341
- XPT2046_Touchscreen

WiFi, HTTPClient, SPI and ArduinoOTA are supplied by the ESP32 Arduino core. The original archive did not pin firmware library/core versions, so board compilation remains a local validation step.

Copy `config.example.h` to `config.h` in the sketch folder. Set your Wi-Fi details, the Node-RED server's LAN check-in URL, the matching device key, and a private OTA password. `config.h` is ignored by Git.

Use the [pin map](HARDWARE.md), verify touch calibration for your panel, then upload over USB. Later OTA updates use the configured `continio-door` hostname.

## End-to-end check

1. Start MySQL, Node-RED, and the Python service.
2. Confirm both API health endpoints respond and the configured cameras appear online.
3. Enroll a test student, associate a card, and create a short active timetable entry.
4. Check in using RFID; repeat with touchscreen USN entry.
5. Observe all three wave records and the resulting status.
6. Review a flagged record through the teacher portal and, if configured, Telegram.
7. Confirm submission and inspect the final attendance/CSV export.

No live database, camera, Telegram account, ESP32 or tunnel is started by the repository's offline checks.
