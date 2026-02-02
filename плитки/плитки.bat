@echo off

:menu
echo Выберите режим:
echo 1. Заблокировать перемещение плиток в меню "Пуск"
echo 2. Разблокировать перемещение плиток в меню "Пуск"
set /p choice=Введите цифру (1 или 2) и нажмите Enter:

if "%choice%"=="1" goto block
if "%choice%"=="2" goto unblock
echo Неверный выбор. Пожалуйста, введите 1 или 2.
goto menu

:block
echo Блокировка перемещения плиток в меню "Пуск"...
:: Останавливаем процесс проводника
taskkill /f /im explorer.exe
:: Отключаем возможность перемещать плитки в меню "Пуск"
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoChangeStartMenu /t REG_DWORD /d 1 /f
:: Запускаем процесс проводника
start explorer.exe
echo Перемещение плиток в меню "Пуск" заблокировано.
pause
goto end

:unblock
echo Разблокировка перемещения плиток в меню "Пуск"...
:: Останавливаем процесс проводника
taskkill /f /im explorer.exe
:: Включаем возможность перемещать плитки в меню "Пуск"
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\Explorer" /v NoChangeStartMenu /f
:: Запускаем процесс проводника
start explorer.exe
echo Перемещение плиток в меню "Пуск" разблокировано.
pause
goto end

:end
