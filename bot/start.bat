@echo off
echo Installing dependencies...
pip install -r requirements.txt

echo.
echo Starting Vera Bot on http://localhost:8080
echo.
echo Make sure ANTHROPIC_API_KEY is set in your environment or .env file.
echo.

REM Load .env if it exists
if exist .env (
    for /f "tokens=1,2 delims==" %%A in (.env) do (
        if not "%%A"=="" if not "%%A:~0,1%"=="#" set "%%A=%%B"
    )
)

uvicorn main:app --host 0.0.0.0 --port 8080
