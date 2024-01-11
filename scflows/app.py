#!/usr/bin/env python

from flask import Flask, request, render_template, redirect, url_for
import json
from os.path import join, exists
from os import getcwd
from scflows.config import config
from scflows.cron import parsetabfiles, validate, savetabfiles, triggercrontab
from scflows.tools import get_tabfile_dir

app = Flask(__name__)

tabfile_dir=None
cronthread={}

@app.route('/', methods = ['GET', 'POST'])
def default():
    global tabfile_dir
    error = None
    if request.method == 'POST':
        request.get_data()
        tabfile_dir = request.form['path-tab-file']
        if not tabfile_dir or not exists(tabfile_dir):
            error = 'Not a valid path'
    else:
        if tabfile_dir == '' or tabfile_dir is None:
            if config.paths['public'].startswith('/'):
                prepend = ''
            else:
                prepend = getcwd()
            tabfile_dir=join(prepend, config.paths['public'])
    tabfiles = parsetabfiles(path=tabfile_dir)
    return render_template("jobs.html", tabfiles=tabfiles, defaultpath=tabfile_dir, error=error)

@app.route('/editjob/<tabfile>-<cron>', methods = ['POST', 'GET'])
def editjob(tabfile,cron,error=None):
    tabfiles=parsetabfiles(path=tabfile_dir)
    if request.method == 'POST':
        request.get_data()
        # Form input
        log=request.form["logfile-input"]
        task=request.form["task-input"]
        schedule=request.form["schedule-input"]
        if request.form.getlist("enabled-input") == ['on']: enabled=True
        else: enabled=False
        who=request.form["who-input"]
        # Validate
        error=validate(schedule, who, task, log)
        tabfiles[tabfile][cron]['logfile']=log
        tabfiles[tabfile][cron]['task']=task
        tabfiles[tabfile][cron]['schedule']=schedule
        tabfiles[tabfile][cron]['enabled']=enabled
        tabfiles[tabfile][cron]['who']=who
        # Check error
        if not error:
            savetabfiles(tabfiles=tabfiles, path=tabfile_dir)
            return redirect(url_for("default"))
    # Overwrite schedule to cope with lists or normal strings options
    tabfiles[tabfile][cron]['schedule'] = str(tabfiles[tabfile][cron]['schedule'])
    crondict=tabfiles[tabfile][cron]

    return render_template("editjob.html", tabfile=tabfile, cron=cron, crondict=crondict, error=error)

@app.route('/triggerjob/<tabfile>-<cron>', methods = ['POST'])
def triggerjob(tabfile,cron):
    global cronthread
    tabfiles=parsetabfiles(path=tabfile_dir)
    if request.method == 'POST':
        cronthread[cron] = triggercrontab(tabfile_dir,tabfile,cron)
        if cronthread[cron] == False:
            error = "Job could not run as it's not valid"
            return render_template("jobs.html", tabfiles=tabfiles, defaultpath=tabfile_dir, error=error)
        else:
            return redirect(url_for("logfile", tabfile=tabfile, cron=cron))

@app.route('/tabfiles/<tabfile>')
def tabfile(tabfile):
    tabfiles = parsetabfiles(path=tabfile_dir)
    tabpath = f"{tabfile_dir}/{tabfile}.tab"
    tab = []
    with open(tabpath, 'r') as file:
        _tab = file.readlines()
    for line in _tab:
        line = line.strip('\n')
        if line != '':
            tab.append(line)
    return render_template("file_viewer.html", file_type='tabfile', file=tab)

@app.route('/logfiles/<tabfile>-<cron>')
def logfile(tabfile, cron):
    global cronthread
    tabfiles = parsetabfiles(path=tabfile_dir)
    logfile = tabfiles[tabfile][cron]['logfile']
    log = []
    with open(logfile, 'r') as file:
        _log = file.readlines()
    for line in _log:
        line = line.strip('\n')
        if line != '':
            if '[31m' in line:
                line = line.replace('\x1b[31m', '<span style="color:#B22222">')
                line = line.replace('\x1b[0m', '</span>')
            if '[33m' in line:
                line = line.replace('\x1b[33m', '<span style="color:rgb(255,200,0)">')
                line = line.replace('\x1b[0m', '</span>')
            if '[32m' in line:
                line = line.replace('\x1b[32m', '<span style="color:#228B22">')
                line = line.replace('\x1b[0m', '</span>')
            log.append(line)
    if cron in cronthread:
        status = cronthread[cron].status
    else:
        status = 'Not running'
    return render_template("file_viewer.html", file_type='log', cron=cron, file=log, status = status)

@app.route('/jobfiles/<tabfile>-<cron>')
def taskfile(tabfile, cron):
    tabfiles = parsetabfiles(path=tabfile_dir)
    taskfile = tabfiles[tabfile][cron]['task'].split(' ')[0]
    task = []
    with open(taskfile, 'r') as file:
        _task = file.readlines()
    for line in _task:
        line = line.strip('\n')
        if line != '':
            task.append(line)
    return render_template("file_viewer.html", file_type='task', cron=cron, file=task)

if __name__ == '__main__':
   app.run(debug = True)
