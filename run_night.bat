@echo off
rem Task Scheduler launcher: anaconda python needs Library\bin on PATH for SSL
set "PATH=C:\Users\Denis\anaconda3;C:\Users\Denis\anaconda3\Library\bin;C:\Users\Denis\anaconda3\Scripts;C:\Users\Denis\anaconda3\DLLs;%PATH%"
cd /d D:\Cloude\Translation_simplify_app
C:\Users\Denis\anaconda3\python.exe night_run.py
