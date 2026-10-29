"""Validated file/image/PDF uploads and auxiliary vision descriptions."""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from .config import AppConfig
from .db import Database
from .model_gateway import BaseGateway, ModelRequest, messages_prefix_text
from .models import Session, Upload, new_id
from .security import redact_text

logger = logging.getLogger(__name__)
TEXT_MIME = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/x-yaml",
    "text/yaml",
    "text/html",
    "application/xml",
}
MAX_IMAGE_EDGE = 2_048


class UploadError(ValueError):
    pass


def upload_dto(upload: Upload) -> dict[str, Any]:
    return {
        "id": upload.id,
        "filename": upload.filename,
        "mime": upload.mime,
        "size": upload.size,
        "kind": upload.kind,
        "width": upload.width,
        "height": upload.height,
        "hasText": bool(upload.extracted_text),
        "url": f"/api/v1/uploads/{upload.id}/content",
        "createdAt": upload.created_at,
    }


class UploadService:
    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config

    async def save(
        self,
        data: bytes,
        *,
        filename: str,
        mime: str = "",
        session_id: str | None = None,
    ) -> Upload:
        limit = self.config.security.max_upload_mb * 1024 * 1024
        if len(data) > limit:
            raise UploadError(f"文件超过 {self.config.security.max_upload_mb}MB 限制")
        safe_name = Path(filename or "upload.bin").name[:255]
        detected = (mime or mimetypes.guess_type(safe_name)[0] or "application/octet-stream").split(";")[0]
        kind = "file"
        extracted: str | None = None
        width = height = None
        payload = data
        extension = ".bin"
        if detected.startswith("image/"):
            try:
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    width, height = image.size
                    if max(width, height) > MAX_IMAGE_EDGE:
                        image.thumbnail((MAX_IMAGE_EDGE, MAX_IMAGE_EDGE))
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert("RGB")
                    output = io.BytesIO()
                    if image.mode == "RGBA":
                        image.save(output, format="PNG", optimize=True)
                        detected, extension = "image/png", ".png"
                    else:
                        image.save(output, format="JPEG", quality=88, optimize=True)
                        detected, extension = "image/jpeg", ".jpg"
                    payload = output.getvalue()
                kind = "image"
            except (UnidentifiedImageError, OSError) as exc:
                raise UploadError("图片无法解析或已损坏") from exc
        elif detected == "application/pdf" or safe_name.lower().endswith(".pdf"):
            kind = "pdf"
            extension = ".pdf"
            try:
                reader = PdfReader(io.BytesIO(data))
                pages = []
                for index, page in enumerate(reader.pages[:20]):
                    pages.append(f"[page {index + 1}]\n{page.extract_text() or ''}")
                extracted = redact_text("\n\n".join(pages))[:100_000]
                detected = "application/pdf"
            except Exception as exc:  # noqa: BLE001
                raise UploadError("PDF 解析失败") from exc
        elif (
            detected in TEXT_MIME
            or detected.startswith("text/")
            or safe_name.lower().endswith(
                (".md", ".txt", ".py", ".ts", ".tsx", ".js", ".json", ".yaml", ".yml", ".toml", ".csv")
            )
        ):
            kind = "text"
            extension = Path(safe_name).suffix[:10] or ".txt"
            try:
                extracted = redact_text(data.decode("utf-8", errors="replace"))[:100_000]
            except Exception:  # noqa: BLE001
                extracted = None
            detected = detected if detected != "application/octet-stream" else "text/plain"
        else:
            extension = Path(safe_name).suffix[:10] or ".bin"
        upload = Upload(
            id=new_id("up"),
            session_id=session_id,
            filename=safe_name,
            mime=detected,
            size=len(payload),
            path="",
            kind=kind,
            width=width,
            height=height,
            extracted_text=extracted,
        )
        async with self.db.session() as db:
            if session_id and await db.get(Session, session_id) is None:
                raise UploadError("会话不存在")
        target_dir = self.config.data_dir / "uploads"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{upload.id}{extension}"
        target.write_bytes(payload)
        upload.path = str(target.relative_to(self.config.data_dir))
        async with self.db.session() as db:
            db.add(upload)
            await db.commit()
        return upload

    async def get(self, upload_id: str) -> Upload:
        async with self.db.session() as db:
            upload = await db.get(Upload, upload_id)
            if upload is None:
                raise LookupError("上传文件不存在")
            return upload

    async def link_to_message(self, upload_ids: list[str], message_id: str, session_id: str) -> list[Upload]:
        uploads: list[Upload] = []
        async with self.db.session() as db:
            for upload_id in upload_ids:
                upload = await db.get(Upload, upload_id)
                if upload is None or (upload.session_id and upload.session_id != session_id):
                    raise UploadError(f"附件 {upload_id} 不属于当前会话")
                upload.session_id = session_id
                upload.message_id = message_id
                uploads.append(upload)
            await db.commit()
        return uploads

    def absolute_path(self, upload: Upload) -> Path:
        target = (self.config.data_dir / upload.path).resolve()
        if not target.is_relative_to(self.config.data_dir.resolve()):
            raise UploadError("附件路径非法")
        return target

    def content_bytes(self, upload: Upload) -> bytes:
        return self.absolute_path(upload).read_bytes()

    def openai_content(self, upload: Upload) -> dict[str, Any]:
        if upload.kind == "image":
            encoded = base64.b64encode(self.content_bytes(upload)).decode("ascii")
            return {
                "type": "image_url",
                "image_url": {"url": f"data:{upload.mime};base64,{encoded}"},
            }
        return {
            "type": "text",
            "text": f"[附件 {upload.filename}]\n{upload.extracted_text or ''}".strip(),
        }

    async def describe_image(
        self,
        gateway: BaseGateway,
        *,
        model_ref: str,
        image_path: Path,
        prompt: str = "请详细描述这张图片的内容、文字、布局和可能的问题。",
    ) -> str:
        mime = mimetypes.guess_type(image_path.name)[0] or "image/png"
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
                ],
            }
        ]
        prefix_text, prefix_hash = messages_prefix_text(messages, [])
        request = ModelRequest(
            session_id="vision",
            turn_id="vision",
            step=1,
            model_ref=model_ref,
            messages=messages,
            tools=[],
            reasoning_level="off",
            max_output=1_200,
            prefix_text=prefix_text,
            prefix_hash=prefix_hash,
        )
        parts: list[str] = []
        async for chunk in gateway.stream(request):
            if chunk.kind == "content":
                parts.append(chunk.text)
            elif chunk.kind == "completed" and chunk.content:
                parts = [chunk.content]
        if not parts:
            raise UploadError("视觉模型没有返回描述")
        return "".join(parts)


def extract_markdown_image_links(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
