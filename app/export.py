"""导出:TXT / Markdown / DOCX(python-docx 可选)。"""
import re


def _safe_name(title):
    return re.sub(r'[\\/:*?"<>|]+', "_", title or "novel").strip() or "novel"


def export_text(project, chapters, fmt="md"):
    title = project.get("title") or "未命名作品"
    author_line = project.get("genre", "")
    if fmt == "txt":
        parts = [title, f"类型:{author_line}" if author_line else "", ""]
        for ch in chapters:
            parts.append(f"第{ch['idx']}章 {ch.get('title', '')}")
            parts.append(ch.get("content") or "(未写)")
            parts.append("")
        return "\n".join(parts), f"{_safe_name(title)}.txt", "text/plain; charset=utf-8"

    parts = [f"# {title}", ""]
    if author_line:
        parts += [f"> 类型:{author_line}", ""]
    if project.get("premise"):
        parts += [f"**梗概:**{project['premise']}", ""]
    for ch in chapters:
        parts.append(f"## 第{ch['idx']}章 {ch.get('title', '')}")
        parts.append("")
        parts.append(ch.get("content") or "(未写)")
        parts.append("")
    return "\n".join(parts), f"{_safe_name(title)}.md", "text/markdown; charset=utf-8"


def export_docx(project, chapters):
    try:
        from docx import Document
    except ImportError:
        raise RuntimeError("导出 DOCX 需要安装 python-docx:pip install python-docx")
    doc = Document()
    title = project.get("title") or "未命名作品"
    doc.add_heading(title, level=0)
    if project.get("premise"):
        doc.add_paragraph(project["premise"])
    for ch in chapters:
        doc.add_heading(f"第{ch['idx']}章 {ch.get('title', '')}", level=1)
        for para in (ch.get("content") or "(未写)").splitlines():
            if para.strip():
                doc.add_paragraph(para.strip())
    import io

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), f"{_safe_name(title)}.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
