import asyncio
import json
from functools import partial

import httpx
import pytest

from app.services import image_generation


SETTINGS = image_generation.ImageSettings(
    endpoint="https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
    api_key="test-key",
    model="qwen-image-3.0-pro",
)
RESULT_BASE = "https://dashscope-result.oss-cn-beijing.aliyuncs.com"


def mock_provider(monkeypatch, handler):
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(image_generation.httpx, "AsyncClient", client)


def provider_response(content):
    return httpx.Response(200, json={"output": {"choices": [{"message": {"content": content}}]}})


def test_dashscope_sends_each_page_and_downloads_without_api_key(monkeypatch):
    title = "小兔看世界"
    pages = ["小兔走过草地。", "小兔到达河边。"]
    requests = []
    downloads = []

    def handler(request):
        if request.method == "POST":
            requests.append(request)
            return provider_response([{"image": f"{RESULT_BASE}/{len(requests)}.png?signature=test"}])
        downloads.append(request)
        return httpx.Response(200, content=request.url.path.encode())

    mock_provider(monkeypatch, handler)
    pinned = []
    assets = [{"id": "first"}, {"id": "second"}]

    def pin(images):
        pinned.extend(images)
        return assets

    monkeypatch.setattr(image_generation, "pin_generated_illustrations", pin)

    result = asyncio.run(image_generation.generate_story_illustrations(SETTINGS, title, pages))

    assert result == assets
    assert pinned == [b"/1.png", b"/2.png"]
    assert len(requests) == len(downloads) == len(pages)
    for request, page in zip(requests, pages, strict=True):
        assert str(request.url) == SETTINGS.endpoint
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        prompt = body["input"]["messages"][0]["content"][0]["text"]
        assert prompt.endswith(f"书名：{title}\n本页正文：{page}")
        assert body == {
            "model": SETTINGS.model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"size": "1024*1024", "n": 1, "prompt_extend": False},
        }
    for index, request in enumerate(downloads, start=1):
        assert request.method == "GET"
        assert str(request.url) == f"{RESULT_BASE}/{index}.png?signature=test"
        assert "Authorization" not in request.headers


def test_image_settings_reuse_only_an_official_speech_workspace(monkeypatch):
    monkeypatch.delenv("IMAGE_API_URL", raising=False)
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "fake-workspace-key")
    monkeypatch.setenv("TTS_API_URL", "https://workspace.cn-beijing.maas.aliyuncs.com/voice/synthesis")
    settings = image_generation.image_generation_settings()
    assert settings.endpoint == (
        "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
    )
    assert settings.model == "qwen-image-3.0-pro"
    monkeypatch.setenv("TTS_API_URL", "https://workspace.cn-beijing.maas.aliyuncs.com.invalid/voice/synthesis")
    assert image_generation.image_generation_settings() is None


@pytest.mark.parametrize("content", [
    [{"image": "https://example.test/image.png"}],
    [{"text": "No image was generated"}],
], ids=["unexpected-host", "missing-image"])
def test_dashscope_rejects_invalid_results_without_downloading(monkeypatch, content):
    methods = []

    def handler(request):
        methods.append(request.method)
        return provider_response(content)

    mock_provider(monkeypatch, handler)

    with pytest.raises(image_generation.ImageGenerationError, match="^Illustration generation failed$"):
        asyncio.run(image_generation.generate_story_illustrations(SETTINGS, "绘本", ["第一页正文"]))

    assert methods == ["POST"]
