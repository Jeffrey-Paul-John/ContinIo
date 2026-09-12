# ESP32 hardware

The supplied firmware targets an ESP32, RC522 RFID reader, ILI9341 TFT and XPT2046 touch controller. Values below are taken from the sketch; verify your own module pin labels and voltage requirements before wiring.

| Signal | ESP32 GPIO |
| --- | ---: |
| Shared SPI SCK | 18 |
| Shared SPI MISO | 19 |
| Shared SPI MOSI | 23 |
| TFT CS | 27 |
| TFT DC | 26 |
| TFT RST | 25 |
| Touch CS | 33 |
| Touch IRQ | 32 |
| RC522 SS | 21 |
| RC522 RST | 22 |

The sketch separately selects each SPI device. Touch calibration values are specific to the original demonstration panel; recalibrate if touch coordinates do not align.

Wi-Fi credentials, check-in URL, device key and OTA password belong in the ignored `config.h`, copied from `config.example.h`. Do not put deployment secrets directly in the published sketch.
