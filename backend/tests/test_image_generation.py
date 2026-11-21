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
    scenes = ["青苔覆盖的林间小径，小兔跃过树根", "小兔在树根旁把松果递给戴黄帽子的松鼠"]
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

    result = asyncio.run(image_generation.generate_story_illustrations(settings, scenes, **BRIEF))

    assert result == assets
    assert pinned == [b"/1.png", b"/2.png"]
    assert len(requests) == len(downloads) == len(scenes)
    prompts = []
    for request, scene in zip(requests, scenes, strict=True):
        assert str(request.url) == settings.endpoint
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        prompt = body["input"]["messages"][0]["content"][0]["text"]
        prompts.append(prompt)
        assert "单一连续场景" in prompt
        assert "森林" in prompt and "友谊" in prompt and "戴黄帽子的松鼠" in prompt
        assert "奶白色绒毛" in prompt and "粉色长耳" in prompt and "不穿衣服" in prompt
        assert "无字" in prompt
        assert all(label not in prompt for label in ("书名", "故事主题", "故事场景", "固定角色设定", "本页画面素材"))
        assert all(mark not in prompt for mark in ('{', '}', '\n', '"'))
        assert scene in prompt
        parameters = {"size": "1024*1024", "n": 1, "prompt_extend": False}
        if supports_negative_prompt:
            parameters["negative_prompt"] = body["parameters"]["negative_prompt"]
            assert all(item in parameters["negative_prompt"] for item in ("文字", "角色设定表", "多格分镜"))
        assert body == {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": parameters,
        }
    assert prompts[0].replace(scenes[0], "") == prompts[1].replace(scenes[1], "")
    assert scenes[1] not in prompts[0] and scenes[0] not in prompts[1]
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
