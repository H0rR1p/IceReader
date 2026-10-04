"""Android adapter to official Java Sudachi; no tokenizer approximation."""
import json
import os
from pathlib import Path
from java import jclass

class Morpheme:
    def __init__(self, value): self.value = value
    def surface(self): return str(self.value.surface())
    def dictionary_form(self): return str(self.value.dictionaryForm())
    def reading_form(self): return str(self.value.readingForm())
    def part_of_speech(self):
        parts = self.value.partOfSpeech()
        return tuple(str(parts.get(i)) for i in range(parts.size()))

class JavaTokenizer:
    class SplitMode:
        B = 'B'
    def __init__(self, dictionary): self.instance = dictionary.create()
    def tokenize(self, text, mode):
        java_mode = jclass('com.worksap.nlp.sudachi.Tokenizer$SplitMode').valueOf(str(mode))
        values = self.instance.tokenize(java_mode, text)
        return [Morpheme(values.get(i)) for i in range(values.size())]

class Dictionary:
    def __init__(self, dict='core'):
        config = json.dumps({'systemDict': str(Path(os.environ['BINGDU_DATA_DIR']) / 'nlp/system_core.dic')})
        self.value = jclass('com.worksap.nlp.sudachi.DictionaryFactory')().create(config)
    def create(self): return JavaTokenizer(self.value)

class dictionary:
    Dictionary = Dictionary
class tokenizer:
    Tokenizer = JavaTokenizer
