from __future__ import annotations

from pathlib import Path
from typing import Dict


def load_env_file(env_path: Path) -> Dict[str, str]:
    """
    Cargador minimalista de archivos .env (sin dependencias externas).
    
    Soporta líneas como KEY=VALUE, ignora líneas en blanco y comentarios (#).
    No expande variables ni soporta valores multi-línea.
    """
    result: Dict[str, str] = {}
    if not env_path.exists() or not env_path.is_file():
        return result

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        # Ignorar líneas vacías y comentarios
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        # Dividir en clave=valor
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")  # Remover comillas
        if not key:
            continue
        result[key] = value

    return result


def apply_env_vars(pairs: Dict[str, str], overwrite: bool = False) -> None:
    """
    Aplicar variables de entorno al proceso actual.
    
    Args:
        pairs: Diccionario de variables clave=valor
        overwrite: Si True, sobrescribe variables existentes
    """
    import os

    for key, value in pairs.items():
        # Si overwrite=False, no sobrescribir variables existentes
        if not overwrite and key in os.environ:
            continue
        os.environ[key] = value
