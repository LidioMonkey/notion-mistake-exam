#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_exam.py — 把结构化错题 JSON 渲染成「错题复盘卷」HTML，并导出 A4 打印 PDF。

用法：
    python3 build_exam.py spec.json [--outdir 输出目录] [--name 文件名前缀]

spec.json 结构见 references/exam-paper.md。
题目区在前（不含答案/解析），答案区新起一页在后。
"""

import argparse
import datetime
import html
import json
import pathlib
import re
import subprocess
import sys

SKILL_ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_ROOT / "assets" / "exam-template.html"

VALID_TYPES = {"single", "multi", "judge", "blank", "subjective"}
CHOICE_TYPES = {"single", "multi"}


def fail(msg: str) -> None:
    print(f"[build_exam] 错误：{msg}", file=sys.stderr)
    sys.exit(1)


def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=False)


def load_spec(path: pathlib.Path) -> dict:
    if not path.exists():
        fail(f"找不到 spec 文件：{path}")
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        fail(f"spec.json 不是合法 JSON：{e}")
    if "sections" not in spec or not isinstance(spec["sections"], list):
        fail("spec 缺少必填字段 sections（数组）")
    if not spec["sections"]:
        fail("sections 不能为空")
    return spec


def validate(spec: dict) -> dict:
    sections = spec["sections"]
    total = 0
    type_counts = {t: 0 for t in VALID_TYPES}

    for si, sec in enumerate(sections):
        stype = sec.get("type")
        if stype not in VALID_TYPES:
            fail(f"第 {si+1} 个 section 的 type 非法：{stype!r}，必须是 {sorted(VALID_TYPES)}")
        if "questions" not in sec or not isinstance(sec["questions"], list) or not sec["questions"]:
            fail(f"第 {si+1} 个 section（{sec.get('name','?')}）questions 不能为空")
        type_counts[stype] += len(sec["questions"])

        for qi, q in enumerate(sec["questions"]):
            qid = f"section[{si}].questions[{qi}]"
            if not str(q.get("stem", "")).strip():
                fail(f"{qid} 缺少 stem")
            if "answer" not in q:
                fail(f"{qid} 缺少 answer")

            if stype in CHOICE_TYPES:
                opts = q.get("options")
                if not isinstance(opts, dict) or not opts:
                    fail(f"{qid}（{stype}）必须有非空 options 字典")
                ans = str(q.get("answer", "")).strip().upper()
                keys = {k.strip().upper() for k in opts.keys()}
                if not ans or any(c not in keys for c in ans):
                    fail(f"{qid} 答案 {ans!r} 不在选项键 {sorted(keys)} 中")
                if stype == "single" and len(ans) != 1:
                    fail(f"{qid} 单选题答案必须是单个字母，当前 {ans!r}")
                if len(set(ans)) != len(ans):
                    fail(f"{qid} 答案字母有重复：{ans!r}")
            elif stype == "judge":
                if not isinstance(q.get("answer"), bool):
                    fail(f"{qid}（judge）answer 必须是布尔 true/false")
            elif stype == "blank":
                if not re.search(r"_{3,}", str(q.get("stem", ""))):
                    fail(f"{qid}（blank）stem 中必须含 ____（连续 3+ 下划线）占位")
            elif stype == "subjective":
                if not str(q.get("answer", "")).strip():
                    fail(f"{qid}（subjective）answer（参考要点）不能为空")

            total += 1

    if total < 1:
        fail(f"总题数至少 1 道，当前 {total}（不设上限，所有到期题全部入卷）")

    return {"total": total, "type_counts": type_counts}


def normalize_math(s: str) -> str:
    """把 Notion 专有公式语法转成 MathJax 标准语法。
    - Notion 行内：$`公式`$  →  $公式$
    - Notion 块级：$$...$$ 保持不变（MathJax 认）
    - 其他纯文本公式（Unicode √∫ 等）保留原样
    """
    if not s:
        return s
    # Notion 行内公式：$`...`$  →  $...$
    s = re.sub(r"\$`([^`]+?)`\$", r"$\1$", s)
    return s


def render_inline(s: str) -> str:
    """渲染行内文本：转义 + 公式归一 + 行内代码（反引号）。"""
    s = esc(normalize_math(s))
    # 行内代码 `code` → <code>
    s = re.sub(r"`([^`]+)`", r'<code class="inline-code">\1</code>', s)
    return s


def render_text(s: str) -> str:
    """渲染通用文本（答案/解析）：支持 ``` 代码块、行内代码、换行。"""
    parts = re.split(r"```(?:\w+)?\n(.*?)```", s, flags=re.DOTALL)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            code = esc(part.rstrip("\n"))
            out.append(f'<pre class="code-block"><code>{code}</code></pre>')
        else:
            t = render_inline(part)
            t = t.replace("\n", "<br>")
            out.append(t)
    return "".join(out)


def stem_html(stem: str, stype: str) -> str:
    """渲染题干：支持 ``` 代码块、行内代码、换行、下划线占位。
    代码块用 <pre class="code-block"> 等宽字体保留换行缩进。
    """
    parts = re.split(r"```(?:\w+)?\n(.*?)```", stem, flags=re.DOTALL)
    out = []
    for i, part in enumerate(parts):
        if i % 2 == 1:
            # 代码块内容：转义但保留换行和空格
            code = esc(part.rstrip("\n"))
            out.append(f'<pre class="code-block"><code>{code}</code></pre>')
        else:
            # 普通文本
            s = render_inline(part)
            if stype == "blank":
                s = re.sub(r"_{3,}", '<span class="blank-line"></span>', s)
            s = s.replace("\n", "<br>")
            out.append(s)
    return "".join(out)


def build_question_block(idx: int, q: dict, stype: str) -> str:
    """渲染一道题的题目区 HTML（不含答案/解析）。"""
    classes = ["q"]
    if q.get("long"):
        classes.append("long")
    qcls = " ".join(classes)

    stem = stem_html(q["stem"], stype)
    extra = ""
    qid_html = f'<span class="qid">{esc(q["qid"])}</span>' if q.get("qid") else ""

    if stype in CHOICE_TYPES:
        opts = q["options"]
        # 判断是否横排：所有选项短且总数少
        opt_list = list(opts.items())
        flat = all(len(str(v)) <= 14 for _, v in opt_list) and len(opt_list) <= 4
        if flat:
            cells = "".join(
                f'<span class="opt opt-h"><span class="k">{esc(k)}</span>. {render_inline(v)}</span>'
                for k, v in opt_list
            )
            opts_html = f'<div class="opts opts-h">{cells}</div>'
        else:
            rows = "".join(
                f'<div class="opt"><span class="k">{esc(k)}</span>. {render_inline(v)}</div>'
                for k, v in opt_list
            )
            opts_html = f'<div class="opts">{rows}</div>'
        tag = ' <span class="qtag">（多选）</span>' if stype == "multi" else ""
        body = f'<div class="stem"><span class="qn">{idx}.</span>{qid_html}{tag} {stem}</div>{opts_html}'
    elif stype == "judge":
        body = (
            f'<div class="stem"><span class="qn">{idx}.</span>{qid_html} {stem}'
            f'<span class="judge-bracket">（&nbsp;&nbsp;）</span></div>'
        )
    elif stype == "blank":
        body = f'<div class="stem"><span class="qn">{idx}.</span>{qid_html} {stem}</div>'
    else:  # subjective
        body = (
            f'<div class="stem"><span class="qn">{idx}.</span>{qid_html} {stem}</div>'
            f'<div class="answer-space"><div class="answer-box"></div></div>'
        )

    return f'<div class="{qcls}">{body}</div>'


def build_section_block(sec: dict, start_idx: int) -> tuple:
    """渲染一个题型 section 的题目区，返回 (html, next_idx)。"""
    stype = sec["type"]
    name = esc(sec.get("name") or default_section_name(stype))
    blocks = []
    idx = start_idx
    for q in sec["questions"]:
        blocks.append(build_question_block(idx, q, stype))
        idx += 1
    return f'<h2 class="sec">{name}</h2>' + "\n".join(blocks), idx


def build_subjective_twocol(sec: dict, start_idx: int) -> tuple:
    """大题答题卡式：题干全宽横向排列，仅答题框两栏并排、高度直达页底。"""
    stype = sec["type"]
    name = esc(sec.get("name") or default_section_name(stype))
    questions = sec["questions"]
    idx = start_idx
    pages = []
    for ci in range(0, len(questions), 2):
        chunk = questions[ci:ci + 2]
        stems, boxes = [], []
        for q in chunk:
            qid_html = f'<span class="qid">{esc(q["qid"])}</span>' if q.get("qid") else ""
            stem = stem_html(q["stem"], stype)
            stems.append(
                f'<div class="stem"><span class="qn">{idx}.</span>{qid_html} {stem}</div>'
            )
            boxes.append('<div class="subj-col"><div class="answer-box"></div></div>')
            idx += 1
        if len(chunk) == 1:
            boxes.append('<div class="subj-col"></div>')
        pages.append(
            '<div class="subj-page">'
            f'<div class="subj-stems">{"".join(stems)}</div>'
            f'<div class="subj-boxes">{"".join(boxes)}</div>'
            '</div>'
        )
    html = f'<div class="subj-section"><h2 class="sec">{name}</h2>{"".join(pages)}</div>'
    return html, idx


def default_section_name(stype: str) -> str:
    return {
        "single": "一、单项选择题",
        "multi": "二、多项选择题",
        "judge": "三、判断题",
        "blank": "四、填空题",
        "subjective": "五、简答题",
    }.get(stype, "题")


def build_answer_row(idx: int, q: dict, stype: str) -> str:
    """渲染答案区一行：仅题号 + 答案，不显示题干和解析。"""
    ans = q.get("answer")
    if stype == "judge":
        ans_text = "√ 正确" if ans else "× 错误"
    else:
        ans_text = str(ans if ans is not None else "")
    ans_norm = render_text(ans_text)

    qid_html = f'<span class="qid">{esc(q["qid"])}</span>' if q.get("qid") else ""
    return (
        f'<div class="arow arow-compact">'
        f'<span class="aq">{idx}.</span>{qid_html} '
        f'<b class="ans">{ans_norm}</b>'
        f'</div>'
    )


def build_answer_section(sec: dict, start_idx: int) -> tuple:
    rows = []
    idx = start_idx
    for q in sec["questions"]:
        rows.append(build_answer_row(idx, q, sec["type"]))
        idx += 1
    return "\n".join(rows), idx


def _build_common(spec: dict, stats: dict) -> dict:
    """组装公共字段：标题/日期/范围/题型统计/题目 HTML/答案 HTML。"""
    title = spec.get("title") or "错题复盘卷"
    subtitle = spec.get("subtitle") or "Notion 错题库 · 闭卷复盘"
    date = spec.get("date") or datetime.date.today().isoformat()
    scope = spec.get("scope") or "—"
    duration = spec.get("duration_hint") or ""

    q_parts = []
    idx = 1
    for sec in spec["sections"]:
        block, idx = build_section_block(sec, idx)
        q_parts.append(block)
    questions_html = "\n".join(q_parts)

    a_parts = []
    idx = 1
    for sec in spec["sections"]:
        block, idx = build_answer_section(sec, idx)
        a_parts.append(block)
    answers_html = "\n".join(a_parts)

    type_parts = []
    labels = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空", "subjective": "简答"}
    for t in ("single", "multi", "judge", "blank", "subjective"):
        c = stats["type_counts"].get(t, 0)
        if c:
            type_parts.append(f"{labels[t]} {c}")
    type_summary = " · ".join(type_parts)

    return {
        "title": title, "subtitle": subtitle, "date": date,
        "scope": scope, "duration": duration,
        "questions": questions_html, "answers": answers_html,
        "type_summary": type_summary,
    }


def render_questions_html(spec: dict, stats: dict) -> str:
    """只渲染试题卷（不含答案）。"""
    if not TEMPLATE.exists():
        fail(f"模板不存在：{TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    c = _build_common(spec, stats)

    # 去掉答案页整段
    tpl = re.sub(
        r'<div class="answer-page">.*?</div>\s*</body>',
        "</body>",
        tpl, flags=re.DOTALL,
    )

    repl = {
        "{{TITLE}}": esc(c["title"]),
        "{{SUBTITLE}}": esc(c["subtitle"]),
        "{{META_DATE}}": esc(c["date"]),
        "{{META_SCOPE}}": esc(c["scope"]),
        "{{META_COUNT}}": str(stats["total"]),
        "{{META_TYPES}}": esc(c["type_summary"]),
        "{{META_DURATION}}": esc(c["duration"]),
        "{{QUESTIONS}}": c["questions"],
        "{{ANSWERS}}": "",
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def render_answers_html(spec: dict, stats: dict) -> str:
    """只渲染答案与解析卷。"""
    if not TEMPLATE.exists():
        fail(f"模板不存在：{TEMPLATE}")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    c = _build_common(spec, stats)

    # 去掉试题卷的 report-head 和 QUESTIONS 占位，换成答案卷专用头部
    tpl = re.sub(
        r'<div class="report-head">.*?</div>\s*{{QUESTIONS}}',
        '<div class="report-head">'
        f'<h1>{esc(c["title"])} · 答案与解析</h1>'
        f'<div class="sub">{esc(c["subtitle"])}</div>'
        '<div class="meta">'
        f'<span>日期：<b>{esc(c["date"])}</b></span>'
        f'<span>范围：<b>{esc(c["scope"])}</b></span>'
        f'<span>题数：<b>{stats["total"]}</b></span>'
        f'<span>构成：<b>{esc(c["type_summary"])}</b></span>'
        '</div></div>',
        tpl, flags=re.DOTALL,
    )
    # 答案页不再需要 page-break-before（它自己就是独立文档）
    tpl = tpl.replace('<div class="answer-page">', '<div>')

    repl = {
        "{{TITLE}}": esc(c["title"]),
        "{{SUBTITLE}}": esc(c["subtitle"]),
        "{{META_DATE}}": esc(c["date"]),
        "{{META_SCOPE}}": esc(c["scope"]),
        "{{META_COUNT}}": str(stats["total"]),
        "{{META_TYPES}}": esc(c["type_summary"]),
        "{{META_DURATION}}": esc(c["duration"]),
        "{{QUESTIONS}}": "",
        "{{ANSWERS}}": c["answers"],
    }
    for k, v in repl.items():
        tpl = tpl.replace(k, v)
    return tpl


def export_pdf(html_path: pathlib.Path, pdf_path: pathlib.Path) -> str:
    uri = html_path.resolve().as_uri()
    chromium = None
    for cand in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if subprocess.run(["which", cand], capture_output=True).returncode == 0:
            chromium = cand
            break
    if chromium:
        base = [
            chromium, "--headless=new", "--no-sandbox", "--disable-gpu",
            "--disable-dev-shm-usage", "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=45000",
            f"--print-to-pdf={pdf_path}", uri,
        ]
        try:
            subprocess.run(base, capture_output=True, timeout=120)
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return "chromium"
        except Exception:
            pass
        old = [a for a in base if a != "--headless=new"]
        old.insert(1, "--headless")
        try:
            subprocess.run(old, capture_output=True, timeout=120)
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return "chromium"
        except Exception:
            pass
    try:
        import weasyprint  # noqa
        weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        if pdf_path.exists() and pdf_path.stat().st_size > 0:
            return "weasyprint"
    except Exception as e:
        fail(f"PDF 导出失败（chromium 与 weasyprint 均失败）：{e}")
    fail("PDF 导出失败：未生成有效文件")


def check_pages(pdf_path: pathlib.Path) -> int:
    try:
        from pypdf import PdfReader
        return len(PdfReader(str(pdf_path)).pages)
    except Exception as e:
        print(f"[build_exam] 警告：无法校验 PDF 页数：{e}", file=sys.stderr)
        return -1


SUBJECT_SLUG = {"高等数学": "math", "高数": "math", "数学": "math",
                "408": "cs408", "计算机408": "cs408", "408计算机": "cs408"}


def split_spec_by_subject(spec: dict) -> list:
    """按每题的 subject 字段把 spec 拆成多个子 spec。
    每桶保留原 sections 的题型顺序，但只保留属于该 subject 的题。"""
    buckets = {}  # subject_label -> list of (stype, name, questions)
    for sec in spec["sections"]:
        stype = sec["type"]
        name = sec.get("name") or default_section_name(stype)
        for q in sec["questions"]:
            subj = str(q.get("subject", "408")).strip()
            buckets.setdefault(subj, []).append((stype, name, q))

    # 题型固定顺序：单选→多选→判断→填空→简答（客观题在前，大题答题卡在后）
    type_order = ["single", "multi", "judge", "blank", "subjective"]
    children = []
    for subj, items in buckets.items():
        # 按题型重新聚合
        by_type = {}
        for stype, name, q in items:
            if stype not in by_type:
                by_type[stype] = {"type": stype, "name": name, "questions": []}
            by_type[stype]["questions"].append(q)
        child = dict(spec)
        child["sections"] = [by_type[t] for t in type_order if t in by_type]
        # 子卷标题加上科目
        child["title"] = f"{spec.get('title','错题复盘卷')} · {subj}"
        children.append((subj, child))
    return children


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="组卷 JSON 文件路径")
    ap.add_argument("--outdir", default=None, help="输出目录，默认与 spec 同目录")
    ap.add_argument("--name", default="exam", help="文件名前缀，默认 exam")
    args = ap.parse_args()

    spec_path = pathlib.Path(args.spec).resolve()
    spec = load_spec(spec_path)
    stats = validate(spec)

    outdir = pathlib.Path(args.outdir).resolve() if args.outdir else spec_path.parent
    outdir.mkdir(parents=True, exist_ok=True)

    parts = split_spec_by_subject(spec)
    # 科目顺序：高数在前，408在后
    subj_order = ["高等数学", "408"]
    parts.sort(key=lambda p: subj_order.index(p[0]) if p[0] in subj_order else 99)

    title = spec.get("title") or "错题复盘卷"
    subtitle = spec.get("subtitle") or "Notion 错题库 · 闭卷复盘"
    date = spec.get("date") or datetime.date.today().isoformat()
    scope = spec.get("scope") or "—"
    duration = spec.get("duration_hint") or ""

    type_labels = {"single": "单选", "multi": "多选", "judge": "判断", "blank": "填空", "subjective": "简答"}
    type_parts = []
    for t in ("single", "multi", "judge", "blank", "subjective"):
        c = stats["type_counts"].get(t, 0)
        if c:
            type_parts.append(f"{type_labels[t]} {c}")
    type_summary = " · ".join(type_parts)

    # ---- 组装题目 HTML（高数在前，408 另起一页）----
    q_blocks = []
    idx = 1
    for i, (subj, child) in enumerate(parts):
        if i > 0:
            q_blocks.append('<div style="page-break-before: always"></div>')
        q_blocks.append(f'<h2 style="font-size:14pt;margin:0 0 8px 0;border-left:4px solid #b35900;padding-left:8px;">{esc(subj)}</h2>')
        for sec in child["sections"]:
            if sec["type"] == "subjective":
                block, idx = build_subjective_twocol(sec, idx)
            else:
                block, idx = build_section_block(sec, idx)
            q_blocks.append(block)
    questions_html = "\n".join(q_blocks)

    # ---- 组装答案 HTML ----
    a_blocks = []
    idx = 1
    for i, (subj, child) in enumerate(parts):
        if i > 0:
            a_blocks.append('<div style="page-break-before: always"></div>')
        a_blocks.append(f'<h2 style="font-size:13pt;margin:10px 0 6px 0;color:#b35900;">{esc(subj)} · 答案</h2>')
        for sec in child["sections"]:
            block, idx = build_answer_section(sec, idx)
            a_blocks.append(block)
    answers_html = "\n".join(a_blocks)

    tpl = TEMPLATE.read_text(encoding="utf-8")

    # 题目 PDF：去掉答案页
    tpl_q = re.sub(r'<div class="answer-page">.*?</div>\s*</body>', "</body>", tpl, flags=re.DOTALL)
    repl_q = {
        "{{TITLE}}": esc(title), "{{SUBTITLE}}": esc(subtitle),
        "{{META_DATE}}": esc(date), "{{META_SCOPE}}": esc(scope),
        "{{META_COUNT}}": str(stats["total"]), "{{META_TYPES}}": esc(type_summary),
        "{{META_DURATION}}": esc(duration), "{{QUESTIONS}}": questions_html,
        "{{ANSWERS}}": "",
    }
    for k, v in repl_q.items():
        tpl_q = tpl_q.replace(k, v)

    q_html_path = outdir / f".{args.name}-questions.tmp.html"
    q_pdf_path = outdir / f"{args.name}-questions.pdf"
    q_html_path.write_text(tpl_q, encoding="utf-8")
    engine_used = export_pdf(q_html_path, q_pdf_path)
    q_pages = check_pages(q_pdf_path)
    q_html_path.unlink(missing_ok=True)

    # 答案 PDF：替换 report-head 和 QUESTIONS
    tpl_a = re.sub(
        r'<div class="report-head">.*?</div>\s*{{QUESTIONS}}',
        '<div class="report-head">'
        f'<h1>{esc(title)} · 答案</h1>'
        f'<div class="sub">{esc(subtitle)}</div>'
        '<div class="meta">'
        f'<span>日期：<b>{esc(date)}</b></span>'
        f'<span>范围：<b>{esc(scope)}</b></span>'
        f'<span>题数：<b>{stats["total"]}</b></span>'
        f'<span>构成：<b>{esc(type_summary)}</b></span>'
        '</div></div>',
        tpl, flags=re.DOTALL,
    )
    tpl_a = tpl_a.replace('<div class="answer-page">', '<div>')
    repl_a = {
        "{{TITLE}}": esc(title), "{{SUBTITLE}}": esc(subtitle),
        "{{META_DATE}}": esc(date), "{{META_SCOPE}}": esc(scope),
        "{{META_COUNT}}": str(stats["total"]), "{{META_TYPES}}": esc(type_summary),
        "{{META_DURATION}}": esc(duration), "{{QUESTIONS}}": "",
        "{{ANSWERS}}": answers_html,
    }
    for k, v in repl_a.items():
        tpl_a = tpl_a.replace(k, v)

    a_html_path = outdir / f".{args.name}-answers.tmp.html"
    a_pdf_path = outdir / f"{args.name}-answers.pdf"
    a_html_path.write_text(tpl_a, encoding="utf-8")
    export_pdf(a_html_path, a_pdf_path)
    a_pages = check_pages(a_pdf_path)
    a_html_path.unlink(missing_ok=True)

    print(json.dumps({
        "engine": engine_used,
        "questions_pdf": str(q_pdf_path),
        "answers_pdf": str(a_pdf_path),
        "questions_pages": q_pages,
        "answers_pages": a_pages,
        "total": stats["total"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
