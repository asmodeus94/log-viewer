import re

text = b"foo\nbar\nbaz"
pattern = b"^bar$"
matcher = re.compile(pattern, re.MULTILINE)

print("MULTILINE:", matcher.findall(text))

matcher2 = re.compile(pattern)
print("No MULTILINE:", matcher2.findall(text))
