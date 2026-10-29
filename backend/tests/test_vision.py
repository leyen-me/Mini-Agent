from __future__ import annotations

import io
from pathlib import Path

import pytest
from conftest import FakeResponse, make_config, run_harness
from PIL import Image


@pytest.mark.asyncio
async def test_read_image_uses_auxiliary_vision_model(tmp_path: Path):
    config = make_config(tmp_path)
    config.defaults.vision_model = "mock/echo"
    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), (240, 120, 30)).save(buffer, format="PNG")
    session_id = ""
    upload_id = ""
    calls = {"n": 0}

    def spec(request, index):  # noqa: ANN001
        if "请详细描述" in str(request.messages[-1].get("content")):
            return FakeResponse(content="一张橙色矩形图片")
        if calls["n"] == 0:
            calls["n"] = 1
            return FakeResponse(tool_calls=[("read_image", {"upload_id": upload_id, "prompt": "请详细描述"})])
        return FakeResponse(content="已读取图片")

    async with run_harness(tmp_path, script=[spec], config=config) as h:
        session_id = await h.create_session()
        upload = await h.client.post(
            "/api/v1/uploads",
            files={"file": ("pixel.png", buffer.getvalue(), "image/png")},
            data={"session_id": session_id},
        )
        assert upload.status_code == 200, upload.text
        upload_id = upload.json()["data"]["upload"]["id"]
        await h.send(session_id, "看看图片")
        snapshot = await h.wait_idle(session_id)
        call = next(call for call in snapshot["toolCalls"] if call["name"] == "read_image")
        assert call["output"]["code"] == 0, call["output"]
        assert "橙色矩形" in call["output"]["data"]["description"]
        assert call["output"]["data"]["visionModel"] == "mock/echo"
