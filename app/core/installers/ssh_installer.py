"""
SSH-инсталлятор для remote_client.
"""

from __future__ import annotations

import io
import logging
import shlex
import textwrap
from dataclasses import dataclass
from typing import List, Optional

import paramiko  # type: ignore[import]

from ...config import Settings
from ...schemas.install import (
    CommandExecutionResult,
    SSHInstallRequest,
    SSHInstallResponse,
)

logger = logging.getLogger(__name__)


class SSHInstallError(Exception):
    """Базовая ошибка установки."""


@dataclass
class _PlatformInfo:
    os_name: str
    arch: str


class SSHInstaller:
    """Подключается по SSH и ставит remote_client на устройство."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.default_install_dir = settings.remote_client_install_dir or "/opt/remote-client"
        self.binary_basename = settings.remote_client_binary_name or "remote-client"
        base_url = settings.remote_client_release_base_url
        repo = (settings.remote_client_repo or "").strip("/")
        if base_url:
            self.download_base = base_url.rstrip("/")
        elif repo:
            self.download_base = f"https://github.com/{repo}/releases/latest/download"
        else:
            # Можно переопределить через download_url в запросе
            self.download_base = None

    def install_remote_client(self, request: SSHInstallRequest) -> SSHInstallResponse:
        """Основной метод установки."""
        ssh = self._connect(request)
        commands: List[CommandExecutionResult] = []
        service_created = False
        download_url = ""

        try:
            platform = self._detect_platform(ssh, request)
            artifact_name = self._resolve_artifact_name(platform, request)
            download_url = self._resolve_download_url(artifact_name, request)
            install_dir = (request.install_dir or self.default_install_dir).rstrip("/")
            remote_binary_path = f"{install_dir}/{self.binary_basename}"

            downloader_template = self._detect_downloader(ssh, request)

            commands.append(
                self._exec_and_check(
                    ssh,
                    f"mkdir -p {shlex.quote(install_dir)}",
                    use_sudo=request.use_sudo,
                    timeout=request.timeout,
                )
            )

            download_command = downloader_template.format(
                url=shlex.quote(download_url),
                path=shlex.quote(remote_binary_path),
            )
            commands.append(
                self._exec_and_check(
                    ssh,
                    download_command,
                    use_sudo=request.use_sudo,
                    timeout=request.timeout,
                )
            )

            commands.append(
                self._exec_and_check(
                    ssh,
                    f"chmod +x {shlex.quote(remote_binary_path)}",
                    use_sudo=request.use_sudo,
                    timeout=request.timeout,
                )
            )

            for extra_command in request.extra_install_commands:
                commands.append(
                    self._exec_and_check(
                        ssh,
                        extra_command,
                        use_sudo=request.use_sudo,
                        timeout=request.timeout,
                        trusted=False,
                    )
                )

            if request.create_service:
                service_created = self._create_systemd_service(
                    ssh,
                    request=request,
                    binary_path=remote_binary_path,
                    install_dir=install_dir,
                    commands=commands,
                )

            message = "Удалённый клиент установлен"
            if service_created:
                message += " и сервис systemd создан"

            return SSHInstallResponse(
                host=request.host,
                status="success",
                detected_os=platform.os_name,
                detected_arch=platform.arch,
                download_url=download_url,
                binary_path=remote_binary_path,
                service_created=service_created,
                commands=commands,
                message=message,
            )
        except SSHInstallError:
            raise
        except Exception as exc:  # pragma: no cover - общий fallback
            logger.exception("Не удалось установить remote_client через SSH")
            raise SSHInstallError(str(exc)) from exc
        finally:
            ssh.close()

    # --- Вспомогательные методы -------------------------------------------------

    def _connect(self, request: SSHInstallRequest) -> paramiko.SSHClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": request.host,
            "port": request.port,
            "username": request.username,
            "password": request.password,
            "look_for_keys": False,
            "allow_agent": False,
            "timeout": 15,
        }

        if request.private_key:
            connect_kwargs["pkey"] = self._load_private_key(request.private_key, request.passphrase)

        try:
            client.connect(**connect_kwargs)
        except Exception as exc:
            raise SSHInstallError(f"Не удалось подключиться по SSH: {exc}") from exc

        return client

    def _load_private_key(self, key_data: str, passphrase: Optional[str]) -> paramiko.PKey:
        key_data = key_data.strip()
        errors = []
        for key_cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey):
            try:
                return key_cls.from_private_key(io.StringIO(key_data), password=passphrase)
            except Exception as exc:  # pragma: no cover - разные типы ключей
                errors.append(str(exc))
        raise SSHInstallError("Не удалось прочитать приватный ключ (RSA/ECDSA/Ed25519)")

    def _detect_platform(self, ssh: paramiko.SSHClient, request: SSHInstallRequest) -> _PlatformInfo:
        os_name = self._run_text(ssh, "uname -s", timeout=request.timeout) or "linux"
        arch = self._run_text(ssh, "uname -m", timeout=request.timeout) or "amd64"
        return _PlatformInfo(os_name=os_name.strip(), arch=arch.strip())

    def _resolve_artifact_name(self, platform: _PlatformInfo, request: SSHInstallRequest) -> str:
        if request.binary_name:
            return request.binary_name

        normalized_os = platform.os_name.lower()
        normalized_arch = platform.arch.lower()

        if "linux" in normalized_os:
            os_part = "linux"
        elif "darwin" in normalized_os or "mac" in normalized_os:
            os_part = "darwin"
        elif "win" in normalized_os:
            os_part = "windows"
        else:
            os_part = "linux"

        arch_map = {
            "x86_64": "amd64",
            "amd64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
            "armv7l": "armv7",
            "armv6l": "armv6",
            "mips": "mips",
            "mips64": "mips64",
            "mips64le": "mips64le",
            "mipsle": "mipsle",
        }
        arch_part = arch_map.get(normalized_arch, "amd64")

        suffix = ".exe" if os_part == "windows" else ""
        return f"{self.binary_basename}-{os_part}-{arch_part}{suffix}"

    def _resolve_download_url(self, artifact_name: str, request: SSHInstallRequest) -> str:
        if request.download_url:
            return request.download_url
        if not self.download_base:
            raise SSHInstallError(
                "Не настроен URL загрузки remote_client. "
                "Укажите REMOTE_CLIENT_REPO, REMOTE_CLIENT_RELEASE_BASE_URL или download_url в запросе.",
            )
        return f"{self.download_base}/{artifact_name}"

    def _detect_downloader(self, ssh: paramiko.SSHClient, request: SSHInstallRequest) -> str:
        for binary, template in (
            ("curl", "curl -fsSL {url} -o {path}"),
            ("wget", "wget -qO {path} {url}"),
        ):
            result = self._exec_command(
                ssh,
                command=f"command -v {binary}",
                use_sudo=False,
                timeout=request.timeout,
            )
            if result.exit_code == 0:
                return template
        raise SSHInstallError("На целевом устройстве нет ни curl, ни wget — не могу скачать remote_client")

    def _create_systemd_service(
        self,
        ssh: paramiko.SSHClient,
        request: SSHInstallRequest,
        binary_path: str,
        install_dir: str,
        commands: List[CommandExecutionResult],
    ) -> bool:
        unit_contents = self._render_systemd_unit(request, binary_path, install_dir)
        tmp_unit = f"/tmp/{request.service_name}.service"
        final_unit = f"/etc/systemd/system/{request.service_name}.service"

        try:
            sftp = ssh.open_sftp()
            with sftp.file(tmp_unit, "w") as unit_file:
                unit_file.write(unit_contents)
            sftp.close()
        except Exception as exc:
            raise SSHInstallError(f"Не удалось записать файл сервиса: {exc}") from exc

        commands.append(
            self._exec_and_check(
                ssh,
                f"mv {shlex.quote(tmp_unit)} {shlex.quote(final_unit)}",
                use_sudo=True,
                timeout=request.timeout,
            )
        )
        commands.append(
            self._exec_and_check(
                ssh,
                "systemctl daemon-reload",
                use_sudo=True,
                timeout=request.timeout,
            )
        )

        enable_cmd = f"systemctl enable {'--now ' if request.auto_start else ''}{request.service_name}"
        commands.append(
            self._exec_and_check(
                ssh,
                enable_cmd,
                use_sudo=True,
                timeout=request.timeout,
            )
        )
        return True

    def _render_systemd_unit(
        self,
        request: SSHInstallRequest,
        binary_path: str,
        install_dir: str,
    ) -> str:
        env_lines = "\n".join(
            f"Environment=\"{key}={value}\"" for key, value in request.env.items()
        )
        exec_args = " ".join(shlex.quote(arg) for arg in request.exec_args)
        exec_command = f"{binary_path}"
        if request.config_path:
            exec_command = f"{exec_command} --config {shlex.quote(request.config_path)}"
        if exec_args:
            exec_command = f"{exec_command} {exec_args}"

        service_lines = [
            "[Unit]",
            f"Description={request.service_description or 'Remote Client'}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={install_dir}",
            f"ExecStart={exec_command}",
            "Restart=always",
            "RestartSec=5",
        ]

        if request.service_user:
            service_lines.append(f"User={request.service_user}")
        if request.service_group:
            service_lines.append(f"Group={request.service_group}")
        if env_lines:
            service_lines.append(env_lines)

        service_lines.append("")
        service_lines.extend(
            [
                "[Install]",
                "WantedBy=multi-user.target",
            ]
        )

        return textwrap.dedent("\n".join(service_lines)).strip() + "\n"

    def _exec_and_check(
        self,
        ssh: paramiko.SSHClient,
        command: str,
        use_sudo: bool,
        timeout: int,
        *,
        trusted: bool = True,
    ) -> CommandExecutionResult:
        # If command is untrusted (came from external input), validate it
        if not trusted:
            self._validate_user_command(command)

        result = self._exec_command(ssh, command, use_sudo, timeout)
        if result.exit_code != 0:
            raise SSHInstallError(
                f"Команда '{command}' завершилась с кодом {result.exit_code}: "
                f"{result.stderr.strip() or result.stdout.strip()}",
            )
        return result

    def _validate_user_command(self, command: str) -> None:
        """Conservative validation for user-provided commands.

        Reject commands that contain common shell metacharacters or
        control operators which could be used for command chaining/injection.
        """
        bad_tokens = [';', '&&', '||', '|', '<', '>', '`', '$(', '$']
        for t in bad_tokens:
            if t in command:
                raise SSHInstallError(f"Rejected unsafe install command from user: contains '{t}'")

    def _run_text(
        self,
        ssh: paramiko.SSHClient,
        command: str,
        timeout: int,
        use_sudo: bool = False,
    ) -> str:
        result = self._exec_command(ssh, command, use_sudo, timeout)
        if result.exit_code != 0:
            raise SSHInstallError(
                f"Команда '{command}' завершилась с кодом {result.exit_code}: "
                f"{result.stderr.strip() or result.stdout.strip()}",
            )
        return result.stdout.strip()

    def _exec_command(
        self,
        ssh: paramiko.SSHClient,
        command: str,
        use_sudo: bool,
        timeout: int,
    ) -> CommandExecutionResult:
        wrapped_command = self._wrap_command(command, use_sudo)
        stdin, stdout, stderr = ssh.exec_command(wrapped_command, timeout=timeout)
        out = stdout.read().decode("utf-8", errors="ignore")
        err = stderr.read().decode("utf-8", errors="ignore")
        exit_code = stdout.channel.recv_exit_status()
        logger.debug("SSH command finished", extra={"command": command, "exit_code": exit_code})
        return CommandExecutionResult(
            command=command,
            exit_code=exit_code,
            stdout=out,
            stderr=err,
        )

    @staticmethod
    def _wrap_command(command: str, use_sudo: bool) -> str:
        shell_command = f"bash -lc {shlex.quote(command)}"
        if use_sudo:
            return f"sudo -H {shell_command}"
        return shell_command


