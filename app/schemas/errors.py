"""The structured error envelope every error response uses, regardless of
source (Pydantic validation, a plain HTTPException, or a domain exception
like IdempotencyKeyConflictError). See app/main.py's exception handlers."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
