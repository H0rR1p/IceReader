"""Token-anchored construction graph, with overlaps and original captures.

Literal markers alone never suffice. Matches must align with morpheme
boundaries and satisfy their predicate / nominal and conjugation anchors.
The graph describes teaching candidates; it does not invent dependencies
or claim uniquely determined semantics for a polysemous construction.
"""
from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from functools import lru_cache

from .rules import get_rule_catalog, rule_version, stable_span_id, token_class, token_form, token_pos, validate_tokens


@lru_cache(maxsize=1)
def _patterns():
    return [(rule, re.compile(rule["pattern"])) for rule in get_rule_catalog()]


def _predicate(token: dict | None) -> bool:
    return bool(token) and token_pos(token)[0] in {"動詞", "形容詞", "形状詞", "助動詞"}


def _anchor(rule: dict, matched: list[dict], previous: dict | None) -> bool:
    kind = rule["anchor"]
    if rule.get("required_pos") and not any(token_pos(item)[0] in rule["required_pos"] for item in matched):
        return False
    if rule.get("preceding_form") and (not previous or not any(token_form(previous).startswith(form) for form in rule["preceding_form"])):
        return False
    if rule.get("repeated_predicate"):
        repeated = [item for item in matched if token_pos(item)[0] in {"動詞", "形容詞"}]
        if not previous or not repeated or repeated[-1].get("lemma") != previous.get("lemma"):
            return False
    if kind == "fixed":
        first = matched[0]
        if rule.get("first_token_lemma") and first.get("lemma") != rule["first_token_lemma"]:
            return False
        if rule.get("first_token_pos") and token_pos(first)[0] != rule["first_token_pos"]:
            return False
        return not any(value in token_pos(first) for value in rule.get("excluded_first_subpos", []))
    if not previous or previous["end"] != matched[0]["start"]:
        return False
    if kind == "te_predicate":
        return _predicate(previous) and token_form(previous).startswith("連用形") and token_pos(matched[0])[:2] == ("助詞", "接続助詞")
    if kind == "predicate":
        # A topic particle is not a predicate anchor. Restricting to actual
        # morphology avoids 名詞から (origin) being shown as causal から.
        return _predicate(previous)
    if kind == "adjective":
        return token_pos(previous)[0] in {"形容詞", "形状詞"}
    if kind == "nominal":
        return token_pos(previous)[0] in {"名詞", "代名詞"}
    return token_pos(previous)[0] in {"名詞", "代名詞", "動詞", "形容詞", "形状詞", "助動詞"}


def match_grammar(sentence_id: str, text: str, tokens: list[dict], morph_spans: list[dict]) -> list[dict]:
    validate_tokens(text, tokens)
    starts = {item["start"]: index for index, item in enumerate(tokens)}
    ends = {item["end"]: index for index, item in enumerate(tokens)}
    morphology_by_token = {token_id: span for span in morph_spans for token_id in span.get("token_ids", [])}
    ordered_morphology = sorted(morph_spans, key=lambda item: item["start"])
    morphology_starts = [item["start"] for item in ordered_morphology]
    output: list[dict] = []
    for rule, pattern in _patterns():
        for match in pattern.finditer(text):
            if match.start() not in starts or match.end() not in ends:
                continue
            first, last = starts[match.start()], ends[match.end()]
            matched = tokens[first:last + 1]
            if not matched or any(item["surface"] in {"。", "！", "？", "!", "?", "\n"} for item in matched):
                continue
            previous = tokens[first - 1] if first else None
            following = tokens[last + 1] if last + 1 < len(tokens) else None
            if following and following.get("lemma") in rule.get("following_lemma_excludes", []):
                continue
            if not _anchor(rule, matched, previous):
                continue
            start, end = match.start(), match.end()
            children: list[str] = []
            if rule["anchor"] != "fixed" and previous:
                start = previous["start"]
            # Keep all overlapping analyses, including the complete inflected
            # predicate which can extend past the literal marker (〜ている
            # plus politeness / past). Never take atoms from another sentence.
            anchor_span = morphology_by_token.get(previous.get("id")) if previous and rule["anchor"] != "fixed" else None
            lemma = anchor_span.get("lemma", "") if anchor_span else previous.get("lemma", "") if previous else ""
            if anchor_span:
                start = anchor_span["start"]
                end = max(end, anchor_span["end"])
            for span in ordered_morphology[bisect_left(morphology_starts, start):bisect_right(morphology_starts, end)]:
                if span["end"] <= end:
                    children.append(span["id"])
            selected = tokens[starts[start]:ends[end] + 1]
            if not selected or any(token_pos(item)[0] in {"補助記号", "記号", "空白"} for item in selected):
                continue
            polysemous = rule["id"] in {"ja.tame_ni", "ja.noni", "ja.to_condition", "ja.to_iu", "ja.nagara", "ja.koto_wa_nai", "ja.ta_koto_ga_aru"}
            candidates = []
            if polysemous:
                meanings = {
                    "ja.tame_ni": [("purpose", "目的"), ("reason", "原因")],
                    # の + に also follows a nominalized clause as an
                    # ordinary case phrase (e.g. いるのに気づく). Sudachi
                    # can assign the same POS pair to concessive のに,
                    # so morphology alone must retain all three readings.
                    "ja.noni": [("concessive", "虽然这样，却……"), ("purpose", "用来做某事"),
                                ("nominalized_case", "把前面的事作为注意、发现等动作的对象")],
                    "ja.to_condition": [("conditional", "一这样做，就……"), ("quotation", "引用别人说的话或想法")],
                    "ja.to_iu": [("naming", "叫作……，或者解释名称"), ("quotation", "引用别人说的话或想法")],
                    "ja.nagara": [("simultaneous", "一边做，一边……"), ("concessive", "虽然这样，却……")],
                    "ja.koto_wa_nai": [("unnecessary", "不必做"), ("never", "从未发生")],
                    "ja.ta_koto_ga_aru": [("experience", "经验"), ("occasional", "偶尔发生")],
                }
                candidates = [{"id": identity, "label": label, "features": [identity],
                               "evidence": ["construction_has_multiple_contextual_uses"], "status": "possible"}
                              for identity, label in meanings[rule["id"]]]
            if rule["anchor"] == "fixed":
                lemma = rule["label"]
            grammatical_features = [rule["id"], *rule.get("semantic_features", [])]
            if anchor_span:
                grammatical_features.extend(anchor_span.get("features", []))
            if rule.get("kind") == "idiom":
                grammatical_features.extend(["idiom", "perception"] if rule["id"] in {"ja.me_ni_suru", "ja.mimi_ni_suru"} else ["idiom"])
            if selected and token_class(selected[-1]) == "助動詞-タ" and "past" not in grammatical_features:
                grammatical_features.append("past")
            if selected and selected[-1].get("lemma") in {"ます", "です"} and "polite" not in grammatical_features:
                grammatical_features.append("polite")
            output.append({"id": stable_span_id(sentence_id, start, end, rule["id"]), "sentence_id": sentence_id,
                           "start": start, "end": end, "surface": text[start:end], "lemma": lemma, "reading": anchor_span.get("reading", "") if anchor_span else "",
                           "kind": rule.get("kind", "construction"), "grammar_ids": [rule["id"]], "features": list(dict.fromkeys(grammatical_features)),
                           "token_ids": [item["id"] for item in selected], "steps": [], "candidates": candidates,
                           "explanation_zh": rule["explanation_zh"], "children": children, "source": "rule", "version": rule_version(),
                           "status": "ambiguous" if candidates else "determined",
                           "captures": {"marker": match.group(), "anchor": text[start:match.start()], "construction": text[start:end], "grammar_label": rule["label"]},
                           "evidence": ["token_aligned_pattern", f"anchor:{rule['anchor']}", f"rule:{rule['id']}"]})
    # A graph, not destructive longest-match tokenisation: e.g. ている is a
    # child of ているのに, and both remain available for teaching and cards.
    for parent in output:
        nested = [item["id"] for item in output if item["id"] != parent["id"] and parent["start"] <= item["start"] and item["end"] <= parent["end"]
                  and (parent["start"], parent["end"]) != (item["start"], item["end"])]
        parent["children"] = list(dict.fromkeys(parent["children"] + nested))
    return sorted(output, key=lambda item: (item["start"], -(item["end"] - item["start"]), item["grammar_ids"][0]))
