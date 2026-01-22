import json
from pathlib import Path
from collections import defaultdict
d2i = {'Hepatocellular Carcinoma': 'HCC', 'Intrahepatic Cholangiocarcinoma': 'iCCA'}
actual = defaultdict(lambda: defaultdict(int))
for f in Path('data/validated').glob('*.json'):
    data = json.load(open(f, encoding='utf-8'))
    disease = d2i.get(data.get('domain', ''), 'unknown')
    guideline = data.get('guideline', 'unknown')
    actual[disease][guideline] += 1
print('Validated files by guideline:')
for disease in sorted(actual.keys()):
    print(f'{disease}:')
    for g in sorted(actual[disease].keys()):
        print(f'  {g}: {actual[disease][g]}')
