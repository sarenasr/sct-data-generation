"""Export validated SCT items to CSV in the proper format."""
import json
from pathlib import Path
from datetime import datetime
import csv
from collections import Counter

# Collect all validated files
all_items = []
for f in sorted(Path('data/validated').glob('*.json')):
    data = json.load(open(f, encoding='utf-8'))
    all_items.append(data)

print(f'Total validated files: {len(all_items)}')

# Group by disease
hcc = [x for x in all_items if x.get('domain') == 'Hepatocellular Carcinoma']
icca = [x for x in all_items if x.get('domain') == 'Intrahepatic Cholangiocarcinoma']

print(f'HCC: {len(hcc)}')
print(f'iCCA: {len(icca)}')

# Take first 100 of each
hcc_export = hcc[:100]
icca_export = icca[:100]

print(f'Exporting HCC: {len(hcc_export)}')
print(f'Exporting iCCA: {len(icca_export)}')

# Export to CSV matching the existing format
output_file = f'data/exports/sct_items_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

with open(output_file, 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    # Header matching existing format
    writer.writerow([
        'domain', 'guideline', 'vignette', 'item_author_notes',
        'validator_guideline', 'validator_notes', 'question_type',
        'hypothesis', 'new_information', 'effect_phrase', 'options',
        'author_notes', 'validator_selected_option'
    ])
    
    for item in hcc_export + icca_export:
        domain = item.get('domain', '')
        guideline = item.get('guideline', '')
        vignette = item.get('vignette', '')
        item_author_notes = item.get('author_notes', '')
        
        validator_result = item.get('validator_result', {})
        validator_guideline = validator_result.get('validator_guideline', '')
        validator_notes = validator_result.get('validator_notes', '')
        validator_responses = validator_result.get('validator_responses', [])
        
        questions = item.get('questions', [])
        
        # Create a map of question_type -> selected_option
        response_map = {r.get('question_type'): r.get('selected_option') for r in validator_responses}
        
        for i, q in enumerate(questions):
            question_type = q.get('question_type', '')
            hypothesis = q.get('hypothesis', '')
            new_information = q.get('new_information', '')
            effect_phrase = q.get('effect_phrase', '')
            options = ', '.join(q.get('options', []))
            author_notes = q.get('author_notes', '')
            selected_option = response_map.get(question_type, '')
            
            # First question row includes vignette, subsequent rows have empty vignette
            if i == 0:
                writer.writerow([
                    domain, guideline, vignette, item_author_notes,
                    validator_guideline, validator_notes, question_type,
                    hypothesis, new_information, effect_phrase, options,
                    author_notes, selected_option
                ])
            else:
                writer.writerow([
                    '', '', '', '',
                    '', '', question_type,
                    hypothesis, new_information, effect_phrase, options,
                    author_notes, selected_option
                ])

print(f'\nExported to: {output_file}')
print(f'Total SCT items: {len(hcc_export) + len(icca_export)}')
print(f'Total rows (3 questions each): {(len(hcc_export) + len(icca_export)) * 3}')

# Show breakdown by guideline
print('\nBreakdown by guideline:')
print('\nHCC:')
hcc_guidelines = Counter(x.get('guideline') for x in hcc_export)
for g, c in sorted(hcc_guidelines.items()):
    print(f'  {g}: {c}')

print('\niCCA:')
icca_guidelines = Counter(x.get('guideline') for x in icca_export)
for g, c in sorted(icca_guidelines.items()):
    print(f'  {g}: {c}')
