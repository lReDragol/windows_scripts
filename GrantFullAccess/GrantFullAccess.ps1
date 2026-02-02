# Установите имя пользователя, которому нужно предоставить права
# Пример: "Drago"
$targetUser = "Drago"

# Проверка аргумента (путь к папке)
if ($args.Count -eq 0) {
    Write-Host "Не указан путь к папке. Скрипт завершен." -ForegroundColor Red
    exit
}

# Получение пути из аргумента
$folderPath = $args[0]

# Проверка существования папки
if (-not (Test-Path $folderPath)) {
    Write-Host "Указанный путь не существует: $folderPath. Скрипт завершен." -ForegroundColor Red
    exit
}

# Функция для назначения владельца
function Set-Owner {
    param (
        [string]$path,
        [string]$user
    )
    try {
        Write-Host "==> Назначаю владельца $user для $path" -ForegroundColor Yellow
        takeown.exe /f $path /r /d y
        Write-Host "✅ Владелец успешно изменен на $user для $path" -ForegroundColor Green
    } catch {
        Write-Host "❌ Не удалось изменить владельца для: $path" -ForegroundColor Red
    }
}

# Функция для предоставления полного доступа
function Grant-FullAccess {
    param (
        [string]$path,
        [string]$user
    )
    try {
        Write-Host "==> Предоставляю полный доступ $user для $path" -ForegroundColor Yellow
        icacls $path /grant "$user:F" /T /C
        Write-Host "✅ Полный доступ успешно предоставлен $user для $path" -ForegroundColor Green
    } catch {
        Write-Host "❌ Не удалось предоставить полный доступ для: $path" -ForegroundColor Red
    }
}

# Шаг 1: Назначить владельца
Set-Owner -path $folderPath -user $targetUser

# Шаг 2: Предоставить полный доступ
Grant-FullAccess -path $folderPath -user $targetUser

# Шаг 3: Обработка вложенных элементов
Write-Host "==> Обработка вложенных элементов: $folderPath" -ForegroundColor Cyan
Get-ChildItem -Path $folderPath -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
    $itemPath = $_.FullName
    if (Test-Path $itemPath) {
        Write-Host "🔄 Обработка элемента: $itemPath" -ForegroundColor Magenta
        Set-Owner -path $itemPath -user $targetUser
        Grant-FullAccess -path $itemPath -user $targetUser
    }
}

Write-Host "✅ Все операции завершены. Владелец назначен, права предоставлены." -ForegroundColor Green
