import wiringpi

_initialized = False

def setup_gpio():
    global _initialized
    if not _initialized:
        wiringpi.wiringPiSetup()
        #wiringpi.wiringPiSetupSys()
        #wiringpi.wiringPiSetupGpio()
        _initialized = True