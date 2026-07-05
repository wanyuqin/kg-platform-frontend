"""飞书同步 MQ 消息协议（feishu-sync §11）。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Literal

TriggerKind = Literal["event", "poll", "manual"]


@dataclass
class FeishuEventMessage:
    source_doc_id: int
    feishu_doc_token: str
    feishu_doc_type: str
    triggered_by: TriggerKind
    retry_count: int = 0
    enqueued_at: str = ""

    def __post_init__(self) -> None:
        if not self.enqueued_at:
            self.enqueued_at = datetime.now(UTC).isoformat()

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), ensure_ascii=False).encode()

    @classmethod
    def from_bytes(cls, raw: bytes) -> FeishuEventMessage:
        data = json.loads(raw.decode())
        return cls(
            source_doc_id=int(data["source_doc_id"]),
            feishu_doc_token=data["feishu_doc_token"],
            feishu_doc_type=data["feishu_doc_type"],
            triggered_by=data["triggered_by"],
            retry_count=int(data.get("retry_count") or 0),
            enqueued_at=data.get("enqueued_at") or "",
        )

    def with_retry(self) -> FeishuEventMessage:
        return FeishuEventMessage(
            source_doc_id=self.source_doc_id,
            feishu_doc_token=self.feishu_doc_token,
            feishu_doc_type=self.feishu_doc_type,
            triggered_by=self.triggered_by,
            retry_count=self.retry_count + 1,
        )
