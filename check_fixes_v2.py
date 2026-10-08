import sys
sys.path.insert(0, r'C:\Users\intpj\Documents\Codex\2026-09-29\agent\outputs\fy-finish')
from pathlib import Path

root = Path(r'C:\Users\intpj\Documents\Codex\2026-09-29\agent\outputs\fy-finish')

# D-002
text = (root / 'src' / 'find_yourself' / 'api' / 'schemas.py').read_text(encoding='utf-8')
in_msgcreate = False
has_mode = False
for line in text.split('\n'):
    if 'class MessageCreate' in line:
        in_msgcreate = True
        continue
    if in_msgcreate and line.strip().startswith('class '):
        break
    if in_msgcreate:
        if 'mode:' in line:
            has_mode = True
            break
print(f'D-002 MessageCreate mode: {has_mode}')

# D-004
dispatch = root / 'src' / 'find_yourself' / 'services' / 'agent_dispatch.py'
text = dispatch.read_text(encoding='utf-8')
print(f'D-004 _ensure_builtin_tools: {"_ensure_builtin_tools" in text}')

# D-005
obs = root / 'src' / 'find_yourself' / 'services' / 'observability.py'
text = obs.read_text(encoding='utf-8')
print(f'D-005 agents in obs: {"agents" in text}')

# D-001 - 检查 store.py
store = root / 'src' / 'find_yourself' / 'services' / 'assets' / 'store.py'
text = store.read_text(encoding='utf-8')
has_validate = '_validate_magic' in text
has_call = '_validate_magic(data, row.mime)' in text
has_magic = 'MAGIC_BYTES' in text
print(f'D-001 store validate func: {has_validate}')
print(f'D-001 store magic table: {has_magic}')
print(f'D-001 store call: {has_call}')

# assets.py 应该没有那套代码（这是对的）
assets = root / 'src' / 'find_yourself' / 'api' / 'routes' / 'assets.py'
text = assets.read_text(encoding='utf-8')
has_wrong = '_validate_magic(file, content_type)' in text
print(f'D-001 assets wrong code: {has_wrong} (should be False)')