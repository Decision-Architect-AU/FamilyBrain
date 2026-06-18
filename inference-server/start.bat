@echo off
cd /d "%~dp0"
call "C:\Program Files (x86)\Intel\oneAPI\setvars.bat"
set HF_TOKEN=your_token_here
python -m uvicorn src.server:app --host 0.0.0.0 --port 11434 --workers 1
