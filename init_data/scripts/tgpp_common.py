#!/usr/bin/env python3
"""3GPP 数据准备管线的公共工具：版本编码、系列推断、目录解析。

3GPP 规范版本号编码规则（archive 目录里 zip 文件名的 `-XXX` 部分）：
  格式为「主版本 + 技术版本 + 编辑版本」三段，各段用 base36 风格单字符表达：
    0-9  -> '0'..'9'
    10   -> 'a'，11 -> 'b'，... 15 -> 'f'，16 -> 'g'，17 -> 'h'，18 -> 'i'，19 -> 'j' ...
  例：'i50' = 主18.技术5.编辑0 = v18.5.0（属 Rel-18）
      'h80' = v17.8.0（Rel-17）
      早期纯数字版本 '001'/'010' = v0.0.1 / v0.1.0（Rel 之前的草案）
  规范的「主版本号」通常等于其所属 Release 号，因此「38.331 的 Rel-18 版本」= 版本号以 'i' 开头的最新那个 zip。
"""
import re


def _char_to_int(c: str) -> int:
    """单字符版本位 -> 整数。'0'..'9' -> 0..9，'a'..'z' -> 10..35。"""
    if c.isdigit():
        return int(c)
    return ord(c.lower()) - ord("a") + 10


def _int_to_char(n: int) -> str:
    """整数 -> 单字符版本位（逆运算）。"""
    if n < 10:
        return str(n)
    return chr(ord("a") + n - 10)


def decode_version(vraw: str):
    """解码 zip 文件名里的版本串（如 'i50'）为 (major, technical, editorial)。

    返回 (18, 5, 0) 之类；无法解析返回 None。
    """
    vraw = vraw.strip().lower()
    if len(vraw) != 3 or not re.match(r"[0-9a-z]{3}", vraw):
        return None
    return (_char_to_int(vraw[0]), _char_to_int(vraw[1]), _char_to_int(vraw[2]))


def version_str(vraw: str) -> str:
    """'i50' -> 'v18.5.0'（人类可读）。"""
    dv = decode_version(vraw)
    return f"v{dv[0]}.{dv[1]}.{dv[2]}" if dv else vraw


def release_of(vraw: str):
    """由版本串推断所属 Release 号（= 主版本号）。'i50' -> 18。"""
    dv = decode_version(vraw)
    return dv[0] if dv else None


def release_letter(release: int) -> str:
    """Release 号 -> 主版本首字母。18 -> 'i'。"""
    return _int_to_char(release)


def spec_to_paths(spec: str):
    """规范号（如 '38.331'）-> (series 数字, archive 子路径片段)。

    3GPP archive 目录结构：/ftp/Specs/archive/<NN>_series/<spec>/
    例：'38.331' -> ('38', '38_series/38.331')
    """
    spec = spec.strip()
    series = spec.split(".")[0]
    return series, f"{series}_series/{spec}"


def spec_num_compact(spec: str) -> str:
    """'38.331' -> '38331'（zip 文件名前缀）。"""
    return spec.replace(".", "")


def guess_doc_type(spec: str) -> str:
    """粗略推断 TS / TR（仅作默认值，建议在 config 中显式指定）。

    3GPP 惯例：TR（技术报告）多落在 xx.7xx / xx.8xx / xx.9xx 号段，
    其余多为 TS（技术规范）。此判断不完全可靠，config 显式值优先。
    """
    try:
        minor = int(spec.split(".")[1][:1])  # 首位数字
        return "TR" if minor >= 7 else "TS"
    except Exception:
        return "TS"


# archive 目录 HTML 列表里 zip 文件名的匹配：<specnum>-<3位版本>.zip
def zip_re_for(spec: str):
    num = spec_num_compact(spec)
    return re.compile(rf"{num}-([0-9a-z]{{3}})\.zip", re.IGNORECASE)


if __name__ == "__main__":
    # 自检
    assert decode_version("i50") == (18, 5, 0)
    assert decode_version("h80") == (17, 8, 0)
    assert release_of("i70") == 18
    assert release_letter(18) == "i"
    assert version_str("j30") == "v19.3.0"
    assert spec_to_paths("38.331") == ("38", "38_series/38.331")
    assert spec_num_compact("38.331") == "38331"
    print("tgpp_common 自检通过")
