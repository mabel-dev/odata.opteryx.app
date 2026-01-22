#!/usr/bin/env python3
import re, ast, pprint, shutil, sys, json, os
from collections import Counter

P='tests/test_odata_battery.py'
BAK=P+'.bak'
if not os.path.exists(BAK):
    shutil.copyfile(P, BAK)
    print('BACKUP_CREATED', BAK)
else:
    print('BACKUP_ALREADY_EXISTS', BAK)

s = open(P).read()
# Extract block
m = re.search(r'(PLANETS_TEST_BATTERY\s*=\s*\[)(.*?)(\]\s*# fmt:on)', s, re.S)
if not m:
    print('ERROR: PLANETS_TEST_BATTERY block not found')
    sys.exit(1)
content = m.group(2)
try:
    lst = ast.literal_eval('['+content+']')
except Exception as e:
    print('ERROR parsing PLANETS_TEST_BATTERY:', e)
    sys.exit(2)
counts_before = Counter(len(t) for t in lst if isinstance(t, tuple))
print('COUNTS_BEFORE', json.dumps(counts_before))
# Convert
newlst=[]
changed=0
for t in lst:
    if isinstance(t, tuple) and len(t)==5:
        dataset, query_string, expected_rows, expected_columns, expected_error = t
        newlst.append((dataset, query_string, expected_rows, expected_columns, None, expected_error))
        changed+=1
    else:
        newlst.append(t)
counts_after = Counter(len(t) for t in newlst if isinstance(t, tuple))
print('COUNTS_AFTER', json.dumps(counts_after), 'CHANGED', changed)
# Remove normalization block
s2 = s
norm_re = re.compile(r"# Normalize test tuples to 6-tuple format:.*?PLANETS_TEST_BATTERY = _normalized_plan\s*EXTENDED_TEST_BATTERY = PLANETS_TEST_BATTERY\s*", re.S)
if norm_re.search(s2):
    s2 = norm_re.sub('# All test tuples are explicit 6-tuples now.\n', s2)
    print('REMOVED_NORMALIZATION')

# Build formatted block
lines = []
for t in newlst:
    lines.append(pprint.pformat(t, width=120))
formatted = '\n'.join('    '+ln+',' for ln in lines) + '\n'
new_block = m.group(1) + '\n' + formatted + m.group(3)
# Replace
s2 = s2[:m.start(1)] + new_block + s2[m.end(3):]
# Write atomically
tmp = P + '.tmp'
open(tmp,'w').write(s2)
os.replace(tmp,P)
print('FILE_UPDATED', P)
# Verify
m2 = re.search(r'(PLANETS_TEST_BATTERY\s*=\s*\[)(.*?)(\]\s*# fmt:on)', s2, re.S)
if not m2:
    print('ERROR: verification block not found')
    sys.exit(3)
lst2 = ast.literal_eval('['+m2.group(2)+']')
counts_final = Counter(len(t) for t in lst2 if isinstance(t, tuple))
print('COUNTS_FINAL', json.dumps(counts_final))
print('TOTAL_TUPLES', len(lst2))
# Print first 5 tuples for inspection
print('\nFIRST_10_TUPLES:')
for t in lst2[:10]:
    print(' ', t)

print('\nDone')
