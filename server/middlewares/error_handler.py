import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
logger=logging.getLogger("uvicorn.error")
class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self,request:Request,call_next):
        try:
            logger.info(f"Processing request error middleware: {request.method} {request.url}")
            response=await call_next(request)
            return response
        except Exception as e:
            logger.error(f"Error processing request {request.method} {request.url}: {str(e)}")
            return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})