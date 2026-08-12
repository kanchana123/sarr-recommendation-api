"""AWS Lambda entrypoint (API Gateway HTTP API → Mangum → FastAPI)."""

from mangum import Mangum

from sarr.api.app import app

handler = Mangum(app, lifespan="off")
