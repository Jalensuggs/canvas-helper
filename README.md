# Canvas 助手

本地优先的 Canvas 学习助手。同步本学期课程、作业截止时间、公告和课程资料，支持搜索、文件预览、笔记和引用式 AI。

可以自己本机用，也可以部署成网站：别人注册后填入自己的 Canvas API Token 即可使用。

## 功能

- 本学期课程、作业、日历待办和教师/Tutor 公告
- 资料库：页面、作业说明、Word / PDF / Excel 预览与全文检索
- 自动同步：公告、作业、日历约 5 分钟；资料约 15 分钟
- Markdown 笔记，可关联课程、作业或资料
- 引用式 AI（用户自备 Anthropic / OpenAI Key）
- 两种运行方式：本机桌面 / 多用户自托管

## 本机使用

需要 Python 3.12+、Node.js 20+。

```bash
make install
```

开两个终端：

```bash
make backend
make frontend
```

打开 <http://127.0.0.1:5173>，在设置向导中填入 Canvas 地址和访问令牌。

令牌保存在本机钥匙串，不会回显，也不会写入仓库。

```bash
make test
```

## 上线给别人用

线上模式是多用户网站：邮箱魔法链接登录，每人自己绑定 Canvas Token 和可选的 AI Key。

下面是概要。**完整的一步步手册见 [docs/deploy.md](docs/deploy.md)**，里面有服务商对比、
备案说明、DNS 和发信域名验证、以及实测过的验证清单。

生产环境必须同时满足：

- HTTPS 域名
- PostgreSQL
- SMTP（用来发登录邮件）
- 32 字节加密密钥（用来加密用户的 Canvas / AI 凭证）

缺任何一项服务都不会启动。

### 1. 准备一台服务器

Linux VPS，装好 Docker 和 Docker Compose，域名解析到这台机器。

**内存至少 2 GB，并且要加 swap。** 构建镜像时要编译前端再装整个 Python 依赖集，
峰值超过 1 GB，2 GB 机器不加 swap 会在这一步被 OOM 杀掉：

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

跑起来之后三个容器合计约 300 MB，压力主要在构建那一下。

### 2. 克隆并配置

```bash
git clone https://github.com/Jalensuggs/canvas-helper.git
cd canvas-helper
cp .env.example .env
```

编辑 `.env`，至少填这些：

```bash
POSTGRES_PASSWORD=换成很长的随机密码
CANVAS_HELPER_PUBLIC_URL=https://canvas.yourdomain.com
CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=见下一步生成
CANVAS_HELPER_EMAIL_FROM=Canvas Helper <noreply@yourdomain.com>
CANVAS_HELPER_SMTP_HOST=smtp.yourdomain.com
CANVAS_HELPER_SMTP_PORT=587
CANVAS_HELPER_SMTP_USERNAME=你的发信账号
CANVAS_HELPER_SMTP_PASSWORD=你的发信密码
```

**强烈建议同时限制谁能注册。** 不设置的话，任何知道网址的人都能注册账号：

```bash
# 只允许这些邮箱域名登录（逗号分隔）
CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS=student.uts.edu.au
```

登录邮件接口默认已经限流（每个邮箱每小时 5 封、每个 IP 每小时 20 封），
可以用 `CANVAS_HELPER_MAGIC_LINK_PER_EMAIL_PER_HOUR` 和
`CANVAS_HELPER_MAGIC_LINK_PER_IP_PER_HOUR` 调整。

如果学校不是 UTS，改一下学期推断用的时区和开学月份：

```bash
CANVAS_HELPER_ACADEMIC_TIMEZONE=Asia/Shanghai
CANVAS_HELPER_TERM_START_MONTHS=3,9
```

生成加密密钥：

```bash
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

SMTP 可以用 Brevo、Resend、Amazon SES 这类发信服务，或 Gmail 应用专用密码。
没有能发出去的登录邮件，别人就登不进去。

**光有 SMTP 凭据不够，还要在发信服务里验证发件域名。** 用未验证的域名发信会被直接拒绝，
而接口对用户永远返回 202（防邮箱枚举），页面上看不出任何异常。发信成没成只能看日志：

```bash
docker compose logs api | grep -i "delivery failed"
```

验证域名要在发信服务后台拿几条 DNS 记录加到域名商那里，步骤见
[docs/deploy.md](docs/deploy.md)。

### 3. 启动应用

```bash
docker compose --env-file .env up -d --build
docker compose ps
```

容器默认只把 API 绑在本机 `127.0.0.1:8000`，不要把 8000、5432 直接暴露到公网。

### 4. 前面加 HTTPS

把 `docker/Caddyfile.example` 里的域名改成你的，然后用 Caddy 或 Nginx 反代到 `127.0.0.1:8000`。

Caddy 示例：

```bash
sudo cp docker/Caddyfile.example /etc/caddy/Caddyfile
# 改域名后
sudo systemctl reload caddy
```

证书由 Caddy 自动申请。确认 `CANVAS_HELPER_PUBLIC_URL` 和浏览器访问的 HTTPS 地址完全一致。

### 5. 别人怎么用

1. 打开你的网站，输入邮箱
2. 查收魔法链接并登录
3. 在设置里填 Canvas 地址和 API Token
4. 等待第一次同步，之后会自动更新公告、作业和资料

Canvas Token 只存在服务器加密存储里，不会出现在页面或日志中。

### 6. 更新

```bash
git pull
docker compose --env-file .env up -d --build
```

数据库迁移会在 API 容器启动时自动执行。备份见 [docs/backup-upgrade.md](docs/backup-upgrade.md)。

## 环境变量

只读取 `CANVAS_HELPER_` 开头的环境变量，不会自动加载 `.env` 文件。Docker Compose 会把 `.env` 注入容器。

| 变量 | 说明 |
| --- | --- |
| `CANVAS_HELPER_DEPLOYMENT_MODE` | `local_desktop` 或 `server` |
| `CANVAS_HELPER_DATABASE_URL` | 本机 SQLite，或线上 `postgresql+asyncpg://...` |
| `CANVAS_HELPER_PUBLIC_URL` | 线上 HTTPS 根地址 |
| `CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY` | 线上必填，丢失后旧凭证无法解密 |
| `CANVAS_HELPER_EMAIL_BACKEND` | 开发用 `development`，线上必须 `smtp` |
| `CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS` | 注册邮箱域名白名单（逗号分隔），留空表示不限制 |
| `CANVAS_HELPER_MAGIC_LINK_PER_EMAIL_PER_HOUR` | 单邮箱每小时登录邮件上限，`0` 关闭 |
| `CANVAS_HELPER_MAGIC_LINK_PER_IP_PER_HOUR` | 单来源每小时登录邮件上限，`0` 关闭 |
| `CANVAS_HELPER_ACADEMIC_TIMEZONE` | 学期推断用的 IANA 时区 |
| `CANVAS_HELPER_TERM_START_MONTHS` | 开学月份（逗号分隔，如 `1,7`） |
| `CANVAS_HELPER_SMTP_USE_SSL` | 465 端口的隐式 TLS；端口为 465 时自动开启 |
| `CANVAS_HELPER_SYNC_JOB_RETENTION_DAYS` | 同步任务记录保留天数，`0` 关闭清理 |

完整示例见 [.env.example](.env.example)。

## 上线前检查

```bash
make check          # ruff + pytest + 前端类型检查/测试 + 前端构建 + compose 校验
```

- `requirements.lock` 固定了服务端镜像的依赖版本。改过 `pyproject.toml` 后跑 `make lock` 重新生成，否则镜像重建时会装到不同的版本。
- 镜像安装的是 `.[ai]`，所以线上模式的 AI 功能可用。去掉这个 extra 的话 `/api/ai/chat` 会一直返回 503。
- 数据库迁移在 API 容器启动时执行；worker 容器通过 `CANVAS_HELPER_RUN_MIGRATIONS=false` 跳过。
- PostgreSQL 上的中文检索依赖 `pg_trgm` 扩展。迁移会尝试创建它；如果数据库账号权限不足，迁移不会失败，但中文搜索会退化成全表扫描。授权后重跑 `alembic upgrade head` 即可建好索引。

## 安全

- 本机模式只监听 `127.0.0.1`，写请求还要可信 Origin
- 线上模式用 HttpOnly Session + CSRF，凭证 AES-GCM 加密
- 登录邮件接口有按邮箱和按来源的限流，可选邮箱域名白名单
- Canvas HTML 会净化；下载路径会检查穿越和符号链接
- 反代示例已带 CSP、HSTS 和同源 frame 限制
- 不要把 PostgreSQL、MinIO 或 8000 端口直接对公网开放
- `*.env`、数据库、下载资料和密钥目录默认被 Git 忽略
- Canvas Token 只应该从设置页填入，永远不要写进仓库里的文件或环境变量

更多见 [PRIVACY.md](PRIVACY.md)、[SECURITY.md](SECURITY.md)、[docs/threat-model.md](docs/threat-model.md)。

## 文档

- [上线部署清单](docs/deploy.md)
- [本地开发](docs/local-development.md)
- [Docker 自托管](docs/docker-self-host.md)
- [桌面打包](docs/desktop-release.md)
- [备份与升级](docs/backup-upgrade.md)
- [贡献](CONTRIBUTING.md)

MIT License。
