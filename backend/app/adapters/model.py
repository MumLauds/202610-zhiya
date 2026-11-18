"""G08 bridge to the model request/result contract from PR #27."""

from app.errors import ServiceError
from app.model_gateway import ModelGateway
from app.schemas.agent import ModelGenerateRequest, ModelGenerateResult


class ModelAdapter:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def generate(self, request: ModelGenerateRequest) -> ModelGenerateResult:
        try:
            text = await self._gateway.generate(
                input_text=request.user_input,
                system_prompt=request.system_prompt,
                context_text=request.context_text,
            )
        except ServiceError as exc:
            if not exc.code.startswith("MODEL_"):
                raise
            return ModelGenerateResult(
                success=False,
                error_code="TIMEOUT" if exc.code == "MODEL_TIMEOUT" else "API_ERROR",
            )
        return ModelGenerateResult(success=True, text=text)