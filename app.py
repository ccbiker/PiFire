#!/usr/bin/env python3

# *****************************************
# PiFire Web UI (Flask App)
# *****************************************
#
# Description: This script will start at boot, and start up the web user
#  interface.
#
# This script runs as a separate process from the control program
# implementation which handles interfacing and running I2C devices & GPIOs.
#
# *****************************************

from flask import Flask, request, abort, render_template, make_response, send_file, jsonify, redirect
from flask_socketio import SocketIO
from flask_qrcode import QRcode
from werkzeug.utils import secure_filename
from collections.abc import Mapping
import threading
import zipfile
import pathlib
from threading import Thread
from datetime import datetime
from common import generate_uuid, _epoch_to_time
from updater import *  # Library for doing project updates from GitHub
from file_common import fixup_assets, read_json_file_data, update_json_file_data, remove_assets
from file_cookfile import read_cookfile, upgrade_cookfile
from file_media import add_asset, set_thumbnail, unpack_thumb

BACKUP_PATH = './backups/'  # Path to backups of settings.json, pelletdb.json
UPLOAD_FOLDER = BACKUP_PATH  # Point uploads to the backup path
HISTORY_FOLDER = './history/'  # Path to historical cook files
ALLOWED_EXTENSIONS = {'json', 'pifire', 'jpg', 'jpeg', 'png', 'gif', 'bmp'}
server_status = 'available'

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")
QRcode(app)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['HISTORY_FOLDER'] = HISTORY_FOLDER

@app.route('/')
def index():
	global settings
	
	if settings['globals']['first_time_setup']:
		return redirect('/wizard/welcome')
	else: 
		return redirect('/dash')

@app.route('/dash')
def dash():
	global settings
	control = read_control()
	errors = read_errors()

	dash_template = 'dash_default.html'
	for dash in settings['dashboard']['dashboards']:
		if dash['name'] == settings['dashboard']['current']:
			dash_template = dash['html_name']
			break

	return render_template(dash_template,
						   set_points=control['setpoints'],
						   notify_req=control['notify_req'],
						   probes_enabled=settings['probe_settings']['probes_enabled'],
						   control=control,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'],
						   units=settings['globals']['units'],
						   dc_fan=settings['globals']['dc_fan'],
						   errors=errors)

@app.route('/dashdata')
def dash_data():
	global settings
	control = read_control()

	probes_enabled = settings['probe_settings']['probes_enabled']
	cur_probe_temps = read_current()

	return jsonify({ 'cur_probe_temps' : cur_probe_temps, 'probes_enabled' : probes_enabled,
					 'current_mode' : control['mode'], 'set_points' : control['setpoints'],
					 'notify_req' : control['notify_req'], 'splus' : control['s_plus'],
					 'splus_default' : settings['smoke_plus']['enabled'],
					 'pwm_control' : control['pwm_control'],
					 'probe_titles' :  control['probe_titles']})

@app.route('/hopperlevel')
def hopper_level():
	pelletdb = read_pellet_db()
	cur_pellets_string = pelletdb['archive'][pelletdb['current']['pelletid']]['brand'] + ' ' + \
						 pelletdb['archive'][pelletdb['current']['pelletid']]['wood']
	return jsonify({ 'hopper_level' : pelletdb['current']['hopper_level'], 'cur_pellets' : cur_pellets_string })

@app.route('/timer', methods=['POST','GET'])
def timer():
	global settings 
	control = read_control()

	if request.method == "GET":
		return jsonify({ 'start' : control['timer']['start'], 'paused' : control['timer']['paused'],
						 'end' : control['timer']['end'], 'shutdown': control['timer']['shutdown']})
	elif request.method == "POST": 
		if 'input' in request.form:
			if 'timer_start' == request.form['input']: 
				control['notify_req']['timer'] = True
				# If starting new timer
				if control['timer']['paused'] == 0:
					now = time.time()
					control['timer']['start'] = now
					if 'hoursInputRange' in request.form and 'minsInputRange' in request.form:
						seconds = int(request.form['hoursInputRange']) * 60 * 60
						seconds = seconds + int(request.form['minsInputRange']) * 60
						control['timer']['end'] = now + seconds
					else:
						control['timer']['end'] = now + 60
					if 'shutdownTimer' in request.form:
						if request.form['shutdownTimer'] == 'true':
							control['notify_data']['timer_shutdown'] = True
						else: 
							control['notify_data']['timer_shutdown'] = False
					if 'keepWarmTimer' in request.form:
						if request.form['keepWarmTimer'] == 'true':
							control['notify_data']['timer_keep_warm'] = True
						else:
							control['notify_data']['timer_keep_warm'] = False
					write_log('Timer started.  Ends at: ' + _epoch_to_time(control['timer']['end']))
					write_control(control)
				else:	# If Timer was paused, restart with new end time.
					now = time.time()
					control['timer']['end'] = (control['timer']['end'] - control['timer']['paused']) + now
					control['timer']['paused'] = 0
					write_log('Timer unpaused.  Ends at: ' + _epoch_to_time(control['timer']['end']))
					write_control(control)
			elif 'timer_pause' == request.form['input']:
				if control['timer']['start'] != 0:
					control['notify_req']['timer'] = False
					now = time.time()
					control['timer']['paused'] = now
					write_log('Timer paused.')
					write_control(control)
				else:
					control['notify_req']['timer'] = False
					control['timer']['start'] = 0
					control['timer']['end'] = 0
					control['timer']['paused'] = 0
					control['notify_data']['timer_shutdown'] = False
					control['notify_data']['timer_keep_warm'] = False
					write_log('Timer cleared.')
					write_control(control)
			elif 'timer_stop' == request.form['input']:
				control['notify_req']['timer'] = False
				control['timer']['start'] = 0
				control['timer']['end'] = 0
				control['timer']['paused'] = 0
				control['notify_data']['timer_shutdown'] = False
				control['notify_data']['timer_keep_warm'] = False
				write_log('Timer stopped.')
				write_control(control)
		return jsonify({'result':'success'})

@app.route('/history/<action>', methods=['POST','GET'])
@app.route('/history', methods=['POST','GET'])
def history_page(action=None):
	global settings
	control = read_control()
	errors = []

	if request.method == 'POST':
		response = request.form
		if(action == 'cookfile'):
			if('delcookfile' in response):
				filename = './history/' + response["delcookfile"]
				os.remove(filename)
				return redirect('/history')
			if('opencookfile' in response):
				cookfilename = HISTORY_FOLDER + response['opencookfile']
				cookfilestruct, status = read_cookfile(cookfilename)
				if(status == 'OK'):
					events = cookfilestruct['events']
					event_totals = _prepare_event_totals(events)
					comments = cookfilestruct['comments']
					for comment in comments:
						comment['text'] = comment['text'].replace('\n', '<br>')
					metadata = cookfilestruct['metadata']
					metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
					metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
					labels = cookfilestruct['graph_labels']
					assets = cookfilestruct['assets']
					filenameonly = response['opencookfile']
					return render_template('cookfile.html', settings=settings, cookfilename=cookfilename, filenameonly=filenameonly, events=events, event_totals=event_totals, comments=comments, metadata=metadata, labels=labels, assets=assets, errors=errors, page_theme=settings['globals']['page_theme'], grill_name=settings['globals']['grill_name'])
				else:
					errors.append(status)
					if 'version' in status:
						errortype = 'version'
					elif 'asset' in status: 
						errortype = 'asset'
					else: 
						errortype = 'other'
					return render_template('cferror.html', settings=settings, cookfilename=cookfilename, errortype=errortype, errors=errors, page_theme=settings['globals']['page_theme'], grill_name=settings['globals']['grill_name'])
			if('dlcookfile' in response):
				filename = './history/' + response['dlcookfile']
				return send_file(filename, as_attachment=True, max_age=0)

		if(action == 'setmins'):
			if('minutes' in response):
				if(response['minutes'] != ''):
					num_items = int(response['minutes']) * 20
					settings['history_page']['minutes'] = int(response['minutes'])
					write_settings(settings)

		elif action == 'clear':
			if 'clearhistory' in response:
				if response['clearhistory'] == 'true':
					write_log('Clearing History Log.')
					read_history(0, flushhistory=True)

	elif (request.method == 'GET') and (action == 'export'):
		exportfilename = _prepare_graph_csv()
		return send_file(exportfilename, as_attachment=True, max_age=0)

	num_items = settings['history_page']['minutes'] * 20
	probes_enabled = settings['probe_settings']['probes_enabled']

	data_blob = _prepare_data(num_items, True, settings['history_page']['datapoints'])

	auto_refresh = settings['history_page']['autorefresh']

	# Calculate Displayed Start Time
	displayed_starttime = time.time() - (settings['history_page']['minutes'] * 60)
	annotations = _prepare_annotations(displayed_starttime)

	return render_template('history.html',
						   control=control,
						   grill_temp_list=data_blob['grill_temp_list'],
						   grill_settemp_list=data_blob['grill_settemp_list'],
						   probe1_temp_list=data_blob['probe1_temp_list'],
						   probe1_settemp_list=data_blob['probe1_settemp_list'],
						   probe2_temp_list=data_blob['probe2_temp_list'],
						   probe2_settemp_list=data_blob['probe2_settemp_list'],
						   label_time_list=data_blob['label_time_list'],
						   probes_enabled=probes_enabled,
						   num_mins=settings['history_page']['minutes'],
						   num_datapoints=settings['history_page']['datapoints'],
						   autorefresh=auto_refresh,
						   annotations=annotations,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])

@app.route('/historyupdate/<action>', methods=['POST','GET'])    
@app.route('/historyupdate')
def history_update(action=None):
	global settings

	if action == 'stream':
		# GET - Read current temperatures and set points for history streaming 
		control = read_control()
		if control['mode'] == 'Stop':
			set_temps = [0,0,0]
			cur_probe_temps = [0,0,0]
		else:
			set_temps = control['setpoints']
			set_temps[0] = control['setpoints']['grill']
			set_temps[1] = control['setpoints']['probe1']
			set_temps[2] = control['setpoints']['probe2']
			cur_probe_temps = read_current()

		# Calculate Displayed Start Time
		displayed_starttime = time.time() - (settings['history_page']['minutes'] * 60)
		annotations = _prepare_annotations(displayed_starttime)

		json_data = {
			'probe0_temp' : int(float(cur_probe_temps[0])), 
			'probe0_settemp' : set_temps[0], 
			'probe1_temp' : int(float(cur_probe_temps[1])), 
			'probe1_settemp' : set_temps[1], 
			'probe2_temp' : int(float(cur_probe_temps[2])), 
			'probe2_settemp' : set_temps[2],
			'annotations' : annotations,
			'mode' : control['mode']
		}
		return jsonify(json_data)

	elif action == 'refresh':
		# POST - Get number of minutes into the history to refresh the history chart
		control = read_control()
		request_json = request.json
		if 'num_mins' in request_json:
			num_items = int(request_json['num_mins']) * 20  # Calculate number of items requested
			settings['history_page']['minutes'] = int(request_json['num_mins'])
			write_settings(settings)
			data_blob = _prepare_data(num_items, True, settings['history_page']['datapoints'])

			# Calculate Displayed Start Time
			displayed_starttime = time.time() - (settings['history_page']['minutes'] * 60)
			annotations = _prepare_annotations(displayed_starttime)

			json_data = {
				'grill_temp_list' : data_blob['grill_temp_list'],
				'grill_settemp_list' : data_blob['grill_settemp_list'],
				'probe1_temp_list' : data_blob['probe1_temp_list'],
				'probe1_settemp_list' : data_blob['probe1_settemp_list'],
				'probe2_temp_list' : data_blob['probe2_temp_list'],
				'probe2_settemp_list' : data_blob['probe2_settemp_list'],
				'label_time_list' : data_blob['label_time_list'], 
				'annotations' : annotations, 
				'mode' : control['mode']
			}
			return jsonify(json_data)
	return jsonify({'status' : 'ERROR'})

@app.route('/cookfiledata', methods=['POST', 'GET'])
def cookfiledata(action=None):
	global settings 

	errors = []
	
	if(request.method == 'POST') and ('json' in request.content_type):
		requestjson = request.json
		if('full_graph' in requestjson):
			filename = requestjson['filename']
			cookfiledata, status = read_cookfile(filename)

			if(status == 'OK'):
				annotations = _prepare_annotations(0, cookfiledata['events'])

				json_data = { 
					'GT1_label' : cookfiledata['graph_labels']['grill1_label'],
					'GSP1_label' : cookfiledata['graph_labels']['grill1_label'] + " SetPoint",
					'PT1_label' : cookfiledata['graph_labels']['probe1_label'],
					'PSP1_label' : cookfiledata['graph_labels']['probe1_label'] + " SetPoint",
					'PT2_label' : cookfiledata['graph_labels']['probe2_label'],
					'PSP2_label' : cookfiledata['graph_labels']['probe2_label'] + " SetPoint",
					'GT1_data' : cookfiledata['graph_data']['grill1_temp'], 
					'GSP1_data' : cookfiledata['graph_data']['grill1_setpoint'], 
					'PT1_data' : cookfiledata['graph_data']['probe1_temp'], 
					'PT2_data' : cookfiledata['graph_data']['probe2_temp'], 
					'PSP1_data' : cookfiledata['graph_data']['probe1_setpoint'],
					'PSP2_data' : cookfiledata['graph_data']['probe2_setpoint'],
					'time_labels' : cookfiledata['graph_data']['time_labels'],
					'annotations' : annotations
				}
				return jsonify(json_data)

		if('getcommentassets' in requestjson):
			assetlist = []
			cookfilename = requestjson['cookfilename']
			commentid = requestjson['commentid']
			comments, status = read_json_file_data(cookfilename, 'comments')
			for comment in comments:
				if comment['id'] == commentid:
					assetlist = comment['assets']
					break
			return jsonify({'result' : 'OK', 'assetlist' : assetlist})

		if('managemediacomment' in requestjson):
			# Grab list of all assets in file, build assetlist
			assetlist = []
			cookfilename = requestjson['cookfilename']
			commentid = requestjson['commentid']
			
			assets, status = read_json_file_data(cookfilename, 'assets')
			metadata, status = read_json_file_data(cookfilename, 'metadata')
			for asset in assets:
				asset_object = {
					'assetname' : asset['filename'],
					'assetid' : asset['id'],
					'selected' : False
				}
				assetlist.append(asset_object)

			# Grab list of selected assets in comment currently
			selectedassets = []
			comments, status = read_json_file_data(cookfilename, 'comments')
			for comment in comments:
				if comment['id'] == commentid:
					selectedassets = comment['assets']
					break 

			# For each item in asset list, if in comment, mark selected
			for object in assetlist:
				if object['assetname'] in selectedassets:
					object['selected'] = True 

			return jsonify({'result' : 'OK', 'assetlist' : assetlist}) 

		if('getallmedia' in requestjson):
			# Grab list of all assets in file, build assetlist
			assetlist = []
			cookfilename = requestjson['cookfilename']
			assets, status = read_json_file_data(cookfilename, 'assets')

			for asset in assets:
				asset_object = {
					'assetname' : asset['filename'],
					'assetid' : asset['id'],
				}
				assetlist.append(asset_object)

			return jsonify({'result' : 'OK', 'assetlist' : assetlist}) 

		if('navimage' in requestjson):
			direction = requestjson['navimage']
			mediafilename = requestjson['mediafilename'] 
			commentid = requestjson['commentid']
			cookfilename = requestjson['cookfilename']

			comments, status = read_json_file_data(cookfilename, 'comments')
			if status == 'OK':
				assetlist = []
				for comment in comments:
					if comment['id'] == commentid:
						assetlist = comment['assets']
						break 
				current = 0
				found = False 
				for index in range(0, len(assetlist)):
					if assetlist[index] == mediafilename:
						current = index
						found = True 
						break 
				
				if found and direction == 'next':
					if current == len(assetlist)-1:
						mediafilename = assetlist[0]
					else:
						mediafilename = assetlist[current+1]
					return jsonify({'result' : 'OK', 'mediafilename' : mediafilename})
				elif found and direction == 'prev':
					if current == 0:
						mediafilename = assetlist[-1]
					else:
						mediafilename = assetlist[current-1]
					return jsonify({'result' : 'OK', 'mediafilename' : mediafilename})

		errors.append('Something unexpected has happened.')
		return jsonify({'result' : 'ERROR', 'errors' : errors})

	if(request.method == 'POST') and ('form' in request.content_type):
		requestform = request.form 
		if('dl_cookfile' in requestform):
			# Download the full JSON Cook File Locally
			filename = requestform['dl_cookfile']
			return send_file(filename, as_attachment=True, max_age=0)

		if('dl_eventfile' in requestform):
			filename = requestform['dl_eventfile']
			cookfiledata, status = read_json_file_data(filename, 'events')
			if(status == 'OK'):
				csvfilename = _prepare_metrics_csv(cookfiledata, filename)
				return send_file(csvfilename, as_attachment=True, max_age=0)

		if('dl_graphfile' in requestform):
			# Download CSV of the Graph Data Only
			filename = requestform['dl_graphfile']
			cookfiledata, status = read_cookfile(filename)
			if(status == 'OK'):
				cookfiledata['graph_data'] = _convert_labels(cookfiledata['graph_data'])
				csvfilename = _prepare_graph_csv(cookfiledata['graph_data'], cookfiledata['graph_labels'], filename)
				return send_file(csvfilename, as_attachment=True, max_age=0)

		if('ulcookfilereq' in requestform):
			# Assume we have request.files and localfile in response
			remotefile = request.files['ulcookfile']
			
			if (remotefile.filename != ''):
				# If the user does not select a file, the browser submits an
				# empty file without a filename.
				if remotefile and _allowed_file(remotefile.filename):
					filename = secure_filename(remotefile.filename)
					remotefile.save(os.path.join(app.config['HISTORY_FOLDER'], filename))
				else:
					print('Disallowed File Upload.')
					errors.append('Disallowed File Upload.')
				return redirect('/history')

		if('thumbSelected' in requestform):
			thumbnail = requestform['thumbSelected']
			filename = requestform['filename']
			# Reload Cook File
			cookfilename = HISTORY_FOLDER + filename
			cookfilestruct, status = read_cookfile(cookfilename)
			if status=='OK':
				cookfilestruct['metadata']['thumbnail'] = thumbnail
				update_json_file_data(cookfilestruct['metadata'], HISTORY_FOLDER + filename, 'metadata')
				events = cookfilestruct['events']
				event_totals = _prepare_event_totals(events)
				comments = cookfilestruct['comments']
				for comment in comments:
					comment['text'] = comment['text'].replace('\n', '<br>')
				metadata = cookfilestruct['metadata']
				metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
				metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
				labels = cookfilestruct['graph_labels']
				assets = cookfilestruct['assets']
				
				return render_template('cookfile.html', settings=settings, \
					cookfilename=cookfilename, filenameonly=filename, \
					events=events, event_totals=event_totals, \
					comments=comments, metadata=metadata, labels=labels, \
					assets=assets, errors=errors, \
					page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])

		if('ulmediafn' in requestform) or ('ulthumbfn' in requestform):
			# Assume we have request.files and localfile in response
			if 'ulmediafn' in requestform:
				#uploadedfile = request.files['ulmedia']
				uploadedfiles = request.files.getlist('ulmedia')
				cookfilename = HISTORY_FOLDER + requestform['ulmediafn']
				filenameonly = requestform['ulmediafn']
			else: 
				uploadedfile = request.files['ulthumbnail']
				cookfilename = HISTORY_FOLDER + requestform['ulthumbfn']
				filenameonly = requestform['ulthumbfn']
				uploadedfiles = [uploadedfile]

			status = 'ERROR'
			for remotefile in uploadedfiles:
				if (remotefile.filename != ''):
					# Reload Cook File
					cookfilestruct, status = read_cookfile(cookfilename)
					parent_id = cookfilestruct['metadata']['id']
					tmp_path = f'/tmp/pifire/{parent_id}'
					if not os.path.exists(tmp_path):
						os.mkdir(tmp_path)

					if remotefile and _allowed_file(remotefile.filename):
						filename = secure_filename(remotefile.filename)
						pathfile = os.path.join(tmp_path, filename)
						remotefile.save(pathfile)
						asset_id, asset_filetype = add_asset(cookfilename, tmp_path, filename)
						if 'ulthumbfn' in requestform:
							set_thumbnail(cookfilename, f'{asset_id}.{asset_filetype}')
						#  Reload all of the data
						cookfilestruct, status = read_cookfile(cookfilename)
					else:
						errors.append('Disallowed File Upload.')

			if(status == 'OK'):
				events = cookfilestruct['events']
				event_totals = _prepare_event_totals(events)
				comments = cookfilestruct['comments']
				for comment in comments:
					comment['text'] = comment['text'].replace('\n', '<br>')
				metadata = cookfilestruct['metadata']
				metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
				metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
				labels = cookfilestruct['graph_labels']
				assets = cookfilestruct['assets']

				return render_template('cookfile.html', settings=settings, \
					cookfilename=cookfilename, filenameonly=filenameonly, \
					events=events, event_totals=event_totals, \
					comments=comments, metadata=metadata, labels=labels, \
					assets=assets, errors=errors, \
					page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])

		if('cookfilelist' in requestform):
			page = int(requestform['page'])
			reverse = True if requestform['reverse'] == 'true' else False
			itemsperpage = int(requestform['itemsperpage'])
			filelist = _get_cookfilelist()
			cookfilelist = []
			for filename in filelist:
				cookfilelist.append({'filename' : filename, 'title' : '', 'thumbnail' : ''})
			paginated_cookfile = _paginate_list(cookfilelist, 'filename', reverse, itemsperpage, page)
			paginated_cookfile['displaydata'] = _get_cookfilelist_details(paginated_cookfile['displaydata'])
			return render_template('_cookfile_list.html', pgntdcf = paginated_cookfile)

		if('repairCF' in requestform):
			cookfilename = requestform['repairCF']
			filenameonly = requestform['repairCF'].replace(HISTORY_FOLDER, '')
			cookfilestruct, status = upgrade_cookfile(cookfilename)
			if status != 'OK':
				errors.append(status)
				if 'version' in status:
					errortype = 'version'
				elif 'asset' in status: 
					errortype = 'asset'
				else: 
					errortype = 'other'
				errors.append('Repair Failed.')
				return render_template('cferror.html', settings=settings, \
					cookfilename=cookfilename, errortype=errortype, \
					errors=errors, page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])
			# Fix issues with assets
			cookfilestruct, status = read_cookfile(cookfilename)
			cookfilestruct, status = fixup_assets(cookfilename, cookfilestruct)
			if status != 'OK':
				errors.append(status)
				if 'version' in status:
					errortype = 'version'
				elif 'asset' in status: 
					errortype = 'asset'
				else: 
					errortype = 'other'
				errors.append('Repair Failed.')
				return render_template('cferror.html', settings=settings, \
					cookfilename=cookfilename, errortype=errortype, \
					errors=errors, page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])
			else: 
				events = cookfilestruct['events']
				event_totals = _prepare_event_totals(events)
				comments = cookfilestruct['comments']
				for comment in comments:
					comment['text'] = comment['text'].replace('\n', '<br>')
				metadata = cookfilestruct['metadata']
				metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
				metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
				labels = cookfilestruct['graph_labels']
				assets = cookfilestruct['assets']

				return render_template('cookfile.html', settings=settings, \
					cookfilename=cookfilename, filenameonly=filenameonly, \
					events=events, event_totals=event_totals, \
					comments=comments, metadata=metadata, labels=labels, \
					assets=assets, errors=errors, \
					page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])

		if('upgradeCF' in requestform):
			cookfilename = requestform['upgradeCF']
			filenameonly = requestform['upgradeCF'].replace(HISTORY_FOLDER, '')
			cookfilestruct, status = upgrade_cookfile(cookfilename)
			if status != 'OK':
				errors.append(status)
				if 'version' in status:
					errortype = 'version'
				elif 'asset' in status: 
					errortype = 'asset'
				else: 
					errortype = 'other'
				return render_template('cferror.html', settings=settings, \
					cookfilename=cookfilename, errortype=errortype, \
					errors=errors, page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])
			else: 
				events = cookfilestruct['events']
				event_totals = _prepare_event_totals(events)
				comments = cookfilestruct['comments']
				for comment in comments:
					comment['text'] = comment['text'].replace('\n', '<br>')
				metadata = cookfilestruct['metadata']
				metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
				metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
				labels = cookfilestruct['graph_labels']
				assets = cookfilestruct['assets']

				return render_template('cookfile.html', settings=settings, \
					cookfilename=cookfilename, filenameonly=filenameonly, \
					events=events, event_totals=event_totals, \
					comments=comments, metadata=metadata, labels=labels, \
					assets=assets, errors=errors, \
					page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])

		if('delmedialist' in requestform):
			cookfilename = HISTORY_FOLDER + requestform['delmedialist']
			filenameonly = requestform['delmedialist']
			assetlist = requestform['delAssetlist'].split(',') if requestform['delAssetlist'] != '' else []
			status = remove_assets(cookfilename, assetlist)
			cookfilestruct, status = read_cookfile(cookfilename)
			if status != 'OK':
				errors.append(status)
				if 'version' in status:
					errortype = 'version'
				elif 'asset' in status: 
					errortype = 'asset'
				else: 
					errortype = 'other'
				return render_template('cferror.html', settings=settings, \
					cookfilename=cookfilename, errortype=errortype, \
					errors=errors, page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])
			else: 
				events = cookfilestruct['events']
				event_totals = _prepare_event_totals(events)
				comments = cookfilestruct['comments']
				for comment in comments:
					comment['text'] = comment['text'].replace('\n', '<br>')
				metadata = cookfilestruct['metadata']
				metadata['starttime'] = _epoch_to_time(metadata['starttime'] / 1000)
				metadata['endtime'] = _epoch_to_time(metadata['endtime'] / 1000)
				labels = cookfilestruct['graph_labels']
				assets = cookfilestruct['assets']

				return render_template('cookfile.html', settings=settings, \
					cookfilename=cookfilename, filenameonly=filenameonly, \
					events=events, event_totals=event_totals, \
					comments=comments, metadata=metadata, labels=labels, \
					assets=assets, errors=errors, \
					page_theme=settings['globals']['page_theme'], \
					grill_name=settings['globals']['grill_name'])

	errors.append('Something unexpected has happened.')
	return jsonify({'result' : 'ERROR', 'errors' : errors})

@app.route('/updatecookfile', methods=['POST','GET'])
def updatecookdata(action=None):
	global settings 

	if(request.method == 'POST'):
		requestjson = request.json 
		if('comments' in requestjson):
			filename = requestjson['filename']
			cookfiledata, status = read_json_file_data(filename, 'comments')

			if('commentnew' in requestjson):
				now = datetime.datetime.now()
				comment_struct = {}
				comment_struct['text'] = requestjson['commentnew']
				comment_struct['id'] = generate_uuid()
				comment_struct['edited'] = ''
				comment_struct['date'] = now.strftime('%Y-%m-%d')
				comment_struct['time'] = now.strftime('%H:%M')
				comment_struct['assets'] = []
				cookfiledata.append(comment_struct)
				result = update_json_file_data(cookfiledata, filename, 'comments')
				if(result == 'OK'):
					return jsonify({'result' : 'OK', 'newcommentid' : comment_struct['id'], 'newcommentdt': comment_struct['date'] + ' ' + comment_struct['time']})
			if('delcomment' in requestjson):
				for item in cookfiledata:
					if item['id'] == requestjson['delcomment']:
						cookfiledata.remove(item)
						result = update_json_file_data(cookfiledata, filename, 'comments')
						if(result == 'OK'):
							return jsonify({'result' : 'OK'})
			if('editcomment' in requestjson):
				for item in cookfiledata:
					if item['id'] == requestjson['editcomment']:
						return jsonify({'result' : 'OK', 'text' : item['text']})
			if('savecomment' in requestjson):
				for item in cookfiledata:
					if item['id'] == requestjson['savecomment']:
						now = datetime.datetime.now()
						item['text'] = requestjson['text']
						item['edited'] = now.strftime('%Y-%m-%d %H:%M')
						result = update_json_file_data(cookfiledata, filename, 'comments')
						if(result == 'OK'):
							return jsonify({'result' : 'OK', 'text' : item['text'].replace('\n', '<br>'), 'edited' : item['edited'], 'datetime' : item['date'] + ' ' + item['time']})
		
		if('metadata' in requestjson):
			filename = requestjson['filename']
			cookfiledata, status = read_json_file_data(filename, 'metadata')
			if(status == 'OK'):
				if('editTitle' in requestjson):
					cookfiledata['title'] = requestjson['editTitle']
					result = update_json_file_data(cookfiledata, filename, 'metadata')
					if(result == 'OK'):
						return jsonify({'result' : 'OK'})
					else: 
						print(f'Result: {result}')
		
		if('graph_labels' in requestjson):
			filename = requestjson['filename']
			cookfiledata, status = read_json_file_data(filename, 'graph_labels')
			if(status == 'OK'):
				if('grill1_label' in requestjson):
					cookfiledata['grill1_label'] = requestjson['grill1_label']
					result = update_json_file_data(cookfiledata, filename, 'graph_labels')
					if(result == 'OK'):
						return jsonify({'result' : 'OK'})
				if('probe1_label' in requestjson):
					cookfiledata['probe1_label'] = requestjson['probe1_label']
					result = update_json_file_data(cookfiledata, filename, 'graph_labels')
					if(result == 'OK'):
						return jsonify({'result' : 'OK'})
				if('probe2_label' in requestjson):
					cookfiledata['probe2_label'] = requestjson['probe2_label']
					result = update_json_file_data(cookfiledata, filename, 'graph_labels')
					if(result == 'OK'):
						return jsonify({'result' : 'OK'})
			else:
				print(f'ERROR: {status}')

		if('media' in requestjson):
			filename = requestjson['filename']
			assetfilename = requestjson['assetfilename']
			commentid = requestjson['commentid']
			state = requestjson['state']
			comments, status = read_json_file_data(filename, 'comments')
			result = 'OK'
			for index in range(0, len(comments)):
				if comments[index]['id'] == commentid:
					if assetfilename in comments[index]['assets'] and state == 'selected':
						comments[index]['assets'].remove(assetfilename)
						result = update_json_file_data(comments, filename, 'comments')
					elif assetfilename not in comments[index]['assets'] and state == 'unselected':
						comments[index]['assets'].append(assetfilename)
						result = update_json_file_data(comments, filename, 'comments')
					break

			return jsonify({'result' : result})

	return jsonify({'result' : 'ERROR'})
	

@app.route('/tuning/<action>', methods=['POST','GET'])
@app.route('/tuning', methods=['POST','GET'])
def tuning_page(action=None):

	global settings
	control = read_control()

	if(control['mode'] == 'Stop'): 
		alert = 'Warning!  Grill must be in an active mode to perform tuning (i.e. Monitor Mode, Smoke Mode, ' \
				'Hold Mode, etc.)'
	else: 
		alert = ''

	pagectrl = {}

	pagectrl['refresh'] = 'off'
	pagectrl['selected'] = 'none'
	pagectrl['showcalc'] = 'false'
	pagectrl['low_trvalue'] = ''
	pagectrl['med_trvalue'] = ''
	pagectrl['high_trvalue'] = ''
	pagectrl['low_tempvalue'] = ''
	pagectrl['med_tempvalue'] = ''
	pagectrl['high_tempvalue'] = ''

	if request.method == 'POST':
		response = request.form
		if 'probe_select' in response:
			pagectrl['selected'] = response['probe_select']
			pagectrl['refresh'] = 'on'
			control['tuning_mode'] = True  # Enable tuning mode
			write_control(control)

			if'pause' in response:
				if response['low_trvalue'] != '':
					pagectrl['low_trvalue'] = response['low_trvalue']
				if response['med_trvalue'] != '':
					pagectrl['med_trvalue'] = response['med_trvalue']
				if response['high_trvalue'] != '':
					pagectrl['high_trvalue'] = response['high_trvalue']

				if response['low_tempvalue'] != '':
					pagectrl['low_tempvalue'] = response['low_tempvalue']
				if response['med_tempvalue'] != '':
					pagectrl['med_tempvalue'] = response['med_tempvalue']
				if response['high_tempvalue'] != '':
					pagectrl['high_tempvalue'] = response['high_tempvalue']

				pagectrl['refresh'] = 'off'	
				control['tuning_mode'] = False  # Disable tuning mode while paused
				write_control(control)

			elif 'save' in response:
				if response['low_trvalue'] != '':
					pagectrl['low_trvalue'] = response['low_trvalue']
				if response['med_trvalue'] != '':
					pagectrl['med_trvalue'] = response['med_trvalue']
				if response['high_trvalue'] != '':
					pagectrl['high_trvalue'] = response['high_trvalue']

				if response['low_tempvalue'] != '':
					pagectrl['low_tempvalue'] = response['low_tempvalue']
				if response['med_tempvalue'] != '':
					pagectrl['med_tempvalue'] = response['med_tempvalue']
				if response['high_tempvalue'] != '':
					pagectrl['high_tempvalue'] = response['high_tempvalue']

				if (pagectrl['low_tempvalue'] != '' and pagectrl['med_tempvalue'] != '' and
						pagectrl['high_tempvalue'] != ''):
					pagectrl['refresh'] = 'off'
					control['tuning_mode'] = False  # Disable tuning mode when complete
					write_control(control)
					pagectrl['showcalc'] = 'true'
					a, b, c = _calc_shh_coefficients(int(pagectrl['low_tempvalue']), int(pagectrl['med_tempvalue']),
													int(pagectrl['high_tempvalue']), int(pagectrl['low_trvalue']),
													int(pagectrl['med_trvalue']), int(pagectrl['high_trvalue']))
					pagectrl['a'] = a
					pagectrl['b'] = b
					pagectrl['c'] = c
					
					pagectrl['templist'] = ''
					pagectrl['trlist'] = ''

					range_size = abs(int(pagectrl['low_trvalue']) - int(pagectrl['high_trvalue']))
					range_step = int(range_size / 20)

					if int(pagectrl['low_trvalue']) < int(pagectrl['high_trvalue']):
						# Add 5% to the resistance at the low temperature side
						low_tr_range = int(int(pagectrl['low_trvalue']) - (range_size * 0.05))
						# Add 5% to the resistance at the high temperature side
						high_tr_range = int(int(pagectrl['high_trvalue']) + (range_size * 0.05))
						# Swap Tr values for the loop below, so that we start with a low value and go high
						high_tr_range, low_tr_range = low_tr_range, high_tr_range
						# Swapped Value Case (i.e. Low Temp = Low Resistance)
						for index in range(high_tr_range, low_tr_range, range_step):
							if index == high_tr_range:
								pagectrl['trlist'] = str(index)
								pagectrl['templist'] = str(_tr_to_temp(index, a, b, c))
							else:
								pagectrl['trlist'] = str(index) + ', ' + pagectrl['trlist']
								pagectrl['templist'] = str(_tr_to_temp(index, a, b, c)) + ', ' + pagectrl['templist']
					else:
						# Add 5% to the resistance at the low temperature side
						low_tr_range = int(int(pagectrl['low_trvalue']) + (range_size * 0.05))
						# Add 5% to the resistance at the high temperature side
						high_tr_range = int(int(pagectrl['high_trvalue']) - (range_size * 0.05))
						# Normal Value Case (i.e. Low Temp = High Resistance)
						for index in range(high_tr_range, low_tr_range, range_step):
							if index == high_tr_range:
								pagectrl['trlist'] = str(index)
								pagectrl['templist'] = str(_tr_to_temp(index, a, b, c))
							else:
								pagectrl['trlist'] += ', ' + str(index)
								pagectrl['templist'] += ', ' + str(_tr_to_temp(index, a, b, c))
				else:
					pagectrl['refresh'] = 'on'
					control['tuning_mode'] = True  # Enable tuning mode
					write_control(control)
	
	return render_template('tuning.html',
						   control=control,
						   settings=settings,
						   pagectrl=pagectrl,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'],
						   alert=alert)

@app.route('/_grilltr', methods=['GET'])
def grill_tr():

	cur_probe_tr = read_tr()
	tr = {}
	tr['trohms'] = cur_probe_tr[0]

	return json.dumps(tr)

@app.route('/_probe1tr', methods=['GET'])
def probe1_tr():

	cur_probe_tr = read_tr()
	tr = {}
	tr['trohms'] = cur_probe_tr[1]

	return json.dumps(tr)

@app.route('/_probe2tr', methods=['GET'])
def probe2_tr():

	cur_probe_tr = read_tr()
	tr = {}
	tr['trohms'] = cur_probe_tr[2]

	return json.dumps(tr)


@app.route('/events/<action>', methods=['POST','GET'])
@app.route('/events', methods=['POST','GET'])
def events_page(action=None):
	global settings

	if(request.method == 'POST') and ('form' in request.content_type):
		requestform = request.form 
		if 'eventslist' in requestform:
			event_list = read_log(legacy=False)
			page = int(requestform['page'])
			reverse = True if requestform['reverse'] == 'true' else False
			itemsperpage = int(requestform['itemsperpage'])
			pgntd_data = _paginate_list(event_list, reversesortorder=reverse, itemsperpage=itemsperpage, page=page)
			return render_template('_events_list.html', pgntd_data = pgntd_data)
		else:
			return ('Error')

	return render_template('events.html',
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])

@app.route('/pellets/<action>', methods=['POST','GET'])
@app.route('/pellets', methods=['POST','GET'])
def pellets_page(action=None):
	# Pellet Management page
	global settings
	pelletdb = read_pellet_db()

	event = {
		'type' : 'none',
		'text' : ''
	}

	if request.method == 'POST' and action == 'loadprofile':
		response = request.form
		if 'load_profile' in response:
			if response['load_profile'] == 'true':
				pelletdb['current']['pelletid'] = response['load_id']
				pelletdb['current']['est_usage'] = 0
				control = read_control()
				control['hopper_check'] = True
				write_control(control)
				now = str(datetime.datetime.now())
				now = now[0:19] # Truncate the microseconds
				pelletdb['current']['date_loaded'] = now 
				pelletdb['log'][now] = response['load_id']
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = 'Successfully loaded profile and logged.'
	elif request.method == 'GET' and action == 'hopperlevel':
		control = read_control()
		control['hopper_check'] = True
		write_control(control)
	elif request.method == 'POST' and action == 'editbrands':
		response = request.form
		if 'delBrand' in response:
			del_brand = response['delBrand']
			if del_brand in pelletdb['brands']:
				pelletdb['brands'].remove(del_brand)
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = del_brand + ' successfully deleted.'
			else: 
				event['type'] = 'error'
				event['text'] = del_brand + ' not found in pellet brands.'
		elif 'newBrand' in response:
			new_brand = response['newBrand']
			if(new_brand in pelletdb['brands']):
				event['type'] = 'error'
				event['text'] = new_brand + ' already in pellet brands list.'
			else: 
				pelletdb['brands'].append(new_brand)
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = new_brand + ' successfully added.'

	elif request.method == 'POST' and action == 'editwoods':
		response = request.form
		if 'delWood' in response:
			del_wood = response['delWood']
			if del_wood in pelletdb['woods']:
				pelletdb['woods'].remove(del_wood)
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = del_wood + ' successfully deleted.'
			else: 
				event['type'] = 'error'
				event['text'] = del_wood + ' not found in pellet wood list.'
		elif 'newWood' in response:
			new_wood = response['newWood']
			if(new_wood in pelletdb['woods']):
				event['type'] = 'error'
				event['text'] = new_wood + ' already in pellet wood list.'
			else: 
				pelletdb['woods'].append(new_wood)
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = new_wood + ' successfully added.'

	elif request.method == 'POST' and action == 'addprofile':
		response = request.form
		if 'addprofile' in response:
			profile_id = ''.join(filter(str.isalnum, str(datetime.datetime.now())))

			pelletdb['archive'][profile_id] = {
				'id' : profile_id,
				'brand' : response['brand_name'],
				'wood' : response['wood_type'],
				'rating' : int(response['rating']),
				'comments' : response['comments']
			}
			event['type'] = 'updated'
			event['text'] = 'Successfully added profile to database.'

			if response['addprofile'] == 'add_load':
				pelletdb['current']['pelletid'] = profile_id
				control = read_control()
				control['hopper_check'] = True
				write_control(control)
				now = str(datetime.datetime.now())
				now = now[0:19] # Truncate the microseconds
				pelletdb['current']['date_loaded'] = now
				pelletdb['current']['est_usage'] = 0
				pelletdb['log'][now] = profile_id
				event['text'] = 'Successfully added profile and loaded.'

			write_pellet_db(pelletdb)

	elif request.method == 'POST' and action == 'editprofile':
		response = request.form
		if 'editprofile' in response:
			profile_id = response['editprofile']
			pelletdb['archive'][profile_id]['brand'] = response['brand_name']
			pelletdb['archive'][profile_id]['wood'] = response['wood_type']
			pelletdb['archive'][profile_id]['rating'] = int(response['rating'])
			pelletdb['archive'][profile_id]['comments'] = response['comments']
			write_pellet_db(pelletdb)
			event['type'] = 'updated'
			event['text'] = 'Successfully updated ' + response['brand_name'] + ' ' + response['wood_type'] + \
							' profile in database.'
		elif 'delete' in response:
			profile_id = response['delete']
			if pelletdb['current']['pelletid'] == profile_id:
				event['type'] = 'error'
				event['text'] = 'Error: ' + response['brand_name'] + ' ' + response['wood_type'] + \
								' profile cannot be deleted if it is currently loaded.'
			else: 
				pelletdb['archive'].pop(profile_id) # Remove the profile from the archive
				for index in pelletdb['log']:  # Remove this profile ID for the logs
					if(pelletdb['log'][index] == profile_id):
						pelletdb['log'][index] = 'deleted'
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = 'Successfully deleted ' + response['brand_name'] + ' ' + response['wood_type'] + \
								' profile in database.'

	elif request.method == 'POST' and action == 'deletelog':
		response = request.form
		if 'delLog' in response:
			del_log = response['delLog']
			if del_log in pelletdb['log']:
				pelletdb['log'].pop(del_log)
				write_pellet_db(pelletdb)
				event['type'] = 'updated'
				event['text'] = 'Log successfully deleted.'
			else:
				event['type'] = 'error'
				event['text'] = 'Item not found in pellet log.'

	grams = pelletdb['current']['est_usage']
	pounds = round(grams * 0.00220462, 2)
	ounces = round(grams * 0.03527392, 2)
	est_usage_imperial = f'{pounds} lbs' if pounds > 1 else f'{ounces} ozs'
	est_usage_metric = f'{round(grams, 2)} g' if grams < 1000 else f'{round(grams / 1000, 2)} kg'

	return render_template('pellets.html',
						   alert=event,
						   pelletdb=pelletdb,
						   est_usage_imperial=est_usage_imperial,
						   est_usage_metric=est_usage_metric,
						   units=settings['globals']['units'],
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])


@app.route('/recipes', methods=['POST','GET'])
def recipes_page(action=None):
	global settings
	# Placholder for Recipe UI
	return render_template('recipes.html',
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])

@app.route('/settings/<action>', methods=['POST','GET'])
@app.route('/settings', methods=['POST','GET'])
def settings_page(action=None):

	global settings
	control = read_control()

	event = {
		'type' : 'none',
		'text' : ''
	}

	if request.method == 'POST' and action == 'probes':
		response = request.form

		if 'grill1enable' in response:
			if response['grill1enable'] == '0':
				settings['probe_settings']['grill_probe_enabled'][0] = 0
			else:
				settings['probe_settings']['grill_probe_enabled'][0] = 1
		if 'grill2enable' in response:
			if response['grill2enable'] == '0':
				settings['probe_settings']['grill_probe_enabled'][1] = 0
			else:
				settings['probe_settings']['grill_probe_enabled'][1] = 1
		if 'probe1enable' in response:
			if response['probe1enable'] == '0':
				settings['probe_settings']['probes_enabled'][1] = 0
			else:
				settings['probe_settings']['probes_enabled'][1] = 1
		if 'probe2enable' in response:
			if response['probe2enable'] == '0':
				settings['probe_settings']['probes_enabled'][2] = 0
			else:
				settings['probe_settings']['probes_enabled'][2] = 1
		if 'grill_probes' in response:
			if response['grill_probes'] == 'grill_probe1':
				settings['grill_probe_settings']['grill_probe_enabled'][0] = 1
				settings['grill_probe_settings']['grill_probe_enabled'][1] = 0
				settings['grill_probe_settings']['grill_probe_enabled'][2] = 0
				settings['grill_probe_settings']['grill_probe'] = response['grill_probes']
			elif response['grill_probes'] == 'grill_probe2':
				settings['grill_probe_settings']['grill_probe_enabled'][0] = 0
				settings['grill_probe_settings']['grill_probe_enabled'][1] = 1
				settings['grill_probe_settings']['grill_probe_enabled'][2] = 0
				settings['grill_probe_settings']['grill_probe'] = response['grill_probes']
			elif response['grill_probes'] == 'grill_probe3':
				settings['grill_probe_settings']['grill_probe_enabled'][0] = 0
				settings['grill_probe_settings']['grill_probe_enabled'][1] = 0
				settings['grill_probe_settings']['grill_probe_enabled'][2] = 1
				settings['grill_probe_settings']['grill_probe'] = response['grill_probes']
		if 'grill_probe1_type' in response:
			settings['probe_types']['grill1type'] = response['grill_probe1_type']
		if 'grill_probe2_type' in response:
			settings['probe_types']['grill2type'] = response['grill_probe2_type']
		if 'probe1_type' in response:
			settings['probe_types']['probe1type'] = response['probe1_type']
		if 'probe2_type' in response:
			settings['probe_types']['probe2type'] = response['probe2_type']
		if 'adc_grill_probe_one' in response:
			settings['probe_settings']['probe_sources'][0] = response['adc_grill_probe_one']
		if 'adc_grill_probe_two' in response:
			settings['probe_settings']['probe_sources'][3] = response['adc_grill_probe_two']
		if 'adc_probe_one' in response:
			settings['probe_settings']['probe_sources'][1] = response['adc_probe_one']
		if 'adc_probe_two' in response:
			settings['probe_settings']['probe_sources'][2] = response['adc_probe_two']

		event['type'] = 'updated'
		event['text'] = 'Successfully updated probe settings.'

		control['probe_profile_update'] = True

		# Take all settings and write them
		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'notify':
		response = request.form

		if _is_checked(response, 'apprise_enabled'):
			settings['apprise']['enabled'] = True
		else:
			settings['apprise']['enabled'] = False
		if 'appriselocations' in response:
			locations = []
			for location in response.getlist('appriselocations'):
				if(len(location)):
					locations.append(location)
			settings['apprise']['locations'] = locations
		else:
			settings['apprise']['locations'] = []
		if _is_checked(response, 'ifttt_enabled'):
			settings['ifttt']['enabled'] = True
		else:
			settings['ifttt']['enabled'] = False
		if 'iftttapi' in response:
			settings['ifttt']['APIKey'] = response['iftttapi']
		if _is_checked(response, 'pushbullet_enabled'):
			settings['pushbullet']['enabled'] = True
		else:
			settings['pushbullet']['enabled'] = False
		if 'pushbullet_apikey' in response:
			settings['pushbullet']['APIKey'] = response['pushbullet_apikey']
		if 'pushbullet_publicurl' in response:
			settings['pushbullet']['PublicURL'] = response['pushbullet_publicurl']
		if _is_checked(response, 'pushover_enabled'):
			settings['pushover']['enabled'] = True
		else:
			settings['pushover']['enabled'] = False
		if 'pushover_apikey' in response:
			settings['pushover']['APIKey'] = response['pushover_apikey']
		if 'pushover_userkeys' in response:
			settings['pushover']['UserKeys'] = response['pushover_userkeys']
		if 'pushover_publicurl' in response:
			settings['pushover']['PublicURL'] = response['pushover_publicurl']
		if _is_checked(response, 'onesignal_enabled'):
			settings['onesignal']['enabled'] = True
		else:
			settings['onesignal']['enabled'] = False

		if _is_checked(response, 'influxdb_enabled'):
			settings['influxdb']['enabled'] = True
		else:
			settings['influxdb']['enabled'] = False
		if 'influxdb_url' in response:
			settings['influxdb']['url'] = response['influxdb_url']
		if 'influxdb_token' in response:
			settings['influxdb']['token'] = response['influxdb_token']
		if 'influxdb_org' in response:
			settings['influxdb']['org'] = response['influxdb_org']
		if 'influxdb_bucket' in response:
			settings['influxdb']['bucket'] = response['influxdb_bucket']

		if 'delete_device' in response:
			DeviceID = response['delete_device']
			settings['onesignal']['devices'].pop(DeviceID)

		if 'edit_device' in response:
			if response['edit_device'] != '':
				DeviceID = response['edit_device']
				settings['onesignal']['devices'][DeviceID] = {
					'friendly_name' : response['FriendlyName_' + DeviceID],
					'device_name' : response['DeviceName_' + DeviceID],
					'app_version' : response['AppVersion_' + DeviceID]
				}

		control['settings_update'] = True

		event['type'] = 'updated'
		event['text'] = 'Successfully updated notification settings.'

		# Take all settings and write them
		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'editprofile':
		response = request.form

		if 'delete' in response:
			UniqueID = response['delete'] # Get the string of the UniqueID
			try:
				settings['probe_settings']['probe_profiles'].pop(UniqueID)
				write_settings(settings)
				event['type'] = 'updated'
				event['text'] = 'Successfully removed ' + response['Name_' + UniqueID] + ' profile.'
			except:
				event['type'] = 'error'
				event['text'] = 'Error: Failed to remove ' + response['Name_' + UniqueID] + ' profile.'

		if 'editprofile' in response:
			if response['editprofile'] != '':
				# Try to convert input values
				try:
					UniqueID = response['editprofile'] # Get the string of the UniqueID
					settings['probe_settings']['probe_profiles'][UniqueID] = {
						'Vs' : float(response['Vs_' + UniqueID]),
						'Rd' : int(response['Rd_' + UniqueID]),
						'A' : float(response['A_' + UniqueID]),
						'B' : float(response['B_' + UniqueID]),
						'C' : float(response['C_' + UniqueID]),
						'name' : response['Name_' + UniqueID]
					}

					if response['UniqueID_' + UniqueID] != UniqueID:
						# Copy Old Profile to New Profile
						settings['probe_settings']['probe_profiles'][response['UniqueID_' + UniqueID]] = settings[
							'probe_settings']['probe_profiles'][UniqueID]
						# Remove the Old Profile
						settings['probe_settings']['probe_profiles'].pop(UniqueID)
					event['type'] = 'updated'
					event['text'] = 'Successfully added ' + response['Name_' + UniqueID] + ' profile.'
					# Write the new probe profile to disk
					write_settings(settings)
				except:
					event['type'] = 'error'
					event['text'] = 'Something bad happened when trying to format your inputs.  Try again.'
			else:
				event['type'] = 'error'
				event['text'] = 'Error. Profile NOT saved.'

	if request.method == 'POST' and action == 'addprofile':
		response = request.form

		if (response['UniqueID'] != '' and response['Name'] != '' and response['Vs'] != '' and
				response['Rd'] != '' and response['A'] != '' and response['B'] != '' and response['C'] != ''):
			# Try to convert input values
			try:
				settings['probe_settings']['probe_profiles'][response['UniqueID']] = {
					'Vs' : float(response['Vs']),
					'Rd' : int(response['Rd']),
					'A' : float(response['A']),
					'B' : float(response['B']),
					'C' : float(response['C']),
					'name' : response['Name']
				}
				event['type'] = 'updated'
				event['text'] = 'Successfully added ' + response['Name'] + ' profile.'
				# Write the new probe profile to disk
				write_settings(settings)

			except:
				event['type'] = 'error'
				event['text'] = 'Something bad happened when trying to format your inputs.  Try again.'
		else:
			event['type'] = 'error'
			event['text'] = 'All fields must be completed before submitting. Profile NOT saved.'

	if request.method == 'POST' and action == 'cycle':
		response = request.form

		if _is_not_blank(response, 'pmode'):
			settings['cycle_data']['PMode'] = int(response['pmode'])
		if _is_not_blank(response, 'holdcycletime'):
			settings['cycle_data']['HoldCycleTime'] = int(response['holdcycletime'])
		if _is_not_blank(response, 'smokecycletime'):
			settings['cycle_data']['SmokeCycleTime'] = int(response['smokecycletime'])
		if _is_not_blank(response, 'propband'):
			settings['cycle_data']['PB'] = float(response['propband'])
		if _is_not_blank(response, 'integraltime'):
			settings['cycle_data']['Ti'] = float(response['integraltime'])
		if _is_not_blank(response, 'derivtime'):
			settings['cycle_data']['Td'] = float(response['derivtime'])
		if _is_not_blank(response, 'u_min'):
			settings['cycle_data']['u_min'] = float(response['u_min'])
		if _is_not_blank(response, 'u_max'):
			settings['cycle_data']['u_max'] = float(response['u_max'])
		if _is_not_blank(response, 'center'):
			settings['cycle_data']['center'] = float(response['center'])
		if _is_checked(response, 'lid_open_detect_enable'):
			settings['cycle_data']['LidOpenDetectEnabled'] = True
		else:
			settings['cycle_data']['LidOpenDetectEnabled'] = False
		if _is_not_blank(response, 'lid_open_threshold'):
			settings['cycle_data']['LidOpenThreshold'] = int(response['lid_open_threshold'])
		if _is_not_blank(response, 'lid_open_pausetime'):
			settings['cycle_data']['LidOpenPauseTime'] = int(response['lid_open_pausetime'])
		if _is_not_blank(response, 'sp_on_time'):
			settings['smoke_plus']['on_time'] = int(response['sp_on_time'])
		if _is_not_blank(response, 'sp_off_time'):
			settings['smoke_plus']['off_time'] = int(response['sp_off_time'])
		if _is_checked(response, 'sp_fan_ramp'):
			settings['smoke_plus']['fan_ramp'] = True
		else:
			settings['smoke_plus']['fan_ramp'] = False
		if _is_not_blank(response, 'sp_duty_cycle'):
			settings['smoke_plus']['duty_cycle'] = int(response['sp_duty_cycle'])
		if _is_not_blank(response, 'sp_min_temp'):
			settings['smoke_plus']['min_temp'] = int(response['sp_min_temp'])
		if _is_not_blank(response, 'sp_max_temp'):
			settings['smoke_plus']['max_temp'] = int(response['sp_max_temp'])
		if _is_checked(response, 'default_smoke_plus'):
			settings['smoke_plus']['enabled'] = True
		else:
			settings['smoke_plus']['enabled'] = False
		if _is_not_blank(response, 'keep_warm_temp'):
			settings['keep_warm']['temp'] = int(response['keep_warm_temp'])
		if _is_checked(response, 'keep_warm_s_plus'):
			settings['keep_warm']['s_plus'] = True
		else:
			settings['keep_warm']['s_plus'] = False



		event['type'] = 'updated'
		event['text'] = 'Successfully updated cycle settings.'

		control['settings_update'] = True

		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'pwm':
		response = request.form

		if _is_checked(response, 'pwm_control'):
			settings['pwm']['pwm_control'] = True
		else:
			settings['pwm']['pwm_control'] = False
		if _is_not_blank(response, 'pwm_update'):
			settings['pwm']['update_time'] = int(response['pwm_update'])
		if _is_not_blank(response, 'min_duty_cycle'):
			settings['pwm']['min_duty_cycle'] = int(response['min_duty_cycle'])
		if _is_not_blank(response, 'max_duty_cycle'):
			settings['pwm']['max_duty_cycle'] = int(response['max_duty_cycle'])
		if _is_not_blank(response, 'frequency'):
			settings['pwm']['frequency'] = int(response['frequency'])

		event['type'] = 'updated'
		event['text'] = 'Successfully updated PWM settings.'

		control['settings_update'] = True

		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'timers':
		response = request.form

		if _is_not_blank(response, 'shutdown_timer'):
			settings['globals']['shutdown_timer'] = int(response['shutdown_timer'])
		if _is_not_blank(response, 'startup_timer'):
			settings['globals']['startup_timer'] = int(response['startup_timer'])
		if _is_checked(response, 'auto_power_off'):
			settings['globals']['auto_power_off'] = True
		else:
			settings['globals']['auto_power_off'] = False
		if _is_checked(response, 'smartstart_enable'):
			settings['smartstart']['enabled'] = True
		else:
			settings['smartstart']['enabled'] = False

		settings['start_to_mode']['after_startup_mode'] = response['after_startup_mode']
		settings['start_to_mode']['grill1_setpoint'] = int(response['startup_mode_grill1_setpoint'])
		
		event['type'] = 'updated'
		event['text'] = 'Successfully updated startup/shutdown settings.'

		control['settings_update'] = True

		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'dashboard':
		response = request.form
		if _is_not_blank(response, 'dashboardSelect'):
			settings['dashboard']['current'] = response['dashboardSelect']
			write_settings(settings)
			event['type'] = 'updated'
			event['text'] = 'Successfully updated dashboard settings.'

	if request.method == 'POST' and action == 'history':
		response = request.form

		if _is_not_blank(response, 'historymins'):
			settings['history_page']['minutes'] = int(response['historymins'])
		if _is_checked(response, 'clearhistorystartup'):
			settings['history_page']['clearhistoryonstart'] = True
		else:
			settings['history_page']['clearhistoryonstart'] = False
		if _is_checked(response, 'historyautorefresh'):
			settings['history_page']['autorefresh'] = 'on'
		else:
			settings['history_page']['autorefresh'] = 'off'
		if _is_not_blank(response, 'datapoints'):
			settings['history_page']['datapoints'] = int(response['datapoints'])

		# This check should be the last in this group
		if control['mode'] != 'Stop' and _is_checked(response, 'ext_data') != settings['globals']['ext_data']:
			event['type'] = 'error'
			event['text'] = 'This setting cannot be changed in any active mode.  Stop the grill and try again.'
		else: 
			if _is_checked(response, 'ext_data'):
				settings['globals']['ext_data'] = True
			else:
				settings['globals']['ext_data'] = False 

			event['type'] = 'updated'
			event['text'] = 'Successfully updated history settings.'

		write_settings(settings)

	if request.method == 'POST' and action == 'pagesettings':
		response = request.form

		if _is_checked(response, 'darkmode'):
			settings['globals']['page_theme'] = 'dark'
		else:
			settings['globals']['page_theme'] = 'light'

		event['type'] = 'updated'
		event['text'] = 'Successfully updated page settings.'

		write_settings(settings)

	if request.method == 'POST' and action == 'safety':
		response = request.form

		if _is_not_blank(response, 'minstartuptemp'):
			settings['safety']['minstartuptemp'] = int(response['minstartuptemp'])
		if _is_not_blank(response, 'maxstartuptemp'):
			settings['safety']['maxstartuptemp'] = int(response['maxstartuptemp'])
		if _is_not_blank(response, 'reigniteretries'):
			settings['safety']['reigniteretries'] = int(response['reigniteretries'])
		if _is_not_blank(response, 'maxtemp'):
			settings['safety']['maxtemp'] = int(response['maxtemp'])

		event['type'] = 'updated'
		event['text'] = 'Successfully updated safety settings.'

		write_settings(settings)

	if request.method == 'POST' and action == 'grillname':
		response = request.form

		if 'grill_name' in response:
			settings['globals']['grill_name'] = response['grill_name']
			event['type'] = 'updated'
			event['text'] = 'Successfully updated grill name.'

		write_settings(settings)

	if request.method == 'POST' and action == 'pellets':
		response = request.form

		if _is_checked(response, 'pellet_warning'):
			settings['pelletlevel']['warning_enabled'] = True
		else:
			settings['pelletlevel']['warning_enabled'] = False
		if _is_not_blank(response, 'warning_time'):
			settings['pelletlevel']['warning_time'] = int(response['warning_time'])
		if _is_not_blank(response, 'warning_level'):
			settings['pelletlevel']['warning_level'] = int(response['warning_level'])
		if _is_not_blank(response, 'empty'):
			settings['pelletlevel']['empty'] = int(response['empty'])
			control['distance_update'] = True
		if _is_not_blank(response, 'full'):
			settings['pelletlevel']['full'] = int(response['full'])
			control['distance_update'] = True
		if _is_not_blank(response, 'auger_rate'):
			settings['globals']['augerrate'] = float(response['auger_rate'])

		event['type'] = 'updated'
		event['text'] = 'Successfully updated pellet settings.'

		control['settings_update'] = True

		write_settings(settings)
		write_control(control)

	if request.method == 'POST' and action == 'units':
		response = request.form

		if 'units' in response:
			if response['units'] == 'C' and settings['globals']['units'] == 'F':
				settings = convert_settings_units('C', settings)
				write_settings(settings)
				event['type'] = 'updated'
				event['text'] = 'Successfully updated units to Celsius.'
				control = read_control()
				control['updated'] = True
				control['units_change'] = True
				write_control(control)
			elif response['units'] == 'F' and settings['globals']['units'] == 'C':
				settings = convert_settings_units('F', settings)
				write_settings(settings)
				event['type'] = 'updated'
				event['text'] = 'Successfully updated units to Fahrenheit.'
				control = read_control()
				control['updated'] = True
				control['units_change'] = True
				write_control(control)
	'''
	Smart Start Settings
	'''
	if request.method == 'GET' and action == 'smartstart':
		temps = settings['smartstart']['temp_range_list']
		profiles = settings['smartstart']['profiles']
		return(jsonify({'temps_list' : temps, 'profiles' : profiles}))

	if request.method == 'POST' and action == 'smartstart':
		response = request.json 
		settings['smartstart']['temp_range_list'] = response['temps_list']
		settings['smartstart']['profiles'] = response['profiles']
		write_settings(settings)
		return(jsonify({'result' : 'success'}))

	'''
	PWM Duty Cycle
	'''
	if request.method == 'GET' and action == 'pwm_duty_cycle':
		temps = settings['pwm']['temp_range_list']
		profiles = settings['pwm']['profiles']
		return(jsonify({'dc_temps_list' : temps, 'dc_profiles' : profiles}))

	if request.method == 'POST' and action == 'pwm_duty_cycle':
		response = request.json
		settings['pwm']['temp_range_list'] = response['dc_temps_list']
		settings['pwm']['profiles'] = response['dc_profiles']
		write_settings(settings)
		return(jsonify({'result' : 'success'}))

	return render_template('settings.html',
						   settings=settings,
						   alert=event,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])

@app.route('/admin/<action>', methods=['POST','GET'])
@app.route('/admin', methods=['POST','GET'])
def admin_page(action=None):
	global server_status
	global settings
	control = read_control()
	pelletdb = read_pellet_db()
	notify = ''

	if not os.path.exists(BACKUP_PATH):
		os.mkdir(BACKUP_PATH)
	files = os.listdir(BACKUP_PATH)
	for file in files:
		if not _allowed_file(file):
			files.remove(file)

	if action == 'reboot':
		event = "Admin: Reboot"
		write_log(event)
		if is_raspberry_pi():
			os.system("sleep 3 && sudo reboot &")
		server_status = 'rebooting'
		return render_template('shutdown.html', action=action, page_theme=settings['globals']['page_theme'],
							   grill_name=settings['globals']['grill_name'])

	elif action == 'shutdown':
		event = "Admin: Shutdown"
		write_log(event)
		if is_raspberry_pi():
			os.system("sleep 3 && sudo shutdown -h now &")
		server_status = 'shutdown'
		return render_template('shutdown.html', action=action, page_theme=settings['globals']['page_theme'],
							   grill_name=settings['globals']['grill_name'])

	elif action == 'restart':
		event = "Admin: Restart Server"
		write_log(event)
		server_status = 'restarting'
		restart_scripts()
		return render_template('shutdown.html', action=action, page_theme=settings['globals']['page_theme'],
							   grill_name=settings['globals']['grill_name'])

	if request.method == 'POST' and action == 'setting':
		response = request.form

		if 'debugenabled' in response:
			control['settings_update'] = True
			if response['debugenabled'] == 'disabled':
				write_log('Debug Mode Disabled.')
				settings['globals']['debug_mode'] = False
				write_settings(settings)
				write_control(control)
			else:
				settings['globals']['debug_mode'] = True
				write_settings(settings)
				write_control(control)
				write_log('Debug Mode Enabled.')

		if 'clearhistory' in response:
			if response['clearhistory'] == 'true':
				write_log('Clearing History Log.')
				read_history(0, flushhistory=True)

		if 'clearevents' in response:
			if response['clearevents'] == 'true':
				write_log('Clearing Events Log.')
				os.system('rm /tmp/events.log')

		if 'clearpelletdb' in response:
			if response['clearpelletdb'] == 'true':
				write_log('Clearing Pellet Database.')
				os.system('rm pelletdb.json')

		if 'clearpelletdblog' in response:
			if response['clearpelletdblog'] == 'true':
				write_log('Clearing Pellet Database Log.')
				pelletdb['log'].clear()
				write_pellet_db(pelletdb)

		if 'factorydefaults' in response:
			if response['factorydefaults'] == 'true':
				write_log('Resetting Settings, Control and History to factory defaults.')
				read_history(0, flushhistory=True)
				read_control(flush=True)
				os.system('rm settings.json')
				os.system('rm pelletdb.json')
				settings = default_settings()
				control = default_control()
				write_settings(settings)
				write_control(control)
				server_status = 'restarting'
				restart_scripts()
				return render_template('shutdown.html', action='restart', page_theme=settings['globals']['page_theme'],
									   grill_name=settings['globals']['grill_name'])

		if 'download_logs' in response:
			zip_file = _zip_files_logs('logs')
			return send_file(zip_file, as_attachment=True, max_age=0)
		
		if 'backupsettings' in response:
			time_now = datetime.datetime.now()
			time_str = time_now.strftime('%m-%d-%y_%H%M%S') # Truncate the microseconds
			backup_file = BACKUP_PATH + 'PiFire_' + time_str + '.json'
			os.system(f'cp settings.json {backup_file}')
			return send_file(backup_file, as_attachment=True, max_age=0)

		if 'restoresettings' in response:
			# Assume we have request.files and local file in response
			remote_file = request.files['uploadfile']
			local_file = request.form['localfile']
			
			if local_file != 'none':
				settings = read_settings(filename=BACKUP_PATH+local_file)
				notify = "success"
			elif remote_file.filename != '':
				# If the user does not select a file, the browser submits an
				# empty file without a filename.
				if remote_file and _allowed_file(remote_file.filename):
					filename = secure_filename(remote_file.filename)
					remote_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
					notify = "success"
					settings = read_settings(filename=BACKUP_PATH+filename)
				else:
					notify = "error"
			else:
				notify = "error"

		if 'backuppelletdb' in response:
			time_now = datetime.datetime.now()
			time_str = time_now.strftime('%m-%d-%y_%H%M%S') # Truncate the microseconds
			backup_file = BACKUP_PATH + 'PelletDB_' + time_str + '.json'
			os.system(f'cp pelletdb.json {backup_file}')
			return send_file(backup_file, as_attachment=True, max_age=0)

		if 'restorepelletdb' in response:
			# Assume we have request.files and local file in response
			remote_file = request.files['uploadfile']
			local_file = request.form['localfile']
			
			if local_file != 'none':
				pelletdb = read_pellet_db(filename=BACKUP_PATH+local_file)
				write_pellet_db(pelletdb)
				notify = "success"
			elif remote_file.filename != '':
				# If the user does not select a file, the browser submits an
				# empty file without a filename.
				if remote_file and _allowed_file(remote_file.filename):
					filename = secure_filename(remote_file.filename)
					remote_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
					#print(f'{filename} saved to {BACKUPPATH}')
					notify = "success"
					pelletdb = read_pellet_db(filename=BACKUP_PATH+filename)
					write_pellet_db(pelletdb)
				else:
					notify = "error"
			else:
				notify = "error"

	uptime = os.popen('uptime').readline()

	cpu_info = os.popen('cat /proc/cpuinfo').readlines()

	ifconfig = os.popen('ifconfig').readlines()

	if is_raspberry_pi():
		temp = _check_cpu_temp()
	else:
		temp = '---'

	debug_mode = settings['globals']['debug_mode']

	url = request.url_root

	return render_template('admin.html', settings=settings, notify=notify, uptime=uptime, cpuinfo=cpu_info, temp=temp,
						   ifconfig=ifconfig, debug_mode=debug_mode, qr_content=url,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'], files=files)

@app.route('/manual/<action>', methods=['POST','GET'])
@app.route('/manual', methods=['POST','GET'])
def manual_page(action=None):

	global settings
	control = read_control()

	if request.method == 'POST':
		response = request.form

		if 'setmode' in response:
			if response['setmode'] == 'manual':
				control['updated'] = True
				control['mode'] = 'Manual'
			else:
				control['updated'] = True
				control['mode'] = 'Stop'

		if 'change_output_fan' in response:
			if response['change_output_fan'] == 'on':
				control['manual']['change'] = True
				control['manual']['fan'] = True
			elif response['change_output_fan'] == 'off':
				control['manual']['change'] = True
				control['manual']['fan'] = False
				control['manual']['pwm'] = 100
		elif 'change_output_auger' in response:
			if response['change_output_auger'] == 'on':
				control['manual']['change'] = True
				control['manual']['auger'] = True
			elif response['change_output_auger'] == 'off':
				control['manual']['change'] = True
				control['manual']['auger'] = False
		elif 'change_output_igniter' in response:
			if response['change_output_igniter'] == 'on':
				control['manual']['change'] = True
				control['manual']['igniter'] = True
			elif response['change_output_igniter'] == 'off':
				control['manual']['change'] = True
				control['manual']['igniter'] = False
		elif 'change_output_power' in response:
			if response['change_output_power'] == 'on':
				control['manual']['change'] = True
				control['manual']['power'] = True
			elif response['change_output_power'] == 'off':
				control['manual']['change'] = True
				control['manual']['power'] = False
		elif 'duty_cycle_range' in response:
			speed = int(response['duty_cycle_range'])
			control['manual']['change'] = True
			control['manual']['pwm'] = speed

		write_control(control)

		time.sleep(1)
		control = read_control()

	return render_template('manual.html', settings=settings, control=control,
						   page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'])

@app.route('/api/<action>', methods=['POST','GET'])
@app.route('/api', methods=['POST','GET'])
def api_page(action=None):
	global settings
	global server_status

	if request.method == 'GET':
		if action == 'settings':
			return jsonify({'settings':settings}), 201
		elif action == 'server':
			return jsonify({'server_status' : server_status}), 201
		elif action == 'control':
			control=read_control()
			return jsonify({'control':control}), 201
		elif action == 'current':
			current = read_current()
			current_temps = {
				'grill_temp' : current[0],
				'probe1_temp' : current[1],
				'probe2_temp' : current[2]
			}
			control = read_control()
			current_setpoints = control['setpoints']
			pelletdb = read_pellet_db()
			status = {}
			status['mode'] = control['mode']
			status['status'] = control['status']
			status['s_plus'] = control['s_plus']
			status['units'] = settings['globals']['units']
			status['name'] = settings['globals']['grill_name']
			status['pelletlevel'] = pelletdb['current']['hopper_level']
			pelletid = pelletdb['current']['pelletid']
			status['pellets'] = f'{pelletdb["archive"][pelletid]["brand"]} {pelletdb["archive"][pelletid]["wood"]}'
			return jsonify({'current':current_temps, 'setpoints':current_setpoints, 'status':status}), 201
		else:
			return jsonify({'Error':'Received GET request, without valid action'}), 404
	elif request.method == 'POST':
		if not request.json:
			event = "Local API Call Failed"
			write_log(event)
			abort(400)
		else:
			request_json = request.json
			if(action == 'settings'):
				for key in settings.keys():
					if key in request_json.keys():
						settings[key].update(request_json.get(key, {}))
				write_settings(settings)
				return jsonify({'settings':'success'}), 201
			elif(action == 'control'):
				control = read_control()
				for key in control.keys():
					if key in request_json.keys():
						if key in ['setpoints', 'safety', 'notify_req', 'notify_data', 'timer', 'manual']:
							control[key].update(request_json.get(key, {}))
						else:
							control[key] = request_json[key]
				write_control(control)
				return jsonify({'control':'success'}), 201
			else:
				return jsonify({'Error':'Received POST request no valid action.'}), 404
	else:
		return jsonify({'Error':'Received undefined/unsupported request.'}), 404

'''
Wizard Route for PiFire Setup
'''
@app.route('/wizard/<action>', methods=['POST','GET'])
@app.route('/wizard', methods=['GET', 'POST'])
def wizard(action=None):
	global settings

	wizardData = read_wizard()

	if request.method == 'GET':
		if action=='welcome':
			return render_template('wizard.html', settings=settings, page_theme=settings['globals']['page_theme'],
								   grill_name=settings['globals']['grill_name'], wizardData=wizardData)
		elif action=='installstatus':
			percent, status, output = get_wizard_install_status()
			return jsonify({'percent' : percent, 'status' : status, 'output' : output}) 
	elif request.method == 'POST':
		r = request.form
		if action=='cancel':
			settings['globals']['first_time_setup'] = False
			write_settings(settings)
			return redirect('/')
		if action=='finish':
			wizardInstallInfo = prepare_wizard_data(r)
			store_wizard_install_info(wizardInstallInfo)
			set_wizard_install_status(0, 'Starting Install...', '')
			os.system('python3 wizard.py &')	# Kickoff Installation
			return render_template('wizard-finish.html', page_theme=settings['globals']['page_theme'],
								   grill_name=settings['globals']['grill_name'], wizardData=wizardData)
		if action=='modulecard':
			module = r['module']
			section = r['section']
			if section in ['grillplatform', 'probes', 'display', 'distance']:
				moduleData = wizardData['modules'][section][module]
			else:
				return '<strong color="red">No Data</strong>'
			return render_template('wizard-card.html', moduleData=moduleData, moduleSection=section)	

	return render_template('wizard.html', settings=settings, page_theme=settings['globals']['page_theme'],
						   grill_name=settings['globals']['grill_name'], wizardData=wizardData)

def prepare_wizard_data(form_data):
	wizardData = read_wizard()
	
	wizardInstallInfo = {}
	wizardInstallInfo['modules'] = {
		'grillplatform' : {
			'module_selected' : form_data['grillplatformSelect'],
			'settings' : {}
		}, 
		'probes' : {
			'module_selected' : form_data['probesSelect'],
			'settings' : {}
		}, 
		'display' : {
			'module_selected' : form_data['displaySelect'],
			'settings' : {}
		}, 
		'distance' : {
			'module_selected' : form_data['distanceSelect'],
			'settings' : {}
		}, 
	}

	for module in ['grillplatform', 'probes', 'display', 'distance']:
		module_ = module + '_'
		moduleSelect = module + 'Select'
		selected = form_data[moduleSelect]
		for setting in wizardData['modules'][module][selected]['settings_dependencies']:
			settingName = module_ + setting
			if(settingName in form_data):
				wizardInstallInfo['modules'][module]['settings'][setting] = form_data[settingName]

	return(wizardInstallInfo)

'''
Manifest Route for Web Application Integration
'''
@app.route('/manifest')
def manifest():
	res = make_response(render_template('manifest.json'), 200)
	res.headers["Content-Type"] = "text/cache-manifest"
	return res

'''
Updater Function Routes
'''
@app.route('/checkupdate', methods=['GET'])
def check_update(action=None):
	global settings
	update_data = {}
	update_data['version'] = settings['versions']['server']

	avail_updates_struct = get_available_updates()

	if avail_updates_struct['success']:
		commits_behind = avail_updates_struct['commits_behind']
	else:
		event = avail_updates_struct['message']
		write_log(event)
		return jsonify({'result' : 'failure', 'message' : avail_updates_struct['message'] })

	return jsonify({'result' : 'success', 'current' : update_data['version'], 'behind' : commits_behind})

@app.route('/update', methods=['POST','GET'])
def update_page(action=None):
	global settings

	# Populate Update Data Structure
	update_data = {}
	update_data['version'] = settings['versions']['server']
	update_data['branch_target'], error_msg = get_branch()
	if error_msg != '':
		WriteLog(error_msg)
	update_data['branches'], error_msg = get_available_branches()
	if error_msg != '':
		WriteLog(error_msg)
	update_data['remote_url'], error_msg = get_remote_url()
	if error_msg != '':
		WriteLog(error_msg)
	update_data['remote_version'], error_msg = get_remote_version()
	if error_msg != '':
		WriteLog(error_msg)

	if request.method == 'GET':
		if action is None:
			update_data = get_update_data(settings)
			return render_template('updater.html', alert=alert, settings=settings,
								   page_theme=settings['globals']['page_theme'],
								   grill_name=settings['globals']['grill_name'],
								   update_data=update_data)
		elif action=='updatestatus':
			percent, status, output = get_updater_install_status()
			return jsonify({'percent' : percent, 'status' : status, 'output' : output})

	if request.method == 'POST':
		r = request.form
		update_data = get_update_data(settings)

		if 'update_remote_branches' in r:
			if is_raspberry_pi():
				os.system('python3 %s %s &' % ('updater.py', '-r'))	 # Update branches from remote 
				time.sleep(4)  # Artificial delay to avoid race condition
			return redirect('/update')

		if 'change_branch' in r:
			if update_data['branch_target'] in r['branch_target']:
				alert = {
					'type' : 'success',
					'text' : f'Current branch {update_data["branch_target"]} already set to {r["branch_target"]}'
				}
				return render_template('updater.html', alert=alert, settings=settings,
									   page_theme=settings['globals']['page_theme'], update_data=update_data,
									   grill_name=settings['globals']['grill_name'])
			else:
				set_updater_install_status(0, 'Starting Branch Change...', '')
				os.system('python3 %s %s %s &' % ('updater.py', '-b', r['branch_target']))	# Kickoff Branch Change
				return render_template('updater-status.html', page_theme=settings['globals']['page_theme'],
									   grill_name=settings['globals']['grill_name'])

		if 'do_update' in r:
			set_updater_install_status(0, 'Starting Update...', '')
			os.system('python3 %s %s %s &' % ('updater.py', '-u', update_data['branch_target']))  # Kickoff Update
			return render_template('updater-status.html', page_theme=settings['globals']['page_theme'],
								   grill_name=settings['globals']['grill_name'])

		if 'show_log' in r:
			if r['show_log'].isnumeric():
				action='log'
				result, error_msg = get_log(num_commits=int(r['show_log']))
				if error_msg == '':
					output_html = f'*** Getting latest updates from origin/{update_data["branch_target"]} ***<br><br>' 
					output_html += result
				else: 
					output_html = f'*** Getting latest updates from origin/{update_data["branch_target"]} ERROR Occurred ***<br><br>' 
					output_html += error_msg
			else:
				output_html = '*** Error, Number of Commits Not Defined! ***<br><br>'
			
			return render_template('updater_out.html', settings=settings, page_theme=settings['globals']['page_theme'],
								   action=action, output_html=output_html, grill_name=settings['globals']['grill_name'])

	return render_template('updater.html', alert=alert, settings=settings, page_theme=settings['globals']['page_theme'], grill_name=settings['globals']['grill_name'], update_data=update_data)
'''
End Updater Section
'''

''' 
Metrics Routes
'''
@app.route('/metrics/<action>', methods=['POST','GET'])
@app.route('/metrics', methods=['POST','GET'])
def metrics_page(action=None):
	global settings

	metrics_data = process_metrics(read_metrics(all=True))

	if (request.method == 'GET') and (action == 'export'):
		filename = datetime.datetime.now().strftime('%Y%m%d-%H%M') + '-PiFire-Metrics-Export'
		csvfilename = _prepare_metrics_csv(metrics_data, filename)
		return send_file(csvfilename, as_attachment=True, max_age=0)

	return render_template('metrics.html', settings=settings, page_theme=settings['globals']['page_theme'], 
							grill_name=settings['globals']['grill_name'], metrics_data=metrics_data)

'''
Supporting Functions
'''

def _is_not_blank(response, setting):
	return setting in response and setting != ''

def _is_checked(response, setting):
	return setting in response and response[setting] == 'on'

def _allowed_file(filename):
	return '.' in filename and \
		   filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def _check_cpu_temp():
	temp = os.popen('vcgencmd measure_temp').readline()
	return temp.replace("temp=","")

def _prepare_data(num_items=10, reduce=True, data_points=60):
	# num_items: Number of items to store in the data blob
	global settings
	units = settings['globals']['units']

	data_struct = read_history(num_items)

	data_blob = {}

	data_blob['label_time_list'] = []
	data_blob['grill_temp_list'] = []
	data_blob['grill_settemp_list'] = []
	data_blob['probe1_temp_list'] = []
	data_blob['probe1_settemp_list'] = []
	data_blob['probe2_temp_list'] = []
	data_blob['probe2_settemp_list'] = []
	
	list_length = len(data_struct['T']) # Length of list(s)

	if (list_length < num_items) and (list_length > 0):
		num_items = list_length

	if reduce and (num_items > data_points):
		step = int(num_items/data_points)
	else:
		step = 1

	if (list_length > 0):
		# Build all lists from file data
		for index in range(list_length - num_items, list_length, step):
			if(units == 'F'):
				data_blob['label_time_list'].append(int(data_struct['T'][index]))  # Timestamp format is int, so convert from str
				data_blob['grill_temp_list'].append(int(data_struct['GT1'][index]))
				data_blob['grill_settemp_list'].append(int(data_struct['GSP1'][index]))
				data_blob['probe1_temp_list'].append(int(data_struct['PT1'][index]))
				data_blob['probe1_settemp_list'].append(int(data_struct['PSP1'][index]))
				data_blob['probe2_temp_list'].append(int(data_struct['PT2'][index]))
				data_blob['probe2_settemp_list'].append(int(data_struct['PSP2'][index]))
			else: 
				data_blob['label_time_list'].append(int(data_struct['T'][index]))  # Timestamp format is int, so convert from str
				data_blob['grill_temp_list'].append(float(data_struct['GT1'][index]))
				data_blob['grill_settemp_list'].append(float(data_struct['GSP1'][index]))
				data_blob['probe1_temp_list'].append(float(data_struct['PT1'][index]))
				data_blob['probe1_settemp_list'].append(float(data_struct['PSP1'][index]))
				data_blob['probe2_temp_list'].append(float(data_struct['PT2'][index]))
				data_blob['probe2_settemp_list'].append(float(data_struct['PSP2'][index]))
	else:
		now = datetime.datetime.now()
		#time_now = now.strftime('%H:%M:%S')
		time_now = int(now.timestamp() * 1000)  # Use timestamp format (int) instead of H:M:S format in string
		for index in range(num_items):
			data_blob['label_time_list'].append(time_now)
			data_blob['grill_temp_list'].append(0)
			data_blob['grill_settemp_list'].append(0)
			data_blob['probe1_temp_list'].append(0)
			data_blob['probe1_settemp_list'].append(0)
			data_blob['probe2_temp_list'].append(0)
			data_blob['probe2_settemp_list'].append(0)

	return(data_blob)

def _prepare_annotations(displayed_starttime, metrics_data=[]):
	if(metrics_data == []):
		metrics_data = read_metrics(all=True)
	annotation_json = {}
	# Process Additional Metrics Information for Display
	for index in range(0, len(metrics_data)):
		# Check if metric falls in the displayed time window
		if metrics_data[index]['starttime'] > displayed_starttime:
			# Convert Start Time
			# starttime = _epoch_to_time(metrics_data[index]['starttime']/1000)
			mode = metrics_data[index]['mode']
			color = 'blue'
			if mode == 'Startup':
				color = 'green'
			elif mode == 'Stop':
				color = 'red'
			elif mode == 'Shutdown':
				color = 'black'
			elif mode == 'Reignite':
				color = 'orange'
			elif mode == 'Error':
				color = 'red'
			elif mode == 'Hold':
				color = 'blue'
			elif mode == 'Smoke':
				color = 'grey'
			elif mode in ['Monitor', 'Manual']:
				color = 'purple'
			annotation = {
							'type' : 'line',
							'xMin' : metrics_data[index]['starttime'],
							'xMax' : metrics_data[index]['starttime'],
							'borderColor' : color,
							'borderWidth' : 2,
							'label': {
								'backgroundColor': color,
								'borderColor' : 'black',
								'color': 'white',
								'content': mode,
								'enabled': True,
								'position': 'end',
								'rotation': 0,
								},
							'display': True
						}
			annotation_json[f'event_{index}'] = annotation

	return(annotation_json)

def _prepare_graph_csv(graph_data=[], graph_labels=[], filename=''):
		standard_data_keys = ['T', 'GT1', 'GSP1', 'PT1', 'PSP1', 'PT2', 'PSP2']  # Standard Labels / Data To Export
		
		# Create filename if no name specified
		if(filename == ''):
			now = datetime.datetime.now()
			filename = now.strftime('%Y%m%d-%H%M') + '-PiFire-Export'
		else:
			filename = filename.replace('.json', '')
			filename = filename.replace('./history/', '')
			filename += '-Pifire-Export'
		
		exportfilename = '/tmp/' + filename + ".csv"
		
		# Open CSV File for editing
		csvfile = open(exportfilename, "w")

		# Get / Set Standard Labels 
		if(graph_labels == []):
			labels = 'Time,Grill Temp,Grill SetTemp,Probe 1 Temp,Probe 1 SetTemp,Probe 2 Temp, Probe 2 SetTemp'
		else:
			labels = 'Time,' 
			labels += graph_labels['grill1_label'] + ' Temp,'
			labels += graph_labels['grill1_label'] + ' Setpoint,'
			labels += graph_labels['probe1_label'] + ' Temp,'
			labels += graph_labels['probe1_label'] + ' Setpoint,'
			labels += graph_labels['probe2_label'] + ' Temp,'
			labels += graph_labels['probe2_label'] + ' Setpoint'

		if(graph_data == []):
			graph_data = read_history()
		
		# Get the length of the data (number of captured events)
		list_length = len(graph_data['T'])

		# Add any additional label data if it exists
		ext_keys = []
		for key in graph_data.keys():
			if key not in standard_data_keys:
				labels += f', {key}'
				ext_keys.append(key)

		# End the labels line
		labels += '\n'

		if(list_length > 0):
			writeline = labels
			csvfile.write(writeline)
			last = -1
			for index in range(0, list_length):
				if (int((index/list_length)*100) > last):
					last = int((index/list_length)*100)
				converted_dt = datetime.datetime.fromtimestamp(int(graph_data['T'][index]) / 1000)
				timestr = converted_dt.strftime('%Y-%m-%d %H:%M:%S')
				writeline = f"{timestr}, {graph_data['GT1'][index]}, {graph_data['GSP1'][index]}, {graph_data['PT1'][index]}, {graph_data['PSP1'][index]}, {graph_data['PT2'][index]}, {graph_data['PSP2'][index]}"
				# Add any additional data if keys exist
				if ext_keys != []:
					for key in ext_keys:
						writeline += f', {graph_data[key][index]}'
				csvfile.write(writeline + '\n')
		else:
			writeline = 'No Data\n'
			csvfile.write(writeline)

		csvfile.close()

		return(exportfilename)

def _convert_labels(indata):
	'''
	Temporary function to convert Grill Data Labels to Legacy Format
	'''
	outdata = {}
	outdata['T'] = indata.pop('time_labels')
	outdata['GT1'] = indata.pop('grill1_temp')
	outdata['GSP1'] = indata.pop('grill1_setpoint')
	outdata['PT1'] = indata.pop('probe1_temp')
	outdata['PSP1'] = indata.pop('probe1_setpoint')
	outdata['PT2'] = indata.pop('probe2_temp')
	outdata['PSP2'] = indata.pop('probe2_setpoint')

	# For any additional keys (extended data)
	ext_keys = []
	for key in indata.keys():
		ext_keys.append(key)
	
	for key in ext_keys: 
		outdata[key] = indata.pop(key)

	return(outdata)

def _prepare_metrics_csv(metrics_data, filename):
	filename = filename.replace('.json', '')
	filename = filename.replace('./history/', '')
	filename = '/tmp/' + filename + '-PiFire-Metrics-Export.csv'

	csvfile = open(filename, 'w')

	list_length = len(metrics_data) # Length of list

	if(list_length > 0):
		# Build the header row
		writeline=''
		for item in range(0, len(metrics_items)):
			writeline += f'{metrics_items[item][0]}, '
		writeline += '\n'
		csvfile.write(writeline)
		for index in range(0, list_length):
			writeline = ''
			for item in range(0, len(metrics_items)):
				writeline += f'{metrics_data[index][metrics_items[item][0]]}, '
			writeline += '\n'
			csvfile.write(writeline)
	else:
		writeline = 'No Data\n'
		csvfile.write(writeline)

	csvfile.close()
	return(filename)

def _prepare_event_totals(events):
	auger_time = 0
	for index in range(0, len(events)):
		auger_time += events[index]['augerontime']
	auger_time = int(auger_time)

	event_totals = {}
	event_totals['augerontime'] = seconds_to_string(auger_time)

	grams = int(auger_time * settings['globals']['augerrate'])
	pounds = round(grams * 0.00220462, 2)
	ounces = round(grams * 0.03527392, 2)
	event_totals['estusage_m'] = f'{grams} grams'
	event_totals['estusage_i'] = f'{pounds} pounds ({ounces} ounces)'

	seconds = int((events[-1]['starttime']/1000) - (events[0]['starttime']/1000))
	
	event_totals['cooktime'] = seconds_to_string(seconds)

	event_totals['pellet_level_start'] = events[0]['pellet_level_start']
	event_totals['pellet_level_end'] = events[-2]['pellet_level_end']

	return(event_totals)

def _paginate_list(datalist, sortkey='', reversesortorder=False, itemsperpage=10, page=1):
	if sortkey != '':
		#  Sort list if key is specified
		tempdatalist = sorted(datalist, key=lambda d: d[sortkey], reverse=reversesortorder)
	else:
		#  If no key, reverse list if specified, or keep order 
		if reversesortorder:
			datalist.reverse()
		tempdatalist = datalist.copy()
	listlength = len(tempdatalist)
	if listlength <= itemsperpage:
		curpage = 1
		prevpage = 1 
		nextpage = 1 
		lastpage = 1
		displaydata = tempdatalist.copy()
	else: 
		lastpage = (listlength // itemsperpage) + ((listlength % itemsperpage) > 0)
		if (lastpage < page):
			curpage = lastpage
			prevpage = curpage - 1 if curpage > 1 else 1
			nextpage = curpage + 1 if curpage < lastpage else lastpage 
		else: 
			curpage = page if page > 0 else 1
			prevpage = curpage - 1 if curpage > 1 else 1
			nextpage = curpage + 1 if curpage < lastpage else lastpage 
		#  Calculate starting / ending position and create list with that data
		start = itemsperpage * (curpage - 1)  # Get starting position 
		end = start + itemsperpage # Get ending position 
		displaydata = tempdatalist.copy()[start:end]

	reverse = 'true' if reversesortorder else 'false'

	pagination = {
		'displaydata' : displaydata,
		'curpage' : curpage,
		'prevpage' : prevpage,
		'nextpage' : nextpage, 
		'lastpage' : lastpage,
		'reverse' : reverse,
		'itemspage' : itemsperpage
	}

	return (pagination)

def _get_cookfilelist(folder=HISTORY_FOLDER):
	# Grab list of Historical Cook Files
	if not os.path.exists(folder):
		os.mkdir(folder)
	dirfiles = os.listdir(folder)
	cookfiles = []
	for file in dirfiles:
		if file.endswith('.pifire'):
			cookfiles.append(file)
	return(cookfiles)

def _get_cookfilelist_details(cookfilelist):
	cookfiledetails = []
	for item in cookfilelist:
		filename = HISTORY_FOLDER + item['filename']
		cookfiledata, status = read_json_file_data(filename, 'metadata')
		if(status == 'OK'):
			thumbnail = unpack_thumb(cookfiledata['thumbnail'], filename) if ('thumbnail' in cookfiledata) else ''
			cookfiledetails.append({'filename' : item['filename'], 'title' : cookfiledata['title'], 'thumbnail' : thumbnail})
		else:
			cookfiledetails.append({'filename' : item['filename'], 'title' : 'ERROR', 'thumbnail' : ''})
	return(cookfiledetails)

def _calc_shh_coefficients(t1, t2, t3, r1, r2, r3):
	try: 
		# Convert Temps from Fahrenheit to Kelvin
		t1 = ((t1 - 32) * (5 / 9)) + 273.15
		t2 = ((t2 - 32) * (5 / 9)) + 273.15
		t3 = ((t3 - 32) * (5 / 9)) + 273.15

		# https://en.wikipedia.org/wiki/Steinhart%E2%80%93Hart_equation

		# Step 1: L1 = ln (R1), L2 = ln (R2), L3 = ln (R3)
		l1 = math.log(r1)
		l2 = math.log(r2)
		l3 = math.log(r3)

		# Step 2: Y1 = 1 / T1, Y2 = 1 / T2, Y3 = 1 / T3
		y1 = 1 / t1
		y2 = 1 / t2
		y3 = 1 / t3

		# Step 3: G2 = (Y2 - Y1) / (L2 - L1) , G3 = (Y3 - Y1) / (L3 - L1)
		g2 = (y2 - y1) / (l2 - l1)
		g3 = (y3 - y1) / (l3 - l1)

		# Step 4: C = ((G3 - G2) / (L3 - L2)) * (L1 + L2 + L3)^-1
		c = ((g3 - g2) / (l3 - l2)) * math.pow(l1 + l2 + l3, -1)

		# Step 5: B = G2 - C * (L1^2 + (L1*L2) + L2^2)
		b = g2 - c * (math.pow(l1, 2) + (l1 * l2) + math.pow(l2, 2))

		# Step 6: A = Y1 - (B + L1^2*C) * L1
		a = y1 - ((b + (math.pow(l1, 2) * c)) * l1)
	except:
		#print('An error occurred when calculating coefficients.')
		a = 0
		b = 0
		c = 0

	return(a, b, c)

def _temp_to_tr(temp_f, a, b, c):
	try: 
		temp_k = ((temp_f - 32) * (5 / 9)) + 273.15

		# https://en.wikipedia.org/wiki/Steinhart%E2%80%93Hart_equation
		# Inverse of the equation, to determine Tr = Resistance Value of the thermistor

		# Not recommended for use, as it commonly produces a complex number

		x = (1 / (2 * c)) * (a - (1 / temp_k))

		y = math.sqrt(math.pow((b / (3 * c)), 3) + math.pow(x, 2))

		Tr = math.exp(((y - x) ** (1 / 3)) - ((y + x) ** (1 / 3)))
	except: 
		Tr = 0

	return int(Tr) 

def _tr_to_temp(tr, a, b, c):
	try:
		#Steinhart Hart Equation
		# 1/T = A + B(ln(R)) + C(ln(R))^3
		# T = 1/(a + b[ln(ohm)] + c[ln(ohm)]^3)
		ln_ohm = math.log(tr) # ln(ohms)
		t1 = (b * ln_ohm) # b[ln(ohm)]
		t2 = c * math.pow(ln_ohm, 3) # c[ln(ohm)]^3
		temp_k = 1/(a + t1 + t2) # calculate temperature in Kelvin
		temp_c = temp_k - 273.15 # Kelvin to Celsius
		temp_c = temp_c * (9 / 5) + 32 # Celsius to Fahrenheit
	except:
		#print('Error occurred while calculating temperature.')
		temp_c = 0.0
	return int(temp_c) # Return Calculated Temperature and Thermistor Value in Ohms

def _str_td(td):
	s = str(td).split(", ", 1)
	a = s[-1]
	if a[1] == ':':
		a = "0" + a
	s2 = s[:-1] + [a]
	return ", ".join(s2)

def _zip_files_dir(dir_name):
	memory_file = BytesIO()
	with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
		for root, dirs, files in os.walk(dir_name):
			for file in files:
				zipf.write(os.path.join(root, file))
	memory_file.seek(0)
	return memory_file

def _zip_files_logs(dir_name):
	time_now = datetime.datetime.now()
	time_str = time_now.strftime('%m-%d-%y_%H%M%S') # Truncate the microseconds
	file_name = f'/tmp/PiFire_Logs_{time_str}.zip'
	directory = pathlib.Path(f'{dir_name}')
	with zipfile.ZipFile(file_name, "w", zipfile.ZIP_DEFLATED) as archive:
		for file_path in directory.rglob("*"):
			archive.write(file_path, arcname=file_path.relative_to(directory))
	return file_name

def _deep_dict_update(orig_dict, new_dict):
	for key, value in new_dict.items():
		if isinstance(value, Mapping):
			orig_dict[key] = _deep_dict_update(orig_dict.get(key, {}), value)
		else:
			orig_dict[key] = value
	return orig_dict

'''
Socket IO for Android Functionality
'''
thread = Thread()
thread_lock = threading.Lock()
clients = 0
force_refresh = False

@socketio.on("connect")
def connect():
	global clients
	clients += 1

@socketio.on("disconnect")
def disconnect():
	global clients
	clients -= 1

@socketio.on('get_dash_data')
def get_dash_data(force=False):
	global thread
	global force_refresh
	force_refresh = force

	with thread_lock:
		if not thread.is_alive():
			thread = socketio.start_background_task(emit_dash_data)

def emit_dash_data():
	global clients
	global force_refresh
	previous_data = ''

	while (clients > 0):
		global settings
		control = read_control()
		pelletdb = read_pellet_db()

		probes_enabled = settings['probe_settings']['probes_enabled']
		cur_probe_temps = read_current()

		current_temps = {
			'grill_temp' : cur_probe_temps[0],
			'probe1_temp' : cur_probe_temps[1],
			'probe2_temp' : cur_probe_temps[2] }
		enabled_probes = {
			'grill' : bool(probes_enabled[0]),
			'probe1' : bool(probes_enabled[1]),
			'probe2' : bool(probes_enabled[2]) }
		probe_titles = {
			'grill_title' : control['probe_titles']['grill_title'],
			'probe1_title' : control['probe_titles']['probe1_title'],
			'probe2_title' : control['probe_titles']['probe2_title'] }

		if control['timer']['end'] - time.time() > 0 or bool(control['timer']['paused']):
			timer_info = {
				'timer_paused' : bool(control['timer']['paused']),
				'timer_start_time' : math.trunc(control['timer']['start']),
				'timer_end_time' : math.trunc(control['timer']['end']),
				'timer_paused_time' : math.trunc(control['timer']['paused']),
				'timer_active' : 'true'
			}
		else:
			timer_info = {
				'timer_paused' : 'false',
				'timer_start_time' : '0',
				'timer_end_time' : '0',
				'timer_paused_time' : '0',
				'timer_active' : 'false'
			}

		current_data = {
			'cur_probe_temps' : current_temps,
			'probes_enabled' : enabled_probes,
			'probe_titles' : probe_titles,
			'set_points' : control['setpoints'],
			'notify_req' : control['notify_req'],
			'notify_data' : control['notify_data'],
			'timer_info' : timer_info,
			'current_mode' : control['mode'],
			'smoke_plus' : control['s_plus'],
			'pwm_control' : control['pwm_control'],
			'hopper_level' : pelletdb['current']['hopper_level']
		}

		if force_refresh:
			socketio.emit('grill_control_data', current_data, broadcast=True)
			force_refresh = False
			socketio.sleep(2)
		elif previous_data != current_data:
			socketio.emit('grill_control_data', current_data, broadcast=True)
			previous_data = current_data
			socketio.sleep(2)
		else:
			socketio.sleep(2)

@socketio.on('get_app_data')
def get_app_data(action=None, type=None):
	global settings

	if action == 'settings_data':
		return settings

	elif action == 'pellets_data':
		return read_pellet_db()

	elif action == 'events_data':
		event_list, num_events = read_log()
		events_trim = []
		for x in range(min(num_events, 60)):
			events_trim.append(event_list[x])
		return { 'events_list' : events_trim }

	elif action == 'history_data':
		num_items = settings['history_page']['minutes'] * 20
		data_blob = _prepare_data(num_items, True, settings['history_page']['datapoints'])
		# Converting time format from 'time from epoch' to H:M:S
		# @weberbox:  Trying to keep the legacy format for the time labels so that I don't break the Android app
		for index in range(0, len(data_blob['label_time_list'])): 
			data_blob['label_time_list'][index] = datetime.datetime.fromtimestamp(
				int(data_blob['label_time_list'][index]) / 1000).strftime('%H:%M:%S')

		return { 'grill_temp_list' : data_blob['grill_temp_list'],
				 'grill_settemp_list' : data_blob['grill_settemp_list'],
				 'probe1_temp_list' : data_blob['probe1_temp_list'],
				 'probe1_settemp_list' : data_blob['probe1_settemp_list'],
				 'probe2_temp_list' : data_blob['probe2_temp_list'],
				 'probe2_settemp_list' : data_blob['probe2_settemp_list'],
				 'label_time_list' : data_blob['label_time_list'] }

	elif action == 'info_data':
		return {
			'uptime' : os.popen('uptime').readline(),
			'cpuinfo' : os.popen('cat /proc/cpuinfo').readlines(),
			'ifconfig' : os.popen('ifconfig').readlines(),
			'temp' : _check_cpu_temp(),
			'outpins' : settings['outpins'],
			'inpins' : settings['inpins'],
			'dev_pins' : settings['dev_pins'],
			'server_version' : settings['versions']['server'] }

	elif action == 'manual_data':
		control = read_control()
		return {
			'manual' : control['manual'],
			'mode' : control['mode'] }

	elif action == 'backup_list':
		if not os.path.exists(BACKUP_PATH):
			os.mkdir(BACKUP_PATH)
		files = os.listdir(BACKUP_PATH)
		for file in files[:]:
			if not _allowed_file(file):
				files.remove(file)

		if type == 'settings':
			for file in files[:]:
				if not file.startswith('PiFire_'):
					files.remove(file)
			return json.dumps(files)

		if type == 'pelletdb':
			for file in files[:]:
				if not file.startswith('PelletDB_'):
					files.remove(file)
		return json.dumps(files)

	elif action == 'backup_data':
		time_now = datetime.datetime.now()
		time_str = time_now.strftime('%m-%d-%y_%H%M%S')

		if type == 'settings':
			backup_file = BACKUP_PATH + 'PiFire_' + time_str + '.json'
			os.system(f'cp settings.json {backup_file}')
			return settings

		if type == 'pelletdb':
			backup_file = BACKUP_PATH + 'PelletDB_' + time_str + '.json'
			os.system(f'cp pelletdb.json {backup_file}')
			return read_pellet_db()

	elif action == 'updater_data':
		avail_updates_struct = get_available_updates()

		if avail_updates_struct['success']:
			commits_behind = avail_updates_struct['commits_behind']
		else:
			message = avail_updates_struct['message']
			write_log(message)
			return {'response': {'result':'error', 'message':'Error: ' + message }}

		if commits_behind > 0:
			logs_result = get_log(commits_behind)
		else:
			logs_result = None

		update_data = {}
		update_data['branch_target'], error_msg = get_branch()
		update_data['branches'], error_msg = get_available_branches()
		update_data['remote_url'], error_msg = get_remote_url()
		update_data['remote_version'], error_msg = get_remote_version()

		return { 'check_success' : avail_updates_struct['success'],
				 'version' : settings['versions']['server'],
				 'branches' : update_data['branches'],
				 'branch_target' : update_data['branch_target'],
				 'remote_url' : update_data['remote_url'],
				 'remote_version' : update_data['remote_version'],
				 'commits_behind' : commits_behind,
				 'logs_result' : logs_result,
				 'error_message' : error_msg }
	else:
		return {'response': {'result':'error', 'message':'Error: Recieved request without valid action'}}

@socketio.on('post_app_data')
def post_app_data(action=None, type=None, json_data=None):
	global settings

	if json_data is not None:
		request = json.loads(json_data)
	else:
		request = {''}

	if action == 'update_action':
		if type == 'settings':
			for key in request.keys():
				if key in settings.keys():
					settings = _deep_dict_update(settings, request)
					write_settings(settings)
					return {'response': {'result':'success'}}
				else:
					return {'response': {'result':'error', 'message':'Error: Key not found in settings'}}
		elif type == 'control':
			control = read_control()
			for key in request.keys():
				if key in control.keys():
					control = _deep_dict_update(control, request)
					write_control(control)
					return {'response': {'result':'success'}}
				else:
					return {'response': {'result':'error', 'message':'Error: Key not found in control'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Recieved request without valid type'}}

	elif action == 'admin_action':
		if type == 'clear_history':
			write_log('Clearing History Log.')
			read_history(0, flushhistory=True)
			return {'response': {'result':'success'}}
		elif type == 'clear_events':
			write_log('Clearing Events Log.')
			os.system('rm /tmp/events.log')
			return {'response': {'result':'success'}}
		elif type == 'clear_pelletdb':
			write_log('Clearing Pellet Database.')
			os.system('rm pelletdb.json')
			return {'response': {'result':'success'}}
		elif type == 'clear_pelletdb_log':
			pelletdb = read_pellet_db()
			pelletdb['log'].clear()
			write_pellet_db(pelletdb)
			write_log('Clearing Pellet Database Log.')
			return {'response': {'result':'success'}}
		elif type == 'factory_defaults':
			read_history(0, flushhistory=True)
			read_control(flush=True)
			os.system('rm settings.json')
			settings = default_settings()
			control = default_control()
			write_settings(settings)
			write_control(control)
			write_log('Resetting Settings, Control, History to factory defaults.')
			return {'response': {'result':'success'}}
		elif type == 'reboot':
			write_log("Admin: Reboot")
			os.system("sleep 3 && sudo reboot &")
			return {'response': {'result':'success'}}
		elif type == 'shutdown':
			write_log("Admin: Shutdown")
			os.system("sleep 3 && sudo shutdown -h now &")
			return {'response': {'result':'success'}}
		elif type == 'restart':
			write_log("Admin: Restart Server")
			restart_scripts()
			return {'response': {'result':'success'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Recieved request without valid type'}}

	elif action == 'units_action':
		if type == 'f_units' and settings['globals']['units'] == 'C':
			settings = convert_settings_units('F', settings)
			write_settings(settings)
			control = read_control()
			control['updated'] = True
			control['units_change'] = True
			write_control(control)
			write_log("Changed units to Fahrenheit")
			return {'response': {'result':'success'}}
		elif type == 'c_units' and settings['globals']['units'] == 'F':
			settings = convert_settings_units('C', settings)
			write_settings(settings)
			control = read_control()
			control['updated'] = True
			control['units_change'] = True
			write_control(control)
			write_log("Changed units to Celsius")
			return {'response': {'result':'success'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Units could not be changed'}}

	elif action == 'remove_action':
		if type == 'onesignal_device':
			if 'onesignal_player_id' in request['onesignal_device']:
				device = request['onesignal_device']['onesignal_player_id']
				if device in settings['onesignal']['devices']:
					settings['onesignal']['devices'].pop(device)
				write_settings(settings)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Device not specified'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Remove type not found'}}

	elif action == 'pellets_action':
		pelletdb = read_pellet_db()
		if type == 'load_profile':
			if 'profile' in request['pellets_action']:
				pelletdb['current']['pelletid'] = request['pellets_action']['profile']
				now = str(datetime.datetime.now())
				now = now[0:19]
				pelletdb['current']['date_loaded'] = now
				pelletdb['current']['est_usage'] = 0
				pelletdb['log'][now] = request['pellets_action']['profile']
				control = read_control()
				control['hopper_check'] = True
				write_control(control)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Profile not included in request'}}
		elif type == 'hopper_check':
			control = read_control()
			control['hopper_check'] = True
			write_control(control)
			return {'response': {'result':'success'}}
		elif type == 'edit_brands':
			if 'delete_brand' in request['pellets_action']:
				delBrand = request['pellets_action']['delete_brand']
				if delBrand in pelletdb['brands']:
					pelletdb['brands'].remove(delBrand)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			elif 'new_brand' in request['pellets_action']:
				newBrand = request['pellets_action']['new_brand']
				if newBrand not in pelletdb['brands']:
					pelletdb['brands'].append(newBrand)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Function not specified'}}
		elif type == 'edit_woods':
			if 'delete_wood' in request['pellets_action']:
				delWood = request['pellets_action']['delete_wood']
				if delWood in pelletdb['woods']:
					pelletdb['woods'].remove(delWood)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			elif 'new_wood' in request['pellets_action']:
				newWood = request['pellets_action']['new_wood']
				if newWood not in pelletdb['woods']:
					pelletdb['woods'].append(newWood)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Function not specified'}}
		elif type == 'add_profile':
			profile_id = ''.join(filter(str.isalnum, str(datetime.datetime.now())))
			pelletdb['archive'][profile_id] = {
				'id' : profile_id,
				'brand' : request['pellets_action']['brand_name'],
				'wood' : request['pellets_action']['wood_type'],
				'rating' : request['pellets_action']['rating'],
				'comments' : request['pellets_action']['comments'] }
			if request['pellets_action']['add_and_load']:
				pelletdb['current']['pelletid'] = profile_id
				control = read_control()
				control['hopper_check'] = True
				write_control(control)
				now = str(datetime.datetime.now())
				now = now[0:19]
				pelletdb['current']['date_loaded'] = now
				pelletdb['current']['est_usage'] = 0
				pelletdb['log'][now] = profile_id
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
		if type == 'edit_profile':
			if 'profile' in request['pellets_action']:
				profile_id = request['pellets_action']['profile']
				pelletdb['archive'][profile_id]['brand'] = request['pellets_action']['brand_name']
				pelletdb['archive'][profile_id]['wood'] = request['pellets_action']['wood_type']
				pelletdb['archive'][profile_id]['rating'] = request['pellets_action']['rating']
				pelletdb['archive'][profile_id]['comments'] = request['pellets_action']['comments']
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Profile not included in request'}}
		if type == 'delete_profile':
			if 'profile' in request['pellets_action']:
				profile_id = request['pellets_action']['profile']
				if pelletdb['current']['pelletid'] == profile_id:
					return {'response': {'result':'error', 'message':'Error: Cannot delete current profile'}}
				else:
					pelletdb['archive'].pop(profile_id)
					for index in pelletdb['log']:
						if pelletdb['log'][index] == profile_id:
							pelletdb['log'][index] = 'deleted'
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Profile not included in request'}}
		elif type == 'delete_log':
			if 'log_item' in request['pellets_action']:
				delLog = request['pellets_action']['log_item']
				if delLog in pelletdb['log']:
					pelletdb['log'].pop(delLog)
				write_pellet_db(pelletdb)
				return {'response': {'result':'success'}}
			else:
				return {'response': {'result':'error', 'message':'Error: Function not specified'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Recieved request without valid type'}}

	elif action == 'timer_action':
		control = read_control()
		if type == 'start_timer':
			control['notify_req']['timer'] = True
			if control['timer']['paused'] == 0:
				now = time.time()
				control['timer']['start'] = now
				if 'hours_range' in request['timer_action'] and 'minutes_range' in request['timer_action']:
					seconds = request['timer_action']['hours_range'] * 60 * 60
					seconds = seconds + request['timer_action']['minutes_range'] * 60
					control['timer']['end'] = now + seconds
					control['notify_data']['timer_shutdown'] = request['timer_action']['timer_shutdown']
					control['notify_data']['timer_keep_warm'] = request['timer_action']['timer_keep_warm']
					write_log('Timer started.  Ends at: ' + _epoch_to_time(control['timer']['end']))
					write_control(control)
					return {'response': {'result':'success'}}
				else:
					return {'response': {'result':'error', 'message':'Error: Start time not specifed'}}
			else:
				now = time.time()
				control['timer']['end'] = (control['timer']['end'] - control['timer']['paused']) + now
				control['timer']['paused'] = 0
				write_log('Timer unpaused.  Ends at: ' + _epoch_to_time(control['timer']['end']))
				write_control(control)
				return {'response': {'result':'success'}}
		elif type == 'pause_timer':
			control['notify_req']['timer'] = False
			now = time.time()
			control['timer']['paused'] = now
			write_log('Timer paused.')
			write_control(control)
			return {'response': {'result':'success'}}
		elif type == 'stop_timer':
			control['notify_req']['timer'] = False
			control['timer']['start'] = 0
			control['timer']['end'] = 0
			control['timer']['paused'] = 0
			control['notify_data']['timer_shutdown'] = False
			control['notify_data']['timer_keep_warm'] = False
			write_log('Timer stopped.')
			write_control(control)
			return {'response': {'result':'success'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Recieved request without valid type'}}
	else:
		return {'response': {'result':'error', 'message':'Error: Recieved request without valid action'}}

@socketio.on('post_updater_data')
def updater_action(type='none', branch=None):

	if type == 'change_branch':
		if branch is not None:
			result, error_msg = set_branch(branch)
			message = f'Changing to {branch} branch \n'
			if error_msg == '':
				message += result
				restart_scripts()
				return {'response': {'result':'success', 'message': message }}
			else:
				return {'response': {'result':'error', 'message':'Error: ' + error_msg }}
		else:
			return {'response': {'result':'error', 'message':'Error: Branch not specified in request'}}

	elif type == 'do_update':
		if branch is not None:
			result, error_msg = do_update()
			message = f'Attempting update on {branch} \n'
			if error_msg == '':
				message += result
				restart_scripts()
				return {'response': {'result':'success', 'message': message }}
			else:
				return {'response': {'result':'error', 'message':'Error: ' + error_msg }}
		else:
			return {'response': {'result':'error', 'message':'Error: Branch not specified in request'}}

	elif type == 'update_remote_branches':
		if is_raspberry_pi():
			os.system('python3 %s %s &' % ('updater.py', '-r'))	 # Update branches from remote
			time.sleep(2)
			return {'response': {'result':'success', 'message': 'Branches successfully updated from remote' }}
		else:
			return {'response': {'result':'error', 'message': 'System is not a Raspberry Pi. Branches not updated.' }}
	else:
		return {'response': {'result':'error', 'message':'Error: Recieved request without valid action'}}

@socketio.on('post_restore_data')
def post_restore_data(type='none', filename='none', json_data=None):

	if type == 'settings':
		if filename != 'none':
			read_settings(filename=BACKUP_PATH+filename)
			restart_scripts()
			return {'response': {'result':'success'}}
		elif json_data is not None:
			write_settings(json.loads(json_data))
			return {'response': {'result':'success'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Filename or JSON data not supplied'}}

	elif type == 'pelletdb':
		if filename != 'none':
			read_pellet_db(filename=BACKUP_PATH+filename)
			return {'response': {'result':'success'}}
		elif json_data is not None:
			write_pellet_db(json.loads(json_data))
			return {'response': {'result':'success'}}
		else:
			return {'response': {'result':'error', 'message':'Error: Filename or JSON data not supplied'}}
	else:
		return {'response': {'result':'error', 'message':'Error: Recieved request without valid type'}}

'''
Main Program Start
'''
settings = read_settings()

if __name__ == '__main__':
	if is_raspberry_pi():
		socketio.run(app, host='0.0.0.0')
	else:
		socketio.run(app, host='0.0.0.0', debug=True)
	else:
		socketio.run(app, host='0.0.0.0')