"""Offline source validation; does not start services or load models."""
import ast
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
for path in root.glob('*.py'):
    ast.parse(path.read_text(), filename=str(path))
flows = json.loads((root / 'node-red/flows.json').read_text())
ids = [node['id'] for node in flows]
assert len(ids) == len(set(ids)), 'Duplicate node IDs'
for node in flows:
    for output in node.get('wires', []):
        for target in output:
            assert target in ids, f'Missing wire target: {target}'
assert (root / 'firmware/ContinIo_Final_ESP32_API/config.example.h').exists()
print('Python syntax, flow JSON and wire targets passed.')
