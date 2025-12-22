from __future__ import annotations

import argparse
import signal
import time
from pathlib import Path

from .env_loader import apply_env_vars, load_env_file
from .config_loader import load_config
from .file_watcher import ChangeEvent, FileWatcher
from .logging_setup import setup_logging


def main() -> int:
    # Configurar argumentos de línea de comandos
    parser = argparse.ArgumentParser(description="Monitor de Control de Versiones CNC")
    parser.add_argument("--config", default="config.yml", help="Ruta al archivo config.yml")
    parser.add_argument(
        "--run-seconds",
        type=int,
        default=0,
        help="Para pruebas: ejecutar durante N segundos y salir (0 = ejecutar indefinidamente)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Para pruebas: crear/modificar un archivo .NC numérico después del inicio para validar detección/commit",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()

    # Cargar variables de entorno desde .env ANTES de importar GitPython
    # (GitPython lee las variables de entorno al importarse, por eso debe hacerse aquí)
    env_path = config_path.parent / ".env"
    apply_env_vars(load_env_file(env_path), overwrite=False)

    # Cargar la configuración desde config.yml
    cfg = load_config(config_path)

    # Configurar el sistema de logging
    logger = setup_logging(cfg.repo_root, cfg.logging)
    logger.info("Starting CNC Version Control")
    logger.info(f"Repo root: {cfg.repo_root}")
    logger.info(f"Programs dir: {cfg.programs_dir}")
    logger.info(f"Debounce seconds: {cfg.debounce_seconds}")
    logger.info(f"Watcher polling: {cfg.watcher.use_polling}")

    # Importación tardía para evitar que GitPython falle antes de que se aplique .env
    from .git_handler import CommitContext, GitHandler

    # Crear el directorio de programas si no existe
    if not cfg.programs_dir.exists():
        cfg.programs_dir.mkdir(parents=True, exist_ok=True)
        logger.warning(f"Created programs dir: {cfg.programs_dir}")

    # Inicializar el manejador de Git
    try:
        git_handler = GitHandler(
            repo_root=cfg.repo_root,
            programs_dir=cfg.programs_dir,
            git_cfg=cfg.git,
            identity=cfg.identity,
            conflicts=cfg.conflicts,
            logger=logger,
        )
    except Exception as exc:
        logger.error(str(exc))
        return 2

    def on_change(evt: ChangeEvent) -> None:
        logger.info(f"Detected: {evt.action} {evt.abs_path}")
        git_handler.handle_change(
            CommitContext(action=evt.action, abs_path=evt.abs_path, abs_path_src=evt.abs_path_src)
        )

    # Inicializar el observador de archivos
    watcher = FileWatcher(
        root_dir=cfg.programs_dir,
        max_depth=cfg.max_depth,
        extensions=cfg.extensions,
        ignore_patterns=cfg.ignore_patterns,
        debounce_seconds=cfg.debounce_seconds,
        use_polling=cfg.watcher.use_polling,
        logger=logger,
        on_change=on_change,
    )

    # Variable compartida para manejar señales de terminación
    stop = {"value": False}

    # Manejador de señales para detención limpia
    def _handle_sig(_sig, _frame):
        stop["value"] = True

    signal.signal(signal.SIGINT, _handle_sig)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _handle_sig)

    # Iniciar el observador
    watcher.start()

    # Modo de auto-prueba: crear un archivo de prueba
    if args.self_test:
        test_path = cfg.programs_dir / "99998.NC"
        try:
            test_path.parent.mkdir(parents=True, exist_ok=True)
            test_path.write_text(f"SELFTEST {time.time()}\n", encoding="utf-8")
            logger.info(f"Self-test wrote: {test_path}")
        except Exception as exc:
            logger.error(f"Self-test failed to write file: {exc}")

    # Bucle principal: esperar hasta que se reciba señal de terminación
    try:
        start_ts = time.time()
        while not stop["value"]:
            # Si se especificó tiempo de ejecución, verificar si ya transcurrió
            if args.run_seconds and (time.time() - start_ts) >= args.run_seconds:
                break
            time.sleep(1)
    finally:
        # Detener el observador de forma limpia
        watcher.stop()
        logger.info("Stopped")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
