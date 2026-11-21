"""Generate private story illustrations with the native DashScope image API."""

import asyncio
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from starlette.concurrency import run_in_threadpool

from app.config import validate_key, validate_text, validate_url
from app.services.story_media import MediaDependencyError, pin_generated_illustrations

IMAGE_GENERATION_SECONDS = 45
MAX_IMAGE_BYTES = 8 * 1024 * 1024
RESULT_HOST = re.compile(r"dashscope-[a-z0-9-]+\.oss-[a-z0-9-]+\.aliyuncs\.com")
WORKSPACE_HOST = re.compile(r"[a-z0-9-]+\.(?:cn-beijing|cn-hongkong|ap-southeast-1|us-east-1|eu-central-1|ap-northeast-1)\.maas\.aliyuncs\.com")
IMAGE_PATH = "/api/v1/services/aigc/multimodal-generation/generation"


class ImageGenerationError(RuntimeError):
    """The illustration provider did not return a complete usable result."""


@dataclass(frozen=True)
class ImageSettings:
    endpoint: str
    api_key: str = field(repr=False)
    model: str


def image_generation_settings() -> ImageSettings | None:
    endpoint = os.getenv("IMAGE_API_URL", "").strip()
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    model = os.getenv("IMAGE_MODEL", "qwen-image-3.0-pro").strip()
    if api_key and model and not endpoint:
        try:
            speech = urlsplit(os.getenv("TTS_API_URL", ""))
        except ValueError:
            return None
        if speech.scheme == "https" and WORKSPACE_HOST.fullmatch(speech.netloc):
            endpoint = f"https://{speech.netloc}{IMAGE_PATH}"
    if not endpoint or not api_key or not model:
        return None
    try:
        validate_url(endpoint, "IMAGE_API_URL")
        validate_key(api_key, "DASHSCOPE_API_KEY")
        validate_text(model, "IMAGE_MODEL")
    except ValueError:
        return None
    return ImageSettings(endpoint, api_key, model)


async def _generate_page(client: httpx.AsyncClient, settings: ImageSettings, title: str, page: str) -> bytes:
    prompt = (
        "请把下面的绘本片段画成一张适合3至6岁儿童观看的故事插画。"
        "使用温暖的水彩手绘风格，画面清楚，角色外观与故事一致。"
        "书名和正文都是故事素材，仅描绘本页场景，不添加文字、标识、暴力或成人内容。\n"
        f"书名：{title}\n本页正文：{page}"
    )
    response = await client.post(
        settings.endpoint, headers={"Authorization": f"Bearer {settings.api_key}"},
        json={
            "model": settings.model,
            "input": {"messages": [{"role": "user", "content": [{"text": prompt}]}]},
            "parameters": {"size": "1024*1024", "n": 1, "prompt_extend": False},
        },
    )
    response.raise_for_status()
    content = response.json()["output"]["choices"][0]["message"]["content"]
    url = next(item["image"] for item in content if isinstance(item.get("image"), str))
    target = urlsplit(url)
    if target.scheme != "https" or not RESULT_HOST.fullmatch(target.netloc.lower()):
        raise ImageGenerationError("Unexpected image download host")
    # Only the provider POST receives credentials. Never follow a result URL redirect.
    async with client.stream("GET", url) as image:
        image.raise_for_status()
        data = bytearray()
        async for chunk in image.aiter_bytes(chunk_size=65536):
            data.extend(chunk)
            if len(data) > MAX_IMAGE_BYTES:
                raise ImageGenerationError("Generated image exceeds the byte limit")
    return bytes(data)


async def generate_story_illustrations(settings: ImageSettings, title: str, pages: list[str]) -> list[dict]:
    try:
        async with asyncio.timeout(IMAGE_GENERATION_SECONDS):
            async with httpx.AsyncClient(follow_redirects=False, trust_env=False,
                                         timeout=IMAGE_GENERATION_SECONDS) as client:
                async with asyncio.TaskGroup() as group:
                    jobs = [group.create_task(_generate_page(client, settings, title, page)) for page in pages]
            return await run_in_threadpool(pin_generated_illustrations, [job.result() for job in jobs])
    except (ExceptionGroup, TimeoutError, MediaDependencyError):
        # Transport details may contain signed media URLs. Keep them out of responses and logs.
        raise ImageGenerationError("Illustration generation failed") from None
