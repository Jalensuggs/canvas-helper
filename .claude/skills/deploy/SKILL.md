---
name: deploy
description: 把 canvas-helper 部署或更新到自有的阿里云轻量服务器（香港节点），以及服务器到期、被释放或需要重建时，从零重新买机器并完整重建线上环境。当用户说"帮我部署上线""部署一下""更新服务器""上线新版本""服务器到期了""服务器没了""重新部署"，或询问线上环境怎么更新、怎么重建时使用。
---

# 部署到阿里云轻量（香港）

线上环境是用户自己的一台阿里云轻量应用服务器。这份 skill 记录它的样子、怎么更新、
以及机器没了之后怎么重建。

用中文回复用户。

## 先确认现状，别假设

每次动手前先弄清楚是哪种情况：

```bash
# 服务器还在吗（IP 从域名反查，重建后会变，所以不要记死）
nslookup canvas-myles.me 8.8.8.8
ssh -i ~/.ssh/canvas-helper.pem root@<上面查到的IP> 'uptime && docker compose -f ~/canvas-helper/docker-compose.yml ps'
```

- 能登录、容器在跑 → 走 **日常更新**
- 连不上、或用户说到期/释放了 → 走 **重建**

用户的 Mac 终端和服务器终端是两个地方，命令给错了会白跑一遍。提示符 `myles@...MacBook-Air`
是本机，`root@iZ...` 是服务器。给命令时说清楚在哪跑。

## 环境事实

| 项 | 值 |
| --- | --- |
| 云 | 阿里云轻量应用服务器，**中国香港**（免 ICP 备案的前提） |
| 规格 | 2 vCPU / 2 GiB / 40 GiB ESSD，Ubuntu 24.04 |
| 域名 | `canvas-myles.me`（Namecheap，GitHub 学生包免费 `.me`） |
| 登录 | SSH 密钥，本机 `~/.ssh/canvas-helper.pem`，用户 `root` |
| 代码 | 服务器上 `~/canvas-helper` |
| 反代 | Caddy，配置 `/etc/caddy/Caddyfile`，自动申请 Let's Encrypt |
| 发信 | Brevo SMTP，免费档 300 封/天 |

IP、SMTP 用户名、密钥一律不写在这里（仓库是公开的）。要用时：IP 从 DNS 查，SMTP
配置读服务器上的 `~/canvas-helper/.env`，密钥从用户的密码管理器取。

## 日常更新

```bash
cd ~/canvas-helper
git pull
docker compose --env-file .env up -d --build
docker compose ps
```

前端要重新编译，几分钟。三个容器都应是 healthy/Up（`api`、`worker`、`postgres`）。

**构建峰值内存超过 1 GB**，而这台只有 2 GB + 2 GB swap。服务器上如果还跑着别的项目，
构建前先把它们 `docker compose stop`，否则可能 OOM 把两边都搞挂。

### 更新后必须验证

前四条在服务器上跑，最后一条在用户本机：

```bash
curl -s http://127.0.0.1:8000/health                                      # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/courses # 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://127.0.0.1:8000/api/auth/request-link \
  -H 'Content-Type: application/json' -d '{"email":"x@gmail.com"}'         # 403（域名白名单）
docker compose logs api --since 5m | grep -i "delivery failed"             # 无输出
```

```bash
curl -s https://canvas-myles.me/health                                     # {"status":"ok"}
```

改动涉及登录流程时，让用户真的走一遍：请求链接 → 收邮件 → 点"确认登录"按钮。

## 重建（服务器到期或被释放）

阿里云轻量按月/年付。**到期后先停机，过一段保留期就释放，磁盘数据一并删除**（保留期
以控制台续费页面当时的提示为准，不要凭记忆告诉用户具体天数）。

### 先抢救数据

机器还能登录就先备份，这步的价值远大于后面所有步骤：

```bash
cd ~/canvas-helper
docker compose exec -T postgres pg_dump -U canvas canvas > ~/backup-$(date +%F).sql
docker run --rm -v canvas-helper_app_data:/data -v "$PWD":/backup \
  alpine tar czf /backup/app_data-$(date +%F).tar.gz -C /data .
grep CREDENTIAL_ENCRYPTION_KEY .env
```

用 `scp` 把这几个文件拉到用户本机。

**加密密钥丢了，数据库备份里的 Canvas Token 解不出来**，所有人都得重新填。密钥应该在
用户的密码管理器里；不在的话，这是重建前最紧急的一件事。

### 重新买机器

阿里云控制台 → 轻量应用服务器 → 创建。五项对照着选：

| 项 | 要求 | 为什么 |
| --- | --- | --- |
| 地域 | **中国香港** | 境内节点必须 ICP 备案，且备案不区分端口，8080 也躲不过 |
| 内存 | **≥ 2 GB** | 便宜套餐常见 0.5G/1G，构建镜像直接 OOM |
| 系统盘 | **≥ 40 GB** | 课件要占空间 |
| 镜像 | 系统镜像 → Ubuntu 24.04 | 别选应用镜像，预装面板用不上 |
| 续费价 | 购买页会显示 | 首年促销价和续费价可能差不少 |

轻量**升配容易、降配基本不行**，提醒用户别买了大的指望以后降。

创建后：控制台绑定密钥对并重启（默认是密码登录），防火墙确认放行 80/443（默认模板
通常已含 22/80/443，先看再加，别重复加）。

### 装环境

```bash
apt-get update && apt-get install -y ca-certificates curl
curl -fsSL https://get.docker.com | sh

# swap：2 GB 内存构建镜像必做
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h          # Swap 那行要是 2.0Gi
```

阿里云的 Ubuntu 镜像默认没有主机防火墙，放行在控制台做。配好 Caddy 后外网还不通，
再回头查 `iptables -L INPUT -n` 和 `ufw status`。

### 改 DNS 并等生效

Namecheap → Domain List → Manage → Advanced DNS，把 A 记录 `@` 的值改成新 IP。

```bash
nslookup canvas-myles.me 8.8.8.8      # 必须返回新 IP 才能继续
```

**解析没生效不要启动 Caddy。** 证书申请会连续失败，Let's Encrypt 有失败限流，
被限了要等一小时。

### 部署

```bash
cd ~ && git clone https://github.com/Jalensuggs/canvas-helper.git && cd canvas-helper
```

`.env` 按下表重建。加密密钥用**备份里的那一个**，不要重新生成：

```bash
POSTGRES_PASSWORD=<新的随机串>
CANVAS_HELPER_CREDENTIAL_ENCRYPTION_KEY=<备份里的原密钥>
CANVAS_HELPER_PUBLIC_URL=https://canvas-myles.me
CANVAS_HELPER_EMAIL_FROM=Canvas Helper <noreply@canvas-myles.me>
CANVAS_HELPER_SMTP_HOST=smtp-relay.brevo.com
CANVAS_HELPER_SMTP_PORT=587
CANVAS_HELPER_SMTP_USERNAME=<Brevo 控制台 SMTP & API 页>
CANVAS_HELPER_SMTP_PASSWORD=<Brevo 生成的 SMTP key>
CANVAS_HELPER_ALLOWED_EMAIL_DOMAINS=student.uts.edu.au
CANVAS_HELPER_ACADEMIC_TIMEZONE=Australia/Sydney
CANVAS_HELPER_TERM_START_MONTHS=1,7
CANVAS_HELPER_MAGIC_LINK_PER_EMAIL_PER_HOUR=20
```

`chmod 600 .env`。让用户自己粘贴密钥类的值，别让它们经过聊天记录：

```bash
read -rs -p "粘贴后回车: " K; echo
sed -i "s|CANVAS_HELPER_SMTP_PASSWORD=.*|CANVAS_HELPER_SMTP_PASSWORD=$K|" .env
unset K
```

起服务并恢复数据：

```bash
docker compose --env-file .env up -d --build
docker compose exec -T postgres psql -U canvas canvas < ~/backup-<日期>.sql   # 有备份才做
```

### Caddy

```bash
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update && apt-get install -y caddy

cp ~/canvas-helper/docker/Caddyfile.example /etc/caddy/Caddyfile
sed -i 's/canvas.example.com/canvas-myles.me/' /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile     # 要有 Valid configuration
systemctl reload caddy
journalctl -u caddy --no-pager -n 25 | grep -i "certificate obtained"
```

最后跑一遍上面的验证清单。

### Brevo 发件域名

DNS 记录在 Namecheap，换服务器不影响，**不用重做**。只有换域名时才需要重新验证：
Brevo → Senders, domains, IPs → Domains，选 Manual，它给 4 条记录（brevo-code TXT、
两条 DKIM CNAME、DMARC TXT）。

Namecheap 的 **Host 栏只填前缀**：`brevo1._domainkey`，不是
`brevo1._domainkey.canvas-myles.me`，填全了会变成双重域名，验证永远不过。

## 踩过的坑，别再踩

**登录页不能在加载时自动验证。** 微软 365 Safe Links 会在沙箱里打开邮件链接并执行
页面 JavaScript，等于替用户点了一次，把一次性 token 消费掉，用户点进来只看到
"invalid or expired"。全部 UTS 邮箱用户都会中。修复是改成点"确认登录"按钮才验证，
`frontend/src/login.test.tsx` 守着这个行为。**看到任何"页面加载即验证"的改动都要拦。**

**接口对发信失败永远返回 202**，这是防邮箱枚举。所以发信成不成功**只能看日志**：
`docker compose logs api | grep -i "delivery failed"`。命令行折行时 `"delivery failed"`
会单独显示一行，那是命令回显不是输出，用 `grep -c` 拿数字更可靠。

**新注册的域名，注册局要一会儿才发布。** 刚注册就查会是 NXDOMAIN，连 `a0.nic.me`
都查不到，这是正常的，等十几分钟。别急着改 DNS 配置。

**Namecheap 领完免费域名会塞 4 条 GitHub Pages 的 A 记录**（`185.199.10x.153`），
必须全删，留着解析会随机落到 GitHub，网站时通时不通。

**服务器到期是真的会删数据。** 提醒用户看控制台的到期时间，并建议开自动续费或者设日历提醒。

## 相关文档

`docs/deploy.md` 是面向读者的完整部署手册，包含免费方案对比、备案说明、SMTP 排错表。
这份 skill 是给 Claude 的操作指南，两者内容有重叠时以仓库当前代码和实际验证结果为准。
