"""知识状态机（技术设计文档 四）。

状态迁移的唯一入口。P2/P3 的事件已在转移表中定义，
调用方按阶段接入，状态机本身不再改动。
"""

from enum import StrEnum


class Status(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class Event(StrEnum):
    SAVE_DRAFT = "save_draft"  # P1：（新建）→ draft
    SUBMIT_PASS = "submit_pass"  # P1：draft → published（低风险）
    SUBMIT_RISK = "submit_risk"  # P2：draft → pending_review
    REVIEW_APPROVE = "review_approve"  # P2
    REVIEW_REJECT = "review_reject"  # P2
    UPDATE_CONTENT = "update_content"  # P1：published → published（version+1）
    EXPIRE = "expire"  # P3：过期扫描
    RENEW = "renew"  # P3：续期
    ARCHIVE = "archive"  # P1：下架，终态


class InvalidTransition(Exception):
    def __init__(self, current: Status | None, event: Event):
        self.current = current
        self.event = event
        super().__init__(f"illegal transition: {current} --{event}--> ?")


# (当前状态, 事件) -> 目标状态；None 表示新建
TRANSITIONS: dict[tuple[Status | None, Event], Status] = {
    (None, Event.SAVE_DRAFT): Status.DRAFT,
    (None, Event.SUBMIT_PASS): Status.PUBLISHED,  # 表单直接提交发布
    (None, Event.SUBMIT_RISK): Status.PENDING_REVIEW,  # 导入批次含文件内重复
    (Status.DRAFT, Event.SUBMIT_PASS): Status.PUBLISHED,
    (Status.DRAFT, Event.SUBMIT_RISK): Status.PENDING_REVIEW,
    (Status.PUBLISHED, Event.SUBMIT_RISK): Status.PENDING_REVIEW,  # 更新条目待审核入库
    (Status.PENDING_REVIEW, Event.REVIEW_APPROVE): Status.PUBLISHED,
    (Status.PENDING_REVIEW, Event.REVIEW_REJECT): Status.DRAFT,
    (Status.PUBLISHED, Event.UPDATE_CONTENT): Status.PUBLISHED,
    (Status.PUBLISHED, Event.EXPIRE): Status.EXPIRED,
    (Status.EXPIRED, Event.RENEW): Status.PUBLISHED,
    (Status.PUBLISHED, Event.ARCHIVE): Status.ARCHIVED,
    (Status.EXPIRED, Event.ARCHIVE): Status.ARCHIVED,
}


def transition(current: Status | None, event: Event) -> Status:
    """返回目标状态；非法迁移抛 InvalidTransition。archived 为终态。"""
    try:
        return TRANSITIONS[(current, event)]
    except KeyError:
        raise InvalidTransition(current, event) from None
