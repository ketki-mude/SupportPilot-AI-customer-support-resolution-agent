"""The error envelope, declared for OpenAPI docs."""

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    loc: list[str | int]
    message: str
    type: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody
