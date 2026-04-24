from fastapi import  Request
import time
import logging
logger=logging.getLogger(__name__)
logger.propagate = False
from starlette.middleware.base import BaseHTTPMiddleware
class latency_middleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["Process-Time"]=str(process_time)
        logger.info(f"Request: {request.method} {request.url} processed in {process_time:.4f} seconds")
        return response

