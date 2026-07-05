"""知识溯源字段解析（Gateway / 控制台共用）。"""

from app.storage.pg.models import SourceDoc


def resolve_source_title(doc: SourceDoc | None) -> str | None:
    """原文/所属文章标题：自建取平台标题，飞书取同步的文档标题，缺省回退 name。"""
    if doc is None:
        return None
    return doc.source_title or doc.name
