from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.predict import router as predict_router
from app.api.test_endpoints import router as api_router
from app.api.model_eval import router as model_eval_router
from app.api.teams import router as teams_router
from app.api.payments import router as payments_router
from app.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(application: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Futbol Bot API", lifespan=lifespan)

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(predict_router, prefix="/predict", tags=["predict"])
app.include_router(api_router, prefix="/api", tags=["api"])
app.include_router(model_eval_router, prefix="/model", tags=["model"])
app.include_router(teams_router, prefix="/teams", tags=["teams"])
app.include_router(payments_router, prefix="/payments", tags=["payments"])