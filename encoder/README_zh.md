# guild2_codec

《行会2文艺复兴》的全局字体转码 CLI。

## 转码表

```text
data\guild2_codec.json
```

转码表只有一个纯 JSON 字典，包含编码项和私有码位解码项。

```json
{"波":"꒯","꒯":"波","꛶":"波","ą":"ç"}
```

编码只使用非 private key；解码只使用 private key，所以波兰替代不会把普通 Unicode，例如法语 `ç`，反向改坏。

## 命令

```bat
python guild2_codec.py encode "你好，法庭。"
python guild2_codec.py decode "U+A19C U+A3B3 U+AC95 U+A6F3 U+A48A U+A109"
python guild2_codec.py lookup "你好"
python guild2_codec.py stats
```

## 文件

```bat
python guild2_codec.py encode --file input.txt --output output.txt
python guild2_codec.py decode --file encoded.txt --output plain.txt
```

## 缺失字符

```text
error     CJK/private 缺映射时报错
replace   使用 --replacement 替换
keep      保留原字符
drop      删除字符
```
