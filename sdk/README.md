# FindYourself Standard API & Multi-Language SDKs

> A 线 P12 交付物 · 市场与生态（A-生态兼容-01 · A-生态兼容-04 · A-工具市场-03 · A-开箱模板-04 · A-开箱模板-06）

本目录包含 FindYourself 面向外部生态、开发者与企业系统的统一 SDK 套件与 OpenAPI 规范工具。

---

## 目录结构

```
sdk/
├── openapi_generator.py      # OpenAPI 3.1 规格导出与 SDK 契约对齐校验脚本
├── openapi.json              # 导出的标准 OpenAPI 规格定义
├── python/                   # Python 标准 SDK (find-yourself-sdk)
├── javascript/               # JavaScript / TypeScript 标准 SDK (@findyourself/sdk)
└── java/                     # Java 17+ 标准 SDK (com.findyourself.sdk)
```

---

## 核心功能矩阵

| 能力 | 说明 | 对应需求 |
|---|---|---|
| **插件市场与评分** | 插件检索、打分（1-5星）、贝叶斯综合排序（好用的自然被顶上来） | A-工具市场-03 |
| **模板三层分层** | 新手默认层 / 进阶可换场景矩阵 / 一级技术入口（可拆模板改代码） | A-开箱模板-04 🔒 GATE |
| **模板导入导出与安全审查** | 单文件 `.fytemplate` 导出、SHA-256 防篡改校验、高危工具拦截与权限裁剪 | A-开箱模板-06 |
| **Cursor 级代码上下文** | AST / 符号深度提取、相关性代码切片检索、字符预算自适应高密度上下文装配 | A-生态兼容-04 |
| **企业 WebApp iframe 嵌入** | 带安全签名与时效性的 iframe 嵌入 URL 生成、多租户隔离、SSO 传参 | A-生态兼容-01 |

---

## 快速使用

### 1. Python SDK

```python
from find_yourself_sdk import FindYourselfClient, generate_iframe_embed_url

client = FindYourselfClient("http://localhost:8000", token="YOUR_TOKEN")

# 1. 浏览插件市场（好用的自然被顶上来）
plugins = client.list_plugins(query="search", sort_by="score")
print(plugins["items"])

# 2. 为插件打分
client.rate_plugin("my-plugin-id", rating=5.0, comment="非常稳定！")

# 3. 查看模板分层体系
hierarchy = client.get_template_hierarchy()
print("三层体系:", [layer["id"] for layer in hierarchy["layers"]])

# 4. 生成企业 WebApp iframe 嵌入 URL
embed_url = client.generate_embed_url(page="marketplace", user_id="emp_001")
print("嵌入地址:", embed_url)
```

### 2. JavaScript / TypeScript SDK

```typescript
import { FindYourselfClient, generateIframeEmbedUrl } from '@findyourself/sdk';

const client = new FindYourselfClient({
  baseUrl: 'http://localhost:8000',
  token: 'YOUR_TOKEN',
});

// 检索模板市场并按评分排序
const templates = await client.listTemplates({ sortBy: 'score' });

// Cursor 级代码上下文装配
const context = await client.assembleCodeContext('find user method', {
  'user.py': 'def get_user(): pass',
});
```

### 3. Java SDK

```java
import com.findyourself.sdk.FindYourselfClient;
import com.findyourself.sdk.EmbedHelper;

FindYourselfClient client = new FindYourselfClient("http://localhost:8000", "YOUR_TOKEN", "CSRF", "tenant-1");
String result = client.listPlugins("search", "score");

// 企业 WebApp 嵌入 URL
String embedUrl = client.generateEmbedUrl("marketplace", "user_101", "light");
```

---

## OpenAPI 规格导出与更新

在仓库根目录下运行：

```bash
python sdk/openapi_generator.py
```

该命令将自动启动 FastAPI 路由注册，将完整的 OpenAPI 规范导出到 `sdk/openapi.json`，并校验 P12 生态路由覆盖完整性。
