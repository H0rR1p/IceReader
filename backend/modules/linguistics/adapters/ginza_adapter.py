"""Optional CPU model. Import and load only after a user enables it."""
import importlib.util
import threading
from importlib.metadata import version, PackageNotFoundError
from .base import validate_parse


class GinzaAdapter:
    _lock = threading.Lock()
    _model = None

    def available(self):
        return importlib.util.find_spec('spacy') is not None and importlib.util.find_spec('ja_ginza') is not None

    def manifest(self):
        try:
            model_version = version('ja-ginza') if self.available() else 'unavailable'
        except PackageNotFoundError:
            model_version = 'unknown'
        return {'backend': 'ginza', 'model': 'ja_ginza', 'version': model_version,
                'available': self.available(), 'lazy': True, 'device': 'cpu'}

    def analyze(self, text):
        if not self.available():
            return {'nodes': [], 'version': 'none', 'warning': '未安装可选GiNZA模型，继续使用轻量规则'}
        with self._lock:
            if self._model is None:
                import spacy
                self._model = spacy.load('ja_ginza')
            doc = self._model(text)
            nodes = [{'id': token.i, 'start': token.idx, 'end': token.idx + len(token.text),
                'surface': token.text, 'lemma': token.lemma_, 'pos': token.pos_,
                'head': token.head.i, 'dependency': token.dep_} for token in doc if token.text]
        return validate_parse(text, {'nodes': nodes, 'version': self.manifest()['version']})


backend = GinzaAdapter()
