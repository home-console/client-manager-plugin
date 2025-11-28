from pydantic import BaseModel, Field
from typing import Optional, Literal


class UploadInitRequest(BaseModel):
    client_id: str
    path: str = Field(description="Путь назначения на стороне получателя")
    sha256: Optional[str] = None
    direction: Literal["upload", "download"] = "download"
    source_path_server: Optional[str] = Field(default=None, description="Локальный путь на сервере для direction=upload")
    dest_path_server: Optional[str] = Field(default=None, description="Путь сохранения на сервере для direction=download")
    size: Optional[int] = Field(default=None, description="Размер файла в байтах (если известен)")
    original_filename: Optional[str] = Field(default=None, description="Имя файла, которое выбрал пользователь")


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
    client_id: Optional[str] = None
    direction: Optional[str] = None
    path: Optional[str] = None
    source_path_server: Optional[str] = None
    dest_path: Optional[str] = None
    original_filename: Optional[str] = None


class PauseResumeRequest(BaseModel):
    transfer_id: str


