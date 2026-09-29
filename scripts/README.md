# scripts/ 运维脚本

这些是 Infra 分片提供的**开发/运维入口**，不是系统自带命令；每个脚本顶部有
comment-based help（`Get-Help ./scripts/<name>.ps1`），未就绪时如实返回 NOT_RUN，
绝不伪造“服务已存在”。

| 脚本 | 作用 | 退出码要点 |
|---|---|---|
| `healthcheck.ps1` | 探活 API `/health/live`、`/health/ready`，输出 JSON | 0 通过；2 降级；3 服务未就绪 |
| `wait-for.ps1` | 等待 TCP 端口或 HTTP 路径就绪（分阶段启动用） | 0 就绪；3 超时未就绪 |
| `backup-preflight.ps1` | 备份前预检：docker、容器 healthy、目标可写、禁止 public bucket | 0 通过；2 失败；3 未就绪；4 安全守卫拒绝 |

CLI 入口（在 `src/find_yourself/cli.py`）：

```powershell
$env:PYTHONPATH = "<project>\src"
.\.venv\Scripts\python.exe -m find_yourself.cli doctor --format json
.\.venv\Scripts\python.exe -m find_yourself.cli backup --target <dir> --manifest <out.json>
.\.venv\Scripts\python.exe -m find_yourself.cli restore --manifest <in.json> --environment recovery
.\.venv\Scripts\python.exe -m find_yourself.cli verify-deletions --environment recovery
```

退出码：0 成功 / 1 参数或内部错误 / 2 存在 FAIL / 3 服务未就绪 NOT_RUN / 4 安全守卫拒绝。
