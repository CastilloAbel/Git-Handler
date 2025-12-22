from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


def _expand_env_vars(value: str) -> str:
    """Expandir variables de entorno en strings (ej: ${COMPUTERNAME})"""
    return os.path.expandvars(value)


def _as_bool(value: Any, default: bool) -> bool:
    """Convertir valor a booleano con valor por defecto"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_int(value: Any, default: int) -> int:
    """Convertir valor a entero con valor por defecto"""
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


@dataclass(frozen=True)
class LoggingConfig:
    """Configuración del sistema de logging"""
    level: str = "INFO"  # Nivel de log: DEBUG, INFO, WARNING, ERROR
    file: str = "logs/cnc-version-control.log"  # Ruta del archivo de log
    max_bytes: int = 1048576  # Tamaño máximo por archivo (1MB)
    backup_count: int = 5  # Número de archivos de respaldo


@dataclass(frozen=True)
class WatcherConfig:
    """Configuración del observador de archivos"""
    use_polling: bool = False  # True: polling, False: observador nativo


@dataclass(frozen=True)
class GitConfig:
    """Configuración de operaciones Git"""
    branch: str = "main"  # Rama principal
    remote_name: str = "origin"  # Nombre del remoto
    pull_before_push: bool = True  # Hacer pull antes de push
    pull_strategy: str = "rebase"  # Estrategia: rebase | merge | ff-only
    push_after_commit: bool = True  # Push automático después de commit
    max_push_retries: int = 5  # Número máximo de reintentos de push
    retry_backoff_seconds: int = 10  # Segundos de espera entre reintentos
    token_env: str = "GITHUB_TOKEN"  # Variable de entorno con el token
    remote_url: str = ""  # URL del repositorio remoto


@dataclass(frozen=True)
class IdentityConfig:
    """Configuración de identidad del usuario/máquina"""
    user_label: str = "${COMPUTERNAME}"  # Etiqueta para commits (expande vars de entorno)
    author_name: str = ""  # Nombre del autor (opcional)
    author_email: str = ""  # Email del autor (opcional)


@dataclass(frozen=True)
class ConflictsConfig:
    """Configuración de manejo de conflictos"""
    lock_file: str = "logs/CONFLICT.lock"  # Ruta del archivo de bloqueo


@dataclass(frozen=True)
class AppConfig:
    """Configuración completa de la aplicación"""
    repo_root: Path  # Raíz del repositorio Git
    programs_dir: Path  # Directorio de programas CNC a monitorear
    max_depth: int  # Profundidad máxima del árbol de directorios
    extensions: List[str]  # Extensiones de archivo permitidas
    debounce_seconds: int  # Segundos de debounce
    ignore_patterns: List[str]  # Patrones de archivos a ignorar
    watcher: WatcherConfig
    logging: LoggingConfig
    git: GitConfig
    identity: IdentityConfig
    conflicts: ConflictsConfig


def load_config(config_path: Path) -> AppConfig:
    """
    Cargar configuración desde archivo YAML.
    Aplica valores por defecto y valida parámetros.
    """
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    # Resolver repo_root (puede ser relativo al config.yml)
    repo_root_str = _expand_env_vars(str(raw.get("repo_root", ".")))
    repo_root = (config_path.parent / repo_root_str).resolve() if not Path(repo_root_str).is_absolute() else Path(repo_root_str)

    # Auto-corrección: si config.yml está en scripts/ y repo_root apunta a scripts/,
    # corregir automáticamente al padre si ahí está el .git
    if config_path.parent.name.lower() == "scripts":
        if not (repo_root / ".git").exists() and (repo_root.parent / ".git").exists():
            repo_root = repo_root.parent

    # Resolver programs_dir (relativo a repo_root)
    programs_dir_str = _expand_env_vars(str(raw.get("programs_dir", "programs")))
    programs_dir = (repo_root / programs_dir_str).resolve() if not Path(programs_dir_str).is_absolute() else Path(programs_dir_str)

    # Validar y cargar max_depth
    max_depth = _as_int(raw.get("max_depth"), 5)
    if max_depth < 0:
        max_depth = 0

    # Cargar extensiones permitidas
    extensions = raw.get("extensions") or [".nc", ".txt"]
    extensions = [str(ext).strip().lower() for ext in extensions if str(ext).strip()]
    if not extensions:
        extensions = [".nc", ".txt"]

    # Validar debounce_seconds
    debounce_seconds = _as_int(raw.get("debounce_seconds"), 30)
    if debounce_seconds < 0:
        debounce_seconds = 0

    # Cargar patrones de ignorar
    ignore_patterns = raw.get("ignore_patterns") or []
    ignore_patterns = [str(p).strip() for p in ignore_patterns if str(p).strip()]

    # Cargar configuración del observador
    watcher_raw: Dict[str, Any] = raw.get("watcher") or {}
    watcher = WatcherConfig(use_polling=_as_bool(watcher_raw.get("use_polling"), False))

    # Cargar configuración de logging
    logging_raw: Dict[str, Any] = raw.get("logging") or {}
    logging_cfg = LoggingConfig(
        level=str(logging_raw.get("level", "INFO")).upper(),
        file=str(logging_raw.get("file", "logs/cnc-version-control.log")),
        max_bytes=_as_int(logging_raw.get("max_bytes"), 1048576),
        backup_count=_as_int(logging_raw.get("backup_count"), 5),
    )

    # Cargar configuración de Git
    git_raw: Dict[str, Any] = raw.get("git") or {}
    git_cfg = GitConfig(
        branch=str(git_raw.get("branch", "main")),
        remote_name=str(git_raw.get("remote_name", "origin")),
        pull_before_push=_as_bool(git_raw.get("pull_before_push"), True),
        pull_strategy=str(git_raw.get("pull_strategy", "rebase")).lower(),
        push_after_commit=_as_bool(git_raw.get("push_after_commit"), True),
        max_push_retries=_as_int(git_raw.get("max_push_retries"), 5),
        retry_backoff_seconds=_as_int(git_raw.get("retry_backoff_seconds"), 10),
        token_env=str(git_raw.get("token_env", "GITHUB_TOKEN")),
        remote_url=str(git_raw.get("remote_url", "")),
    )

    # Cargar configuración de identidad
    identity_raw: Dict[str, Any] = raw.get("identity") or {}
    identity = IdentityConfig(
        user_label=_expand_env_vars(str(identity_raw.get("user_label", "${COMPUTERNAME}"))),
        author_name=str(identity_raw.get("author_name", "")),
        author_email=str(identity_raw.get("author_email", "")),
    )

    # Cargar configuración de conflictos
    conflicts_raw: Dict[str, Any] = raw.get("conflicts") or {}
    conflicts = ConflictsConfig(lock_file=str(conflicts_raw.get("lock_file", "logs/CONFLICT.lock")))

    return AppConfig(
        repo_root=repo_root,
        programs_dir=programs_dir,
        max_depth=max_depth,
        extensions=extensions,
        debounce_seconds=debounce_seconds,
        ignore_patterns=ignore_patterns,
        watcher=watcher,
        logging=logging_cfg,
        git=git_cfg,
        identity=identity,
        conflicts=conflicts,
    )
