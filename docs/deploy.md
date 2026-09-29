# 上线部署清单

从代码到一个同学能访问的站点。服务器约 ¥25–35/月，其余全部免费。

本文只写**线上模式**。只给自己用的话走 [local-development.md](local-development.md)，不需要服务器、
域名和 SMTP，也不用看这篇。

> 本文的免费额度和限制是 2026-09-29 核对过的。**服务器价格是第三方渠道的报价，未经阿里云
> 官方定价页核实**——中文 VPS 内容返利含量很高，同一套餐常见互相矛盾的配置说明。下单前一律
> 以购买页显示的配置和续费价为准。

---

## 1. 你需要凑齐的四样东西

| | 用什么 | 成本 |
| --- | --- | --- |
| 服务器 | 阿里云轻量应用服务器 · **中国香港** | ~¥25–35/月 |
| 域名 | GitHub 学生包的 Namecheap 免费域名 | 0（首年） |
| HTTPS | Caddy 自动申请 Let's Encrypt | 0 |
| SMTP | Brevo 免费档 | 0 |

PostgreSQL 在 compose 里自带，不用另外买。

### 为什么是香港节点

**境内节点必须 ICP 备案，这是硬阻塞。** 备案不区分端口——以为换成 8080 就能绕过去是常见
误解，只要域名解析指向中国内地服务器就得备案，未备案不允许开通访问。备案需要大陆实体或
身份，周期以周计。而且学生包那个 `.me` 域名还要求注册商有资质（阿里云、新网），Namecheap
注册的得先转入，学生免费域名通常还有 60 天转移锁。

香港节点不在备案范围内，域名解析过去直接可用。香港到悉尼约 130ms，比本地节点慢但完全够用；
如果有同学在国内，香港的体验反而比悉尼好。

### 为什么不是那些免费方案

- **Oracle Always Free** 是唯一真正永久免费且够用的，但注册审核经常过不了（多卡在信用卡
  验证或区域风控）。能开出来的话它更优：悉尼节点、2 OCPU / 12 GB、0 元
- **GitHub 学生包**里没有适合跑 7×24 服务的托管额度了。DigitalOcean 已于 2026-08-01 退出
  学生包且额度全部作废；Azure 的 $100 是有限额度，2GB 机型约 3 个月烧完；Heroku 文件系统
  是临时的，存不住课件；CamberCloud 每月 40 CPU 小时不够常驻
- **GCP / AWS 免费档**只有 1GB 内存，跑不动 api + worker + Postgres 三个容器

学生包在这套方案里贡献的是**域名**，不是服务器。

---

## 2. 开服务器（阿里云轻量应用服务器）

阿里云控制台 → 轻量应用服务器 → 创建。

**地域选「中国香港」**——这一项决定了要不要备案，选错了后面全白做。

配置上只有两个数字需要认真对待：

- **内存 ≥ 2GB**。便宜套餐里有不少是 0.5G 或 1G，那些跑不起来：三个容器加上构建镜像会直接
  OOM。别只看月费，先看内存
- **系统盘 ≥ 40GB**。课件下载下来要占空间，20G 偏紧

镜像选**系统镜像 → Ubuntu 24.04**，不要选应用镜像（那些预装了用不上的面板）。

创建完还有两件事：

1. **防火墙**：轻量控制台的防火墙里放行 **80** 和 **443**。默认只开了 22
2. **登录方式**：默认是密码登录，建议在控制台绑定密钥对，然后关掉密码登录

> **下单前看一眼续费价格。** 阿里云的惯例是首年促销价低、续费回原价，购买页会显示续费价，
> 差距可能不小。另外轻量**升配容易、降配基本不行**，别买了大的指望以后降。

### 架构说明

轻量是 x86_64 机器，和本文验证部署链路时用的架构一致，依赖不会有兼容性问题。

> 如果你以后换到 Oracle 的 ARM 机器：`requirements.lock` 里 58 个依赖已逐个核对过，
> 42 个纯 Python、16 个有 linux aarch64 wheel（含 PyMuPDF、cryptography、pydantic_core、
> lxml、pillow），0 个需要源码编译；三个基础镜像也都有 arm64 变体。ARM 上不会因为缺 wheel
> 而构建失败。

---

## 3. 装 Docker

```bash
ssh root@<你的服务器公网IP>      # 阿里云 Ubuntu 镜像默认用户是 root

sudo apt-get update && sudo apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exec su -l $USER          # 重新登录让用户组生效
docker --version
```

### 加 swap（2GB 内存必做）

构建镜像要编译前端再装整个 Python 依赖集，2GB 内存跑这一步很容易 OOM。先加 2GB swap：

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h          # 确认 Swap 那行不是 0
```

写进 `/etc/fstab` 是为了重启后仍然生效，别省这一步。

### 确认端口通了

阿里云的放行在**控制台防火墙**里做（第 2 节那步），系统镜像默认不额外带主机防火墙。
如果配好 Caddy 后外网仍然打不开，回头查一下主机这层：

```bash
sudo iptables -L INPUT -n | head     # 有没有 DROP/REJECT 规则
sudo ufw status                      # 装了 ufw 的话是否 active
```

ufw 是 active 的话放行：

```bash
sudo ufw allow 80/tcp && sudo ufw allow 443/tcp
```

---

## 4. 域名

GitHub 学生包的 Namecheap 给一个免费 `.me` 域名（含 SSL），一年。
Name.com 和 `.tech` 也各有一个，都能用。

> Namecheap 这项有地区限制，只对美/英/加/澳的学校开放。UTS 在澳洲，符合条件。
> 以你自己 Student Pack 面板里显示的为准。

拿到域名后加一条 A 记录指向轻量实例的公网 IP。用 Cloudflare 的免费 DNS 托管也行。

> **免备案的前提是解析指向香港 IP。** 哪天你把这个域名指回境内节点，备案义务立刻回来，
> 站点会被挡掉。换服务器时记着这条。

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
- [ ] SSH 用密钥登录，关掉密码登录（轻量默认开密码登录，这条别跳过）
- [ ] 轻量控制台防火墙只放行了 22 / 80 / 443，没有多余端口

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
