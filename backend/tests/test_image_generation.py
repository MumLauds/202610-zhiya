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
BRIEF = {"topic": "友谊", "scene": "森林"}


def mock_provider(monkeypatch, handler):
    client = partial(httpx.AsyncClient, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(image_generation.httpx, "AsyncClient", client)


def provider_response(content):
    return httpx.Response(200, json={"output": {"choices": [{"message": {"content": content}}]}})


@pytest.mark.parametrize("model, supports_negative_prompt", [
    ("qwen-image-3.0", True),
    ("qwen-image-3.0-pro", True),
    ("qwen-image-2.1-turbo", False),
])
def test_dashscope_sends_consistent_illustration_brief_for_each_page(monkeypatch, model, supports_negative_prompt):
    settings = replace(SETTINGS, model=model)
    scenes = ["小兔背着印有小熊图案的书包跃过树根", "小兔在树根旁把松果递给小松鼠", "小松鼠带着松果走向树洞"]
    rabbit = {"小兔": "奶白色绒毛、粉色长耳"}
    squirrel = {"小松鼠": "棕色绒毛、戴黄帽子"}
    page_casts = [rabbit, {**rabbit, **squirrel}, squirrel]
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
    assets = [{"id": "first"}, {"id": "second"}, {"id": "third"}]

    def pin(images):
        pinned.extend(images)
        return assets

    monkeypatch.setattr(image_generation, "pin_generated_illustrations", pin)

    result = asyncio.run(image_generation.generate_story_illustrations(settings, scenes, page_casts=page_casts, **BRIEF))

    assert result == assets
    assert pinned == [b"/1.png", b"/2.png", b"/3.png"]
    assert len(requests) == len(downloads) == len(scenes)
    prompts = []
    for request, scene, cast in zip(requests, scenes, page_casts, strict=True):
        assert str(request.url) == settings.endpoint
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        prompt = body["input"]["messages"][0]["content"][0]["text"]
        prompts.append(prompt)
        assert "单一连续场景" in prompt
        assert "森林" in prompt and "友谊" in prompt
        assert ("戴黄帽子" in prompt) == ("小松鼠" in cast)
        assert "小熊沿用" not in prompt and "家人" not in prompt
        assert ("奶白色绒毛" in prompt) == ("小兔" in cast)
        assert "只画本页明确出场的角色" in prompt and "自然动物体态" in prompt
        assert "无字" in prompt and "无标识" in prompt
        assert "适合3至6岁" in prompt
        assert "不含暴力或成人内容" in prompt
        assert all(label not in prompt for label in ("书名", "故事主题", "故事场景", "固定角色设定", "本页画面素材"))
        assert all(mark not in prompt for mark in ('{', '}', '\n', '"'))
        assert scene in prompt
        parameters = {"size": "1024*1024", "n": 1, "prompt_extend": False}
        if supports_negative_prompt:
            parameters["negative_prompt"] = body["parameters"]["negative_prompt"]
            excluded = parameters["negative_prompt"].split("、")
            assert all(item in excluded for item in ("文字", "角色设定表", "多格分镜", "暴力", "成人内容", "额外角色"))
        assert body == {
            "model": model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": parameters,
        }
    assert scenes[1] not in prompts[0] and scenes[0] not in prompts[1]
    for index, request in enumerate(downloads, start=1):
        assert request.method == "GET"
        assert str(request.url) == f"{RESULT_BASE}/{index}.png?signature=test"
        assert "Authorization" not in request.headers


@pytest.mark.parametrize(("description", "other_role"), [
    ("背着红书包的小熊独自走在林间小道上", "小女孩"),
    ("小女孩牵着妈妈的手走过林间小道", "小熊"),
], ids=["animal", "human-family"])
def test_image_request_keeps_the_family_story_scene_cast(monkeypatch, description, other_role):
    prompts = []

    def handler(request):
        if request.method == "POST":
            body = json.loads(request.content)
            prompts.append(body["input"]["messages"][0]["content"][0]["text"])
            return provider_response([{"image": f"{RESULT_BASE}/family.png"}])
        return httpx.Response(200, content=b"illustration")

    mock_provider(monkeypatch, handler)
    monkeypatch.setattr(image_generation, "pin_generated_illustrations", lambda images: images)
    asyncio.run(image_generation.generate_story_illustrations(
        SETTINGS, [description], page_casts=[{"小熊": "棕色绒毛"} if other_role == "小女孩"
                                                else {"小女孩": "蓝色外套", "妈妈": "长发"}], **BRIEF,
    ))

    assert len(prompts) == 1
    assert description in prompts[0]
    assert "家人" not in prompts[0] and other_role not in prompts[0]


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
        asyncio.run(image_generation.generate_story_illustrations(SETTINGS, ["第一页正文"], page_casts=[{}], **BRIEF))

    assert methods == ["POST"]
