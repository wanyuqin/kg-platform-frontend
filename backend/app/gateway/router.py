"""Gateway 对外接口（技术设计文档 六）。

请求校验按 6.2 契约实现；检索与取全文的业务逻辑
待 OpenViking PoC（9.1）后接入，当前返回 501。
"""

from typing import Annotated

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field, field_validator

from app import errors
from app.domain.kid import KNOWLEDGE_TYPES

router = APIRouter(prefix="/v1", tags=["gateway"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=512)
    type: list[str] | None = None
    tag: list[str] | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _trim(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query must not be blank")
        return v

    @field_validator("type")
    @classmethod
    def _known_types(cls, v: list[str] | None) -> list[str] | None:
        if v:
            unknown = [t for t in v if t not in KNOWLEDGE_TYPES]
            if unknown:
                raise ValueError(f"unknown type: {', '.join(unknown)}")
        return v


def _require_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise errors.unauthorized()
    return authorization.removeprefix("Bearer ").strip()


@router.post("/search")
async def search(
    body: SearchRequest,
    authorization: Annotated[str | None, Header()] = None,
):
    _require_bearer(authorization)
    # TODO(P1): 鉴权缓存比对（十）→ 限流（10.2）→ OpenViking 检索 →
    #           PG 回查过滤（published + 未过期，excluded_expired 计数）→ 审计（十一）
    raise errors.not_implemented("POST /v1/search")


@router.get("/knowledge/{kid}")
async def get_knowledge(
    kid: str,
    authorization: Annotated[str | None, Header()] = None,
):
    _require_bearer(authorization)
    # TODO(P1): 白名单/状态/过期校验（任一不满足统一 404）→ 从 knowledge_version 快照取 content
    raise errors.not_implemented("GET /v1/knowledge/{kid}")
