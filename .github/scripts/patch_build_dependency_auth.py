from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding="utf-8-sig")

patches = [
    ("Invoke-RestMethod -Headers @{ 'Accept' = 'application/vnd.github.v3+json' }",
     "Invoke-RestMethod -Headers @{ 'Accept' = 'application/vnd.github.v3+json'; Authorization = ('Bearer ' + $env:GITHUB_TOKEN) }"),
    ("$files = Invoke-RestMethod $githubApiUrl",
     "$files = Invoke-RestMethod -Headers @{ Authorization = ('Bearer ' + $env:GITHUB_TOKEN) } $githubApiUrl"),
]
for old, new in patches:
    if s.count(old) != 1:
        raise SystemExit(f"Expected a unique upstream dependency API call ({old})")
    s = s.replace(old, new, 1)
p.write_text(s, encoding="utf-8")
print("Authenticated GitHub dependency metadata lookups; no changes to filter implementation.")
