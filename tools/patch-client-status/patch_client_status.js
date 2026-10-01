/**
 * patch_client_status.js
 * ---------------------------------------------------------------------------
 * 修复 WorkBuddy 面板「客户端未登录」误报。
 *
 * 原理：exe 内嵌的前端 JS 被 brotli 压缩，解压后改判据，再重压缩覆写回原槽位。
 *       改动点 = refreshWbClient 失败时，退化为「助手账号池非空 = 可用」。
 *
 * 用法：
 *   node patch_client_status.js                       # 默认取脚本上一级的 trae-work-assistant.exe
 *   node patch_client_status.js "<exe路径>"            # 指定 exe
 *   node patch_client_status.js "<exe路径>" --apply    # 备份原文件后替换（助手必须已完全退出）
 *
 *   不带 --apply 时只生成 <exe>.new 并校验，不动原文件。
 *
 * 特性：幂等（已打过补丁会直接退出）、硬门禁（锚点不唯一或压缩超长则拒绝写盘）。
 * 依赖：Node 内置 fs/zlib，无需装包。
 * ---------------------------------------------------------------------------
 */
const fs = require('fs');
const path = require('path');
const zlib = require('zlib');

// 位置参数优先；缺省回退到「脚本上一级目录」的 exe（兼容旧布局）
const POSITIONAL = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const EXE = POSITIONAL[0]
  ? path.resolve(POSITIONAL[0])
  : path.resolve(__dirname, '..', 'trae-work-assistant.exe');
const APPLY = process.argv.includes('--apply');

const PATCH_MARK = 'if(!o){try{o=!!(await X.workbuddy.listAccounts()).length}catch{}}';
const ANCHOR_STORE = 'wbClientLoggedIn:!1,env:null';
const REPL_STORE = 'wbClientLoggedIn:!0,env:null';
const ANCHOR_REFRESH =
  'refreshWbClient:async()=>{try{const r=await X.workbuddy.clientStatus();e({wbClientLoggedIn:r.loggedIn})}catch{}}';
const REPL_REFRESH =
  'refreshWbClient:async()=>{let o=!1;try{o=!!(await X.workbuddy.clientStatus()).loggedIn}catch{}' +
  'if(!o){try{o=!!(await X.workbuddy.listAccounts()).length}catch{}}e({wbClientLoggedIn:o})}';

function fail(msg) {
  console.error('[拒绝] ' + msg);
  process.exit(1);
}

const buf = fs.readFileSync(EXE);
console.log('exe:', EXE);
console.log('size:', buf.length);

// 1) 定位内嵌的 JS 资源：资源按 [路径][压缩数据] 顺序拼接
const jsKeyRe = /\/assets\/index-[A-Za-z0-9_\-]+\.js/g;
const jsKeys = [...buf.toString('latin1').matchAll(jsKeyRe)].map(m => ({ key: m[0], at: m.index }));
if (jsKeys.length === 0) fail('没找到内嵌的 /assets/index-*.js 资源键（exe 结构可能变了）');
const jsKey = jsKeys[0];
const DATA_START = jsKey.at + jsKey.key.length;
console.log('js key:', jsKey.key, '@', jsKey.at, '-> dataStart', DATA_START);

// 段末 = 下一个资源键的位置
const nextKeyRe = /\/(?:assets\/)?[A-Za-z0-9_.\-]+\.(?:js|css|html|png|jpg|jpeg|svg|ico|woff2?|json|wasm|txt)/g;
nextKeyRe.lastIndex = DATA_START;
let slotLen = -1;
for (let m; (m = nextKeyRe.exec(buf.toString('latin1'))); ) {
  if (m.index > DATA_START) { slotLen = m.index - DATA_START; break; }
}
if (slotLen <= 0) slotLen = buf.length - DATA_START;
console.log('slot length:', slotLen);

// 2) 解压（用槽位长度切片即可，解码器读到流结束标记就停）
const origJs = zlib.brotliDecompressSync(buf.subarray(DATA_START, DATA_START + slotLen));
const origTxt = origJs.toString('utf8');
console.log('decompressed js:', origJs.length, 'bytes');

// 3) 幂等检查
if (origTxt.includes(PATCH_MARK)) {
  console.log('\n[已是最新] 该文件已经打过此补丁，无需重复处理。');
  process.exit(0);
}

// 4) 应用补丁（锚点必须唯一）
for (const [name, anchor] of [['store 初值', ANCHOR_STORE], ['refreshWbClient', ANCHOR_REFRESH]]) {
  const n = origTxt.split(anchor).length - 1;
  if (n !== 1) fail(`锚点「${name}」出现 ${n} 次，期望 1 次 —— 前端结构已变，请重新定位锚点`);
}
const patchedTxt = origTxt.replace(ANCHOR_STORE, REPL_STORE).replace(ANCHOR_REFRESH, REPL_REFRESH);
const patchedJs = Buffer.from(patchedTxt, 'utf8');
console.log('patched js:', patchedJs.length, 'bytes (+' + (patchedJs.length - origJs.length) + ')');

// 5) 重新压缩 + 门禁
const comp = zlib.brotliCompressSync(patchedJs, {
  params: {
    [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
    [zlib.constants.BROTLI_PARAM_LGWIN]: 24,
  },
});
const origComp = zlib.brotliCompressSync(origJs, {
  params: {
    [zlib.constants.BROTLI_PARAM_QUALITY]: 11,
    [zlib.constants.BROTLI_PARAM_LGWIN]: 24,
  },
});
console.log('recompressed:', comp.length, '(原文件重压复现:', origComp.length + ')');
if (comp.length > slotLen) fail(`压缩后 ${comp.length} > 槽位 ${slotLen}，无法原位覆写`);
if (comp.length > origComp.length) {
  console.log(`[注意] 新流比原流长 ${comp.length - origComp.length} 字节，依赖解码器忽略尾部残留（已实测成立）`);
}

// 6) 回读校验
const back = zlib.brotliDecompressSync(comp);
if (!back.equals(patchedJs)) fail('回读校验失败');

// 7) 输出
const out = EXE + '.new';
const nb = Buffer.from(buf);
nb.set(comp, DATA_START);
fs.writeFileSync(out, nb);
console.log('\n[OK] 已生成', out);

if (APPLY) {
  const running = (() => {
    try {
      const o = require('child_process').execSync('tasklist /FI "IMAGENAME eq trae-work-assistant.exe"', { stdio: 'pipe' }).toString('latin1');
      return /trae-work-assistant\.exe/i.test(o);
    } catch (e) { return false; }
  })();
  if (running) fail('助手正在运行，请先完全退出再执行 --apply（或用改名法替换）');
  const bak = EXE + '.bak-' + new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14) + '-preClientStatusPatch';
  fs.copyFileSync(EXE, bak);
  console.log('备份 ->', path.basename(bak));
  fs.renameSync(out, EXE);
  console.log('[OK] 已替换', EXE);
} else {
  console.log('（未替换原文件。确认无误后用 --apply，或手动替换。）');
}
