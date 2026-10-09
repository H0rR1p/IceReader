"""Rebuild the original teaching catalogue without external dictionary text.

Each tuple contains a bounded token-aligned marker, an explicit anchor,
and independently authored positive / adversarial examples. Review status
means repository tests and structural review, not an externally established
95 percent precision claim on real literary prose.
"""
from pathlib import Path
import json
from teaching_text import EXPLANATIONS


# identity, label, marker, anchor, explanation, positive, negative
ROWS = [
    ("te_iru", "〜ている", "[てで](?:いる|いた|い(?:る|た|ます|ました))", "te_predicate", "猫が窓を見ている。", "「ている」という名前を考えた。"),
    ("te_aru", "〜てある", "[てで](?:ある|あった|あり(?:ます|ました))", "te_predicate", "窓が開けてある。", "ある日は雨だった。"),
    ("te_oku", "〜ておく", "[てで](?:おく|おいた|おき(?:ます|ました))", "te_predicate", "水を買っておく。", "奥の部屋へ進む。"),
    ("te_shimau", "〜てしまう", "[てで]しま(?:う|った|い(?:ます|ました))", "te_predicate", "鍵を忘れてしまった。", "島の景色を見る。"),
    ("te_miru", "〜てみる", "[てで](?:みる|みた|見た|見る|み(?:ます|ました))", "te_predicate", "別の道を歩いてみる。", "店を見る。"),
    ("te_kureru", "〜てくれる", "[てで]くれ(?:る|た|ます|ました)", "te_predicate", "友達が手伝ってくれた。", "暮れの空が赤い。"),
    ("te_ageru", "〜てあげる", "[てで]あげ(?:る|た|ます|ました)", "te_predicate", "弟に読んであげた。", "上げた手が痛い。"),
    ("te_morau", "〜てもらう", "[てで]もら(?:う|った|い(?:ます|ました))", "te_predicate", "先生に見てもらう。", "お金をもらう。"),
    ("te_iku", "〜ていく", "[てで](?:いく|いった|行く|行った|いきます)", "te_predicate", "少しずつ覚えていく。", "駅へ行く。"),
    ("te_kuru", "〜てくる", "[てで](?:くる|きた|来る|来た|きます)", "te_predicate", "風が強くなってきた。", "明日ここへ来る。"),
    ("te_hoshii", "〜てほしい", "[てで](?:ほしい|欲しい)", "te_predicate", "もう少し待ってほしい。", "新しい本がほしい。"),
    ("te_kudasai", "〜てください", "[てで](?:ください|下さい)", "te_predicate", "ここで待ってください。", "水をください。"),
    ("naide", "〜ないで", "ないで", "predicate", "急がないで歩く。", "内では静かにする。"),
    ("te_mo_ii", "〜てもいい", "[てで]も(?:いい|良い)", "te_predicate", "ここに座ってもいい。", "とてもいい天気だ。"),
    ("te_wa_ikenai", "〜てはいけない", "[てで]はいけない", "te_predicate", "ここで走ってはいけない。", "彼には行けない理由がある。"),
    ("nakute_mo_ii", "〜なくてもいい", "なくても(?:いい|良い)", "predicate", "今日は来なくてもいい。", "泣くと気持ちがいい。"),
    ("nakereba_naranai", "〜なければならない", "なければならない", "predicate", "早く寝なければならない。", "なければという言葉を書く。"),
    ("nakute_wa_naranai", "〜なくてはならない", "なくてはならない", "predicate", "約束を守らなくてはならない。", "亡くてはという文字列だ。"),
    ("koto_ga_dekiru", "〜ことができる", "ことが(?:できる|出来る|できた|できます)", "predicate", "この道を通ることができる。", "出来る人が集まる。"),
    ("koto_ga_nai", "〜ことがない", "ことがない", "predicate", "そこへ行ったことがない。", "事がないと書く。"),
    ("ta_koto_ga_aru", "〜たことがある", "ことが(?:ある|あります)", "predicate", "この歌を聞いたことがある。", "机の上に本がある。"),
    ("koto_ni_suru", "〜ことにする", "ことに(?:する|した|します)", "predicate", "今日は歩くことにした。", "ことにするという名前だ。"),
    ("koto_ni_naru", "〜ことになる", "ことに(?:なる|なった|なります)", "predicate", "明日会うことになった。", "鳴る音が大きい。"),
    ("you_ni_suru", "〜ようにする", "ように(?:する|した|します)", "predicate", "毎日読むようにする。", "同じ模様にする。"),
    ("you_ni_naru", "〜ようになる", "ように(?:なる|なった|なります)", "predicate", "一人で書けるようになった。", "模様になる。"),
    ("tsumori", "〜つもり", "つもり", "predicate", "明日は休むつもりだ。", "雪が積もり始めた。"),
    ("hazu", "〜はず", "はず", "predicate", "彼はもう着いたはずだ。", "箱を外す。"),
    ("wake_da", "〜わけだ", "わけ(?:だ|です)", "predicate", "道が近いから早く着くわけだ。", "わけだという文字を書く。"),
    ("wake_dewa_nai", "〜わけではない", "わけ(?:では|じゃ)ない", "predicate", "嫌いなわけではない。", "訳ではないという題名だ。"),
    ("tokoro", "〜ところ", "ところ(?:だ|です|だった)?", "predicate", "今から出るところだ。", "ところどころ光る。"),
    ("ta_bakari", "〜たばかり", "ばかり", "predicate", "さっき起きたばかりだ。", "ばかりという語を調べる。"),
    ("sou_appearance", "〜そう（样态）", "そう(?:だ|です)", "predicate", "雨が降りそうだ。", "彼は行くそうだ。"),
    ("sou_hearsay", "〜そう（传闻）", "そう(?:だ|です)", "predicate", "彼は行くそうだ。", "雨が降りそうだ。"),
    ("rashii", "〜らしい", "らしい", "predicate", "電車は遅れるらしい。", "新しい本を読む。"),
    ("you_da", "〜ようだ", "よう(?:だ|です)", "predicate", "彼は疲れたようだ。", "模様だと答える。"),
    ("mitai_da", "〜みたいだ", "みたい(?:だ|です)", "predicate", "雨が降るみたいだ。", "映画を見たい。"),
    ("darou", "〜だろう／でしょう", "(?:だろう|でしょう)", "predicate", "明日は晴れるでしょう。", "でしょうという音を聞いた。"),
    ("kamoshirenai", "〜かもしれない", "かもしれない", "predicate", "彼は来ないかもしれない。", "鴨を知らない。"),
    ("tame_ni", "〜ために", "ために", "predicate", "覚えるために何度も読む。", "ためにという札がある。"),
    ("noni", "〜のに", "のに", "predicate", "知っているのに黙っている。", "野に花が咲く。"),
    ("node", "〜ので", "ので", "predicate", "雨が降るので休む。", "角の出口へ向かう。"),
    ("kara_reason", "〜から（原因）", "から", "predicate", "寒いから窓を閉める。", "東京から来た。"),
    ("nagara", "〜ながら", "ながら", "predicate", "歌いながら歩く。", "長良川へ行く。"),
    ("tara", "〜たら", "(?:たら|だら)", "predicate", "着いたら電話する。", "たらという魚を買う。"),
    ("ba", "〜ば", "ば", "predicate", "読めば分かる。", "そばを食べる。"),
    ("to_condition", "〜と（条件）", "と", "predicate", "押すと扉が開く。", "猫と犬がいる。"),
    ("te_mo", "〜ても", "[てで]も", "te_predicate", "雨が降っても行く。", "とても明るい。"),
    ("nara", "〜なら", "なら", "any", "行くなら連絡して。", "奈良へ行く。"),
    ("mae_ni", "〜前に", "(?:前|まえ)に", "predicate", "寝る前に水を飲む。", "駅前に立つ。"),
    ("ato_de", "〜後で", "(?:後|あと)で", "predicate", "読んだ後で返す。", "後で連絡する。"),
    ("aida", "〜間", "(?:間|あいだ)", "predicate", "待っている間、本を読む。", "間違いを直す。"),
    ("aida_ni", "〜間に", "(?:間|あいだ)に", "predicate", "寝ている間に雨が止んだ。", "仲間に話す。"),
    ("uchi_ni", "〜うちに", "うちに", "predicate", "明るいうちに帰ろう。", "うちには猫がいる。"),
    ("tabi_ni", "〜たびに", "(?:たび|度)に", "predicate", "会うたびに話す。", "旅に出る。"),
    ("toki", "〜とき", "(?:とき|時)", "predicate", "困ったときに相談する。", "時計を見る。"),
    ("ni_tsuite", "〜について", "について", "nominal", "本について話す。", "机に付いている傷を見る。"),
    ("ni_taishite", "〜に対して", "に(?:対して|たいして)", "nominal", "質問に対して答える。", "対してという字を書いた。"),
    ("ni_yotte", "〜によって", "によって", "nominal", "人によって好みが違う。", "船に寄って帰る。"),
    ("to_shite", "〜として", "として", "nominal", "代表として話す。", "財布を落として困った。"),
    ("ni_totte", "〜にとって", "にとって", "nominal", "私にとって大切だ。", "机に取っておくという文字を書く。"),
    ("ni_yoruto", "〜によると", "によると", "nominal", "予報によると晴れるそうだ。", "店に寄ると休みだった。"),
    ("ni_chigainai", "〜に違いない", "に(?:違いない|ちがいない)", "predicate", "彼は知っているに違いない。", "二つの違いを調べる。"),
    ("beki", "〜べき", "べき", "predicate", "ここで待つべきだ。", "べきという文字を書く。"),
    ("hou_ga_ii", "〜ほうがいい", "(?:ほう|方)が(?:いい|良い)", "predicate", "早く寝たほうがいい。", "方角がいい。"),
    ("ni_wa", "〜には（目的）", "には", "predicate", "読むには明かりが必要だ。", "庭には木がある。"),
    ("zu_ni", "〜ずに", "ずに", "predicate", "何も言わずに帰る。", "図に線を引く。"),
    ("nagara_mo", "〜ながらも", "ながらも", "predicate", "知りながらも黙っている。", "ながらもという文字列を書く。"),
    ("dake_de", "〜だけで", "だけで", "any", "見るだけで分かる。", "竹で作る。"),
    ("shika_negative", "〜しか〜ない", "しか(?:[ぁ-ゖァ-ヺ一-龯]{1,12})?ない", "nominal", "水しかない。", "鹿が歩いている。"),
    ("n_desu", "〜んです／のです", "(?:ん|の)(?:だ|です|だった)", "predicate", "雨が降るんです。", "のですという文字を書く。"),
    ("nominal_copula", "名词＋だ／です", "(?:だ|です)", "nominal", "私は学生です。", "だという音を聞く。"),
    ("de_aru", "〜である", "である", "nominal", "これは本である。", "それで歩く。"),
    ("ni_mo_kakawarazu", "〜にもかかわらず", "にも(?:かかわらず|関わらず)", "nominal", "雨にもかかわらず歩く。", "関わらずという語を調べる。"),
    ("to_iu", "〜という", "と(?:いう|言う)", "any", "青という色が好きだ。", "問う人がいる。"),
    ("volitional_to_suru", "〜ようとする", "と(?:する|した|します)", "predicate", "扉を開けようとした。", "本と筆を取る。"),
    ("me_ni_suru", "目にする", "目に(?:する|した|します)", "fixed", "珍しい鳥を目にした。", "三回目に会った。"),
    ("ki_ga_suru", "気がする", "気が(?:する|した|します)", "fixed", "雨が降る気がする。", "空気が澄んでいる。"),
    ("ki_ni_naru", "気になる", "気に(?:なる|なった|なります)", "fixed", "遠くの音が気になる。", "元気になった。"),
    ("te_ni_ireru", "手に入れる", "手に(?:入れる|入れた|入れます)", "fixed", "新しい資料を手に入れた。", "袋に手を入れた。"),
    ("koto_wa_nai", "〜ことはない", "ことはない", "predicate", "そんなに急ぐことはない。", "ことはないという題名を書く。"),
    ("correlative_ba_hodo", "〜ば〜ほど", "ば[ぁ-ゖァ-ヺ一-龯]{1,24}ほど", "predicate", "読めば読むほど分かる。", "読めば買うほど困る。"),
    ("purpose_you_ni", "〜ように", "ように", "predicate", "遅れないように早く出た。", "模様に色を付けた。"),
    ("tochuu_de", "〜途中で", "途中で", "predicate", "帰る途中で店に寄った。", "途中で連絡する。"),
    ("mama", "〜まま", "まま", "predicate", "暗いままで待った。", "ままごとで遊ぶ。"),
    ("mimi_ni_suru", "耳にする", "耳に(?:する|した|します)", "fixed", "噂を耳にした。", "耳に飾りを付けた。"),
    ("te_itadaku", "〜ていただく", "[てで](?:いただ|頂)(?:く|いた|き(?:ます|ました)|ける)", "te_predicate", "先生に教えていただいた。", "先生から本をいただいた。"),
    ("te_kudasaru", "〜てくださる", "[てで](?:くださ|下さ)(?:る|った|い(?:ます|ました))", "te_predicate", "先生が教えてくださった。", "先生が本をくださった。"),
    ("te_sashiageru", "〜て差し上げる", "[てで](?:さしあげ|差し上げ)(?:る|た|ます|ました)", "te_predicate", "先生に送って差し上げた。", "先生に花を差し上げた。"),
]


def generate():
    # Text is reviewed separately from the matching expressions. Rebuilding
    # the catalogue must never silently restore the old technical wording.
    assert len(ROWS) == 88
    directory = Path(__file__).resolve().parent
    rules = []
    for index, (identity, label, pattern, anchor, positive, negative) in enumerate(ROWS, 1):
        rule = {"id": f"ja.{identity}", "label": label, "pattern": pattern, "anchor": anchor,
                "explanation_zh": EXPLANATIONS[identity], "positive": [positive], "negative": [negative],
                "status": "enabled", "review": "original examples and structural negative tests; real-prose precision unmeasured",
                "order": index, "kind": "idiom" if anchor == "fixed" else "construction"}
        if identity.startswith("te_") and identity != "te_ni_ireru":
            rule["required_pos"] = ["助詞"]
        if identity == "sou_appearance":
            rule["preceding_form"] = ["連用形", "語幹"]
        if identity == "sou_hearsay":
            rule["preceding_form"] = ["終止形", "連体形"]
        if identity == "ba":
            rule["preceding_form"] = ["仮定形"]
        if identity == "nagara" or identity == "nagara_mo":
            rule["preceding_form"] = ["連用形"]
        if identity == "volitional_to_suru":
            rule["preceding_form"] = ["意志推量形"]
        if identity == "correlative_ba_hodo":
            rule["preceding_form"] = ["仮定形"]
            rule["repeated_predicate"] = True
        if identity == "me_ni_suru":
            rule["first_token_lemma"] = "目"
            rule["first_token_pos"] = "名詞"
            rule["excluded_first_subpos"] = ["数詞", "接尾辞"]
        if identity == "ki_ga_suru" or identity == "ki_ni_naru":
            rule["first_token_lemma"] = "気"
        if identity == "te_ni_ireru":
            rule["first_token_lemma"] = "手"
        if identity == "mimi_ni_suru":
            rule["first_token_lemma"] = "耳"
        semantic_features = {
            "nakereba_naranai": ["obligation"], "nakute_wa_naranai": ["obligation"],
            "te_mo_ii": ["permission"], "nakute_mo_ii": ["permission", "unnecessary"],
            "te_mo": ["concessive"], "correlative_ba_hodo": ["correlative"],
            "tochuu_de": ["in_progress"], "mama": ["unchanged_state"],
            "sou_hearsay": ["hearsay"], "sou_appearance": ["appearance"],
            "wake_dewa_nai": ["partial_negation"], "kamoshirenai": ["possibility"],
        }
        rule["semantic_features"] = semantic_features.get(identity, [])
        if identity == "ni_tsuite":
            # についている can be lexical 付く + ている. Keep this case for
            # the morphology graph instead of declaring topic-marking.
            rule["following_lemma_excludes"] = ["いる", "おる"]
        rules.append(rule)
    for filename, data in (("constructions.json", [item for item in rules if item["kind"] == "construction"]),
                           ("idioms.json", [item for item in rules if item["kind"] == "idiom"])):
        (directory / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    generate()
