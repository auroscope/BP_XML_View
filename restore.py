import re

with open('app.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if line.startswith('@app.route(\'/proposal\')'):
        skip = True
    if skip and line.startswith('def show_proposal():'):
        skip = False
        new_lines.append('@app.route(\'/proposal\')\n')
    if not skip:
        new_lines.append(line)

with open('app.py', 'w') as f:
    f.writelines(new_lines)
