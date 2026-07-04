"""OpenViking PoC 验证脚本（技术设计文档 9.1，结论回填 doc/modules/storage.md）。

用法（backend 目录下，需先起 docker compose 的 openviking）：
    uv run python scripts/poc_viking.py            # 全部六项
    uv run python scripts/poc_viking.py --quick    # 跳过 5/6（摘要抽检与批量灌入，依赖模型网关且耗时）

验证项：
  1. content/write 覆盖写语义与幂等性
  2. 写入 → 可被检索的实际延迟
  3. 多目录前缀检索（target_uri 数组，单次调用）
  4. L0/L1 就绪判据（content/abstract vs system/wait）
  5. 中文 L0 摘要质量抽检（20 条六类 mock，人工过目）
  6. 单目录批量条目的检索延迟
"""

import argparse
import statistics
import sys
import time

import httpx

sys.path.insert(0, ".")
from app.config import get_settings  # noqa: E402

POC_ROOT = "viking://resources/poc-kg"  # PoC 专用目录，脚本结束时整体删除

# 六类中文 mock 知识（模板段名与附录 A 一致），(类型, 相对路径, 标题, 正文段落)
MOCK_ENTRIES: list[tuple[str, str, str, dict[str, str]]] = [
    # --- faq ×5 ---
    (
        "faq",
        "faq/faq-poc-0001.md",
        "企业版发票如何申请？",
        {
            "标准问法": "企业版发票如何申请？",
            "相似问法": "- 怎么开发票？\n- 发票在哪里申请？",
            "标准答案": "登录管理后台 → 费用中心 → 发票管理，选择需开票订单并填写抬头提交。电子普票 1–3 个工作日开出，专票 5–7 个工作日邮寄。",
            "适用条件": "企业版付费客户；个人版仅支持电子普票",
            "例外情况": "渠道代理商付款的订单需联系代理商开票",
        },
    ),
    (
        "faq",
        "faq/faq-poc-0002.md",
        "发票抬头填错了怎么办？",
        {
            "标准问法": "发票抬头填错了怎么办？",
            "相似问法": "- 抬头写错了能改吗？\n- 发票信息填错如何处理？",
            "标准答案": "电子普票在发票管理页作废后重新申请即可；专票需先将纸质发票寄回，财务确认后重开。",
            "适用条件": "开票后 90 天内",
            "例外情况": "跨年度发票需走人工工单处理",
        },
    ),
    (
        "faq",
        "faq/faq-poc-0003.md",
        "免单资格如何判断？",
        {
            "标准问法": "免单资格如何判断？",
            "相似问法": "- 什么订单可以免单？\n- 免单条件是什么？",
            "标准答案": "订单因平台原因（配送超时 40 分钟以上、商品严重错漏）产生投诉且核实属实的，系统自动标记免单资格，用户无需申请。",
            "适用条件": "订单完成后 24 小时内发起的投诉",
            "例外情况": "恶劣天气期间配送超时不计入平台原因",
        },
    ),
    (
        "faq",
        "faq/faq-poc-0004.md",
        "账号可以多人共用吗？",
        {
            "标准问法": "账号可以多人共用吗？",
            "相似问法": "- 一个账号能几个人登录？\n- 支持子账号吗？",
            "标准答案": "主账号不建议共用。企业版支持创建子账号（管理后台 → 成员管理），每个成员独立登录、权限可配。",
            "适用条件": "企业版支持子账号；个人版单点登录",
            "例外情况": "无",
        },
    ),
    (
        "faq",
        "faq/faq-poc-0005.md",
        "订单取消后退款多久到账？",
        {
            "标准问法": "订单取消后退款多久到账？",
            "相似问法": "- 退款要等几天？\n- 取消订单钱什么时候退回？",
            "标准答案": "原路退回：微信/支付宝 1–3 个工作日，银行卡 3–7 个工作日。余额支付即时到账。",
            "适用条件": "支付成功且未发货的订单",
            "例外情况": "组合支付的订单各渠道分别退回，到账时间以最慢渠道为准",
        },
    ),
    # --- sop ×3 ---
    (
        "sop",
        "sop/sop-poc-0001.md",
        "免单审核操作流程",
        {
            "目标与适用场景": "客服核实用户免单申请的标准操作，适用于免单域全部工单。",
            "前置条件": "已取得工单号与订单号；客服具备免单审核权限",
            "操作步骤": "1. 在工单系统输入订单号调出订单详情，预期看到配送时间线；\n2. 核对超时时长是否 ≥40 分钟，预期系统自动标注超时原因；\n3. 确认投诉发起时间在订单完成后 24 小时内，预期工单状态为「待审核」；\n4. 点击「通过免单」，预期订单状态变更为「已免单」且用户收到通知。",
            "异常与分支处理": "超时原因标注为「恶劣天气」时转人工复核组；订单详情调取失败时按系统故障流程上报。",
            "完成标志": "订单状态为「已免单」，工单自动关闭。",
            "回滚方式": "误操作后 30 分钟内可在工单详情页点击「撤销免单」，超时需财务介入冲正。",
            "注意事项": "单日免单金额超 500 元需组长二次确认。",
        },
    ),
    (
        "sop",
        "sop/sop-poc-0002.md",
        "子账号开通流程",
        {
            "目标与适用场景": "为企业版客户开通成员子账号。",
            "前置条件": "主账号为企业版且成员数未达套餐上限",
            "操作步骤": "1. 管理后台进入「成员管理」，预期看到成员列表与「添加成员」按钮；\n2. 填写成员邮箱并选择角色，预期系统发送邀请邮件；\n3. 成员点击邮件链接设置密码，预期首次登录强制改密。",
            "异常与分支处理": "邮件未收到时检查垃圾箱，或在成员列表点「重发邀请」；成员数达上限时引导升级套餐。",
            "完成标志": "成员状态显示「已激活」。",
            "注意事项": "无",
        },
    ),
    (
        "sop",
        "sop/sop-poc-0003.md",
        "线上退款人工冲正流程",
        {
            "目标与适用场景": "自动退款失败后由客服发起人工冲正，适用于支付渠道异常场景。",
            "前置条件": "自动退款已失败且错误码为 CHANNEL_ERROR；客服具备退款操作权限",
            "操作步骤": "1. 在退款工单页点击「人工冲正」，预期弹出金额确认框；\n2. 核对退款金额与原支付金额一致，预期两者相等；\n3. 提交冲正申请，预期状态变为「待财务审核」。",
            "异常与分支处理": "金额不一致时禁止提交，转财务对账组核查。",
            "完成标志": "财务审核通过后退款状态为「已退款」。",
            "回滚方式": "财务审核通过前可在工单页撤回申请；审核通过后资金已动，需走反向收款流程。",
            "注意事项": "冲正涉及资金操作，禁止代客户确认金额。",
        },
    ),
    # --- policy ×3 ---
    (
        "policy",
        "policy/pol-poc-0001.md",
        "免单赔付政策",
        {
            "一句话摘要": "平台原因导致的严重履约问题按订单实付金额免单，每用户每月上限 3 次。",
            "适用范围": "全部即时配送订单；拼团与预售订单除外",
            "规则条款": "1. 配送超时 ≥40 分钟且原因为平台侧的，全额免单；\n2. 商品严重错漏（缺主品或错品类）的，全额免单；\n3. 每用户自然月免单不超过 3 次，超过后转优惠券补偿。",
            "例外条款": "恶劣天气、不可抗力期间的超时不适用；用户拒收导致的履约失败不适用。",
            "生效 / 失效时间": "2026-01-01 生效，长期有效，每年 12 月复审。",
            "罚则与违规处理": "骑手或商家伪造超时记录的，按作弊处理并追回赔付金额。",
            "制度依据来源": "《客户体验保障管理办法》第四章",
        },
    ),
    (
        "policy",
        "policy/pol-poc-0002.md",
        "子账号数据权限规范",
        {
            "一句话摘要": "子账号默认仅可见本人创建的数据，跨成员可见性须主账号显式授权。",
            "适用范围": "企业版全部子账号",
            "规则条款": "1. 子账号默认数据范围为「本人」；\n2. 角色为「管理员」的子账号可见全部成员数据；\n3. 权限变更即时生效并记录审计日志。",
            "例外条款": "对账单与发票信息仅主账号可见。",
            "生效 / 失效时间": "2025-06-01 生效，长期有效。",
            "罚则与违规处理": "无",
            "制度依据来源": "《企业客户数据安全规范》",
        },
    ),
    (
        "policy",
        "policy/pol-poc-0003.md",
        "退款时效承诺",
        {
            "一句话摘要": "平台承诺退款申请受理后 24 小时内发起原路退回。",
            "适用范围": "全部线上支付订单",
            "规则条款": "1. 未发货订单退款申请自动通过；\n2. 已发货订单需商家在 24 小时内响应，逾期系统自动同意；\n3. 退款发起后到账时间以支付渠道为准。",
            "例外条款": "涉嫌欺诈的订单冻结退款并转风控人工处理。",
            "生效 / 失效时间": "2025-03-15 生效，长期有效。",
            "罚则与违规处理": "商家恶意拖延退款的，按服务分扣减规则处理。",
            "制度依据来源": "《平台交易规则》第七节",
        },
    ),
    # --- product ×3 ---
    (
        "product",
        "product/prd-poc-0001.md",
        "发票管理功能说明",
        {
            "功能定义": "为企业客户提供订单批量开票、抬头管理、开票记录查询能力。",
            "适用版本 / 套餐": "企业版全部套餐；个人版仅支持单笔电子普票",
            "能力边界": "支持：电子普票、增值税专票、批量勾选开票、抬头收藏。\n不支持：跨主体合并开票、纸质普票、开票金额拆分。",
            "使用入口": "管理后台 → 费用中心 → 发票管理",
            "限制与配额": "单次批量开票最多 200 笔订单；专票单张金额上限 10 万元。",
            "常见误解澄清": "「批量开票」合并的是申请动作，不是把多笔订单合成一张发票；每笔订单仍对应独立发票。",
        },
    ),
    (
        "product",
        "product/prd-poc-0002.md",
        "成员管理功能说明",
        {
            "功能定义": "企业主账号创建、停用子账号并配置角色权限的功能模块。",
            "适用版本 / 套餐": "企业版标准套餐及以上",
            "能力边界": "支持：邮箱邀请、角色配置（管理员/成员）、批量停用。\n不支持：跨企业成员共享、SSO 单点登录（规划中）。",
            "使用入口": "管理后台 → 设置 → 成员管理",
            "限制与配额": "标准套餐 10 个子账号，旗舰套餐 100 个。",
            "常见误解澄清": "停用成员不删除其历史数据，数据归属保留可查。",
        },
    ),
    (
        "product",
        "product/prd-poc-0003.md",
        "订单导出功能说明",
        {
            "功能定义": "按筛选条件将订单列表导出为 CSV/Excel 文件。",
            "适用版本 / 套餐": "全部版本",
            "能力边界": "支持：按时间/状态/门店筛选导出、异步大文件导出。\n不支持：自定义字段模板、定时自动导出。",
            "使用入口": "管理后台 → 订单 → 导出",
            "限制与配额": "单次导出上限 10 万行；导出文件保留 7 天。",
            "常见误解澄清": "导出金额为下单实付口径，与财务对账单的结算口径存在时间差。",
        },
    ),
    # --- case ×3 ---
    (
        "case",
        "case/case-poc-0001.md",
        "退款按钮置灰无法点击",
        {
            "问题现象": "用户订单详情页退款按钮置灰，无法发起退款。",
            "触发条件与根因": "订单处于「配送中」状态时退款入口按设计关闭；用户误以为故障。",
            "排查步骤": "1. 核对订单当前状态；\n2. 确认是否配送中；\n3. 告知用户签收后或联系骑手取消配送后再操作。",
            "解决方案": "属产品设计而非故障：引导用户等待配送节点结束后发起退款，紧急情况由客服后台代发起。",
            "影响范围": "全部即时配送订单",
            "预防措施": "置灰按钮增加悬浮提示文案，说明可退款的时点。",
        },
    ),
    (
        "case",
        "case/case-poc-0002.md",
        "批量开票任务卡在处理中",
        {
            "问题现象": "企业客户批量开票任务停留「处理中」超过 24 小时。",
            "触发条件与根因": "单次勾选订单跨了纳税主体变更日，税号校验循环重试导致任务挂起。",
            "排查步骤": "1. 后台查任务日志确认卡点；\n2. 核对订单时间范围是否跨主体变更日；\n3. 确认税号校验错误码。",
            "解决方案": "运营后台终止原任务，指导客户按主体变更日拆成两批重新提交。",
            "影响范围": "纳税主体近期变更过的企业客户",
            "预防措施": "开票提交时增加跨主体日期的前置拦截提示。",
        },
    ),
    (
        "case",
        "case/case-poc-0003.md",
        "成员邀请邮件收不到",
        {
            "问题现象": "新成员反馈未收到子账号邀请邮件。",
            "触发条件与根因": "未知",
            "排查步骤": "1. 确认邮箱地址拼写；\n2. 查发送日志确认投递状态；\n3. 让用户检查垃圾邮件；\n4. 换绑其他邮箱重试。",
            "解决方案": "重发邀请或换绑邮箱；若发送日志显示投递失败，提交工单给基础设施组查发信信誉。",
            "影响范围": "个别企业邮箱域名",
            "预防措施": "邀请页提示常见企业邮箱网关拦截问题。",
        },
    ),
    # --- term ×3 ---
    (
        "term",
        "term/term-poc-0001.md",
        "免单",
        {
            "术语名": "免单",
            "定义": "因平台原因导致严重履约问题时，按订单实付金额全额减免用户费用的赔付动作。",
            "同义词 / 别名": "全额赔付、订单减免",
            "使用示例": "该订单配送超时 45 分钟，符合免单条件。",
            "易混淆术语辨析": "与「退款」不同：免单是平台承担损失的赔付，退款是交易撤销的资金原路退回。",
        },
    ),
    (
        "term",
        "term/term-poc-0002.md",
        "原路退回",
        {
            "术语名": "原路退回",
            "定义": "退款资金沿原支付渠道返还到用户付款账户的退款方式。",
            "同义词 / 别名": "原渠道退款",
            "使用示例": "微信支付的订单退款将原路退回到用户微信零钱或绑定银行卡。",
            "易混淆术语辨析": "与「余额退款」不同：余额退款进平台账户余额，原路退回进外部支付账户。",
        },
    ),
    (
        "term",
        "term/term-poc-0003.md",
        "纳税主体",
        {
            "术语名": "纳税主体",
            "定义": "企业客户用于开具发票的法定纳税实体，对应唯一税号。",
            "同义词 / 别名": "开票主体",
            "使用示例": "客户本月完成了纳税主体变更，需要按变更日拆分开票批次。",
            "易混淆术语辨析": "与「账号主体」不同：一个平台账号可先后关联不同纳税主体，开票以开票时点的主体为准。",
        },
    ),
]


def render(title: str, sections: dict[str, str]) -> str:
    parts = [f"# {title}"]
    for name, body in sections.items():
        parts.append(f"## {name}\n{body}")
    return "\n\n".join(parts) + "\n"


POC_ACCOUNT = "kg"
POC_USER = "kg-backend"


def ensure_user_key(base_url: str, root_key: str) -> str:
    """PoC 发现：api_key 模式下 ROOT key 禁止访问数据 API（403 PERMISSION_DENIED），
    须用 root key 经 admin API 创建 account/user 后改用用户级 key。幂等。"""
    admin = httpx.Client(base_url=base_url, headers={"x-api-key": root_key}, timeout=10.0)
    users = admin.get(f"/api/v1/admin/accounts/{POC_ACCOUNT}/users")
    if users.status_code == 200 and users.json().get("result"):
        for u in users.json()["result"]:
            if u["user_id"] == POC_USER:
                return u["api_key"]
    created = admin.post(
        "/api/v1/admin/accounts",
        json={"account_id": POC_ACCOUNT, "admin_user_id": POC_USER},
    )
    created.raise_for_status()
    return created.json()["result"]["user_key"]


class Poc:
    def __init__(self) -> None:
        s = get_settings()
        user_key = ensure_user_key(s.viking_base_url, s.viking_api_key)
        print(f"已获取用户级 API key（account={POC_ACCOUNT}, user={POC_USER}）", flush=True)
        self.client = httpx.Client(
            base_url=s.viking_base_url,
            headers={"x-api-key": user_key},
            timeout=60.0,  # 写入触发 L0/L1 生成，远超线上 800ms 检索预算，PoC 放宽
        )
        self.conclusions: list[str] = []

    def log(self, msg: str) -> None:
        print(msg, flush=True)

    def conclude(self, item: str, verdict: str) -> None:
        line = f"[结论] {item}：{verdict}"
        self.conclusions.append(line)
        self.log(f"\n{line}\n")

    # ---- 基础调用 ----
    def write(self, uri: str, content: str, wait: bool = False) -> httpx.Response:
        """upsert：replace 覆盖已有文件，404（不存在）时降级 create（父目录自动创建）。

        PoC 发现：content/write 的 mode 语义为 create=新建（已存在 409 ALREADY_EXISTS）、
        replace=覆盖（不存在 404 NOT_FOUND），"同 URI 幂等覆盖"需客户端组合实现。
        """
        payload = {"uri": uri, "content": content, "wait": wait, "mode": "replace"}
        r = self.client.post("/api/v1/content/write", json=payload)
        if r.status_code == 404:
            r = self.client.post("/api/v1/content/write", json={**payload, "mode": "create"})
        return r

    def find(self, query: str, target_uri, limit: int = 5) -> httpx.Response:
        return self.client.post(
            "/api/v1/search/find",
            json={"query": query, "target_uri": target_uri, "limit": limit},
        )

    def abstract(self, uri: str) -> httpx.Response:
        return self.client.get("/api/v1/content/abstract", params={"uri": uri})

    def abstract_ready(self, uri: str) -> tuple[bool, str]:
        """L0 就绪 = HTTP 200 且 result 非占位符（未生成时返回 "[... not generated]"）。"""
        r = self.abstract(uri)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        result = r.json().get("result") or ""
        if not result or "not generated" in result:
            return False, result
        return True, result

    def rm(self, uri: str) -> httpx.Response:
        return self.client.delete("/api/v1/fs", params={"uri": uri, "recursive": True})

    # ---- 验证项 ----
    def item1_overwrite_idempotent(self) -> None:
        self.log("=== 1. content/write 覆盖写语义与幂等性 ===")
        uri = f"{POC_ROOT}/faq/faq-poc-overwrite.md"
        v1 = render("覆盖写测试", {"标准问法": "第一版内容？", "标准答案": "版本一。"})
        v2 = render("覆盖写测试", {"标准问法": "第二版内容？", "标准答案": "版本二。"})
        # wait=False：覆盖写语义验证不依赖语义索引完成（read 走文件层，写完即可读）；
        # PoC 实测 wait=True 会阻塞到 L0/L1 生成完成，60s 内不返回
        r1 = self.write(uri, v1)
        self.log(f"首次写入: {r1.status_code} {r1.text[:200]}")
        r2 = self.write(uri, v2)
        self.log(f"同 URI 二次写入: {r2.status_code} {r2.text[:200]}")
        r3 = self.write(uri, v2)
        self.log(f"同内容重复写入: {r3.status_code} {r3.text[:200]}")
        read = self.client.get("/api/v1/content/read", params={"uri": uri, "raw": True})
        body = read.text
        ok_overwrite = "版本二" in body and "版本一" not in body
        ls = self.client.get("/api/v1/fs/ls", params={"uri": f"{POC_ROOT}/faq"})
        self.log(f"read 校验含版本二: {ok_overwrite}; ls: {ls.status_code} {ls.text[:300]}")
        self.conclude(
            "覆盖写幂等",
            f"二次写入 HTTP {r2.status_code}、重复写入 HTTP {r3.status_code}，"
            f"read 返回{'最新内容，旧内容已被覆盖' if ok_overwrite else '异常（含旧内容），需人工确认！'}",
        )

    def item2_write_to_searchable_latency(self) -> None:
        self.log("=== 2. 写入 → 可被检索的实际延迟（wait=False 异步写） ===")
        uri = f"{POC_ROOT}/faq/faq-poc-latency.md"
        marker = "延迟测试专用不重复词组蓝鲸打伞"
        content = render(
            marker, {"标准问法": f"{marker}是什么？", "标准答案": "用于测量写入到可检索延迟。"}
        )
        t0 = time.monotonic()
        r = self.write(uri, content, wait=False)
        write_ms = (time.monotonic() - t0) * 1000
        self.log(f"异步写入返回: {r.status_code}（{write_ms:.0f}ms）")
        deadline = time.monotonic() + 300
        hit_at = None
        while time.monotonic() < deadline:
            resp = self.find(marker, f"{POC_ROOT}/faq", limit=5)
            if resp.status_code == 200 and "faq-poc-latency" in resp.text:
                hit_at = time.monotonic() - t0
                break
            time.sleep(2)
        if hit_at is None:
            self.conclude("写入→可检索延迟", "300s 内未检索到，需人工排查（模型网关/队列）！")
        else:
            self.conclude(
                "写入→可检索延迟", f"约 {hit_at:.1f}s（异步写返回 {write_ms:.0f}ms 后台处理）"
            )

    def item3_multi_prefix_search(self) -> None:
        self.log("=== 3. 多目录前缀检索（target_uri 数组单次调用） ===")
        r = self.find("发票如何申请", [f"{POC_ROOT}/faq", f"{POC_ROOT}/policy"], limit=5)
        self.log(f"数组 target_uri: {r.status_code} {r.text[:400]}")
        single = self.find("发票如何申请", f"{POC_ROOT}/faq", limit=5)
        ok = r.status_code == 200
        self.conclude(
            "多前缀检索形态",
            f"target_uri 传数组 HTTP {r.status_code}（单值 HTTP {single.status_code}），"
            + (
                "单次多前缀调用可行，search 第 2 步无需 N 次合并"
                if ok
                else "数组不被接受，需 N 次调用合并！"
            ),
        )

    def item4_readiness_probe(self) -> None:
        """就绪判据定案（PoC 实测）：find probe 检索命中该 uri 的 level=2 条目。

        已排除的候选：content/abstract 对文件 URI 恒返回目录级 fallback（不可判文件）；
        fs/ls 的 abstract 字段不及时回填；tasks 不追踪写入任务；system/wait 是全局等待。
        """
        self.log("=== 4. L0/L1 就绪判据（find probe level=2 命中） ===")
        uri = f"{POC_ROOT}/faq/faq-poc-ready.md"
        probe = "就绪判据怎么查"
        content = render(
            "就绪判据测试", {"标准问法": "就绪判据怎么查？", "标准答案": "find probe 命中即就绪。"}
        )
        self.write(uri, content, wait=False)
        ready = False
        deadline = time.monotonic() + 300
        t0 = time.monotonic()
        while time.monotonic() < deadline:
            r = self.find(probe, f"{POC_ROOT}/faq", limit=10)
            if r.status_code == 200:
                resources = (r.json().get("result") or {}).get("resources") or []
                if any(x.get("uri") == uri and x.get("level") == 2 for x in resources):
                    ready = True
                    self.log(f"find probe 命中 level=2（{time.monotonic() - t0:.1f}s）")
                    break
            time.sleep(2)
        _, abstract_detail = self.abstract_ready(uri)
        self.conclude(
            "就绪判据",
            (
                f"find probe（限父目录）level=2 命中可用，{time.monotonic() - t0:.1f}s 就绪，"
                "已落地 client.is_indexed"
                if ready
                else "find probe 300s 未命中，需人工排查！"
            )
            + f"；content/abstract 对文件恒返回目录级 fallback（「{abstract_detail[:30]}…」），不可作文件级判据",
        )

    def item5_chinese_abstract_quality(self) -> None:
        self.log("=== 5. 中文 L0 摘要质量抽检（20 条六类 mock） ===")
        for i, (_type, rel, title, sections) in enumerate(MOCK_ENTRIES, 1):
            uri = f"{POC_ROOT}/{rel}"
            r = self.write(uri, render(title, sections), wait=False)
            self.log(f"[{i}/{len(MOCK_ENTRIES)}] write {rel}: {r.status_code}")
        self.log("等待全部处理完成（system/wait，最长 15 分钟）…")
        self.client.post("/api/v1/system/wait", json={"timeout": 900}, timeout=920.0)
        # 文件级 L0 只能从 find 命中项的 abstract 字段取（content/abstract 恒返回目录级 fallback）
        shown = 0
        for _type, rel, title, _sections in MOCK_ENTRIES:
            uri = f"{POC_ROOT}/{rel}"
            parent = uri.rsplit("/", 1)[0]
            r = self.find(title, parent, limit=10)
            detail = ""
            if r.status_code == 200:
                for item in (r.json().get("result") or {}).get("resources") or []:
                    if item.get("uri") == uri and item.get("level") == 2:
                        detail = item.get("abstract") or ""
                        break
            if detail:
                shown += 1
                self.log(f"\n--- [{_type}] {title}\nL0: {detail[:300]}")
            else:
                self.log(f"\n--- [{_type}] {title}\nL0 未就绪或未命中")
        self.conclude(
            "中文 L0 摘要抽检",
            f"{shown}/{len(MOCK_ENTRIES)} 条摘要已生成并打印在上方，**质量请人工过目**（自包含性、是否保留关键条件）",
        )

    def item6_bulk_search_latency(self) -> None:
        self.log("=== 6. 单目录批量条目的检索延迟（复用第 5 项灌入的数据） ===")
        queries = [
            "发票怎么开",
            "退款多久到账",
            "免单的条件",
            "子账号权限",
            "批量开票卡住了",
            "什么是原路退回",
        ]
        latencies = []
        for q in queries * 3:
            t0 = time.monotonic()
            r = self.find(q, POC_ROOT, limit=5)
            ms = (time.monotonic() - t0) * 1000
            if r.status_code == 200:
                latencies.append(ms)
        if latencies:
            avg = statistics.mean(latencies)
            p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
            self.conclude(
                "批量条目检索延迟",
                f"{len(MOCK_ENTRIES)}+ 条目录下 {len(latencies)} 次查询：avg {avg:.0f}ms / p95 {p95:.0f}ms"
                f"（线上预算 800ms {'内 ✓' if p95 < 800 else '外 ✗，需关注！'}；500 条压测待免单域真实数据灌入后复测）",
            )
        else:
            self.conclude("批量条目检索延迟", "查询全部失败，需人工排查！")

    def cleanup(self) -> None:
        r = self.rm(POC_ROOT)
        self.log(f"清理 PoC 目录 {POC_ROOT}: {r.status_code}")

    def run(self, quick: bool) -> None:
        health = self.client.get("/health")
        self.log(f"health: {health.status_code} {health.text}")
        try:
            self.item1_overwrite_idempotent()
            self.item2_write_to_searchable_latency()
            self.item3_multi_prefix_search()
            self.item4_readiness_probe()
            if not quick:
                self.item5_chinese_abstract_quality()
                self.item6_bulk_search_latency()
        finally:
            self.cleanup()
        print("\n" + "=" * 60)
        print("PoC 结论汇总（回填 doc/modules/storage.md）：")
        for c in self.conclusions:
            print(" ", c)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="跳过摘要抽检与批量灌入（第 5/6 项）")
    args = ap.parse_args()
    Poc().run(quick=args.quick)
