# GrantFullAccess

PowerShell-скрипт для Windows, который выдаёт полный доступ (права) к выбранному файлу/папке.

## Запуск вручную

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File ".\\GrantFullAccess\\GrantFullAccess.ps1" "C:\\Path\\To\\Folder"
```

## Вызов из контекстного меню (пример)

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& 'D:\\PowerShellScripts\\GrantFullAccess.ps1' '%V'"
```

## Примечания

- Обычно требуется запуск PowerShell **от имени администратора**.
- Будь аккуратен: выдача «полных прав» на системные папки может быть опасна.
