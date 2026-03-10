from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.predict import router as predict_router
from app.api.test_endpoints import router as test_router

app = FastAPI(title="Futbol Bot API")

app.include_router(health_router, prefix="/health", tags=["health"])
app.include_router(predict_router, prefix="/predict", tags=["predict"])
app.include_router(test_router, prefix="/test", tags=["test"])