"""Explicit local connectivity check; this is not a public model API or an Agent."""

import asyncio
import json
import logging
import sys
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv

from app.config import Settings
from app.errors import ServiceError
from app.model_gateway import ModelGateway


async def main() -> int:
    load_dotenv(Path(__file__).with_name(".env"), override=False)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    request_id = uuid4().hex
    try:
        settings = Settings.from_env()
        input_text = input("输入不含个人信息的测试问题（1～200 字）：\n")
        async with ModelGateway(settings) as gateway:
            output = await gateway.generate(input_text=input_text, system_prompt="你是文本接口连通性测试助手，请简短回答。")
        print(json.dumps({"data": {"outputText": output}, "requestId": request_id}, ensure_ascii=False))
        return 0
    except ServiceError as exc:
        print(json.dumps(exc.as_response(request_id), ensure_ascii=False), file=sys.stderr)
        return 1
    except ValueError:
        print("模型环境变量格式不正确，请核对 .env.example 中的配置要求。", file=sys.stderr)
        return 1
    except EOFError:
        print("未读取到测试问题。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
