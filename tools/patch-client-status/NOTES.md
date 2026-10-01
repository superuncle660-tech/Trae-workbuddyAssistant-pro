# 修改说明：WorkBuddy 面板「客户端未登录」误报修复

- 日期：2026-09-30
- 目标文件：`<助手目录>\trae-work-assistant.exe`（23,862,784 字节）
- 改动前后 SHA256 前 16 位：`a57a6ec6316e8144` → `9752104a030b9537`
- 备份：`trae-work-assistant.exe.bak-20260930-193449-preClientStatusPatch`

---

## 一、现象

助手左上角状态栏，切到 **WorkBuddy** 页签时固定显示红字 **「客户端未登录」**，
但同一排的「已登录 4 个账号」「今日已签 4/4」都正常。切到 **TRAE** 页签则显示正常。
看起来像"两个面板的登录状态不一致"。

## 二、根因（两层）

### 第一层：两个页签的状态栏本来就是两套独立组件

前端源码里：

```js
function wT(){ return L(t => t.module) === "workbuddy" ? <kT/> : <jT/> }
```

四个指示灯里**只有第 4 颗「API 服务」语义相同**，其余三颗各不相同：

| 位置 | TRAE 页签（`jT`） | WorkBuddy 页签（`kT`） |
|---|---|---|
| 第 1 颗 | Trae 客户端**装没装** | WorkBuddy 客户端**登没登** |
| 第 2 颗 | 证书信任 | 助手账号池 |
| 第 3 颗 | 代理状态 | 签到进度 |

所以"一边已安装、一边未登录"不是矛盾，是在比两个不同的问题。

### 第二层（真 bug）：这颗灯是误报，链路被客户端的加密存储打断了

`wbClientLoggedIn` 的链路：

```
wbClientLoggedIn（store 初值写死 false）
  ← 唯一赋值点 refreshWbClient()
  ← Tauri 命令 workbuddy_client_status
  ← Rust 读 %LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth\workbuddy-desktop.info
```

WorkBuddy 客户端 **5.6.2** 起对该文件做了**字段级静态加密**（`app.asar` 里的
`packages/at-rest-crypto`），token 变成：

```json
"accessToken": { "$wbEncrypted": 1, "envelope": "eyJzdWl0ZSI6MSwia2V5SWQiOiI5MTI3ZGVhMWIw…" }
```

而助手的 Rust 侧仍按**明文字符串**反序列化（字符串池里期望
`email / accessToken / refreshToken / token_type / domain / expiresAt …`），
解析直接抛错。前端这一处的 `catch` 是**空的**：

```js
refreshWbClient: async () => { try { … } catch {} }
```

异常被静默吞掉，值停在 store 初值 `false` → 红字「未登录」。
**UI 看着像"检测结果"，其实是"压根没测到"。**

佐证：`$wbEncrypted` 在助手 exe 里出现 **0** 次，在客户端 `app.asar` 里出现 **7** 次。
时间线：09-20 及以前的 19 个 auth 备份全是明文 JWT，09-30 的写入全是加密。

## 三、修复方式

`<助手目录>\` 只有编译产物，没有源码，所以采用**内嵌前端原位补丁**：
解出 exe 里 brotli 压缩的前端 JS，改判据后重新压缩，覆写回原槽位。

改动只有两处（均在 `refreshWbClient` 相关的 store 逻辑上）：

```diff
- wbClientLoggedIn:!1,env:null
+ wbClientLoggedIn:!0,env:null

- refreshWbClient:async()=>{try{const r=await X.workbuddy.clientStatus();e({wbClientLoggedIn:r.loggedIn})}catch{}},
+ refreshWbClient:async()=>{let o=!1;try{o=!!(await X.workbuddy.clientStatus()).loggedIn}catch{}
+   if(!o){try{o=!!(await X.workbuddy.listAccounts()).length}catch{}}e({wbClientLoggedIn:o})},
```

即：**读不到客户端登录态时，退化为「助手账号池非空 = 客户端可用」**。

之所以选这个判据而不是"无脑置真"：`workbuddy_list_accounts` 读的是助手自己的
`workbuddy_accounts.json`（明文，不受加密影响），所以它一直可用。

## 四、生效条件

⚠️ **必须完全退出助手再重新打开**，磁盘上的新版本才会被加载。
替换时助手正在运行，旧的 5 个进程仍在用改名后的旧文件。

## 五、验证（离线，未靠"打开看一眼"）

用 Chrome 无头浏览器 + 假 Tauri 后端，把补丁后的前端真实渲染出来，跑 5 个场景：

| 场景 | 状态灯 | 结论 |
|---|---|---|
| **原版** + clientStatus 抛错 | 客户端未登录 | 复现原 bug |
| 补丁 + clientStatus 抛错 | **客户端已登录** | 修复生效 |
| 补丁 + clientStatus 返回 true | 客户端已登录 | 正常路径不受影响 |
| 补丁 + clientStatus 返回 false | 客户端已登录 | 走兜底 |
| 补丁 + 抛错 + **账号池为空** | **客户端未登录** | 证明不是无脑乐观 |

另外的字节级校验：

- 补丁后前端解压 763,290 字节（原 763,219，+71），`node --check` 语法通过
- 改动字节全部落在 JS 流区内（16982111..17155270），**未碰到流之后的填充区**
- `index.html` / `index.css` / `reward-qr.png` 等其它资源逐字节未变且仍可解压
- 补丁流 173,161 字节 ≤ 原流 173,174 字节，**未越界**

## 六、回滚

双击 `回滚-客户端登录态修复.bat`（需先完全退出助手）。
它会用备份覆盖回原文件。备份文件**没有被删除**，一直保留在工具目录。
也可以手动把 `.bak-20260930-193449-preClientStatusPatch` 改名回 `trae-work-assistant.exe`。

## 七、遗留（本次未处理）

1. **「导入本机账号」按钮仍然不可用** —— 它走的是 Rust 侧 `workbuddy_import_local`，
   读的是同一个加密文件，前端补丁改不到。助手账号池里已有 4 个账号，暂时用不上这个功能。
2. **根治方案**：改源码让 Rust 侧支持 `$wbEncrypted` 解密，或在 `clientStatus`
   的判据里直接补一句"`listAccounts()` 非空即视为可用"。这需要源码，
   `<助手目录>\` 里没有（只有 exe + ps 脚本）。
3. **助手槽位快照格式不统一**：向自由 / 超先生 / 英小虾 是加密态，
   谢先生那个是明文态（之前手工重建的），切换时走 legacy 迁移路径，相对更脆。

## 八、本次新增/改动的文件

| 文件 | 说明 |
|---|---|
| `trae-work-assistant.exe` | 已替换为补丁版 |
| `trae-work-assistant.exe.bak-20260930-193449-preClientStatusPatch` | 原版备份（勿删） |
| `trae-work-assistant.exe.running-20260930-193723` | 替换时正在运行的旧版停放文件 |
| `回滚-客户端登录态修复.bat` | 一键回滚 |
| `patch-client-status\` | 补丁复现脚本与说明（含幂等 + 长度门禁） |
| `extracted-frontend-20260930\app_index.patched.js` | 补丁后的前端源码（留档） |
