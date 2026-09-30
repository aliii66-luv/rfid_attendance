# Smart RFID Attendance System (Raspberry Pi 3)

Python-based embedded attendance kiosk with a Tkinter GUI.

## Parts
- Raspberry Pi 3 + HDMI monitor, keyboard, mouse
- RC522 RFID reader (SPI), I2C 16x2 LCD (via BSS138 level shifter)
- 4x4 keypad (PIN + IN/OUT mode), HC-SR04 (wake from standby, ECHO via 1k/2k divider)
- Active buzzer (NPN + 1N4148), RGB LED (common cathode)

## Files
| File | Purpose |
|---|---|
| hardware.py | pin map, drivers, input/output background threads, part tests |
| database.py | SQLite storage, attendance rules (late, duplicate), CSV/Excel export |
| app.py | Tkinter GUI: Dashboard, Users, Logs, Control (Auto / Manual mode) |

## Setup (on the Pi)
1. Enable SPI and I2C: Preferences > Raspberry Pi Configuration > Interfaces, then reboot
2. `sudo apt install -y python3-spidev python3-smbus2 python3-openpyxl python3-pil.imagetk i2c-tools`

## Run
    cd ~/rfid_attendance
    python3 app.py

## Test one part
    python3 hardware.py rfid | lcd | led | buzzer | distance | keypad

## Pin map (BCM GPIO -> physical pin)
| Part | GPIO | Pin |
|---|---|---|
| RC522 SDA / SCK / MOSI / MISO / RST | 8 / 11 / 10 / 9 / 25 | 24 / 23 / 19 / 21 / 22 |
| LCD SDA / SCL (via level shifter) | 2 / 3 | 3 / 5 |
| Keypad rows R1-R4 (470 ohm) | 5 / 6 / 13 / 19 | 29 / 31 / 33 / 35 |
| Keypad columns C1-C4 | 12 / 16 / 20 / 21 | 32 / 36 / 38 / 40 |
| HC-SR04 TRIG / ECHO (divider) | 23 / 24 | 16 / 18 |
| Buzzer (via NPN) | 17 | 11 |
| RGB LED R / G / B | 22 / 27 / 18 | 15 / 13 / 12 |
