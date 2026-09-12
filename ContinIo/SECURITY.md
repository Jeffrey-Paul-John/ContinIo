# Demonstration scope and data handling

This is a classroom-attendance prototype. Publishing its source does not mean the running application is hardened for internet exposure.

## Local configuration

- Keep the real `continio_telegram.env`, `node-red.env`, firmware `config.h`, and Node-RED credential exports out of Git. Only configuration templates are included.
- The publication copy loads device/admin registration codes from environment variables and rejects those operations when the values are missing.
- Store student data, face embeddings, account records and attendance exports outside the repository. Embeddings remain sensitive biometric data even though enrollment photos are processed in RAM.

## Known limitations

- The Flask `/embed`, `/scan` and active-room camera endpoints do not implement user authentication. Room-in-use checks and CORS are not authentication.
- The macOS Telegram launcher creates a public Cloudflare tunnel to the Flask service. That can expose those routes; do not treat the launcher as a production deployment recipe.
- The Node-RED flow uses salted SHA-256 for passwords and constructs session tokens with `Math.random()`. These need stronger production mechanisms and a full authorization review.
- The anonymous count shortcut can pass a wave when enough faces are visible without checking each identity.
- No production liveness, spoof-resistance or measured recognition-accuracy claim is made by this repository.

Use local test data and a trusted development environment. Do not post credentials or private data in public issues.
