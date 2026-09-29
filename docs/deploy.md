# 上线部署清单

从代码到一个同学能访问的站点。总成本 0 元/月。

本文只写**线上模式**。只给自己用的话走 [local-development.md](local-development.md)，不需要服务器、
域名和 SMTP，也不用看这篇。

> 各家免费额度变动很快。本文的数字是 2026-09-29 核对过的，下单前请以官网当时的条款为准。

---

## 0. 前置：先合并部署修复

**这一步不能跳。** `main` 当前的代码在容器里起不来。

两处路径都用 `Path(__file__).resolve().parents[2]` 定位资源。源码检出时这是仓库根目录，
所以本地开发一切正常；但镜像把包装进 site-packages 后，这个路径变成
`/opt/venv/lib/python3.12`，既没有 `migrations/` 也没有 `frontend/dist`：

- **迁移**：API 容器启动时崩溃，陷入重启循环，worker 等不到健康检查也起不来
- **前端**：`/health` 和整个 `/api` 都正常，但每个页面返回 404——看起来像反代配错了，很难查

修复在 `claude/lucid-brahmagupta-8cjn4e` 分支。**先把 PR 合进 `main` 再往下走。**

---

## 1. 你需要凑齐的四样东西

| | 用什么 | 成本 |
| --- | --- | --- |
| 服务器 | Oracle Cloud Always Free（ARM） | 0 |
| 域名 | GitHub 学生包的 Namecheap 免费域名 | 0（首年） |
| HTTPS | Caddy 自动申请 Let's Encrypt | 0 |
| SMTP | Brevo 免费档 | 0 |

PostgreSQL 在 compose 里自带，不用另外买。

**注意**：DigitalOcean 已于 2026-08-01 退出 GitHub 学生包，$200 额度全部作废。
学生包里现在没有适合跑 7×24 服务的托管额度（Azure 的 $100 是有限额度；Heroku 文件系统
是临时的，存不住课件；CamberCloud 每月 40 CPU 小时不够常驻），所以服务器走 Oracle。

---

## 2. 开服务器（Oracle Always Free）

1. 注册 <https://cloud.oracle.com/>，选 Always Free。信用卡只做实名验证，不扣费。
   注册完**确认账号类型是 Always Free 而不是 Pay As You Go**
2. **区域选 Sydney**：延迟低（Canvas 本身在 `canvas.uts.edu.au`），而且 APAC 区的 ARM
   容量明显比美国区好开
3. Compute → Create Instance：
   - Shape：`VM.Standard.A1.Flex`
   - 规格：**2 OCPU / 12 GB**（Oracle 在 2026 年把免费额度砍半了，这是现在的上限）
   - 镜像：Ubuntu 24.04
   - **SSH 私钥当场下载保存**，只有这一次机会
4. 网络 → VCN → Security List：加 **80** 和 **443** 的入站规则
5. 抢不到容量就换可用域重试，或过几小时再试。**别为此升级成 Pay As You Go**

### ARM 兼容性（已核对）

Oracle 免费的是 ARM 机器。已逐个核对 `requirements.lock` 里 58 个依赖：

- 42 个纯 Python 包（与架构无关）
- 16 个有 linux aarch64 wheel（含 PyMuPDF、cryptography、pydantic_core、lxml、pillow）
- **0 个需要源码编译**

三个基础镜像 `python:3.12-slim-bookworm`、`node:22-bookworm-slim`、`postgres:17-alpine`
都有 arm64 变体。ARM 上构建不会因为缺 wheel 而变慢或失败。

> 说明：这份清单是在 x86 上验证的部署链路 + 对 ARM 依赖可用性的核对，
> 不是在 ARM 机器上的实测。

---

## 3. 装 Docker

```bash
ssh ubuntu@<你的服务器IP>

sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exec su -l $USER          # 重新登录让用户组生效
docker --version
```

Ubuntu 镜像自带一层 iptables，**不放行的话安全组开了也通不了**：

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

---

## 4. 域名

GitHub 学生包的 Namecheap 给一个免费 `.me` 域名（含 SSL），一年。
Name.com 和 `.tech` 也各有一个，都能用。

> Namecheap 这项有地区限制，只对美/英/加/澳的学校开放。UTS 在澳洲，符合条件。
> 以你自己 Student Pack 面板里显示的为准。

拿到域名后加一条 A 记录指向服务器公网 IP。用 Cloudflare 的免费 DNS 托管也行。

不想用学生包的话，[DuckDNS](https://www.duckdns.org/) 的免费子域名一样能跑，
Caddy 申请证书没区别。

---

## 5. SMTP

登录靠邮件魔法链接，**没有能发出去的 SMTP 就没人能登录**。

| 服务 | 免费额度 |
| --- | --- |
| **Brevo** | 300 封/天（约 9000 封/月）——额度最大 |
| Resend | 3000 封/月，100 封/天，限 1 个域名 |
| MailerSend | 500 封/月，100 封/天 |

登录邮件量很小，100 封/天够几十个人用。注册后在后台拿 SMTP 主机、端口、用户名、密码。

> SMTP 密码不是你的登录密码，是后台单独生成的 SMTP key。这是最常见的配置错误。

---

## 6. 部署

```bash
git clone https://github.com/Jalensuggs/canvas-helper.git
cd canvas-helper
cp .env.example .env

# 生成 32 字节加密密钥
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

# 生成 Postgres 密码
python3 -c "import secrets; print(secrets.token_urlsafe(24))"
```

编辑 `.env`（**值不要加引号，列表用逗号分隔**）：

```bash
POSTGRES_PASSWORD=上面生成的随机密码
CANVAS_HELPER_DEPLOYMENT_MODE=server
CANVAS_HELPER_ENVIRONMENT=production
CANVAS_HELPER_DATABASE_URL=postgresql+asyncpg://canvas:上面那个密码@postgres:5432/canvas
CANVAS_HELPER_PUBLIC_URL=https://你的域名
CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=上面生成的密钥

CANVAS_HELPER_EMAIL_BACKEND=smtp
CANVAS_HELPER_EMAIL_FROM=Canvas Helper <noreply@你的域名>
CANVAS_HELPER_SMTP_HOST=smtp-relay.brevo.com
CANVAS_HELPER_SMTP_PORT=587
CANVAS_HELPER_SMTP_USERNAME=你的SMTP用户名
CANVAS_HELPER_SMTP_PASSWORD=你的SMTP密码

# ⚠️ 不设这行，任何知道网址的人都能注册
CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS=student.uts.edu.au

# 不是 UTS 的话改这两行
CANVAS_HELPER_ACADEMIC_TIMEZONE=Australia/Sydney
CANVAS_HELPER_TERM_START_MONTHS=1,7
```

配置不全应用会**拒绝启动**，这是故意的。

```bash
docker compose --env-file .env up -d --build
docker compose ps
```

三个容器都应该是 `running`/`healthy`：`api`、`worker`、`postgres`。

第一次构建要编译前端并安装整个 Python 依赖集，会跑一阵，属正常。

---

## 7. HTTPS

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

sudo cp docker/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # 把 canvas.example.com 换成你的域名
sudo systemctl reload caddy
```

Caddy 自动申请 Let's Encrypt 证书。仓库里的示例已带 HSTS、CSP、同源 frame 限制。

---

## 8. 验证

以下命令在本地验证过实际返回值，按顺序走，每步确认通过再往下。

```bash
# 1. 容器都活着，api 是 healthy
docker compose ps

# 2. 本机能通
curl -s http://127.0.0.1:8000/health
# 期望 {"status":"ok"}

# 3. 外网 HTTPS 能通
curl -s https://你的域名/health
# 期望 {"status":"ok"}

# 4. 未登录访问被挡
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/api/courses
# 期望 401

# 5. 域名白名单生效
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://你的域名/api/auth/request-link \
  -H 'Content-Type: application/json' -d '{"email":"someone@gmail.com"}'
# 期望 403

# 6. 白名单内邮箱被接受
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://你的域名/api/auth/request-link \
  -H 'Content-Type: application/json' -d '{"email":"你@student.uts.edu.au"}'
# 期望 202

# 7. 网页能打开（必须带 Accept 头，不带会返回 404 JSON，这是内容协商，正常）
curl -s -o /dev/null -w '%{http_code}\n' -H 'Accept: text/html' https://你的域名/
# 期望 200
```

第 7 步如果是 404：先确认合并了第 0 节的修复。修复前这一步必然 404。

### 最关键：确认邮件真的发得出去

第一次部署最容易挂在 SMTP 上。在网页上用**允许的**邮箱请求一次登录链接，然后看日志：

```bash
docker compose logs api | grep -i "delivery failed"
```

- **没有输出** → 发信成功，去收件箱（和垃圾邮件箱）找
- **有 `Magic-link delivery failed`** → 下面有完整堆栈，按报错改 `.env`

| 现象 | 原因 |
| --- | --- |
| `ConnectionRefusedError` | 主机或端口错了 |
| `SMTPAuthenticationError` | 用户名/密码错了（是 SMTP key，不是登录密码） |
| 连接卡住然后超时 | 端口 465 要设 `CANVAS_HELPER_SMTP_USE_SSL=true` |
| 发送成功但收不到 | 进垃圾箱了，或发件域名没验证 |

> 接口对用户**永远返回 202**，不会因为发信失败而报错——这是防止别人拿它探测
> 哪些邮箱注册过。所以**只能从日志判断**发信是否成功。

---

## 9. 上线前安全检查

- [ ] `CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS` 已设置（否则开放注册）
- [ ] `POSTGRES_PASSWORD` 是长随机串，不是 `change-me`
- [ ] 加密密钥已备份到密码管理器
- [ ] 5432 和 8000 **没有**暴露到公网（compose 默认只绑 127.0.0.1）
- [ ] HTTPS 证书正常，`CANVAS_HELPER_PUBLIC_URL` 和浏览器地址完全一致
- [ ] `.env` 没有被提交进 Git（`.gitignore` 里已排除）
- [ ] SSH 用密钥登录，关掉密码登录

登录接口默认已限流：每邮箱每小时 5 封、每 IP 每小时 20 封，
可用 `CANVAS_HELPER_MAGIC_LINK_PER_EMAIL_PER_HOUR` 和
`CANVAS_HELPER_MAGIC_LINK_PER_IP_PER_HOUR` 调整。

---

## 10. 日常运维

```bash
# 看日志
docker compose logs -f api
docker compose logs -f worker

# 更新
git pull
docker compose --env-file .env up -d --build

# 重启
docker compose restart api worker
```

数据库迁移在 api 容器启动时自动执行，worker 等 api 健康后再起。

### 备份

```bash
# 数据库
docker compose exec -T postgres pg_dump -U canvas canvas > backup-$(date +%F).sql

# 课件和附件
docker run --rm -v canvas-helper_app_data:/data -v "$PWD":/backup \
  alpine tar czf /backup/app_data-$(date +%F).tar.gz -C /data .
```

**加密密钥要单独存**（密码管理器里）。没有 `CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY`
的数据库备份**无法**解出用户凭证——密钥丢了，所有人都得重新填 Canvas Token。

更多见 [backup-upgrade.md](backup-upgrade.md)。

---

## 附录：为什么不能用 Vercel / Render / GitHub Pages

这个应用需要四样东西：常驻的后台 worker 进程（每 5~15 分钟同步 Canvas）、
持久磁盘（存下载的课件）、PostgreSQL、SSE 长连接。免费的 Serverless / 容器平台
通常这四样至少缺两样。

- **GitHub Pages**：只托管静态文件，没有服务端运行时
- **Vercel**：依赖 294 MB 超过 Python 函数 250 MB 上限；函数执行完即销毁，没有常驻进程；
  文件系统临时；不提供数据库，SSE 会被执行时长上限掐断
- **Render 免费版**：不支持持久磁盘，后台 worker 最低 $7/月，免费 Postgres 30 天过期

前端理论上可以单独发到 Vercel/Pages，但那只是个连不上后端的空壳，而且要把同源
Cookie + CSRF 改成跨域 CORS，等于拆掉现有的安全模型。
