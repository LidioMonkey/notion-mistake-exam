# 错题库列名映射表

识别 Notion 错题库数据库列时按此表匹配：**先精确匹配列名（忽略大小写、空格、下划线）→ 再子串包含 → 仍识别不了就把候选列展示给用户确认**。

## 核心列（识别不了要问用户）

| 用途 | 常见列名（英文） | 常见列名（中文） |
|---|---|---|
| 题干 | question, stem, title, problem, prompt, text | 题干、题目、问题、题面 |
| 答案 | answer, correct, answer_key, right_answer | 答案、正确答案、答案键 |

## 辅助列（有则用）

| 用途 | 常见列名（英文） | 常见列名（中文） |
|---|---|---|
| 选项 | options, choices, items, options_json | 选项、备选项 |
| 解析 | analysis, explanation, solution, reasoning, note | 解析、解释、解答、思路 |
| 题型 | type, qtype, question_type, category | 题型、类型 |
| 科目 | subject, course, paper | 科目、学科、课程 |
| 章节/知识点 | chapter, section, topic, knowledge, point | 章节、知识点、考点 |
| 标签 | tags, label, group, chapter | 标签、分类、分组 |
| 来源 | source, from, origin, year, exam | 来源、出处、年份、真题 |
| 错误次数 | wrong_count, error_count, mistakes, times | 错误次数、做错次数、次数 |
| 掌握状态 | status, mastery, level, familiarity, review_status | 掌握度、状态、掌握情况、复盘状态 |
| 最近做错日期 | last_wrong, wrong_at, created_at, date | 最近做错、做错日期、日期 |
| 最近复盘日期 | last_review, reviewed_at, review_date | 最近复盘、复盘日期 |

## 题型取值识别

库里若有"题型"列，按以下映射归一到 spec 的 `type`：

| 归一值 | 库中常见取值 |
|---|---|
| `single` | single, single_choice, 单选, 单项选择, 单选题, A/B/C/D |
| `multi` | multi, multiple, multi_choice, 多选, 多项选择, 多选题 |
| `judge` | judge, true_false, tf, 判断, 判断题, 对错 |
| `blank` | blank, fill, fill_blank, 填空, 填空题 |
| `subjective` | subjective, essay, short_answer, 简答, 主观, 论述, 问答题 |

库里没有题型列时按结构推断：
- 有 options 列且答案为字母组合 → single / multi（看答案是单个字母还是多个字母）。
- 答案为 `√/×/对/错/正确/错误/true/false` → judge。
- 题干含 `____`（连续 3 个以上下划线）→ blank。
- 其余 → subjective。

## 选项列格式

Notion 里"选项"常见存法：
- **JSON 字符串**：`{"A": "...", "B": "...", "C": "...", "D": "..."}` —— 直接 `json.loads`。
- **多列分开**：`选项A / 选项B / 选项C / 选项D` 四列 —— 拼起来。
- **换行文本**：每行一个选项，行首可能带 `A.` `A、` `A、` 或 `A)` —— 正则剥掉前缀。
- **Notion multiselect / relation**：少见，遇到再问用户。

组装 spec 时统一成 `{"A": "...", "B": "...", ...}` 字典。

## 答案列格式归一

- 单选：`B` / `b` / `选B` / `【B】` → 归一为大写字母 `B`。
- 多选：`ABD` / `A,B,D` / `A、B、D` / `[A,B,D]` → 归一为字母串 `ABD`（按 A→Z 排序）。
- 判断：`√`/`对`/`正确`/`true`/`T` → `true`；`×`/`错`/`错误`/`false`/`F` → `false`。
- 填空/简答：原样保留文本；前后空白 strip。

## 掌握状态取值建议

若掌握状态是文本：
- 复盘后用户反馈"还是不会" → 保持/降一档：未掌握→学习中→已掌握（已在最低档不动）。
- 用户反馈"会了" → 升一档；但错题本里的题建议"连续两次复盘都会"才升已掌握，第一次会了只升到"学习中"。

若用数字（0–5）：会了 +1，还不会 -1（下限 0）。

## 写回示例（用户确认后）

```sql
-- 把本次组卷涉及的题标记为已复盘
UPDATE <数据源>
SET last_reviewed = '2026-09-19',
    review_status = '学习中'
WHERE question IN ('题1 题干...', '题2 题干...')
```

或按 Notion 行 ID 逐条更新（notion-update-data-source 支持按 page_id 更新单条）。

## 边界情况

- **题干为空**：跳过该题，不要硬组卷。
- **答案为空**：不能进卷（复盘卷必须有答案）。在交付说明里告诉用户"题库里有 N 道题缺答案，已跳过"。
- **解析为空**：可以进卷，脚本会在答案页显示"（题库未提供解析）"；由你现场补一段解析并在交付说明里标注「AI 补解析」，不要写回题库。
- **选项与答案不匹配**（如答案是 `E` 但只有 A–D）：跳过该题或问用户。
- **同题干多条记录**：按题干去重，错误次数累加或取最大值，来源合并显示。
- **超长题干**（>500 字）：题干保留全文，但在 spec 里标注 `long: true`，模板会用稍小字号渲染。
