#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Trae Work 助手 —— 授权（license_guard）自动续期  [抗变更加固版 v2]
=================================================================

背景
----
trae-work-assistant.exe 内置了一个商业授权模块 `license_guard`：

  * 本地凭证：%USERPROFILE%\.license_guard\license.dat
      {"last_seen": <unix>, "payload_b64": <b64>, "signature_b64": <b64>}
  * payload 解出来是：
      {"expires_at": <unix>, "issued_at": <unix>, "machine_id": <sha256hex>, "version": 1}
  * **有效期固定 7 天**，到期就弹「授权已过期，请重新获取口令激活」。
  * 签名 = RSA-2048 / PKCS#1 v1.5 / SHA-256，签的是 base64 **解码后**的 payload 原始字节。
    公钥内嵌在 exe 里，本地只验签、没有私钥 —— 所以**改本地文件没用**。

激活接口（实测）
----------------
  POST  http://64.90.20.244:8443/api/activate
  body  {"code": "<激活口令>", "machine_id": "<sha256 hex>"}
  resp  {"payload_b64": "...", "signature_b64": "..."}

  machine_id = sha256("winreg_machineguid:" + MachineGuid 的**大写**形式)
    MachineGuid <- HKLM\SOFTWARE\Microsoft\Cryptography\MachineGuid

  实测错误形态：
    200  成功
    403  {"detail":"口令错误"}       <- 口令被换掉 / 被撤销（★ 关键信号）
    422  {"detail":[{"loc":["body","code"],...}]}  参数问题（本地 bug）
    连接超时                        服务器不在 / 网络不通

★ 抗变更设计（v2 新增，作者改了东西也不用改代码）
-------------------------------------------------
  A) 口令轮换 —— 支持**口令池**
       配置 code_list: ["旧码", "新码", ...]，脚本按顺序逐个试，
       第一个 200 即停。作者换码后，你只需把**新码追加进数组**，不动代码。
       （实测：错码返回 403，服务端无频率限制，轮试成本极低）

  B) 服务器搬迁 —— server 默认 "auto"
       从 exe 里实时提取内嵌的授权服务器地址（带端口的非本地 URL），
       作者升级 exe 换了 IP，脚本自动跟上；提取失败才退回 server_fallback。

  C) 签名密钥更换 —— 公钥候选集轮试
       候选 = exe 内嵌全部 PEM ＋ 内置兜底副本 ＋ 配置 pubkey_extra。
       作者若同步升级 exe（换钥必须同步，否则他自己客户端也验不过），
       脚本自动用新公钥；万一提取不到，把新公钥贴进 pubkey_extra 即可。

  D) 失败必告警 —— 自愈不了就立刻让你知道
       失败时写 logs\license-alert.json，并可 POST 到 alert_webhook（飞书格式）。
       同种失败默认 12h 冷却，不刷屏。

用法
----
    python renew_license.py                # 按阈值判断，需要时才续（默认）
    python renew_license.py --check        # 只看现状，绝不联网、绝不写盘
    python renew_license.py --dump-info    # 打印 exe 里提取到的公钥/服务器 + 当前凭证
    python renew_license.py --force        # 忽略阈值，强制续一次
    python renew_license.py --json         # 以 JSON 输出结果（给调度器/通知用）
    python renew_license.py --code XXXX    # 临时指定口令（不改配置文件，优先级最高）

退出码
------
    0 = 成功（含"未到期，已跳过"）   1 = 失败   2 = 参数/配置错误   3 = 找不到 exe

配置（同目录 license_config.json）
----------------------------------
  {
    "code": "REPLACE_WITH_YOUR_CODE",                      // 主口令（可留空，用 code_list）
    "code_list": ["REPLACE_WITH_YOUR_CODE"],               // 口令池，作者换码就往这里加
    "server": "auto",                        // "auto" = 从 exe 提取；也可以写死 URL
    "server_fallback": "http://64.90.20.244:8443",
    "threshold_days": 2,
    "timeout": 15,
    "retries": 3,
    "exe_path": "",
    "pubkey_extra": [],                      // 额外候选公钥（PEM 字符串数组）
    "alert_webhook": "",                     // 飞书群机器人 webhook，留空则只写本地告警文件
    "alert_cooldown_hours": 12
  }
"""

import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "license_config.json")
ROOT = os.path.dirname(HERE)
LOG_DIR = os.path.join(ROOT, "logs")
LOG_PATH = os.path.join(LOG_DIR, "license-renew.log")
ALERT_PATH = os.path.join(LOG_DIR, "license-alert.json")
STATE_PATH = os.path.join(LOG_DIR, "license-state.json")

DEFAULT_CONFIG = {
    "code": "",
    "code_list": [],
    "server": "auto",
    "server_fallback": "http://64.90.20.244:8443",
    "threshold_days": 2,
    "timeout": 15,
    "retries": 3,
    "exe_path": "",
    "pubkey_extra": [],
    "alert_webhook": "",
    "alert_format": "feishu_card",
    "alert_cooldown_hours": 12,
}

# exe 里内嵌的公钥（兜底副本；正常情况下会直接从 exe 实时提取，作者换钥也能跟上）
FALLBACK_PUBKEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAntf1Zq3V+o9DIu4sLfI8
rHxsc4weH17dIUm/UUIsGjXSipUN1/XqKj5EB2PGGYwu5gWlDoDWR7IZUmbcX6p5
NmmL8AqccBQbAzv/pyoKjYuh1T+nb0SzXHKxQdRPu3WdVWQMkdiGaYDu4XjxBoBN
JM3qvjMs8QCv3cQIbMeYbukFoUpPo0nX7JphZb5DqJw33I+mgqcxb5++ekXbR6H4
a6An/dfEnkf72ObvkUb6N1uJQGkLvpJBCWVTZw+DNnCO/DF52eob0z0iIKazS8Eb
/5y7hfk1ixnh4+YcvvwqNlc3JYv/rce9xvQWi8j5uxnEX7PBZkYwrc22+ZaouBM2
fQIDAQAB
-----END PUBLIC KEY-----
"""

DIGESTINFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")

# 这些端口的 URL 是开发/内部地址，不当授权服务器
DEV_PORTS = {"5173", "1420", "3000", "8080", "8000", "5000", "17388"}

# 服务器返回的错误码 -> 分类
ERR_CODE_REJECTED = "code_rejected"     # 403 口令错误
ERR_BAD_REQUEST = "bad_request"         # 422
ERR_NETWORK = "network"                 # 连不上
ERR_UNKNOWN = "unknown"


# --------------------------------------------------------------------------
# 日志 / 状态
# --------------------------------------------------------------------------
def log(msg, quiet=False):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not quiet:
        try:
            print(line, flush=True)
        except Exception:
            pass
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8", newline="") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(patch):
    st = load_state()
    st.update(patch)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(STATE_PATH, "w", encoding="utf-8", newline="") as f:
            json.dump(st, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    return st


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if os.path.isfile(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8-sig") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log("配置读取失败，用默认值: %r" % (e,))
    if not cfg.get("exe_path"):
        cfg["exe_path"] = os.path.join(ROOT, "trae-work-assistant.exe")
    return cfg


def license_path():
    prof = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(prof, ".license_guard", "license.dat")


def candidate_codes(cfg, cli_code):
    """口令候选顺序：--code > code > code_list（去重保序）"""
    out = []
    for c in [cli_code, cfg.get("code")] + list(cfg.get("code_list") or []):
        c = (c or "").strip()
        if c and c not in out:
            out.append(c)
    return out


# --------------------------------------------------------------------------
# 机器指纹
# --------------------------------------------------------------------------
def machine_guid():
    """HKLM\\SOFTWARE\\Microsoft\\Cryptography\\MachineGuid"""
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
    try:
        val, _ = winreg.QueryValueEx(key, "MachineGuid")
    finally:
        winreg.CloseKey(key)
    return str(val).strip()


def machine_id():
    """sha256('winreg_machineguid:' + GUID 大写)  —— 与 exe 内实现逐字节一致"""
    raw = "winreg_machineguid:" + machine_guid().upper()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# PEM / RSA（纯标准库实现，不依赖 cryptography）
# --------------------------------------------------------------------------
def _der_tlv(buf, pos):
    tag = buf[pos]
    pos += 1
    ln = buf[pos]
    pos += 1
    if ln & 0x80:
        n = ln & 0x7F
        ln = int.from_bytes(buf[pos:pos + n], "big")
        pos += n
    return tag, buf[pos:pos + ln], pos + ln


def parse_rsa_pubkey(pem):
    b64 = "".join(l.strip() for l in pem.splitlines() if l.strip() and "KEY" not in l)
    der = base64.b64decode(b64)
    _, spki, _ = _der_tlv(der, 0)              # SubjectPublicKeyInfo ::= SEQUENCE
    _, _alg, p = _der_tlv(spki, 0)             #   algorithm SEQUENCE
    _, bitstr, _ = _der_tlv(spki, p)           #   subjectPublicKey BIT STRING
    inner = bitstr[1:]                         # 去掉 unused-bits 计数字节
    _, rsa, _ = _der_tlv(inner, 0)             # RSAPublicKey ::= SEQUENCE
    _, n_b, p2 = _der_tlv(rsa, 0)              #   modulus  INTEGER
    _, e_b, _ = _der_tlv(rsa, p2)              #   exponent INTEGER
    return int.from_bytes(n_b, "big"), int.from_bytes(e_b, "big")


def rsa_verify_sha256(n, e, msg, sig):
    """PKCS#1 v1.5 / SHA-256 验签"""
    k = (n.bit_length() + 7) // 8
    if len(sig) != k:
        return False
    m = pow(int.from_bytes(sig, "big"), e, n)
    em = m.to_bytes(k, "big")
    ps_len = k - 3 - len(DIGESTINFO_SHA256) - 32
    if ps_len < 8:
        return False
    want = b"\x00\x01" + b"\xff" * ps_len + b"\x00" + DIGESTINFO_SHA256 + hashlib.sha256(msg).digest()
    return em == want


PEM_RE = re.compile(rb"-----BEGIN PUBLIC KEY-----[\s\S]{40,4000}?-----END PUBLIC KEY-----")
URL_RE = re.compile(rb"https?://[0-9A-Za-z_.\-]+(?::\d{2,5})?")


def read_exe(exe_path):
    with open(exe_path, "rb") as f:
        return f.read()


def extract_pubkeys(exe_path):
    """从 exe 里实时抠出**全部**内嵌 PEM 公钥（可能多把）；返回 [pem, ...]"""
    out = []
    try:
        blob = read_exe(exe_path)
    except Exception as e:
        log("读取 exe 失败: %r" % (e,))
        return out
    for m in PEM_RE.finditer(blob):
        pem = m.group().decode("ascii", "replace").replace("\r\n", "\n").strip() + "\n"
        try:
            parse_rsa_pubkey(pem)
        except Exception:
            continue
        if pem not in out:
            out.append(pem)
    return out


def extract_servers(exe_path):
    """从 exe 里提取授权服务器候选地址（带端口、非本地、非开发端口）"""
    out = []
    try:
        blob = read_exe(exe_path)
    except Exception:
        return out
    for m in URL_RE.finditer(blob):
        u = m.group().decode("ascii", "replace").rstrip("/")
        after = u.split("//", 1)[-1]
        if ":" not in after:              # 只要带端口的，避免误抓 api.trae.cn 之类
            continue
        host, _, port = after.rpartition(":")
        if host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]"):
            continue
        if port in DEV_PORTS:
            continue
        if u not in out:
            out.append(u)
    # 8443 优先（已知授权服务端口）
    out.sort(key=lambda x: (0 if x.endswith(":8443") else 1))
    return out


def resolve_server(cfg, exe_path, exe_ok):
    """返回 (url, source)。server='auto' 时优先从 exe 提取。"""
    s = (cfg.get("server") or "").strip()
    if s and s.lower() != "auto":
        return s, "config"
    if exe_ok:
        cands = extract_servers(exe_path)
        if cands:
            return cands[0], "exe"
    fb = (cfg.get("server_fallback") or "").strip()
    if fb:
        return fb, "fallback"
    return DEFAULT_CONFIG["server_fallback"], "default"


def collect_pubkeys(exe_path, cfg, exe_ok):
    """候选公钥集：exe 内嵌全部 -> 内置兜底 -> 配置 pubkey_extra。按 DER 去重。"""
    keys, seen = [], set()

    def add(pem, label):
        try:
            n, e = parse_rsa_pubkey(pem)
        except Exception:
            return
        sig = (n.bit_length(), e)
        if sig in seen:
            return
        seen.add(sig)
        keys.append((pem, label))

    if exe_ok:
        for i, pem in enumerate(extract_pubkeys(exe_path)):
            add(pem, "exe#%d" % i)
    add(FALLBACK_PUBKEY, "builtin")
    for i, pem in enumerate(cfg.get("pubkey_extra") or []):
        add(pem, "config#%d" % i)
    return keys


# --------------------------------------------------------------------------
# license.dat 读写
# --------------------------------------------------------------------------
def read_license(path):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    payload = json.loads(base64.b64decode(doc["payload_b64"]))
    return doc, payload


def days_left(payload):
    return (payload["expires_at"] - time.time()) / 86400.0


def verify_credential(doc, payload, pubkeys, expect_machine_id):
    """用候选公钥逐个验签。返回 (ok, why, used_label)"""
    try:
        sig = base64.b64decode(doc["signature_b64"])
        raw = base64.b64decode(doc["payload_b64"])
    except Exception as ex:
        return False, "base64 解码失败: %r" % (ex,), None

    # 先做与密钥无关的一致性检查，早失败早报错
    if payload.get("machine_id") != expect_machine_id:
        return False, "机器指纹不匹配（machine_mismatch）", None
    if int(payload.get("expires_at", 0)) <= time.time():
        return False, "返回的授权已过期（expired）", None

    for pem, label in pubkeys:
        try:
            n, e = parse_rsa_pubkey(pem)
        except Exception:
            continue
        if rsa_verify_sha256(n, e, raw, sig):
            return True, "ok", label
    tried = ", ".join(l for _, l in pubkeys) or "(无可用公钥)"
    return False, "签名校验失败（signature_invalid）—— 试过: %s" % tried, None


def write_license(path, doc):
    """先备份旧的，再原子写入。全程不删除任何文件。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        bak = "%s.bak-%s" % (path, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, bak)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".license-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)
        os.replace(tmp, path)          # 原子替换
    except Exception:
        if os.path.exists(tmp):
            os.replace(tmp, tmp + ".failed")
        raise
    return path


# --------------------------------------------------------------------------
# 网络
# --------------------------------------------------------------------------
def activate(server, code, mid, timeout, retries):
    """返回 (resp, err_kind, err_detail)。成功时 err_kind=None。"""
    url = server.rstrip("/") + "/api/activate"
    body = json.dumps({"code": code, "machine_id": mid}).encode("utf-8")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 直连，绕过一切代理
    last_kind, last_detail = ERR_NETWORK, ""
    for attempt in range(1, max(1, retries) + 1):
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "trae-license-renew/2.0"})
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8")), None, ""
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")
            except Exception:
                pass
            if e.code == 403 and "口令" in detail:
                return None, ERR_CODE_REJECTED, detail[:200]
            if e.code in (400, 401, 403, 422):
                last_kind, last_detail = ERR_BAD_REQUEST, "HTTP %s %s" % (e.code, detail[:200])
            else:
                last_kind, last_detail = ERR_UNKNOWN, "HTTP %s %s" % (e.code, detail[:200])
            log("  第 %d/%d 次失败：%s" % (attempt, retries, last_detail))
        except Exception as e:
            last_kind, last_detail = ERR_NETWORK, "%r" % (e,)
            log("  第 %d/%d 次失败：%s" % (attempt, retries, last_detail))
        if attempt < retries:
            time.sleep(2 * attempt)
    return None, last_kind, last_detail


# --------------------------------------------------------------------------
# 告警
# --------------------------------------------------------------------------
def send_alert(cfg, kind, title, lines):
    """失败告警：写本地文件；配了 webhook 才推飞书。同种失败有冷却，不刷屏。"""
    now = time.time()
    st = load_state()
    cooldown = float(cfg.get("alert_cooldown_hours", 12)) * 3600
    last = st.get("last_alert", {}) or {}
    suppressed = (last.get("kind") == kind and now - float(last.get("at", 0)) < cooldown)

    rec = {"at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "kind": kind, "title": title, "lines": lines, "suppressed": suppressed}
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(ALERT_PATH, "w", encoding="utf-8", newline="") as f:
            json.dump(rec, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

    if suppressed:
        log("（告警冷却中，仅写本地文件，不推送）")
        return False

    # 记录本次告警时间用于冷却 —— 无论是否配了 webhook 都要记，
    # 否则没配 webhook 时 last_alert 永远为空，冷却形同虚设。
    save_state({"last_alert": {"kind": kind, "at": now}})

    wh = (cfg.get("alert_webhook") or "").strip()
    if not wh:
        return False
    fmt = (cfg.get("alert_format") or "feishu_card").strip().lower()
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    if fmt == "text":
        payload = {"msg_type": "text",
                   "content": {"text": title + "\n" + "\n".join("· " + l for l in lines)}}
    else:
        # 与「Trae&Buddy自动签到播报」同款卡片格式，群里风格统一
        payload = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {"title": {"tag": "plain_text", "content": title},
                           "template": "red"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}},
                    {"tag": "hr"},
                    {"tag": "note", "elements": [{"tag": "plain_text",
                     "content": "Trae 助手授权续期 · " + stamp}]},
                ],
            },
        }
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(wh, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=10) as r:
            r.read()
        log("已推送告警到 webhook")
        return True
    except Exception as e:
        log("告警推送失败: %r" % (e,))
        return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Trae Work 助手授权自动续期")
    ap.add_argument("--check", action="store_true", help="只看现状，不联网不写盘")
    ap.add_argument("--dump-info", action="store_true", help="打印 exe 内嵌公钥/服务器与当前凭证")
    ap.add_argument("--force", action="store_true", help="忽略阈值，强制续期")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--code", default="", help="临时指定激活口令")
    ap.add_argument("--quiet", action="store_true", help="不打印到控制台（只写日志）")
    args = ap.parse_args()

    cfg = load_config()
    lic = license_path()
    exe = cfg.get("exe_path")
    exe_ok = bool(exe) and os.path.isfile(exe)

    def emit(status, message, extra=None):
        obj = {"status": status, "message": message}
        if extra:
            obj.update(extra)
        if args.json:
            print(json.dumps(obj, ensure_ascii=False))
        else:
            log(message, quiet=args.quiet)
        return obj

    # ---- 读现状 ----
    cur_doc = cur_payload = None
    if os.path.isfile(lic):
        try:
            cur_doc, cur_payload = read_license(lic)
        except Exception as e:
            log("现有凭证无法解析（按需重新激活）: %r" % (e,))

    if cur_payload:
        left = days_left(cur_payload)
        exp = datetime.datetime.fromtimestamp(cur_payload["expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
        log("当前授权：到期 %s（剩余 %.2f 天）" % (exp, left))
    else:
        left = -999.0
        log("未找到可用的本地授权")

    # ---- dump-info：把「作者可能换掉的东西」都摊开 ----
    if args.dump_info:
        pems = extract_pubkeys(exe) if exe_ok else []
        srvs = extract_servers(exe) if exe_ok else []
        srv, src = resolve_server(cfg, exe, exe_ok)
        keys = collect_pubkeys(exe, cfg, exe_ok)
        log("exe: %s (%s)" % (exe, "存在" if exe_ok else "★ 不存在"))
        log("内嵌公钥 %d 把: %s" % (len(pems), ", ".join("len=%d" % len(p) for p in pems) or "无"))
        log("内嵌服务器候选: %s" % (", ".join(srvs) or "无"))
        log("实际使用服务器: %s  （来源: %s）" % (srv, src))
        log("验签候选公钥 %d 把: %s" % (len(keys), ", ".join(l for _, l in keys) or "无"))
        log("当前凭证文件: %s" % lic)
        return emit("dump", "已打印 exe 内嵌信息",
                    {"pubkeys_in_exe": len(pems), "servers_in_exe": srvs,
                     "server_used": srv, "server_source": src,
                     "pubkey_candidates": [l for _, l in keys],
                     "days_left": round(left, 3)})

    if args.check:
        return emit("check", "仅检查：剩余 %.2f 天" % left,
                    {"days_left": round(left, 3), "license": lic})

    if left > float(cfg.get("threshold_days", 2)) and not args.force:
        return emit("skipped", "剩余 %.2f 天（> 阈值 %s 天），本次不续期"
                    % (left, cfg.get("threshold_days", 2)), {"days_left": round(left, 3)})

    # ---- 需要续期 ----
    codes = candidate_codes(cfg, args.code)
    if not codes:
        return emit("error", "缺少激活口令：请在 %s 里填 code / code_list，或用 --code 传入" % CONFIG_PATH)

    if not exe_ok:
        return emit("error", "找不到主程序（无法提取公钥）: %s" % exe)

    srv, src = resolve_server(cfg, exe, exe_ok)
    pubkeys = collect_pubkeys(exe, cfg, exe_ok)
    log("服务器: %s（来源 %s）｜验签候选公钥 %d 把" % (srv, src, len(pubkeys)))

    try:
        mid = machine_id()
    except Exception as e:
        return emit("error", "无法采集机器指纹: %r" % (e,))
    log("机器指纹 machine_id=%s" % mid)

    # ---- 口令池轮试 ----
    resp = None
    last_kind, last_detail = ERR_UNKNOWN, ""
    used_code = ""
    for i, c in enumerate(codes, 1):
        log("尝试口令 %d/%d: %s…" % (i, len(codes), c[:4] + "*" * max(0, len(c) - 4)))
        r, kind, detail = activate(srv, c, mid, cfg.get("timeout", 15), cfg.get("retries", 3))
        if r is not None:
            resp, used_code = r, c
            break
        last_kind, last_detail = kind, detail
        if kind == ERR_CODE_REJECTED:
            log("  该口令已被服务端拒绝（403 口令错误），换下一个")
            continue
        if kind == ERR_NETWORK:
            log("  网络不通，停止轮试（避免无意义重试）")
            break
        log("  失败（%s），换下一个" % kind)

    if resp is None:
        # ---- 精确判定失败原因并告警 ----
        if last_kind == ERR_CODE_REJECTED:
            title = "⚠️ Trae 助手授权续期失败：口令全部失效"
            lines = ["服务器明确返回 403「口令错误」，说明**作者已更换/撤销激活口令**。",
                     "处理：去群里拿最新口令，加进 %s 的 code_list 数组（旧码留着无妨）。" % CONFIG_PATH,
                     "当前池中口令 %d 个，均已失效。" % len(codes)]
            save_state({"last_ok_server": srv, "last_fail_kind": last_kind})
            send_alert(cfg, last_kind, title, lines)
            return emit("error", title + " —— " + lines[1],
                        {"days_left": round(left, 3), "hint": "update_code",
                         "config": CONFIG_PATH, "tried_codes": len(codes)})

        if last_kind == ERR_NETWORK:
            title = "⚠️ Trae 助手授权续期失败：连不上验证服务器"
            lines = ["目标 %s（来源 %s）不可达：%s" % (srv, src, last_detail),
                     "可能是作者搬迁了服务器地址，或本机网络/代理异常。",
                     "如确认新地址：写入 %s 的 server（或 server_fallback）。" % CONFIG_PATH]
            send_alert(cfg, last_kind, title, lines)
            return emit("error", title + " —— " + lines[0],
                        {"hint": "check_network", "server": srv, "server_source": src})

        title = "⚠️ Trae 助手授权续期失败"
        lines = ["服务器返回：%s" % (last_detail or last_kind),
                 "若为 4xx 且口令已确认可用，可能是接口契约变化，请运行 --dump-info 排查。"]
        send_alert(cfg, ERR_UNKNOWN, title, lines)
        return emit("error", title + " —— %s" % (last_detail or last_kind), {"hint": "inspect"})

    # ---- 校验响应 ----
    if "payload_b64" not in resp:
        return emit("error", "激活响应缺少 payload_b64：%s" % json.dumps(resp, ensure_ascii=False)[:200])
    if "signature_b64" not in resp:
        return emit("error", "激活响应缺少 signature_b64")

    try:
        payload = json.loads(base64.b64decode(resp["payload_b64"]))
    except Exception as e:
        return emit("error", "解析激活响应失败: %r" % (e,))

    ok, why, used_key = verify_credential(resp, payload, pubkeys, mid)
    if not ok:
        hint = "inspect"
        if "signature_invalid" in why:
            title = "⚠️ Trae 助手授权续期失败：签名验不过（很可能是作者换了签名密钥）"
            lines = ["服务器签发的凭证无法用本地任一把公钥验签（试过 %d 把）。" % len(pubkeys),
                     "作者若更换了签名密钥对，通常会同步升级 exe；请更新助手后重跑，",
                     "或把新公钥贴进 %s 的 pubkey_extra 数组。" % CONFIG_PATH,
                     "（旧凭证未被改动，程序仍能用到原到期日为止。）"]
            send_alert(cfg, "sig_invalid", title, lines)
            return emit("error", title + " —— " + lines[0], {"hint": "update_pubkey"})
        return emit("error", "新凭证未通过校验（未写盘）: %s" % why, {"hint": hint})

    new_doc = {
        "last_seen": int(time.time()),
        "payload_b64": resp["payload_b64"],
        "signature_b64": resp["signature_b64"],
    }
    try:
        write_license(lic, new_doc)
    except Exception as e:
        return emit("error", "写入授权文件失败（原文件已备份）: %r" % (e,))

    # 清掉失败告警（避免下次误报冷却）
    save_state({"last_alert": {"kind": "", "at": 0},
                "last_ok_server": srv, "last_ok_key": used_key,
                "last_ok_code": used_code[:4] + "*" * max(0, len(used_code) - 4),
                "last_ok_at": int(time.time())})

    newexp = datetime.datetime.fromtimestamp(payload["expires_at"]).strftime("%Y-%m-%d %H:%M:%S")
    return emit("renewed", "✅ 续期成功：新到期 %s（+%.1f 天），已写回 %s（口令 %s…，公钥 %s）"
                % (newexp, (payload["expires_at"] - payload["issued_at"]) / 86400.0, lic,
                   used_code[:4] + "*" * max(0, len(used_code) - 4), used_key),
                {"days_left": round(days_left(payload), 3), "expires_at": payload["expires_at"],
                 "server": srv, "server_source": src, "pubkey": used_key})


if __name__ == "__main__":
    try:
        result = main()
        rc = 0 if result["status"] in ("renewed", "skipped", "check", "dump") else 1
    except KeyboardInterrupt:
        rc = 1
    except Exception as e:
        log("未捕获异常: %r" % (e,))
        rc = 1
    sys.exit(rc)
