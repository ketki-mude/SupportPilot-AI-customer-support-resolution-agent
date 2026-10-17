"""Shared response shapes."""

from typing import Generic, TypeVar

from pydantic import BaseModel

ItemT = TypeVar("ItemT")


class Page(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    total: int
    page: int
    page_size: int
