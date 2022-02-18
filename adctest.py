#!/usr/bin/env python3


# *****************************************
# Imported Libraries
# *****************************************

import time
import os
import json
import datetime
from common import *  # Common Library for WebUI and Control Program
from pushbullet import Pushbullet # Pushbullet Import
import pid as PID # Library for calculating PID setpoints
import requests
from temp_queue import TempQueue
import pid_fan

# Read Settings to Get Modules Configuration 
settings = ReadSettings()

if(settings['modules']['grillplat'] == 'pifire'):
	from grillplat_pifire import GrillPlatform # Library for controlling the grill platform w/Raspberry Pi GPIOs
else:
	from grillplat_prototype import GrillPlatform # Simulated Library for controlling the grill platform

if(settings['modules']['adc'] == 'ads1115'):
	from adc_ads1115 import ReadADC # Library for reading the ADC device
else: 
	from adc_prototype import ReadADC # Simulated Library for reading the ADC device
	
if(settings['modules']['display'] == 'ssd1306'):
	from display_ssd1306 import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'ssd1306b'):
	from display_ssd1306b import Display # Library for controlling the display device w/button input
elif(settings['modules']['display'] == 'st7789p'):
	from display_st7789p import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'pygame'):
	from display_pygame import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'pygame_240x320'):
	from display_pygame_240x320 import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'pygame_240x320b'):
	from display_pygame_240x320b import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'pygame_64x128'):
	from display_pygame_64x128 import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'ili9341'):
	from display_ili9341 import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'ili9341_encoder'):
	from display_ili9341_encoder import Display # Library for controlling the display device
elif(settings['modules']['display'] == 'ili9341b'):
	from display_ili9341b import Display # Library for controlling the display device
else:
	from display_prototype import Display # Simulated Library for controlling the display device

if(settings['modules']['dist'] == 'vl53l0x'):
	from distance_vl53l0x import HopperLevel # Library for reading the HopperLevel from vl53l0x TOF Sensor
elif(settings['modules']['dist'] == 'hcsr04'):
	from distance_hcsr04 import HopperLevel # Library for reading HopperLevel HC-SR04 Ultrasonic Sensor
else: 
	from distance_prototype import HopperLevel # Simulated Library for reading the HopperLevel

# *****************************************
# Function Definitions
# *****************************************

def GetStatus(grill_platform, control, settings, pelletdb):
	# *****************************************
	# Get Status Details for Display Function
	# *****************************************
	status_data = {}
	status_data['outpins'] = {}

	current = grill_platform.GetOutputStatus()	# Get current pin settings

	if settings['globals']['triggerlevel'] == 'LOW':
		for item in settings['outpins']:
			status_data['outpins'][item] = current[item]
	else:
		for item in settings['outpins']:
			status_data['outpins'][item] = not current[item] # Reverse Logic

	status_data['mode'] = control['mode'] # Get current mode
	status_data['notify_req'] = control['notify_req'] # Get any flagged notificiations
	status_data['timer'] = control['timer'] # Get the timer information
	status_data['ipaddress'] = '192.168.10.43' # Future implementation (TODO)
	status_data['s_plus'] = control['s_plus']
	status_data['hopper_level'] = pelletdb['current']['hopper_level']
	status_data['units'] = settings['globals']['units']

	return(status_data)

def WorkCycle(mode, grill_platform, adc_device, display_device, dist_device):
	# *****************************************
	# Work Cycle Function
	# *****************************************
	event = mode + ' Mode started.'
	WriteLog(event)

	# Setup Cycle Parameters
	settings = ReadSettings()
	control = ReadControl()
	pelletdb = ReadPelletDB()

	# Get ON/OFF Switch state and set as last state
	last = grill_platform.GetInputStatus()

	# Set Starting Configuration for Igniter, Fan , Auger
	grill_platform.FanOn()
	grill_platform.IgniterOff()
	grill_platform.AugerOff()
	grill_platform.PowerOn()
	grill_platform.pigpio.set_PWM_frequency(grill_platform.outpins['pwm'], 20000)
	grill_platform.pigpio.set_PWM_range(grill_platform.outpins['pwm'], 100)
	grill_platform.FanDutyCycle(50)

	if(settings['globals']['debug_mode'] == True):
		event = '* Fan ON, Igniter OFF, Auger OFF'
		print(event)
		WriteLog(event)
	if ((mode == 'Startup') or (mode == 'Reignite')):
		grill_platform.IgniterOn()
		if(settings['globals']['debug_mode'] == True):
			event = '* Igniter ON'
			print(event)
			WriteLog(event)
	if ((mode == 'Smoke') or (mode == 'Hold') or (mode == 'Startup') or (mode == 'Reignite')):
		grill_platform.AugerOn()
		grill_platform.FanDutyCycle(55)
		if(settings['globals']['debug_mode'] == True):
			event = '* Auger ON'
			print(event)
			WriteLog(event)

	if (mode == 'Startup' or 'Smoke' or 'Reignite'):
		OnTime = settings['cycle_data']['SmokeCycleTime'] #  Auger On Time (Default 15s)
		OffTime = 45 + (settings['cycle_data']['PMode'] * 10) 	#  Auger Off Time
		CycleTime = OnTime + OffTime 	#  Total Cycle Time
		CycleRatio = OnTime / CycleTime #  Ratio of OnTime to CycleTime

		# test zone -------
		OnTime =  .5
		OffTime = 20
		CycleTime = OnTime + OffTime
		CycleRatio = OnTime / CycleTime


	if (mode == 'Shutdown'):
		OnTime = 0		#  Auger On Time
		OffTime = 100	#  Auger Off Time
		CycleTime = 100 #  Total Cycle Time
		CycleRatio = 0 	#  Ratio of OnTime to CycleTime

	if (mode == 'Hold'):
		OnTime = settings['cycle_data']['HoldCycleTime'] * settings['cycle_data']['u_min']		#  Auger On Time
		OffTime = settings['cycle_data']['HoldCycleTime'] * (1 - settings['cycle_data']['u_min'])	#  Auger Off Time
		CycleTime = settings['cycle_data']['HoldCycleTime'] #  Total Cycle Time
		CycleRatio = settings['cycle_data']['u_min'] 	#  Ratio of OnTime to CycleTime

		OnTime = 0.1 # force a quick cycle so that within the main work cycle loop the ratio can be computed promptly instead of waiting a whole cycle at min ratio, which is annoying when setpoint is increased and it sits there at min_u for a whole cycle
		OffTime = 0.9
		CycleTime = 1.0
		CycleRatio = 0.1

		PIDControl = PID.PID(settings['cycle_data']['PB'],settings['cycle_data']['Ti'],settings['cycle_data']['Td'])
		PIDControl.setTarget(control['setpoints']['grill'])	# Initialize with setpoint for grill
		PIDFan = pid_fan.PIDfan(55, 85, -30, 20)
		PIDFan.setTarget(control['setpoints']['grill'])
		if(settings['globals']['debug_mode'] == True):
			event = '* On Time = ' + str(OnTime) + ', OffTime = ' + str(OffTime) + ', CycleTime = ' + str(CycleTime) + ', CycleRatio = ' + str(CycleRatio)
			print(event)
			WriteLog(event)

	# Initialize all temperature variables
	AvgGT = TempQueue(units=settings['globals']['units'], qlength = 30)
	AvgP1 = TempQueue(units=settings['globals']['units'])
	AvgP2 = TempQueue(units=settings['globals']['units'])

	# Check pellets level notification upon starting cycle
	CheckNotifyPellets(control, settings, pelletdb)

	# Collect Initial Temperature Information
	# Get Probe Types From Settings
	grill0type = settings['probe_types']['grill0type']
	probe1type = settings['probe_types']['probe1type']
	probe2type = settings['probe_types']['probe2type']

	adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

	adc_data = {}
	adc_data = adc_device.ReadAllPorts()

	AvgGT.enqueue(adc_data['GrillTemp'])
	AvgP1.enqueue(adc_data['Probe1Temp'])
	AvgP2.enqueue(adc_data['Probe2Temp'])

	status = 'Active'

	# Safety Controls
	if ((mode == 'Startup') or (mode == 'Reignite')):
		#control = ReadControl()  # Read Modify Write
#		control['safety']['startuptemp'] = int(max((AvgGT.average()*0.9), settings['safety']['minstartuptemp']))
		control['safety']['startuptemp'] = int(max((AvgGT.average()*0.4), settings['safety']['minstartuptemp'])) #temporary hack to allow restarting while hot for debugging and tuning
		control['safety']['startuptemp'] = int(min(control['safety']['startuptemp'], settings['safety']['maxstartuptemp']))
		control['safety']['afterstarttemp'] = AvgGT.average()
		WriteControl(control)
	# Check if the temperature of the grill dropped below the startuptemperature 
	elif ((mode == 'Hold') or (mode == 'Smoke')):
		if (control['safety']['afterstarttemp'] < control['safety']['startuptemp']):
			if(control['safety']['reigniteretries'] == 0):
				status = 'Inactive'
				event = 'ERROR: Grill temperature dropped below minimum startup temperature of ' + str(control['safety']['startuptemp']) + settings['globals']['units'] + '! Shutting down to prevent firepot overload.'
				WriteLog(event)
				display_device.DisplayText('ERROR')
				#control = ReadControl()  # Read Modify Write
				control['mode'] = 'Error'
				control['updated'] = True
				WriteControl(control)
				SendNotifications("Grill_Error_02", control, settings, pelletdb)
			else:
				#control = ReadControl()  # Read Modify Write
				control['safety']['reigniteretries'] -= 1
				control['safety']['reignitelaststate'] = mode 
				status = 'Inactive'
				event = 'ERROR: Grill temperature dropped below minimum startup temperature of ' + str(control['safety']['startuptemp']) + settings['globals']['units'] + '. Starting a re-ignite attempt, per user settings.'
				WriteLog(event)
				display_device.DisplayText('Re-Ignite')
				control['mode'] = 'Reignite'
				control['updated'] = True
				WriteControl(control)

	# Set the start time
	starttime = time.time()

	# Set time since toggle for temperature
	temptoggletime = starttime

	# Set time since toggle for auger
	augertoggletime = starttime

	# Set time since toggle for display
	displaytoggletime = starttime 

	# Initializing Start Time for Smoke Plus Mode
	sp_cycletoggletime = starttime 

	# Set time since toggle for hopper check
	hoppertoggletime = starttime 

	# Set time since last control check
	controlchecktime = starttime

	# Set time since last pellet level check
	pelletschecktime = starttime

	# Set time since last PID recompute
	PIDupdatetime = starttime

	# Initialize Current Auger State Structure
	current_output_status = {}

	# Set Hold Mode Target Temp Boolean
	target_temp_achieved = False

	# ============ Main Work Cycle ============
	while(status == 'Active'):
		now = time.time()

		# Check for button input event
		display_device.EventDetect()

		# Check for update in control status every 0.1 seconds 
		if (now - controlchecktime > 0.1):
			control = ReadControl()
			controlchecktime = now

		# Check for pellet level notifications every 20 minutes
		if (now - pelletschecktime > 1200):
			CheckNotifyPellets(control, settings, pelletdb)
			pelletschecktime = now

		# Check if new mode has been requested 
		if (control['updated'] == True):
			status = 'Inactive'
			break

		# Check hopper level when requested or every 300 seconds 
		if (control['hopper_check'] == True) or (now - hoppertoggletime > 300):
			pelletdb = ReadPelletDB()
			# Get current hopper level and save it to the current pellet information
			pelletdb['current']['hopper_level'] = dist_device.GetLevel()
			WritePelletDB(pelletdb)
			hoppertoggletime = now
			if(control['hopper_check'] == True):
				#control = ReadControl()  # Read Modify Write
				control['hopper_check'] = False
				WriteControl(control)
			if(settings['globals']['debug_mode'] == True):
				event = "* Hopper Level Checked @ " + str(pelletdb['current']['hopper_level']) + "%"
				print(event)
				WriteLog(event)

		# Check for update in ON/OFF Switch
		if (last != grill_platform.GetInputStatus()):
			last = grill_platform.GetInputStatus()
			if(last == 1):
				status = 'Inactive'
				event = 'Switch set to off, going to monitor mode.'
				WriteLog(event)
				#control = ReadControl()  # Read Modify Write
				control['updated'] = True # Change mode
				control['mode'] = 'Stop'
				control['status'] = 'active'
				WriteControl(control)
				break

		# Change Auger State based on Cycle Time
		current_output_status = grill_platform.GetOutputStatus()


		# If in "Hold" mode, recompute PID settings
		if (mode == 'Hold'):
			if now - PIDupdatetime > 6:
				#PIDControl.update(AvgGT.average())
				#newdc = PIDFan.update(AvgGT.average())
				if CycleRatio > 0.35:
					newdc = 95
				elif CycleRatio > 0.3:
					newdc = 85
				elif CycleRatio > 0.2:
					newdc = 70
				else:
					newdc = 55
				grill_platform.FanDutyCycle(newdc)
				PIDupdatetime = now
				if (settings['globals']['debug_mode'] == True):
					event = '* New Fan Duty Cycle: ' + str(round(newdc,1)) + '%'
					print(event)
					WriteLog(event)

		# If Auger is OFF and time since toggle is greater than Off Time
		if (current_output_status['auger'] == AUGEROFF) and (now - augertoggletime > CycleTime * (1-CycleRatio)):
			grill_platform.AugerOn()
			augertoggletime = now
			# Reset Cycle Time for HOLD Mode
			if (mode == 'Hold'):
				CycleRatio = PIDControl.update(AvgGT.average())
				#CycleRatio = max(CycleRatio, settings['cycle_data']['u_min'])
				#CycleRatio = min(CycleRatio, settings['cycle_data']['u_max'])
				OnTime = settings['cycle_data']['HoldCycleTime'] * CycleRatio
				OffTime = settings['cycle_data']['HoldCycleTime'] * (1 - CycleRatio)
				CycleTime = OnTime + OffTime
				if(settings['globals']['debug_mode'] == True):
					event = '* On Time = ' + str(OnTime) + ', OffTime = ' + str(OffTime) + ', CycleTime = ' + str(CycleTime) + ', CycleRatio = ' + str(CycleRatio) + ', PID = ' + str(PIDControl.getPID())
					print(event)
					WriteLog(event)
			if(settings['globals']['debug_mode'] == True):
				event = '* Cycle Event: Auger On'
				print(event)
				WriteLog(event)

		# If Auger is ON and time since toggle is greater than On Time
		if (current_output_status['auger'] == AUGERON) and (now - augertoggletime > CycleTime * CycleRatio):
			grill_platform.AugerOff()
			augertoggletime = now
			if(settings['globals']['debug_mode'] == True):
				event = '* Cycle Event: Auger Off'
				print(event)
				WriteLog(event)

		# Grab current probe profiles if they have changed since the last loop. 
		if (control['probe_profile_update'] == True):
			settings = ReadSettings()
			#control = ReadControl()  # Read Modify Write
			control['probe_profile_update'] = False
			WriteControl(control)
			# Get new probe profiles
			grill0type = settings['probe_types']['grill0type']
			probe1type = settings['probe_types']['probe1type']
			probe2type = settings['probe_types']['probe2type']
			# Add new probe profiles to ADC Object
			adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

		# Get temperatures from all probes
		adc_data = {}
		adc_data = adc_device.ReadAllPorts()
	#	#WriteLog(f'adc_data GrillTemp: {adc_data["GrillTemp"]:0.1f}')

		# Test temperature data returned for errors (+/- 20% Temp Variance), and average the data since last reading
		AvgGT.enqueue(adc_data['GrillTemp'])
		AvgP1.enqueue(adc_data['Probe1Temp'])
		AvgP2.enqueue(adc_data['Probe2Temp'])

		in_data = {}
		in_data['GrillTemp'] = AvgGT.average()
		in_data['GrillSetPoint'] = control['setpoints']['grill']
		in_data['Probe1Temp'] = AvgP1.average()
		in_data['Probe1SetPoint'] = control['setpoints']['probe1']
		in_data['Probe2Temp'] = AvgP2.average()
		in_data['Probe2SetPoint'] = control['setpoints']['probe2']
		in_data['GrillTr'] = adc_data['GrillTr']  # For Temp Resistance Tuning
		in_data['Probe1Tr'] = adc_data['Probe1Tr']  # For Temp Resistance Tuning
		in_data['Probe2Tr'] = adc_data['Probe2Tr']  # For Temp Resistance Tuning

		# Check to see if there are any pending notifications (i.e. Timer / Temperature Settings)
		control = CheckNotify(in_data, control, settings, pelletdb)

		# Check for button input event
		display_device.EventDetect()
		
		# Send Current Status / Temperature Data to Display Device every 0.5 second (Display Refresh)
		if(now - displaytoggletime > 0.5):
			status_data = GetStatus(grill_platform, control, settings, pelletdb)
			display_device.DisplayStatus(in_data, status_data)
			displaytoggletime = time.time() # Reset the displaytoggletime to current time

		# Safety Controls
		if ((mode == 'Startup') or (mode == 'Reignite')):
			control['safety']['afterstarttemp'] = AvgGT.average()
		elif ((mode == 'Hold') or (mode == 'Smoke')):
			if (AvgGT.average() < control['safety']['startuptemp']):
				if(control['safety']['reigniteretries'] == 0):
					status = 'Inactive'
					event = 'ERROR: Grill temperature dropped below minimum startup temperature of ' + str(control['safety']['startuptemp']) + settings['globals']['units'] + '! Shutting down to prevent firepot overload.'
					WriteLog(event)
					display_device.DisplayText('ERROR')
					#control = ReadControl()  # Read Modify Write
					control['mode'] = 'Error'
					control['updated'] = True
					WriteControl(control)
					SendNotifications("Grill_Error_02", control, settings, pelletdb)
				else:
					control['safety']['reigniteretries'] -= 1
					control['safety']['reignitelaststate'] = mode 
					status = 'Inactive'
					event = 'ERROR: Grill temperature dropped below minimum startup temperature of ' + str(control['safety']['startuptemp']) + settings['globals']['units'] + '. Starting a re-ignite attempt, per user settings.'
					WriteLog(event)
					display_device.DisplayText('Re-Ignite')
					#control = ReadControl()  # Read Modify Write
					control['mode'] = 'Reignite'
					control['updated'] = True
					WriteControl(control)

			if (AvgGT.average() > settings['safety']['maxtemp']):
				status = 'Inactive'
				event = 'ERROR: Grill exceed maximum temperature limit of ' + str(settings['safety']['maxtemp']) + 'F! Shutting down.'
				WriteLog(event)
				display_device.DisplayText('ERROR')
				#control = ReadControl()  # Read Modify Write
				control['mode'] = 'Error'
				control['updated'] = True
				WriteControl(control)
				SendNotifications("Grill_Error_01", control, settings, pelletdb)

		# Check if target temperature has been achieved before utilizing Smoke Plus Mode
		if((mode == 'Hold') and (AvgGT.average() >= control['setpoints']['grill']) and (target_temp_achieved==False)):
			target_temp_achieved = True
			
		# If in Smoke Plus Mode, Cycle the Fan
		if(((mode == 'Smoke') or ((mode == 'Hold') and (target_temp_achieved))) and (control['s_plus'] == True)):
			# If Temperature is > settings['smoke_plus']['max_temp'] then turn on fan
			if(AvgGT.average() > settings['smoke_plus']['max_temp']):
				grill_platform.FanOn()
				grill_platform.FanDutyCycle(75)
			# elif Temperature is < settings['smoke_plus']['min_temp'] then turn on fan
			elif(AvgGT.average() < settings['smoke_plus']['min_temp']):
				grill_platform.FanOn()
				grill_platform.FanDutyCycle(75)
			# elif now - sp_cycletoggletime > settings['smoke_plus']['cycle'] / 2 then toggle fan, reset sp_cycletoggletime = now
			elif((now - sp_cycletoggletime) > (settings['smoke_plus']['cycle']*0.5)):
				grill_platform.FanToggle()
				sp_cycletoggletime = now
				if(settings['globals']['debug_mode'] == True):
					event = '* Smoke Plus: Fan Toggled'
					print(event)
					WriteLog(event)

		elif((current_output_status['fan'] == FANOFF) and (control['s_plus'] == False)):
			grill_platform.FanOn()

		# Write History after 3 seconds has passed
		if (now - temptoggletime > 3):
			temptoggletime = time.time()
			WriteHistory(in_data, tuning_mode=control['tuning_mode'])

		# Check if 240s have elapsed since startup/reignite mode started
		if ((mode == 'Startup') or (mode == 'Reignite')):
			if((now - starttime) > 240):
				status = 'Inactive'

		# Check if shutdown time has elapsed since shutdown mode started
		if ((mode == 'Shutdown') and ((now - starttime) > settings['globals']['shutdown_timer'])):
			status = 'Inactive'

		time.sleep(0.05)
		# *********
		# END Mode Loop
		# *********

	# Clean-up and Exit
	grill_platform.AugerOff()
	grill_platform.IgniterOff()
	
	if(settings['globals']['debug_mode'] == True):
		event = '* Auger OFF, Igniter OFF'
		print(event)
		WriteLog(event)
	if(mode == 'Shutdown'):
		grill_platform.FanDutyCycle(0)
		grill_platform.FanOff()
		grill_platform.PowerOff()
		if(settings['globals']['debug_mode'] == True):
			event = '* Fan OFF, Power OFF'
			print(event)
			WriteLog(event)
	if ((mode == 'Startup') or (mode == 'Reignite')):
		#control = ReadControl()  # Read Modify Write
		control['safety']['afterstarttemp'] = AvgGT.average()
		WriteControl(control)
	event = mode + ' mode ended.'
	WriteLog(event)

	return()

# ******************************
# Monitor Grill Temperatures while alternative OEM controller is running
# ******************************

def Monitor(grill_platform, adc_device, display_device, dist_device):

	event = 'Monitor Mode started.'
	WriteLog(event)

	# Get ON/OFF Switch state and set as last state
	last = grill_platform.GetInputStatus()

	grill_platform.AugerOff()
	grill_platform.IgniterOff()
	grill_platform.FanOff()
	grill_platform.PowerOff()

	# Setup Cycle Parameters
	settings = ReadSettings()
	control = ReadControl()
	pelletdb = ReadPelletDB()

	# Initialize all temperature objects
	AvgGT = TempQueue(units=settings['globals']['units'])
	AvgP1 = TempQueue(units=settings['globals']['units'])
	AvgP2 = TempQueue(units=settings['globals']['units'])

	# Check pellets level notification upon starting cycle
	CheckNotifyPellets(control, settings, pelletdb)

	# Collect Initial Temperature Information
	# Get Probe Types From Settings
	grill0type = settings['probe_types']['grill0type']
	probe1type = settings['probe_types']['probe1type']
	probe2type = settings['probe_types']['probe2type']

	adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

	adc_data = {}
	adc_data = adc_device.ReadAllPorts()

	AvgGT.enqueue(adc_data['GrillTemp'])
	AvgP1.enqueue(adc_data['Probe1Temp'])
	AvgP2.enqueue(adc_data['Probe2Temp'])

	now = time.time()

	# Set time since toggle for temperature
	temptoggletime = now

	# Set time since toggle for display
	displaytoggletime = now 

	# Set time since toggle for hopper check
	hoppertoggletime = now 

	# Set time since last control check
	controlchecktime = now

	# Set time since last pellet level check
	pelletschecktime = now

	status = 'Active'

	while(status == 'Active'):
		now = time.time()

		# Check for update in control status every 0.5 seconds 
		if (now - controlchecktime > 0.5):
			control = ReadControl()
			controlchecktime = now

		# Check for pellet level notifications every 20 minutes
		if (now - pelletschecktime > 1200):
			CheckNotifyPellets(control, settings, pelletdb)
			pelletschecktime = now

		# Check for update in control status
		if (control['updated'] == True):
			status = 'Inactive'
			break

		# Check for update in ON/OFF Switch
		if (last != grill_platform.GetInputStatus()):
			last = grill_platform.GetInputStatus()
			if(last == 1):
				status = 'Inactive'
				event = 'Switch set to off, going to Stop mode.'
				WriteLog(event)
				#control = ReadControl()  # Read Modify Write
				control['updated'] = True # Change mode
				control['mode'] == 'Stop'
				control['status'] == 'active'
				WriteControl(control)
				break

		# Check hopper level when requested or every 300 seconds 
		if (control['hopper_check'] == True) or (now - hoppertoggletime > 300):
			pelletdb = ReadPelletDB()
			# Get current hopper level and save it to the current pellet information
			pelletdb['current']['hopper_level'] = dist_device.GetLevel()
			WritePelletDB(pelletdb)
			hoppertoggletime = now
			if(control['hopper_check'] == True):
				#control = ReadControl()  # Read Modify Write
				control['hopper_check'] = False
				WriteControl(control)
			if(settings['globals']['debug_mode'] == True):
				event = "* Hopper Level Checked @ " + str(pelletdb['current']['hopper_level']) + "%"
				print(event)
				WriteLog(event)

		# Grab current probe profiles if they have changed since the last loop. 
		if (control['probe_profile_update'] == True):
			settings = ReadSettings()
			#control = ReadControl()  # Read Modify Write
			control['probe_profile_update'] = False
			WriteControl(control)
			# Get new probe profiles
			grill0type = settings['probe_types']['grill0type']
			probe1type = settings['probe_types']['probe1type']
			probe2type = settings['probe_types']['probe2type']
			# Add new probe profiles to ADC Object
			adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

		adc_data = {}
		adc_data = adc_device.ReadAllPorts()

		# Test temperature data returned for errors (+/- 20% Temp Variance), and average the data since last reading
		AvgGT.enqueue(adc_data['GrillTemp'])
		AvgP1.enqueue(adc_data['Probe1Temp'])
		AvgP2.enqueue(adc_data['Probe2Temp'])

		in_data = {}
		in_data['GrillTemp'] = AvgGT.average()
		in_data['GrillSetPoint'] = control['setpoints']['grill']
		in_data['Probe1Temp'] = AvgP1.average()
		in_data['Probe1SetPoint'] = control['setpoints']['probe1']
		in_data['Probe2Temp'] = AvgP2.average()
		in_data['Probe2SetPoint'] = control['setpoints']['probe2']
		in_data['GrillTr'] = adc_data['GrillTr']  # For Temp Resistance Tuning
		in_data['Probe1Tr'] = adc_data['Probe1Tr']  # For Temp Resistance Tuning
		in_data['Probe2Tr'] = adc_data['Probe2Tr']  # For Temp Resistance Tuning

		# Check to see if there are any pending notifications (i.e. Timer / Temperature Settings)
		control = CheckNotify(in_data, control, settings, pelletdb)

		# Check for button input event
		display_device.EventDetect()

		# Update Display Device after 1 second has passed 
		if(now - displaytoggletime > 1):
			status_data = GetStatus(grill_platform, control, settings, pelletdb)
			display_device.DisplayStatus(in_data, status_data)
			displaytoggletime = now 

		# Write History after 3 seconds has passed
		if (now - temptoggletime > 3):
			temptoggletime = now 
			WriteHistory(in_data, tuning_mode=control['tuning_mode'])

		# Safety Control Section
		if (AvgGT.average() > settings['safety']['maxtemp']):
			status = 'Inactive'
			event = 'ERROR: Grill exceed maximum temperature limit of ' + str(settings['safety']['maxtemp']) + settings['globals']['units'] + '! Shutting down.'
			WriteLog(event)
			display_device.DisplayText('ERROR')
			#control = ReadControl()  # Read Modify Write
			control['mode'] = 'Error'
			control['updated'] = True
			control['status'] = 'monitor'
			WriteControl(control)
			SendNotifications("Grill_Error_01", control, settings, pelletdb)

		time.sleep(0.05)

	event = 'Monitor mode ended.'
	WriteLog(event)

	return()

# ******************************
# Manual Mode Control
# ******************************

def Manual_Mode(grill_platform, adc_device, display_device, dist_device):
	# Setup Cycle Parameters
	settings = ReadSettings()
	control = ReadControl()
	pelletdb = ReadPelletDB()

	event = 'Manual Mode started.'
	WriteLog(event)

	# Get ON/OFF Switch state and set as last state
	last = grill_platform.GetInputStatus()

	grill_platform.AugerOff()
	grill_platform.IgniterOff()
	grill_platform.FanOff()
	grill_platform.PowerOff()

	# Initialize all temperature variables
	AvgGT = TempQueue(units=settings['globals']['units'])
	AvgP1 = TempQueue(units=settings['globals']['units'])
	AvgP2 = TempQueue(units=settings['globals']['units'])

	# Collect Initial Temperature Information
	# Get Probe Types From Settings
	grill0type = settings['probe_types']['grill0type']
	probe1type = settings['probe_types']['probe1type']
	probe2type = settings['probe_types']['probe2type']

	adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

	adc_data = {}
	adc_data = adc_device.ReadAllPorts()

	AvgGT.enqueue(adc_data['GrillTemp'])
	AvgP1.enqueue(adc_data['Probe1Temp'])
	AvgP2.enqueue(adc_data['Probe2Temp'])

	now = time.time()

	# Set time since toggle for temperature
	temptoggletime = now

	# Set time since toggle for display
	displaytoggletime = now 

	# Set time since last control check
	controlchecktime = now 

	status = 'Active'

	while(status == 'Active'):
		now = time.time()
		# Check for update in control status every 0.5 seconds 
		if (now - controlchecktime > 0.5):
			control = ReadControl()
			controlchecktime = now 

		# Check for update in control status
		if (control['updated'] == True):
			status = 'Inactive'
			break

		# Check for update in ON/OFF Switch
		if (last != grill_platform.GetInputStatus()):
			last = grill_platform.GetInputStatus()
			if(last == 1):
				status = 'Inactive'
				event = 'Switch set to off, going to Stop mode.'
				WriteLog(event)
				#control = ReadControl()  # Read Modify Write
				control['updated'] = True # Change mode
				control['mode'] == 'Stop'
				control['status'] == 'active'
				WriteControl(control)
				break

		# Get current grill output status
		current_output_status = grill_platform.GetOutputStatus()

		if(control['manual']['change'] == True):
			if(control['manual']['fan'] == True) and (current_output_status['fan'] == FANOFF):
				grill_platform.FanOn()
			elif(control['manual']['fan'] == False) and (current_output_status['fan'] == FANON):
				grill_platform.FanOff()

			if(control['manual']['auger'] == True) and (current_output_status['auger'] == AUGEROFF):
				grill_platform.AugerOn()
			elif(control['manual']['auger'] == False) and (current_output_status['auger'] == AUGERON):
				grill_platform.AugerOff()

			if(control['manual']['igniter'] == True) and (current_output_status['igniter'] == IGNITEROFF):
				grill_platform.IgniterOn()
			elif(control['manual']['igniter'] == False) and (current_output_status['igniter'] == IGNITERON):
				grill_platform.IgniterOff()

			if(control['manual']['power'] == True) and (current_output_status['power'] == POWEROFF):
				grill_platform.PowerOn()
			elif(control['manual']['power'] == False) and (current_output_status['power'] == POWERON):
				grill_platform.PowerOff()

			#control = ReadControl()  # Read Modify Write
			control['manual']['change'] = False
			WriteControl(control)

		# Grab current probe profiles if they have changed since the last loop. 
		if (control['probe_profile_update'] == True):
			settings = ReadSettings()
			control['probe_profile_update'] = False
			WriteControl(control)
			# Get new probe profiles
			grill0type = settings['probe_types']['grill0type']
			probe1type = settings['probe_types']['probe1type']
			probe2type = settings['probe_types']['probe2type']
			# Add new probe profiles to ADC Object
			adc_device.SetProfiles(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type])

		adc_data = {}
		adc_data = adc_device.ReadAllPorts()

		# Test temperature data returned for errors (+/- 20% Temp Variance), and average the data since last reading
		AvgGT.enqueue(adc_data['GrillTemp'])
		AvgP1.enqueue(adc_data['Probe1Temp'])
		AvgP2.enqueue(adc_data['Probe2Temp'])

		in_data = {}
		in_data['GrillTemp'] = AvgGT.average()
		in_data['GrillSetPoint'] = control['setpoints']['grill']
		in_data['Probe1Temp'] = AvgP1.average()
		in_data['Probe1SetPoint'] = control['setpoints']['probe1']
		in_data['Probe2Temp'] = AvgP2.average()
		in_data['Probe2SetPoint'] = control['setpoints']['probe2']
		in_data['GrillTr'] = adc_data['GrillTr']  # For Temp Resistance Tuning
		in_data['Probe1Tr'] = adc_data['Probe1Tr']  # For Temp Resistance Tuning
		in_data['Probe2Tr'] = adc_data['Probe2Tr']  # For Temp Resistance Tuning

		# Update Display Device after 1 second has passed 
		if(now - displaytoggletime > 1):
			status_data = GetStatus(grill_platform, control, settings, pelletdb)
			display_device.DisplayStatus(in_data, status_data)
			displaytoggletime = now 

		control = CheckNotify(in_data, control, settings, pelletdb)

		# Write History after 3 seconds has passed
		if (now - temptoggletime > 3):
			temptoggletime = time.time()
			WriteHistory(in_data, tuning_mode=control['tuning_mode'])

		time.sleep(0.2)

	# Clean-up and Exit
	grill_platform.AugerOff()
	grill_platform.IgniterOff()
	grill_platform.FanOff()
	grill_platform.PowerOff()

	event = 'Manual mode ended.'
	WriteLog(event)

	return()

# ******************************
# Recipe Mode Control
# ******************************

def Recipe_Mode(grill_platform, adc_device, display_device, dist_device):
	settings = ReadSettings()
	event = 'Recipe Mode started.'
	WriteLog(event)

	# Find Recipe
	control = ReadControl()
	recipename = control['recipe']
	cookbook = ReadRecipes()

	if(recipename in cookbook):
		recipe = cookbook[recipename]
		if(settings['globals']['debug_mode'] == True):
			event = '* Found recipe: ' + recipename
			print(event)
			WriteLog(event)

		# Execute Recipe Steps
		#for(item in recipe['steps'].sort()):
		#	if('grill_temp' in recipe['steps'][item]):
		#		temp = recipe['steps'][item]['grill_temp']
		#		notify = recipe['steps'][item]['notify']
		#		desc = recipe['steps'][item]['description']
		#		event = item + ': Setting Grill Temp: ' + str(temp) + 'F, Notify: ' + str(notify) + ', Desc: ' + desc
		#		WriteLog(event)

			# Read Control, Check for updates, break
			# Read Switch, Check if changed to off, break
	else:
		# Error Recipe Not Found
		event = 'Recipe not found'



	event = 'Recipe mode ended.'
	WriteLog(event)

	return()

# ******************************
# Send Pushover Notifications
# ******************************

def SendPushoverNotification(notifyevent, control, settings, pelletdb):
	now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

	unit = settings['globals']['units']

	if "Grill_Temp_Achieved" in notifyevent:
		notifymessage = "The Grill setpoint of " + str(control['setpoints']['grill']) + unit + " was achieved at " + str(now)
		subjectmessage = "Grill at " + str(control['setpoints']['grill']) + unit + " at " + str(now)
	elif "Probe1_Temp_Achieved" in notifyevent:
		notifymessage = "The Probe 1 setpoint of " + str(control['setpoints']['probe1']) + unit + " was achieved at " + str(now)
		subjectmessage = "Probe 1 at " + str(control['setpoints']['probe1']) + unit + " at " + str(now)
	elif "Probe2_Temp_Achieved" in notifyevent:
		notifymessage = "The Probe 2 setpoint of " + str(control['setpoints']['probe2']) + unit + " was achieved at " + str(now)
		subjectmessage = "Probe 2 at " + str(control['setpoints']['probe2']) + unit + " at " + str(now)
	elif "Timer_Expired" in notifyevent:
		notifymessage = "Your grill timer has expired, time to check your cook!"
		subjectmessage = "Grill Timer Complete: " + str(now)
	elif "Pellet_Level_Low" in notifyevent:
		notifymessage = "Your pellet level is currently at " + str(pelletdb['current']['hopper_level']) + "%"
		subjectmessage = "Low Pellet Level"
	elif "Grill_Error_00" in notifyevent:
		notifymessage = "Your grill has experienced an error and will shutdown now. " + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Error_01" in notifyevent:
		notifymessage = "Grill exceed maximum temperature limit of " + str(settings['safety']['maxtemp']) + unit + "! Shutting down." + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Error_02" in notifyevent:
		notifymessage = "Grill temperature dropped below minimum startup temperature of " + str(control['safety']['startuptemp']) + unit + "! Shutting down to prevent firepot overload." + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Warning" in notifyevent:
		notifymessage = "Your grill has experienced a warning condition.  Please check the logs."  + str(now)
		subjectmessage = "Grill Warning!"
	else:
		notifymessage = "Whoops! PiFire had the following unhandled notify event: " + notifyevent + " at " + now
		subjectmessage = "PiFire: Unknown Notification at " + str(now)

	url = 'https://api.pushover.net/1/messages.json'
	for user in settings['pushover']['UserKeys'].split(','):
		try:
			r = requests.post(url, data={
				"token": settings['pushover']['APIKey'],
				"user": user.strip(),
				"message": notifymessage,
				"title": subjectmessage,
				"url": settings['pushover']['PublicURL']
			})
			if(settings['globals']['debug_mode'] == True):
				event = '* Pushover Response: ' + r.text
				print(event)
				WriteLog(event)
			WriteLog(subjectmessage + ". Pushover notification sent to: " + user.strip())

		except Exception as e:
			WriteLog("WARNING: Pushover Notification to %s failed: %s" % (user, e))
		except:
			WriteLog("WARNING: Pushover Notification to %s failed for unknown reason." % (user))

# ******************************
# Send Pushbullet Notifications
# ******************************

def SendPushBulletNotification(notifyevent, control, settings, pelletdb):
	now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')

	unit = settings['globals']['units']
	if "Grill_Temp_Achieved" in notifyevent:
		notifymessage = "The Grill setpoint of " + str(control['setpoints']['grill']) + unit + " was achieved at " + str(now)
		subjectmessage = "Grill at " + str(control['setpoints']['grill']) + unit + " at " + str(now)
	elif "Probe1_Temp_Achieved" in notifyevent:
		notifymessage = "The Probe 1 setpoint of " + str(control['setpoints']['probe1']) + unit + " was achieved at " + str(now)
		subjectmessage = "Probe 1 at " + str(control['setpoints']['probe1']) + unit + " at " + str(now)
	elif "Probe2_Temp_Achieved" in notifyevent:
		notifymessage = "The Probe 2 setpoint of " + str(control['setpoints']['probe2']) + unit + " was achieved at " + str(now)
		subjectmessage = "Probe 2 at " + str(control['setpoints']['probe2']) + unit + " at " + str(now)
	elif "Timer_Expired" in notifyevent:
		notifymessage = "Your grill timer has expired, time to check your cook!"
		subjectmessage = "Grill Timer Complete: " + str(now)
	elif "Pellet_Level_Low" in notifyevent:
		notifymessage = "Your pellet level is currently at " + str(pelletdb['current']['hopper_level']) + "%"
		subjectmessage = "Low Pellet Level"
	elif "Grill_Error_00" in notifyevent:
		notifymessage = "Your grill has experienced an error and will shutdown now. " + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Error_01" in notifyevent:
		notifymessage = "Grill exceed maximum temperature limit of " + str(settings['safety']['maxtemp']) + unit + "! Shutting down." + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Error_02" in notifyevent:
		notifymessage = "Grill temperature dropped below minimum startup temperature of " + str(control['safety']['startuptemp']) + unit + "! Shutting down to prevent firepot overload." + str(now)
		subjectmessage = "Grill Error!"
	elif "Grill_Warning" in notifyevent:
		notifymessage = "Your grill has experienced a warning condition.  Please check the logs."  + str(now)
		subjectmessage = "Grill Warning!"
	else:
		notifymessage = "Whoops! PiFire had the following unhandled notify event: " + notifyevent + " at " + now
		subjectmessage = "PiFire: Unknown Notification at " + str(now)

	api_key = settings['pushbullet']['APIKey']
	pushbullet_link = settings['pushbullet']['PublicURL']

	try:
		pb = Pushbullet(api_key)
		pb.push_link(subjectmessage, pushbullet_link, notifymessage)
		WriteLog("Pushbullet Notification Success: " + subjectmessage)
	except:
		WriteLog("Pushbullet Notification Failed: " + subjectmessage)

# ******************************
# Send Firebase Notifications
# ******************************

def SendFirebaseNotification(notifyevent, control, settings, pelletdb):
	date = datetime.datetime.now()
	now = date.strftime('%m-%d %H:%M')
	time = date.strftime('%H:%M')
	day = date.strftime('%m/%d')

	unit = settings['globals']['units']

	if "Grill_Temp_Achieved" in notifyevent:
		titlemessage = "Grill Setpoint Achieved"
		bodymessage = "Grill setpoint of " + str(control['setpoints']['grill']) + unit + " achieved at " + str(time) + " on " + str(day)
		sound = 'temp_achieved'
		channel = 'pifire_temp_alerts'
	elif "Probe1_Temp_Achieved" in notifyevent:
		titlemessage = "Probe 1 Setpoint Achieved"
		bodymessage = "Probe 1 setpoint of " + str(control['setpoints']['probe1']) + unit + " achieved at " + str(time) + " on " + str(day)
		sound = 'temp_achieved'
		channel = 'pifire_temp_alerts'
	elif "Probe2_Temp_Achieved" in notifyevent:
		titlemessage = "Probe 2 Setpoint Achieved"
		bodymessage = "Probe 2 setpoint of " + str(control['setpoints']['probe2']) + unit + " achieved at " + str(time) + " on " + str(day)
		sound = 'temp_achieved'
		channel = 'pifire_temp_alerts'
	elif "Timer_Expired" in notifyevent:
		titlemessage = "Grill Timer Complete"
		bodymessage = "Your grill timer has expired, time to check your cook!"
		sound = 'timer_alarm'
		channel = 'pifire_timer_alerts'
	elif "Pellet_Level_Low" in notifyevent:
		titlemessage = "Low Pellet Level"
		bodymessage = "Your pellet level is currently at " + str(pelletdb['current']['hopper_level']) + "%"
		sound = 'pellet_alarm'
		channel = 'pifire_pellet_alerts'
	elif "Grill_Error_00" in notifyevent:
		titlemessage = "Grill Error!"
		bodymessage = "Your grill has experienced an error and will shutdown now. " + str(now)
		sound = 'grill_error'
		channel = 'pifire_error_alerts'
	elif "Grill_Error_01" in notifyevent:
		titlemessage = "Grill Error!"
		bodymessage = "Grill exceded maximum temperature limit of " + str(settings['safety']['maxtemp']) + "F! Shutting down." + str(now)
		sound = 'grill_error'
		channel = 'pifire_error_alerts'
	elif "Grill_Error_02" in notifyevent:
		titlemessage = "Grill Error!"
		bodymessage = "Grill temperature dropped below minimum startup temperature of " + str(control['safety']['startuptemp']) + unit + "! Shutting down to prevent firepot overload." + str(now)
		sound = 'grill_error'
		channel = 'pifire_error_alerts'
	elif "Grill_Warning" in notifyevent:
		titlemessage = "Grill Warning!"
		bodymessage = "Your grill has experienced a warning condition. Please check the logs."  + str(now)
		sound = 'grill_error'
		channel = 'pifire_error_alerts'
	else:
		titlemessage = "PiFire: Unknown Notification issue"
		bodymessage = "Whoops! PiFire had the following unhandled notify event: " + notifyevent + " at " + str(now)
		sound = 'default'
		channel = 'default'

	server_url = settings['firebase']['ServerUrl']
	device_uuid = settings['firebase']['uuid']

	headers = {
		'Content-Type': 'application/json'
	  }

	body = {
		'uuid': device_uuid,
		'title': titlemessage,
		'message': bodymessage,
		'sound': sound,
		'channel': channel,
		'priority': 'high',
		'ttl': 3600
	}

	response = requests.post(server_url, headers=headers, data=json.dumps(body))

	if(response.status_code == 200):
		WriteLog("Firebase Notification Success: " + titlemessage)
	else:
		WriteLog("FirebaseNotification Failed: " + titlemessage)

	if(settings['modules']['grillplat'] == 'prototype'):
		print(response.status_code)
		print(response.json())

# ******************************
# Send IFTTT Notifications
# ******************************

def SendIFTTTNotification(notifyevent, control, settings, pelletdb):

	if "Grill_Temp_Achieved" in notifyevent:
		query_args = { "value1" : str(control['setpoints']['grill']) }
	elif "Probe1_Temp_Achieved" in notifyevent:
		query_args = { "value1" : str(control['setpoints']['probe1']) }
	elif "Probe2_Temp_Achieved" in notifyevent:
		query_args = { "value1" : str(control['setpoints']['probe2']) }
	elif "Timer_Expired" in notifyevent:
		query_args = { "value1" : 'Your grill timer has expired.' }
	elif "Pellet_Level_Low" in notifyevent:
		query_args = { "value1" : 'Pellet level currently at ' + str(pelletdb['current']['hopper_level']) + '%' }
	elif "Grill_Error_00" in notifyevent:
		query_args = { "value1" : 'Your grill has experienced an error and will shutdown now. ' }
	elif "Grill_Error_01" in notifyevent:
		query_args = { "value1" : str(settings['safety']['maxtemp']) }
	elif "Grill_Error_02" in notifyevent:
		query_args = { "value1" : str(control['safety']['startuptemp']) }
	elif "Grill_Warning" in notifyevent:
		query_args = { "value1" : 'General Warning.' }
	else:
		WriteLog("IFTTT Notification Failed: Unhandled notify event.")
		return()

	key = settings['ifttt']['APIKey']
	url = 'https://maker.ifttt.com/trigger/' + notifyevent + '/with/key/' + key

	try:
		r = requests.post(url, data=query_args)
		WriteLog("IFTTT Notification Success: " + r.text)
	except:
		WriteLog("IFTTT Notification Failed: " + r.text)

# ******************************
# Send Notifications
# ******************************

def SendNotifications(notifyevent, control, settings, pelletdb):

	if(settings['ifttt']['APIKey'] != '' and settings['ifttt']['enabled'] == True):
		SendIFTTTNotification(notifyevent, control, settings, pelletdb)
	if(settings['pushbullet']['APIKey'] != '' and settings['pushbullet']['enabled'] == True):
		SendPushBulletNotification(notifyevent, control, settings, pelletdb)
	if(settings['pushover']['APIKey'] != '' and settings['pushover']['UserKeys'] != '' and settings['pushover']['enabled'] == True):
		SendPushoverNotification(notifyevent, control, settings, pelletdb)
	if(settings['firebase']['ServerUrl'] != '' and settings['firebase']['enabled'] == True):
		SendFirebaseNotification(notifyevent, control, settings, pelletdb)

# ******************************
# Check for any pending notifications
# ******************************

def CheckNotify(in_data, control, settings, pelletdb):

	if (control['notify_req']['grill'] == True):
		if (in_data['GrillTemp'] >= control['setpoints']['grill']):
			#control = ReadControl()  # Read Modify Write
			control['notify_req']['grill'] = False
			WriteControl(control)
			SendNotifications("Grill_Temp_Achieved", control, settings, pelletdb)
			notify_event = "Grill Temp of " + str(control['setpoints']['grill']) + settings['globals']['units'] + " Achieved"
			WriteLog(notify_event)

	if (control['notify_req']['probe1']):
		if (in_data['Probe1Temp'] >= control['setpoints']['probe1']):
			SendNotifications("Probe1_Temp_Achieved", control, settings, pelletdb)
			#control = ReadControl()  # Read Modify Write
			control['notify_req']['probe1'] = False
			if(control['notify_data']['p1_shutdown'] == True)and((control['mode'] == 'Smoke')or(control['mode'] == 'Hold')or(control['mode'] == 'Startup')or(control['mode'] == 'Reignite')):
				control['mode'] = 'Shutdown'
				control['updated'] = True
				control['notify_data']['p1_shutdown'] = False
			WriteControl(control)
			notify_event = "Probe 1 Temp of " + str(control['setpoints']['probe1']) + settings['globals']['units'] + " Achieved"
			WriteLog(notify_event)

	if (control['notify_req']['probe2']):
		if (in_data['Probe2Temp'] >= control['setpoints']['probe2']):
			SendNotifications("Probe2_Temp_Achieved", control, settings, pelletdb)
			#control = ReadControl()  # Read Modify Write
			control['notify_req']['probe2'] = False
			if(control['notify_data']['p2_shutdown'] == True)and((control['mode'] == 'Smoke')or(control['mode'] == 'Hold')or(control['mode'] == 'Startup')or(control['mode'] == 'Reignite')):
				control['mode'] = 'Shutdown'
				control['updated'] = True
				control['notify_data']['p2_shutdown'] = False
			WriteControl(control)
			notify_event = "Probe 2 Temp of " + str(control['setpoints']['probe2']) + settings['globals']['units'] + " Achieved"
			WriteLog(notify_event)

	if (control['notify_req']['timer']):
		if (time.time() >= control['timer']['end']):
			SendNotifications("Timer_Expired", control, settings, pelletdb)
			#control = ReadControl()  # Read Modify Write
			if(control['notify_data']['timer_shutdown'] == True)and((control['mode'] == 'Smoke')or(control['mode'] == 'Hold')or(control['mode'] == 'Startup')or(control['mode'] == 'Reignite')):
				control['mode'] = 'Shutdown'
				control['updated'] = True
			control['notify_req']['timer'] = False
			control['timer']['start'] = 0
			control['timer']['paused'] = 0
			control['timer']['end'] = 0
			control['notify_data']['timer_shutdown'] = False 
			WriteControl(control)

	return(control)

# ******************************
# Check for any pending pellet notifications
# ******************************

def CheckNotifyPellets(control, settings, pelletdb):

	if (settings['pelletlevel']['warning_enabled'] == True):
		if (pelletdb['current']['hopper_level'] <= settings['pelletlevel']['warning_level']):
			SendNotifications("Pellet_Level_Low", control, settings, pelletdb)

# *****************************************
# Main Program Start / Init
# *****************************************

# Init Global Variables / Classes

settings = ReadSettings()

outpins = settings['outpins']
inpins = settings['inpins']
triggerlevel = settings['globals']['triggerlevel']
buttonslevel = settings['globals']['buttonslevel']
units = settings['globals']['units']

if triggerlevel == 'LOW':
	AUGERON = 0
	AUGEROFF = 1
	FANON = 0
	FANOFF = 1
	IGNITERON = 0
	IGNITEROFF = 1
	POWERON = 0
	POWEROFF = 1
else:
	AUGERON = 1
	AUGEROFF = 0
	FANON = 1
	FANOFF = 0
	IGNITERON = 1
	IGNITEROFF = 0
	POWERON = 1
	POWEROFF = 0

# Initialize Grill Platform Object
grill_platform = GrillPlatform(outpins, inpins, triggerlevel)

# If powering on, check the on/off switch and set grill power appropriately.
last = grill_platform.GetInputStatus()

if(last == 0):
	grill_platform.PowerOn()
else:
	grill_platform.PowerOff()

# Start display device object and display splash
if(str(settings['modules']['display']).endswith('b')):	
	display_device = Display(buttonslevel=buttonslevel, units=units)
else:
	display_device = Display(units=units)

grill0type = settings['probe_types']['grill0type']
probe1type = settings['probe_types']['probe1type']
probe2type = settings['probe_types']['probe2type']

# Start ADC object and set profiles
adc_device = ReadADC(settings['probe_settings']['probe_profiles'][grill0type], settings['probe_settings']['probe_profiles'][probe1type], settings['probe_settings']['probe_profiles'][probe2type], units=settings['globals']['units'])
print('Sleeping...')
#time.sleep(20)
ts = time.time()
print(f'Starting Read: {ts}')
values = adc_device.FastRead(samples=20,pga=4096,sps=8)
ts = time.time()
print(f'Finished Read: {ts}')
for x in values:
	print(f'{x}')
print('Done.')

exit()
