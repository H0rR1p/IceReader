"""Rebuild the complete functional-expression catalog from fixed upstream data.

No live reader data is accessed. Tsutsuji attributes are inherited exactly as
documented by its README/schema, and all L9 leaves and L1-L9 ancestors survive.
Sudachi's complete installed core dictionary is enumerated using its documented
binary layout (sudachi.rs v0.6.10 dic/{header,grammar,lexicon,read} sources).
Unknown source formats and legacy connection fields fail the build.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import mmap
from pathlib import Path
import struct
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://drive.google.com/uc?export=download&id=1-Ya2uoUzGpQEtb9SMgq5OsC4vNZ9mkn_"
SOURCE_SHA256 = "38ecf8f9d147edd7c167d31fa0a8c11648a7896fbdb1d66544aacd98c4697270"
SUDACHI_DICTIONARY_SHA256 = "cd6ee0d3d4f038314b16ddf611a776db97c1813f42068a9354b1c17e966aee53"
OFFICIAL_PAGE = "https://sites.google.com/edu.teu.ac.jp/cl-lab/研究/言語資源/日本語機能表現辞書つつじ"
RESOURCE_VERSION = "tsutsuji-1.1u-ice-1"
NS = "{http://sslab.nuee.nagoya-u.ac.jp/~matuyosi/simpleXML}"
LICENSE = "CC-BY-SA-4.0"


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # One record per line keeps generated data reviewable without duplicating
    # inherited source attributes on every leaf.
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    lines = ["{"]
    for index, (key, value) in enumerate(obj.items()):
        suffix = "," if index + 1 < len(obj) else ""
        if isinstance(value, list) and len(value) > 100:
            lines.append(f"  {encode(key)}:[")
            lines.extend("    " + encode(item) + ("," if i + 1 < len(value) else "") for i, item in enumerate(value))
            lines.append("  ]" + suffix)
        elif isinstance(value, dict) and len(value) > 50:
            lines.append(f"  {encode(key)}:{{")
            lines.extend(f"    {encode(k)}:{encode(item)}" + ("," if i + 1 < len(value) else "")
                         for i, (k, item) in enumerate(value.items()))
            lines.append("  }" + suffix)
        else:
            lines.append(f"  {encode(key)}:{encode(value)}" + suffix)
    lines.append("}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class SudachiBinary:
    """Bounded, read-only parser for the system dictionary v2 format."""

    def __init__(self, path: Path):
        self.file = path.open("rb")
        self.data = mmap.mmap(self.file.fileno(), 0, access=mmap.ACCESS_READ)
        if self.number("<Q", 0) != 0xCE9F011A92394434:
            raise ValueError("Expected Sudachi system dictionary v2")
        at = 272  # 8-byte format, 8-byte timestamp, 256-byte description.
        count = self.number("<H", at)
        at += 2
        self.pos = []
        for _ in range(count):
            fields = []
            for _ in range(6):
                value, at = self.string(at)
                fields.append(value)
            self.pos.append(fields)
        left, right = struct.unpack_from("<hh", self.data, at)
        if left < 0 or right < 0:
            raise ValueError("Invalid connection matrix dimensions")
        at += 4 + 2 * left * right
        at += 4 + 4 * self.number("<I", at)  # double-array trie
        at += 4 + self.number("<I", at)  # word-id lookup table
        self.word_count = self.number("<I", at)
        self.word_params = at + 4
        at += 4 + 6 * self.word_count  # left/right/cost parameters
        self.offsets = at
        if at + 4 * self.word_count > len(self.data):
            raise ValueError("Truncated word-info offsets")

    def number(self, fmt, at):
        return struct.unpack_from(fmt, self.data, at)[0]

    def length(self, at):
        value = self.data[at]
        at += 1
        if value >= 128:
            value = ((value & 127) << 8) | self.data[at]
            at += 1
        return value, at

    def string(self, at):
        size, at = self.length(at)
        end = at + size * 2
        if end > len(self.data):
            raise ValueError("Truncated UTF-16 string")
        return self.data[at:end].decode("utf-16le"), end

    def offset(self, word_id):
        if not 0 <= word_id < self.word_count:
            raise ValueError(f"Invalid dictionary-form word id {word_id}")
        return self.number("<I", self.offsets + 4 * word_id)

    def words(self):
        for word_id in range(self.word_count):
            surface, at = self.string(self.offset(word_id))
            _, at = self.length(at)  # headword length, not UTF-16 string
            pos_id = self.number("<H", at)
            if not 0 <= pos_id < len(self.pos):
                raise ValueError(f"Invalid POS {pos_id} at word {word_id}")
            at += 2
            normalized, at = self.string(at)
            dict_id = self.number("<i", at)
            at += 4
            reading, _ = self.string(at)
            lemma = surface if dict_id < 0 or dict_id == word_id else self.string(self.offset(dict_id))[0]
            left_id, right_id, _ = struct.unpack_from("<hhh", self.data, self.word_params + 6 * word_id)
            yield {"word_id": word_id, "surface": surface, "lemma": lemma,
                   "split_only": left_id == -1 and right_id == -1,
                   "normalized": normalized or surface, "reading": reading or surface, "pos_id": pos_id}

    def close(self):
        self.data.close()
        self.file.close()


# Explicit IPADIC-to-UniDic form mapping. Source connection codes are finite
# POS alternatives, not regular expressions to attach anywhere in raw text.
# Where Sudachi splits old 基本形 into 終止/連体, both are admitted; contextual
# expression candidates still need their source token boundaries checked.
FORM_MAP = {
    "基本形": ["終止形", "連体形"],
    "現代基本形": ["終止形", "連体形"],
    "文語基本形": ["終止形", "連体形"],
    "体言接続": ["連体形"],
    "体言接続特殊": ["連体形"],
    "体言接続特殊２": ["連体形"],
    "連用形": ["連用形-一般", "連用形-省略", "連用形-補助"],
    "連用タ接続": ["連用形-イ音便", "連用形-促音便", "連用形-撥音便", "連用形-ウ音便"],
    "連用テ接続": ["連用形-一般", "連用形-ウ音便", "連用形-補助"],
    "連用ゴザイ接続": ["連用形-ウ音便"],
    "連用ニ接続": ["連用形-ニ", "連用形-補助"],
    "ガル接続": ["語幹"],
    "未然形": ["未然形-一般", "未然形-サ", "未然形-セ", "未然形-補助", "未然形-撥音便"],
    "未然ウ接続": ["意志推量形"],
    "未然ヌ接続": ["未然形-一般", "未然形-セ", "未然形-補助"],
    "未然レル接続": ["未然形-一般", "未然形-サ", "未然形-補助"],
    "未然特殊": ["未然形-一般", "未然形-サ", "未然形-セ"],
    "仮定形": ["仮定形-一般", "已然形-一般", "已然形-補助"],
    "仮定縮約１": ["仮定形-融合"],
    "仮定縮約２": ["仮定形-融合"],
    "命令ｅ": ["命令形"], "命令ｉ": ["命令形"],
    "命令ｒｏ": ["命令形"], "命令ｙｏ": ["命令形"],
}
TYPE_MAP = {
    "一段": ["上一段", "下一段"],
    "一段・クレル": ["下一段-ラ行"], "一段・得ル": ["下一段-ア行"],
    "カ変・クル": ["カ行変格"], "カ変・来ル": ["カ行変格"],
    "サ変・スル": ["サ行変格"], "サ変・−スル": ["サ行変格"], "サ変・−ズル": ["サ行変格"],
    "五段・サ行": ["五段-サ行"], "五段・ガ行": ["五段-ガ行"],
    "五段・マ行": ["五段-マ行"], "五段・ナ行": ["五段-ナ行"], "五段・バ行": ["五段-バ行"],
    "特殊・タ": ["助動詞-タ"], "特殊・ダ": ["助動詞-ダ", "助動詞-ジャ"],
    "特殊・ヌ": ["助動詞-ヌ", "文語助動詞-ズ"],
    "文語・ベシ": ["文語助動詞-ベシ"], "不変化型": ["無変化型"],
}
POS2_MAP = {
    "格助詞": ["格助詞"], "終助詞": ["終助詞"], "接続助詞": ["接続助詞"],
    "副助詞": ["副助詞"], "係助詞": ["係助詞"], "並立助詞": ["副助詞"],
    "副助詞／並立助詞／終助詞": ["副助詞", "終助詞"],
    "特殊": ["格助詞", "副助詞", "接続助詞", "終助詞"],
    "非自立": ["普通名詞"],
}


def matches_prefix(value, prefixes):
    return any(value == p or value.startswith(p + "-") or value.startswith(p + "+") for p in prefixes)


def compile_pattern(fields, all_pos):
    if len(fields) == 6:  # source 7V omits its trailing wildcard
        fields.append("*")
    if len(fields) != 7:
        raise ValueError(f"Unknown legacy pattern arity: {fields}")
    main, second, third, fourth, ctype, form, lemma = fields
    if fourth != "*" or third not in ("*", "一般", "連語", "副詞可能"):
        raise ValueError(f"Unmapped legacy POS detail: {fields}")
    if main not in ("*", "名詞", "動詞", "形容詞", "副詞", "接続詞", "助詞", "助動詞"):
        raise ValueError(f"Unknown legacy main POS: {main}")
    if second != "*" and second not in POS2_MAP:
        raise ValueError(f"Unknown legacy secondary POS: {second}")
    if ctype != "*" and ctype not in TYPE_MAP:
        raise ValueError(f"Unknown legacy conjugation type: {ctype}")
    if form != "*" and form not in FORM_MAP:
        raise ValueError(f"Unknown legacy conjugation form: {form}")
    main_set = {"名詞", "代名詞", "形状詞"} if main == "名詞" else {main}
    pos_ids = []
    for index, pos in enumerate(all_pos):
        # IPADIC 名詞,非自立 (の/ん nominalization) becomes UniDic 準体助詞.
        # Preserve that functional correspondence without allowing every 助詞.
        nominalizer = main == "名詞" and second == "*" and pos[:2] == ["助詞", "準体助詞"]
        if main != "*" and pos[0] not in main_set and not nominalizer:
            continue
        if second != "*" and pos[1] not in POS2_MAP[second]:
            continue
        if third == "副詞可能" and pos[2] != "副詞可能":
            continue
        if ctype != "*" and not matches_prefix(pos[4], TYPE_MAP[ctype]):
            continue
        if form == "文語基本形" and not pos[4].startswith("文語"):
            continue
        if form == "現代基本形" and pos[4].startswith("文語"):
            continue
        form_options = FORM_MAP.get(form, [])
        if ctype in ("特殊・タ", "特殊・ダ") and form == "未然形":
            # IPADIC だろ/たろ + う is a single 意志推量形 in UniDic.
            form_options = [*form_options, "意志推量形"]
        if ctype == "特殊・ヌ" and form == "連用ニ接続":
            # IPADIC ず (the に-attaching negative) is 連用形-一般 in core.
            form_options = [*form_options, "連用形-一般"]
        if form != "*" and not matches_prefix(pos[5], form_options):
            # IPADIC adjective/だ past-connective forms are かった/だっ.
            if not (form == "連用タ接続" and pos[4] in ("形容詞", "助動詞-ダ", "助動詞-ジャ") and pos[5] == "連用形-促音便"):
                continue
        pos_ids.append(index)
    # Lemma aliases reflect normalization differences between the tagsets.
    lemmas = [] if lemma == "*" else [lemma]
    if ctype == "特殊・タ" and lemma == "だ":
        lemmas.append("た")  # voiced surface だ has dictionary form た in core
    if ctype == "特殊・ヌ" and lemma == "ぬ":
        lemmas.extend(["ず", "ん"])
    if ctype == "特殊・ダ" and lemma == "だ":
        lemmas.append("じゃ")
    return {"legacy": fields, "pos_ids": pos_ids, "lemma": list(dict.fromkeys(lemmas))}


def connections(text, all_pos):
    result = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        code, value = line.split("\t", 1)
        alternatives = value.split(";")
        if "," in value:
            result[code] = {"patterns": [compile_pattern(v.split(","), all_pos) for v in alternatives], "refs": []}
        else:
            result[code] = {"patterns": [], "refs": alternatives}
    for code, spec in result.items():
        for ref in spec["refs"]:
            if ref not in result:
                raise ValueError(f"Unknown connection reference {code}->{ref}")
        if any(not item["pos_ids"] for item in spec["patterns"]):
            if code != "7V":
                raise ValueError(f"Unmapped active source connection {code}")
            # 7V = old copula imperative: there is no matching single Sudachi
            # copula POS. It is unused by every source leaf and reference.
            spec["mapping_status"] = "unrepresented_unused"
            spec["mapping_note"] = "Legacy 特殊・ダ/命令ｅ has no single-token core20250515 POS; no leaf or compound uses this code; fail closed."
    return result


def load_teaching():
    path = ROOT / "resources/grammar/functional_teaching.py"
    spec = importlib.util.spec_from_file_location("functional_teaching", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tsutsuji_catalog(path, all_pos):
    if digest(path) != SOURCE_SHA256:
        raise ValueError("Tsutsuji archive SHA-256 does not match the fixed official release")
    teaching = load_teaching()
    with zipfile.ZipFile(path) as source:
        read = lambda name: source.read("tsutsuji-1.1u/" + name)
        xml = read("tsutsuji1.1.xml")
        root = ET.fromstring(xml)
        conn = connections(read("connectID").decode("utf-8"), all_pos)
        classes = {}
        for line in read("className").decode("utf-8").splitlines():
            if not line.strip():
                continue
            code, name = line.split("\t", 1)
            name_zh, explanation = teaching.teaching_for(code)
            classes[code] = {"name_ja": name, "name_zh": name_zh, "explanation_zh": explanation,
                             "parent": code[:-1] if len(code) > 1 else None,
                             "explanation_source": "IceReader original Chinese teaching text"}
        nodes, entries, counts = [], [], Counter()

        def walk(node, inherited, path_ids):
            tag = node.tag.removeprefix(NS)
            if tag not in {f"L{i}" for i in range(1, 10)}:
                raise ValueError(f"Unexpected XML hierarchy tag {tag}")
            level = int(tag[1:])
            node_id = node.attrib["ID" if level == 9 else ("L1ID" if level == 1 else f"L1to{level}ID")]
            attrs = dict(inherited)
            attrs.update(node.attrib)
            if level == 2:
                attrs["_L2_BASE"] = attrs["BASE"]
            node_path = [*path_ids, node_id]
            nodes.append({"id": node_id, "parent": path_ids[-1] if path_ids else None,
                          "level": level, "base": node.attrib.get("BASE", ""), "attrs": dict(node.attrib)})
            counts[str(level)] += 1
            if level == 9:
                segments = (node.text or "").strip().split(".")
                surface = "".join(segments)
                if not surface or attrs["MCLASS"] not in classes:
                    raise ValueError(f"Invalid leaf {node_id}")
                for direction in ("LEFT", "RIGHT"):
                    code = attrs[direction]
                    if len(code) != 4 or code[2:] != "90" or code[:2] not in conn:
                        raise ValueError(f"Unknown {direction} connection {node_id}: {code}")
                entries.append({"id": node_id, "surface": surface, "reading": attrs["PRONUNCIATION"].replace(".", ""),
                                "segments": segments, "path": node_path,
                                "lemma": attrs["_L2_BASE"].replace(".", ""),
                                "function": attrs["L3ID"], "mclass": attrs["MCLASS"],
                                "left": attrs["LEFT"], "right": attrs["RIGHT"],
                                "style": attrs["STYLE"], "difficulty": attrs["DIFFICULTY"],
                                "uncommon": attrs.get("UNCOMMON", "0") == "1",
                                "core": [int(x) for x in attrs["CORE"].split("/")]})
            else:
                for child in node:
                    walk(child, attrs, node_path)

        for node in root:
            walk(node, {}, [])
        if len(entries) != 16801 or len({e["id"] for e in entries}) != 16801:
            raise ValueError("Tsutsuji must preserve exactly 16,801 unique L9 IDs")
        # Independently check the published UTF-8 L9 list. Excel CSV is Shift-JIS;
        # parse it too to catch accidental locale decoding or omitted rows.
        list_rows = list(csv.reader(io.StringIO(read("L9.list").decode("utf-8"))))
        exported = {row[1]: row for row in list_rows}
        for entry in entries:
            row = exported[entry["id"]]
            if [entry[k] for k in ("surface", "id", "reading", "mclass", "difficulty", "style", "left", "right")] != row[:8]:
                raise ValueError(f"XML/list mismatch for {entry['id']}")
        used_codes = {e[k][:2] for e in entries for k in ("left", "right")}
        referenced_codes = {ref for item in conn.values() for ref in item["refs"]}
        if "7V" in used_codes or "7V" in referenced_codes:
            raise ValueError("Previously unused legacy copula imperative now requires an explicit mapping")
        csv_rows = list(csv.reader(io.StringIO(read("tsutsuji.csv").decode("shift_jis"))))
        if len(csv_rows) < 16801:
            raise ValueError("Excel CSV is truncated")
        catalog = {"schema_version": 1, "version": RESOURCE_VERSION,
                   "source": {"name": "Tsutsuji Japanese functional expression dictionary", "version": "1.1u",
                              "authors": ["Suguru Matsuyoshi", "Satoshi Sato"], "copyright": "2008 Suguru Matsuyoshi and Satoshi Sato",
                              "official_page": OFFICIAL_PAGE, "download_url": SOURCE_URL, "sha256": SOURCE_SHA256,
                              "xml_sha256": hashlib.sha256(xml).hexdigest(), "license": LICENSE,
                              "license_url": "https://creativecommons.org/licenses/by-sa/4.0/"},
                   "counts": {"levels": dict(sorted(counts.items())), "leaves": len(entries),
                              "nodes": len(nodes), "semantic_classes": len(classes), "connection_codes": len(conn)},
                   "pos_sets": all_pos,
                   "function_labels": {"P": "格助词", "Q": "接续助词", "D": "连体修饰", "C": "连词", "M": "助动词", "N": "形式名词", "T": "强调限定", "W": "话题"},
                   "connection_semantics": {"left": "preceding morphological POS union", "right": "terminal morpheme POS union, NOT following-token constraint",
                                            "code_format": "two-character connectID + 90", "matching": "recursive OR refs; exact pos_sets[pos_ids]; lemma condition if nonempty",
                                            "unmapped": "generation fails; no unknown-to-wildcard fallback"},
                   "semantic_classes": classes, "connections": conn, "nodes": nodes,
                   "entries": sorted(entries, key=lambda x: x["id"])}
        notices = {name: read(name) for name in ("README.txt", "connectID", "schema.xsd", "className")}
        return catalog, notices


def inventory(path, binary):
    pos = binary.pos
    records = []
    scanned = Counter()
    for item in binary.words():
        fields = pos[item["pos_id"]]
        kind = None
        if fields[0] == "助詞":
            kind = "particle"
        elif fields[0] == "助動詞":
            kind = "auxiliary"
        elif fields[0] in ("動詞", "形容詞") and fields[1] == "非自立可能":
            kind = "support"
        if kind:
            item["kind"] = kind
            records.append(item)
            scanned[kind] += 1
    functional_ids = [i for i, p in enumerate(pos) if p[0] in ("助詞", "助動詞") or (p[0] in ("動詞", "形容詞") and p[1] == "非自立可能")]
    observed = {r["pos_id"] for r in records}
    if set(functional_ids) != observed:
        raise ValueError(f"Functional POS entries missing: {set(functional_ids) - observed}")
    return {"schema_version": 1, "version": "sudachi-core-20250515-ice-1",
            "source": {"name": "Sudachi core dictionary", "version": "20250515", "tokenizer_version": "0.6.10",
                       "system_dictionary_sha256": digest(path), "license": "Apache-2.0",
                       "authors": ["Works Applications"],
                       "notice_path": "third_party_licenses/python/sudachidict-core-20250515/00-sudachidict_core-20250515.dist-info_licenses_LICENSE-2.0.txt",
                       "changes": "IceReader full binary word-info enumeration, functional-POS selection, JSON conversion; original word IDs, forms, POS and normalization retained",
                       "binary_layout_reference": "https://github.com/WorksApplications/sudachi.rs/tree/v0.6.10/sudachi/src/dic",
                       "enumeration": "all word-info records, without tokenization sampling"},
            "counts": {"all_pos": len(pos), "functional_pos": len(functional_ids), "all_dictionary_words": binary.word_count,
                       "functional_words": len(records), "by_kind": dict(sorted(scanned.items()))},
            "pos": pos, "functional_pos_ids": functional_ids, "forms": records,
            "selection": {"particle": ["助詞"], "auxiliary": ["助動詞"],
                          "support": "動詞/形容詞 with POS2 非自立可能; contextual candidate, not automatically grammatical in every occurrence"}}


def write_notice(path, notices, catalog, inv):
    path.mkdir(parents=True, exist_ok=True)
    for name in ("README.txt", "connectID", "schema.xsd", "className"):
        (path / name).write_bytes(notices[name])
    (path / "NOTICE.md").write_text(
        "# Tsutsuji attribution and changes\n\n"
        "日本語機能表現辞書『つつじ』 / Tsutsuji, release 1.1u.\n\n"
        "Copyright © 2008 Suguru Matsuyoshi and Satoshi Sato.\n\n"
        f"Official source: {OFFICIAL_PAGE}\n\n"
        "The authors distribute the dictionary under Creative Commons Attribution-ShareAlike 4.0 International as of 2023-11-03.\n"
        "License: https://creativecommons.org/licenses/by-sa/4.0/\n\n"
        "IceReader changes: converted the complete nine-level XML hierarchy and inherited attributes to JSON; retained all 16,801 surface-form IDs, source connection definitions and source attributes; added an explicit IPADIC-to-Sudachi connection map and newly written simple Chinese teaching text.\n\n"
        "The derived resources/grammar/functional_expressions.json and its Tsutsuji-based hierarchy/connection adaptations are distributed under CC-BY-SA-4.0. Original Chinese explanations in this combined dictionary are likewise made available under CC-BY-SA-4.0. Application code has its separately stated software license.\n\n"
        f"Fixed archive: tsutsuji-1.1u.zip; SHA-256 `{SOURCE_SHA256}`.\n"
        "The corresponding-source package includes this archive under dependency-sources/.\n\n"
        "resources/grammar/functional_inventory.json is separately derived from Sudachi core 20250515 by Works Applications, Apache-2.0. IceReader enumerated all word-info records and converted the functional-word subset to JSON. The pinned package source and SudachiPy 0.6.10 binary-format source accompany the dependency sources; the core package downloads its fixed dictionary during installation. Regeneration uses that installed dictionary (or --sudachi-dictionary) and verifies the hash below. Apache attribution and license texts are retained in third_party_licenses/python/sudachidict-core-20250515/.\n"
        f"Sudachi system dictionary SHA-256: `{inv['source']['system_dictionary_sha256']}`.\n",
        encoding="utf-8")
    write_json(path / "source.json", {**catalog["source"], "changes": "Full XML to JSON; inherited teaching text; explicit legacy-to-Sudachi mapping",
                                     "derived_files": ["resources/grammar/functional_expressions.json"],
                                     "inventory_source": inv["source"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsutsuji-source", type=Path, default=ROOT / "build/legal-cache/tsutsuji-1.1u.zip")
    parser.add_argument("--sudachi-dictionary", type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "resources/grammar")
    parser.add_argument("--check", action="store_true", help="Compare reproducible resources without rewriting")
    args = parser.parse_args()
    if importlib.metadata.version("SudachiPy") != "0.6.10" or importlib.metadata.version("sudachidict_core") != "20250515":
        raise ValueError("Resource build requires SudachiPy 0.6.10 and sudachidict_core 20250515")
    if not args.tsutsuji_source.exists():
        args.tsutsuji_source.parent.mkdir(parents=True, exist_ok=True)
        args.tsutsuji_source.write_bytes(urllib.request.urlopen(SOURCE_URL, timeout=120).read())
    if args.sudachi_dictionary is None:
        import sudachidict_core
        args.sudachi_dictionary = Path(sudachidict_core.__file__).parent / "resources/system.dic"
    if digest(args.sudachi_dictionary) != SUDACHI_DICTIONARY_SHA256:
        raise ValueError("Sudachi core dictionary SHA-256 does not match the fixed 20250515 release")
    binary = SudachiBinary(args.sudachi_dictionary)
    try:
        catalog, read = tsutsuji_catalog(args.tsutsuji_source, binary.pos)
        inv = inventory(args.sudachi_dictionary, binary)
    finally:
        binary.close()
    for name, data in (("functional_expressions.json", catalog), ("functional_inventory.json", inv)):
        output = args.output_dir / name
        if args.check:
            if json.loads(output.read_text(encoding="utf-8")) != data:
                raise ValueError(f"Generated resource drift: {output}")
        else:
            write_json(output, data)
    if not args.check:
        write_notice(ROOT / "third_party_licenses/tsutsuji", read, catalog, inv)
    print(json.dumps({"expressions": catalog["counts"], "inventory": inv["counts"], "checked": args.check}, ensure_ascii=False))


if __name__ == "__main__":
    main()
