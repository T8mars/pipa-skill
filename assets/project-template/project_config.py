"""Shared cast, frame-table and current-file review checks; no network access."""
from pathlib import Path
import hashlib
import json
import re

def load_cast(root):
    data = json.loads((Path(root) / '角色映射.json').read_text(encoding='utf-8-sig'))
    cast = data.get('segments', {})
    if data.get('schema_version') != 1 or not cast:
        raise ValueError('角色映射.json 缺少有效 schema_version/segments')
    for num, roles in cast.items():
        if not re.fullmatch(r'\d{3}', num) or roles not in ('一', '二', '一二'):
            raise ValueError(f'无效角色映射：{num} {roles}')
    return cast

def frame_table(path):
    rows = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.startswith('合计'):
            continue
        match = re.fullmatch(r'(\d{3}\.mp4)\s+([1-9]\d*)\s*', line)
        if not match:
            raise ValueError('无效原始帧数行：' + line)
        rows.append((match[1], int(match[2])))
    if not rows or len({name for name, _ in rows}) != len(rows):
        raise ValueError('原始帧数表为空或有重复段号')
    return rows

def file_signature(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    stat = path.stat()
    return {'sha256': digest.hexdigest(), 'size_bytes': stat.st_size,
            'source_mtime_ns': stat.st_mtime_ns}

def require_current_reviews(root, rows):
    root = Path(root)
    state = json.loads((root / 'rh_state.json').read_text(encoding='utf-8'))['tasks']
    reviews = json.loads((root / '制作分析/生成复核/visual-reviews.json').read_text(encoding='utf-8'))
    for name, keep in rows:
        num = Path(name).stem
        signature = file_signature(root / '完成分段' / name)
        review = reviews.get(num, {})
        task = state.get(num, {})
        if (task.get('status') != 'SUCCESS' or not task.get('taskId')
                or review.get('status') != 'passed'
                or review.get('taskId') != task['taskId']
                or any(review.get(key) != value for key, value in signature.items())
                or review.get('reviewed_retained_frames') != [0, keep]):
            raise ValueError('缺少当前版本完整视觉复核：' + num)
