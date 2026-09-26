@echo off
echo %*>>"%AF59_ARGS_LOG%"
if "%AF59_STUB_WRITE%"=="1" copy /b /y "%AF59_PAYLOAD%" "%AF59_PBO%" >nul
if not "%AF59_STUB_TEXT%"=="" echo %AF59_STUB_TEXT%
exit /b %AF59_STUB_EXIT%
