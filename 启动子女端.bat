@echo off
chcp 65001 >nul
rem 护院鹅子女端 · 一键启动（chcp 防中文路径在部分控制台代码页下解析失败）
rem 自动拉起后端（如未运行）并打开子女端独立窗口
cd /d "%~dp0"
set "PYW=C:\Users\User\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" tools\launch_guardian.py
