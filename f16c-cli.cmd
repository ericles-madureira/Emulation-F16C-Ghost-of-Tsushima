@echo off
rem Linha de comando: f16c-cli scan^|patch^|restore ARQUIVO   (f16c-cli -h para ajuda)
"%~dp0python\python.exe" -X utf8 "%~dp0f16c_emu.py" %*
exit /b %ERRORLEVEL%
