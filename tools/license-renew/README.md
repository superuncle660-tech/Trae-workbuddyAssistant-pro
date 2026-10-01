# Trae Work 助手 —— 「定期弹激活框」根因与自动续期方案

> 时间：2026-09-30（v2 抗变更加固版）
> 现象：打开 `trae-work-assistant.exe` 会弹激活框，要输激活口令才能进入，**每隔几天就来一次**
> 结论：**不是软件坏了，是作者内置的商业授权只有 7 天有效期。** 已用可复用的群发口令做成静默自动续期，
> 并且对「作者换口令 / 换服务器 / 换签名密钥」三种变化做了自适应或明确告警。

---

## 一、一句话根因

助手主程序里内置了一个授权模块 `license_guard`：

* 本地凭证：`%USERPROFILE%\.license_guard\license.dat`
* 结构：

```json
{
  "last_seen": 1790757345,
  "payload_b64": "<base64>",
  "signature_b64": "<base64, 256 字节 = RSA-2048>"
}
```

`payload_b64` 解出来是：

```json
{ "expires_at": 1791359792, "issued_at": 1790754992,
  "machine_id": "df693edc…53", "version": 1 }
```

**有效期 = 正好 7 天**（`expires_at - issued_at` = 604800 秒）。
到期后主程序报「授权已过期，请重新获取口令激活」→ 就是那个弹框。

### 为什么改本地文件没用

`signature_b64` 是**服务端用私钥**签的，主程序里只内嵌了**公钥**做验签：

| 手段 | 结果 |
|---|---|
| 改 `license.dat` 里的 `expires_at` | ❌ 报 `signature_invalid` →「凭证无效（校验失败），请重新激活」 |
| 回拨系统时间 | ❌ 有 `clock_rollback` 检测 →「检测到系统时间异常，请校正系统时间后重新激活」 |
| 断网 / 改 hosts 拦服务器 | ❌ 验签是离线的，本地有凭证就能进；断网只是让"重新激活"做不了 |

所以唯一不碰二进制的正路就是：**拿口令去换新的 7 天凭证**。

---

## 二、还原出来的完整契约（全部实测）

```
POST http://64.90.20.244:8443/api/activate
Content-Type: application/json

{"code": "<激活口令>", "machine_id": "<sha256 hex>"}

→ 200 {"payload_b64": "...", "signature_b64": "..."}
```

| 项 | 值 |
|---|---|
| 服务器 | `64.90.20.244:8443`（`uvicorn`/FastAPI；exe 里可用环境变量 `LICENSE_GUARD_SERVER_URL` 覆盖） |
| 必填字段 | `code` + `machine_id`（缺任一 → 422，FastAPI 会直接列出来） |
| **机器指纹算法** | `sha256("winreg_machineguid:" + MachineGuid.大写)` |
| MachineGuid 来源 | `HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid` |
| 签名算法 | **RSA-2048 / PKCS#1 v1.5 / SHA-256**，签的是 base64 **解码后**的 payload 原始字节 |
| 公钥 | 内嵌在 exe 里（`-----BEGIN PUBLIC KEY-----` … 固定长度 PEM） |

> 指纹里的 wmic 两条（csproduct uuid / cpu processorid）只是 fallback，本机走的是 winreg 这条 —— 已用 sha256 反推逐字节验证通过。

### 服务端的错误形态（实测，用于精准判定失败原因）

| 情形 | 响应 | 脚本判定 |
|---|---|---|
| 成功 | `200` + `payload_b64`/`signature_b64` | 继续验签 |
| **口令被换掉 / 撤销** | `403` `{"detail":"口令错误"}` | **`code_rejected`** → 提示"去拿新码" |
| 参数缺失 | `422`，detail 里列出字段 | `bad_request`（本地 bug） |
| 服务器不可达 | 连接超时 | `network` |

> 另有一条实测发现：**服务器不校验 `machine_id` 是否真实**（传全 0 也照签 200）。
> 绑定关系完全靠客户端本地比对 `payload.machine_id`，所以指纹必须自己算对。

**口令可复用**：同一口令每次调用都会重新签一份 7 天，不会一次性作废。

---

## 三、交付内容

| 文件 | 作用 |
|---|---|
| `python\renew_license.py` | 续期主脚本（纯标准库，零依赖；v2 抗变更加固） |
| `python\license_config.json` | 配置：口令池 / 服务器 / 阈值 / 告警 |
| `续期授权.bat` | **双击手动续一次**，带窗口、全程可见 |
| `安装-自动续期任务.bat` | **双击注册定时任务**（静默无窗口） |
| `install-renew-task.ps1` | 上面那个 bat 实际调用的注册脚本 |
| `诊断-授权状态.bat` | **双击 = 打印 exe 内嵌公钥/服务器 + 凭证剩余天数**（排障首选） |
| `logs\license-renew.log` | 续期日志（追加式） |
| `logs\license-state.json` | 最近一次成功用的服务器/口令/公钥（排障用） |
| `logs\license-alert.json` | 失败告警快照 |

### 脚本行为

```
读 license.dat → 算剩余天数
   ├─ 剩余 > 2 天  → 直接退出，不联网
   └─ 剩余 ≤ 2 天  → 依次尝试「口令池」里的口令
                       → POST /api/activate 换新凭证
                       → 用「公钥候选集」逐个验签
                       → 校验 machine_id / expires_at
                       → 旧文件备份成 license.dat.bak-<时间戳>
                       → 原子写回
                       → 失败则写告警（可选推飞书，12h 冷却）
```

**平时不联网**，只在临期时打接口。

### 常用参数

```
python renew_license.py                # 按阈值判断（默认，定时任务用这个）
python renew_license.py --check        # 只看现状，不联网不写盘
python renew_license.py --dump-info    # 打印 exe 内嵌公钥/服务器 + 当前凭证（排障首选）
python renew_license.py --force        # 强制续一次
python renew_license.py --json         # JSON 输出（接调度器/通知用）
python renew_license.py --code XXXX    # 临时指定口令（优先级最高，不改配置）
```

---

## 四、怎么用

**① 装自动续期（一次性）**

双击 `安装-自动续期任务.bat` → 注册计划任务 `TraeWorkLicenseRenew`
（触发：**登录后 3 分钟** + **每天 10:10**；动作：`pythonw.exe renew_license.py --quiet`，无窗口）。

**② 平时什么都不用做**

剩余 2 天以内时它会自己续上，弹框不会再出现。

**③ 想手动续 / 看状态**

双击 `续期授权.bat`，窗口里直接看到结果。

**④ 出问题时先诊断**

双击 `诊断-授权状态.bat` —— 一次打印：exe 是否存在、内嵌几把公钥、内嵌哪些服务器候选、实际用哪个、凭证还剩几天。

**⑤ 卸载**

```powershell
Unregister-ScheduledTask -TaskName TraeWorkLicenseRenew -Confirm:$false
```

---

## 五、★ 作者改了东西怎么办

这是 v2 的核心。结论先给：

| 作者改了什么 | 脚本能否自愈 | 你要做什么 |
|---|---|---|
| **① 换激活口令**（最常见） | 池里的码能自愈；池子全失效 → `403` 告警 | **把新码加进 `code_list`**（一行事） |
| **② 换授权服务器地址** | ✅ 自动 —— 地址从 exe 里实时提取 | 升级助手 exe 即可 |
| **③ 换签名密钥对** | ✅ 自动 —— 公钥从 exe 里实时提取、多候选轮试验签 | 升级助手 exe 即可 |
| **④ 改接口路径 / 下线服务器** | ❌ 做不到 | 只能回退手输口令，或找作者 |

### ① 换口令 —— 用「口令池」

`python\license_config.json`：

```json
"code": "REPLACE_WITH_YOUR_CODE",
"code_list": ["REPLACE_WITH_YOUR_CODE", "新拿到的码", "再新的码"]
```

脚本按 `--code` → `code` → `code_list` 顺序逐个试，**第一个成功即停**。
旧码留在数组里**无害**（服务端对错码只是回 403，没有频率限制，实测轮试成本极低）。

> 所以作者轮换口令这件事，对你只是"往数组里追加一项"。
> 实测：故意先喂一个失效码 `WRONGCODE999`，脚本自动跳过并用手上有效码续期成功。

**池子里的码全失效时**，脚本会：
- 日志写明「服务器明确返回 403 口令错误，说明作者已更换/撤销激活口令」
- 写 `logs\license-alert.json`，配了 `alert_webhook` 就推飞书
- 退出码 1，`--json` 输出里带 `"hint": "update_code"`、`"config": "<配置路径>"`

### ② 换服务器地址 —— 自动跟随

exe 里就嵌着授权服务器地址。脚本 `server` 默认 `"auto"`：
**从当前 exe 实时提取**（带端口、排除 localhost 和开发端口 5173/17388 等，8443 优先）。
作者发新版助手换了 IP，你只要用新版 exe，地址自动跟上；提取不到才退回 `server_fallback`。

实测：`--dump-info` 显示 `实际使用服务器: http://64.90.20.244:8443（来源: exe）`。

想手动指定就把 `"server"` 写成完整 URL（非 `auto` 时尊重你的值）。

### ③ 换签名密钥 —— 公钥候选集轮试

验签候选 = **exe 内嵌全部 PEM** ＋ 内置兜底副本 ＋ 配置里的 `pubkey_extra`，按 (模数, 指数) 去重后逐个试。

> 关键点：作者若换签名私钥，**必须同时升级 exe**（否则他自己的客户端也验不过）。
> 所以"你用的 exe 是最新的" ≈ "脚本手里的公钥是对的"，这条会自动成立。

万一 exe 里的公钥以非 PEM 形式内嵌、提取不到，把新公钥文本贴进 `pubkey_extra` 数组即可：

```json
"pubkey_extra": ["-----BEGIN PUBLIC KEY-----\nMIIBIj...\n-----END PUBLIC KEY-----\n"]
```

这种情况下脚本会报 `signature_invalid` 并写明「试过哪几把公钥」，**且不会改动你原有的有效凭证**——原凭证能一直用到到期日。

### ④ 排障一条命令

```powershell
# 在 <助手目录>\python 下
%USERPROFILE%\AppData\Local\Python\bin\python.exe renew_license.py --dump-info
```

一次摊开：exe 是否存在 / 内嵌了几把公钥 / 内嵌了哪些服务器候选 / 实际用哪个 / 凭证剩余天数。

---

## 六、本次验证记录

### v1（首次打通）

| 检查项 | 结果 |
|---|---|
| 空 body POST | `422` 列出必填 `code` / `machine_id` ✅ |
| 指纹算法反推 | `sha256("winreg_machineguid:19E64346-…")` == 凭证里的 `df693edc…` ✅ |
| 真实激活调用 | `HTTP 200`，0.21s ✅ |
| 验签 | `openssl dgst -sha256 -verify` → **`Verified OK`** ✅ |
| 脚本 `--force` 全链路 | 续期成功，旧文件已备份 ✅ |
| `pythonw.exe` 无窗口 | rc=0，日志正常落盘 ✅ |

### v2（抗变更加固，本轮实测）

| 测试 | 预期 | 结果 |
|---|---|---|
| T1 假 exe（无内嵌公钥/地址） | 退到 `server_fallback` + `builtin` 公钥 | ✅ PASS |
| T2 错口令 | 精确分类为 `code_rejected`（403 口令错误） | ✅ PASS |
| T3 篡改签名（模拟换签名密钥） | 判定 `signature_invalid`，不写盘 | ✅ PASS |
| T4 告警冷却（无 webhook） | 同种失败 12h 内只告警一次 | ✅ PASS（首次发现 bug 已修） |
| T5 口令池去重保序 | `[--code, code, *code_list]` 去重 | ✅ PASS |
| T6 不同失败类型互不干扰冷却 | 换 kind 立即告警 | ✅ PASS |
| T7 续期成功清空冷却状态 | 下次失败立即告警 | ✅ PASS |
| 真实轮试 | 错码跳过 + 有效码续期成功 | ✅ PASS |
| 服务器地址自动提取 | `来源: exe` | ✅ PASS |

> ⚠️ 修复记录：初版 T4 失败 —— 没配 `alert_webhook` 时 `last_alert` 从不落盘，冷却形同虚设。
> 已改为**无论是否配置 webhook 都记录告警时间**。这是靠测试跑出来的真 bug，不是推测。

当前凭证：签发 `2026-09-30 17:27:19` → 到期 `2026-10-07 17:27:19`，有效期 7.0 天。
备份文件：`~\.license_guard\license.dat.bak-20260930-170759`（原始）及后续若干份。
**全程未删除任何文件。**

---

## 七、边界与风险

1. **口令是公开的**。作者换码时，只有你手上**已经有新码**才能自愈 —— 脚本没法替你从 QQ 群里拿码。
   所以配 `alert_webhook` 很重要：它会在码失效的第一时间通知你。
2. **换主板 / 重装系统会变 machine_id**，届时必须重新激活，脚本会报 `machine_mismatch`。
3. 服务器是作者自建的。若他**改了接口路径或直接下线**，本方案失效 —— 只能回去手输口令。
4. 脚本只做「调用公开接口 + 写自己那份凭证」，**不碰 exe、不改校验逻辑**，所以作者升级助手不会导致本方案失效。
5. ⚠️ `license.dat` 里含你本机的机器指纹，**别外传**。
6. 飞书告警**已接入**：复用你「Trae&Buddy自动签到播报」那个群机器人的 webhook（写在 `license_config.json` 的 `alert_webhook`），格式是同款**红色卡片**，仅失败时推送、12h 冷却。
   不想推就把 `alert_webhook` 留空（只写本地 `logs\license-alert.json`）；想换纯文本把 `alert_format` 改成 `"text"`。

---

## 八、回滚

脚本完全不改主程序，回滚只需二选一：

```powershell
# 只停自动续期
Unregister-ScheduledTask -TaskName TraeWorkLicenseRenew -Confirm:$false

# 连凭证一起还原到最初那份
Copy-Item "$env:USERPROFILE\.license_guard\license.dat.bak-20260930-170759" `
          "$env:USERPROFILE\.license_guard\license.dat" -Force
```

> 回滚后回到「每 7 天手动激活」的状态。
