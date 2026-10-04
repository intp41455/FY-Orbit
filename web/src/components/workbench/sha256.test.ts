import { describe, it, expect } from 'vitest';
import { sha256Hex } from './sha256';

// P1-14：哈希工具校验。期望值均为 UTF-8 输入的 SHA-256 十六进制摘要，
// 已预先用 Node crypto / FIPS 180-4 标准向量算好并硬编码，
// 避免测试依赖 @types/node（纯 TS 实现保证 jsdom 与浏览器行为一致）。
describe('sha256Hex', () => {
  it('FIPS 向量：空串', () => {
    expect(sha256Hex('')).toBe('e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855');
  });

  it('FIPS 向量："abc"', () => {
    expect(sha256Hex('abc')).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  });

  it('FIPS 向量：448-bit 消息（跨块边界）', () => {
    expect(sha256Hex('abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq')).toBe(
      '248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1',
    );
  });

  it('多块长消息（"a"×1000）', () => {
    expect(sha256Hex('a'.repeat(1000))).toBe('41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3');
  });

  it('多字节 UTF-8（中文）', () => {
    expect(sha256Hex('你好，备份与回滚 P1-14')).toBe('b59b110de340dc019a9389ed4c4fcb1b3bea9a0dcf69b25c8f9762ae8c0ed74a');
  });

  it('四字节 emoji（代理对）', () => {
    expect(sha256Hex('backup 🚀 ok')).toBe('3867140d84a20e312fa91feadc44bf0909bff96211647e55159f090418f9c548');
  });
});
