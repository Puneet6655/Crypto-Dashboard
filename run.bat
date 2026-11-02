@echo off
REM One-click launcher: starts the FastAPI backend and the Vite frontend.
REM Double-click this file, or run:  run.bat
REM Close the two server windows (or press Ctrl+C in them) to stop everything.
setlocal
cd /d "%~dp0"

REM --- 1. Python environment (created and filled on the first run only) ---
if not exist ".venv\Scripts\python.exe" (
    echo Creating Python environment...
    python -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, httpx, psycopg, psycopg_pool, dotenv, networkx" 2>nul
if errorlevel 1 (
    echo Installing backend packages...
    ".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt || goto :fail
)

REM --- 2. Frontend packages (first run only) ---
if not exist "frontend\node_modules" (
    echo Installing frontend packages...
    pushd frontend
    call npm install || (popd & goto :fail)
    popd
)

REM --- 3. Start both servers, each in its own window ---
echo Starting backend on http://localhost:8000 ...
start "Backend (FastAPI)" /D "%~dp0backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"

echo Starting frontend on http://localhost:5173 ...
start "Frontend (Vite)" /D "%~dp0frontend" cmd /k "npm run dev"

REM --- 4. Open the dashboard once the servers have had a moment to start ---
timeout /t 5 /nobreak >nul
start "" http://localhost:5173
echo.
echo Dashboard: http://localhost:5173   API docs: http://localhost:8000/docs
exit /b 0

:fail
echo.
echo Something went wrong. Read the message above, then press any key to close.
pause >nul
exit /b 1
