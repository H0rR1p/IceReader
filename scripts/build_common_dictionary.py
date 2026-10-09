"""Package the original AI-authored TSV as a reproducible Yomitan v3 dictionary.

No API calls and no third-party dictionary definitions are used by this builder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "resources/dictionaries/common-ja-zh.tsv"
TITLE = "冰读 AI 常用日中词典（通用义项）"


def build(output: Path) -> dict:
    terms: dict[tuple[str, str], dict] = {}
    category = "未分类"
    duplicates = 0
    for number, raw in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        if raw.startswith("#"):
            category = raw.lstrip("# ").strip()
            continue
        columns = raw.split("|")
        if len(columns) != 3:
            raise ValueError(f"Line {number}: expected three columns")
        lemma, reading, gloss = (item.strip() for item in columns)
        if not lemma or not re.fullmatch(r"[ぁ-ゖー]+", reading):
            raise ValueError(f"Line {number}: invalid headword or reading")
        senses = [item.strip() for item in gloss.split("；") if item.strip()]
        if not senses or not all(re.search(r"[\u4e00-\u9fff]", item) for item in senses):
            raise ValueError(f"Line {number}: missing Chinese definition")
        key = lemma, reading
        if key in terms:
            duplicates += 1
            terms[key]["senses"] = list(dict.fromkeys(terms[key]["senses"] + senses))
        else:
            terms[key] = {"senses": senses, "category": category, "alias": False}

    base_count = len(terms)
    # Only add kana aliases for unambiguous readings. Never merge unrelated
    # homophones such as 橋 / 箸 or 青 / 青い into a new kana entry.
    reading_counts = Counter(reading for _, reading in terms)
    for (lemma, reading), record in list(terms.items()):
        key = reading, reading
        if (lemma != reading and key not in terms and reading_counts[reading] == 1
                and not re.fullmatch(r"[ぁ-ゖー]+", lemma)):
            terms[key] = {"senses": [f"【{lemma}】{sense}" for sense in record["senses"]],
                          "category": record["category"], "alias": True}

    categories = sorted({record["category"] for record in terms.values()})
    tags = {category: f"topic{index:02d}" for index, category in enumerate(categories, 1)}
    entries = [[lemma, reading, tags[record["category"]], "", 0,
                record["senses"], index, ""]
               for index, ((lemma, reading), record) in enumerate(terms.items(), 1)]
    manifest = {
        "title": TITLE, "format": 3, "revision": "2026.10.05-ai.1",
        "sequenced": True, "author": "IceReader / AI 生成",
        "description": "原创 AI 生成的日语常用词与常见搭配中文释义。未经过完整人工审校；通用词义不能替代句中义项判断。",
        "sourceLanguage": "ja", "targetLanguage": "zh",
        "package_id": "icereader/ai-common-ja-zh",
        "license": "AGPL-3.0-or-later",
        "homepage": "https://github.com/H0rR1p/IceReader",
    }
    report = {
        "base_headword_reading_pairs": base_count,
        "distinct_headwords": len({lemma for lemma, _ in list(terms)[:base_count]}),
        "kana_aliases": len(terms) - base_count,
        "importable_entries": len(entries), "duplicate_rows_merged": duplicates,
        "category_counts": dict(Counter(record["category"] for record in list(terms.values())[:base_count])),
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        "definitions": "AI-authored original draft; no copied third-party dictionary definitions",
        "review_status": "Structural validation and representative lookup checks; not fully human-reviewed",
    }
    readme = f"""# {TITLE}

版本：2026.10.05-ai.1
原始词形/读音组合：{base_count}；假名检索别名：{len(terms)-base_count}；可导入条目：{len(entries)}。
这不是 JLPT 官方分级表、语料频率排名或经全面人工审校的权威词典。
中文释义由 AI 原创生成，读音由 AI 整理；没有复制其他词典的释义。
保留多义项、同形异音和常见搭配；没有靠枚举动词活用或数字拼接扩充词数。
重复词形/读音合并。同音异义词不自动生成混合的假名别名。

## 导入
冰读 → 个人词库 → 导入其他词典 → 选择本 ZIP，不要解压。
导入后新切分章节会优先匹配本地词义。已有个人词义和句中语境释义可能优先显示。
快速句意模式仍会调用 AI 翻译句子；本包主要减少重复补充通用词义的需要。
固定搭配按完整词条提供，单字切词不一定自动匹配完整搭配。
例如「目にする」是看见、目睹；「目」也有眼睛、目光和序数标记等用法。
不能把底库义项直接认定为所有上下文的唯一正确答案。
发现错误可用现有“修正”或“AI 修正释义”接口更正个人词义。

## 授权与来源
本包数据随 IceReader 采用 AGPL-3.0-or-later。LICENSE.txt 为完整许可证。
SOURCE.tsv 为原始 AI 生成数据；build_common_dictionary.py 为打包脚本。
AI 输出可能存在误义、漏义或读音错误，统计与结构检查不代表全面语言学审校。
格式参照 Yomitan v3：https://github.com/yomidevs/yomitan/blob/master/docs/making-yomitan-dictionaries.md
"""
    files = {"index.json": json.dumps(manifest, ensure_ascii=False, indent=2),
             "tag_bank_1.json": json.dumps([[tag, "", 0, category, 0] for category, tag in tags.items()], ensure_ascii=False),
             "README.md": readme, "SOURCE.tsv": SOURCE.read_text(encoding="utf-8"),
             "build_common_dictionary.py": Path(__file__).read_text(encoding="utf-8"),
             "LICENSE.txt": (ROOT / "LICENSE").read_text(encoding="utf-8"),
             "manifest.json": json.dumps(report, ensure_ascii=False, indent=2)}
    for bank, start in enumerate(range(0, len(entries), 1000), 1):
        files[f"term_bank_{bank}.json"] = json.dumps(entries[start:start+1000], ensure_ascii=False, separators=(",", ":"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name, (2026, 10, 5, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"), compresslevel=9)
    report["package_sha256"] = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".说明.md").write_text(readme, encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "build/dictionaries/冰读-AI常用日中词典-20261005.zip")
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2))
