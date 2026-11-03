from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str


app = FastAPI(title="AI Visibility Audit Agent")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
