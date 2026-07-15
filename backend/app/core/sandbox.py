from __future__ import annotations

import os
import platform
import resource
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, IO, Mapping, Sequence


_BASE_ENV_KEYS = {
    "CONDA_DEFAULT_ENV",
    "CONDA_PREFIX",
    "DYLD_LIBRARY_PATH",
    "FONTCONFIG_PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "PATH",
    "REQUESTS_CA_BUNDLE",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TERM",
    "TZ",
    "WINDIR",
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _deduplicate_paths(paths: Sequence[Path | str]) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return tuple(result)


@dataclass(frozen=True)
class SandboxLimits:
    """Local process limits shared by every executable launched by the agent."""

    timeout_seconds: float = 600.0
    cpu_seconds: int = 600
    memory_mb: int = 16_384
    max_processes: int = 256
    max_open_files: int = 512
    max_file_size_mb: int = 1_024


@dataclass(frozen=True)
class SandboxMetadata:
    enabled: bool
    mode: str
    network_allowed: bool
    cwd: str
    executable: str
    limits: SandboxLimits
    read_roots: tuple[str, ...] = field(default_factory=tuple)
    write_roots: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "network_allowed": self.network_allowed,
            "cwd": self.cwd,
            "executable": self.executable,
            "read_roots": list(self.read_roots),
            "write_roots": list(self.write_roots),
            "limits": {
                "timeout_seconds": self.limits.timeout_seconds,
                "cpu_seconds": self.limits.cpu_seconds,
                "memory_mb": self.limits.memory_mb,
                "max_processes": self.limits.max_processes,
                "max_open_files": self.limits.max_open_files,
                "max_file_size_mb": self.limits.max_file_size_mb,
            },
        }


@dataclass
class SandboxCompletedProcess:
    args: list[str]
    returncode: int
    stdout: str | bytes | None = None
    stderr: str | bytes | None = None
    timed_out: bool = False
    sandbox: SandboxMetadata | None = None


class SandboxedProcess:
    """Small Popen wrapper that enforces deadlines and kills the whole process group."""

    def __init__(
        self,
        process: subprocess.Popen[Any],
        *,
        command: list[str],
        limits: SandboxLimits,
        metadata: SandboxMetadata,
        temporary_directory: tempfile.TemporaryDirectory[str],
        cleanup_paths: Sequence[Path] = (),
    ) -> None:
        self._process = process
        self.command = command
        self.limits = limits
        self.sandbox = metadata
        self.started_at = time.monotonic()
        self.deadline = self.started_at + limits.timeout_seconds if limits.timeout_seconds > 0 else None
        self.timed_out = False
        self._temporary_directory = temporary_directory
        self._cleanup_paths = tuple(cleanup_paths)
        self._cleaned = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._process, name)

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def _remaining_timeout(self) -> float | None:
        if self.deadline is None:
            return None
        return max(0.0, self.deadline - time.monotonic())

    def _deadline_expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def poll(self) -> int | None:
        returncode = self._process.poll()
        if returncode is None and self._deadline_expired():
            self.timed_out = True
            self.kill()
            returncode = self._process.poll()
        if returncode is not None:
            self._cleanup()
        return returncode

    def wait(self, timeout: float | None = None) -> int:
        effective_timeout = timeout if timeout is not None else self._remaining_timeout()
        try:
            returncode = self._process.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            self.kill()
            returncode = self._process.wait(timeout=2)
        finally:
            self._cleanup()
        return returncode

    def communicate(self, input: str | bytes | None = None, timeout: float | None = None):  # noqa: A002, ANN201
        effective_timeout = timeout if timeout is not None else self._remaining_timeout()
        try:
            return self._process.communicate(input=input, timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            self.timed_out = True
            self.kill()
            return self._process.communicate(timeout=2)
        finally:
            self._cleanup()

    def terminate(self) -> None:
        self._signal_group(signal.SIGTERM, fallback=self._process.terminate)

    def kill(self) -> None:
        self._signal_group(signal.SIGKILL, fallback=self._process.kill)

    def _signal_group(self, sig: int, *, fallback) -> None:  # noqa: ANN001
        if self._process.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(os.getpgid(self._process.pid), sig)
                return
            except (OSError, ProcessLookupError):
                pass
        try:
            fallback()
        except ProcessLookupError:
            pass

    def _cleanup(self) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        for path in self._cleanup_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        self._temporary_directory.cleanup()


class SandboxRunner:
    """One local sandbox boundary for Python, LAMMPS, post-processing and MCP.

    The runner deliberately uses no shell and no container. It always applies a
    clean environment, an allowed working-directory boundary, POSIX resource
    limits and process-group cleanup. On macOS it additionally uses
    ``sandbox-exec`` when the host permits it; restricted hosts automatically
    retain the resource-limited fallback instead of breaking execution.
    """

    _native_probe_lock = threading.Lock()
    _native_probe_result: bool | None = None

    def __init__(
        self,
        *,
        allowed_roots: Sequence[Path | str],
        limits: SandboxLimits | None = None,
        enabled: bool | None = None,
        native_enabled: bool | None = None,
    ) -> None:
        self.allowed_roots = _deduplicate_paths(allowed_roots)
        if not self.allowed_roots:
            raise ValueError("SandboxRunner requires at least one allowed root.")
        self.limits = limits or SandboxLimits()
        self.enabled = _env_bool("PHASE_DIAGRAM_SANDBOX_ENABLED", True) if enabled is None else enabled
        self.native_enabled = _env_bool("PHASE_DIAGRAM_SANDBOX_NATIVE_ENABLED", True) if native_enabled is None else native_enabled

    def describe(self) -> dict[str, Any]:
        native_active = self.enabled and self.native_enabled and self._native_sandbox_available()
        metadata = SandboxMetadata(
            enabled=self.enabled,
            mode="native_macos+resource_limits" if native_active else ("resource_limits" if self.enabled else "disabled"),
            network_allowed=False,
            cwd="",
            executable="",
            limits=self.limits,
            read_roots=(),
            write_roots=(),
        ).as_dict()
        metadata.update(
            {
                "native_requested": self.native_enabled,
                "native_available": native_active,
                "allowed_roots": [str(path) for path in self.allowed_roots],
            }
        )
        return metadata

    def run(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path | str,
        env_overrides: Mapping[str, str] | None = None,
        allow_network: bool = False,
        read_roots: Sequence[Path | str] = (),
        write_roots: Sequence[Path | str] | None = None,
        limits: SandboxLimits | None = None,
        input: str | bytes | None = None,  # noqa: A002
        text: bool = True,
    ) -> SandboxCompletedProcess:
        process = self.popen(
            command,
            cwd=cwd,
            env_overrides=env_overrides,
            allow_network=allow_network,
            read_roots=read_roots,
            write_roots=write_roots,
            limits=limits,
            stdin=subprocess.PIPE if input is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=text,
        )
        stdout, stderr = process.communicate(input=input)
        if process.timed_out:
            marker = "Sandbox timeout exceeded."
            if isinstance(stderr, bytes):
                stderr = (stderr or b"") + (b"\n" if stderr else b"") + marker.encode("utf-8")
            else:
                stderr = f"{stderr or ''}\n{marker}".strip()
        return SandboxCompletedProcess(
            args=process.command,
            returncode=int(process.returncode or 0),
            stdout=stdout,
            stderr=stderr,
            timed_out=process.timed_out,
            sandbox=process.sandbox,
        )

    def popen(
        self,
        command: Sequence[str | Path],
        *,
        cwd: Path | str,
        env_overrides: Mapping[str, str] | None = None,
        allow_network: bool = False,
        read_roots: Sequence[Path | str] = (),
        write_roots: Sequence[Path | str] | None = None,
        limits: SandboxLimits | None = None,
        **popen_kwargs: Any,
    ) -> SandboxedProcess:
        if popen_kwargs.pop("shell", False):
            raise ValueError("SandboxRunner never permits shell=True.")

        resolved_cwd = Path(cwd).expanduser().resolve()
        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            raise ValueError(f"Sandbox working directory does not exist: {resolved_cwd}")
        if not any(_is_within(resolved_cwd, root) for root in self.allowed_roots):
            raise ValueError(f"Sandbox working directory is outside allowed roots: {resolved_cwd}")

        normalized_command = [str(item) for item in command]
        if not normalized_command or not normalized_command[0].strip():
            raise ValueError("Sandbox command must contain an executable.")
        executable = self._resolve_executable(normalized_command[0], env_overrides)
        normalized_command[0] = executable

        active_limits = limits or self.limits
        requested_write_roots = [resolved_cwd] if write_roots is None else list(write_roots)
        normalized_read_roots = _deduplicate_paths(read_roots)
        normalized_write_roots = _deduplicate_paths(requested_write_roots)
        for path in normalized_write_roots:
            if not any(_is_within(path, root) for root in self.allowed_roots):
                raise ValueError(f"Sandbox write root is outside allowed roots: {path}")

        temporary_directory = tempfile.TemporaryDirectory(prefix="matterlab-sandbox-")
        sandbox_home = Path(temporary_directory.name)
        environment = self._build_environment(
            executable=Path(executable),
            sandbox_home=sandbox_home,
            overrides=env_overrides,
        )

        launch_command = normalized_command
        cleanup_paths: list[Path] = []
        native_active = self.enabled and self.native_enabled and self._native_sandbox_available()
        if native_active:
            profile_path = sandbox_home / "profile.sb"
            profile_path.write_text(
                self._build_macos_profile(
                    allow_network=allow_network,
                    write_roots=[*normalized_write_roots, sandbox_home],
                ),
                encoding="utf-8",
            )
            launch_command = ["/usr/bin/sandbox-exec", "-f", str(profile_path), *normalized_command]
            cleanup_paths.append(profile_path)

        metadata = SandboxMetadata(
            enabled=self.enabled,
            mode="native_macos+resource_limits" if native_active else ("resource_limits" if self.enabled else "disabled"),
            network_allowed=allow_network,
            cwd=str(resolved_cwd),
            executable=executable,
            limits=active_limits,
            read_roots=tuple(str(path) for path in normalized_read_roots),
            write_roots=tuple(str(path) for path in normalized_write_roots),
        )
        try:
            process = subprocess.Popen(
                launch_command,
                cwd=resolved_cwd,
                env=environment,
                shell=False,
                start_new_session=True,
                preexec_fn=self._build_preexec(active_limits) if self.enabled and os.name == "posix" else None,
                **popen_kwargs,
            )
        except Exception:
            temporary_directory.cleanup()
            raise
        return SandboxedProcess(
            process,
            command=normalized_command,
            limits=active_limits,
            metadata=metadata,
            temporary_directory=temporary_directory,
            cleanup_paths=cleanup_paths,
        )

    @staticmethod
    def _resolve_executable(command: str, env_overrides: Mapping[str, str] | None) -> str:
        candidate = Path(command).expanduser()
        if candidate.is_absolute() or candidate.parent != Path("."):
            resolved = candidate.resolve()
            if not resolved.exists() or not resolved.is_file():
                raise ValueError(f"Sandbox executable does not exist: {resolved}")
            return str(resolved)
        path = (env_overrides or {}).get("PATH") or os.environ.get("PATH", "")
        resolved_command = shutil.which(command, path=path)
        if not resolved_command:
            raise ValueError(f"Sandbox executable was not found on PATH: {command}")
        return str(Path(resolved_command).resolve())

    @staticmethod
    def _build_environment(*, executable: Path, sandbox_home: Path, overrides: Mapping[str, str] | None) -> dict[str, str]:
        environment = {key: value for key, value in os.environ.items() if key in _BASE_ENV_KEYS and value}
        executable_path = str(executable.parent)
        current_path = environment.get("PATH", "")
        environment["PATH"] = executable_path if not current_path else f"{executable_path}{os.pathsep}{current_path}"
        environment.update({str(key): str(value) for key, value in (overrides or {}).items()})
        environment.update(
            {
                "HOME": str(sandbox_home),
                "TMPDIR": str(sandbox_home),
                "TMP": str(sandbox_home),
                "TEMP": str(sandbox_home),
                "MPLCONFIGDIR": str(sandbox_home / "matplotlib"),
                "XDG_CACHE_HOME": str(sandbox_home / "cache"),
                "PYTHONDONTWRITEBYTECODE": "1",
                "MATTERLAB_SANDBOX": "1",
            }
        )
        return environment

    @staticmethod
    def _set_limit(kind: int, value: int, *, allow_zero: bool = False) -> None:
        if value < 0 or (value == 0 and not allow_zero):
            return
        try:
            _, hard = resource.getrlimit(kind)
            target = value if hard == resource.RLIM_INFINITY else min(value, int(hard))
            resource.setrlimit(kind, (target, target))
        except (OSError, ValueError):
            # Some managed macOS hosts reject individual hard-limit changes.
            # A missing optional limit must not disable the remaining sandbox
            # boundary (deadline, clean env, cwd guard and process-group kill).
            return

    @classmethod
    def _build_preexec(cls, limits: SandboxLimits):
        def apply_limits() -> None:
            os.umask(0o077)
            cls._set_limit(resource.RLIMIT_CORE, 0, allow_zero=True)
            cls._set_limit(resource.RLIMIT_CPU, int(limits.cpu_seconds))
            cls._set_limit(resource.RLIMIT_AS, int(limits.memory_mb) * 1024 * 1024)
            cls._set_limit(resource.RLIMIT_FSIZE, int(limits.max_file_size_mb) * 1024 * 1024)
            cls._set_limit(resource.RLIMIT_NOFILE, int(limits.max_open_files))
            if hasattr(resource, "RLIMIT_NPROC"):
                cls._set_limit(resource.RLIMIT_NPROC, int(limits.max_processes))

        return apply_limits

    @classmethod
    def _native_sandbox_available(cls) -> bool:
        if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").exists():
            return False
        with cls._native_probe_lock:
            if cls._native_probe_result is not None:
                return cls._native_probe_result
            try:
                completed = subprocess.run(
                    ["/usr/bin/sandbox-exec", "-p", "(version 1)(allow default)", "/usr/bin/true"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
                cls._native_probe_result = completed.returncode == 0
            except (OSError, subprocess.SubprocessError):
                cls._native_probe_result = False
            return cls._native_probe_result

    @staticmethod
    def _build_macos_profile(*, allow_network: bool, write_roots: Sequence[Path]) -> str:
        rules = ["(version 1)", "(allow default)"]
        if not allow_network:
            rules.append("(deny network*)")
        rules.append("(deny file-write*)")
        rules.append('(allow file-write* (literal "/dev/null") (literal "/dev/zero"))')
        for root in write_roots:
            escaped = str(root).replace("\\", "\\\\").replace('"', '\\"')
            rules.append(f'(allow file-write* (subpath "{escaped}"))')
        return "\n".join(rules) + "\n"


@lru_cache(maxsize=1)
def get_sandbox_runner() -> SandboxRunner:
    from app.config import PROJECT_ROOT, settings

    return SandboxRunner(
        allowed_roots=(PROJECT_ROOT, settings.tmp_dir, Path(tempfile.gettempdir())),
        enabled=settings.sandbox_enabled,
        native_enabled=settings.sandbox_native_enabled,
    )
