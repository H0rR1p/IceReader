"""Original, fixed teaching-unit review matrix, independent of parser rules.

These are authored inflection tables and synthetic sentences, not a literary
corpus or an external expert adjudication. Do not derive golds from predictions.
"""
import json
from pathlib import Path

# dictionary, stem, te, past, negative stem, conditional, volitional, causative stem
VERBS = [
    ('読む','読み','読んで','読んだ','読ま','読めば','読もう','読ませ'),
    ('書く','書き','書いて','書いた','書か','書けば','書こう','書かせ'),
    ('歩く','歩き','歩いて','歩いた','歩か','歩けば','歩こう','歩かせ'),
    ('話す','話し','話して','話した','話さ','話せば','話そう','話させ'),
    ('聞く','聞き','聞いて','聞いた','聞か','聞けば','聞こう','聞かせ'),
    ('待つ','待ち','待って','待った','待た','待てば','待とう','待たせ'),
    ('買う','買い','買って','買った','買わ','買えば','買おう','買わせ'),
    ('飲む','飲み','飲んで','飲んだ','飲ま','飲めば','飲もう','飲ませ'),
    ('遊ぶ','遊び','遊んで','遊んだ','遊ば','遊べば','遊ぼう','遊ばせ'),
    ('帰る','帰り','帰って','帰った','帰ら','帰れば','帰ろう','帰らせ'),
    ('泳ぐ','泳ぎ','泳いで','泳いだ','泳が','泳げば','泳ごう','泳がせ'),
    ('急ぐ','急ぎ','急いで','急いだ','急が','急げば','急ごう','急がせ'),
    ('使う','使い','使って','使った','使わ','使えば','使おう','使わせ'),
    ('取る','取り','取って','取った','取ら','取れば','取ろう','取らせ'),
    ('運ぶ','運び','運んで','運んだ','運ば','運べば','運ぼう','運ばせ'),
    ('食べる','食べ','食べて','食べた','食べ','食べれば','食べよう','食べさせ'),
    ('見る','見','見て','見た','見','見れば','見よう','見させ'),
    ('起きる','起き','起きて','起きた','起き','起きれば','起きよう','起きさせ'),
    ('寝る','寝','寝て','寝た','寝','寝れば','寝よう','寝させ'),
    ('借りる','借り','借りて','借りた','借り','借りれば','借りよう','借りさせ'),
    ('教える','教え','教えて','教えた','教え','教えれば','教えよう','教えさせ'),
    ('開ける','開け','開けて','開けた','開け','開ければ','開けよう','開けさせ'),
    ('閉める','閉め','閉めて','閉めた','閉め','閉めれば','閉めよう','閉めさせ'),
    ('調べる','調べ','調べて','調べた','調べ','調べれば','調べよう','調べさせ'),
    ('届ける','届け','届けて','届けた','届け','届ければ','届けよう','届けさせ'),
]


def cases():
    rows = []
    for lemma, stem, te, past, neg, conditional, volitional, cause in VERBS:
        forms = [
            ('honorific', '先生が', 'お'+stem+'になる', ['honorific']),
            ('honorific', '先生が', 'お'+stem+'になりました', ['honorific','polite','past']),
            ('honorific', '先生が', 'お'+stem+'にならなかった', ['honorific','negative','past']),
            ('honorific', '先生が', 'お'+stem+'になっていた', ['honorific','progressive_resultative','past']),
            ('humble', '私が', 'お'+stem+'する', ['humble']),
            ('humble', '私が', 'お'+stem+'いたしました', ['humble','polite','past']),
            ('benefactive', '先生が', 'お'+stem+'くださった', ['respectful_benefactive','past']),
            ('benefactive', '先生に', 'お'+stem+'いただきました', ['humble_benefactive','polite','past']),
            ('auxiliary', '私が', te+'いる', ['progressive_resultative']),
            ('auxiliary', '私が', te+'いた', ['progressive_resultative','past']),
            ('auxiliary', '私が', te+'いません', ['progressive_resultative','negative','polite']),
            ('benefactive', '先生が', te+'くださる', ['respectful_benefactive']),
            ('benefactive', '先生が', te+'くださった', ['respectful_benefactive','past']),
            ('benefactive', '先生が', te+'くださらなかった', ['respectful_benefactive','negative','past']),
            ('benefactive', '先生に', te+'いただく', ['humble_benefactive']),
            ('benefactive', '先生に', te+'いただいた', ['humble_benefactive','past']),
            ('benefactive', '先生に', te+'いただきませんでした', ['humble_benefactive','negative','polite','past']),
            ('benefactive', '友達に', te+'もらう', ['benefactive']),
            ('benefactive', '友達が', te+'くれる', ['benefactive']),
            ('benefactive', '私が', te+'あげる', ['benefactive']),
            ('auxiliary', '私が', te+'しまった', ['completion','past']),
            ('auxiliary', '私が', te+'みた', ['trial','past']),
            ('causative', '先生に', cause+'ていただく', ['causative','humble_benefactive']),
            ('causative', '私は', cause+'られなかった', ['causative','voice_ambiguous','negative','past']),
            ('inflection', '私が', neg+'ない', ['negative']),
            ('inflection', '私が', neg+'なかった', ['negative','past']),
            ('inflection', '私が', stem+'ます', ['polite']),
            ('inflection', '私が', stem+'ました', ['polite','past']),
            ('inflection', '私が', te, ['te_form']),
            ('inflection', '私が', past, ['past']),
            ('inflection', '私が', conditional, ['conditional']),
            ('inflection', '私が', volitional, ['volitional']),
        ]
        for category, prefix, surface, features in forms:
            rows.append({'id':f'review-{len(rows)+1:04}', 'category':category,
                         'text':prefix+surface+'。', 'surface':surface, 'lemma':lemma,
                         'required_features':features})
    for surface in ['お茶になった','お金になった','お店になった','お酒になった','お薬になった',
                    'お部屋になった','お土産になった','ご迷惑になった','ご参考になった',
                    'お話を聞いた','ご飯をいただいた','お金をくださった']:
        rows.append({'id':f'review-{len(rows)+1:04}', 'category':'negative', 'text':surface+'。',
                     # Standalone くださる is legitimately respectful giving.
                     # Reject merging the ordinary noun into a teaching verb,
                     # and reject misidentifying physical receipt as a helper.
                     'rejected_prefix_span':surface,
                     'rejected_features':['humble_benefactive','respectful_benefactive']})
    return rows


if __name__ == '__main__':
    rows = cases()
    Path(__file__).with_name('review-v1.jsonl').write_text(
        ''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
