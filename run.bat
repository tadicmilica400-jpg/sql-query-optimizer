@echo off
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py primer_ulaza.json %*
) else (
    python main.py primer_ulaza.json %*
)
