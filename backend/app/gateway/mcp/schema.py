"""MCP tool inputSchema 定义（单文件维护，避免与 HTTP Pydantic 漂移）。"""

from app.domain.kid import KNOWLEDGE_TYPES

SEARCH_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 512,
            "description": "检索关键词",
        },
        "type": {
            "type": "array",
            "items": {"type": "string", "enum": list(KNOWLEDGE_TYPES)},
            "description": "按知识类型过滤",
        },
        "tag": {
            "type": "array",
            "items": {"type": "string"},
            "description": "按标签过滤（OR）",
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "返回条数",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

READ_TOOL_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "kid": {
            "type": "string",
            "description": "知识条目 ID（含 .md 后缀亦可）",
        },
    },
    "required": ["kid"],
    "additionalProperties": False,
}
