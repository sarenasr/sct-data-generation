"""Rebuild progress.json based on actual validated files."""
import json
from pathlib import Path
from collections import defaultdict

# Expected configuration (20 per guideline)
expected = {
    'hepatocellular_carcinoma': {
        'AASLD 2018': 20, 'AASLD 2023': 20, 'BSG': 20, 'EASL': 20, 'ESMO': 20,
    },
    'icca': {
        'AASLD': 20, 'ASCO': 20, 'BSG': 20, 'EASL-ILCA': 20, 'ESMO': 20,
    }
}

d2i = {'Hepatocellular Carcinoma': 'hepatocellular_carcinoma', 'Intrahepatic Cholangiocarcinoma': 'icca'}

# Count validated files
actual = defaultdict(lambda: defaultdict(int))
for f in Path('data/validated').glob('*.json'):
    data = json.load(open(f, encoding='utf-8'))
    disease = d2i.get(data.get('domain', ''), 'unknown')
    guideline = data.get('guideline', 'unknown')
    actual[disease][guideline] += 1

# Build new progress.json
progress = {
    'session_id': '20260122_rebuild',
    'started_at': '2026-01-22T08:00:00',
    'diseases': ['hepatocellular_carcinoma', 'icca'],
    'questions_per_guideline': 20,
    'total_questions_target': 200,
    'questions': {},
    'status': 'running',
}

total_validated = 0
total_pending = 0

for disease, guidelines in expected.items():
    for guideline, target in guidelines.items():
        have = min(actual[disease][guideline], target)  # Cap at target
        need = max(0, target - actual[disease][guideline])
        
        # Mark validated
        for i in range(1, have + 1):
            qid = f'{disease}_{guideline}_{i}'
            progress['questions'][qid] = {
                'question_id': qid, 'disease': disease, 'guideline': guideline,
                'question_number': i, 'status': 'validated',
                'generated_at': '2026-01-22T03:00:00', 'validated_at': '2026-01-22T03:00:00',
                'file_path': None, 'error': None,
            }
            total_validated += 1
        
        # Mark pending
        for i in range(have + 1, target + 1):
            qid = f'{disease}_{guideline}_{i}'
            progress['questions'][qid] = {
                'question_id': qid, 'disease': disease, 'guideline': guideline,
                'question_number': i, 'status': 'pending',
                'generated_at': None, 'validated_at': None,
                'file_path': None, 'error': None,
            }
            total_pending += 1

# Save
with open('data/progress.json', 'w', encoding='utf-8') as f:
    json.dump(progress, f, indent=2)

print('PROGRESS.JSON REBUILT')
print('=' * 50)
print(f'Total validated: {total_validated}')
print(f'Total pending (to generate): {total_pending}')
print()
print('Current validated counts:')
for disease, guidelines in expected.items():
    name = 'HCC' if 'hepato' in disease else 'iCCA'
    print(f'\n{name}:')
    for g, target in guidelines.items():
        have = actual[disease][g]
        print(f'  {g}: {have}/{target}')

print()
print('MISSING BY GUIDELINE:')
for disease, guidelines in expected.items():
    name = 'HCC' if 'hepato' in disease else 'iCCA'
    for g, target in guidelines.items():
        have = actual[disease][g]
        need = max(0, target - have)
        if need > 0:
            print(f'  {name} - {g}: need {need} more')
