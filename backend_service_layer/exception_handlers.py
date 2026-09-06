import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from services.shared.src.blockchain_client import (
    BlockchainClientError, ContractNotDeployedError, ProviderUnavailableError,
    ContractCallError, ContractWriteError, TransactionRevertedError,
    NonceError, InputValidationError,
)
from .services.agent_service import AgentNotFoundError

logger = logging.getLogger(__name__)

_STATUS_MAP = {
    AgentNotFoundError: (404, True),
    InputValidationError: (400, True),
    ContractNotDeployedError: (503, True),
    ProviderUnavailableError: (503, True),
    TransactionRevertedError: (502, True),
    ContractCallError: (502, False),
    ContractWriteError: (502, False),
    NonceError: (502, False),
}

def _error_response(request: Request, error_id: str, status_code: int, message: str):
    return JSONResponse(status_code=status_code, content={"error_id": error_id, "message": message, "path": str(request.url.path)})

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BlockchainClientError)
    @app.exception_handler(AgentNotFoundError)
    async def handle_known_errors(request: Request, exc: Exception):
        error_id = str(uuid.uuid4())
        status_code, expose_message = _STATUS_MAP.get(type(exc), (500, False))
        logger.error("Handled exception: %s", exc, extra={"context": {"error_id": error_id, "error_type": type(exc).__name__, "path": request.url.path}})
        message = str(exc) if expose_message else "An internal error occurred while processing this request."
        return _error_response(request, error_id, status_code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected_errors(request: Request, exc: Exception):
        error_id = str(uuid.uuid4())
        logger.exception("Unhandled exception, error_id=%s", error_id)
        
        import os
        env = os.environ.get("APP_ENV", "development")
        if env == "development":
            import traceback
            return _error_response(request, error_id, 500, f"{type(exc).__name__}: {exc}")
        return _error_response(request, error_id, 500, "An unexpected error occurred. Reference this error_id if reporting the issue.")
