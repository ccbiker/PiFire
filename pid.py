#!/usr/bin/env python3

# *****************************************
# PiFire PID Controller
# *****************************************
#
# Description: This object will be used to calculate PID for maintaining
# temperature in the grill.
#
# This software was developed by GitHub user DBorello as part of his excellent
# PiSmoker project: https://github.com/DBorello/PiSmoker
#
# Adapted for PiFire
#
# PID controller based on proportional band in standard PID form https://en.wikipedia.org/wiki/PID_controller#Ideal_versus_standard_PID_form
#   u = Kp (e(t)+ 1/Ti INT + Td de/dt)
#  PB = Proportional Band
#  Ti = Goal of eliminating in Ti seconds
#  Td = Predicts error value at Td in seconds

# *****************************************

# *****************************************
# Imported Libraries
# *****************************************
import time
from common import ReadSettings

# *****************************************
# Class Definition
# *****************************************
class PID:
	def __init__(self,  PB, Ti, Td):
		self.CalculateGains(PB,Ti,Td)

		self.P = 0.0
		self.I = 0.0
		self.D = 0.0
		self.u = 0

		settings = ReadSettings()
		self.Center = settings['cycle_data']['center']
		self.u_min = settings['cycle_data']['u_min']
		self.u_max = settings['cycle_data']['u_max']

		self.Derv = 0.0
		self.Inter = 0.0
		self.Inter_max = abs(0.4/self.Ki) # abs(self.Center/self.Ki)

		self.Last = None

		self.setTarget(0.0)

		self.recentCycleRatios = [settings['cycle_data']['u_min'] for x in range(int(120/8))] # 120s (2 minute) / cycletime (8s) #TODO pass cycletime value into PID?

	def CalculateGains(self,PB,Ti,Td):
		self.Kp = -1/PB
		self.Ki = self.Kp/Ti
		self.Kd = self.Kp*Td

	def update(self, Current):
		if self.Last is None:
			self.Last = Current  # avoid derivative spike on first update
		#P
		error = Current - self.setPoint
		self.P = self.Kp*error + self.Center #P = 1 for PB/2 under setPoint, P = 0 for PB/2 over setPoint

		#I
		dT = time.time() - self.LastUpdate
		#if self.P > 0 and self.P < 1: #Ensure we are in the PB, otherwise do not calculate I to avoid windup
		if abs(error) > 0:  # integral deadband
			self.Inter += error*dT
		self.I = self.Ki * self.Inter

		#D
		self.Derv = (Current - self.Last)/dT
		self.D = self.Kd * self.Derv
#		self.D = min(self.D, 0.5)

		#PID
		self.u = self.P + self.I + self.D
		if self.u >  self.u_max:
			self.Inter -= error*dT # undo accumulation
			self.u = self.u_max
		elif self.u < self.u_min:
			if error > 10:
				self.Inter -= error*dT  # undo accumulation if there is a large positive error, but allow it to accumulate so low setpoints can be reached from above instead of bouncing up due to center setting
			else:
				self.Inter -= error*dT * 0.7 #partly undo accum (allow partial accumulation so Prop/center setting doesn't cause CR to keep going over u_min  after being at u_min for a while and needing to go down further			self.I += self.u_min - self.u
			self.u = self.u_min
		#Update for next cycle
		self.error = error
		self.Last = Current
		self.LastUpdate = time.time()

		self.recentCycleRatios.append(self.u)
		self.recentCycleRatios.remove(self.recentCycleRatios[0])

		return self.u

	def	setTarget(self, setPoint):
		self.setPoint = setPoint
		self.error = 0.0
		self.Inter = 0.0
		self.Derv = 0.0
		self.LastUpdate = time.time()

	def setGains(self, PB, Ti, Td):
		self.CalculateGains(PB,Ti,Td)
		self.Inter_max = abs(self.Center/self.Ki)

	def getK(self):
		return self.Kp, self.Ki, self.Kd

	def getPID(self):
		return self.P, self.I, self.D

	def computeFanSpeed(self):
		dCR = self.u - (sum(self.recentCycleRatios)/len(self.recentCycleRatios))
		fan = 65 #80
		if dCR <= 0:
			fan = 80
		elif dCR < .01:
			fan = 83
		elif dCR < .02:
			fan = 88
		elif dCR < .03:
			fan = 92
		else:
			fan = 95
		
		maxfan = 90
		minfan = 70
		maxerr = 40
		minerr = 8
		if abs(self.error) > maxerr:
			fan = maxfan
		elif abs(self.error) < minerr:
			fan = minfan
		else:
			fan = minfan + int((abs(self.error) - minerr)/(maxerr - minerr)*(maxfan - minfan))

		return fan