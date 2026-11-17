import re
path = 'templates/base.html'
src = open(path, encoding='utf-8').read()

# Print the region around the extra_js block start (byte 201343)
# Read a generous window
start = 201343 - 2000
end = 201343 + 40000
chunk = src[start:end]
# Print first 6000 chars to see the structure
print(chunk[:6000])
