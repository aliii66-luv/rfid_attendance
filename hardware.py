# hardware.py - pins, drivers and background threads for the Smart RFID Attendance System
# Test one part at a time:  python3 hardware.py rfid | lcd | led | buzzer | distance | keypad
import queue
import sys
import threading
import time

import spidev
from gpiozero import Buzzer, DigitalInputDevice, DigitalOutputDevice, DistanceSensor, RGBLED
from smbus2 import SMBus

# ---------------- pin map (BCM GPIO numbers) ----------------
RFID_RST = 25
LCD_ADDRESS = 0x27
KEY_ROWS = (5, 6, 13, 19)
KEY_COLS = (12, 16, 20, 21)        
KEYS = ("123A", "456B", "789C", "*0#D")
TRIG, ECHO = 23, 24
BUZZER = 17
LED_R, LED_G, LED_B = 22, 27, 18
COMMON_ANODE_LED = False
NEAR_CM, FAR_CM = 60, 80


class RC522:
    """Minimal RFID-RC522 reader over SPI. read_uid() returns 'A1B2C3D4' or None."""

    def __init__(self):
        self.rst = DigitalOutputDevice(RFID_RST, initial_value=True)
        self.spi = spidev.SpiDev()
        self.spi.open(0, 0)
        self.spi.max_speed_hz = 1000000
        self.write(0x01, 0x0F)                          # soft reset
        time.sleep(0.05)
        if self.read(0x37) in (0x00, 0xFF):
            raise RuntimeError("RC522 not responding - check the SPI wires")
        for reg, val in ((0x2A, 0x8D), (0x2B, 0x3E), (0x2D, 30), (0x2C, 0),
                         (0x15, 0x40), (0x11, 0x3D), (0x26, 0x70)):
            self.write(reg, val)                        # timer, modulation, gain
        self.write(0x14, self.read(0x14) | 0x03)        # antenna on

    def write(self, reg, val):
        self.spi.xfer2([(reg << 1) & 0x7E, val])

    def read(self, reg):
        return self.spi.xfer2([((reg << 1) & 0x7E) | 0x80, 0])[1]

    def transceive(self, data, bits):
        self.write(0x0D, bits)                          # bit framing
        self.write(0x02, 0xF7)                          # enable interrupts
        self.write(0x04, 0x7F)                          # clear interrupts
        self.write(0x0A, 0x80)                          # flush FIFO
        self.write(0x01, 0x00)                          # idle
        for b in data:
            self.write(0x09, b)                         # data into FIFO
        self.write(0x01, 0x0C)                          # transceive command
        self.write(0x0D, self.read(0x0D) | 0x80)        # start sending
        for _ in range(200):
            irq = self.read(0x04)
            if irq & 0x31:
                break
        self.write(0x0D, self.read(0x0D) & 0x7F)
        if not irq & 0x30 or self.read(0x06) & 0x1B:   # timeout (no card) or error
            return []
        return [self.read(0x09) for _ in range(min(self.read(0x0A), 16))]

    def read_uid(self):
        if len(self.transceive([0x26], 0x07)) != 2:     # is a card there?
            return None
        uid = self.transceive([0x93, 0x20], 0x00)       # anticollision -> 4 bytes + check
        if len(uid) != 5 or uid[0] ^ uid[1] ^ uid[2] ^ uid[3] != uid[4]:
            return None
        return "".join(f"{b:02X}" for b in uid[:4])

    def close(self):
        self.spi.close()
        self.rst.close()
        

class LCD:
    """16x2 LCD with a PCF8574 I2C backpack (4-bit mode)."""

    def __init__(self, address=LCD_ADDRESS):
        self.bus, self.address, self.light = SMBus(1), address, 0x08
        for nibble in (0x30, 0x30, 0x30, 0x20):         # reset, then 4-bit mode
            self.nibble(nibble)
            time.sleep(0.005)
        for cmd in (0x28, 0x0C, 0x06, 0x01):            # 2 lines, display on, clear
            self.send(cmd)
        time.sleep(0.002)

    def nibble(self, data):
        self.bus.write_byte(self.address, data | self.light | 0x04)   # EN high
        self.bus.write_byte(self.address, data | self.light)          # EN low
        time.sleep(0.0001)

    def send(self, value, rs=0):
        self.nibble((value & 0xF0) | rs)
        self.nibble(((value << 4) & 0xF0) | rs)

    def show(self, line1, line2=""):
        for row, text in ((0x80, line1), (0xC0, line2)):
            self.send(row)
            for ch in str(text)[:16].ljust(16):
                self.send(ord(ch) if 32 <= ord(ch) < 127 else 63, rs=1)

    def backlight(self, on):
        self.light = 0x08 if on else 0
        self.bus.write_byte(self.address, self.light)
        

class InputThread(threading.Thread):
    """Background thread: polls keypad (20 ms), RFID and distance (100 ms).
    Sends events to the GUI through a queue, so the GUI never waits on hardware."""

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events = events
        self.present = False

    def run(self):
        rfid = keypad = sensor = None
        tick, last_uid, last_time = 0, None, 0

        while True:
            try:
                rfid = rfid or RC522()
                keypad = keypad or Keypad()
                sensor = sensor or DistanceSensor(echo=ECHO, trigger=TRIG, max_distance=2)
                key = keypad.get_key()
                if key:
                    self.events.put(("key", key))
                if tick % 5 == 0:                       # every 100 ms
                    uid = rfid.read_uid()
                    if uid and (uid != last_uid or time.time() - last_time > 2):
                        self.events.put(("card", uid))     # same card: once every 2 s
                    if uid:
                        last_uid, last_time = uid, time.time()
                    cm = sensor.distance * 100
                    self.events.put(("distance", cm))
                    if not self.present and cm < NEAR_CM or self.present and cm > FAR_CM:
                        self.present = not self.present
                        self.events.put(("presence", self.present))
                tick += 1
                time.sleep(0.02)
            except Exception as error:                  # e.g. a loose wire
                self.events.put(("error", str(error)))
                for device in (rfid, keypad, sensor):
                    if device:
                        device.close()
                rfid = keypad = sensor = None
                time.sleep(3)                           # then try again
        

class OutputThread(threading.Thread):
    """Background thread: runs LCD / LED / buzzer commands from a queue,
    so a 0.8 s beep never freezes the GUI."""

    def __init__(self, events):
        super().__init__(daemon=True)
        self.events, self.jobs = events, queue.Queue()

    def send(self, lcd=None, led=None, beep=None, backlight=None):
        self.jobs.put((lcd, led, beep, backlight))

    def run(self):
        try:
            lcd = LCD()
        except OSError as error:
            lcd = None
            self.events.put(("error", f"LCD not found: {error}"))
        buzzer = Buzzer(BUZZER)
        led = RGBLED(LED_R, LED_G, LED_B, active_high=not COMMON_ANODE_LED)
        colors = {"green": (0, 1, 0), "red": (1, 0, 0), "blue": (0, 0, 1), "off": (0, 0, 0)}
        while True:
            text, color, beep, light = self.jobs.get()
            if lcd and light is not None:
                lcd.backlight(light)
            if lcd and text:
                lcd.show(*text)
            if color == "standby":
                led.pulse(on_color=(0, 0, 0.2), off_color=(0, 0, 0))   # slow dim blue
            elif color:
                led.color = colors[color]
            if beep == "ok":                             # short double beep
                for _ in range(2):
                    buzzer.on(); time.sleep(0.08); buzzer.off(); time.sleep(0.08)
            elif beep == "error":                        # long error tone
                buzzer.on(); time.sleep(0.8); buzzer.off()
            elif beep == "click":
                buzzer.on(); time.sleep(0.02); buzzer.off()
                

# ---------------- module tests: python3 hardware.py <part> ----------------
if __name__ == "__main__":
    part = sys.argv[1] if len(sys.argv) > 1 else ""
    if part == "rfid":
        reader = RC522()
        print("RC522 found. Tap a card (Ctrl+C to stop)...")
        while True:
            uid = reader.read_uid()
            if uid:
                print("Card UID:", uid)
                time.sleep(1)
            time.sleep(0.1)
    elif part == "lcd":
        LCD().show("LCD test OK!", time.strftime("%H:%M:%S"))
        print("Check the LCD. Blank? Turn the blue screw on the back.")
    elif part == "led":
        led = RGBLED(LED_R, LED_G, LED_B, active_high=not COMMON_ANODE_LED)
        for name, color in (("RED", (1, 0, 0)), ("GREEN", (0, 1, 0)), ("BLUE", (0, 0, 1))):
            print("LED should be", name)
            led.color = color
            time.sleep(1.5)
    elif part == "buzzer":
        buzzer = Buzzer(BUZZER)
        print("Double beep, then long beep")
        for _ in range(2):
            buzzer.on(); time.sleep(0.08); buzzer.off(); time.sleep(0.08)
        time.sleep(1)
        buzzer.on(); time.sleep(0.8); buzzer.off()
    elif part == "distance":
        sensor = DistanceSensor(echo=ECHO, trigger=TRIG, max_distance=2)
        while True:
            print(f"{sensor.distance * 100:6.1f} cm")
            time.sleep(0.3)
    elif part == "keypad":
        keypad = Keypad()
        print("Press keys (Ctrl+C to stop)...")
        while True:
            key = keypad.get_key()
            if key:
                print("Key:", key)
            time.sleep(0.02)
    else:
        print("Usage: python3 hardware.py rfid | lcd | led | buzzer | distance | keypad")
