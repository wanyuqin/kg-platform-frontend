"""飞书 URL / wiki 解析 + 权限预检（feishu-sync §4）。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

from app.feishu.client import FeishuClient
from app.feishu.exceptions import FeishuError, FeishuPermissionError

# docx / 旧版 doc / wiki 节点
_DOCX_RE = re.compile(r"/docx/([A-Za-z0-9_-]+)")
_DOC_RE = re.compile(r"/docs/([A-Za-z0-9_-]+)")
_WIKI_RE = re.compile(r"/wiki/([A-Za-z0-9_-]+)")

FeishuDocType = Literal["docx", "doc", "wiki"]
# 飞书 get_node 的 obj_type：旧版为整型（22/1/16），新版为字符串（docx/doc/wiki）
_WIKI_OBJ_DOCX = frozenset({22, "22", "docx"})
_WIKI_OBJ_DOC = frozenset({1, "1", "doc"})
_WIKI_OBJ_WIKI = frozenset({16, "16", "wiki"})


def _wiki_obj_kind(obj_type: object) -> FeishuDocType | None:
    """将 get_node 返回的 obj_type 规范为 docx / doc / wiki。"""
    if obj_type in _WIKI_OBJ_DOCX:
        return "docx"
    if obj_type in _WIKI_OBJ_DOC:
        return "doc"
    if obj_type in _WIKI_OBJ_WIKI:
        return "wiki"
    return None


@dataclass(frozen=True)
class ResolvedDoc:
    obj_type: FeishuDocType
    document_token: str
    doc_url: str
    title: str | None = None


@dataclass(frozen=True)
class PermissionCheck:
    ok: bool
    error_code: str | None = None
    error_message: str | None = None
    action_guide: str | None = None


def parse_feishu_url(url: str) -> ResolvedDoc:
    """从 URL 正则解析初始 (obj_type, token)；wiki 需后续调 API 展开。"""
    url = url.strip()
    parsed = urlparse(url)
    path = parsed.path or url

    if m := _DOCX_RE.search(path):
        token = m.group(1)
        return ResolvedDoc("docx", token, _canonical_url(parsed, f"/docx/{token}"))
    if m := _DOC_RE.search(path):
        token = m.group(1)
        return ResolvedDoc("doc", token, _canonical_url(parsed, f"/docs/{token}"))
    if m := _WIKI_RE.search(path):
        token = m.group(1)
        return ResolvedDoc("wiki", token, _canonical_url(parsed, f"/wiki/{token}"))

    raise FeishuError(f"无法识别的飞书文档 URL: {url}")


def _canonical_url(parsed, suffix: str) -> str:
    host = parsed.netloc or "feishu.cn"
    scheme = parsed.scheme or "https"
    return f"{scheme}://{host}{suffix}"


async def resolve_wiki_to_doc(client: FeishuClient, wiki: ResolvedDoc) -> ResolvedDoc:
    """wiki 节点 → 实际 docx/doc token（§4.2）。"""
    if wiki.obj_type != "wiki":
        return wiki

    node = await client.get_wiki_node(wiki.document_token)
    node_data = node.get("node") or node
    obj_type_raw = node_data.get("obj_type")
    obj_token = node_data.get("obj_token") or node_data.get("object_token")
    if not obj_token:
        raise FeishuError("wiki 节点缺少 obj_token")

    kind = _wiki_obj_kind(obj_type_raw)
    if kind == "docx":
        return ResolvedDoc("docx", obj_token, wiki.doc_url, title=node_data.get("title"))
    if kind == "doc":
        return ResolvedDoc("doc", obj_token, wiki.doc_url, title=node_data.get("title"))
    if kind == "wiki":
        nested = ResolvedDoc("wiki", obj_token, wiki.doc_url, title=node_data.get("title"))
        return await resolve_wiki_to_doc(client, nested)

    raise FeishuError(f"不支持的 wiki 对象类型 obj_type={obj_type_raw}")


async def resolve_doc(client: FeishuClient, url: str) -> ResolvedDoc:
    """完整解析：URL → (obj_type, document_token, title?)。"""
    initial = parse_feishu_url(url)
    if initial.obj_type == "wiki":
        resolved = await resolve_wiki_to_doc(client, initial)
    else:
        resolved = initial

    if resolved.obj_type == "doc":
        raise FeishuError("旧版 doc 格式暂不支持同步，请迁移到 docx")

    # docx 拉元信息补 title
    meta = await client.get_document_meta(resolved.document_token)
    title = meta.get("document", {}).get("title") or resolved.title
    return ResolvedDoc(resolved.obj_type, resolved.document_token, resolved.doc_url, title=title)


async def check_permission(client: FeishuClient, document_token: str) -> PermissionCheck:
    """权限预检（§4.4）；成功返回 ok=True。"""
    try:
        await client.check_document_permission(document_token)
        return PermissionCheck(ok=True)
    except FeishuPermissionError as exc:
        return PermissionCheck(
            ok=False,
            error_code=exc.platform_code,
            error_message=str(exc),
            action_guide=exc.action_guide,
        )


async def resolve_with_permission(client: FeishuClient, url: str) -> tuple[ResolvedDoc, PermissionCheck]:
    """绑定前预览：解析 URL + 权限预检。"""
    initial = parse_feishu_url(url)
    if initial.obj_type == "wiki":
        try:
            resolved = await resolve_wiki_to_doc(client, initial)
        except FeishuPermissionError as exc:
            return initial, PermissionCheck(
                ok=False,
                error_code=exc.platform_code,
                error_message=str(exc),
                action_guide=exc.action_guide,
            )
    else:
        resolved = initial

    if resolved.obj_type == "doc":
        return resolved, PermissionCheck(
            ok=False,
            error_code="feishu_api_error",
            error_message="旧版 doc 格式暂不支持同步，请迁移到 docx",
        )

    perm = await check_permission(client, resolved.document_token)
    if perm.ok:
        try:
            meta = await client.get_document_meta(resolved.document_token)
            title = meta.get("document", {}).get("title") or resolved.title
            resolved = ResolvedDoc(
                resolved.obj_type, resolved.document_token, resolved.doc_url, title=title
            )
        except FeishuPermissionError:
            pass
    return resolved, perm
