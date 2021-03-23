#!/usr/bin/python3
# -*- coding: utf-8 -*-

__author__ = 'Richard J. Sears'
VERSION = "0.2 (2021-03-23)"

# Simple python script that helps to move my chia plots from my plotter to
# my nas. I wanted to use netcat as it was much faster on my 10GBe link than
# rsync and the servers are secure so I wrote this script to manage that
# move process.

# This is part of a two part process. On the NAS server there is drive_manager.py
# that manages the drives themselves and decides based on various criteria where
# the incoming plots will be placed. This script simply sends those plots when
# they are ready to send.

#   Updates
#   V0.2 2021-03-23
# - Added per_plot system notification function (send_new_plot_notification()
#   in chianas drive_manager.py and updated process_plot() and verify_plot_move()
#   to support the new function
# - Moved remote_mount lookup to happen before starting the plot move

import os
import sys
import subprocess
import socket
import rpyc
import logging
from system_logging import setup_logging
from system_logging import read_logging_config
sys.path.append('/home/chia/plot_manager')
import glob

# Let's do some housekeeping
nas_server = 'chianas01-internal' # Internal 10Gbe link, entry in /etc/hosts
plot_server = 'chiaplot01'

# Are we testing?
testing = False
if testing:
    plot_dir = '/home/chia/plot_manager/test_plots/'
    plot_size = 10000000
else:
    plot_dir = "/mnt/ssdraid/array0/"
    plot_size = 108644374730  # Based on K32 plot size

status_file = '/home/chia/plot_manager/transfer_job_running'
remote_checkfile = '/root/plot_manager/remote_transfer_is_active'



# Setup Module logging. Main logging is configured in system_logging.py
setup_logging()
level = read_logging_config('plot_manager_config', 'system_logging', 'log_level')
level = logging._checkLevel(level)
log = logging.getLogger(__name__)
log.setLevel(level)

# Not needed for now....
'''
# Setup to read and write to our config file:
def read_config_data(file, section, item):
    pathname = '/home/chia/plot_manager/' + file
    config.read(pathname)
    return config.get(section, item)

def update_config_data(file, section, item, value):
    pathname = '/home/chia/plot_manager/' + file
    config.read(pathname)
    cfgfile = open(pathname, 'w')
    config.set(section, item, value)
    config.write(cfgfile)
    cfgfile.close()
'''


# Look in our plot directory and get a list of plots. Do a basic
# size check for sanity's sake.
def get_list_of_plots():
    log.debug('get_list_of_plots() Started')
    try:
        plot_to_process = glob.glob(f'{plot_dir}/*.plot')[0].split('/')[4]
        log.debug(f'{plot_to_process}')
    except IndexError:
        log.debug(f'{plot_dir} is Empty: No Plots to Process. Will check again soon!')
        return False
    if os.path.getsize(plot_dir + plot_to_process) >= plot_size:
        log.info(f'We will process this plot next: {plot_to_process}')
        return (plot_to_process)
    else:
        log.debug('No Plots to Process')
        return False

# If we have plots and we are NOT currently transferring another plot and
# we are NOT testing the script, then process the next plot if there is
# one to process.
def process_plot():
    log.debug('process_plot() Started')
    if not process_control('check_status', 0):
        plot_to_process = get_list_of_plots()
        if plot_to_process and not testing:
            process_control('set_status', 'start')
            plot_path = plot_dir + plot_to_process
            log.info(f'Processing Plot: {plot_path}')
            try:
                remote_mount = str(subprocess.check_output(
                    ['ssh', nas_server, 'grep enclosure /root/plot_manager/plot_manager_config | awk {\'print $3\'}']).decode(
                    ('utf-8'))).strip("\n")
            except subprocess.CalledProcessError as e:
                log.warning(e.output)  # TODO Do something here...cannot go on...
                quit()
            log.debug(f'{nas_server} reports remote mount as {remote_mount}')
            subprocess.call(['/home/chia/plot_manager/send_plot.sh', plot_path, plot_to_process])
            try:
                subprocess.call(
                    ['ssh', nas_server, '/root/plot_manager/kill_nc.sh'])  # make sure all of the nc processes are dead on the receiving end
                log.debug('Remote nc kill called!')
            except subprocess.CalledProcessError as e:
                log.warning(e.output)
            if verify_plot_move(remote_mount, plot_path, plot_to_process):
                log.info('Plot Sizes Match, we have a good plot move!')
            else:
                log.debug('FAILURE - Plot sizes DO NOT Match - Exiting') # ToDo Do some notification here and then...?
                process_control('set_status', 'stop') #Set to stop so it will attempt to run again in the event we want to retry....
                quit()
            process_control('set_status', 'stop')
            os.remove(plot_path)
            log.info(f'Removing: {plot_path}')
        elif testing:
            log.debug('Testing Only - Nothing will be Done!')
        else:
            return
    else:
        return

# This assumes passwordless SSH between this host and remote host.
# Make changes as necessary! Checks to make sure we are not already
# doing a file transfer. If we are we just return. If not we go ahead
# and start the process notifying this local machine as well as the
# remote NAS that a file transfer will be in progress. Right now the
# remote notification does not do anything, but I have plans to use
# it for more control so I am leaving it here.

def process_control(command, action):
    log.debug(f'process_control() called with [{command}] and [{action}]')
    if command == 'set_status':
        if action == "start":
            if os.path.isfile(status_file):
                log.debug(f'Status File: [{status_file}] already exists!')
                return
            else:
                os.open(status_file, os.O_CREAT)
                try:
                    subprocess.check_output(['ssh', nas_server, 'touch %s' % remote_checkfile])
                except subprocess.CalledProcessError as e:
                    log.warning(e.output) #Nothing to add here yet as we are not using this function remotely (yet)
        if action == "stop":
            if os.path.isfile(status_file):
                os.remove(status_file)
                try:
                    subprocess.check_output(['ssh', nas_server, 'rm %s' % remote_checkfile])
                except subprocess.CalledProcessError as e:
                    log.warning(e.output) #Nothing to add here yet as we are not using this function remotely (yet)
            else:
                log.debug(f'Status File: [{status_file}] does not exist!')
                return
    elif command == 'check_status':
        if os.path.isfile(status_file):
            log.debug(f'Checkfile Exists, We are currently Running a Transfer, Exiting')
            return True
        else:
            log.debug(f'Checkfile Does Not Exist')
            return False
    else:
        return

def verify_plot_move(remote_mount, plot_path, plot_to_process):
    log.debug('verify_plot_move() Started')
    log.debug (f'Verifing: {nas_server}: {remote_mount}/{plot_to_process}')
    try:
        remote_plot_size = (int(subprocess.check_output(['ssh', nas_server, 'ls -al %s | awk {\'print $5\'}' % f'{remote_mount}/{plot_to_process}'])))
    except subprocess.CalledProcessError as e:
        log.warning(e.output) #TODO Do something here...cannot go on...
        quit()
    log.debug(f'Remote Plot Size Reported as: {remote_plot_size}')
    local_plot_size = os.path.getsize(plot_path)
    log.debug(f'Local Plot Size Reported as: {local_plot_size}')
    if remote_plot_size == local_plot_size:
        try:
            subprocess.check_output(['ssh', nas_server, 'touch %s' % new_plot_received])
        except subprocess.CalledProcessError as e:
            log.warning(e.output)  # Nothing to add here yet as we are not using this function remotely (yet)
        return True
    else:
        log.debug(f'Plot Size Mismatch!')
        return False

def get_remote_mount():
    plot = "/plot.test"
    remote_mount = str(subprocess.check_output(
        ['ssh', nas_server, 'grep enclosure /root/plot_manager/plot_manager_config | awk {\'print $3\'}']).decode(('utf-8'))).strip("\n")
    print ((remote_mount) +  (plot))

# Not used yet
def netcat(host, port, content):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, int(port)))
    s.sendall(content.encode())
    s.shutdown(socket.SHUT_WR)
    while True:
        data = s.recv(4096)
        if not data:
            break
        print(repr(data))
    s.close()

# Testing remote RPC (Must run server.py on nas server for this to work). Just Testing!
def get_remote_drive_info():
    chianas = rpyc.connect(nas_server, 18861, config={'allow_public_attrs': True})
    drive_manager = chianas.root.drive_manager
    log.debug(f"Number of plots space left: {drive_manager('space_free_plots', 'drive4')}")
    log.debug(f"Number of plots on drive:   {drive_manager('total_current_plots', 'drive4')}")
    log.debug(f"Drive space available (GB): {drive_manager('space_free', 'drive4')}")
    log.debug(f"The Drive Device is: {drive_manager('device', 'drive4')}")
    log.debug(f"The Drive Temperature is: {drive_manager('temperature', 'drive4')}")
    log.debug(f"The Drive Health Assessment has: {drive_manager('health', 'drive4')}ED")



def main():
    process_plot()

if __name__ == '__main__':
    main()

