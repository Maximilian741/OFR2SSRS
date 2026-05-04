# Rules for any agent editing this codebase

These rules exist because the Windows host mount silently corrupts files when
the wrong write strategy is used. Following them is non-negotiable.

## The mount problem (root cause)

The project lives on `C:\Users\maxca\Documents\HackathonOracle2SSRS` on the
Windows host, mounted into the agent's Linux sandbox at
`/sessions/.../mnt/Documents/HackathonOracle2SSRS`. The mount has two
documented failure modes:

1. **Trailing null-byte padding.** When `Edit` or `Write` tools shrink a file,
   the host doesn't truncate; it leaves the old tail as `\x00` bytes. This
   produces files that LOOK fine on the host but cause Python to throw
   `SyntaxError: unterminated triple-quoted string` or
   `ValueError: source code string cannot contain null bytes`.

2. **Mid-tag/mid-line truncation.** Long writes via `Edit` can lose the
   bottom of the file entirely.

## The required write protocol

For ANY file edit in this repo:

### 1. Prefer bash heredocs over Edit/Write

```bash
cat > path/to/file.py << 'PYEOF'
...full file content...
PYEOF
```

The `'PYEOF'` (quoted) prevents shell expansion. Heredocs truncate-on-write
in the kernel, so no padding is left behind.

### 2. ALWAYS strip trailing nulls after any write, regardless of tool

```bash
python3 -c "data=open('path/to/file.py','rb').read().rstrip(b'\\x00'); open('path/to/file.py','wb').write(data)"
```

### 3. ALWAYS verify the file parses cleanly

```bash
# Python:
python3 -c "import ast; ast.parse(open('path/to/file.py').read())"
# JavaScript:
node --check path/to/file.js
# JSON:
python3 -c "import json; json.load(open('path/to/file.json'))"
```

### 4. After ANY change, run the integration smoke test

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -c "
import sys; sys.path.insert(0,'backend')
from converter import convert
out = convert(open('samples/oracle/MVWF_PERMIT.xml','rb').read())
assert out['rdl_xml'], 'pipeline broken'
print('OK')
"
```

### 5. If the file gets truncated, restore by appending the missing tail with a heredoc

Do NOT try to use `Edit` on a truncated file. The heuristic match will fail
and you'll keep extending it. Instead, identify the cut point and `cat >>` the
missing remainder.

## File-ownership discipline (multi-agent safety)

When spawning sub-agents in parallel, give each one a SINGLE owned file or
directory. Two agents touching `plsql_to_tsql.py` simultaneously is the same
class of race condition as two threads writing one variable. Don't do it.

## Common mistakes that have happened in this repo

- Edit on `app.js` left a missing `}`, breaking all event handlers
- Edit on `live_data.py` left 452 trailing null bytes
- A long Write to `index.html` truncated the bottom, dropping the
  `<script>` tag entirely
- Two agents edited `plsql_to_tsql.py` in parallel and clobbered each
  other's tail

If you find yourself debugging one of these symptoms, return to rule 2 first
(strip nulls, verify parse). Then check rule 5 (verify the file ends
correctly).
