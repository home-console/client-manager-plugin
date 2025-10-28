from pydantic import BaseModel, Field
from typing import Optional, Literal


class UploadInitRequest(BaseModel):
    client_id: str
    path: str = Field(description="Путь назначения на стороне получателя")
    sha256: Optional[str] = None
    direction: Literal["upload", "download"] = "download"
    source_path_server: Optional[str] = Field(default=None, description="Локальный путь на сервере для direction=upload")


class UploadInitResponse(BaseModel):
    transfer_id: str
    state: str
    direction: Literal["upload", "download"]


class TransferStatusResponse(BaseModel):
    transfer_id: str
    state: str
    received: int
    size: Optional[int] = None
    sha256: Optional[str] = None


class PauseResumeRequest(BaseModel):
    transfer_id: str


