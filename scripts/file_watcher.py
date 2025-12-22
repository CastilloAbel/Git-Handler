from __future__ import annotations

import fnmatch
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

try:
    from watchdog.observers.polling import PollingObserver
except Exception:
    PollingObserver = None  # type: ignore


# Patrón para validar nombres de archivo: deben ser números seguidos de .nc o .txt
# Ejemplo válido: 12345.nc, 999.txt
NAME_PATTERN = re.compile(r"^\d+\.(nc|txt)$", re.IGNORECASE)


@dataclass(frozen=True)
class ChangeEvent:
    """Evento de cambio detectado en el sistema de archivos"""
    action: str  # CREADO | MODIFICADO | ELIMINADO | RENOMBRADO
    abs_path: Path  # Ruta absoluta del archivo afectado
    abs_path_src: Optional[Path] = None  # Ruta origen (solo para renombrados)


class _Debouncer:
    """
    Implementa un mecanismo de debouncing para evitar múltiples commits
    por el mismo archivo cuando se producen varios cambios rápidos.
    
    Funciona acumulando eventos y emitiéndolos solo después de que
    transcurra el tiempo de debounce sin nuevos cambios.
    """
    def __init__(self, debounce_seconds: int, emit: Callable[[ChangeEvent], None]):
        self._debounce_seconds = max(0, int(debounce_seconds))
        self._emit = emit  # Función callback para emitir eventos procesados
        self._lock = threading.Lock()  # Lock para acceso thread-safe
        # Diccionario: clave = ruta del archivo, valor = (evento, timestamp)
        self._pending: Dict[str, Tuple[ChangeEvent, float]] = {}
        self._stop = threading.Event()  # Señal para detener el hilo
        # Hilo daemon que procesa eventos pendientes
        self._thread = threading.Thread(target=self._run, name="debouncer", daemon=True)
        self._thread.start()

    def submit(self, event: ChangeEvent) -> None:
        """Agregar un evento a la cola de debouncing"""
        now = time.time()
        with self._lock:
            # Actualizar el timestamp del archivo (reemplaza eventos anteriores)
            self._pending[str(event.abs_path)] = (event, now)

    def close(self) -> None:
        """Detener el hilo de debouncing"""
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        """Bucle principal del hilo de debouncing"""
        while not self._stop.is_set():
            time.sleep(0.5)  # Verificar cada medio segundo
            now = time.time()
            to_emit = []
            with self._lock:
                # Buscar eventos que ya cumplieron el tiempo de debounce
                for key, (evt, last_ts) in list(self._pending.items()):
                    if now - last_ts >= self._debounce_seconds:
                        to_emit.append(evt)
                        self._pending.pop(key, None)
            # Emitir eventos fuera del lock para evitar bloqueos
            for evt in to_emit:
                self._emit(evt)


class FileWatcher:
    """
    Observador de archivos que monitorea cambios en el directorio de programas CNC.
    Filtra archivos por extensión, profundidad y patrones de ignorar.
    """
    def __init__(
        self,
        root_dir: Path,
        max_depth: int,
        extensions: list[str],
        ignore_patterns: list[str],
        debounce_seconds: int,
        use_polling: bool,
        logger,
        on_change: Callable[[ChangeEvent], None],
    ):
        self._root_dir = root_dir.resolve()
        self._max_depth = max(0, int(max_depth))  # Profundidad máxima del árbol
        self._extensions = {e.lower() for e in extensions}  # Extensiones permitidas
        self._ignore_patterns = list(ignore_patterns)  # Patrones a ignorar
        self._logger = logger
        self._on_change = on_change  # Callback al detectar cambio
        self._debounce_seconds = max(0, int(debounce_seconds))
        
        # Inicializar el debouncer
        self._debouncer = _Debouncer(debounce_seconds=self._debounce_seconds, emit=self._on_change)

        # Elegir entre observador nativo o polling
        # Polling es más confiable en redes compartidas/NFS
        if use_polling and PollingObserver is not None:
            self._observer = PollingObserver()
        else:
            self._observer = Observer()

    def start(self) -> None:
        """Iniciar la observación del directorio"""
        handler = _Handler(self)
        # recursive=True: observar subdirectorios
        self._observer.schedule(handler, str(self._root_dir), recursive=True)
        self._observer.start()
        self._logger.info(f"Watching: {self._root_dir}")

    def stop(self) -> None:
        """Detener la observación"""
        try:
            self._observer.stop()
            self._observer.join(timeout=5)
        finally:
            self._debouncer.close()

    def _should_track(self, path: Path) -> bool:
        """
        Determinar si un archivo debe ser rastreado según:
        - Profundidad en el árbol de directorios
        - Patrones de ignorar
        - Extensión del archivo
        - Patrón de nombre numérico
        """
        try:
            # Calcular ruta relativa al directorio raíz
            rel = path.resolve().relative_to(self._root_dir)
        except Exception:
            return False

        # Verificar profundidad: número de carpetas bajo la raíz
        depth = len(rel.parts) - 1
        if depth > self._max_depth:
            return False

        basename = path.name

        # Verificar patrones de ignorar (ej: *.tmp, .git/*)
        for pat in self._ignore_patterns:
            if fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(str(rel).replace(os.sep, "/"), pat):
                return False

        # Verificar extensión
        if path.suffix.lower() not in self._extensions:
            return False

        # Validación de patrón numérico (advertencia solamente, no bloquea)
        if not NAME_PATTERN.match(basename):
            self._logger.warning(f"Filename does not match numeric pattern: {basename}")

        return True

    def _submit(self, evt: ChangeEvent) -> None:
        """Enviar evento al debouncer"""
        # Registrar inmediatamente para feedback en tiempo real
        self._logger.info(
            f"Queued: {evt.action} {evt.abs_path} (debounce {self._debounce_seconds}s)"
        )
        self._debouncer.submit(evt)


class _Handler(FileSystemEventHandler):
    """
    Manejador de eventos del sistema de archivos.
    Convierte eventos de watchdog en ChangeEvents y los envía al debouncer.
    """
    def __init__(self, watcher: FileWatcher):
        super().__init__()
        self._w = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        """Archivo creado"""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._w._should_track(path):
            self._w._submit(ChangeEvent(action="CREADO", abs_path=path))

    def on_modified(self, event: FileSystemEvent) -> None:
        """Archivo modificado"""
        if event.is_directory:
            return
        path = Path(event.src_path)
        if self._w._should_track(path):
            self._w._submit(ChangeEvent(action="MODIFICADO", abs_path=path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        """Archivo eliminado"""
        if event.is_directory:
            return
        path = Path(event.src_path)
        # Para eliminaciones no podemos resolver relative_to, usar mejor esfuerzo
        if self._w._should_track(path) or path.suffix.lower() in self._w._extensions:
            self._w._submit(ChangeEvent(action="ELIMINADO", abs_path=path))

    def on_moved(self, event) -> None:
        """Archivo renombrado/movido"""
        if getattr(event, "is_directory", False):
            return
        src = Path(event.src_path)
        dest = Path(event.dest_path)
        # Tratar movimiento como evento único (Git puede detectar renombrados)
        if self._w._should_track(dest) or self._w._should_track(src):
            self._w._submit(ChangeEvent(action="RENOMBRADO", abs_path=dest, abs_path_src=src))
