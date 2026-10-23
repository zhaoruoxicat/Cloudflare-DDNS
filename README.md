# Cloudflare DDNS

一个适用于树莓派的命令行 DDNS 工具。自动获取家庭网络的公网 IPv4，并更新 Cloudflare 中的域名解析，让你在 IP 变化后仍能通过固定域名访问家里的服务。

使用 Python 3 开发，无需安装 pip 依赖，支持中文配置向导和开机自动更新。

## 使用前准备

你需要：

- 一台使用 systemd 的 Linux 设备，已适配 Raspberry Pi OS Lite / Debian 13 arm64。
- 一个已由 Cloudflare 托管 DNS 的域名。
- 一个具有 DNS 编辑权限的 Cloudflare API Token。

如果需要从外网访问家中设备，还需具备可入站的公网网络，并自行设置路由器端口转发。DDNS 只负责更新域名解析。

## 1. 安装

准备好 `cf-ddns_0.1.1_all.deb`，在安装包所在目录执行：

```bash
sudo apt install ./cf-ddns_0.1.1_all.deb
```

安装包可自行从源码构建，方法见下方“源码构建”。系统依赖为 Python 3.11+、ca-certificates、systemd 252+ 和 adduser；使用 apt 安装时会处理缺失依赖。

## 2. 获取 Cloudflare Token

打开 [Cloudflare API Tokens 页面](https://dash.cloudflare.com/profile/api-tokens)，创建一个 Token，配置以下权限：

| 类别   | 权限   | 级别   |
| ---- | ---- | ---- |
| Zone | Zone | Read |
| Zone | DNS  | Edit |

区域资源选择你要使用的域名，例如 `example.com`。家庭公网 IP 会变化，请勿将 Token 的客户端 IP 限制为当前公网 IP。

创建后保存 Token，接下来在程序中填写。

## 3. 启动并配置

```bash
sudo cf-ddns
```

首次启动会提示输入以下信息：

| 配置项       | 填写示例                         |
| --------- | ---------------------------- |
| API Token | 刚创建的 Cloudflare Token，输入时不显示 |
| DNS 区域域名  | `example.com`                |
| 完整记录域名    | `home.example.com`           |
| 更新间隔      | `300`，即每 5 分钟检查一次            |
| 开机自启      | 输入 `y` 开启，`n` 关闭             |

将示例域名替换为你自己的域名。**不必提前创建 A 记录**，程序会自动创建或更新。

选择开启自启后会立即执行一次更新，并在后台定期检查，不需要一直开着终端。选择关闭则只保存配置，可稍后手动更新。

再次运行 `sudo cf-ddns` 会显示管理菜单。

## 常用命令

| 命令                       | 用途            |
| ------------------------ | ------------- |
| `sudo cf-ddns`           | 打开配置向导或管理菜单   |
| `sudo cf-ddns configure` | 修改配置          |
| `sudo cf-ddns update`    | 立即更新一次        |
| `cf-ddns status`         | 查看运行状态和最近更新结果 |
| `sudo cf-ddns logs`      | 查看最近的日志       |
| `sudo cf-ddns enable`    | 开启自动更新和开机自启   |
| `sudo cf-ddns disable`   | 停止自动更新并关闭自启   |

修改配置时，留空可保留原值。更新间隔支持 60～86400 秒。

## 遇到问题

先执行以下命令查看原因：

```bash
sudo cf-ddns logs
```

- **公网 IP 查询失败**：检查网络和 DNS，程序会依次尝试多个查询源。它会绕过环境代理，避免把代理出口误认为家庭公网 IP。
- **Cloudflare 认证失败**：检查 Token 是否有效，以及是否包含上述两项权限和正确的区域范围。
- **记录冲突**：检查是否有同名 CNAME 或多条 A 记录，手动处理后重试。
- **域名正确但无法从外网连接**：检查公网入站条件、端口转发和目标服务是否正常。

程序目前只更新 IPv4 的 A 记录，并使用 **DNS only（灰云）**。已有记录也会被设为灰云，适合 WireGuard 等需要直连的服务。

## 源码构建

在项目根目录执行，需要系统提供 `dpkg-deb`：

```bash
bash build-deb.sh
```

生成的安装包位于 `dist/cf-ddns_0.1.1_all.deb`。

运行测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

测试使用模拟 API，不会修改真实 DNS。

## 卸载

```bash
sudo apt remove cf-ddns  # 卸载程序，保留配置
sudo apt purge cf-ddns   # 卸载并清理配置和运行状态
```

卸载不会删除 Cloudflare 上的 DNS 记录。

## 配置与许可证

配置保存在 `/etc/cf-ddns/config.json`，仅管理员可读。请勿公开其中的 Token。

本项目使用 [MIT License](LICENSE)。
