import pigpio
import time

fan = pigpio.pi()
pwm_pin = 13
tach_pin = 19

cb = fan.callback(tach_pin, pigpio.FALLING_EDGE)

try:
	while(True):
		start = time.perf_counter()
		cb.reset_tally()
#		time.sleep(1.5)
#		tally = cb.tally()
		while(time.perf_counter() - start < 1):
			pass
		end = time.perf_counter()
		tally = cb.tally()
		rpm = tally / 2.0 / (end - start) * 60
		print(f'RPM: {rpm:.0f}')
except:
	print('\nending now...')
	fan.stop() # stop connection to pigpio
