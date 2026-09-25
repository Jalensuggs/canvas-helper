# 部署上线指南

从零把 Canvas 助手跑起来，包含一条**完全免费**的路径。

> 各家云厂商的免费额度变动很快。本文里的数字是 2026-09 核对过的，但下单前
> 请以官网当时的条款为准。文末列了来源。

---

## 0. 先想清楚：你到底需不需要服务器

这个项目有两种运行方式，**大多数人其实只需要第一种**。

| | 本机模式 `local_desktop` | 线上模式 `server` |
| --- | --- | --- |
| 谁能用 | 只有你自己 | 任何注册的人 |
| 需要服务器 | 不需要 | 需要 |
| 需要域名 / HTTPS | 不需要 | 需要 |
| 需要 PostgreSQL | 不需要（SQLite） | 需要 |
| 需要 SMTP 发信 | 不需要 | 需要（登录靠邮件） |
| 费用 | **0** | 见第 2 节 |
| Token 存在哪 | 你本机的系统钥匙串 | 服务器上 AES-GCM 加密 |

**如果只是你自己用，走第 1 节，到此为止，一分钱不花，也不用管后面所有内容。**

只有当你要让同学也能用的时候，才需要第 2 节往后。

---

## 1. 本机模式（免费，5 分钟）

需要 Python 3.12+ 和 Node.js 20+。

```bash
git clone https://github.com/Jalensuggs/canvas-helper.git
cd canvas-helper
make install
```

开两个终端：

```bash
make backend     # 终端 1
make frontend    # 终端 2
```

打开 <http://127.0.0.1:5173>，在设置向导里填 Canvas 地址和 API Token。

Token 存进本机系统钥匙串，不回显、不进仓库、不出你的电脑。

想要一个真正的桌面应用（不用开终端）：

```bash
make install-desktop
make desktop-build      # 产物在 src-tauri/target/release/
```

---

## 2. 线上模式需要什么

四样东西缺一不可，配置不全应用会**拒绝启动**（这是故意的）：

1. **一台能跑 Docker 的服务器**
2. **一个域名 + HTTPS**
3. **PostgreSQL**（compose 里自带，不用另外买）
4. **能发出邮件的 SMTP**（登录链接靠它，没有就没人能登录）

### 免费方案对比

| 方案 | 能不能白嫖 | 说明 |
| --- | --- | --- |
| **Oracle Cloud Always Free** | ✅ 真免费 | 2 OCPU / 12 GB ARM，或 2 台 AMD 微型机。**推荐** |
| **GitHub 学生包** | ✅ 一年左右 | DigitalOcean 等厂商的额度 + Namecheap 免费域名，UTS 邮箱可申请 |
| 自己的旧电脑 / 树莓派 | ✅ | 家宽通常封 80/443，需要内网穿透 |
| Render 免费版 | ❌ **不行** | 免费版不支持持久磁盘，后台 worker 最低 $7/月，免费 Postgres 30 天过期 |
| Vercel | ❌ **不行** | 见附录 A |
| GitHub Pages | ❌ **不行** | 见附录 A |
| Fly.io / Railway | ⚠️ 要自己核 | 免费额度这两年一直在缩，下单前看清楚 |

### 为什么 Render / Vercel 这类不行

这个应用需要：一个常驻的后台 worker 进程（每 5~15 分钟同步 Canvas）、一块
持久磁盘（存下载的课件）、一个 PostgreSQL、以及 SSE 长连接。免费版的
Serverless / 容器平台通常这四样至少缺两样。详见附录 A。

---

## 3. 完全免费的线上部署（Oracle Cloud）

总成本 **0 元/月**。需要一张信用卡做实名验证，但不扣费。

### 3.1 开机器

1. 注册 <https://cloud.oracle.com/> 的 Always Free
2. 创建 Compute 实例：
   - Shape：`VM.Standard.A1.Flex`（ARM），**2 OCPU / 12 GB**
   - 镜像：Ubuntu 22.04 或 24.04
   - 保存好 SSH 私钥
3. 放行端口：VCN → Security List 加入 **80** 和 **443** 的入站规则

> ARM 机型在热门区域经常显示 "out of capacity"，多试几次或换个可用域。
> 实在开不到就用 2 台 AMD 微型机（性能够用，只是配置低一些）。

### 3.2 装 Docker

```bash
ssh ubuntu@<你的服务器IP>

sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exec su -l $USER          # 重新登录让用户组生效
docker --version
```

Ubuntu 默认还有一层 iptables 挡着，要放行：

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

### 3.3 弄一个免费域名

最省事的是 [DuckDNS](https://www.duckdns.org/)：用 GitHub 账号登录，建一个
子域名（比如 `canvas-yourname.duckdns.org`），把 IP 填成服务器公网 IP。

有自己的域名更好（学生包的 Namecheap 免费域名也行），Cloudflare 免费 DNS
加一条 A 记录指过去即可。

### 3.4 申请免费 SMTP

| 服务 | 免费额度（2026-09 核对） |
| --- | --- |
| **Brevo** | 300 封/天（约 9000 封/月）——额度最大 |
| Resend | 3000 封/月，100 封/天，限 1 个域名 |
| MailerSend | 500 封/月，100 封/天 |

登录邮件量很小，100 封/天完全够几十个人用。注册后在后台拿到 SMTP 主机、
端口、用户名、密码。

> 也可以用学校邮箱或 Gmail 应用专用密码，但容易被判垃圾邮件或限流，
> 不建议长期用。

### 3.5 部署

```bash
git clone https://github.com/Jalensuggs/canvas-helper.git
cd canvas-helper
cp .env.example .env

# 生成 32 字节加密密钥
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

编辑 `.env`，把下面这些填全（注意：**没有引号，逗号分隔**）：

```bash
POSTGRES_PASSWORD=换成很长的随机密码
CANVAS_HELPER_DEPLOYMENT_MODE=server
CANVAS_HELPER_ENVIRONMENT=production
CANVAS_HELPER_DATABASE_URL=postgresql+asyncpg://canvas:上面那个密码@postgres:5432/canvas
CANVAS_HELPER_PUBLIC_URL=https://canvas-yourname.duckdns.org
CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=刚才生成的那串

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

启动：

```bash
docker compose --env-file .env up -d --build
docker compose ps
```

三个容器都应该是 `running`/`healthy`：`api`、`worker`、`postgres`。

### 3.6 配 HTTPS

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

sudo cp docker/Caddyfile.example /etc/caddy/Caddyfile
sudo nano /etc/caddy/Caddyfile      # 把 canvas.example.com 改成你的域名
sudo systemctl reload caddy
```

Caddy 会自动申请 Let's Encrypt 证书。仓库里的示例已经带了 HSTS、CSP、
同源 frame 限制这些安全头。

---

## 4. 部署后必做的验证

按顺序走一遍，每一步都确认通过再往下：

```bash
# 1. 容器都活着
docker compose ps

# 2. 本机能通
curl -s http://127.0.0.1:8000/health          # 期望 {"status":"ok"}

# 3. 外网 HTTPS 能通
curl -s https://你的域名/health                # 期望 {"status":"ok"}

# 4. 未登录访问被挡
curl -s -o /dev/null -w '%{http_code}\n' https://你的域名/api/courses   # 期望 401

# 5. 域名白名单生效
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://你的域名/api/auth/request-link \
  -H 'Content-Type: application/json' -d '{"email":"someone@gmail.com"}'   # 期望 403
```

### 最关键的一步：确认邮件真的发得出去

第一次部署最容易挂在 SMTP 上。在网页上用**允许的**邮箱请求一次登录链接，
然后立刻看日志：

```bash
docker compose logs api | grep -i "delivery failed"
```

- **没有输出** → 发信成功，去收件箱（和垃圾邮件箱）找
- **有 `Magic-link delivery failed`** → 下面有完整堆栈，按报错改 `.env`

常见 SMTP 报错：

| 现象 | 原因 |
| --- | --- |
| `ConnectionRefusedError` | 主机或端口错了 |
| `SMTPAuthenticationError` | 用户名/密码错了（注意不是登录密码，是 SMTP key） |
| 连接卡住然后超时 | 端口 465 要设 `CANVAS_HELPER_SMTP_USE_SSL=true` |
| 发送成功但收不到 | 进垃圾箱了，或发件域名没验证 |

> 注意：接口对用户永远返回 `202`，**不会**因为发信失败而报错——这是防止
> 别人拿它探测哪些邮箱注册过。所以**只能从日志判断**发信是否成功。

---

## 5. 日常运维

```bash
# 看日志
docker compose logs -f api
docker compose logs -f worker

# 更新到最新版
git pull
docker compose --env-file .env up -d --build

# 重启
docker compose restart api worker
```

数据库迁移在 api 容器启动时自动执行，worker 容器会等 api 健康后再起。

### 备份

```bash
# 数据库
docker compose exec -T postgres pg_dump -U canvas canvas > backup-$(date +%F).sql

# 课件和附件
docker run --rm -v canvas-helper_app_data:/data -v "$PWD":/backup \
  alpine tar czf /backup/app_data-$(date +%F).tar.gz -C /data .
```

**加密密钥要单独存**（密码管理器里）。数据库备份在没有
`CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY` 的情况下**无法**解出用户凭证——
密钥丢了，所有人都得重新填 Canvas Token。

更多见 [backup-upgrade.md](backup-upgrade.md)。

---

## 6. 安全检查清单

上线前逐条确认：

- [ ] `CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS` 已设置（否则开放注册）
- [ ] `POSTGRES_PASSWORD` 是长随机串，不是 `change-me`
- [ ] 加密密钥已备份到密码管理器
- [ ] 5432 和 8000 端口**没有**暴露到公网（compose 默认只绑 127.0.0.1）
- [ ] HTTPS 证书正常，`CANVAS_HELPER_PUBLIC_URL` 和浏览器地址完全一致
- [ ] `.env` 没有被提交进 Git（`.gitignore` 里已排除）
- [ ] 服务器 SSH 用密钥登录，关掉密码登录

登录接口默认已限流：每个邮箱每小时 5 封、每个来源 IP 每小时 20 封，
可用 `CANVAS_HELPER_MAGIC_LINK_PER_EMAIL_PER_HOUR` 和
`CANVAS_HELPER_MAGIC_LINK_PER_IP_PER_HOUR` 调整。

---

## 附录 A：为什么 Vercel 和 GitHub Pages 用不了

**GitHub Pages** 只托管静态文件，没有任何服务端运行时。这个项目主体是
Python 后端，直接排除。

**Vercel** 是 Serverless，四个硬冲突：

| 项目需要 | Vercel 的限制 |
| --- | --- |
| 依赖 294 MB（PyMuPDF 64M、SQLAlchemy 28M、cryptography 16M…） | Python 函数上限 250 MB，装不下 |
| 常驻 worker 进程做定时同步 | 函数执行完即销毁，没有常驻进程 |
| 持久磁盘存下载的课件 | 文件系统临时，每次调用重置 |
| PostgreSQL + SSE 长连接 | 不提供数据库；函数有执行时长上限，SSE 会被掐断 |

前端理论上可以单独发到 Vercel/Pages，但那只是个连不上后端的空壳，而且要
把同源 Cookie + CSRF 改成跨域 CORS，等于拆掉现有的安全模型。不建议。

---

## 附录 B：来源

- [Oracle Always Free 资源文档](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Oracle Always Free 2026 额度调整说明](https://space-node.net/blog/oracle-vps-free-tier-review-2026)
- [Render 持久磁盘文档（付费专属）](https://render.com/docs/disks)
- [Render 免费额度说明](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026)
- [免费 SMTP 服务额度对比](https://www.emailtooltester.com/en/blog/free-smtp-servers/)
