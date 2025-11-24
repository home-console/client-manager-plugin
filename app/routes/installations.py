"""
Эндпоинты для установки remote_client на устройства по SSH.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from ..config import get_settings
from ..core.installers.ssh_installer import SSHInstallError, SSHInstaller
from ..schemas.install import SSHInstallRequest, SSHInstallResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/installations", tags=["installations"])
installer = SSHInstaller(get_settings())


@router.post("/remote-client", response_model=SSHInstallResponse)
async def install_remote_client(request: SSHInstallRequest) -> SSHInstallResponse:
    """
    Подключается к устройству по SSH и скачивает бинарник remote_client с GitHub.
    """
    logger.info("Запрос на установку remote_client через SSH", extra={"host": request.host})
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: installer.install_remote_client(request))
    except SSHInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - общий fallback
        logger.exception("Неожиданная ошибка установки remote_client")
        raise HTTPException(status_code=500, detail="Не удалось установить remote_client") from exc

