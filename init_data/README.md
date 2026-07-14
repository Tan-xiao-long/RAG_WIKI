# 3GPP 数据集准备管线

子任务一「3GPP 标准知识线」的第一步：把 3GPP 规范变成可精确溯源的检索切片（JSONL，遵循方案 §14.4 契约）。

完整说明见 **[数据准备指南.md](数据准备指南.md)**。

## 快速上手

```bash
pip install -r requirements.txt

# 路线 A：用现成数据集 TSpec-LLM（PoC 最省力）
python scripts/fetch_tspec_llm.py --out data/tspec_llm --series 38
python scripts/process_tspec_llm.py --in data/tspec_llm --out data/chunks.jsonl

# 路线 B：从官方 FTP 自建（生产推荐）
python scripts/download_3gpp_specs.py --config config.example.json --out data/specs_zip --release 18
python scripts/process_3gpp_specs.py --in data/specs_zip --out data/chunks.jsonl --manifest data/specs_zip/manifest.json
```

## 文件

| 文件 | 作用 |
|---|---|
| `scripts/download_3gpp_specs.py` | 从 3GPP FTP 下载 TS/TR 规范 zip，按 Release 选版本，限速+断点续传 |
| `scripts/process_3gpp_specs.py` | 解压→转文本→章节感知切片→打元数据→JSONL |
| `scripts/fetch_tspec_llm.py` | 拉取现成 TSpec-LLM 数据集（HuggingFace） |
| `scripts/process_tspec_llm.py` | 对 TSpec-LLM 的 Markdown 切片（同一契约） |
| `scripts/convert_doc.py` | .doc/.docx→文本（复用自 3gpp-topic-timeline skill） |
| `scripts/tgpp_common.py` | 版本编码/系列推断/目录解析公共工具（含自检） |
| `config.example.json` | 规范下载清单示例（5G NR Rel-18 核心 10 篇） |

## 与方案的对应

- 数据源与版本策略：§7.1
- 章节感知切片、元数据 schema：§8.1 / §8.2
- 统一切片对象契约（输出字段）：§14.4
- TDoc / Chairs Notes 不在此管线，走 `3gpp-topic-timeline` skill（§7 现有流程）
