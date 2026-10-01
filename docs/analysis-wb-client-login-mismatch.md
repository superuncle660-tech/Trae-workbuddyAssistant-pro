# TRAE / WorkBuddy 面板「登录状态」不一致 —— 根因分析

> 时间：2026-09-30 19:20
> 现象：同一个助手（窗口标题 *Trae & Buddy签到助手*）左上角状态栏里，
> TRAE 页签第一颗 chip 是绿色「Trae Work 已安装 v2.3.87413」，
> WorkBuddy 页签第一颗 chip 是红色「WorkBuddy 客户端未登录」——看起来像同一个指标给出了矛盾答案。
> 结论：**这不是同一个指标，而且 WB 侧那颗「未登录」本身是误报。**

---

## 一、先拆清楚：两个面板的状态栏根本不是同一套东西

前端源码（从 `<助手目录>\trae-work-assistant.exe` 内嵌的
`assets/index-Ds8Ut0_1.js`，brotli 解压后 763,219 字节）里：

```js
function wT(){
  return L(t=>t.module)==="workbuddy" ? l.jsx(kT,{}) : l.jsx(jT,{})
}
```

| 位置 | TRAE 面板（组件 `jT`） | WorkBuddy 面板（组件 `kT`） |
|---|---|---|
| 第 1 颗 | `env.installed` → **客户端是否安装** | `wbClientLoggedIn` → **客户端是否登录** |
| 第 2 颗 | `certInstalled` → 证书是否信任 | `listAccounts()` → 助手账号池 |
| 第 3 颗 | `proxy.running` → 代理状态 | 签到进度 |
| 第 4 颗 | `apiStatus` → API 服务 | `apiStatus` → API 服务 |

**四颗 chip 里只有第 4 颗（API 服务）语义相同。** TRAE 面板压根没有"客户端登录"这一项
（Trae 的账号由助手自己维护的 JWT 池管理，不依赖客户端登录态）；
WorkBuddy 面板多出这一项，是因为 WorkBuddy 账号还能走 OAuth / 从本机客户端导入。

所以「一边说已安装、一边说未登录」= **在比两个不同的问题**。这是"看着不一致"的第一层原因。

---

## 二、第二层（真 bug）：WB 侧那颗「未登录」是误报

### 2.1 UI 文案与数据来源

```js
e ? "WorkBuddy 客户端已登录" : "WorkBuddy 客户端未登录"
```
`e = wbClientLoggedIn`，store 初始值写死 `wbClientLoggedIn: !1`（false）。

**全工程唯一赋值点**：

```js
refreshWbClient: async () => {
  try {
    const r = await X.workbuddy.clientStatus();
    e({ wbClientLoggedIn: r.loggedIn })
  } catch {}          // ← 空 catch，失败静默
}
```

`X.workbuddy.clientStatus` → Tauri 命令 **`workbuddy_client_status`**。

### 2.2 Rust 侧读的是哪个文件

从 exe 字符串常量池（offset 17441966）取到：

```
TRAEDATA_DIR / PYTHONIOENCODING=utf-8 / LOCALAPPDATA
CodeBuddyExtension / Data/Public/auth/workbuddy-desktop.info
account  auth  token ...
```

即实际路径：

```
%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

Rust 期望的反序列化字段（同一段字符串池）：

```
email  accessToken  refreshToken  token_type  domain
expiresAt  refreshExpiresAt  enterpriseName  enterpriseId
```

`lastLogin` / `logged_in` 在助手 exe 里出现 **0 次** → 判定不是靠"文件存在/最后登录标记"，
而是**靠成功解析出 token**。

### 2.3 文件里现在装的是什么

`workbuddy-desktop.info`（8,746 B，mtime **2026-09-30 19:18:46**）：

```json
{
  "account": { "uid":"09d7464a-…","uin":"330106445109","lastLogin":true,
               "nickname":{"$wbEncrypted":1,"envelope":"eyJzdWl0ZSI6MSwia2V5SWQiOiI5MTI3ZGVhMWIw…"} },
  "auth": { "accessToken":{"$wbEncrypted":1,"envelope":"…"},
            "refreshToken":{"$wbEncrypted":1,"envelope":"…"},
            "expiresAt":1793359125844, "domain":"www.workbuddy.cn", "…":"…" }
}
```

**token 不再是明文字符串，而是一个加密信封对象。**

### 2.4 关键对照

| 对象 | `$wbEncrypted` 出现次数 |
|---|---|
| 助手 `trae-work-assistant.exe`（22.8 MB） | **0** |
| WorkBuddy 客户端 `app.asar`（317 MB） | **7** |

客户端 `app.asar` 里的实现（`packages/at-rest-crypto/dist/field.mjs` +
`packages/workbuddy-server/src/auth/file-authentication-storage.ts`）：

```js
function isStandardEncryptedFieldWrapper(value){  // {$wbEncrypted:1, envelope:"…"}
function isAsymmetricEncryptedFieldWrapper(value){ // 外加 scheme:"asym-v1"
class ProtectedFieldCodec { encodeString / decodeString / seal / open }  // AES-GCM
class ProtectedJsonFields  { decode / encode }
// FileAuthenticationStorage.store(): serializedSession = this.encodeSession(filePath, session)
```

→ **WorkBuddy 客户端 5.6.2 给凭证文件加了「字段级静态加密（at-rest crypto）」**，
自己写自己读，密钥体系是 AES-GCM + 非对称信封（`keyId: 9127dea1b44020a7`）。

**助手完全不知道这套格式**，于是：
`serde 反序列化 String 字段 → 撞上 map → Err` → `loggedIn=false`
→ 前端空 `catch{}` 吞掉 → `wbClientLoggedIn` 停在初始 `false`
→ 红字「WorkBuddy 客户端未登录」。

---

## 三、时间线（磁盘实物为证）

```
09-20 14:03   最后一次「明文」备份   accessToken = plain:eyJhbGciOiJSUzI1Ni…(明文 JWT)
              ↑ 此前 19 个 auth 备份，无一例加密
------（09-21 客户端升级 5.6.2，app.asar mtime 09-21 20:37；resources 09-23 19:11）------
09-30 19:06:29  中间备份 …T11-07-01-789Z.30428…info   6,627 B   accessToken = ENCRYPTED
09-30 19:15:41  workbuddy-desktop.info.prev           9,621 B   ENCRYPTED（uid=英小虾）
09-30 19:18:46  workbuddy-desktop.info（主文件）      8,746 B   ENCRYPTED（uid=谢先生）
```

备案：18:41:58 那张截图里第一颗 chip **也已经是红的** —— 与"客户端早已改成加密存储"一致。

---

## 四、影响面（为什么其它都正常）

| 功能 | 数据源 | 受影响？ |
|---|---|---|
| 「已登录 4 个账号」 | 助手自有 `%APPDATA%\TraeWorkAssistant\data\workbuddy_accounts.json`（明文） | ❌ 正常 |
| 「今日已签 4/4」 | `data\checkin_status_cache.json` 的 workbuddy 段 | ❌ 正常 |
| 一键签到 | 助手自有 token（OAuth 拿到的那套） | ❌ 正常 |
| **「WorkBuddy 客户端未登录」** | 读客户端加密文件 | ✅ **误报** |
| **「导入本机账号」`workbuddy_import_local`** | 同一个文件 | ✅ **必然失败** |

**换句话说：这颗红灯只影响显示 + 从客户端导入账号这两个口子，签到链路是好的。**

---

## 五、顺带发现：助手槽位快照格式不统一

`%APPDATA%\TraeWorkAssistant\data\profiles-wb\*\auth\workbuddy-desktop.info`：

| 槽位 | 账号 | token 存储 | mtime |
|---|---|---|---|
| wb-1a0a81926e5-3c686d53 | 向自由:-C | ENCRYPTED | 09-30 19:05:52 |
| wb-1a0a81a2169-04c9855e | 超先生 | ENCRYPTED | 09-30 19:05:36 |
| wb-1a0a8574c61-f9be1ee7 | 英小虾 | ENCRYPTED | 09-30 19:15:41 |
| **wb-1a0bdbc06cc-706ea2b1** | **谢先生** | **明文** | 09-30 19:13:50 |

前三个是从客户端"备份"复制来的（客户端什么格式就是什么格式），
**谢先生那个是上一轮手工重建的明文版** —— 与另外三个格式不一致。

切换账号 = 把槽位文件写回客户端主文件，所以：
- 切到前三个 → 写回的是**加密格式**，客户端原生认得；
- 切到谢先生 → 写回的是**明文格式**，要靠客户端的 legacy 迁移逻辑兜（`legacy-auth-session-migrator`）。
  这条路径更脆，很可能就是「谢先生积分获取失败 / 要重新登录」的诱因之一。

**建议**：把谢先生槽位也用加密格式重做一次，或统一由客户端刷新后再备份。

---

## 六、修复方案（按性价比排序）

| 方案 | 做法 | 风险 | 备注 |
|---|---|---|---|
| **A. 前端补丁改判据（推荐）** | 把 `refreshWbClient` 改成：拿到 `listAccounts()` 非空即视为客户端可用 | 低，可回滚 | 属我们做过多次的 brotli 原位覆写；改的是助手自己的 JS，不动客户端 |
| B. 让助手支持解密 | 实现 at-rest 解封（AES-GCM + keyId） | 高 | 密钥在客户端内部，等于重写它的密钥体系，不划算 |
| C. 等作者更新 | — | 无 | 作者只要升级客户端依赖就会撞上同样问题，大概率会修 |
| D. 不管它 | — | 无 | 只影响一颗 chip 和「导入本机账号」 |

**方案 A 的具体改动（一处，约 40 字节）**：

```diff
- refreshWbClient:async()=>{try{const r=await X.workbuddy.clientStatus();e({wbClientLoggedIn:r.loggedIn})}catch{}},
+ refreshWbClient:async()=>{try{const r=await X.workbuddy.clientStatus();e({wbClientLoggedIn:r.loggedIn})}catch{}try{const a=await X.workbuddy.listAccounts();if(Array.isArray(a)&&a.length>0)e({wbClientLoggedIn:!0})}catch{}},
```

语义上从"客户端文件可解析"改为"助手里有可用账号"，与右侧「已登录 N 个账号」自洽。

---

## 七、1 分钟自验方法（零风险）

在 WorkBuddy 面板点一下 **「导入本机账号」**：
- 若弹红字 `导入本机账号失败：...` → 直接坐实「助手解不开客户端加密文件」；
- 若成功 → 说明客户端的 legacy 兼容还能用，那是另一条路径的问题，回来告诉我。

---

## 八、复现用命令（只读）

```bash
# 1) 看客户端 auth 文件当前是不是加密态
grep -c '\$wbEncrypted' "$LOCALAPPDATA/CodeBuddyExtension/Data/Public/auth/workbuddy-desktop.info"

# 2) 对比历史备份的 token 形态
#    （用 python 遍历该目录 *.info，看 auth.accessToken 是 str 还是 dict）
```

---

*本分析只做只读排查，未修改任何文件。*
