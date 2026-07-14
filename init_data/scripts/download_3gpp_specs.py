#!/usr/bin/env python3
"""从 3GPP FTP（HTTP 镜像）下载 TS/TR 规范 zip。

数据源：https://www.3gpp.org/ftp/Specs/archive/<NN>_series/<spec>/
        每个规范目录下是历版 zip：<specnum>-<版本>.zip（如 38331-i50.zip）。

对每个规范，脚本先抓取其 archive 目录列表，按目标 Release / 版本策略
选出对应 zip 下载。带限速与断点续传（已存在且非空即跳过）。

⚠ 3GPP FTP 对高频访问敏感：--delay 建议 >= 2 秒，勿低于 1.5；大批量分批跑。

用法：
  # 下载 config.json 里列出的规范，各取 Rel-18 最新版本
  python download_3gpp_specs.py --config config.json --out data/specs_zip --release 18

  # 直接命令行指定规范号
  python download_3gpp_specs.py --specs 38.331,38.321,38.213 --out data/specs_zip --release 18

  # 取每个规范的全局最新版本（不限 Release）
  python download_3gpp_specs.py --specs 38.331 --out data/specs_zip --latest
"""
import argparse
import json
import os
import random
import re
import sys
import time

try:
    import requests
except ImportError:
    print("请先安装依赖：pip install requests", file=sys.stderr)
    sys.exit(1)

from tgpp_common import (
    spec_to_paths, spec_num_compact, zip_re_for,
    decode_version, release_of, version_str, guess_doc_type,
)

BASE = "https://www.3gpp.org/ftp/Specs/archive"
UA = "Mozilla/5.0 (compatible; 3gpp-dataset-pipeline/1.0)"


def list_versions(sess, spec, timeout=60):
    """抓取规范 archive 目录，返回 [(version_raw, zip_url), ...]（去重）。"""
    _, sub = spec_to_paths(spec)
    url = f"{BASE}/{sub}/"
    r = sess.get(url, timeout=timeout)
    r.raise_for_status()
    html = r.text
    zre = zip_re_for(spec)
    num = spec_num_compact(spec)
    seen, out = set(), []
    for m in zre.finditer(html):
        vraw = m.group(1).lower()
        if vraw in seen:
            continue
        seen.add(vraw)
        out.append((vraw, f"{url}{num}-{vraw}.zip"))
    return out


def pick_version(versions, release=None, latest=False):
    """按策略从候选版本中选一个。

    - latest=True：取全局版本号最大者（按 major.tech.editorial 排序）。
    - release=N：取属于 Rel-N（主版本==N）中版本号最大者；无则返回 None。
    """
    def key(v):
        dv = decode_version(v[0]) or (0, 0, 0)
        return dv

    valid = [v for v in versions if decode_version(v[0])]
    if not valid:
        return None
    if latest:
        return max(valid, key=key)
    cand = [v for v in valid if release_of(v[0]) == release]
    return max(cand, key=key) if cand else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--config", help="JSON 配置文件，含 specs 列表（见 config.example.json）")
    src.add_argument("--specs", help="逗号分隔的规范号，如 38.331,38.321")
    ap.add_argument("--out", required=True, help="zip 输出目录")
    ap.add_argument("--release", type=int, default=None,
                    help="目标 Release 号（如 18 表示 Rel-18）")
    ap.add_argument("--latest", action="store_true",
                    help="忽略 --release，取每个规范的全局最新版本")
    ap.add_argument("--delay", type=float, default=2.0,
                    help="请求间隔基准秒数（会叠加随机抖动）；勿 < 1.5")
    ap.add_argument("--manifest", default=None,
                    help="下载清单输出路径（JSON），默认 <out>/manifest.json")
    a = ap.parse_args()

    if a.config:
        cfg = json.load(open(a.config, encoding="utf-8"))
        specs = cfg.get("specs", [])
        default_release = cfg.get("release", a.release)
    else:
        specs = [{"spec": s.strip()} for s in a.specs.split(",") if s.strip()]
        default_release = a.release

    if not a.latest and default_release is None:
        print("错误：需指定 --release N 或 --latest（或在 config 里给 release）", file=sys.stderr)
        sys.exit(2)

    os.makedirs(a.out, exist_ok=True)
    sess = requests.Session()
    sess.headers["User-Agent"] = UA

    manifest = []
    ok = fail = skip = 0
    for item in specs:
        spec = item["spec"] if isinstance(item, dict) else item
        rel = (item.get("release") if isinstance(item, dict) else None) or default_release
        doc_type = (item.get("doc_type") if isinstance(item, dict) else None) or guess_doc_type(spec)
        try:
            versions = list_versions(sess, spec)
        except Exception as ex:
            print(f"[WARN] {spec}: 目录抓取失败 {ex}", file=sys.stderr)
            fail += 1
            time.sleep(a.delay)
            continue
        if not versions:
            print(f"[WARN] {spec}: archive 目录未发现 zip", file=sys.stderr)
            fail += 1
            continue

        chosen = pick_version(versions, release=rel, latest=a.latest)
        if not chosen:
            print(f"[WARN] {spec}: 未找到 Rel-{rel} 版本（可用：{[v[0] for v in versions][:8]}...）",
                  file=sys.stderr)
            fail += 1
            continue
        vraw, url = chosen
        dst = os.path.join(a.out, f"{spec_num_compact(spec)}-{vraw}.zip")
        rec = {"spec": spec, "doc_type": doc_type, "version_raw": vraw,
               "version": version_str(vraw), "release": release_of(vraw),
               "url": url, "file": os.path.basename(dst)}

        if os.path.exists(dst) and os.path.getsize(dst) > 0:
            print(f"  {spec} {version_str(vraw)}  [已存在，跳过]")
            manifest.append(rec)
            skip += 1
            continue

        try:
            r = sess.get(url, timeout=120)
            if r.status_code == 200 and r.content[:2] == b"PK":
                with open(dst, "wb") as f:
                    f.write(r.content)
                print(f"  {spec} {version_str(vraw)} <- {os.path.basename(url)} ({len(r.content)} B)")
                manifest.append(rec)
                ok += 1
            else:
                print(f"  {spec}: 下载失败 HTTP {r.status_code}", file=sys.stderr)
                fail += 1
        except Exception as ex:
            print(f"  {spec}: {ex}", file=sys.stderr)
            fail += 1
        time.sleep(a.delay + random.random() * a.delay / 2)

    mpath = a.manifest or os.path.join(a.out, "manifest.json")
    json.dump(manifest, open(mpath, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"\n完成：下载 {ok}，跳过(已存在) {skip}，失败 {fail}")
    print(f"清单已写入 {mpath}（供 process 阶段读取版本/类型元数据）")


if __name__ == "__main__":
    main()
