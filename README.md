# 海康多机通道盘点（HikChannelInventory）

海康威视 **多台 NVR / 录像机数字通道一键盘点** 小工具。用账号密码走 ISAPI（Digest / Basic），**不依赖 Cookie / SessionTag**，也不需要海康官方 SDK。

适合现场巡检、搬迁对点、补点排查：一次拉完好几台录像机的通道，看总数、查重复 IP，再拿自己的 Excel IP 表对照「已存在 / 未添加」。

> 风格与姊妹项目一致：纯 Python 标准库 + tkinter，可源码双击运行，也可直接用 Release 里的 exe。

---

## 功能一览

| 能力 | 说明 |
|------|------|
| 多机拉取 | 每行一台录像机，并行读取数字通道 |
| 短名区分 | 列表显示 `251录像机` / `252录像机`…，避免机型名（Network Video Recorder 等）撞车 |
| 监控汇总 | 成功台数、监控总数、有效 IP、唯一 IP |
| 跨机查重 | 同一 IP 出现在多路 / 多台都会标红 |
| IP 表对照 | 粘贴或导入自己的表，标出已存在 / 未添加 / 仅录像机有 |
| 双击打开 | 双击监控 IP 打开摄像头网页；可选带账号尝试登录 |
| 导出 TSV | 通道清单、对照结果均可导出，Excel 直接打开 |

---

## 快速开始（推荐：exe）

1. 打开 [Releases](https://github.com/fig3458/hikvision-channel-inventory/releases) 下载 `HikChannelInventory.exe`
2. 双击运行（无需安装 Python）
3. 填 **默认用户名 / 密码**，在列表里每行写一台录像机地址
4. 点 **一键拉取全部录像机**

单文件体积已尽量压缩（stdlib + tkinter 打包）；若杀软误报，可加白名单或改用下方源码运行。

---

## 源码运行

需要本机 **Python 3.10+**（仅标准库，无 pip 依赖）。

```text
双击「打开通道盘点.bat」
```

或：

```bash
python inventory_gui.py
```

---

## 使用说明

### 1. 填写录像机

上方填默认账号密码。录像机列表支持：

```text
http://192.168.23.251
http://192.168.23.252
192.168.23.253
192.168.23.254	admin	其它密码	四号楼
```

| 写法 | 含义 |
|------|------|
| 只写地址 | 用默认账号；界面短名自动为 `254录像机` |
| 地址 + 备注 | 自定义短名 |
| 地址 + 用户 + 密码 + 备注 | 该台单独账号 |

`#` 开头行为注释。

### 2. 一键拉取

- 并行连接多台；某台失败不影响其它台
- 「各机状态」页可看成功 / 失败与通道数
- 可用「筛选录像机」只看某一台
- 「只看重复 IP」过滤跨机 / 本机重复

### 3. IP 表对照

从 Excel 粘贴两列（IP、名称）或「从文件导入」，再点 **对照：已存在 / 未添加**。

| 状态 | 含义 |
|------|------|
| 已存在 | 表中 IP 已在任一已拉取录像机上 |
| 未添加 | 所有已拉取录像机上都没有 |
| 表中重复 | 你贴的表里同一 IP 出现多次 |
| 仅录像机 | 某台录像机有，你的表没有 |

### 4. 双击 IP 打开网页

在「全部通道」或「IP 表对照」中 **双击监控 IP**，浏览器打开 `http://该IP/`。

勾选「尝试带账号打开」时，会拼 `http://用户:密码@IP/`：

- 部分环境可直接过 Digest / Basic
- Chrome 等常拦截 URL 内嵌密码，此时仍需手输一次  
海康网页登录页无法像脚本那样完整代填，这是浏览器安全限制。

---

## 打包 exe（开发者）

本机已装 Python 与 PyInstaller：

```powershell
.\build_exe.ps1
```

产物：`dist\HikChannelInventory.exe`

可选：把 [UPX](https://github.com/upx/upx/releases) 的 `upx.exe` 放到 PATH 或项目目录，体积会再小一截。spec 已排除 numpy / pandas 等无关模块，避免臃肿。

---

## 技术说明

| 项 | 说明 |
|----|------|
| 协议 | HTTP ISAPI |
| 认证 | Digest（优先）/ Basic |
| 通道列表 | `GET /ISAPI/ContentMgmt/InputProxy/channels` |
| 设备信息 | `GET /ISAPI/System/deviceInfo` |
| 依赖 | 无第三方库 |

需能访问录像机 Web 端口（常见 80）。防火墙或 VLAN 隔离会导致拉取失败，请看「各机状态」与日志。

---

## 仓库文件

| 文件 | 说明 |
|------|------|
| `inventory_core.py` | 多机拉取、查重、对照、网页 URL |
| `inventory_gui.py` | 图形界面 |
| `hik_inventory.spec` | PyInstaller 精简配置 |
| `build_exe.ps1` | 一键打包脚本 |
| `打开通道盘点.bat` | 源码启动入口 |

---

## 姊妹项目

- [hikvision-channel-order](../hikvision-channel-order) — 按表格把监控加到指定数字通道
- [hikvision-camera-reset](../hikvision-camera-reset) — PoE 直连旧摄像头搜索 / 恢复出厂

---

## License

[MIT](LICENSE)
