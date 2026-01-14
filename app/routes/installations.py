"""
Эндпоинты для установки remote_client на устройства по SSH.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from ..config import get_settings

# Lazy import of SSH installer depending on feature flag
installer = None
if get_settings().enable_ssh_installer:
    try:
        from ..core.installers.ssh_installer import SSHInstallError, SSHInstaller
        installer = SSHInstaller(get_settings())
    except Exception:
        # If optional dependency fails to import, keep installer None
        installer = None
else:
    SSHInstallError = Exception  # placeholder for typing
from ..schemas.install import SSHInstallRequest, SSHInstallResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/installations", tags=["installations"])
# `installer` was created conditionally above based on feature flag


@router.post("/remote-client", response_model=SSHInstallResponse)
async def install_remote_client(request: SSHInstallRequest) -> SSHInstallResponse:
    """
    Подключается к устройству по SSH и скачивает бинарник remote_client с GitHub.
    """
    logger.info("Запрос на установку remote_client через SSH", extra={"host": request.host})
    loop = asyncio.get_running_loop()
    if installer is None:
        raise HTTPException(status_code=501, detail="SSH installer is disabled in configuration")

    try:
        return await loop.run_in_executor(None, lambda: installer.install_remote_client(request))
    except SSHInstallError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - общий fallback
        logger.exception("Неожиданная ошибка установки remote_client")
        raise HTTPException(status_code=500, detail="Не удалось установить remote_client") from exc


