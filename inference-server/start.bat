@echo off
cd /d "%~dp0"
call "C:\Program Files (x86)\Intel\oneAPI\setvars.bat"
rem set HF_TOKEN from environment or .env — do not hardcode here
if "%HF_TOKEN%"=="" echo WARNING: HF_TOKEN not set
set GPU_QUEUE_THROTTLE=LOW
python -m uvicorn src.server:app --host 0.0.0.0 --port 11434 --workers 1
