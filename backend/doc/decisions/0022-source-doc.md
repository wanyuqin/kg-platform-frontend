# ADR-0022：知识文件（source_doc）＝管理容器，条目仍是生命周期原子

- **状态**：已采纳
- **日期**：2026-07-04（P1 落地后浏览器实测发现 + 用户决策）
- **来源**：设计文档 3.1（Markdown 上传）、4.1.4（重拆对齐）、7.2（拆分预览确认页）；本仓 `docs/superpowers/specs/2026-07-04-source-doc-design.md`

## 背景

Markdown 导入把文件拆成条目后文件本身即被丢弃：`import_batch` 只是一次性解析记录，
用户无法从"一份 FAQ 文档"的视角查看拆出的全部条目，无法对文件整体做更新/续期/下架，
也没有平台内粘贴文本、在线编辑全文的入口（改个错字都要回本地改文件重传）。

## 决策

1. **文件＝管理容器，条目仍是原子**：新增 `source_doc` 表承载"知识文件"概念（命名、
   归属 domain、单一类型、来源、active/archived）；状态机、版本、过期日、检索命中
   全部保持条目级不变（ADR-0004 一条知识一个文件的存储粒度不受影响），文件级操作只是
   批量快捷方式。文件全文没有独立正文存储——**全文＝文件下非 draft/非 archived 条目
   按 `doc_seq` 拼合**，条目是唯一事实源。
2. **统一源文档模型**：`source` 枚举 `manual`（平台自建：粘贴/在线编辑/表单起建）/
   `upload`（上传 .md）/ `feishu`（P2 飞书接入复用本表与对齐链路，本期只留枚举）。
3. **所有条目必须属于一个文件**：`knowledge.source_doc_id`、`doc_seq` 非空，不存在
   游离条目；表单创建改为"往文件里添加条目"（选已有文件或就地新建，必选）。
4. **对齐消失默认下架、表单条目除外**：全文更新走重拆＋标题精确匹配对齐
   （unchanged / changed / new / disappeared 四类），disappeared 预览默认勾选（确认即
   下架）；但 `source_ref` 以 `form:` 开头的表单条目不在粘贴文本中属正常情况，标记
   disappeared 但默认**不勾选**，避免每次更新被误下架。

## 影响

- DB：新表 `source_doc`；`knowledge` + `source_doc_id`/`doc_seq`（非空）；
  `import_batch` + `source_doc_id`（可空，首次导入 confirm 时建档回填）/`origin`；
  `import_item` + `align_action`/`match_kid`（详见 [storage.md](../modules/storage.md)）；
- API：`/api/source-docs` 系列七个接口；`POST /api/imports` 增加粘贴文本入口；
  confirm 按 align_action 分派动作（详见 [console.md](../modules/console.md)）；
- kid、URI 规则不变（ADR-0019），Gateway 检索侧零改动；
- 改标题被判为"消失＋新增"是 P1 标题匹配的已知边界，由预览页人工纠正勾选，
  语义级对齐留给后续 LLM 兜底（ADR-0006 分级触发原则）。

## 明确不做（P1）

条目跨文件移动、归档文件解档、文件级独立过期日、LLM 语义对齐、飞书文档接入。

## 理由

文件视角是用户管理知识的自然单位，但生命周期原子若上移到文件级会推翻已拍板的
条目级状态机与检索模型；"容器 + 批量快捷方式"以最小改动补齐管理动线。表单条目
例外规则源于实测：外部文档粘贴更新永远不含表单追加的条目，默认勾选会造成反复误下架。
