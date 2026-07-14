#!/usr/bin/env python3
"""解压 3GPP 规范 zip → 转文本 → 章节感知切片 → 打元数据 → 输出 JSONL。

产出的每条切片遵循方案 §14.4「统一切片对象」契约字段，可直接送入统一入库 API：
  chunk_id, text, source_type, doc_id, uri, section_path, section_title,
  release, working_group, topic, standard_ref, decision, ambiguous,
  version, security_level, owner, review_status, confidence, last_updated

切片策略（章节感知，非定长）：
  1. 识别形如 "5.3.5<Tab/空格>标题" 的条款标题，构建层级；
  2. 每个叶子条款正文成一个切片，切片正文前缀「父级章节路径 + 标题面包屑」提升召回可读性；
  3. 正文过长的条款按 --max-chars 再切分（同一 section_path 内加 #partN 后缀），避免超长；
  4. 表格/图注等噪声做基本清洗（可选 --keep-tables 保留）。

用法：
  python process_3gpp_specs.py --in data/specs_zip --out data/chunks.jsonl \
      --manifest data/specs_zip/manifest.json --release 18 --security-level internal
"""
import argparse
import glob
import json
import os
import re
import sys
import tempfile
import zipfile
from datetime import date

from convert_doc import to_text
from tgpp_common import (
    decode_version, release_of, version_str, spec_num_compact, guess_doc_type,
)

# 条款标题：行首为点分号（1 / 1.2 / 5.3.5），后接分隔与标题文字。
# 标题文字限定不过长、不以句末标点结束，规避把表格数据/正文误判为标题。
HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,5})[\.\)]?[\t ]+(\S.{0,150}?)\s*$")
# 明显不是标题的行（纯数字表格、含大量分隔符、以句号/逗号结尾的整句）
_SENT_END = re.compile(r"[。．\.,，;；:：]$")
# 附录标题 "Annex A (normative): ..." 之类，单独识别
ANNEX_RE = re.compile(r"^\s*(Annex\s+[A-Z])\b[\t :\-]*(.*)$", re.IGNORECASE)


def looks_like_heading(num: str, title: str) -> bool:
    """进一步过滤：避免把 '3.5 GHz' / 表格里 '1.2 3.4 5.6' 之类误判为标题。"""
    if not title:
        return False
    if _SENT_END.search(title):
        return False
    # 标题里不应有过多数字/单位噪声（如整行是数值表）
    if re.fullmatch(r"[\d\.\s%\-+/]+", title):
        return False
    # 章节号层级过深（>5）通常是列表编号而非条款
    if num.count(".") > 5:
        return False
    return True


def parse_sections(text):
    """把纯文本解析为 [(section_path, title, [body_lines]), ...]（按出现顺序）。

    未归入任何条款的前导文字归到 section_path='0'（前言/范围之前）。
    """
    lines = text.splitlines()
    sections = []
    cur_path, cur_title, cur_body = "0", "(preamble)", []

    def flush():
        if cur_body or cur_path != "0":
            sections.append((cur_path, cur_title, cur_body))

    for ln in lines:
        stripped = ln.strip()
        m_annex = ANNEX_RE.match(stripped)
        m = HEADING_RE.match(ln)
        if m_annex and not m:
            flush()
            cur_path = m_annex.group(1).replace(" ", "")  # "AnnexA"
            cur_title = (m_annex.group(2) or "").strip() or m_annex.group(1)
            cur_body = []
            continue
        if m and looks_like_heading(m.group(1), m.group(2)):
            flush()
            cur_path, cur_title, cur_body = m.group(1), m.group(2).strip(), []
        else:
            if stripped:
                cur_body.append(stripped)
    flush()
    return sections


def breadcrumb(path, title_map):
    """由 section_path 生成祖先标题面包屑，如 '5 RRC > 5.3 Procedures > 5.3.5 Reestablishment'。"""
    if not path or path == "0" or path.startswith("Annex"):
        return title_map.get(path, "")
    parts = path.split(".")
    crumbs = []
    for i in range(1, len(parts) + 1):
        p = ".".join(parts[:i])
        t = title_map.get(p)
        if t:
            crumbs.append(f"{p} {t}")
    return " > ".join(crumbs) if crumbs else f"{path} {title_map.get(path, '')}".strip()


def split_long(body_text, max_chars):
    """把过长正文按段落边界软切分为 <= max_chars 的块。"""
    if len(body_text) <= max_chars:
        return [body_text]
    paras = re.split(r"\n(?=\S)", body_text)
    chunks, buf = [], ""
    for p in paras:
        if buf and len(buf) + len(p) + 1 > max_chars:
            chunks.append(buf.strip())
            buf = p
        else:
            buf = f"{buf}\n{p}" if buf else p
    if buf.strip():
        chunks.append(buf.strip())
    # 仍有超长单段则硬切
    out = []
    for c in chunks:
        while len(c) > max_chars:
            out.append(c[:max_chars])
            c = c[max_chars:]
        if c:
            out.append(c)
    return out


def unzip_to_docs(zip_path, workdir):
    """解压 zip，返回其中 .doc/.docx 文件路径列表。"""
    docs = []
    try:
        with zipfile.ZipFile(zip_path) as z:
            for name in z.namelist():
                if name.lower().endswith((".doc", ".docx")):
                    z.extract(name, workdir)
                    docs.append(os.path.join(workdir, name))
    except zipfile.BadZipFile:
        print(f"[WARN] 损坏的 zip：{zip_path}", file=sys.stderr)
    return docs


def process_zip(zip_path, meta, args):
    """处理单个规范 zip，产出 chunk dict 列表。"""
    fname = os.path.basename(zip_path)
    m = re.match(r"(\d+)-([0-9a-z]{3})\.zip", fname, re.IGNORECASE)
    spec_num = m.group(1) if m else spec_num_compact(meta.get("spec", ""))
    vraw = m.group(2).lower() if m else meta.get("version_raw", "")
    # 规范号带点：38331 -> 38.331
    spec_dotted = meta.get("spec") or (f"{spec_num[:2]}.{spec_num[2:]}" if len(spec_num) >= 4 else spec_num)
    doc_type = meta.get("doc_type") or guess_doc_type(spec_dotted)
    doc_id = f"{doc_type} {spec_dotted}"
    release = meta.get("release") or release_of(vraw)
    version = meta.get("version") or version_str(vraw)
    today = args.today

    with tempfile.TemporaryDirectory() as wd:
        docs = unzip_to_docs(zip_path, wd)
        if not docs:
            print(f"[WARN] {fname}: zip 内无 .doc/.docx", file=sys.stderr)
            return []
        # 规范正文一般是 zip 内最大的那个文档
        docs.sort(key=lambda p: os.path.getsize(p), reverse=True)
        main_doc = docs[0]
        try:
            text = to_text(main_doc)
        except Exception as ex:
            print(f"[WARN] {fname}: 转文本失败 {ex}", file=sys.stderr)
            return []

    sections = parse_sections(text)
    title_map = {p: t for p, t, _ in sections}

    chunks = []
    for path, title, body in sections:
        body_text = "\n".join(body).strip()
        if len(body_text) < args.min_chars:
            continue  # 跳过空壳/极短条款（多为父级标题占位）
        crumb = breadcrumb(path, title_map)
        parts = split_long(body_text, args.max_chars)
        for i, part in enumerate(parts):
            suffix = f"#part{i+1}" if len(parts) > 1 else ""
            section_ref = f"{doc_id} §{path}" if path not in ("0",) else doc_id
            prefix = f"〔{section_ref}〕{crumb}\n" if crumb else f"〔{section_ref}〕\n"
            chunk = {
                "chunk_id": f"{spec_num}-{vraw}-s{path}{suffix}".replace("..", "."),
                "text": prefix + part,
                "source_type": "spec",
                "doc_id": doc_id,
                "uri": meta.get("url") or f"repo://specs/{fname}",
                "section_path": path,
                "section_title": title,
                "release": f"Rel-{release}" if release is not None else None,
                "working_group": meta.get("working_group"),
                "topic": None,
                "standard_ref": [section_ref] if path != "0" else [doc_id],
                "decision": None,
                "ambiguous": False,
                "version": version,
                "security_level": args.security_level,
                "owner": meta.get("owner"),
                "review_status": "raw",   # 原文切片，非 Wiki 精加工；入库后不参与 Wiki 优先加权
                "confidence": None,
                "last_updated": today,
            }
            chunks.append(chunk)
    print(f"  {doc_id} {version}: {len(chunks)} chunks（{len(sections)} sections）")
    return chunks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="indir", required=True, help="规范 zip 所在目录")
    ap.add_argument("--out", required=True, help="输出 JSONL 路径")
    ap.add_argument("--manifest", default=None,
                    help="download 阶段产出的 manifest.json（提供 spec/version/type 元数据）")
    ap.add_argument("--release", type=int, default=None, help="兜底 Release 号（manifest 缺失时用）")
    ap.add_argument("--security-level", default="internal",
                    help="密级标签，默认 internal（3GPP 为公开标准，可设 public）")
    ap.add_argument("--max-chars", type=int, default=1200, help="单切片最大字符数")
    ap.add_argument("--min-chars", type=int, default=40, help="低于此长度的条款跳过")
    ap.add_argument("--today", default=None, help="last_updated 值，默认取系统当天")
    a = ap.parse_args()
    a.today = a.today or date.today().isoformat()

    manifest_map = {}
    if a.manifest and os.path.exists(a.manifest):
        for rec in json.load(open(a.manifest, encoding="utf-8")):
            manifest_map[rec["file"]] = rec

    zips = sorted(glob.glob(os.path.join(a.indir, "*.zip")))
    if not zips:
        print(f"错误：{a.indir} 下没有 zip", file=sys.stderr)
        sys.exit(2)

    total = 0
    with open(a.out, "w", encoding="utf-8") as fout:
        for zp in zips:
            meta = manifest_map.get(os.path.basename(zp), {})
            if a.release and "release" not in meta:
                meta["release"] = a.release
            for chunk in process_zip(zp, meta, a):
                fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                total += 1

    print(f"\n完成：{len(zips)} 个规范 -> {total} 条切片 -> {a.out}")
    print("字段遵循方案 §14.4 契约；可直接送统一入库 API。")


if __name__ == "__main__":
    main()
