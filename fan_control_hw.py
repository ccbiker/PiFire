import pigpio

fan = pigpio.pi()
pwm_pin = 13

go = True
while go:
     
    x = input('Enter new fan duty cycle (%)...: ')
    if x == '':
        go = False
        print('Done.')
    else:
        if x == '00':
            newdc = 0
        else:
            newdc = int(x)
            newdc = max(10,newdc)
            newdc = min(100,newdc)
        fan.hardware_PWM(pwm_pin, 20000, newdc * 10000)
        print(f'Set Duty Cycle to {newdc}%')
fan.stop() # stop connection to pigpio
