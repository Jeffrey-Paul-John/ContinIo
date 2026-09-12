#pragma once
// Copy to config.h. The real config.h is ignored by Git.
const char WIFI_SSID[] = "YOUR_WIFI_NAME";
const char WIFI_PASSWORD[] = "YOUR_WIFI_PASSWORD";
const char CHECKIN_URL[] = "http://YOUR_SERVER_IP:1880/continio-api/rfid-checkin";
// Must match CONTINIO_DEVICE_KEY in the Node-RED process environment.
const char DEVICE_KEY[] = "REPLACE_WITH_A_LONG_RANDOM_KEY";
const char OTA_HOSTNAME[] = "continio-door";
const char OTA_PASSWORD[] = "REPLACE_WITH_A_PRIVATE_OTA_PASSWORD";
