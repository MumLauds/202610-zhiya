import asyncio
import json
from dataclasses import replace
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
BRIEF = {"topic": "友谊", "characters": ["小兔", "戴黄帽子的松鼠"], "scene": "森林"}


def mock_provider(monkeypatch, handler):
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(image_generation.httpx, "AsyncClient", client)


def provider_response(content):
    return httpx.Response(200, json={"output": {"choices": [{"message": {"content": content}}]}})


@pytest.mark.parametrize("model, supports_negative_prompt", [
    ("qwen-image-3.0-pro", True),
    ("qwen-image-2.1-turbo", False),
])
def test_dashscope_sends_consistent_illustration_brief_for_each_page(monkeypatch, model, supports_negative_prompt):
    settings = replace(SETTINGS, model=model)
    pages = ["小兔跳进森林。", "小兔给戴黄帽子的松鼠递了一颗松果。"]
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

    result = asyncio.run(image_generation.generate_story_illustrations(settings, pages, **BRIEF))

    assert result == assets
    assert pinned == [b"/1.png", b"/2.png"]
    assert len(requests) == len(downloads) == len(pages)
    shared_prompts = set()
    for request, page in zip(requests, pages, strict=True):
        assert str(request.url) == settings.endpoint
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        prompt = body["input"]["messages"][0]["content"][0]["text"]
        shared_prompt, page_material = prompt.rsplit("\n本页画面素材：", 1)
        shared_prompts.add(shared_prompt)
        assert json.loads(page_material) == page
        assert "已选角色为画面主体" in shared_prompt
        assert "动物角色保留动物外形" in shared_prompt
        assert "不把正文写进插画" in shared_prompt
        assert "书名：" not in prompt
        assert json.loads(shared_prompt.split("\n全书画面设定：", 1)[1]) == {
            "故事主题": "友谊",
            "故事场景": "森林",
            "固定角色设定": {"小兔": "奶白色绒毛、粉色长耳、圆短尾", "戴黄帽子的松鼠": "戴黄帽子的松鼠"},
        }
        parameters = {"size": "1024*1024", "n": 1, "prompt_extend": False}
        if supports_negative_prompt:
            parameters["negative_prompt"] = "文字、书名、标题、正文、字幕、签名、标识、水印"
        assert body == {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": parameters,
        }
    assert len(shared_prompts) == 1
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
        asyncio.run(image_generation.generate_story_illustrations(SETTINGS, ["第一页正文"], **BRIEF))

    assert methods == ["POST"]
