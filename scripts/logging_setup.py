from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config_loader import LoggingConfig


def setup_logging(repo_root: Path, cfg: LoggingConfig) -> logging.Logger:
    """
    Configurar sistema de logging con rotación de archivos.
    
    Características:
    - Logs rotativos (max 1MB por archivo, 5 backups)
    - Salida simultánea a archivo y consola
    - Formato personalizado con timestamp
    """
    # Resolver ruta del log
    log_path = (repo_root / cfg.file).resolve() if not Path(cfg.file).is_absolute() else Path(cfg.file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("cnc_version_control")
    logger.setLevel(getattr(logging, cfg.level, logging.INFO))

    # Formato del log: timestamp | nivel | mensaje
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Handler de archivo rotativo (evitar duplicados en re-inicialización)
    if not any(isinstance(h, RotatingFileHandler) and getattr(h, "baseFilename", "") == str(log_path) for h in logger.handlers):
        file_handler = RotatingFileHandler(
            filename=str(log_path),
            maxBytes=cfg.max_bytes,
            backupCount=cfg.backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    # Handler de consola (evitar duplicados)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    logger.propagate = False  # No propagar a logger raíz
    return logger
