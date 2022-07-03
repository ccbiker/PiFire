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

# *****************************************
# Class Definition
# *****************************************
class PID:
	def __init__(self,  pb, ti, td, center):
		self._calculate_gains(pb,ti,td)

		self.p = 0.0
		self.i = 0.0
		self.d = 0.0
		self.u = 0

		self.last_update = time.time()
		self.error = 0.0
		self.set_point = 0
		self.center = center

		settings = ReadSettings()
		self.center = settings['cycle_data']['center']
		self.u_min = settings['cycle_data']['u_min']
		self.u_max = settings['cycle_data']['u_max']
		
		self.derv = 0.0
		self.inter = 0.0
		self.last = None

		self.set_target(0.0)
		self.recentCycleRatios = [settings['cycle_data']['u_min'] for x in range(int(120/8))] # 120s (2 minute) / cycletime (8s) #TODO pass cycletime value into PID?
		self.lastFanSpeed = 70

	def _calculate_gains(self, pb, ti, td):
		self.kp = -1 / pb
		self.ki = self.kp / ti
		self.kd = self.kp * td

	def update(self, current):
		if self.last is None:
				self.last = current # avoid derivative spike on first update
		# P
		error = current - self.set_point
		self.p = self.kp * error + self.center # p = 1 for pb / 2 under set_point, p = 0 for pb / 2 over set_point
		#I
		dT = time.time() - self.last_update
		if abs(error) > 0: # integral deadband
			self.inter += error*dT
		self.i = self.ki * self.inter

		#D
		self.derv = (current - self.last) / dT
		self.d = self.kd * self.derv

		# PID
		self.u = self.p + self.i + self.d

		if self.u > self.u_max:
			self.inter -= error*dT # undo accumulation
			self.u = self.u_max
		elif self.u < self.u_min:
			if error > 10:
				self.inter -= error*dT # undo accumulation if there is a large positive error, but allow it to accumulate so low setpoints can be reached from above instead of bouncing up due to center setting
			else:
				self.inter -= error*dT * 0.7 #partly undo accum (allow partial accumulation so Prop/center setting doesn't cause CR to keep going over u_min  after being at u_min for a while and needing to go down further			
			self.u = self.u_min
		# Update for next cycle
		self.error = error
		self.last = current
		self.last_update = time.time()

		self.recentCycleRatios.append(self.u)
		self.recentCycleRatios.remove(self.recentCycleRatios[0])
		
		return self.u

	def set_target(self, set_point):
		self.set_point = set_point
		self.error = 0.0
		self.inter = 0.0
		self.derv = 0.0
		self.last_update = time.time()

	def set_gains(self, pb, ti, td):
		self._calculate_gains(pb,ti,td)
		
	def get_k(self):
		return self.kp, self.ki, self.kd

	def getPID(self):
		return self.P, self.I, self.D
	
	def computeFanSpeed(self):
		
		minfan = 70
		maxfan = 82
		mincr = self.u_min
		maxcr = 0.18
		
		avgCycleRatio = sum(self.recentCycleRatios)/len(self.recentCycleRatios)  # average of recent cycle ratios
		cr_fan = max(avgCycleRatio, self.u)  # cycle ratio value to use to compute fan speed.  Use recent average or most recent value, whichever is higher
		if cr_fan > maxcr:
			fan = maxfan
		elif cr_fan < mincr:
			fan = minfan
		else:
			fan = minfan + int((cr_fan - mincr)/(maxcr - mincr)*(maxfan-minfan))

		# limit how quickly the fan speed is adjusted
		if fan > self.lastFanSpeed + 5:
			fan = self.lastFanSpeed + 5
		elif fan < self.lastFanSpeed - 5:
			fan = self.lastFanSpeed - 5

		# maxerr = 40
		# minerr = 8
		# if abs(self.error) > maxerr:
			# fan = maxfan
		# elif abs(self.error) < minerr:
			# fan = minfan
		# else:
			# fan = minfan + int((abs(self.error) - minerr)/(maxerr - minerr)*(maxfan - minfan))
		self.lastFanSpeed = fan
		return fan