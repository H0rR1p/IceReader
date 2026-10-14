"""Non-destructive functional-expression graph over original Sudachi atoms.

Dictionary lookup uses a surface trie and exact published left connections.
Composition uses typed predicates, nominalizers, auxiliaries and endings.
Neither step rewrites tokens or removes a shorter / crossing interpretation.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from functools import lru_cache
import json
from typing import Iterable

from ...paths import resource_path
from .rules import stable_span_id, token_class, token_form, token_pos, validate_tokens


PARTICLE_CLASSES = {
    "格助詞": ("case", "格助词", "说明前面的词与动作的关系，如对象、地点、方向或来源。"),
    "副助詞": ("focus", "副助词", "给前面的内容加上范围、程度、举例或限定等意思。"),
    "係助詞": ("topic", "系助词", "指出话题，或给前面的内容加上对比、追加等意思。"),
    "接続助詞": ("connective", "接续助词", "连接前后的动作或情况，说明条件、原因、转折等关系。"),
    "終助詞": ("ending", "终助词", "放在句末，表示提问、确认、提醒或说话人的语气。"),
    "準体助詞": ("nominalizer", "准体助词", "把前面的动作或情况作为一件事来谈。"),
}
PARTICLE_TEXT = {
    "は": "提出话题，也可强调与别的情况不同。", "も": "表示也、同样；和其他成分组合时，还可表示即使或强调。",
    "が": "可指出动作或状态的主体；连接两句话时，也可表示转折。", "を": "通常指出动作的对象，也可指出经过或离开的地方。",
    "に": "可指出对象、时间、到达点或动作的目标；具体意思由前后内容决定。",
    "へ": "表示动作所朝向的方向。", "で": "可说明动作发生的地点、使用的手段或原因；也可参与判断表达。",
    "と": "可表示和、一起，或引用内容；接在句子后还可表示条件。", "から": "可说明起点、来源；接在句子后还可说明原因。",
    "まで": "表示范围或动作延续到的终点。", "より": "可表示比较的基准，也可表示起点或来源。",
    "の": "可连接修饰内容与名词；也可把前面的事作为一个整体，或在句末补充说明。",
    "ん": "在这里是「の」的口语形式，把前面的情况作为一件事来说明。",
    "て": "连接前后动作或情况；和后面的辅助成分合起来可表示持续、尝试、准备等意思。",
    "ば": "接在假定形后，表示如果、只要等条件。", "なら": "把前面的内容作为条件或谈话前提，表示如果这样、要是说到……。",
    "くらい": "表示大约的数量、程度或范围；也可强调只有这种程度。", "ぐらい": "表示大约的数量、程度或范围；也可强调只有这种程度。",
    "だけ": "表示只有这些，或限制某个范围。", "しか": "和否定表达合用，表示除此以外没有，也就是只有这些。",
    "ほど": "表示程度；用于比较时，可以说明达到或没有达到某种程度。", "ばかり": "可表示只有、大致的量，或动作刚刚完成。",
    "など": "举出例子，表示之类的；有时也表示说话人对所举内容的态度。", "なんか": "口语中举出例子，表示之类的；也可带有强调或轻视的语气。",
    "なんて": "口语中引用或举出某件事，常带有惊讶、强调等语气。", "さえ": "表示连……都；和条件表达合用时，可表示只要。",
    "こそ": "强调正是前面的内容。", "でも": "可表示举例、追加或让步；在句子开头还可表示但是。",
    "か": "可表示提问、不确定的选择，或某一个不特定的人、事物。", "ね": "向对方确认，或表示希望对方有相同感受。",
    "よ": "向对方告知、提醒或强调。", "な": "可表示感叹或确认；接在动词后时也可能表示禁止。",
    "ぞ": "用较强的语气告知或强调。", "ぜ": "口语中用较随意而较强的语气告知或强调。",
    "わ": "给句末加上说话人的感受或强调语气。", "っけ": "回想或确认以前知道的事。", "け": "方言或口语中用于确认、提问等句末语气。",
    "ながら": "可表示两个动作同时进行，也可表示虽然如此。", "ので": "说明前面的情况是后面内容的原因。",
    "のに": "可表示虽然如此却……；也可把一件事作为后面动作的对象。", "けれど": "连接前后情况，表示转折，也可委婉地引出后面的话。",
    "けど": "口语中表示转折，也可委婉地引出后面的话。", "し": "列举情况或理由，暗示还可以有其他同类内容。",
    "たり": "举出动作或状态的例子，表示做这些事情等。", "とも": "可表示即使，也可加强肯定或说明共同的情况。",
}

# Prefixes are UniDic / Sudachi conjugation forms, not surface suffix guesses.
# Each key has an independently tested positive and invalid attachment below.
AUX_CONNECTIONS = {
    "助動詞-タ": ("連用形",), "助動詞-タイ": ("連用形",), "助動詞-マス": ("連用形",),
    "助動詞-ナイ": ("未然形",), "助動詞-ヌ": ("未然形",), "助動詞-ヘン": ("未然形",),
    "助動詞-ナンダ": ("未然形",), "助動詞-レル": ("未然形",),
    "文語助動詞-ズ": ("未然形",), "文語助動詞-ジ": ("未然形",), "文語助動詞-ム": ("未然形",),
    "文語助動詞-キ": ("連用形",), "文語助動詞-ケリ": ("連用形",), "文語助動詞-ケム": ("連用形",),
    "文語助動詞-ツ": ("連用形",), "文語助動詞-ヌ": ("連用形",), "文語助動詞-タリ-完了": ("連用形",),
    "文語助動詞-ベシ": ("終止形", "連体形"), "文語助動詞-マジ": ("終止形", "連体形"),
    "文語助動詞-ラム": ("終止形", "連体形"), "文語助動詞-ラシ": ("終止形", "連体形"),
    "助動詞-マイ": ("終止形", "連体形"), "助動詞-ラシイ": ("終止形", "連体形"),
    "文語助動詞-ナリ-伝聞": ("終止形", "連体形"), "文語助動詞-ゴトシ": ("連体形",),
    "文語助動詞-リ": ("仮定形", "連用形"),
}
COPULA_CLASSES = {"助動詞-ダ", "助動詞-ジャ", "助動詞-ヤ", "助動詞-ドス", "助動詞-デス",
                   "文語助動詞-ナリ-断定", "文語助動詞-タリ-断定", "文語助動詞-ザマス", "文語助動詞-ザンス"}
TE_AUX_LEMMAS = {
    "いる", "居る", "ある", "有る", "おく", "置く", "しまう", "仕舞う", "みる", "見る", "いく", "行く", "くる", "来る",
    "くれる", "呉れる", "あげる", "上げる", "やる", "もらう", "貰う", "いただく", "頂く", "くださる", "下さる",
    "おる", "居る", "いらっしゃる", "なさる", "ござる", "ある", "見せる",
}
TE_AUX_TEXT = {
    "いる": "表示动作正在进行，或动作之后的状态还在持续。", "ある": "表示有人做了某事，结果作为一种状态保留下来。",
    "おく": "表示提前做，或让某个状态保持不变。", "しまう": "表示动作完成，也可带有后悔、意外等感受。",
    "みる": "表示试着做。", "いく": "表示动作朝远处发展，或情况从现在继续变化。", "くる": "表示动作朝这里发展，或情况逐渐发展到现在。",
    "くれる": "表示别人为自己或自己一方做某事。", "あげる": "表示为别人做某事。", "もらう": "表示得到别人为自己做某事的帮助。",
}
NORMALIZATIONS = {
    "じゃ": ("では", "判断形式的口语缩约"), "ん": ("の", "名词化成分的口语缩约"),
    "てる": ("ている", "持续表达的口语缩约"), "でる": ("でいる", "持续表达的口语缩约"),
    "ちゃう": ("てしまう", "完成表达的口语缩约"), "じゃう": ("でしまう", "完成表达的口语缩约"),
    "ちゃ": ("ては", "接续成分的口语缩约"), "なきゃ": ("なければ", "否定条件的口语缩约"),
    "なくちゃ": ("なくては", "否定接续的口语缩约"), "とく": ("ておく", "准备表达的口语缩约"),
    "どく": ("でおく", "准备表达的口语缩约"),
}
FUNCTION_LABELS = {"P": "复合格助词", "Q": "接续表达", "D": "修饰表达", "C": "连接表达", "M": "辅助表达",
                   "N": "形式名词表达", "T": "强调与限定", "W": "话题表达"}
COMPOSITIONS = {
    "predicate_auxiliary": ("完整活用", "前面的词与助动词合起来，表示完整的动作或状态。", "morphology"),
    "predicate_te": ("接续形式", "用接续形式连接后面的动作、状态或辅助表达。", "morphology"),
    "te_auxiliary": ("接续与辅助表达", "前面的动作与辅助成分合起来说明持续、结果、准备等意思。", "morphology"),
    "nominal_copula": ("判断表达", "把前面的内容判断为某种身份或状态。", "morphology"),
    "copular_negative": ("判断的否定表达", "否定前面说的身份或状态，表示不是、并非如此。", "construction"),
    "predicate_nominalizer": ("动作与说明成分", "把前面的动作或情况作为一件事来说明。", "construction"),
    "predicate_nominal_expression": ("动作与形式表达", "把前面的动作作为打算、事情、理由等内容来说明。", "construction"),
    "predicate_functional": ("完整辅助表达", "把前面的动作或状态和辅助表达连起来，说明完整的意思。", "morphology"),
    "nominalized_copula": ("说明表达", "用名词化成分和判断表达补充说明前面的情况。", "construction"),
    "predicate_ending": ("完整句末表达", "完整保留句末成分，用来提问、确认、提醒或表达感受。", "ending"),
    "nominalized_ending": ("说明与句末表达", "说明前面的情况，再用完整的句末表达确认、提问或表达感受。", "ending"),
    "particle_sequence": ("完整助词组合", "这些助词一起说明范围、限定、追加、强调或前后关系。", "construction"),
    "degree_condition": ("程度与条件", "把前面的程度作为条件，表示如果只是这个程度、这样的话。", "construction"),
    "nominal_particle": ("名词与助词", "用助词说明这个词与后面内容的关系。", "construction"),
    "predicate_connective": ("完整接续表达", "把前面的动作或状态作为条件、原因或前后关系来说明。", "construction"),
}


def _read(name: str) -> dict:
    with (resource_path("resources/grammar") / name).open(encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def functional_data() -> dict:
    data = _read("functional_expressions.json")
    if data.get("schema_version") != 1 or not data.get("version"):
        raise ValueError("Unsupported functional-expression resource")
    return data


@lru_cache(maxsize=1)
def functional_inventory() -> dict:
    return _read("functional_inventory.json")


def functional_version() -> str:
    return functional_data()["version"] + ":" + functional_inventory()["version"]


def functional_manifest() -> dict:
    data, inventory = functional_data(), functional_inventory()
    return {"version": functional_version(), "form_count": len(data["entries"]), "hierarchy_node_count": len(data["nodes"]),
            "pos_configuration_count": len(inventory["pos"]), "inventory_form_count": len(inventory["forms"])}


def _hex(value: str) -> str:
    return value.encode("utf-8").hex() or "00"


def _unhex(value: str) -> str:
    return "" if value == "00" else bytes.fromhex(value).decode("utf-8")


def _semantic_id(entry: dict) -> str:
    return "ja.functional.tsutsuji." + str(entry["path"][2]).lower()


@lru_cache(maxsize=1)
def _resource_index():
    data = functional_data()
    trie: dict = {}
    rules: dict[str, dict] = {}
    for entry in data["entries"]:
        cursor = trie
        for char in entry["surface"]:
            cursor = cursor.setdefault(char, {})
        cursor.setdefault("", []).append(entry)
        identity = _semantic_id(entry)
        if identity not in rules:
            semantic = data["semantic_classes"].get(entry["mclass"], {})
            rules[identity] = {"id": identity, "label": entry["lemma"], "explanation_zh": semantic.get("explanation_zh") or semantic.get("name_zh", ""),
                               "kind": "construction", "status": "enabled", "source": "tsutsuji", "source_ids": entry["path"][:3]}
    return trie, rules


def _aux_text(lemma: str, ctype: str) -> str:
    if lemma in {"れる", "られる"}:
        return "可表示被动、能够、不由自主或尊敬；需要结合前面的词和句意区分。"
    if lemma in {"せる", "させる", "す", "しむ"}:
        return "表示让别人做、使某种情况发生。"
    if lemma in {"ない", "ぬ", "ず", "ん", "へん"} or ctype in {"文語助動詞-ズ", "助動詞-ナイ", "助動詞-ヌ", "助動詞-ヘン"}:
        return "表示否定，也就是没有、不这样做或不是这种情况。"
    if ctype in COPULA_CLASSES:
        return "表示判断，也就是是、属于这种情况；活用后可表示条件、否定或推量。"
    if ctype == "助動詞-タ":
        return "可表示过去、完成或结果；接续形式还可表示如果或列举。"
    if ctype == "助動詞-タイ":
        return "表示想做某事。"
    if ctype in {"助動詞-マス", "助動詞-ヤス", "文語助動詞-ンス", "文語助動詞-ザマス", "文語助動詞-ザンス"}:
        return "用礼貌的方式表达动作或状态。"
    if ctype in {"助動詞-ラシイ", "文語助動詞-ラシ"}:
        return "根据听到或观察到的情况作出推测，表示似乎、好像。"
    if ctype in {"助動詞-マイ", "文語助動詞-ジ", "文語助動詞-マジ"}:
        return "可表示推测不会发生，或表达不打算这样做。"
    if ctype in {"文語助動詞-ム", "文語助動詞-ラム", "文語助動詞-ケム"}:
        return "文语中表达推测、意志或对情况的判断。"
    if ctype in {"文語助動詞-キ", "文語助動詞-ケリ"}:
        return "文语中表达过去的事情或对情况的感受。"
    if ctype in {"文語助動詞-ツ", "文語助動詞-ヌ", "文語助動詞-リ", "文語助動詞-タリ-完了"}:
        return "文语中表达动作完成，或完成后保持的状态。"
    if ctype == "文語助動詞-ナリ-伝聞":
        return "文语中表示听说，或根据声音等线索作出的判断。"
    if ctype == "文語助動詞-ゴトシ":
        return "文语中表示像、如同，用来比喻或举例。"
    if ctype == "文語助動詞-ベシ":
        return "文语中可表达推测、应该、适合、能够等意思，具体用法由句意决定。"
    if lemma in {"そう", "よう"}:
        return "与前面的词组合，表示看起来、似乎或好像等判断。"
    return "与前面的词组合，补充动作、状态或说话语气的意思。"


def functional_rule(grammar_id: str) -> dict | None:
    if grammar_id.startswith("ja.functional.tsutsuji."):
        value = _resource_index()[1].get(grammar_id)
        return deepcopy(value) if value else None
    if grammar_id.startswith("ja.functional.composition."):
        key = grammar_id.removeprefix("ja.functional.composition.")
        if key in COMPOSITIONS:
            label, explanation, role = COMPOSITIONS[key]
            return {"id": grammar_id, "label": label, "explanation_zh": explanation, "kind": "morphology" if role == "morphology" else "construction", "status": "enabled"}
    if grammar_id.startswith("ja.functional.atom."):
        try:
            role, class_code, lemma_code = grammar_id.removeprefix("ja.functional.atom.").split(".")
            lemma = _unhex(lemma_code)
            if role == "particle":
                item = next((value for value in PARTICLE_CLASSES.values() if value[0] == class_code), None)
                if item is None:
                    return None
                explanation = PARTICLE_TEXT.get(lemma, item[2])
            elif role == "auxiliary":
                explanation = _aux_text(lemma, _unhex(class_code))
            else:
                return None
            return {"id": grammar_id, "label": lemma, "explanation_zh": explanation, "kind": "construction", "status": "enabled"}
        except (ValueError, UnicodeError):
            return None
    return None


def _normalization(tokens: Iterable[dict], text: str, start: int, end: int) -> tuple[str, list[dict]]:
    operations = []
    for token in tokens:
        surface = token["surface"]
        value = NORMALIZATIONS.get(surface)
        # A polite-negative ん is not nominalizer の. A lexical でる is not
        # a contraction either: only the actual auxiliary class is expanded.
        if surface == "ん" and token_pos(token)[:2] != ("助詞", "準体助詞"):
            value = None
        if surface in {"でる", "てる"} and token_pos(token)[0] != "助動詞":
            value = None
        if value and start <= token["start"] and token["end"] <= end:
            expanded, description = value
            operations.append({"start": token["start"], "end": token["end"], "surface": surface,
                               "normalized": expanded, "operation": "contraction", "description_zh": description,
                               "virtual_segments": [{"surface": expanded, "original_start": token["start"], "original_end": token["end"]}]})
    result, cursor = [], start
    for item in operations:
        result.extend([text[cursor:item["start"]], item["normalized"]])
        cursor = item["end"]
    result.append(text[cursor:end])
    return "".join(result), operations


def _content(token: dict) -> bool:
    return token_pos(token)[0] not in {"記号", "補助記号", "空白"} and bool(token["surface"].strip())


def _atomic_role(token: dict, previous: dict | None, morph: list[dict]) -> str:
    pos = token_pos(token)
    if pos[0] == "助詞":
        return "particle"
    if pos[0] == "助動詞" or pos[:2] == ("形状詞", "助動詞語幹"):
        return "auxiliary"
    if pos[1] == "非自立可能":
        attached = any(len(item.get("token_ids", [])) > 1 and token["id"] in item.get("token_ids", [])[1:] for item in morph)
        if attached or previous and previous["end"] == token["start"] and (
            previous["surface"] in {"て", "で"} and token_pos(previous)[1] == "接続助詞" or
            token.get("lemma") == "ない" and previous.get("lemma") == "だ"
        ):
            return "auxiliary"
    return "lexical"


def _span(sentence_id: str, text: str, tokens: list[dict], start: int, end: int, key: str, *, role: str,
          grammar_ids: list[str] | None = None, explanation: str = "", lemma: str = "", features: list[str] | None = None,
          children: list[str] | None = None, candidates: list[dict] | None = None, evidence: list[str] | None = None,
          markers: list[dict] | None = None, captures: dict | None = None) -> dict:
    contained = [token for token in tokens if token["start"] < end and token["end"] > start]
    normalized, operations = _normalization(contained, text, start, end)
    kind = "lexical" if role == "lexical" else "morphology" if role == "morphology" else "construction"
    result = {"id": stable_span_id(sentence_id, start, end, "functional:" + key), "sentence_id": sentence_id,
              "start": start, "end": end, "surface": text[start:end], "lemma": lemma or text[start:end],
              "reading": "".join(token.get("reading", "") for token in contained), "kind": kind,
              "unit_role": role, "grammar_ids": grammar_ids or [], "features": features or [],
              "token_ids": [token["id"] for token in contained], "steps": [], "derivation": [], "children": children or [],
              "candidates": candidates or [], "explanation_zh": explanation, "source": "rule", "version": functional_version(),
              "status": "ambiguous" if len(candidates or []) > 1 else "determined", "boundary_status": "supported", "confidence": 1.0,
              "captures": captures or {}, "evidence": evidence or [], "marker_ranges": markers or [{"start": start, "end": end}],
              "normalized_form": normalized, "normalization_steps": operations, "overlap_ids": []}
    return result


def atomic_spans(sentence_id: str, text: str, tokens: list[dict], morph: list[dict]) -> list[dict]:
    result = []
    for index, token in enumerate(tokens):
        if not _content(token):
            continue
        role = _atomic_role(token, tokens[index - 1] if index else None, morph)
        pos = token_pos(token)
        grammar_ids, explanation = [], ""
        if role == "particle":
            class_item = PARTICLE_CLASSES.get(pos[1])
            if class_item:
                identity = "ja.functional.atom.particle." + class_item[0] + "." + _hex(token.get("lemma", token["surface"]))
                grammar_ids = [identity]
                explanation = functional_rule(identity)["explanation_zh"]
        elif role == "auxiliary":
            identity = "ja.functional.atom.auxiliary." + _hex(token_class(token)) + "." + _hex(token.get("lemma", token["surface"]))
            grammar_ids = [identity]
            explanation = functional_rule(identity)["explanation_zh"]
        result.append(_span(sentence_id, text, tokens, token["start"], token["end"], "atom:" + token["id"], role=role,
                            grammar_ids=grammar_ids, explanation=explanation, lemma=token.get("lemma", ""),
                            features=["atomic", *(["functional"] if role != "lexical" else [])],
                            evidence=["original_atomic_token", "pos:" + ",".join(pos)],
                            captures={"pos_class": pos[1], "conjugation_type": token_class(token), "conjugation_form": token_form(token)}))
    return result


@lru_cache(maxsize=256)
def _connection_patterns(code: str) -> tuple[dict, ...]:
    table = functional_data()["connections"]
    code = str(code)[:2]
    if code not in table:
        raise ValueError("Unmapped Tsutsuji connection: " + code)
    record = table[code]
    patterns = [{**pattern, "_allowed_pos": frozenset(tuple(functional_data()["pos_sets"][identity]) for identity in pattern.get("pos_ids", [])),
                 "_allowed_lemma": frozenset(pattern.get("lemma", []))} for pattern in record.get("patterns", [])]
    for reference in record.get("refs", []):
        patterns.extend(_connection_patterns(str(reference)))
    return tuple(patterns)


def connection_matches(code: str, token: dict | None) -> bool:
    """The upstream code 90 alone explicitly permits any left neighbour."""
    if str(code)[:2] == "90":
        return True
    if token is None:
        return False
    actual = tuple(token_pos(token))
    for pattern in _connection_patterns(code):
        if pattern["_allowed_lemma"] and token.get("lemma") not in pattern["_allowed_lemma"] and token.get("normalized_form") not in pattern["_allowed_lemma"]:
            continue
        if "pos_ids" in pattern:
            if actual in pattern["_allowed_pos"]:
                return True
            continue
        if pattern.get("pos_sets"):
            if actual in [tuple(value) for value in pattern["pos_sets"]]:
                return True
            continue
        # The generator can provide a compact pattern for a pinned family.
        if pattern.get("pos") and actual[0] not in pattern["pos"]:
            continue
        if pattern.get("pos2") and actual[1] not in pattern["pos2"]:
            continue
        if pattern.get("form") and not any(token_form(token).startswith(value) for value in pattern["form"]):
            continue
        if pattern.get("ctype") and not any(token_class(token).startswith(value) for value in pattern["ctype"]):
            continue
        if any(pattern.get(key) for key in ("pos", "pos2", "form", "ctype")):
            return True
    return False


def dictionary_spans(sentence_id: str, text: str, tokens: list[dict]) -> list[dict]:
    trie, _ = _resource_index()
    starts = {token["start"]: index for index, token in enumerate(tokens)}
    ends = {token["end"]: index for index, token in enumerate(tokens)}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for start, first in starts.items():
        if not _content(tokens[first]):
            continue
        cursor, position = trie, start
        while position < len(text) and text[position] in cursor:
            cursor = cursor[text[position]]
            position += 1
            if "" not in cursor or position not in ends:
                continue
            last = ends[position]
            if any(not _content(token) for token in tokens[first:last + 1]):
                continue
            previous = tokens[first - 1] if first and tokens[first - 1]["end"] == start else None
            for entry in cursor[""]:
                if not connection_matches(entry["left"], previous):
                    continue
                # A surface inside a single lexical atom (e.g. lexical 付く)
                # is not a functional unit merely because the letters match.
                if first == last and token_pos(tokens[first])[0] in {"名詞", "代名詞", "動詞", "形容詞"} and entry["function"] not in {"N", "M", "D"}:
                    continue
                if first == last:
                    pos = token_pos(tokens[first])
                    # Homographic contracted じゃ may be copular では or
                    # conjunctive ては. Nominalizer ん is not negative ぬ.
                    if entry["function"] == "Q" and token_class(tokens[first]) in COPULA_CLASSES:
                        continue
                    if entry["function"] == "M" and entry["lemma"] == "ぬ" and pos[:2] == ("助詞", "準体助詞"):
                        continue
                    if entry["function"] == "M" and entry["lemma"] == "の" and pos[0] == "助動詞":
                        continue
                groups[(start, position, entry["function"])].append(entry)
    output = []
    for (start, end, function), entries in groups.items():
        semantic_entries = { _semantic_id(entry): entry for entry in entries }
        grammar_ids = sorted(semantic_entries)
        meanings = []
        for identity, entry in sorted(semantic_entries.items()):
            rule = functional_rule(identity)
            meanings.append({"id": identity, "label": rule["explanation_zh"], "features": [entry["mclass"]],
                             "evidence": ["tsutsuji:" + str(entry["path"][2]), "left_connection:" + str(entry["left"])], "status": "possible"})
        role = "ending" if function == "M" and token_pos(tokens[ends[end]])[:2] == ("助詞", "終助詞") else "construction"
        explanations = list(dict.fromkeys(value["label"] for value in meanings))
        output.append(_span(sentence_id, text, tokens, start, end, "tsutsuji:" + function + ":" + ",".join(grammar_ids),
                            role=role, grammar_ids=grammar_ids, lemma=entries[0]["lemma"], features=["functional_expression", "function:" + function],
                            explanation="；".join(explanations), candidates=meanings if len(meanings) > 1 else [],
                            evidence=["token_aligned_dictionary", "left_connections_supported", *["tsutsuji:" + str(item["id"]) for item in entries]],
                            captures={"marker": text[start:end], "grammar_label": entries[0]["lemma"], "function": function,
                                      "source_entry_ids": ",".join(str(item["id"]) for item in entries),
                                      "source_paths": json.dumps([item["path"] for item in entries], ensure_ascii=False),
                                      "right_connections": ",".join(sorted({str(item["right"])[:2] for item in entries}))}))
    return output


def _terminal(token: dict) -> bool:
    return token_form(token).startswith(("終止形", "連体形", "意志推量形", "命令形"))


def auxiliary_attaches(previous: dict, current: dict, head: dict | None = None) -> bool:
    """Typed, form-constrained attachment; never accept a final kana heuristic."""
    previous_pos, current_pos = token_pos(previous), token_pos(current)
    lemma, ctype = current.get("lemma", ""), token_class(current)
    pform = token_form(previous)
    if ctype in COPULA_CLASSES:
        if previous_pos[0] in {"名詞", "代名詞", "形状詞"} or previous_pos[:2] == ("助詞", "準体助詞"):
            return True
        if ctype == "助動詞-デス":
            return previous_pos[0] == "形容詞" and _terminal(previous) or previous.get("lemma") in {"た", "ぬ", "ない", "たい"} and _terminal(previous)
        if current["surface"] in {"なら", "ならば"}:
            return previous_pos[0] == "助詞" and previous["surface"] in {"くらい", "ぐらい", "ほど", "だけ", "ばかり", "まで"}
        return False
    if current_pos[0] not in {"助動詞", "形容詞"}:
        return False
    if lemma == "ない" and current_pos[0] == "形容詞":
        return (previous.get("lemma") == "だ" and pform.startswith("連用形")) or previous_pos[0] in {"形容詞", "形状詞"} and pform.startswith("連用形")
    if lemma in {"れる", "られる", "せる", "させる"}:
        return previous_pos[0] in {"動詞", "助動詞"} and (pform.startswith("未然形") or lemma in {"せる", "させる"} and token_class(previous).startswith(("上一段", "下一段", "カ行変格")) and pform.startswith("連用形"))
    if lemma in {"てる", "でる", "ちゃう", "じゃう", "とく", "どく"}:
        return previous_pos[0] in {"動詞", "助動詞"} and pform.startswith("連用形")
    if ctype in AUX_CONNECTIONS:
        nominal_allowed = ctype in {"助動詞-ラシイ", "文語助動詞-ゴトシ"} and previous_pos[0] in {"名詞", "代名詞", "形状詞"}
        return nominal_allowed or previous_pos[0] in {"動詞", "形容詞", "助動詞"} and pform.startswith(AUX_CONNECTIONS[ctype])
    # Remaining inflecting auxiliary verbs are stem-taking auxiliaries. Voice
    # auxiliaries are handled first, before this productive class connection.
    if ctype.startswith(("下一段", "五段", "文語下二段")):
        return previous_pos[0] in {"動詞", "助動詞"} and pform.startswith("連用形")
    if ctype in {"助動詞-ヤス", "文語助動詞-ンス"}:
        return previous_pos[0] in {"動詞", "助動詞"} and pform.startswith("連用形")
    return False


def _node_type(node: dict, tokens_by_id: dict) -> str:
    if "_node_type" in node:
        return node["_node_type"]
    role = node.get("unit_role")
    if role == "ending":
        return "ending"
    if node["kind"] == "morphology":
        return "predicate"
    ids = node.get("token_ids", [])
    if "atomic" in node.get("features", []) and ids:
        token = tokens_by_id[ids[0]]
        pos = token_pos(token)
        if pos[0] in {"名詞", "代名詞", "形状詞"}:
            return "nominal"
        if pos[0] in {"動詞", "形容詞"} and role == "lexical":
            return "predicate"
        if pos[:2] == ("助詞", "準体助詞"):
            return "nominalizer"
        if pos[:2] == ("助詞", "終助詞"):
            return "ending_atom"
        if pos[0] == "助詞":
            return "particle"
        if role == "auxiliary":
            return "copula" if token_class(token) in COPULA_CLASSES else "auxiliary"
    function = node.get("captures", {}).get("function")
    if function in {"M", "N"} and ids:
        last = tokens_by_id[ids[-1]]
        pos = token_pos(last)
        if pos[:2] == ("助詞", "準体助詞"):
            return "nominalizer"
        if pos[0] in {"名詞", "代名詞", "形状詞"}:
            return "nominal_expression"
        if len(ids) == 1 and token_class(last) in COPULA_CLASSES:
            return "copula"
    return {"P": "case_expression", "Q": "connective_expression", "D": "modifier_expression", "C": "conjunction_expression",
            "M": "auxiliary_expression", "N": "nominal_expression", "T": "focus_expression", "W": "topic_expression"}.get(function, "construction")


def _compose_key(left: dict, right: dict, previous: dict, current: dict, tokens_by_id: dict) -> str | None:
    lt, rt = _node_type(left, tokens_by_id), _node_type(right, tokens_by_id)
    if "atomic" not in right.get("features", []):
        # A recognised nominalizer followed by a complete functional ending
        # (not arbitrary neighbouring constructions) forms one explanation.
        if lt in {"nominalizer", "nominalized"} and rt in {"ending", "auxiliary_expression"}:
            return "nominalized_ending" if rt == "ending" else "nominalized_copula"
        if lt == "predicate" and rt == "nominalizer" and _terminal(previous):
            return "predicate_nominalizer"
        if lt == "predicate" and rt == "nominal_expression" and _terminal(previous):
            return "predicate_nominal_expression"
        if lt == "nominal" and rt == "auxiliary_expression" and "token_aligned_dictionary" in right.get("evidence", []):
            return "nominal_copula"
        if lt == "predicate" and rt in {"ending", "connective_expression", "auxiliary_expression"} and (
            _terminal(previous) or "token_aligned_dictionary" in right.get("evidence", [])
        ):
            return "predicate_ending" if rt == "ending" else "predicate_functional" if rt == "auxiliary_expression" else "predicate_connective"
        return None
    if rt == "nominalizer" and lt == "predicate" and _terminal(previous):
        return "predicate_nominalizer"
    if rt in {"auxiliary", "copula"} and auxiliary_attaches(previous, current):
        if lt in {"nominal", "nominal_expression", "nominalized", "nominalizer"} and token_class(current) in COPULA_CLASSES:
            return "nominalized_copula" if lt in {"nominalized", "nominalizer"} else "nominal_copula"
        if lt in {"predicate", "copula", "copular_focus", "explanation", "auxiliary_expression"}:
            return "copular_negative" if current.get("lemma") == "ない" and previous.get("lemma") == "だ" else "predicate_auxiliary"
        if lt in {"particle", "particle_phrase"} and current["surface"] == "なら" and previous["surface"] in {"くらい", "ぐらい", "ほど", "だけ", "ばかり", "まで"}:
            return "degree_condition"
    if rt == "particle" and token_pos(current)[1] == "接続助詞":
        if current["surface"] in {"て", "で"}:
            return "predicate_te" if lt == "predicate" and token_form(previous).startswith("連用形") else None
        if current["surface"] == "ば":
            return "predicate_connective" if lt == "predicate" and token_form(previous).startswith("仮定形") else None
        if current["surface"] in {"ながら", "つつ", "たり", "だり"}:
            return "predicate_connective" if lt == "predicate" and token_form(previous).startswith("連用形") else None
        if lt == "predicate" and _terminal(previous):
            return "predicate_connective"
    if rt == "ending_atom" and lt in {"predicate", "nominal", "nominal_expression", "nominalized", "copula", "negative", "ending", "explanation"}:
        if lt in {"predicate", "copula", "negative"} and not _terminal(previous):
            return None
        return "nominalized_ending" if lt == "explanation" else "predicate_ending"
    if rt == "lexical" or rt in {"predicate", "auxiliary"}:
        if lt == "te_predicate" and current.get("lemma") in TE_AUX_LEMMAS and token_pos(current)[1] == "非自立可能":
            return "te_auxiliary"
    if rt == "particle" and lt in {"particle", "particle_phrase", "case_expression", "focus_expression", "topic_expression"}:
        current_sub = token_pos(current)[1]
        if current_sub in {"係助詞", "副助詞"} or current_sub == "格助詞" and token_pos(previous)[1] in {"副助詞", "準体助詞"}:
            return "particle_sequence"
    if rt == "particle" and lt in {"nominal", "nominal_expression"} and token_pos(current)[1] in {"格助詞", "副助詞", "係助詞"}:
        return "nominal_particle"
    # Copula + は/も + negative is a constrained three-component sequence.
    if rt == "particle" and lt == "copula" and previous.get("lemma") == "だ" and current["surface"] in {"は", "も"}:
        return "particle_sequence"
    if rt == "predicate" and lt == "copular_focus" and current.get("lemma") == "ない":
        return "copular_negative"
    return None


def composition_spans(sentence_id: str, text: str, tokens: list[dict], nodes: list[dict]) -> list[dict]:
    """Bottom-up adjacent-unit chart, bounded by the inherited chain limit.

    The width limit only constrains newly composed chains, never atomic or
    published dictionary units. Equivalent derivations share one node whose
    children retain each route; distinct grammar functions remain separate.
    """
    tokens_by_id = {token["id"]: token for token in tokens}
    starts, ends = {token["start"]: i for i, token in enumerate(tokens)}, {token["end"]: i for i, token in enumerate(tokens)}
    by_start: dict[int, list[dict]] = defaultdict(list)
    by_end: dict[int, list[dict]] = defaultdict(list)
    for node in nodes:
        by_start[node["start"]].append(node)
        by_end[node["end"]].append(node)
    queue, seen, output, tried = deque(nodes), {}, [], set()
    while queue:
        item = queue.popleft()
        pairs = [(item, right) for right in tuple(by_start.get(item["end"], []))]
        pairs.extend((left, item) for left in tuple(by_end.get(item["start"], [])))
        for left, right in pairs:
            pair = (left["id"], right["id"])
            if pair in tried:
                continue
            tried.add(pair)
            _compose_pair(sentence_id, text, tokens, tokens_by_id, starts, ends, left, right, seen, output, by_start, by_end, queue)
    return output


def _compose_pair(sentence_id, text, tokens, tokens_by_id, starts, ends, left, right, seen, output, by_start, by_end, queue):
        last_index = ends.get(left["end"])
        if last_index is None:
            return
        previous = tokens[last_index]
        first_index, right_index = starts.get(right["start"]), ends.get(right["end"])
        if first_index is None or right_index is None or right_index - starts.get(left["start"], right_index) >= 64:
            return
        current = tokens[first_index]
        key = _compose_key(left, right, previous, current, tokens_by_id)
        if key is None:
            return
        signature = (left["start"], right["end"], key)
        if signature in seen:
            node = seen[signature]
            node["children"] = list(dict.fromkeys([*node["children"], left["id"], right["id"]]))
            return
        label, explanation, role = COMPOSITIONS[key]
        identity = "ja.functional.composition." + key
        if key == "te_auxiliary":
            explanation = TE_AUX_TEXT.get(current.get("normalized_form"), TE_AUX_TEXT.get(current.get("lemma"), explanation))
        grammar_ids = list(dict.fromkeys([identity, *left.get("grammar_ids", []), *right.get("grammar_ids", [])]))
            # Refer to the compound's own rule and exact child units. Atomic
            # rules are not offered as synonyms for the complete expression.
        grammar_ids = [identity, *[value for value in grammar_ids if value.startswith("ja.functional.tsutsuji.")]]
        node = _span(sentence_id, text, tokens, left["start"], right["end"], "compose:" + key, role=role,
                         grammar_ids=grammar_ids, explanation=explanation, lemma=left.get("lemma", ""),
                         features=["composed", key], children=[left["id"], right["id"]],
                         candidates=deepcopy(right.get("candidates", []) or left.get("candidates", [])),
                         evidence=["typed_connection:" + key, "left:" + _node_type(left, tokens_by_id), "right:" + _node_type(right, tokens_by_id)],
                         captures={"grammar_label": label, "marker": text[left["start"]:right["end"]]},
                         markers=[{"start": left["start"], "end": right["end"]}])
        if role == "morphology":
            head = tokens_by_id[left["token_ids"][0]]
            node["reading"] = (left.get("reading", "") if left.get("unit_role") == "morphology"
                               else head.get("lemma_reading") or
                               (head.get("reading", "") if head["surface"] == node["lemma"] else ""))
        node_type = {"predicate_auxiliary": "predicate", "predicate_te": "te_predicate", "te_auxiliary": "predicate", "predicate_functional": "predicate",
                         "nominal_copula": "copula", "copular_negative": "negative", "predicate_nominalizer": "nominalized",
                         "predicate_nominal_expression": "nominalized",
                         "nominalized_copula": "explanation", "predicate_ending": "ending", "nominalized_ending": "ending",
                         "particle_sequence": "copular_focus" if _node_type(left, tokens_by_id) == "copula" else "particle_phrase",
                         "degree_condition": "conditional", "nominal_particle": "particle_phrase", "predicate_connective": "connective"}[key]
            # A negative adjective can still take past/conditional auxiliaries.
        if node_type == "negative":
            node_type = "predicate"
        node["_node_type"] = node_type
        seen[signature] = node
        output.append(node)
        by_start[node["start"]].append(node)
        by_end[node["end"]].append(node)
        queue.append(node)


def _decorate_existing(text: str, tokens: list[dict], spans: list[dict]) -> list[dict]:
    result = deepcopy(spans)
    for span in result:
        span["unit_role"] = "morphology" if span["kind"] == "morphology" else "construction"
        marker = span.get("captures", {}).get("marker", "")
        marker_start = text.find(marker, span["start"], span["end"]) if marker else -1
        span["marker_ranges"] = [{"start": marker_start, "end": marker_start + len(marker)}] if marker_start >= 0 else [{"start": span["start"], "end": span["end"]}]
        span["normalized_form"], span["normalization_steps"] = _normalization(tokens, text, span["start"], span["end"])
        span["boundary_status"] = "unknown" if span["status"] == "unknown" else "supported"
        span["confidence"] = 0.5 if span["status"] == "unknown" else 1.0
        span["overlap_ids"] = []
    return result


def _link_graph(nodes: list[dict]) -> list[dict]:
    """Keep direct contained units and crossing edges, excluding equal ranges.

    A sweep limits comparisons to overlapping units. Equal-range semantic
    alternatives are not children of one another, so the graph is acyclic.
    """
    # Transport IDs include the sentence identity. Stable semantic ordering
    # keeps equivalent analyses in the same cache slots after book migration.
    # Original inflection analyses precede generic compositions of the same
    # range, preserving their canonical reading and detailed feature chain.
    ordered = sorted(nodes, key=lambda item: (
        item["start"], -item["end"], "composed" in item.get("features", []),
        item["kind"], item.get("unit_role", ""), tuple(item.get("grammar_ids", [])),
        tuple(item.get("features", [])), item.get("lemma", ""), item["id"]))
    active = []
    ids = {node["id"] for node in nodes}
    for node in ordered:
        node["children"] = [value for value in node.get("children", []) if value in ids and value != node["id"]]
        active = [other for other in active if other["end"] > node["start"]]
        parents = []
        for other in active:
            same = (other["start"], other["end"]) == (node["start"], node["end"])
            if same:
                other["overlap_ids"].append(node["id"])
                node["overlap_ids"].append(other["id"])
            elif other["start"] <= node["start"] and node["end"] <= other["end"]:
                parents.append(other)
            else:
                other["overlap_ids"].append(node["id"])
                node["overlap_ids"].append(other["id"])
        # Nearest containing range(s) are enough; all descendants are reachable.
        if parents:
            width = min(other["end"] - other["start"] for other in parents)
            for parent in parents:
                if parent["end"] - parent["start"] == width:
                    parent["children"].append(node["id"])
        active.append(node)
    for node in ordered:
        node["children"] = list(dict.fromkeys(node["children"]))
        node["overlap_ids"] = list(dict.fromkeys(node["overlap_ids"]))
        node.pop("_node_type", None)
    return ordered


def build_functional_graph(sentence_id: str, text: str, tokens: list[dict], morph_spans: list[dict], grammar_spans: list[dict]) -> list[dict]:
    validate_tokens(text, tokens)
    existing = _decorate_existing(text, tokens, [*morph_spans, *grammar_spans])
    atoms = atomic_spans(sentence_id, text, tokens, morph_spans)
    dictionary = dictionary_spans(sentence_id, text, tokens)
    nodes = [*atoms, *existing, *dictionary]
    nodes.extend(composition_spans(sentence_id, text, tokens, nodes))
    return _link_graph(nodes)
