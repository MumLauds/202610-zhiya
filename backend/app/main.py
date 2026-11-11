from fastapi import FastAPI

from app.api.v1.health import router

app = FastAPI(title="知芽 API")
app.include_router(router)
