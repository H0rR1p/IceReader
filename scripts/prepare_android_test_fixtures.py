"""Generate tokenizer and EPUB fixtures using the desktop reference engine."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.nlp import split_sentences, tokenize_sentence
from ebooklib import epub

samples = ['姿を目にする。', 'お兄ちゃんと学校へ行く。', '猫が走っている。',
           '「本当に？」「うん！」', 'カップルを見つけた。', '日本語を勉強しています。',
           '𠮷野さんは😊笑った。', '京都\nから奈良へ行く。', '読んでしまった。', '彼女には見えなかった。']
corpus = []
for i in range(200):
    text = f'第{i + 1}回。' + samples[i % len(samples)]
    tokens = []
    for sentence in split_sentences(text):
        tokens += [{k: v for k, v in token.items() if k in ('surface', 'lemma', 'reading', 'part_of_speech', 'start', 'end')}
                   for token in tokenize_sentence('fixture', sentence.text)]
    corpus.append({'text': text, 'expected': tokens})
(ROOT / 'build/token-corpus.json').write_text(json.dumps(corpus, ensure_ascii=False), encoding='utf-8')
image = (ROOT / 'public/bingdu-logo.png').read_bytes()
for i in range(10):
    book = epub.EpubBook(); book.set_identifier(f'android-{i}')
    book.set_title(f'安卓解析验收{i}'); book.set_language('ja'); book.add_author('验收')
    book.set_cover('cover.png', image)
    chapters = []
    for j in range(2):
        chapter = epub.EpubHtml(title=f'第{j + 1}章', file_name=f'chapter-{j}.xhtml', lang='ja')
        chapter.content = '<h1>章节</h1><p><ruby>学校<rt>がっこう</rt></ruby>へ行く。</p><p>姿を目にする。</p><p><img src="cover.png" alt="插图"/></p>'
        if i % 2: chapter.content += '<p><span>京都</span><br/><span>から奈良へ行く。</span></p>'
        book.add_item(chapter); chapters.append(chapter)
    book.toc = chapters; book.add_item(epub.EpubNcx()); book.add_item(epub.EpubNav()); book.spine = ['nav'] + chapters
    epub.write_epub(str(ROOT / f'build/fixture-{i}.epub'), book)
