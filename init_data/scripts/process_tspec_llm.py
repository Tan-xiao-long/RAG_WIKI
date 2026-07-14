#!/usr/bin/env python3
"""对现成 TSpec-LLM（Markdown 格式的 3GPP 规范）做章节感知切片 -> JSONL。

与 process_3gpp_specs.py 产出同一套 §14.4 契约字段，区别仅在于输入是
已转好的 .md（标题用 # 标记 + 条款号），无需解压/转文本。

用法：
  python process_tspec_llm.py --in data/tspec_llm --out data/chunks_tspec.jsonl \
      --security-level public
"""
import argparse
import glob
import json
import os
import re
import sys
from datetime import date

from process_3gpp_specs import split_long, breadcrumb
from tgpp_common import guess_doc_type

# Markdown 标题：# 前缀 + 可选条款号 + 标题文字
MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(?:(\d+(?:\.\d+){0,5})[\.\)]?\s+)?(.*\S)?\s*$")
# 从文件名/路径提取规范号，如 38.331 / 38331
SPEC_IN_NAME = re.compile(r"(\d{2})[._]?(\d{3})")
# 版本串（若文件名带 -i50 之类）
VER_IN_NAME = re.compile(r"[-_]([0-9a-z]{3})(?:\.md)?$", re.IGNORECASE)


def guess_spec_version(path):
    """从文件路径猜规范号与版本。返回 (spec_dotted, version_raw or '')。"""
    base = os.path.basename(path)
    m = SPEC_IN_NAME.search(base) or SPEC_IN_NAME.search(path)
    spec = f"{m.group(1)}.{m.group(2)}" if m else None
    vm = VER_IN_NAME.search(base)
    return spec, (vm.group(1).lower() if vm else "")


def parse_md_sections(text):
    """解析 markdown 为 [(section_path, title, [body_lines])]。

    有条款号的标题用条款号做 section_path；无条款号的标题用其层级序号兜底。
    """
    sections = []
    cur_path, cur_title, cur_body = "0", "(preamble)", []
    auto = [0] * 7  # 无条款号标题的层级计数兜底

    def flush():
        if cur_body or cur_path != "0":
            sections.append((cur_path, cur_title, cur_body))

    for ln in text.splitlines():
        m = MD_HEADING_RE.match(ln)
        if m and (m.group(2) or m.group(3)):
            flush()
            level = len(m.group(1))
            num = m.group(2)
            title = (m.group(3) or "").strip()
            if num:
                cur_path = num
            else:
                auto[level - 1] += 1
                for k in range(level, 7):
                    auto[k] = 0
                cur_path = ".".join(str(x) for x in auto[:level] if x)
                cur_path = cur_path or f"h{level}"
            cur_title = title or (num or "")
            cur_body = []
        else:
            s = ln.strip()
            if s and not s.startswith("|---"):
                cur_body.append(s)
    flush()
    return sections


def process_md(path, args):
    spec, vraw = guess_spec_version(path)
    if not spec:
        return []
    doc_type = guess_doc_type(spec)
    doc_id = f"{doc_type} {spec}"
    spec_num = spec.replace(".", "")
    from tgpp_common import release_of, version_str
    release = release_of(vraw) if vraw else None
    version = version_str(vraw) if vraw else "unknown"

    text = open(path, encoding="utf-8", errors="replace").read()
    sections = parse_md_sections(text)
    title_map = {p: t for p, t, _ in sections}

    chunks = []
    for spath, title, body in sections:
        body_text = "\n".join(body).strip()
        if len(body_text) < args.min_chars:
            continue
        crumb = breadcrumb(spath, title_map)
        for i, part in enumerate(split_long(body_text, args.max_chars)):
            n = len(split_long(body_text, args.max_chars))
            suffix = f"#part{i+1}" if n > 1 else ""
            section_ref = f"{doc_id} §{spath}" if spath != "0" else doc_id
            prefix = f"〔{section_ref}〕{crumb}\n" if crumb else f"〔{section_ref}〕\n"
            chunks.append({
                "chunk_id": f"{spec_num}-{vraw or 'na'}-s{spath}{suffix}",
                "text": prefix + part,
                "source_type": "spec",
                "doc_id": doc_id,
                "uri": f"tspec-llm://{os.path.relpath(path, args.indir)}",
                "section_path": spath,
                "section_title": title,
                "release": f"Rel-{release}" if release is not None else None,
                "working_group": None,
                "topic": None,
                "standard_ref": [section_ref] if spath != "0" else [doc_id],
                "decision": None,
                "ambiguous": False,
                "version": version,
                "security_level": args.security_level,
                "owner": None,
                "review_status": "raw",
                "confidence": None,
                "last_updated": args.today,
            })
    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="indir", required=True, help="TSpec-LLM 数据集根目录")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--security-level", default="public",
                    help="3GPP 为公开标准，默认 public")
    ap.add_argument("--max-chars", type=int, default=1200)
    ap.add_argument("--min-chars", type=int, default=40)
    ap.add_argument("--today", default=None)
    a = ap.parse_args()
    a.today = a.today or date.today().isoformat()

    mds = sorted(glob.glob(os.path.join(a.indir, "**", "*.md"), recursive=True))
    if not mds:
        print(f"错误：{a.indir} 下未发现 .md", file=sys.stderr)
        sys.exit(2)
    print(f"发现 {len(mds)} 个 markdown 文件")

    total = 0
    with open(a.out, "w", encoding="utf-8") as fout:
        for p in mds:
            for c in process_md(p, a):
                fout.write(json.dumps(c, ensure_ascii=False) + "\n")
                total += 1
    print(f"完成：{total} 条切片 -> {a.out}")


if __name__ == "__main__":
    main()
