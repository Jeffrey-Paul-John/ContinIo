#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <ArduinoOTA.h>
#include <SPI.h>
#include <MFRC522.h>
#include <Adafruit_GFX.h>
#include <Adafruit_ILI9341.h>
#include <XPT2046_Touchscreen.h>

// Copy config.example.h to config.h and configure locally before upload.
#include "config.h"

// ============================================================
// SHARED SPI AND MODULE PINS
// ============================================================
#define SPI_SCK     18
#define SPI_MISO    19
#define SPI_MOSI    23

#define TFT_CS      27
#define TFT_DC      26
#define TFT_RST     25

#define TOUCH_CS    33
#define TOUCH_IRQ   32

#define RFID_SS     21
#define RFID_RST    22

// Touch calibration from the tested keyboard sketch
#define TOUCH_LEFT    3726
#define TOUCH_RIGHT    573
#define TOUCH_TOP     3662
#define TOUCH_BOTTOM   552

Adafruit_ILI9341 tft(TFT_CS, TFT_DC, TFT_RST);
XPT2046_Touchscreen touch(TOUCH_CS, TOUCH_IRQ);
MFRC522 rfid(RFID_SS, RFID_RST);

String enteredUSN;
String lastRFID;
unsigned long lastRFIDTime = 0;
unsigned long lastWiFiAttempt = 0;
bool otaStarted = false;
bool keyboardVisible = false;

const uint16_t UI_BLACK = ILI9341_BLACK;
const uint16_t UI_NAVY = 0x000B;
const uint16_t UI_BLUE = 0x02B5;
const uint16_t UI_CYAN = 0x5FFF;
const uint16_t UI_DIM = 0x3339;
const uint16_t UI_GREEN = 0x07E8;
const uint16_t UI_GREEN_DIM = 0x0343;
const uint16_t UI_RED = 0xF986;
const uint16_t UI_RED_DIM = 0x7800;

const unsigned long RFID_COOLDOWN_MS = 3500;
const unsigned long WIFI_RETRY_MS = 10000;

const char numberRow[] = "1234567890";
const char qwertyRow[] = "QWERTYUIOP";
const char homeRow[]   = "ASDFGHJKL";
const char bottomRow[] = "ZXCVBNM";

// ============================================================
// SPI DEVICE SELECTION
// ============================================================
void deselectAllSPI() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_SS, HIGH);
}

void prepareDisplay() {
  digitalWrite(TOUCH_CS, HIGH);
  digitalWrite(RFID_SS, HIGH);
}

void prepareTouch() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(RFID_SS, HIGH);
}

void prepareRFID() {
  digitalWrite(TFT_CS, HIGH);
  digitalWrite(TOUCH_CS, HIGH);
}

// ============================================================
// DISPLAY HELPERS
// ============================================================
void centreText(const String &text, int y, uint8_t size, uint16_t colour) {
  prepareDisplay();
  tft.setTextSize(size);
  tft.setTextColor(colour);
  int width = text.length() * 6 * size;
  tft.setCursor(max(4, (320 - width) / 2), y);
  tft.print(text);
}

void drawButton(int x, int y, int w, int h, const String &label,
                uint16_t colour) {
  prepareDisplay();
  tft.fillRoundRect(x, y, w, h, 4, colour);
  tft.drawRoundRect(x, y, w, h, 4, UI_CYAN);

  uint8_t size = label.length() > 5 ? 1 : 2;
  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(size);

  int textWidth = label.length() * 6 * size;
  int textHeight = 8 * size;
  tft.setCursor(x + max(2, (w - textWidth) / 2),
                y + max(2, (h - textHeight) / 2));
  tft.print(label);
}

void drawStatusBar() {
  prepareDisplay();
  uint16_t colour = WiFi.status() == WL_CONNECTED
                      ? ILI9341_DARKGREEN : ILI9341_RED;
  tft.fillCircle(303, 17, 6, colour);
}

void drawInputBox() {
  prepareDisplay();
  tft.fillRoundRect(5, 5, 310, 43, 6, UI_NAVY);
  tft.drawRoundRect(5, 5, 310, 43, 6, UI_CYAN);

  tft.setTextColor(UI_CYAN);
  tft.setTextSize(1);
  tft.setCursor(12, 10);
  tft.print("ENTER USN OR TAP RFID");

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(2);
  tft.setCursor(12, 25);

  if (enteredUSN.length() == 0) {
    tft.setTextColor(UI_DIM);
    tft.print("USN...");
  } else {
    tft.print(enteredUSN);
  }

  drawStatusBar();
}

void drawCharacterRow(const char characters[], int count,
                      int startX, int y) {
  const int buttonWidth = 30;
  const int buttonHeight = 28;
  const int gap = 2;

  for (int i = 0; i < count; i++) {
    drawButton(startX + i * (buttonWidth + gap), y,
               buttonWidth, buttonHeight,
               String(characters[i]), UI_BLUE);
  }
}

void drawKeyboard() {
  keyboardVisible = true;
  prepareDisplay();
  tft.fillScreen(UI_BLACK);
  drawInputBox();
  drawCharacterRow(numberRow, 10, 1, 54);
  drawCharacterRow(qwertyRow, 10, 1, 86);
  drawCharacterRow(homeRow, 9, 17, 118);
  drawCharacterRow(bottomRow, 7, 49, 150);
  drawButton(5,   190, 75, 42, "CLEAR", 0x7800);
  drawButton(85,  190, 75, 42, "BACK", UI_NAVY);
  drawButton(165, 190, 150, 42, "SUBMIT", UI_BLUE);
}

void drawHomeScreen() {
  keyboardVisible = false;
  prepareDisplay();
  tft.fillScreen(UI_BLACK);

  // Compact circuit-style frame inspired by the supplied reference.
  tft.drawRoundRect(12, 12, 296, 216, 12, UI_CYAN);
  tft.drawRoundRect(18, 18, 284, 204, 9, UI_BLUE);
  tft.drawLine(12, 48, 28, 48, UI_CYAN);
  tft.drawLine(292, 48, 308, 48, UI_CYAN);
  tft.drawLine(12, 192, 28, 192, UI_CYAN);
  tft.drawLine(292, 192, 308, 192, UI_CYAN);
  tft.fillRoundRect(38, 42, 244, 154, 10, UI_NAVY);
  tft.drawRoundRect(38, 42, 244, 154, 10, UI_DIM);

  centreText("ContinIo", 67, 4, UI_CYAN);
  centreText("smart attendance", 111, 2, ILI9341_WHITE);
  centreText("Tap your RFID card", 143, 1, UI_CYAN);
  drawButton(92, 163, 136, 28, "FORGOT ID?", UI_BLUE);
  drawStatusBar();
}

void drawMessageHeader(uint16_t background, const String &title) {
  prepareDisplay();
  tft.fillScreen(background);
  centreText("ContinIo", 22, 3, ILI9341_WHITE);
  centreText(title, 68, 2, ILI9341_WHITE);
}

void drawBiometricFace(int cx, int cy, uint16_t colour) {
  prepareDisplay();

  // Geometric scanner ring.
  const int px[12] = {0, 18, 31, 37, 31, 18, 0, -18, -31, -37, -31, -18};
  const int py[12] = {-39, -34, -20, 0, 20, 34, 39, 34, 20, 0, -20, -34};
  for (int i = 0; i < 12; i++) {
    int next = (i + 1) % 12;
    tft.drawLine(cx + px[i], cy + py[i], cx + px[next], cy + py[next], colour);
    tft.drawLine(cx + px[i], cy + py[i], cx + px[(i + 4) % 12], cy + py[(i + 4) % 12], colour);
    tft.fillCircle(cx + px[i], cy + py[i], 1, colour);
  }

  // Minimal face and shoulders.
  tft.drawCircle(cx, cy - 6, 17, colour);
  tft.drawLine(cx - 11, cy - 10, cx - 4, cy - 10, colour);
  tft.drawLine(cx + 4, cy - 10, cx + 11, cy - 10, colour);
  tft.fillCircle(cx - 7, cy - 9, 2, colour);
  tft.fillCircle(cx + 7, cy - 9, 2, colour);
  tft.drawLine(cx, cy - 7, cx - 2, cy + 1, colour);
  tft.drawLine(cx - 7, cy + 6, cx + 7, cy + 6, colour);
  tft.drawLine(cx - 14, cy + 9, cx - 20, cy + 28, colour);
  tft.drawLine(cx + 14, cy + 9, cx + 20, cy + 28, colour);
  tft.drawLine(cx - 20, cy + 28, cx - 29, cy + 38, colour);
  tft.drawLine(cx + 20, cy + 28, cx + 29, cy + 38, colour);
  tft.drawLine(cx - 29, cy + 38, cx + 29, cy + 38, colour);
}

void drawTerminalFrame(uint16_t bright, uint16_t dim) {
  prepareDisplay();
  tft.fillScreen(UI_BLACK);
  tft.drawRoundRect(5, 5, 310, 230, 14, dim);
  tft.drawRoundRect(9, 9, 302, 222, 12, bright);
  tft.drawLine(20, 20, 72, 20, bright);
  tft.drawLine(248, 20, 300, 20, bright);
  tft.drawLine(20, 220, 72, 220, bright);
  tft.drawLine(248, 220, 300, 220, bright);
}

void showPowerOnAnimation() {
  keyboardVisible = false;
  prepareDisplay();
  tft.fillScreen(UI_BLACK);

  for (int frame = 0; frame < 9; frame++) {
    prepareDisplay();
    tft.fillScreen(UI_BLACK);
    int radius = 18 + frame * 4;
    tft.drawCircle(160, 91, radius, frame % 2 ? UI_BLUE : UI_CYAN);
    tft.drawCircle(160, 91, max(5, radius - 10), UI_BLUE);
    tft.drawLine(160 - radius, 91, 160 + radius, 91, UI_DIM);
    tft.drawLine(160, 91 - radius, 160, 91 + radius, UI_DIM);
    centreText("ContinIo", 139, 3, UI_CYAN);
    centreText("INITIALIZING TERMINAL", 170, 1, ILI9341_WHITE);
    tft.drawRoundRect(55, 194, 210, 12, 4, UI_DIM);
    tft.fillRoundRect(58, 197, (204 * (frame + 1)) / 9, 6, 2, UI_BLUE);
    delay(75);
  }

  centreText("SYSTEM READY", 216, 1, UI_GREEN);
  delay(280);
}

void showWorking(const String &method) {
  drawMessageHeader(UI_NAVY, "Checking in...");
  centreText(method, 112, 2, ILI9341_WHITE);
  centreText("Please wait", 158, 2, ILI9341_WHITE);
}

String shortened(const String &value, size_t maximum) {
  if (value.length() <= maximum) return value;
  return value.substring(0, maximum - 3) + "...";
}

void showSuccess(const String &name, const String &usn,
                 const String &className, int period,
                 const String &subject, const String &room) {
  drawTerminalFrame(UI_GREEN, UI_GREEN_DIM);
  centreText("WELCOME", 22, 3, UI_GREEN);
  drawBiometricFace(74, 116, UI_GREEN);

  prepareDisplay();
  tft.setTextColor(UI_GREEN);
  tft.setTextSize(2);
  tft.setCursor(124, 72);
  tft.print(shortened(name, 15));

  tft.setTextColor(ILI9341_WHITE);
  tft.setTextSize(1);
  tft.setCursor(124, 105);
  tft.print("USN: ");
  tft.print(shortened(usn, 22));
  tft.setCursor(124, 126);
  tft.print("CLASS: ");
  tft.print(shortened(className, 16));
  tft.setCursor(124, 147);
  tft.print("PERIOD: ");
  tft.print(period);
  tft.setCursor(124, 168);
  tft.print("ROOM: ");
  tft.print(shortened(room, 13));
  tft.setCursor(124, 189);
  tft.print(shortened(subject, 28));
  centreText("CHECK-IN ACCEPTED", 215, 1, UI_GREEN);
}

void showError(const String &message) {
  drawTerminalFrame(UI_RED, UI_RED_DIM);
  drawBiometricFace(160, 76, UI_RED);
  centreText("ACCESS DENIED", 126, 3, UI_RED);

  String text = shortened(message, 62);
  int y = 171;
  while (text.length() && y <= 207) {
    int take = min(36, (int)text.length());
    int split = take;
    if ((int)text.length() > take) {
      int space = text.substring(0, take).lastIndexOf(' ');
      if (space > 10) split = space;
    }
    String line = text.substring(0, split);
    text = text.substring(min((int)text.length(), split + 1));
    centreText(line, y, 1, ILI9341_WHITE);
    y += 16;
  }
  centreText("CHECK-IN REJECTED", 218, 1, UI_RED);
}

void returnToHome(unsigned long delayMs = 2800) {
  unsigned long start = millis();
  while (millis() - start < delayMs) {
    if (otaStarted) ArduinoOTA.handle();
    delay(5);
  }
  enteredUSN = "";
  drawHomeScreen();
}

// ============================================================
// WI-FI AND OTA
// ============================================================
bool connectWiFi(unsigned long timeoutMs = 15000) {
  if (WiFi.status() == WL_CONNECTED) return true;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to Wi-Fi");
  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < timeoutMs) {
    Serial.print('.');
    delay(250);
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("Wi-Fi connected. ESP32 IP: ");
    Serial.println(WiFi.localIP());
    return true;
  }

  Serial.println("Wi-Fi connection failed");
  return false;
}

void configureOTA() {
  if (otaStarted || WiFi.status() != WL_CONNECTED) return;
  ArduinoOTA.setHostname(OTA_HOSTNAME);
  ArduinoOTA.setPassword(OTA_PASSWORD);

  ArduinoOTA.onStart([]() {
    drawMessageHeader(ILI9341_ORANGE, "SYSTEM UPDATE");
    centreText("Do not unplug", 120, 2, ILI9341_WHITE);
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    prepareDisplay();
    int percentage = total ? (progress * 100U) / total : 0;
    tft.fillRect(40, 165, 240, 20, ILI9341_DARKGREY);
    tft.fillRect(40, 165, (240 * percentage) / 100, 20,
                 ILI9341_GREEN);
  });

  ArduinoOTA.onEnd([]() {
    centreText("Update complete", 205, 2, ILI9341_WHITE);
  });

  ArduinoOTA.onError([](ota_error_t error) {
    Serial.printf("OTA error %u\n", error);
  });

  ArduinoOTA.begin();
  otaStarted = true;
  Serial.println("Secure OTA ready as continio-door.local");
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    configureOTA();
    return;
  }
  if (millis() - lastWiFiAttempt < WIFI_RETRY_MS) return;
  lastWiFiAttempt = millis();
  if (connectWiFi(5000)) configureOTA();
  drawStatusBar();
}

// ============================================================
// CONTINIO CHECK-IN API
// ============================================================
bool sendCheckin(const char *field, const String &identifier) {
  showWorking(String(field) == "rfid_uid" ? "RFID card" : "USN");

  if (!connectWiFi(7000)) {
    showError("Wi-Fi unavailable");
    returnToHome();
    return false;
  }

  WiFiClient client;
  HTTPClient http;
  http.setConnectTimeout(5000);
  http.setTimeout(8000);

  if (!http.begin(client, CHECKIN_URL)) {
    showError("Invalid server URL");
    returnToHome();
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Continio-Key", DEVICE_KEY);

  JsonDocument requestDocument;
  requestDocument[field] = identifier;
  String requestBody;
  serializeJson(requestDocument, requestBody);

  Serial.print("POST ");
  Serial.println(CHECKIN_URL);
  Serial.println(requestBody);

  int statusCode = http.POST(requestBody);
  String responseBody = statusCode > 0 ? http.getString() : "";
  http.end();

  Serial.print("HTTP status: ");
  Serial.println(statusCode);
  Serial.println(responseBody);

  if (statusCode <= 0) {
    showError("ContinIo server unreachable");
    returnToHome();
    return false;
  }

  JsonDocument responseDocument;
  DeserializationError jsonError =
    deserializeJson(responseDocument, responseBody);

  if (jsonError) {
    showError("Invalid server response");
    returnToHome();
    return false;
  }

  bool ok = responseDocument["ok"] | false;
  if (!ok || statusCode < 200 || statusCode >= 300) {
    String error = responseDocument["error"] | "Check-in rejected";
    showError(error);
    returnToHome();
    return false;
  }

  String name = responseDocument["name"] | "Student";
  String usn = responseDocument["usn"] | identifier;
  String className = responseDocument["class"] | "";
  int period = responseDocument["period"] | 0;
  String subject = responseDocument["subject"] | "";
  String room = responseDocument["room"] | "";

  showSuccess(name, usn, className, period, subject, room);
  returnToHome(3500);
  return true;
}

// ============================================================
// RFID
// ============================================================
String currentRFIDUID() {
  String uid;
  for (byte i = 0; i < rfid.uid.size; i++) {
    if (rfid.uid.uidByte[i] < 0x10) uid += '0';
    uid += String(rfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

void checkRFID() {
  prepareRFID();
  if (!rfid.PICC_IsNewCardPresent()) return;
  if (!rfid.PICC_ReadCardSerial()) return;

  String uid = currentRFIDUID();
  rfid.PICC_HaltA();
  rfid.PCD_StopCrypto1();

  if (uid == lastRFID && millis() - lastRFIDTime < RFID_COOLDOWN_MS) {
    return;
  }

  lastRFID = uid;
  lastRFIDTime = millis();

  Serial.print("RFID UID: ");
  Serial.println(uid);
  sendCheckin("rfid_uid", uid);
}

// ============================================================
// TOUCH KEYBOARD
// ============================================================
bool getTouchPosition(int &screenX, int &screenY) {
  prepareTouch();
  if (!touch.touched()) return false;

  TS_Point point = touch.getPoint();
  if (point.z < 150) return false;

  screenX = map(point.x, TOUCH_LEFT, TOUCH_RIGHT, 0, 319);
  screenY = map(point.y, TOUCH_TOP, TOUCH_BOTTOM, 0, 239);
  screenX = constrain(screenX, 0, 319);
  screenY = constrain(screenY, 0, 239);
  return true;
}

char getCharacterFromRow(int x, int y,
                         const char characters[], int count,
                         int startX, int rowY) {
  const int buttonWidth = 30;
  const int buttonHeight = 28;
  const int gap = 2;

  if (y < rowY || y >= rowY + buttonHeight) return '\0';

  for (int i = 0; i < count; i++) {
    int buttonX = startX + i * (buttonWidth + gap);
    if (x >= buttonX && x < buttonX + buttonWidth) {
      return characters[i];
    }
  }
  return '\0';
}

void handleTouch(int x, int y) {
  if (!keyboardVisible) {
    if (x >= 92 && x <= 228 && y >= 163 && y <= 191) {
      enteredUSN = "";
      drawKeyboard();
    }
    return;
  }
  char selectedCharacter = getCharacterFromRow(
    x, y, numberRow, 10, 1, 54);

  if (selectedCharacter == '\0') {
    selectedCharacter = getCharacterFromRow(
      x, y, qwertyRow, 10, 1, 86);
  }
  if (selectedCharacter == '\0') {
    selectedCharacter = getCharacterFromRow(
      x, y, homeRow, 9, 17, 118);
  }
  if (selectedCharacter == '\0') {
    selectedCharacter = getCharacterFromRow(
      x, y, bottomRow, 7, 49, 150);
  }

  if (selectedCharacter != '\0') {
    if (enteredUSN.length() < 16) {
      enteredUSN += selectedCharacter;
      drawInputBox();
    }
    return;
  }

  if (y >= 190 && y <= 232) {
    if (x >= 5 && x <= 80) {
      enteredUSN = "";
      drawInputBox();
    } else if (x >= 85 && x <= 160) {
      if (enteredUSN.length() > 0) {
        enteredUSN.remove(enteredUSN.length() - 1);
        drawInputBox();
      }
    } else if (x >= 165 && x <= 315 && enteredUSN.length() > 0) {
      String submittedUSN = enteredUSN;
      enteredUSN = "";
      submittedUSN.trim();
      submittedUSN.toUpperCase();
      sendCheckin("usn", submittedUSN);
    }
  }
}

void waitForTouchRelease() {
  prepareTouch();
  while (touch.touched()) {
    if (otaStarted) ArduinoOTA.handle();
    delay(10);
  }
  delay(60);
}

// ============================================================
// STARTUP
// ============================================================
void setup() {
  Serial.begin(115200);
  delay(500);

  pinMode(TFT_CS, OUTPUT);
  pinMode(TOUCH_CS, OUTPUT);
  pinMode(RFID_SS, OUTPUT);
  deselectAllSPI();

  SPI.begin(SPI_SCK, SPI_MISO, SPI_MOSI);

  prepareDisplay();
  tft.begin();
  tft.setRotation(1);

  showPowerOnAnimation();

  prepareTouch();
  touch.begin();
  touch.setRotation(1);

  prepareRFID();
  rfid.PCD_Init();
  delay(100);

  byte version = rfid.PCD_ReadRegister(MFRC522::VersionReg);
  Serial.print("RC522 version: 0x");
  Serial.println(version, HEX);

  drawMessageHeader(UI_NAVY, "STARTING");
  centreText("Connecting Wi-Fi", 120, 2, ILI9341_WHITE);

  bool connected = connectWiFi();
  if (connected) configureOTA();

  enteredUSN = "";
  drawHomeScreen();

  Serial.println("ContinIo door terminal ready");
}

void loop() {
  if (otaStarted) ArduinoOTA.handle();
  maintainWiFi();

  checkRFID();

  int touchX;
  int touchY;
  if (getTouchPosition(touchX, touchY)) {
    handleTouch(touchX, touchY);
    waitForTouchRelease();
  }

  delay(5);
}
