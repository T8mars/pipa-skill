"""Mechanical prompt checks share the role manifest with submission; no API calls."""
from pathlib import Path
import argparse,re
from project_config import load_cast
ROOT=Path(__file__).resolve().parent
SECTIONS=['subject_definitions:','summary:','retention_analysis:','detailed_description:','overall_soundscape:','non_diegetic_music:']

def check_prompt(num,text,cast):
    errors,warns=[],[]
    if text.startswith('\ufeff'):errors.append('UTF-8 BOM')
    if not text.startswith(SECTIONS[0]):errors.append('必须从 subject_definitions: 开始')
    if '```' in text or re.search(r'一号|二号|\.png',text):errors.append('包含代码围栏、项目角色名或图片文件名')
    if 'attribute_transfer' in text:errors.append('人物替换不使用 attribute_transfer')
    headings=re.findall(r'(?m)^([a-z_]+:)\s*$',text)
    if headings!=SECTIONS:
        errors.append('必须恰好六段且顺序正确，不能缺失、重复或增加标题');return errors,warns
    parts={}
    for i,h in enumerate(SECTIONS):
        start=text.index(h)+len(h);end=text.index(SECTIONS[i+1]) if i+1<len(SECTIONS) else len(text)
        parts[h]=text[start:end]
    count=len(cast[num]);expected={str(i) for i in range(1,count+1)};definitions=parts[SECTIONS[0]]
    defined=re.findall(r'(?m)^\s*<Subject (\d+)>',definitions)
    if len(defined)!=count or set(defined)!=expected:errors.append('Subject 定义数量与角色映射不符')
    pictures=re.findall(r'<Picture (\d+)>',text)
    if sorted(pictures)!=sorted(expected):errors.append('每张身份图必须恰好引用一次，数量与角色映射一致')
    for line in definitions.splitlines():
        match=re.match(r'\s*<Subject (\d+)>',line)
        if match and re.findall(r'<Picture (\d+)>',line)!=[match[1]]:errors.append('每个 Subject 定义行须绑定对应编号 Picture')
    if re.search(r'<Picture \d+>',text[text.index(SECTIONS[1]):]):errors.append('Picture 只能出现在对应 Subject 定义中')
    if not set(re.findall(r'<Subject (\d+)>',text)).issubset(expected):errors.append('引用了未定义的 Subject')
    if '[video editing + reference generation]' not in parts[SECTIONS[1]]:errors.append('summary 缺少编辑前缀')
    if 'fully_preserved' not in parts[SECTIONS[2]] or 'partially_preserved' not in parts[SECTIONS[2]]:errors.append('retention_analysis 缺少身份/结构保留标签')
    if 'Kotte Animation' not in text:errors.append('缺少来源项目水印处理')
    detailed=parts[SECTIONS[3]];times=re.findall(r'(?<!\d)(\d{2}):(\d{2})\.(\d{3})(?!\d)',detailed)
    values=[int(m)*60000+int(s)*1000+int(ms) for m,s,ms in times]
    if not values or values[0]!=0 or any(int(s)>=60 for _,s,_ in times):errors.append('动作时间轴须以 00:00.000 开始并使用有效秒数')
    if values!=sorted(values):errors.append('动作时间点倒序')
    if '[Shot 1]' not in detailed:errors.append('缺少 [Shot 1]')
    if num=='003' and 'cigarette' not in text.lower():warns.append('人工核对参考图是否叼烟，并按003规则处理')
    return errors,warns

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('segments',nargs='*');args=ap.parse_args()
    cast=load_cast(ROOT);nums=list(dict.fromkeys(args.segments)) or list(cast);errors,warns=[],[]
    for n in nums:
        if n not in cast:errors.append(f'{n}: 不在角色映射中');continue
        p=ROOT/'提示词'/(n+'.txt')
        if not p.is_file():errors.append(f'{n}: missing file');continue
        es,ws=check_prompt(n,p.read_text(encoding='utf-8'),cast)
        errors.extend(f'{n}: {e}' for e in es);warns.extend(f'{n}: {w}' for w in ws)
    print(f'errors {len(errors)}, warns {len(warns)}')
    for e in errors:print('ERR',e)
    for w in warns:print('WARN',w)
    return 1 if errors else 0
if __name__=='__main__':raise SystemExit(main())
