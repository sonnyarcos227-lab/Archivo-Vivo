@echo off
title Archivo Vivo
cd /d "%~dp0backend"
echo ========================================================
echo             ARCHIVO VIVO  -  ARRANQUE
echo ========================================================
echo.

rem -- 1. Python ---------------------------------------------
echo [1/6] Buscando Python...
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD ( where python >nul 2>&1 && set "PY_CMD=python" )
if not defined PY_CMD (
    echo [ERROR] No se encontro Python. Instalalo desde https://www.python.org/downloads/
    echo         y marca la casilla "Add python.exe to PATH".
    pause
    exit /b 1
)

rem -- Configuracion: backend\.env con la clave de Groq --------
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo        Se creo backend\.env a partir de .env.example
)
findstr /R /C:"^GROQ_API_KEY=gsk_" ".env" >nul
if errorlevel 1 (
    echo.
    echo [CLAVE] Falta tu clave de Groq. Se abrira backend\.env en el Bloc de notas:
    echo         pega la clave despues de GROQ_API_KEY= , guarda y cierra.
    echo         Es gratis en https://console.groq.com  ^(API Keys^)
    echo.
    notepad ".env"
    findstr /R /C:"^GROQ_API_KEY=gsk_" ".env" >nul
    if errorlevel 1 (
        echo [AVISO] Sigue sin clave: la app abrira, pero el chat no podra responder.
        pause
    )
)

rem -- 2. PostgreSQL + pgvector en Docker --------------------
echo [2/6] Encendiendo PostgreSQL + pgvector (Docker)...
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker no esta instalado. Descarga Docker Desktop: https://www.docker.com/products/docker-desktop/
    pause
    exit /b 1
)
docker info >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Desktop esta cerrado. Abrelo, espera a que diga "Engine running"
    echo         y vuelve a ejecutar run.bat
    pause
    exit /b 1
)
docker start pgvector >nul 2>&1
if errorlevel 1 (
    echo        Creando el contenedor pgvector por primera vez...
    docker run -d --name pgvector --restart unless-stopped -e POSTGRES_PASSWORD=postgres -p 5432:5432 -v pgvector_data:/var/lib/postgresql/data pgvector/pgvector:pg17
)
set /a TRIES=0
:esperar_pg
docker exec pgvector pg_isready -U postgres >nul 2>&1
if not errorlevel 1 goto pg_listo
set /a TRIES+=1
if %TRIES% geq 30 (
    echo [AVISO] PostgreSQL esta tardando en arrancar; se continua de todos modos.
    goto pg_listo
)
timeout /t 2 /nobreak >nul
goto esperar_pg
:pg_listo
echo        PostgreSQL listo.

rem -- 3. llama.cpp con EmbeddingGemma 2 ---------------------
echo [3/6] Encendiendo llama.cpp con EmbeddingGemma 2 (puerto 8081)...
curl -s http://127.0.0.1:8081/health >nul 2>&1
if not errorlevel 1 (
    echo        llama.cpp ya estaba encendido.
    goto llama_listo
)
where llama-server >nul 2>&1
if errorlevel 1 (
    echo [ERROR] No se encontro llama-server. Instala llama.cpp con:
    echo         winget install --id ggml.llamacpp --exact --source winget
    echo         y cierra y vuelve a abrir esta ventana.
    pause
    exit /b 1
)
start "llama.cpp - EmbeddingGemma 2" /min llama-server -hf unsloth/embeddinggemma-2-GGUF:UD-Q4_K_XL --embeddings --port 8081 -c 2048 -b 2048 -ub 2048 --device none -ngl 0
echo        Usa solo el procesador (la grafica integrada puede fallar con Vulkan).
echo        La primera vez descarga el modelo (unos 200 MB)...
set /a TRIES=0
:esperar_llama
curl -s http://127.0.0.1:8081/health >nul 2>&1
if not errorlevel 1 goto llama_listo
set /a TRIES+=1
if %TRIES% geq 150 (
    echo [AVISO] llama.cpp esta tardando; revisa la ventana minimizada "llama.cpp".
    goto llama_listo
)
timeout /t 2 /nobreak >nul
goto esperar_llama
:llama_listo
echo        EmbeddingGemma 2 listo.

rem -- 4. Dependencias ---------------------------------------
echo [4/6] Instalando dependencias (la primera vez tarda varios minutos)...
%PY_CMD% -m pip install -r requirements.txt --disable-pip-version-check -q

rem -- 5. Base de conocimiento a pgvector --------------------
echo [5/6] Preparando la base de conocimiento en PostgreSQL...
%PY_CMD% migrar_a_pgvector.py

rem -- 6. Servidor -------------------------------------------
echo [6/6] Iniciando Archivo Vivo en http://localhost:8000
echo        Recuerda tener Ollama abierto para leer imagenes y fotogramas.
echo        Para detenerlo presiona Ctrl+C en esta ventana.
echo.
start "" /min cmd /c "timeout /t 10 /nobreak >nul & start http://localhost:8000"
%PY_CMD% -m uvicorn main:app --reload --reload-dir . --host 0.0.0.0 --port 8000
pause
