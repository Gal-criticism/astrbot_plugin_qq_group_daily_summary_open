# 工作群个人工作总结 — 实现方案

## 需求

工作群每天大量消息，需要根据过去 24 小时的聊天记录，总结**每个人的工作内容**。

关键约束：
- 一个人的工作可能是别人指派的（如「小王你去做竞品分析」 → 小王只回「好的」），所以不能只看本人发言，必须让 LLM 从**全量对话上下文**中推断。

---

## 方案概述

**单次 LLM 调用，全量消息输入，一次性产出所有人的工作总结。**

与现有的「话题分析器（TopicAnalyzer）」模式完全一致：全量对话 → LLM → 结构化 JSON 输出，只是输出 schema 不同。

---

## 实现清单

### 1. 新增实体 `WorkSummary`

**文件**：`src/domain/entities/analysis_result.py`

```python
@dataclass
class WorkSummary:
    """个人工作总结"""
    user_id: str
    name: str
    summary: str          # 工作内容总结
    tasks: list[str]      # 关键任务/事项
    status: str = ""      # 工作状态（可选）
```

---

### 2. 新增分析器 `WorkSummaryAnalyzer`

**文件**：`src/infrastructure/analysis/analyzers/work_summary_analyzer.py`（新建，~120 行）

继承 `BaseAnalyzer`，参考 `TopicAnalyzer` 实现：

| 方法 | 说明 |
|------|------|
| `get_data_type()` | 返回 `"工作总结"` |
| `get_max_count()` | 从配置读取 `max_work_summaries` |
| `build_prompt(data)` | 全量消息 → 拼接自定义 prompt 模板，变量 `{{ messages_text }}` |
| `extract_with_regex()` | 正则降级解析 |
| `create_data_objects()` | LLM 返回 JSON → `WorkSummary` 对象列表 |
| `get_response_schema()` | 返回 `build_work_summaries_schema()` |

关键：`build_prompt` 直接复用 `TopicAnalyzer.extract_text_messages()` 的消息格式（`[HH:MM] [user_id]: content`），让 LLM 看到完整对话上下文。

---

### 3. 新增 Structured Output Schema

**文件**：`src/infrastructure/analysis/utils/structured_output_schema.py`

```python
def build_work_summaries_schema(max_items: int) -> JSONObject:
    return {
        "type": "array",
        "maxItems": max(1, int(max_items)),
        "items": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "name": {"type": "string"},
                "summary": {"type": "string"},
                "tasks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "status": {"type": "string"},
            },
            "required": ["user_id", "name", "summary", "tasks"],
            "additionalProperties": False,
        },
    }
```

---

### 4. 注册到 LLMAnalyzer

**文件**：`src/infrastructure/analysis/llm_analyzer.py`

- 构造函数中初始化 `self.work_summary_analyzer = WorkSummaryAnalyzer(context, config_manager)`
- `analyze_all_concurrent()` 中新增并发任务：

```python
if work_summary_enabled:
    tasks.append(
        self.work_summary_analyzer.analyze(messages, umo, session_id)
    )
    task_names.append("work_summary")
```

---

### 5. 配置项

**文件**：`src/infrastructure/config/config_manager.py`

新增 getter/setter（拷贝自 user_title 相关方法）：

| 配置键 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `analysis_features.work_summary_enabled` | bool | `false` | 开关 |
| `analysis_features.max_work_summaries` | int | `8` | 最大人数 |
| `prompts.work_summary_prompts.work_summary_prompt` | str | `""` | 自定义 prompt 模板 |

---

### 6. 应用层接入

**文件**：`src/application/services/analysis_application_service.py`

在两处分析入口（单次分析 + 增量最终报告）中：
1. 读取 `work_summary_enabled` 开关
2. 将 `work_summaries` 加入 `analysis_result` 字典

---

### 7. 报告渲染

**文件**：`src/infrastructure/reporting/generators.py`

- `_prepare_render_data()`：为 `render_data` 增加 `work_summaries_html`
- 新增 Jinja2 渲染调用 `work_summary_item.html`
- `generate_text_report()`：文本报告中追加「📋 工作总结」段落

**模板文件**：`src/infrastructure/reporting/templates/*/work_summary_item.html`（新建）

---

### 8. 配置声明

**文件**：`_conf_schema.json`

在 `analysis_features` 和 `prompts` 分组中新增对应配置项声明。

---

## 默认 Prompt 模板（建议）

```
你是一个工作助理，正在分析一个工作群的聊天记录。
请根据以下24小时内的群聊消息，总结每个主要参与者的工作内容。

注意：
1. 要从全量对话中推断每个人的工作，不能只看本人的发言。
   例如：如果A指派B做某事，这应当体现在B的工作总结中。
2. 忽略闲聊、表情包等非工作内容。
3. 如果某人的工作内容无法从对话中确定，请标注"未从对话中体现明确工作内容"。

群聊消息：
{{ messages_text }}

请为每个主要参与者输出工作总结，最多 {{ max_summaries }} 人。
```

---

## 工作量

| 模块 | 工作量 |
|------|--------|
| 实体 + Schema | 小 |
| WorkSummaryAnalyzer | 中（~120 行，模板拷贝） |
| LLMAnalyzer 注册 | 小 |
| ConfigManager | 小 |
| ApplicationService | 小 |
| Generators + 模板 | 小 |
| _conf_schema | 小 |

**总计约 250 行**，与现有功能完全开关隔离，无架构风险。
