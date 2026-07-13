$env:UV_CACHE_DIR = Join-Path $PSScriptRoot ".uv-cache"
Set-Location $PSScriptRoot
uv.exe run python manage.py runserver 127.0.0.1:8000
