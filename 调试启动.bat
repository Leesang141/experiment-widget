@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY="

rem 1) 环境变量指定
if defined EXPERIMENT_WIDGET_PYTHON if exist "%EXPERIMENT_WIDGET_PYTHON%" set "PY=%EXPERIMENT_WIDGET_PYTHON%"

rem 2) 程序目录自带的便携 Python
if not defined PY if exist "%~dp0python\python.exe" set "PY=%~dp0python\python.exe"

rem 3) DSH 自带运行时
if not defined PY if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" set "PY=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if not defined PY for /d %%D in ("%USERPROFILE%\.dsh\dsh-runtimes\*") do (
    if not defined PY if exist "%%~fD\dependencies\python\python.exe" set "PY=%%~fD\dependencies\python\python.exe"
)

rem 4) PATH
if not defined PY for %%P in (python.exe) do if not "%%~$PATH:P"=="" set "PY=%%~$PATH:P"

if not defined PY (
    echo 没有找到 Python 运行环境，请设置 EXPERIMENT_WIDGET_PYTHON 指向 python.exe
    pause
    exit /b 1
)

echo 使用 Python: %PY%
"%PY%" run_widget.py %*
echo.
echo 小组件已退出（退出码 %errorlevel%）
pause
