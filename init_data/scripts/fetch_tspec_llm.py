#!/usr/bin/env python3
"""快速启动路径：直接拉取现成的 TSpec-LLM 数据集（已转 Markdown 的 3GPP 规范）。

TSpec-LLM（HuggingFace: rasoul-nikbakht/TSpec-LLM）已把 Rel-8 ~ Rel-19 的
3GPP 规范转成 Markdown/docx，共约 15.6GB。用它可跳过「FTP 下载 + .doc 转文本」
两步，直接进入切片阶段，是 PoC / P0 阶段最省力的起点。

⚠ 许可证：CC-BY-NC-4.0（署名 + 非商业）。用于公司内部研发评估通常可行，
   但接入对外生产系统前须由法务确认边界；且该集仅含规范正文，不含
   Chairs Notes / TDoc（那两类仍走 3gpp-topic-timeline skill 的现有管线）。

依赖：pip install datasets huggingface_hub

用法：
  # 全量下载（需数十 GB 磁盘 + 稳定外网/镜像）
  python fetch_tspec_llm.py --out data/tspec_llm

  # 仅下载某些系列（如 38 系列 5G NR）以节省空间——按文件名前缀过滤
  python fetch_tspec_llm.py --out data/tspec_llm --series 38,36

  # 内网环境：先在有外网的机器上 hf download，再拷贝到内网
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="数据集落地目录")
    ap.add_argument("--repo", default="rasoul-nikbakht/TSpec-LLM",
                    help="HuggingFace 数据集仓库 ID")
    ap.add_argument("--series", default=None,
                    help="逗号分隔的系列前缀过滤，如 38,36；缺省下载全部")
    ap.add_argument("--mirror", default=None,
                    help="HF 镜像端点，如国内可用 https://hf-mirror.com")
    a = ap.parse_args()

    if a.mirror:
        os.environ["HF_ENDPOINT"] = a.mirror

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("请先安装：pip install huggingface_hub", file=sys.stderr)
        sys.exit(1)

    allow = None
    if a.series:
        prefixes = [s.strip() for s in a.series.split(",") if s.strip()]
        # 数据集内文件多按 <series>_series/ 组织，用 allow_patterns 过滤
        allow = [f"*{p}_series*" for p in prefixes] + [f"*{p}.*" for p in prefixes]
        print(f"仅下载系列：{prefixes}")

    os.makedirs(a.out, exist_ok=True)
    print(f"开始下载 {a.repo} -> {a.out}（首次可能较久）...")
    path = snapshot_download(
        repo_id=a.repo, repo_type="dataset", local_dir=a.out,
        allow_patterns=allow,
    )
    print(f"完成：{path}")
    print("下一步：用 process_tspec_llm.py 或 process_3gpp_specs.py 对 .md 切片。")


if __name__ == "__main__":
    main()
