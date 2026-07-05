"""飞书 API 与同步流程专用异常。"""

# 飞书错误码 → 平台错误码（feishu-sync §4.4）
FEISHU_ERROR_MAP: dict[str, tuple[str, str]] = {
    "99991663": ("feishu_doc_not_found", "文档已被删除或分享链接失效"),
    "99991672": ("feishu_wiki_no_scope", "请申请 wiki:wiki:readonly scope"),
    "231002": (
        "feishu_app_not_in_kb",
        "应用未被授权到此文档所在知识库",
    ),
    "99991668": ("feishu_app_disabled", "飞书应用已被禁用，请联系平台管理员"),
}

ACTION_GUIDE_APP_NOT_IN_KB = (
    "请在飞书客户端打开文档所属知识库，将「知识平台机器人」添加为「仅阅读」成员"
)


class FeishuError(Exception):
    """飞书 OpenAPI 调用失败。"""

    def __init__(self, message: str, *, feishu_code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.feishu_code = feishu_code
        self.http_status = http_status


class FeishuPermissionError(FeishuError):
    """权限预检失败（§4.4）；映射为控制台可展示的错误码。"""

    def __init__(
        self,
        platform_code: str,
        message: str,
        *,
        action_guide: str | None = None,
        feishu_code: str | None = None,
    ):
        super().__init__(message, feishu_code=feishu_code)
        self.platform_code = platform_code
        self.action_guide = action_guide


def map_feishu_error(feishu_code: str | int | None, default_message: str = "飞书 API 错误") -> FeishuPermissionError:
    """将飞书业务错误码映射为 FeishuPermissionError。"""
    code = str(feishu_code) if feishu_code is not None else ""
    if code in FEISHU_ERROR_MAP:
        platform_code, message = FEISHU_ERROR_MAP[code]
        guide = ACTION_GUIDE_APP_NOT_IN_KB if platform_code == "feishu_app_not_in_kb" else None
        return FeishuPermissionError(platform_code, message, action_guide=guide, feishu_code=code)
    return FeishuPermissionError("feishu_api_error", default_message, feishu_code=code or None)
