@echo off
REM Web3-design launcher: same as run.bat, but the frontend starts with the new Web3 look switched on
REM (Web3 login page, animated overview banner). run.bat itself is unchanged.
REM Both use port 5173 (the backend only accepts that address), so close the normal one first.
REM Double-click this file, or run:  run-web3.bat
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
REM Start the backend only if it is not already running (for example from run.bat).
curl -s -o nul http://localhost:8000/docs
if errorlevel 1 (
    echo Starting backend on http://localhost:8000 ...
    start "Backend (FastAPI)" /D "%~dp0backend" cmd /k "..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
) else (
    echo Backend already running on http://localhost:8000
)

curl -s -o nul http://localhost:5173
if not errorlevel 1 (
    echo The normal dashboard is already running on port 5173.
    echo Close its "Frontend (Vite)" window, then run this file again.
    goto :fail
)
echo Starting Web3 frontend on http://localhost:5173 ...
start "Frontend Web3 (Vite)" /D "%~dp0frontend" cmd /k "set VITE_LOGIN_DESIGN=web3&& npm run dev -- --port 5173 --strictPort"

REM --- 4. Open the dashboard once the servers have had a moment to start ---
timeout /t 5 /nobreak >nul
start "" "http://localhost:5173/?web3#/overview"
echo.
echo Web3 dashboard: http://localhost:5173/?web3   API docs: http://localhost:8000/docs
exit /b 0

:fail
echo.
echo Something went wrong. Read the message above, then press any key to close.
pause >nul
exit /b 1
