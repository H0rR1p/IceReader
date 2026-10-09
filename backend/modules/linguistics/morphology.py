"""Build non-destructive learning spans from Sudachi's actual inflections.

The original morphemes, their identities and offsets are never rewritten.
Auxiliary-chain recognition is deliberately conservative: unresolved voice
and damaged / legacy morphology are displayed as candidates or unknown.
"""
from __future__ import annotations

from functools import lru_cache
import re

from .rules import morphology_rules, rule_version, stable_span_id, token_class, token_form, token_pos, validate_tokens


def _has_form(token: dict, *forms: str) -> bool:
    return any(token_form(token).startswith(form) for form in forms)


def _base_feature(token: dict) -> str:
    form = token_form(token)
    for prefix, feature in (("意志推量", "volitional"), ("命令", "imperative"), ("仮定", "conditional"),
                            ("未然", "irrealis"), ("連用", "stem"), ("連体", "attributive"), ("終止", "dictionary"), ("語幹", "adjective_stem")):
        if form.startswith(prefix):
            return feature
    return "dictionary" if token_pos(token)[0] in {"名詞", "形状詞"} else "unknown"


@lru_cache(maxsize=256)
def _verified_godan(lemma: str, row: str) -> dict | None:
    """A proposed short-form restoration must exist with the exact class."""
    from ...nlp import tokenize_sentence
    tokens = tokenize_sentence("morphology-dictionary-probe", lemma)
    if len(tokens) != 1:
        return None
    token = tokens[0]
    if token["surface"] != lemma or token.get("lemma") != lemma or token_pos(token)[0] != "動詞":
        return None
    return token if token_class(token) == f"五段-{row}行" else None


def _short_causative_base(base: dict, following: dict | None) -> dict | None:
    if not following or following.get("lemma") != "れる" or token_pos(following)[0] != "助動詞":
        return None
    if token_class(base) != "五段-サ行" or not _has_form(base, "未然形"):
        return None
    lemma = base.get("lemma", "")
    if not lemma.endswith("す") or len(lemma) < 3:
        return None
    # 読ます -> 読ま -> 読む; each result must be a real matching dictionary
    # form. This does not classify arbitrary final る as ichidan / godan.
    stem = lemma[:-1]
    rows = {"か": ("く", "カ"), "が": ("ぐ", "ガ"), "た": ("つ", "タ"), "な": ("ぬ", "ナ"),
            "ば": ("ぶ", "バ"), "ま": ("む", "マ"), "ら": ("る", "ラ"), "わ": ("う", "ワア")}
    if stem[-1:] not in rows:
        return None
    ending, row = rows[stem[-1]]
    return _verified_godan(stem[:-1] + ending, row)


def _potential_base(base: dict) -> dict | None:
    """Use normalized dictionary evidence, never final-る heuristics."""
    normalized = base.get("normalized_form", "")
    lemma = base.get("lemma", "")
    if not normalized or normalized == lemma or not token_class(base).startswith(("上一段", "下一段")):
        return None
    rows = {"う": ("え", "ワア"), "く": ("け", "カ"), "ぐ": ("げ", "ガ"), "す": ("せ", "サ"),
            "つ": ("て", "タ"), "ぬ": ("ね", "ナ"), "ぶ": ("べ", "バ"), "む": ("め", "マ"), "る": ("れ", "ラ")}
    if normalized[-1:] not in rows:
        return None
    ending, row = rows[normalized[-1]]
    if lemma != normalized[:-1] + ending + "る":
        return None
    return _verified_godan(normalized, row)


@lru_cache(maxsize=256)
def _verified_honorific_stem(surface: str) -> dict | None:
    """Sudachi sometimes tags 読み in お読みになる as a noun.

    Require the same complete stem in a valid ます expression, rather than
    guessing a dictionary form from the final kana of an arbitrary noun.
    """
    from ...nlp import tokenize_sentence
    probe = tokenize_sentence("honorific-stem-probe", surface + "ます")
    if len(probe) != 2 or probe[0]["surface"] != surface or probe[1].get("lemma") != "ます":
        return None
    return probe[0] if token_pos(probe[0])[0] == "動詞" and _has_form(probe[0], "連用形") else None


def _honorific_head(text: str, tokens: list[dict], index: int) -> tuple[dict, int, str] | None:
    prefix = tokens[index]
    exceptions = morphology_rules().get("honorific_heads", {})
    standalone = prefix["surface"] in exceptions
    if not standalone and (token_pos(prefix)[0] != "接頭辞" or prefix["surface"] not in {"お", "ご", "御"}):
        return None
    if index + 2 >= len(tokens):
        return None
    head, marker = (prefix, tokens[index + 1]) if standalone else tokens[index + 1:index + 3]
    if (not standalone and prefix["end"] != head["start"]) or head["end"] != marker["start"]:
        return None
    head_pos = token_pos(head)
    exceptional = exceptions.get(text[prefix["start"]:head["end"]])
    if exceptional:
        lexical = {**head, "lemma": exceptional["lemma"], "lemma_reading": exceptional["reading"]}
    elif head_pos[0] == "動詞" and _has_form(head, "連用形"):
        lexical = head
    elif head_pos[0] == "名詞" and "サ変可能" in head_pos:
        lexical = {**head, "lemma": head["lemma"] + "する",
                   "lemma_reading": (head.get("lemma_reading") or head.get("reading", "")) + "スル"}
    elif head_pos[0] == "名詞":
        lexical = _verified_honorific_stem(head["surface"])
        if not lexical:
            return None
    else:
        return None
    end_index = index + (1 if standalone else 2)
    feature = None
    noun_change = False
    if marker["surface"] == "に" and token_pos(marker)[:2] == ("助詞", "格助詞"):
        if end_index + 1 >= len(tokens):
            return None
        marker = tokens[end_index + 1]
        if tokens[end_index]["end"] != marker["start"] or marker.get("lemma") != "なる":
            return None
        end_index += 1
        feature = "honorific"
        if head_pos[0] == "名詞" and "サ変可能" in head_pos and not exceptional:
            if head.get("lemma") not in morphology_rules().get("honorific_verbal_nouns", []):
                return None
        noun_change = head_pos[0] == "名詞" and not standalone
    elif marker.get("lemma") in {"くださる", "下さる"}:
        feature = "respectful_benefactive"
    elif marker.get("lemma") in {"いただく", "頂く"}:
        feature = "humble_benefactive"
    elif marker.get("lemma") in {"する", "いたす", "致す"}:
        feature = "humble"
    if not feature or token_pos(marker)[0] != "動詞":
        return None
    # This virtual head is only an analysis view. Stored atomic tokens and
    # their original offsets/identities are never replaced.
    return {**marker, "id": prefix["id"], "start": prefix["start"],
            "surface": text[prefix["start"]:marker["end"]],
            "lemma": lexical["lemma"], "normalized_form": lexical["lemma"],
            "lemma_reading": lexical.get("lemma_reading") or lexical.get("reading", ""),
            "_nominal_change": head["surface"] if noun_change else "",
            "_honorific_complete": text[prefix["start"]:marker["start"]] + marker["lemma"]}, end_index + 1, feature


def _can_attach(previous: dict, current: dict, feature: str, features: list[str]) -> bool:
    prev_pos = token_pos(previous)[0]
    if feature == "copula":
        return prev_pos in {"名詞", "形状詞"} or previous.get("lemma") == "ない" and "copula" in features
    if feature == "polite":
        if current.get("lemma") == "です":
            return prev_pos in {"名詞", "形状詞", "形容詞"} or previous.get("lemma") in {"ぬ", "ない", "たい", "た"}
        return _has_form(previous, "連用形") and prev_pos in {"動詞", "助動詞"}
    if feature == "negative":
        # The adverbial 連用 of an adjective / たい can connect to ない, but
        # a godan dictionary form cannot: 読むない is not a valid chain.
        return (_has_form(previous, "未然形") and prev_pos in {"動詞", "助動詞"}) or (
            _has_form(previous, "連用形") and (prev_pos in {"形容詞", "形状詞"} or previous.get("lemma") in {"たい", "だ"})
        )
    if feature == "causative":
        if prev_pos != "動詞":
            return False
        return _has_form(previous, "未然形") or (
            token_class(previous).startswith(("上一段", "下一段", "カ行変格")) and _has_form(previous, "連用形")
        )
    if feature == "voice_ambiguous":
        return prev_pos in {"動詞", "助動詞"} and _has_form(previous, "未然形")
    forms = morphology_rules()["connections"].get(feature, [])
    return bool(forms) and _has_form(previous, *forms) and prev_pos in {"動詞", "助動詞", "形容詞"}


def _voice_candidates(features: list[str], evidence: list[str], base: dict) -> list[dict]:
    if "short_causative_passive" in features:
        return [{"id": "causative_passive", "label": "被要求做（使役被动的简短说法）", "features": ["causative", "passive"],
                 "evidence": evidence, "status": "supported"}]
    if "voice_ambiguous" not in features:
        return []
    labels = [("passive", "被别人这样做（被动）"), ("honorific", "尊敬地说别人的动作"), ("spontaneous", "不由自主地这样做")]
    # 五段の未然形+れる is not the modern potential (書ける, 読める).
    # する also uses できる; ichidan and 来る genuinely share られる.
    if token_class(base).startswith(("上一段", "下一段", "カ行変格")):
        labels.insert(1, ("potential", "能够这样做（能力）"))
    if "causative" in features:
        labels = [("causative_passive", "被要求做（使役被动）"), ("causative_potential", "能够让别人做"), ("honorific", "尊敬地说别人的动作")]
    return [{"id": identity, "label": label, "features": [identity], "evidence": evidence,
             "status": "possible"} for identity, label in labels]


def _nominal_negative_connector(tokens: list[dict], index: int) -> bool:
    tail = tokens[index + 1:index + 7]
    if len(tail) < 3 or tail[0]["surface"] != "で" or tail[1]["surface"] not in {"は", "も"}:
        return False
    if token_pos(tail[0])[:2] != ("助詞", "格助詞") or token_pos(tail[1])[0] != "助詞":
        return False
    if tail[2].get("lemma") == "ない" and token_pos(tail[2])[0] == "形容詞":
        return True
    return (len(tail) >= 5 and tail[2].get("lemma") == "ある"
            and tail[3].get("lemma") == "ます" and _has_form(tail[3], "未然形")
            and tail[4].get("lemma") == "ぬ")


def _soft_line_projection(sentence_id: str, text: str, tokens: list[dict]) -> list[dict]:
    """Analyse a shadow string, keeping all stored atom identities untouched.

    A single EPUB layout newline can make Sudachi choose 読み as a noun.
    The shadow analysis removes only such soft newlines, then maps every
    character back. Blank lines and punctuation-adjacent newlines remain.
    """
    removable = {index for index, value in enumerate(text) if value == "\n" and 0 < index < len(text) - 1
                 and re.search(r"[一-龯ぁ-ゖァ-ヺ]", text[index - 1]) and re.search(r"[一-龯ぁ-ゖァ-ヺ]", text[index + 1])}
    if not removable:
        return tokens
    from ...nlp import tokenize_sentence
    offsets = [index for index in range(len(text)) if index not in removable]
    compact = "".join(text[index] for index in offsets)
    shadow = tokenize_sentence(sentence_id + ":soft-layout-shadow", compact)
    for token in shadow:
        start, end = offsets[token["start"]], offsets[token["end"] - 1] + 1
        original = [item for item in tokens if item["start"] < end and item["end"] > start]
        token["id"] = original[0]["id"]
        token["start"], token["end"], token["surface"] = start, end, text[start:end]
        token["_projected"] = True
    return shadow


def _derivation(text: str, chain: list[dict], steps: list[dict], lemma: str, features: list[str]) -> list[str]:
    """Canonical complete forms at each valid auxiliary step, not kana bits."""
    result = [lemma]
    if chain[0].get("_honorific_complete"):
        result.append(chain[0]["_honorific_complete"])
    start = chain[0]["start"]
    if "potential" in features and chain[0].get("lemma") not in result:
        result.append(chain[0]["lemma"])
    if "short_causative_passive" in features:
        a_row = {"う": "わ", "く": "か", "ぐ": "が", "つ": "た", "ぬ": "な", "ぶ": "ば", "む": "ま", "る": "ら"}
        if lemma[-1:] in a_row:
            causative = lemma[:-1] + a_row[lemma[-1]] + "せる"
            result.extend([causative, causative[:-1] + "られる"])
    attached_steps = steps[-(len(chain) - 1):] if len(chain) > 1 else []
    for token, step in zip(chain[1:], attached_steps):
        feature = step["feature"]
        prefix = text[start:token["start"]].replace("\n", "")
        surface = token["surface"].replace("\n", "")
        suffix = token.get("lemma", surface)
        if feature == "copula" and surface in {"は", "も"}:
            continue
        if feature == "short_causative_passive" and surface == "さ":
            continue
        if feature == "polite" and suffix == "です" and result[-1].endswith("ません"):
            # ません + でした is one polite past-negative extension; the
            # intermediate *ませんです is not a complete standard form.
            continue
        if feature in {"past", "te_form"} or feature == "conditional" and token_pos(token)[0] == "助詞" or feature == "negative" and surface == "ん":
            suffix = surface
        if feature == "contracted_completion":
            expanded = prefix + ("でしまう" if suffix == "じゃう" else "てしまう")
            if expanded not in result:
                result.append(expanded)
        elif feature == "contracted_progressive_resultative":
            expanded = prefix + ("でいる" if suffix == "でる" else "ている")
            if expanded not in result:
                result.append(expanded)
        complete = prefix + suffix
        if complete not in result:
            result.append(complete)
    actual = text[start:chain[-1]["end"]].replace("\n", "")
    if actual not in result:
        result.append(actual)
    return result


def analyze_morphology(sentence_id: str, text: str, tokens: list[dict]) -> list[dict]:
    """Recognize full inflected predicates while retaining every atom.

    Offsets are Python Unicode codepoints in the original sentence. Missing
    attributes do not prevent reading old data: their spans become unknown.
    """
    validate_tokens(text, tokens)
    original_tokens = tokens
    tokens = _soft_line_projection(sentence_id, text, tokens)
    rules = morphology_rules()
    output: list[dict] = []
    index = 0
    while index < len(tokens):
        honorific = _honorific_head(text, tokens, index)
        base = honorific[0] if honorific else tokens[index]
        pos = token_pos(base)
        if pos[0] not in {"動詞", "形容詞", "形状詞", "名詞"}:
            index += 1
            continue
        nominal_negative = pos[0] in {"名詞", "形状詞"} and _nominal_negative_connector(tokens, index)
        if pos[0] == "名詞" and not nominal_negative and (index + 1 >= len(tokens) or tokens[index + 1].get("lemma") not in {"だ", "です", "する"}):
            index += 1
            continue
        chain = [base]
        features = [_base_feature(base)]
        if honorific:
            features.append(honorific[2])
        else:
            respectful = rules.get("respectful_verbs", {})
            humble = rules.get("humble_verbs", {})
            if base.get("lemma") in respectful:
                features.append("honorific")
            elif base.get("lemma") in humble:
                features.append("humble")
        evidence = [f"{base.get('id', '')}:POS={','.join(pos)}"]
        lemma = base.get("lemma", base["surface"])
        reading = base.get("lemma_reading") or ""
        restored = _short_causative_base(base, tokens[index + 1] if index + 1 < len(tokens) else None)
        potential = _potential_base(base)
        if potential:
            lemma = potential["lemma"]
            reading = potential.get("lemma_reading") or potential.get("reading", "")
            features.append("potential")
            evidence.append(f"normalized_dictionary_verified:{lemma}:{token_class(potential)}")
        if restored:
            # 励ます/励む and 明かす/明く both exist. Existence alone cannot
            # prove a short causative. Keep Sudachi's lexical head and offer
            # the restoration as a separate, explicitly unselected analysis.
            evidence.append(f"short_causative_candidate:{restored['lemma']}:{token_class(restored)}")
        steps = [{"surface": base["surface"], "lemma": lemma, "feature": features[0],
                  "explanation_zh": rules["features"][features[0]]}]
        for feature in features[1:]:
            steps.append({"surface": base["surface"], "lemma": lemma, "feature": feature,
                          "explanation_zh": rules["features"][feature]})
        if potential:
            steps.append({"surface": base["surface"], "lemma": lemma, "feature": "potential",
                          "explanation_zh": rules["features"]["potential"]})
        cursor = honorific[1] if honorific else index + 1
        while cursor < len(tokens) and len(chain) < rules["max_chain_tokens"]:
            current = tokens[cursor]
            previous = chain[-1]
            if previous["end"] != current["start"] and text[previous["end"]:current["start"]] != "\n":
                break
            current_pos = token_pos(current)
            current_lemma = current.get("lemma", "")
            feature = None
            if nominal_negative and cursor == index + 1:
                feature = "copula"
            elif nominal_negative and cursor == index + 2:
                feature = "copula"
            elif nominal_negative and cursor == index + 3 and current_lemma == "ない":
                feature = "negative"
            elif nominal_negative and cursor == index + 3 and current_lemma == "ある":
                feature = "copula"
            elif current_pos[0] == "助動詞":
                candidate = "past" if token_class(current) == "助動詞-タ" else rules["auxiliaries"].get(current_lemma)
                if candidate and _can_attach(previous, current, candidate, features):
                    feature = candidate
            elif current_lemma == "ない" and current_pos[0] == "形容詞" and _can_attach(previous, current, "negative", features):
                feature = "negative"
            elif current["surface"] in {"て", "で"} and current_pos[:2] == ("助詞", "接続助詞") and _has_form(previous, "連用形"):
                feature = "te_form"
            elif current["surface"] == "ば" and current_pos[:2] == ("助詞", "接続助詞") and _has_form(previous, "仮定形"):
                feature = "conditional"
            elif current["surface"] in {"は", "も"} and previous.get("lemma") == "だ" and "copula" in features:
                # Only keep copular negative when the following ない exists;
                # never swallow a topic particle and the next predicate.
                if cursor + 1 < len(tokens) and tokens[cursor + 1].get("lemma") == "ない":
                    feature = "copula"
            elif current_lemma == "ない" and previous["surface"] in {"は", "も"} and "copula" in features:
                feature = "negative"
            elif current_pos[0] == "動詞" and current_pos[1] == "非自立可能":
                if "te_form" in features and previous["surface"] in {"て", "で"}:
                    feature = rules["te_auxiliaries"].get(current_lemma)
                elif current_lemma == "する" and pos[0] == "名詞" and "サ変可能" in pos and len(chain) == 1:
                    feature = _base_feature(current)
                    lemma = base.get("lemma", base["surface"]) + "する"
                    reading = (base.get("lemma_reading") or base.get("reading", "")) + "スル"
                elif current["surface"] == "さ" and current_lemma == "する" and token_class(previous).startswith("五段") and _has_form(previous, "未然形"):
                    if cursor + 1 < len(tokens) and tokens[cursor + 1].get("lemma") == "れる":
                        feature = "short_causative_passive"
                        if "causative" not in features:
                            features.append("causative")
                elif current_lemma == "ある" and (pos[0] == "形容詞" or previous.get("lemma") == "たい") and _has_form(previous, "連用形"):
                    # おいしくありません: adjectival polite negative.
                    feature = "copula"
            elif current["surface"] == "そう" and current_pos[:2] == ("形状詞", "助動詞語幹") and (
                _has_form(previous, "連用形") or token_pos(previous)[0] == "形容詞" and _has_form(previous, "語幹")
            ):
                feature = "appearance"
            if not feature:
                break
            chain.append(current)
            if feature not in features:
                features.append(feature)
            if feature == "polite" and current_lemma == "です" and pos[0] in {"名詞", "形状詞"} and "copula" not in features:
                features.append("copula")
            steps.append({"surface": current["surface"], "lemma": current_lemma, "feature": feature,
                          "explanation_zh": rules["features"][feature]})
            evidence.append(f"{current.get('id', '')}:POS={','.join(current_pos)}")
            cursor += 1
        if pos[0] in {"名詞", "形状詞"} and len(chain) == 1:
            index += 1
            continue
        start, end = chain[0]["start"], chain[-1]["end"]
        candidates = _voice_candidates(features, evidence, base)
        derivation = _derivation(text, chain, steps, lemma, features)
        if base.get("_nominal_change") and not candidates:
            noun_phrase = base["_nominal_change"] + "になる"
            candidates = [
                {"id": "honorific", "label": "尊敬地说这个动作", "features": features,
                 "lemma": lemma, "reading": reading, "derivation": derivation,
                 "evidence": ["verified_honorific_stem"], "status": "possible"},
                {"id": "nominal_change", "label": "变成这种情况，例如变成休息日", "features": ["nominal_change"],
                 "lemma": noun_phrase, "derivation": [noun_phrase, text[start:end]],
                 "evidence": ["original_head_is_noun"], "status": "possible"},
            ]
        if restored and "voice_ambiguous" in features:
            for candidate in candidates:
                candidate.update(lemma=lemma, reading=reading, derivation=derivation)
            candidates.append({"id": "causative_passive", "label": f"被要求做：{restored['lemma']}（简短说法）",
                               "features": ["causative", "passive", "short_causative_passive"],
                               "evidence": evidence, "status": "possible", "lemma": restored["lemma"],
                               "reading": restored.get("lemma_reading") or restored.get("reading", ""),
                               "derivation": _derivation(text, chain, steps, restored["lemma"],
                                                          [*features, "short_causative_passive"])})
            candidates[0]["label"] = f"被别人这样做：{lemma}"
        output.append({
            "id": stable_span_id(sentence_id, start, end, "morphology"), "sentence_id": sentence_id,
            "start": start, "end": end, "surface": text[start:end], "lemma": lemma, "reading": reading,
            "kind": "morphology", "grammar_ids": [], "features": features,
            "token_ids": [item["id"] for item in original_tokens if start <= item["start"] and item["end"] <= end], "steps": steps, "candidates": candidates,
            "explanation_zh": "；".join(rules["features"][item] for item in features if item not in {"stem", "irrealis", "attributive", "te_form"}
                                       and (item != "dictionary" or len(features) == 1)),
            "children": [], "source": "rule", "version": rule_version(),
            "status": "unknown" if "unknown" in features else "ambiguous" if len(candidates) > 1 else "determined",
            "captures": {"predicate": text[start:end], "dictionary_form": lemma},
            "evidence": evidence + (["soft_line_shadow_projection"] if any(item.get("_projected") for item in chain) else []),
            "derivation": derivation,
        })
        index = cursor
    return output
