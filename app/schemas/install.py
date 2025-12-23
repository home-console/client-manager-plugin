from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


class CommandExecutionResult(BaseModel):
    """Результат выполнения SSH-команды на целевом хосте."""

    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""


class SSHInstallRequest(BaseModel):
    """Запрос на установку remote_client через SSH."""

    host: str = Field(description="Адрес целевого устройства")
    port: int = Field(default=22, description="SSH порт")
    username: str = Field(description="SSH пользователь")
    password: Optional[str] = Field(default=None, repr=False, description="Пароль (если используется)")
    private_key: Optional[str] = Field(
        default=None,
        repr=False,
        description="Приватный ключ в PEM формате",
    )
    passphrase: Optional[str] = Field(
        default=None,
        repr=False,
        description="Пароль к приватному ключу (если требуется)",
    )
    install_dir: Optional[str] = Field(
        default=None,
        description="Каталог установки на устройстве; если не указан — берется из настроек",
    )
    download_url: Optional[str] = Field(
        default=None,
        description="Прямой URL до бинарника remote_client; если не указан — формируется автоматически",
    )
    binary_name: Optional[str] = Field(
        default=None,
        description="Имя бинарника/артефакта; по умолчанию выбирается по платформе",
    )
    use_sudo: bool = Field(
        default=True,
        description="Добавлять sudo перед установочными командами",
    )
    create_service: bool = Field(
        default=False,
        description="Создать systemd сервис после установки",
    )
    service_name: str = Field(default="remote-client", description="Имя systemd сервиса")
    service_description: Optional[str] = Field(
        default="Remote Client",
        description="Описание systemd сервиса",
    )
    service_user: Optional[str] = Field(
        default=None,
        description="User= для systemd (если нужен запуск не от root)",
    )
    service_group: Optional[str] = Field(
        default=None,
        description="Group= для systemd",
    )
    auto_start: bool = Field(
        default=True,
        description="Запустить сервис после установки (если create_service=True)",
    )
    config_path: Optional[str] = Field(
        default=None,
        description="Путь до конфига remote_client, будет добавлен в ExecStart",
    )
    env: Dict[str, str] = Field(
        default_factory=dict,
        description="Дополнительные переменные окружения для systemd",
    )
    exec_args: List[str] = Field(
        default_factory=list,
        description="Список аргументов, которые будут добавлены к ExecStart",
    )
    extra_install_commands: List[str] = Field(
        default_factory=list,
        description="Дополнительные команды после установки бинарника",
    )
    timeout: int = Field(
        default=60,
        ge=1,
        le=600,
        description="Таймаут для отдельных SSH-команд (секунды)",
    )

    @model_validator(mode="after")
    def _validate_auth(self) -> "SSHInstallRequest":
        if not self.password and not self.private_key:
            raise ValueError("Нужно указать password или private_key для SSH подключения")
        return self


class SSHInstallResponse(BaseModel):
    """Ответ об установке remote_client через SSH."""

    host: str
    status: Literal["success", "failed"]
    detected_os: str
    detected_arch: str
    download_url: str
    binary_path: str
    service_created: bool = False
    commands: List[CommandExecutionResult] = Field(default_factory=list)
    message: str



