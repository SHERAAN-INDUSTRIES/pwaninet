import re
path = 'templates/base.html'
src = open(path, encoding='utf-8').read()

# Find the extra_js block
idx_block = src.find('{% block extra_js %}')
print('block extra_js at:', idx_block)
if idx_block == -1:
    raise SystemExit('not found')

# Find the matching endblock
endblocks = [m.start() for m in re.finditer(r'{% endblock %}', src)]
# The block that ends just before "Navigation Progress Bar" script
nav_markers = [m.start() for m in re.finditer(r'Navigation Progress Bar', src)]
print('nav markers at:', nav_markers)

# The endblock immediately before the nav script
extra_end = None
for eb in reversed(endblocks):
    if eb < nav_markers[0]:
        extra_end = eb
        break
print('extra_js endblock at:', extra_end)

# Print the block content
block_start = idx_block
block_end = extra_end + len('{% endblock %}')
print('--- extra_js block ---')
print(src[block_start:block_end])
print('--- end ---')
print('Total block len:', block_end - block_start)

# Check for scripts in the block
scripts = re.findall(r'<script>.*?</script>', src[block_start:block_end], re.DOTALL)
print('scripts in block:', len(scripts))
for i, s in enumerate(scripts):
    print(f'  script {i}: starts with {s[:60]!r}')
