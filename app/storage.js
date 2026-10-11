(function (root) {
  'use strict';
  const KEY = 'sparkroom-state-v1';
  const PREVIOUS = 'sparkroom-before-restore-v1';
  const copy = value => JSON.parse(JSON.stringify(value));
  // 39 号文迁移 A1：SQLite 在这一版只做「整块状态 JSON 的耐久存储」，本模块的
  // interface 语义原样保留——字符串级 CAS、替换前快照、恢复保护都不变。
  //
  // storage() 返回 {getItem(key), setItem(key, value)}：浏览器 localStorage 是同步的，
  // 桌面壳 Tauri → SQLite 返回 Promise。本模块一律按 Promise 处理，并让写入串行排队——
  // 否则连续两次保存会各自拿同一个 expected 去 CAS，后一次将被误判为「另一窗口已更新」。
  function createWorkspace({ storage, initial, validate }) {
    let current = initial(), raw, readable = false, error = null, queue = Promise.resolve();
    function status() { return { raw, readable, recovery: error !== null, error }; }
    function snapshot() { return { ...status(), state: copy(current) }; }
    function enqueue(task) {
      const run = queue.then(task, task);  // 前一次失败不阻断后续写入
      queue = run.then(() => {}, () => {});
      return run;
    }
    async function reload() {
      raw = undefined; readable = false; error = null;
      try {
        raw = await storage().getItem(KEY); readable = true;
        current = raw === null ? validate(initial()) : validate(JSON.parse(raw));
      } catch (failure) {
        error = failure.message || '无法读取本地空间';
        current = initial();
      }
      return snapshot();
    }
    async function unchanged(expected) {
      if (!readable) throw Error('无法读取原始存储，请先重试读取；尚未替换任何数据。');
      if (await storage().getItem(KEY) !== expected) throw Error('另一窗口已更新资料，请关闭当前窗口中的编辑或预览后重新打开，避免覆盖新内容。');
    }
    async function write(next, expected) {
      const candidate = copy(validate(next));
      candidate.revision++;
      const encoded = JSON.stringify(candidate);
      await unchanged(expected);
      await storage().setItem(KEY, encoded);
      current = candidate; raw = encoded; readable = true; error = null;
      return snapshot();
    }
    function commit(next) {
      if (error !== null) return Promise.reject(Error('本地空间处于恢复保护中，请先导出原始数据，再选择有效备份恢复。'));
      // expected 在任务真正执行时才取 raw——排队中的前一次写入可能已经推进了它。
      return enqueue(() => write(next, raw)).catch(failure => { throw Error('本地保存失败：' + failure.message); });
    }
    function replace(next, expected) {
      return enqueue(async () => {
        validate(next);
        await unchanged(expected);
        // A durable raw snapshot is required before replacing even a damaged workspace.
        // If either write fails, storage keeps the old main value intact.
        await storage().setItem(PREVIOUS, JSON.stringify({ format: 'sparkroom-previous', raw: expected, savedAt: new Date().toISOString() }));
        return write(next, expected);
      });
    }
    function previous() {
      return enqueue(async () => {
        const encoded = await storage().getItem(PREVIOUS);
        if (encoded === null) throw Error('还没有恢复前快照。');
        const value = JSON.parse(encoded);
        if (!value || value.format !== 'sparkroom-previous' || (value.raw !== null && typeof value.raw !== 'string')) throw Error('恢复前快照无法识别，请保留原文件。');
        return value;
      });
    }
    return { status, snapshot, reload, commit, replace, previous };
  }
  const api = { KEY, PREVIOUS, createWorkspace };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.SparkStorage = api;
})(typeof window === 'undefined' ? globalThis : window);
