# 任务栏统一 + 「谢先生」切换修复

> 时间：2026-09-30
> 改动文件：`ps\workbuddy-switch-bridge.ps1`（新增一步）、`python\unify_sessions_owner.py`（新增）
> 备份：`ps\workbuddy-switch-bridge.ps1.bak-20260930-185553-preUnify`
> 数据备份：`workbuddy-db-backup\workbuddy.db.20260930-185103.bak`

---

## 一、问题 1：切换到「谢先生」失败

### 根因

`%APPDATA%\TraeWorkAssistant\logs\workbuddy-switch.log` 说得很直白：

```
[2026-09-30 18:41:22] [init]  Switch (accountId=wb-1a0bdbc06cc-706ea2b1)
[2026-09-30 18:41:22] [fatal] 账号 wb-1a0bdbc06cc-706ea2b1 还没有登录态快照
```

`workbuddy_accounts.json` 里有 **4** 个账号，但 `data\profiles-wb\` 只有 **3** 个快照槽位。

谢先生是通过助手（扫码登录 / 导入本机账号）加进池子的 —— 那只拿到了 token，
**从未在客户端里正常登录过**，所以那个「整份登录态文件」压根没生成过。

> 另外 16:36:39 还有一条历史记录：在谢先生那行点「保存登录态」时，
> 客户端当时登录的是英小虾 → 被**映射校验**拒绝。这是 v2 特意加的保护，行为正确，不是 bug。

### 已修

从历史快照里翻到一份谢先生的：

```
auth\workbuddy-desktop.2026-07-09T11-24-50-697Z.info   明文 JSON，uid=09d7464a，issuer=www.codebuddy.cn
```

已精准播种到 `profiles-wb\wb-1a0bdbc06cc-706ea2b1\auth\workbuddy-desktop.info`（3758 B），并补了 manifest。
`ListProfiles` 现在显示 **4 个槽位全部就绪**。

> ⚠️ 该 JWT 的 `exp = 2027-07-09`（未过期），但 **issuer 是 `codebuddy.cn`，而现在客户端用 `workbuddy.cn`**。
> 域名换代了，**兼容性不确定**。点切换后若跳到登录页，属预期，见下方「兜底」。

---

## 二、问题 2：左侧任务栏不统一

### 根因（对照验证过）

左侧任务栏的数据源是 **`workbuddy.db` 的 `sessions` 表**，客户端按当前登录账号的 `user_id` 过滤后渲染：

```
sessions 34 行 → 07d3a385(英小虾) 27 / 6ae74ae6(超先生) 4 / 6d0083ec(向自由) 3
```

英小虾的 `sidebar-list-snapshot.json` 正好是 **25 条 = 27 − 2 条已删**，完全吻合。

**关键结论**：`.workbuddy\<uid>\sidebar-list-snapshot.json` 是**渲染缓存（输出）**，
客户端每次启动按数据库重写它 —— **改它无效，必须改数据库**。

### 已修：切换时自动统一任务归属

`workbuddy-switch-bridge.ps1` 的 `Switch` 分支里，在
`Set-CurrentAccount` 之后、`Start-WorkBuddy` 之前，新增一步：

```powershell
python\unify_sessions_owner.py --target-uid <目标账号uid> --apply --quiet --json
```

把 `sessions` 里所有行的 `user_id` 归到**刚切过去的那个账号** ⇒
**登录哪个号，左栏都是同一份完整列表**。

- 时机正确：此刻客户端已关闭，写库安全
- 失败不影响切换：整段包在 `try/catch` 里，出错只记 `[warn]`
- 幂等：已统一时直接跳过，不做无谓写入

---

## 三、验证方法

在助手「账号管理」页点任意一行的绿色「切换到此账号」，然后看：

1. **`logs\workbuddy-switch.log`** 应出现
   `[unify] 任务归属已统一 -> {"ok": true, "changed": 7, ...}`
2. **客户端左栏**应该显示全部任务（切到超先生也能看到「分析项目定期激活机制」等）

> ⚠️ 切换会**重启 WorkBuddy 客户端**，当前会话会中断（这是它本身的机制）。

---

## 四、回滚

### 只回滚任务归属（推荐，精准）

```powershell
& "$env:LOCALAPPDATA\Python\bin\python.exe" "<助手目录>\python\unify_sessions_owner.py" --rollback
```

按首次执行时记下的 baseline 精确还原每个任务的原始归属。

### 回滚脚本改动

```powershell
Copy-Item "<助手目录>\ps\workbuddy-switch-bridge.ps1.bak-20260930-185553-preUnify" `
          "<助手目录>\ps\workbuddy-switch-bridge.ps1" -Force
```

### 整库还原

用 `workbuddy-db-backup\` 下的备份覆盖 `%USERPROFILE%\.workbuddy\workbuddy.db`（**需先关闭客户端**）。

---

## 五、顺带发现的一个真 bug（重要）

`Seed-AuthSnapshots` 动作**只比较「源文件之间谁最新」，不比较目标槽位**。
DryRun 实测它会拿 **9-20 的旧快照去覆盖超先生 / 向自由的槽位**（降级 10 天，token 早已失效）。

**⇒ 千万别跑全量 `SeedAuthSnapshots`。** 要补槽位就用精准复制，像这次补谢先生一样。

---

## 六、其它

- 本次全程**零删除**。新增文件：1 个脚本、1 份文档、1 份 db 备份、1 份 ps1 备份、1 个谢先生槽位。
- 测试全部在**临时库副本**上做，真库只在切换脚本被触发时才写。
