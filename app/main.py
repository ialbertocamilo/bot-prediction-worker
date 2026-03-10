from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.predict import router as predict_router
from app.api.test_endpoints import router as test_router
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(application: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Futbol Bot API", lifespan=lifespan)

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(predict_router, prefix="/predict", tags=["predict"])
app.include_router(test_router, prefix="/test", tags=["test"])