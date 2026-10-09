from typing import Protocol


class DependencyBackend(Protocol):
    def available(self) -> bool: ...
    def analyze(self, text: str) -> dict: ...


def validate_parse(text: str, result: dict) -> dict:
    nodes = result.get('nodes', [])
    identities = {node['id'] for node in nodes}
    for node in nodes:
        if not 0 <= node['start'] < node['end'] <= len(text) or text[node['start']:node['end']] != node['surface'] or node['head'] not in identities:
            raise ValueError('依存结果不能映射到原文')
    return result
