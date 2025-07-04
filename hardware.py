from smbus2 import SMBus
from RPLCD.i2c import CharLCD
import wiringpi
from wiringpi import GPIO
from gpio_setup import setup_gpio
import time


class Keypad:
    def __init__(self):
        # Определение пинов
        self.ROWS = [2, 5, 7, 8]     # строки (INPUT с подтяжкой вверх)
        self.COLS = [3, 4, 19, 20]   # столбцы (OUTPUT)
        
        self.keys = [
            ['1', '2', '3', 'A'],
            ['4', '5', '6', 'B'],
            ['7', '8', '9', 'C'],
            ['*', '0', '#', 'D']
        ]
        
        # Инициализация WiringPi
        setup_gpio()

        # Настройка строк как входы с подтяжкой вверх
        for row in self.ROWS:
            wiringpi.pinMode(row, wiringpi.GPIO.INPUT)
            wiringpi.pullUpDnControl(row, wiringpi.GPIO.PUD_UP)

        # Колонки сначала как входы (высокоимпедансные)
        for col in self.COLS:
            wiringpi.pinMode(col, wiringpi.GPIO.INPUT)

    def get_key(self):
        for col_idx, col in enumerate(self.COLS):
            wiringpi.pinMode(col, wiringpi.GPIO.OUTPUT)
            wiringpi.digitalWrite(col, wiringpi.GPIO.LOW)
            time.sleep(0.002)

            for row_idx, row in enumerate(self.ROWS):
                if wiringpi.digitalRead(row) == wiringpi.GPIO.LOW:
                    key = self.keys[row_idx][col_idx]
                    while wiringpi.digitalRead(row) == wiringpi.GPIO.LOW:
                        time.sleep(0.01)  # антидребезг: ждём отпускания
                    wiringpi.pinMode(col, wiringpi.GPIO.INPUT)
                    return key

            wiringpi.pinMode(col, wiringpi.GPIO.INPUT)
            time.sleep(0.001)

        return None

class LCD:
    def __init__(self, address=0x27, port=0, cols=16, rows=2):
        self.lcd = CharLCD('PCF8574', address, port=port, cols=cols, rows=rows)
        self.clear()

    def clear(self):
        self.lcd.clear()

    def display(self, line1='', line2='', delay=0):
        """
        Выводит две строки на экран. Можно указать задержку (в секундах).
        """
        self.clear()
        self.lcd.write_string(line1[:16])
        self.lcd.cursor_pos = (1, 0)
        self.lcd.write_string(line2[:16])
        if delay > 0:
            time.sleep(delay)
            self.clear()
    
    def write_line(self, text, line=0):
        """
        Выводит текст на заданную строку (0 или 1).
        """
        if line not in [0, 1]:
            raise ValueError("line должен быть 0 или 1")
        self.lcd.cursor_pos = (line, 0)
        self.lcd.write_string(text[:16])

class MotionSensor:
    def __init__(self, pin=22):
        self.pin = pin
        self.motion_detected_flag = False
        self.callback_function = None  # Сюда сохраняем пользовательскую функцию

        setup_gpio() 
        wiringpi.pinMode(self.pin, wiringpi.GPIO.INPUT)
        wiringpi.pullUpDnControl(self.pin, wiringpi.GPIO.PUD_DOWN)

        wiringpi.wiringPiISR(self.pin, wiringpi.GPIO.INT_EDGE_RISING, self._isr)

    def _isr(self):
        """
        Встроенное прерывание:
        1. Ставит флаг.
        2. Если есть пользовательская функция, вызывает её.
        """
        self.motion_detected_flag = True

        if self.callback_function:
            try:
                self.callback_function()
            except Exception as e:
                # Защита на случай ошибки в пользовательской функции
                print(f"Ошибка в callback: {e}")

    def on_motion(self, callback_function):
        """
        Регистрируем пользовательскую функцию, которая будет вызываться при движении.
        """
        self.callback_function = callback_function

    def read(self):
        """
        Прямое считывание состояния пина.
        """
        return wiringpi.digitalRead(self.pin)

    def check_motion(self):
        """
        Возвращает True, если было движение.
        Сбрасывает флаг.
        """
        if self.motion_detected_flag:
            self.motion_detected_flag = False
            return True
        return False

class PinAction:
    def __init__(self, pin: int):
        self.pin = pin
        setup_gpio()          # Инициализация WiringPi (один раз)
        wiringpi.pinMode(self.pin, 1)     # 1 = OUTPUT

    def on(self):
        wiringpi.digitalWrite(self.pin, 1)

    def off(self):
        wiringpi.digitalWrite(self.pin, 0)

    def toggle(self):
        current_state = self.read()
        if current_state == 1:
            self.off()
        else:
            self.on()

    def blink(self, times: int, delay: float = 0.5):
        for _ in range(times):
            self.on()
            time.sleep(delay)
            self.off()
            time.sleep(delay)

    def read(self) -> int:
        try:
            return wiringpi.digitalRead(self.pin)
        except Exception as e:
            print(f"[Ошибка] Не удалось прочитать состояние пина {self.pin}: {e}")
            return -1


class Relay:
    def __init__(self, pin: int=23):
        self.pin = pin
        setup_gpio()
        wiringpi.pinMode(self.pin, 1)

    def on(self):
        wiringpi.digitalWrite(self.pin, 1)

    def off(self):
        wiringpi.digitalWrite(self.pin, 0)
   