#!/usr/bin/env python3

# *****************************************
# PiFire OEM Interface Library
# *****************************************
#
# Description: This library supports 
#  controlling the PiFire Outputs, alongside 
#  the OEM controller outputs via
#  Raspberry Pi GPIOs, to a 4-channel relay
#
# *****************************************

# *****************************************
# Imported Libraries
# *****************************************

import pigpio
#import RPi.GPIO as GPIO
from time import sleep

class GrillPlatform:

	def __init__(self, outpins, inpins, triggerlevel='LOW'):
		self.pigpio = pigpio.pi()

		self.outpins = outpins # { 'power' : 18, 'auger' : 4, 'fan' : 15, 'igniter' : 14 }
		self.inpins = inpins # { 'selector' : 17 }

		self.pwm_freq = 20000
		self.pwm_duty = 70
		self.fantoggle = False

		if triggerlevel == 'LOW': 
			# Defines for Active LOW relay
			self.RELAY_ON = 0
			self.RELAY_OFF = 1
		else:
			# Defines for Active HIGH relay
			self.RELAY_ON = 1
			self.RELAY_OFF = 0 

#		GPIO.setwarnings(False)
#		GPIO.setmode(GPIO.BCM)
		for item in self.inpins:
		#GPIO.setup(self.inpins[item], GPIO.IN, pull_up_down=GPIO.PUD_UP)
			self.pigpio.set_mode(self.inpins[item], pigpio.INPUT)
			self.pigpio.set_pull_up_down(self.inpins[item], pigpio.PUD_UP)
		#if GPIO.input(self.inpins['selector']) == 0:
			#GPIO.setup(self.outpins['power'], GPIO.OUT, initial=self.RELAY_ON)
			#GPIO.setup(self.outpins['igniter'], GPIO.OUT, initial=self.RELAY_OFF)
			#GPIO.setup(self.outpins['fan'], GPIO.OUT, initial=self.RELAY_OFF)
			#GPIO.setup(self.outpins['auger'], GPIO.OUT, initial=self.RELAY_OFF)
		#self.pigpio.set_mode(self.inpins[item], pigpio.INPUT)
		#else:
			#GPIO.setup(self.outpins['power'], GPIO.OUT, initial=self.RELAY_OFF)
			#GPIO.setup(self.outpins['igniter'], GPIO.OUT, initial=self.RELAY_OFF)
			#GPIO.setup(self.outpins['fan'], GPIO.OUT, initial=self.RELAY_OFF)
			#GPIO.setup(self.outpins['auger'], GPIO.OUT, initial=self.RELAY_OFF)
		for item in self.outpins:
			self.pigpio.set_mode(self.outpins[item], pigpio.OUTPUT)
			self.pigpio.write(self.outpins[item], self.RELAY_OFF)
#		GPIO.setup(self.outpins['pwm'], GPIO.OUT)
#		self.pwm = GPIO.PWM(self.outpins['pwm'], self.pwm_freq)
#		self.pwm.start(self.pwm_duty)

		self.pigpio.set_PWM_frequency(self.outpins['pwm'], 20000)
		self.pigpio.set_PWM_dutycycle(self.outpins['pwm'], self.pwm_duty)
		self.pigpio.set_PWM_range(self.outpins['pwm'], 100)

#		for i in range(10):
#			self.FanRamp(0,100)

	def FanRamp(self, min = 0, max = 100):
		for duty in range(min, max + 1, 10):
#			self.pwm.ChangeDutyCycle(duty)
#			self.pwm.set_PWM_dutycycle(duty)
			self.FanDutyCycle(duty)
			sleep(3)
		for duty in range(max, min-1, -10):
#			self.pwm.ChangeDutyCycle(duty)
			self.FanDutyCycle(duty)
#			self.pwm.set_PWM_dutycycle(duty)
			sleep(0.5)
	def FanDutyCycle(self, dutycycle):
		self.pigpio.set_PWM_dutycycle(self.outpins['pwm'], dutycycle)

	def AugerOn(self):
		#GPIO.output(self.outpins['auger'], self.RELAY_ON)
		self.pigpio.write(self.outpins['auger'], self.RELAY_ON)

	def AugerOff(self):
		#GPIO.output(self.outpins['auger'], self.RELAY_OFF)
		self.pigpio.write(self.outpins['auger'], self.RELAY_OFF)

	def FanOn(self):
		#GPIO.output(self.outpins['fan'], self.RELAY_ON)
		self.pigpio.write(self.outpins['fan'], self.RELAY_ON)

	def FanOff(self):
		#GPIO.output(self.outpins['fan'], self.RELAY_OFF)
		self.pigpio.write(self.outpins['fan'], self.RELAY_OFF)

	def FanToggle(self):
		#if(GPIO.input(self.outpins['fan']) == self.RELAY_ON):
		#	GPIO.output(self.outpins['fan'], self.RELAY_OFF)
		#else:
		#	GPIO.output(self.outpins['fan'], self.RELAY_ON)
		if(self.pigpio.read(self.outpins['fan']) == self.RELAY_ON):
			self.pigpio.write(self.outpins['fan'], self.RELAY_OFF)
		else:
			self.pigpio.write(self.outpins['fan'], self.RELAY_ON)
		if self.fantoggle:
			self.fantoggle = False
			self.FanDutyCycle(25)
		else:
			self.fantoggle = True
			self.FanDutyCycle(65)

	def IgniterOn(self):
		#GPIO.output(self.outpins['igniter'], self.RELAY_ON)
		self.pigpio.write(self.outpins['igniter'], self.RELAY_ON)

	def IgniterOff(self):
		#GPIO.output(self.outpins['igniter'], self.RELAY_OFF)
		self.pigpio.write(self.outpins['igniter'], self.RELAY_OFF)

	def PowerOn(self):
		#GPIO.output(self.outpins['power'], self.RELAY_ON)
		self.pigpio.write(self.outpins['power'], self.RELAY_ON)

	def PowerOff(self):
		#GPIO.output(self.outpins['power'], self.RELAY_OFF)
		self.pigpio.write(self.outpins['power'], self.RELAY_OFF)

	def GetInputStatus(self):
		#return (GPIO.input(self.inpins['selector']))
		return (self.pigpio.read(self.inpins['selector']))

	def GetOutputStatus(self):
		self.current = {}
		for item in self.outpins:
			#self.current[item] = GPIO.input(self.outpins[item])
			self.current[item] = self.pigpio.read(self.outpins[item])
		return self.current
