@echo off
REM 밤 자동 수집. 작업 스케줄러에 이 파일을 등록하면 매일 알아서 돕니다.
REM   setx YOUTUBE_API_KEY "발급받은키"
REM   schtasks /create /tn "korea-refs" /tr "%~f0" /sc daily /st 05:00
setlocal
cd /d "%~dp0.."
if "%YOUTUBE_API_KEY%"=="" (
  echo YOUTUBE_API_KEY 가 없습니다. setx YOUTUBE_API_KEY "키" 먼저 실행하세요.
  exit /b 1
)
node tools\refs-nightly.mjs %*
