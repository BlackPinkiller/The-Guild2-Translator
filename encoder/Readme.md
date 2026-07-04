# guild2_codec

Global font codec CLI for The Guild 2 Renaissance.

## Codec Table

```text
data\guild2_write_codec.json
data\guild2_read_codec.json
```

Each file is one plain JSON dictionary with one direction only.

```json
{"波":"꒯","ą":"ç"}
```

```json
{"꒯":"波","꛶":"波"}
```

Unlisted non-CJK Unicode passes through. Korean uses standard Hangul Unicode and does not need conversion.

## Commands

```bat
python guild2_codec.py encode "你好，法庭。"
python guild2_codec.py decode "U+A19C U+A3B3 U+AC95 U+A6F3 U+A48A U+A109"
python guild2_codec.py lookup "你好"
python guild2_codec.py stats
```

## Files

```bat
python guild2_codec.py encode --file input.txt --output output.txt
python guild2_codec.py decode --file encoded.txt --output plain.txt
```

## Missing Characters

```text
error     fail on missing CJK characters during encode
replace   use --replacement
keep      keep original character
drop      remove character
```

The read table is for text rendered with the Chinese custom font. Standard Korean text must not be passed to `decode` because `U+AC00..U+ACA7` are also borrowed by that Chinese font.
