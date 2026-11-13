"""Small, rebuildable references from a health event to its supporting files."""
import os
from pathlib import Path
from urllib.parse import quote

from health_store import HEALTH_DIR
from manage_records import stored_path
from file_lifecycle import documents_for_episode


def literal(value):
    value = str(value).replace("\n", " ").replace("\r", " ")
    for char in ("\\", "`", "[", "]", "*", "_", "<", ">", "|"):
        value = value.replace(char, "\\" + char)
    return value


def render_document_links(profile, episode, output_dir=None, heading_level=2):
    documents = documents_for_episode(profile, episode)
    if not documents:
        return ""
    lines = ["", "#" * heading_level + " 关联检查与资料", "",
             "资料关联保留事实依据；归档只改变文件列表显示，不删除健康事实。", ""]
    for document in documents:
        title = document.get("title") or document.get("original_name") or "未命名资料"
        target = stored_path(document["raw_file"])
        relative = os.path.relpath(target, Path(output_dir or HEALTH_DIR))
        archive = "；已归档" if document.get("archived") else ""
        names = list(dict.fromkeys(source.get("reporter_name") or "姓名未记录"
                                  for source in document.get("upload_sources", [])))
        if not names:
            names = [document.get("reporter_name") or "姓名未记录"]
        lines.extend(["- [{}]({})（资料日期：{}{}）".format(literal(title), quote(relative, safe="/."),
                      literal(document.get("document_date") or "未知"), archive),
                      "  - 提供者：" + "、".join(literal(name) for name in names),
                      "  - 关联依据：" + literal(document.get("link_basis") or "未记录")])
        differences = [source for source in document.get("upload_sources", []) if source.get("duplicate_conflicts")]
        if differences:
            lines.append("  - 重复上传存在 {} 份转录或分类差异，原已核对字段保持不变，需核对各来源。".format(len(differences)))
    return "\n".join(lines) + "\n"
