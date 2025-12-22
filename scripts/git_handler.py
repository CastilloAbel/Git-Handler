from __future__ import annotations

import os
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from git import Actor, Repo
from git.exc import GitCommandError, InvalidGitRepositoryError, NoSuchPathError

from .config_loader import ConflictsConfig, GitConfig, IdentityConfig


@dataclass(frozen=True)
class CommitContext:
    """Contexto de un cambio detectado para crear un commit"""
    action: str  # CREADO | MODIFICADO | ELIMINADO | RENOMBRADO
    abs_path: Path  # Ruta absoluta del archivo
    abs_path_src: Optional[Path] = None  # Ruta origen (solo para renombrados)


class GitHandler:
    """
    Manejador de todas las operaciones Git:
    - Staging (add/remove)
    - Commits con metadata
    - Pull con estrategias (rebase/merge/ff-only)
    - Push con reintentos
    - Detección y manejo de conflictos
    """
    def __init__(
        self,
        repo_root: Path,
        programs_dir: Path,
        git_cfg: GitConfig,
        identity: IdentityConfig,
        conflicts: ConflictsConfig,
        logger,
    ):
        self._repo_root = repo_root.resolve()
        self._programs_dir = programs_dir.resolve()
        self._git_cfg = git_cfg
        self._identity = identity
        self._conflicts = conflicts
        self._logger = logger
        
        # Ruta del archivo de bloqueo por conflictos
        self._lock_path = (self._repo_root / self._conflicts.lock_file).resolve() if not Path(self._conflicts.lock_file).is_absolute() else Path(self._conflicts.lock_file)
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)

        # Inicializar repositorio Git
        try:
            self._repo = Repo(str(self._repo_root))
        except (InvalidGitRepositoryError, NoSuchPathError) as exc:
            raise RuntimeError(
                f"repo_root is not a Git repository: {self._repo_root}. "
                "Run 'git init' in that folder (admin task) and set the remote (origin)."
            ) from exc

        # Configurar/actualizar la URL del remoto sin requerir Git CLI
        # La URL se mantiene sin token; la autenticación se inyecta en memoria
        if (self._git_cfg.remote_url or "").strip():
            url = self._git_cfg.remote_url.strip().rstrip("/")
            try:
                # Actualizar remoto si ya existe, o crearlo si no
                if self._git_cfg.remote_name in [r.name for r in self._repo.remotes]:
                    self._repo.git.remote("set-url", self._git_cfg.remote_name, url)
                else:
                    self._repo.create_remote(self._git_cfg.remote_name, url)
            except GitCommandError as exc:
                self._logger.warning(f"Failed to set remote URL for {self._git_cfg.remote_name}: {exc}")

        # Lock para operaciones Git (previene condiciones de carrera)
        self._op_lock = threading.Lock()

    def conflict_lock_exists(self) -> bool:
        """Verificar si existe el archivo de bloqueo por conflicto"""
        return self._lock_path.exists()

    def _write_conflict_lock(self, reason: str) -> None:
        """Crear archivo de bloqueo por conflicto"""
        msg = f"{datetime.now().isoformat()} | {reason}\n"
        self._lock_path.write_text(msg, encoding="utf-8")

    def _relative_repo_path(self, abs_path: Path) -> str:
        """Obtener ruta relativa al repositorio (para staging)"""
        rel = abs_path.resolve().relative_to(self._repo_root)
        return rel.as_posix()

    def _relative_programs_path(self, abs_path: Path) -> str:
        """Obtener ruta relativa al directorio de programas (para mensajes)"""
        try:
            rel = abs_path.resolve().relative_to(self._programs_dir)
            return rel.as_posix()
        except Exception:
            return abs_path.as_posix()

    def _actor(self) -> Optional[Actor]:
        """Crear objeto Actor para commits (si se configuró autor)"""
        if self._identity.author_name and self._identity.author_email:
            return Actor(self._identity.author_name, self._identity.author_email)
        return None

    def _machine_label(self) -> str:
        """Obtener etiqueta de usuario/máquina para metadata del commit"""
        return self._identity.user_label or os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname()

    def handle_change(self, ctx: CommitContext) -> None:
        """
        Manejar un cambio detectado:
        1. Stage (add/remove)
        2. Commit con metadata
        3. Sync & Push (si está habilitado)
        """
        with self._op_lock:  # Thread-safe
            try:
                self._stage(ctx)
                # Si no hay cambios en staging, salir
                if not self._repo.is_dirty(untracked_files=True):
                    return
                self._commit(ctx)
                if self._git_cfg.push_after_commit:
                    self._sync_and_push()
            except Exception as exc:
                self._logger.exception(f"Git operation failed: {exc}")

                # Problema común: remoto incorrecto o faltante
                if "Repository not found" in str(exc) or "not found" in str(exc).lower():
                    self._logger.error(
                        "If push/pull fails, set git.remote_url in config.yml to the correct GitHub repo URL "
                        "(example: https://github.com/USER/REPO.git)."
                    )

    def _stage(self, ctx: CommitContext) -> None:
        """Agregar/remover archivos del índice según la acción"""
        if ctx.action in {"CREADO", "MODIFICADO"}:
            # Agregar archivo al staging area
            rel = self._relative_repo_path(ctx.abs_path)
            self._repo.index.add([rel])
        elif ctx.action == "ELIMINADO":
            # Remover del índice incluso si ya fue eliminado del working tree
            rel = self._relative_repo_path(ctx.abs_path)
            try:
                self._repo.index.remove([rel], working_tree=True)
            except Exception:
                # Si ya fue eliminado, intentar sin working_tree
                try:
                    self._repo.index.remove([rel])
                except Exception:
                    pass
        elif ctx.action == "RENOMBRADO":
            # Stagear ambos lados del renombrado (mejor esfuerzo)
            if ctx.abs_path_src is not None:
                try:
                    rel_src = self._relative_repo_path(ctx.abs_path_src)
                    self._repo.index.remove([rel_src], working_tree=True)
                except Exception:
                    pass
            rel_dst = self._relative_repo_path(ctx.abs_path)
            self._repo.index.add([rel_dst])

    def _commit(self, ctx: CommitContext) -> None:
        """
        Crear commit con mensaje estandarizado que incluye:
        - Acción realizada
        - Nombre del archivo
        - Ruta completa
        - Fecha/hora
        - Usuario/máquina
        """
        ts = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        file_name = ctx.abs_path.name
        full_path = self._relative_programs_path(ctx.abs_path)
        user = self._machine_label()

        # Construir mensaje del commit
        lines = [
            f"Auto-commit: {ctx.action} archivo {file_name}",
            f"Ruta: {full_path}",
            f"Fecha: {ts}",
            f"Usuario: {user}",
        ]
        # Para renombrados, agregar ruta origen
        if ctx.action == "RENOMBRADO" and ctx.abs_path_src is not None:
            lines.insert(2, f"Origen: {self._relative_programs_path(ctx.abs_path_src)}")

        message = "\n".join(lines)
        
        # Crear commit con autor personalizado si está configurado
        actor = self._actor()
        if actor is not None:
            self._repo.index.commit(message, author=actor, committer=actor)
        else:
            self._repo.index.commit(message)

        self._logger.info(f"Committed: {ctx.action} {full_path}")

    def _sync_and_push(self) -> None:
        """
        Sincronizar con remoto y hacer push:
        1. Verificar que no exista bloqueo por conflicto
        2. Pull (si está habilitado)
        3. Push con reintentos
        4. Detectar conflictos y crear bloqueo si es necesario
        """
        # No pushear si hay un conflicto sin resolver
        if self.conflict_lock_exists():
            self._logger.error(f"Conflict lock present; skipping push: {self._lock_path}")
            return

        remote_url = (self._git_cfg.remote_url or "").strip()
        # Obtener token de autenticación desde variable de entorno
        token = os.environ.get(self._git_cfg.token_env, "").strip() if self._git_cfg.token_env else ""

        def auth_url(url: str) -> str:
            """Inyectar token en la URL de forma segura (solo en memoria)"""
            if not token:
                return url
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in {"http", "https"}:
                return url
            # Formato: https://x-access-token:TOKEN@github.com/...
            safe_token = urllib.parse.quote(token, safe="")
            netloc = f"x-access-token:{safe_token}@{parsed.netloc}"
            return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

        remote_name = self._git_cfg.remote_name
        branch = self._git_cfg.branch

        # Reintentos con backoff exponencial
        for attempt in range(1, self._git_cfg.max_push_retries + 1):
            try:
                # Pull antes de push (opcional)
                if self._git_cfg.pull_before_push:
                    self._pull(remote_url=remote_url, branch=branch, auth_builder=auth_url)

                # Push
                self._push(remote_url=remote_url, branch=branch, auth_builder=auth_url)
                self._logger.info("Pushed successfully")
                return
            except Exception as exc:
                msg = str(exc)
                self._logger.warning(f"Push attempt {attempt} failed: {msg}")
                if "CONFLICT" in msg.upper() or "merge conflict" in msg.lower():
                    self._write_conflict_lock(f"Pull/push conflict: {msg}")
                    self._logger.error(f"Created conflict lock: {self._lock_path}")
                    return

                # Si no es el último intento, esperar antes de reintentar
                if attempt < self._git_cfg.max_push_retries:
                    time.sleep(max(1, int(self._git_cfg.retry_backoff_seconds)))
                else:
                    raise

    def _pull(self, remote_url: str, branch: str, auth_builder) -> None:
        """
        Hacer pull desde el remoto con la estrategia configurada:
        - rebase: rebase sobre cambios remotos (con --autostash)
        - ff-only: solo fast-forward (falla si requiere merge)
        - merge: merge tradicional
        """
        strategy = (self._git_cfg.pull_strategy or "rebase").lower()

        if remote_url:
            # Pull directo con URL (inyecta token)
            url = auth_builder(remote_url)
            args = []
            if strategy == "rebase":
                # --autostash: guarda cambios locales temporalmente durante rebase
                # (útil porque el working tree puede cambiar mientras se monitorea)
                args = [url, branch, "--rebase", "--autostash"]
            elif strategy == "ff-only":
                args = [url, branch, "--ff-only"]
            else:
                args = [url, branch]
            self._repo.git.pull(*args)
            return

        # Pull usando remoto configurado
        remote = self._repo.remotes[self._git_cfg.remote_name]
        if strategy == "rebase":
            self._repo.git.pull(remote.name, branch, "--rebase", "--autostash")
        elif strategy == "ff-only":
            self._repo.git.pull(remote.name, branch, "--ff-only")
        else:
            remote.pull(branch)

    def _push(self, remote_url: str, branch: str, auth_builder) -> None:
        """Hacer push al remoto"""
        if remote_url:
            # Push directo con URL (inyecta token)
            url = auth_builder(remote_url)
            self._repo.git.push(url, f"HEAD:{branch}")
            return

        # Push usando remoto configurado
        remote = self._repo.remotes[self._git_cfg.remote_name]
        remote.push(branch)
