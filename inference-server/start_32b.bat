@echo off
cd /d "%~dp0"
call "C:\Program Files (x86)\Intel\oneAPI\setvars.bat"
set HF_TOKEN=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
set OPENCLAW_MODELS_OVERRIDE=qwen2.5:32b
set GPU_QUEUE_THROTTLE=LOW
python -m uvicorn src.server:app --host 0.0.0.0 --port 11436 --workers 1
