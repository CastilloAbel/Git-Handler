# CNC Version Control - Sistema Automático de Versionado

Sistema transparente de control de versiones para programas CNC (archivos `.nc` y `.txt`) que monitorea cambios y los sincroniza automáticamente con GitHub.

---

## 📋 Características

- **Monitoreo automático**: Detecta creación, modificación, eliminación y renombrado de archivos
- **Debouncing inteligente**: Agrupa cambios rápidos para evitar commits excesivos
- **Commits descriptivos**: Incluye acción, ruta, fecha/hora y usuario/máquina
- **Sincronización automática**: Pull y push a GitHub sin intervención manual
- **Manejo de conflictos**: Sistema de bloqueo para resolver conflictos de manera segura
- **Validación de archivos**: Filtra por extensión, profundidad y patrones numéricos
- **Logging detallado**: Registro rotativo de todas las operaciones

---

## 🎯 ¿Qué hace?

1. **Monitorea** el directorio de programas CNC hasta `max_depth` niveles de profundidad
2. **Espera** `debounce_seconds` después del último cambio antes de commitear
3. **Crea commits** automáticos con metadata completa (acción, ruta, fecha, usuario)
4. **Sincroniza** con GitHub mediante pull (opcional) y push automáticos
5. **Detecta conflictos** y crea bloqueo hasta que un administrador los resuelva

---

## 🔧 Requisitos

### Sistema Operativo
- Debian/Ubuntu (o cualquier distribución Linux con systemd)
- Python 3.8 o superior
- Git 2.x instalado y disponible en PATH

### Permisos
- Acceso de lectura/escritura al directorio de programas CNC
- Acceso a Internet para sincronización con GitHub

---

## 📦 Instalación

### 1. Preparar el entorno

```bash
# Actualizar sistema
sudo apt update && sudo apt upgrade -y

# Instalar dependencias del sistema
sudo apt install -y git python3 python3-pip python3-venv

# Crear directorio para el proyecto
sudo mkdir -p /opt/cnc-version-control
sudo chown $USER:$USER /opt/cnc-version-control
cd /opt/cnc-version-control
```

### 2. Clonar o copiar el proyecto

```bash
# Opción A: Clonar desde repositorio
git clone https://github.com/TU_USUARIO/cnc-version-control.git .

# Opción B: Copiar archivos manualmente
# Asegúrate de copiar toda la estructura de carpetas:
# - cnc/scripts/
# - cnc/config.yml
# - cnc/requirements.txt
```

### 3. Inicializar el repositorio de programas CNC

```bash
# Ir al directorio donde estarán los programas CNC
# (ajusta la ruta según tu configuración)
cd /ruta/a/PROGRAMAS-CNC

# Inicializar repositorio Git
git init

# Configurar usuario Git local (para commits)
git config user.name "CNC Monitor"
git config user.email "cnc@tuempresa.com"

# Agregar remoto de GitHub
git remote add origin https://github.com/TU_USUARIO/PROGRAMAS-CNC.git

# Crear commit inicial
touch README.md
echo "# Programas CNC" > README.md
git add README.md
git commit -m "Commit inicial"

# Push inicial (requiere autenticación)
git push -u origin main
```

### 4. Configurar credenciales de GitHub

#### Opción A: Usando Token Personal (Recomendado para servidores)

```bash
# Crear archivo .env en la raíz del proyecto de monitoreo
cd /opt/cnc-version-control/cnc
nano .env
```

Agregar:
```bash
GITHUB_TOKEN=ghp_TU_TOKEN_AQUI
```

**Generar token en GitHub**:
1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token → Seleccionar scopes: `repo` (acceso completo)
3. Copiar token y pegarlo en `.env`

#### Opción B: Usando Git Credential Helper

```bash
# Configurar almacenamiento de credenciales
git config --global credential.helper store

# Al hacer el primer push/pull, Git pedirá credenciales
# y las guardará automáticamente
```

### 5. Configurar el sistema

```bash
cd /opt/cnc-version-control/cnc
nano config.yml
```

**Parámetros principales**:

```yaml
# Ruta al repositorio Git (donde están los programas CNC)
repo_root: /ruta/a/PROGRAMAS-CNC

# Carpeta a monitorear dentro del repo (. = raíz)
programs_dir: ./

# Profundidad máxima de subdirectorios
max_depth: 5

# Extensiones permitidas
extensions: [".nc", ".txt"]

# Segundos de espera después del último cambio
debounce_seconds: 2

# Patrones de archivos a ignorar
ignore_patterns:
  - "*.tmp"
  - "*.bak"
  - ".git"
  - "./logs/*"

# Configuración del observador
watcher:
  use_polling: true  # true para NFS/redes compartidas

# Configuración de Git
git:
  branch: main
  remote_name: origin
  pull_before_push: true
  pull_strategy: rebase  # rebase | merge | ff-only
  push_after_commit: true
  max_push_retries: 5
  retry_backoff_seconds: 10
  token_env: GITHUB_TOKEN
  remote_url: "https://github.com/TU_USUARIO/PROGRAMAS-CNC.git"

# Identidad para commits
identity:
  user_label: "${HOSTNAME}"  # Usa nombre del servidor
  author_name: ""  # Opcional: nombre del autor
  author_email: ""  # Opcional: email del autor
```

### 6. Instalar dependencias Python

```bash
cd /opt/cnc-version-control/cnc

# Crear entorno virtual
python3 -m venv .venv

# Activar entorno
source .venv/bin/activate

# Instalar dependencias
pip install -r requirements.txt
```

---

## 🚀 Ejecución

### Modo Manual (para pruebas)

```bash
cd /opt/cnc-version-control/cnc
source .venv/bin/activate

# Ejecución normal
python -m scripts.monitor --config config.yml

# Auto-prueba (crea archivo de prueba)
python -m scripts.monitor --config config.yml --self-test

# Ejecución temporal (60 segundos)
python -m scripts.monitor --config config.yml --run-seconds 60
```

### Modo Servicio (Producción con systemd)

#### 1. Crear archivo de servicio

```bash
sudo nano /etc/systemd/system/cnc-monitor.service
```

Contenido:
```ini
[Unit]
Description=CNC Version Control Monitor
After=network.target

[Service]
Type=simple
User=TU_USUARIO
WorkingDirectory=/opt/cnc-version-control/cnc
Environment="PATH=/opt/cnc-version-control/cnc/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/opt/cnc-version-control/cnc/.venv/bin/python -m scripts.monitor --config /opt/cnc-version-control/cnc/config.yml
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 2. Habilitar e iniciar servicio

```bash
# Recargar configuración de systemd
sudo systemctl daemon-reload

# Habilitar inicio automático
sudo systemctl enable cnc-monitor.service

# Iniciar servicio
sudo systemctl start cnc-monitor.service

# Verificar estado
sudo systemctl status cnc-monitor.service
```

#### 3. Comandos útiles del servicio

```bash
# Ver logs en tiempo real
sudo journalctl -u cnc-monitor.service -f

# Reiniciar servicio
sudo systemctl restart cnc-monitor.service

# Detener servicio
sudo systemctl stop cnc-monitor.service

# Ver últimas 100 líneas de log
sudo journalctl -u cnc-monitor.service -n 100
```

---

## 📊 Logs y Monitoreo

### Ubicación de logs

```bash
# Log principal (rotativo, max 1MB, 5 backups)
/opt/cnc-version-control/cnc/logs/cnc-version-control.log

# Logs del sistema (systemd)
sudo journalctl -u cnc-monitor.service
```

### Ver logs en tiempo real

```bash
# Log de la aplicación
tail -f /opt/cnc-version-control/cnc/logs/cnc-version-control.log

# Log del servicio systemd
sudo journalctl -u cnc-monitor.service -f
```

---

## 🔒 Manejo de Conflictos

### ¿Qué sucede cuando hay un conflicto?

1. El sistema detecta el conflicto durante `pull`
2. Crea archivo de bloqueo: `/opt/cnc-version-control/cnc/logs/CONFLICT.lock`
3. **Deja de hacer push** hasta que se resuelva
4. Continúa detectando cambios y creando commits locales

### Cómo resolver conflictos

```bash
cd /ruta/a/PROGRAMAS-CNC

# Ver estado del repositorio
git status

# Resolver conflictos manualmente
# (editar archivos en conflicto)

# Marcar como resueltos
git add archivo-en-conflicto.nc
git rebase --continue
# O si usas merge:
git commit

# Push manual
git push origin main

# Eliminar archivo de bloqueo
rm /opt/cnc-version-control/cnc/logs/CONFLICT.lock

# El sistema reanudará push automático
```

---

## 🧪 Pruebas

### Prueba básica

```bash
cd /ruta/a/PROGRAMAS-CNC

# Crear archivo de prueba
echo "G00 X0 Y0 Z0" > 12345.nc

# Verificar en logs que se detectó
tail -f /opt/cnc-version-control/cnc/logs/cnc-version-control.log

# Verificar en GitHub que se creó el commit
```

### Auto-prueba integrada

```bash
cd /opt/cnc-version-control/cnc
source .venv/bin/activate
python -m scripts.monitor --config config.yml --self-test --run-seconds 30
```

---

## 🛠️ Solución de Problemas

### El servicio no inicia

```bash
# Ver errores detallados
sudo journalctl -u cnc-monitor.service -n 50 --no-pager

# Verificar permisos
ls -la /opt/cnc-version-control/cnc

# Verificar que .env existe
cat /opt/cnc-version-control/cnc/.env
```

### No detecta cambios

- Verificar que `programs_dir` en `config.yml` apunta al directorio correcto
- Si usas NFS/red compartida, asegurar `use_polling: true`
- Verificar permisos de lectura en el directorio

### Falla el push

- Verificar que `GITHUB_TOKEN` es válido
- Verificar conectividad: `ping github.com`
- Revisar `git.remote_url` en `config.yml`
- Ver logs: `sudo journalctl -u cnc-monitor.service | grep -i push`

### Commits duplicados

- Aumentar `debounce_seconds` en `config.yml` (ej: de 2 a 5)
- Reiniciar servicio: `sudo systemctl restart cnc-monitor.service`
