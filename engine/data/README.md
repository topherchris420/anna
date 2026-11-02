# Engine data

| File | Contents | Used by |
|---|---|---|
| `english-words.txt.gz` | 167,852 English words of five or more letters, one per line, sorted. | [`engine/spelling.py`](../spelling.py): a word in this list is never "corrected", even when no indexed document contains it. |

## `english-words.txt.gz`

**Source:** ENABLE (Enhanced North American Benchmark LEXicon), the word list
compiled for word games and released into the **public domain**; taken from
[`dolph/dictionary`](https://github.com/dolph/dictionary/blob/master/enable1.txt)
(`enable1.txt`, 172,823 words).

**Kept:** the words of five or more lower-case ASCII letters — the only words
spelling correction ever considers (`MIN_LENGTH` in `engine/spelling.py`).
Shorter words are never corrected, so they need no entry. Gzipped at level 9
with no timestamp, so the file is reproducible:

```bash
curl -sO https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt
python3 - <<'PY'
import gzip, re
words = sorted({w.strip() for w in open("enable1.txt") if re.fullmatch(r"[a-z]{5,}", w.strip())})
open("engine/data/english-words.txt.gz", "wb").write(
    gzip.compress(("\n".join(words) + "\n").encode(), 9, mtime=0))
PY
```

**Why a word list at all:** in a corpus of a few hundred papers most everyday
words are absent. Correcting every word no document contains turned 36 of 121
everyday words into technical neighbours one key away ("lunch" into "launch",
"pocket" into "packet"); with this list, none (see
[`test/relevance/README.md`](../../test/relevance/README.md#misspelled-queries)).
