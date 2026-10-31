#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sky 私服 - iOS (Mach-O) 证书校验绕过脚本
用法 (v2):
    python patch_sky_ios.py <输入> [选项]

    <输入> 可以是:
      .ipa / .zip   安装包: 自动解包 -> 定位主二进制 -> 修补 -> 重新打包
      .app 目录     macOS 应用 / 已解包的 iOS App(改包内主二进制, 带 .bak 备份)
      Mach-O 文件   直接改这个可执行文件(带 .bak_original 备份)
      普通目录      递归批量处理其中所有 ipa/zip/.app/裸二进制

    选项:
      --out DIR     输出目录(默认 <输入目录>/patched, 不会覆盖原包)
      --in-place    原地修改(.app 目录/裸二进制; ipa 会重建并覆盖原文件)
      --dry-run     只分析, 不写任何文件(适合先体检整个仓库)
      --json PATH   把逐项结果写成 JSON 报告
      --quiet       只输出结论
      --force       输出文件已存在时直接覆盖

    加密检测: App Store 下发的 ipa 带 FairPlay 加密(cryptid=1), 代码段是密文,
    改不了。脚本会自动识别并归类为 "encrypted", 提示先用解密工具处理。

适配对象:
    iOS / iOS 模拟器 的 Sky 主程序 (Mach-O), 与安卓的 libBootloader.so 不同:
    - 安卓: 静态链接 BoringSSL, patch ssl_verify_cert_chain / X509_verify_cert。
    - iOS : 用苹果 Secure Transport (SSLHandshake/SSLCopyPeerTrust/SecTrustEvaluate),
            没有 BoringSSL 符号。游戏自己在 "服务器证书校验" 回调里:
              SSLHandshake -> 返回 -9841(errSSLServerAuthCompleted)
              -> SSLCopyPeerTrust 取出对端证书
              -> SecTrustEvaluate 评估信任结果到局部变量
              -> 判 result==4(kSecTrustResultUnspecified, 合法) 则成功
              -> test ebx,ebx ; je 成功分支
            本脚本把这条 "信任结果判定后的成功跳转" 的条件跳转(je)改成无条件跳转(jmp),
            使证书信任永远通过; 同时保留真正的传输层错误(非 auth 的握手错误码、
            SSLCopyPeerTrust 失败) —— 只放过证书信任, 不破坏正常报错。

原理 (x86_64):
    定位: 符号表找 _SecTrustEvaluate -> 间接符号表映射到 __stubs 槽 -> 在 __text 里
          找到唯一一条 E8 call 该 stub 的指令 -> 向后扫描该函数尾部的
          特征 `85 DB 0F 84 rel32`(test ebx,ebx ; je 成功分支)。
    修补: 0F 84(je near, 6 字节) -> E9(jmp near, 5 字节) + 90(NOP) 占满 6 字节,
          rel32 自动 +1 以补偿 je(6B) 与 jmp(5B) 的长度差, 跳转目标不变。
    自动备份原始文件, 幂等(已 patch 可识别)。

全版本适配:
    - arm64(iOS 真机): 不依赖固定地址/固定版本, 按指令特征覆盖三条信任路径:
        SecureTransport 回调(SecTrustEvaluate -> CSEL -> CBZ)、
        NSURLSession challenge(result==1/4 判定)、
        NSURLSession dispatch block(allow 写入 completion block)。
      任一版本缺少某条路径时只跳过该条并报告, 不会误改其它代码。
    - 若二进制导入 SecTrustEvaluateWithError(新 SDK 常见), 直接把该 stub 改成恒 true。
    - x86_64(Mac/模拟器): 仍按 `test ebx,ebx ; je` 特征修补。
    - 所有版本均幂等: 已 patch 的文件再次运行只报告 "已经 patch 过"。
"""

import sys
import os
import io
import re
import json
import time
import struct
import shutil
import zipfile
import plistlib
import tempfile
import argparse
import contextlib

# ---- Mach-O 常量 ----
MH_MAGIC_64 = 0xFEEDFACF       # 64-bit little-endian
MH_CIGAM_64 = 0xCFFAEDFE       # 64-bit (raw LE bytes of the magic as seen big-endian)
FAT_MAGIC = 0xCAFEBABE
FAT_CIGAM = 0xBEBAFECF
LC_SYMTAB = 0x2
LC_DYSYMTAB = 0xB
LC_SEGMENT_64 = 0x19
CPU_TYPE_X86_64 = 0x01000007
CPU_TYPE_ARM64 = 0x0100000C


def u32(d, o): return struct.unpack_from("<I", d, o)[0]
def u64(d, o): return struct.unpack_from("<Q", d, o)[0]
def i32(d, o): return struct.unpack_from("<i", d, o)[0]


class MachO:
    """单架构(thin) Mach-O 解析器, 只取本脚本需要的: 段/节、符号表、间接符号表。"""

    def __init__(self, data, base=0):
        self.data = data
        self.base = base          # fat 切片在原文件里的起始偏移(thin 时为 0)
        self.sections = []        # (segname, sectname, addr, size, file_offset, reserved1, reserved2)
        self.symoff = self.nsyms = self.stroff = self.strsize = 0
        self.indirectsymoff = self.nindirectsyms = 0
        self.cputype = u32(data, base + 4)
        self._parse()

    def _parse(self):
        d, b = self.data, self.base
        ncmds = u32(d, b + 16)
        off = b + 32
        for _ in range(ncmds):
            cmd = u32(d, off)
            cmdsize = u32(d, off + 4)
            if cmd == LC_SEGMENT_64:
                nsects = u32(d, off + 64)
                so = off + 72
                for _s in range(nsects):
                    sect = d[so:so + 16].split(b'\0')[0].decode('ascii', 'replace')
                    seg = d[so + 16:so + 32].split(b'\0')[0].decode('ascii', 'replace')
                    addr = u64(d, so + 32)
                    size = u64(d, so + 40)
                    foff = u32(d, so + 48)
                    r1 = u32(d, so + 68)
                    r2 = u32(d, so + 72)
                    self.sections.append((seg, sect, addr, size, foff, r1, r2))
                    so += 80
            elif cmd == LC_SYMTAB:
                self.symoff = u32(d, off + 8)
                self.nsyms = u32(d, off + 12)
                self.stroff = u32(d, off + 16)
                self.strsize = u32(d, off + 20)
            elif cmd == LC_DYSYMTAB:
                self.indirectsymoff = u32(d, off + 8 + 12 * 4)
                self.nindirectsyms = u32(d, off + 8 + 13 * 4)
            off += cmdsize

    def section(self, segname, sectname):
        for s in self.sections:
            if s[0] == segname and s[1] == sectname:
                return s
        return None

    def symname(self, idx):
        d = self.data
        e = self.base + self.symoff + idx * 16
        nx = u32(d, e)
        start = self.base + self.stroff + nx
        end = self.data.find(b'\0', start)
        return self.data[start:end].decode('ascii', 'replace')

    def stub_addr(self, target_symbol):
        """返回 __stubs 里指向 target_symbol 的那一槽的虚拟地址, 找不到返回 None。"""
        stubs = self.section("__TEXT", "__stubs")
        if not stubs:
            return None
        _seg, _sect, addr, size, _foff, r1, r2 = stubs
        stubsize = r2 if r2 else 6
        n = size // stubsize
        d = self.data
        for k in range(n):
            ii = self.base + self.indirectsymoff + (r1 + k) * 4
            if ii + 4 > len(d):
                break
            isym = u32(d, ii)
            # 0x80000000=INDIRECT_SYMBOL_LOCAL, 0xC0000000 等为特殊标记, 跳过
            if isym & 0x80000000:
                continue
            if self.symname(isym) == target_symbol:
                return addr + k * stubsize
        return None

    def text(self):
        return self.section("__TEXT", "__text")

    def va_to_fileoff(self, va):
        # 先按节表匹配(可正确处理 __stubs 等非 __text 节), 再退回 __TEXT 连续映射。
        for _seg, _sect, addr, size, foff, _r1, _r2 in self.sections:
            if size and addr <= va < addr + size:
                return self.base + foff + (va - addr)
        t = self.text()
        _seg, _sect, addr, _size, foff, _r1, _r2 = t
        return self.base + foff + (va - addr)


def find_call_sites(macho, stub_va):
    """在 __text 里找所有 `E8 rel32` 直接 call 命中 stub_va 的指令地址(虚拟地址)。"""
    t = macho.text()
    _seg, _sect, taddr, tsize, tfoff, _r1, _r2 = t
    d = macho.data
    base = macho.base + tfoff
    sites = []
    for i in range(tsize - 5):
        if d[base + i] == 0xE8:
            rel = i32(d, base + i + 1)
            ip = taddr + i
            if ip + 5 + rel == stub_va:
                sites.append(ip)
    return sites


# x86_64 特征: test ebx,ebx ; je near  ==  85 DB 0F 84 rel32
SIG_TEST_JE = bytes([0x85, 0xDB, 0x0F, 0x84])
# 已 patch 后的形态: test ebx,ebx ; jmp near == 85 DB E9 rel32 (用于幂等识别)
SIG_TEST_JMP = bytes([0x85, 0xDB, 0xE9])


def find_trust_je(macho, sectrust_call_va, window=0x140):
    """从 SecTrustEvaluate 调用点向后(同一函数尾部)扫描信任结果判定的
    `test ebx,ebx ; je 成功分支`(或已 patch 的 jmp 形态)。
    返回 (je/jmp 指令的虚拟地址, 是否已patch)。找不到返回 (None, False)。"""
    d = macho.data
    start_fo = macho.va_to_fileoff(sectrust_call_va)
    blob = d[start_fo:start_fo + window]
    idx = blob.find(SIG_TEST_JE)
    if idx >= 0:
        return sectrust_call_va + idx + 2, False
    idx = blob.find(SIG_TEST_JMP)
    if idx >= 0:
        return sectrust_call_va + idx + 2, True
    return None, False


def patch_je_to_jmp(data, je_fileoff):
    """0F 84 rel32 (je, 6B) -> E9 rel32+1 (jmp, 5B) + 90 (NOP)。返回(原6字节, 新6字节)。"""
    orig = bytes(data[je_fileoff:je_fileoff + 6])
    if orig[0:2] != b'\x0f\x84':
        raise ValueError("目标不是 je near(0F 84): %s" % orig.hex(' ').upper())
    rel = struct.unpack_from("<i", orig, 2)[0]
    new_rel = rel + 1  # 补偿 je(6B)->jmp(5B) 的 1 字节长度差, 跳转目标不变
    new = b'\xe9' + struct.pack("<i", new_rel) + b'\x90'
    data[je_fileoff:je_fileoff + 6] = new
    return orig, bytes(new)


# ============================================================
# ARM64 (真机 iOS) 支持
# ============================================================
def find_call_sites_arm64(macho, stub_va):
    """在 __text 里找所有 BL 直接跳到 stub_va 的指令地址(虚拟地址)。
    ARM64 BL: 高 6 位 = 0b100101; 目标 = pc + sign_ext(imm26)<<2。"""
    t = macho.text()
    _seg, _sect, taddr, tsize, tfoff, _r1, _r2 = t
    d = macho.data
    base = macho.base + tfoff
    sites = []
    for o in range(0, tsize - 3, 4):
        w = u32(d, base + o)
        if (w >> 26) == 0x25:
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            ip = taddr + o
            if ip + imm * 4 == stub_va:
                sites.append(ip)
    return sites


def find_trust_cbz_arm64(macho, sectrust_call_va, window=0x60):
    """从 SecTrustEvaluate 调用点向后扫描信任结果判定分支。
    精确定位: 信任结果由 `csel Wd, Ww, Ww, cond` 写入(Wd 即结果寄存器),
    其后的 `cbz Wd, 成功分支` 才是决定性跳转。只认 32 位(sf=0)且寄存器号匹配 Wd 的 cbz,
    从而跳过中间的 `cbz x0`(64 位空指针清理)等无关分支。
    返回 (cbz/b 指令虚拟地址, 是否已patch, 结果寄存器号)。找不到返回 (None, False, None)。"""
    t = macho.text()
    _seg, _sect, taddr, tsize, tfoff, _r1, _r2 = t
    d = macho.data
    start_fo = macho.va_to_fileoff(sectrust_call_va)
    result_reg = None
    for o in range(0, window, 4):
        w = u32(d, start_fo + o)
        va = sectrust_call_va + o
        # CSEL Wd: 0x1A800000 掩码 0x7FE00C00 == 0x1A800000(32位 csel, op2=00)
        if (w & 0xFFE00C00) == 0x1A800000:
            result_reg = w & 0x1F
            continue
        if result_reg is None:
            continue
        # CBZ Wn(32位, sf=0): 掩码 0xFF000000 == 0x34000000, 且 Rt==result_reg
        if (w & 0xFF000000) == 0x34000000 and (w & 0x1F) == result_reg:
            return va, False, result_reg
        # 已 patch: 无条件 b 覆盖在该位置(B 掩码 0xFC000000==0x14000000)
        if (w & 0xFC000000) == 0x14000000 and o >= 4:
            # 仅当紧邻 csel 之后(即原 cbz 位置)才认定已patch
            return va, True, result_reg
    return None, False, None


def site_preceded_by(macho, site_va, prev_stub_va, back=0x60):
    """判断 site_va 之前 back 字节内是否有 BL 调用 prev_stub_va(用于锁定自定义校验回调)。"""
    t = macho.text()
    _seg, _sect, taddr, tsize, tfoff, _r1, _r2 = t
    d = macho.data
    for o in range(4, back + 4, 4):
        va = site_va - o
        if va < taddr:
            break
        w = u32(d, macho.base + tfoff + (va - taddr))
        if (w >> 26) == 0x25:
            imm = w & 0x03FFFFFF
            if imm & 0x02000000:
                imm -= 0x04000000
            if va + imm * 4 == prev_stub_va:
                return True
    return False


def patch_cbz_to_b_arm64(data, fileoff):
    """ARM64: CBZ Wn, target -> B target(无条件)。imm 范围不同(cbz imm19, b imm26),
    target 不变。返回(原4字节, 新4字节)。"""
    w = struct.unpack_from("<I", data, fileoff)[0]
    if (w & 0x7F000000) != 0x34000000:
        raise ValueError("目标不是 CBZ: 0x%08X" % w)
    imm19 = (w >> 5) & 0x7FFFF
    if imm19 & 0x40000:
        imm19 -= 0x80000
    # CBZ 的跳转偏移(指令数), 直接复用为 B 的 imm26
    new = 0x14000000 | (imm19 & 0x03FFFFFF)
    orig = struct.pack("<I", w)
    newb = struct.pack("<I", new)
    data[fileoff:fileoff + 4] = newb
    return orig, newb


# ============================================================
# ARM64: NSURLSession / HTTP 栈的证书校验补丁
# ------------------------------------------------------------
# 只补 Secure Transport 的 "服务器证书" 回调还不够: 客户端登录/账号等
# HTTP 请求走 NSURLSession, 在下面两个位置还会评估一次证书:
#   1) -[XXX handleChallenge:fromURLSession:completionHandler:] 里同步调用
#      -[XXX evaluateTrust:result:] (内部 SecTrustEvaluate),
#      然后按 result==1/4 决定是否用 serverTrust 构造 NSURLCredential。
#   2) 一个 dispatch_async 到全局队列的 block, 自己 SecTrustEvaluate 后把
#      allow(BOOL) 交给捕获的 completion block。
# 不补这两处时, 即使 SecureTransport 放行了, 登录请求仍会被 URLSession
# 判为证书失败, 表现就是 "改了也没用"。
# ============================================================

def _arm64_bcond_target(w, va):
    imm19 = (w >> 5) & 0x7FFFF
    if imm19 & 0x40000:
        imm19 -= 0x80000
    return va + (imm19 << 2)


def _arm64_b_target(w, va):
    imm26 = w & 0x03FFFFFF
    if imm26 & 0x02000000:
        imm26 -= 0x04000000
    return va + (imm26 << 2)


def _arm64_b(addr, target):
    off = (target - addr) >> 2
    if off < -(1 << 25) or off >= (1 << 25):
        raise ValueError("B 跳转超出范围")
    return 0x14000000 | (off & 0x03FFFFFF)


def _match_trust_result(data, fo, va):
    """判断 fo(虚拟地址 va) 处是否是 "信任结果 == 1/4" 的判定序列:
        ldr wN,[sp,#x] ; cmp wN,#1 ; b.eq OK ; cmp wN,#4 ; b.ne FAIL
    (#4/#1 顺序也接受; N 可以是任意寄存器, 3.12/3.13 用 w8, 新版用 w0)

    返回 (OK 地址, 5 条指令的字, 是否已 patch):
        - 未 patch: 最后一条是 b.ne FAIL
        - 已 patch: 最后一条是 b OK
    不匹配返回 (None, None, False)。"""
    w1 = u32(data, fo)
    w2 = u32(data, fo + 4)
    w3 = u32(data, fo + 8)
    w4 = u32(data, fo + 12)
    w5 = u32(data, fo + 16)
    rt = w1 & 0x1F
    if not ((w1 & 0xFFC00000) == 0xB9400000 and ((w1 >> 5) & 0x1F) == 31):
        return None, None, False
    for first, second in ((1, 4), (4, 1)):
        if not ((w2 & 0xFFC00000) == 0x71000000 and ((w2 >> 10) & 0xFFF) == first
                and ((w2 >> 5) & 0x1F) == rt and (w2 & 0x1F) == 31):
            continue
        if not ((w3 & 0xFF000010) == 0x54000000 and (w3 & 0xF) == 0):
            continue
        if not ((w4 & 0xFFC00000) == 0x71000000 and ((w4 >> 10) & 0xFFF) == second
                and ((w4 >> 5) & 0x1F) == rt and (w4 & 0x1F) == 31):
            continue
        ok_va = _arm64_bcond_target(w3, va + 8)
        if (w5 & 0xFF000010) == 0x54000000 and (w5 & 0xF) == 1:
            return ok_va, (w1, w2, w3, w4, w5), False
        if (w5 & 0xFC000000) == 0x14000000 and _arm64_b_target(w5, va + 16) == ok_va:
            return ok_va, (w1, w2, w3, w4, w5), True
    return None, None, False


def _mark(fo, va, old_w, new_w):
    return {"va": va, "fo": fo,
            "orig": struct.pack("<I", old_w),
            "new": struct.pack("<I", new_w)}


def patch_urlsession_challenge_arm64(macho, data):
    """绕过 NSURLSession challenge 处理里的信任判定(所有已知 Sky iOS 版本通用)。

    在 __text 全量扫描编译器的稳定形态:
        ldr  wN,[sp,#x]
        cmp  wN,#1
        b.eq OK
        cmp  wN,#4
        b.ne FAIL

    处理:
        1) b.ne FAIL -> b OK (结果 1/4 都放行);
        2) 若 0x60 字节内还有一条 `cbz wM -> 本判定` 的状态检查, 一并改成 b,
           使 SecTrustEvaluate/objc_msgSend 的非 0 状态不再短路到 FAIL。
        (状态寄存器在 3.12/3.13 是 w8, 新版是 w0, 这里不假定具体寄存器。)

    返回 (changes, already)。
    """
    changes = []
    already = 0
    t = macho.text()
    _g, _s, taddr, tsize, tfoff, _r1, _r2 = t
    off = 0
    while off < tsize - 20:
        va = taddr + off
        fo = macho.base + tfoff + off
        ok_va, words, is_patched = _match_trust_result(data, fo, va)
        if ok_va is None:
            off += 4
            continue

        # 向前找 "状态检查" 分支: cbz wM -> 本判定 (或已 patch 的 b -> 本判定)
        cbz_va = None
        cbz_fo = None
        cbz_is_b = False
        for back in range(4, 0x61, 4):
            a = va - back
            if a < taddr:
                break
            fa = macho.base + tfoff + (a - taddr)
            wa = u32(data, fa)
            if (wa & 0x7F000000) == 0x34000000 and (wa & 0x1F) != 31:
                imm19 = (wa >> 5) & 0x7FFFF
                if imm19 & 0x40000:
                    imm19 -= 0x80000
                if a + (imm19 << 2) == va:
                    cbz_va, cbz_fo = a, fa
                    break
            elif (wa & 0xFC000000) == 0x14000000:
                if _arm64_b_target(wa, a) == va:
                    cbz_va, cbz_fo, cbz_is_b = a, fa, True
                    break

        if cbz_va is not None:
            if cbz_is_b:
                already += 1
            else:
                new_b = _arm64_b(cbz_va, va)
                old_b = u32(data, cbz_fo)
                data[cbz_fo:cbz_fo + 4] = struct.pack("<I", new_b)
                changes.append(_mark(cbz_fo, cbz_va, old_b, new_b))
                print("  [OK] URLSession 证书判定 cbz->b @ 0x%011X" % cbz_va)

        if is_patched:
            already += 1
        else:
            new_ne = _arm64_b(va + 16, ok_va)
            data[fo + 16:fo + 20] = struct.pack("<I", new_ne)
            changes.append(_mark(fo + 16, va + 16, words[4], new_ne))
            print("  [OK] URLSession 证书判定 b.ne->b @ 0x%011X" % (va + 16))
        off += 20
    return changes, already


def _scan_dispatch_window(macho, data, start_va, length, seen):
    """在 [start_va, start_va+length) 内找 dispatch block 的 allow 判定:
        ... orr w1,w9,w8 ; ldr x0,[x19,#0x28] ; ldr x8,[x0,#0x10] ; blr x8
        ... mov w1,#0    ; b 上述 block 调用
    两条路径的 w1(allow) 都改成 mov w1,#1。
    返回 (changes, already, found)。"""
    changes = []
    already = 0
    found = 0
    t = macho.text()
    _g, _s, taddr, tsize, tfoff, _r1, _r2 = t
    for step in range(0, max(0, length - 16), 4):
        va = start_va + step
        if va < taddr or va + 16 > taddr + tsize:
            break
        if va in seen:
            continue
        fo = macho.base + tfoff + (va - taddr)
        w = u32(data, fo)
        is_orr = ((w & 0xFF800000) == 0x2A000000
                  and ((w >> 21) & 1) == 0 and ((w >> 10) & 0x3F) == 0
                  and (w & 0x1F) == 1)
        is_mov1 = (w == 0x52800021)   # 已 patch 过的形态
        if not (is_orr or is_mov1):
            continue
        w0 = u32(data, fo + 4)
        w1 = u32(data, fo + 8)
        w2 = u32(data, fo + 12)
        # ldr x0,[xN,#imm] ; ldr x8,[x0,#0x10] ; blr x8  (调用捕获的 block)
        if not ((w0 & 0xFFC00000) == 0xF9400000 and (w0 & 0x1F) == 0
                and (w1 & 0xFFC00000) == 0xF9400000 and (w1 & 0x1F) == 8
                and ((w1 >> 5) & 0x1F) == 0
                and (w2 & 0xFFFFFC1F) == 0xD63F0000
                and ((w2 >> 5) & 0x1F) == 8):
            continue
        # 同一 block 调用的另一条路径: mov w1,#0 ; b block_call
        for back in range(4, 0x100, 4):
            a = va - back
            if a < taddr:
                break
            fa = macho.base + tfoff + (a - taddr)
            wa = u32(data, fa)
            wb = u32(data, fa + 4)
            if wa not in (0x52800001, 0x52800021):
                continue
            if (wb & 0xFC000000) != 0x14000000:
                continue
            if _arm64_b_target(wb, a + 4) != va + 4:
                continue
            seen.add(va)
            seen.add(a)
            found += 1
            if wa == 0x52800021 and w == 0x52800021:
                already += 2
                break
            if wa == 0x52800001:
                data[fa:fa + 4] = struct.pack("<I", 0x52800021)
                changes.append(_mark(fa, a, wa, 0x52800021))
                print("  [OK] URLSession block allow=1(错误分支) @ 0x%011X" % a)
            if w == 0x52800021:
                already += 1
            else:
                data[fo:fo + 4] = struct.pack("<I", 0x52800021)
                changes.append(_mark(fo, va, w, 0x52800021))
                print("  [OK] URLSession block allow=1(结果分支) @ 0x%011X" % va)
            break
    return changes, already, found


def patch_urlsession_dispatch_arm64(macho, data, sites=None):
    """绕过 dispatch_async block 里的信任判定(登录等 NSURLSession 请求)。

    优先在每个 SecTrustEvaluate 调用点后 0x180 字节内找; 找不到时(例如新版只导入
    SecTrustEvaluateWithError, 或调用点结构变化)再全 __text 扫描一次, 保证全版本可用。
    返回 (changes, already, found)。"""
    changes = []
    already = 0
    found = 0
    seen = set()
    for site in (sites or []):
        c, a, f = _scan_dispatch_window(macho, data, site, 0x180, seen)
        changes += c
        already += a
        found += f
    if found == 0:
        t = macho.text()
        _g, _s, taddr, tsize, _f, _r1, _r2 = t
        c, a, f = _scan_dispatch_window(macho, data, taddr, tsize, seen)
        changes += c
        already += a
        found += f
    return changes, already, found


def iter_thin_slices(data):
    """返回 [(base_offset, cputype)]。支持 fat 和 thin。"""
    magic = u32(data, 0)
    if magic in (FAT_MAGIC, FAT_CIGAM):
        # fat header 是大端
        nfat = struct.unpack_from(">I", data, 4)[0]
        slices = []
        o = 8
        for _ in range(nfat):
            cputype = struct.unpack_from(">i", data, o)[0] & 0xffffffff
            offset = struct.unpack_from(">I", data, o + 8)[0]
            slices.append((offset, cputype))
            o += 20
        return slices
    if magic == MH_MAGIC_64:
        return [(0, u32(data, 4))]
    raise ValueError("不是 Mach-O 文件 (magic=0x%08X)" % magic)


def patch_slice(data, base):
    """对单个架构切片执行定位+修补。返回 (changes, already, arch_name)。
    支持 x86_64(Mac/模拟器)与 arm64(iOS 真机)。"""
    macho = MachO(data, base)
    arch = {CPU_TYPE_X86_64: "x86_64", CPU_TYPE_ARM64: "arm64"}.get(
        macho.cputype, "0x%08X" % macho.cputype)
    changes = []
    already = 0

    if macho.cputype not in (CPU_TYPE_X86_64, CPU_TYPE_ARM64):
        print("  [跳过] 架构 %s: 不支持。" % arch)
        return changes, already, arch

    stub = macho.stub_addr("_SecTrustEvaluate")
    if stub is None:
        print("  [提示] 未找到 _SecTrustEvaluate stub(新版可能只导入 SecTrustEvaluateWithError)")
    else:
        print("  _SecTrustEvaluate stub @ 0x%011X" % stub)

    if macho.cputype == CPU_TYPE_ARM64:
        return _patch_slice_arm64(macho, data, stub, arch)

    # ---- x86_64 ----
    c, a = patch_trust_with_error_stub_x86(macho, data)
    changes += c
    already += a
    if stub is None:
        print("  [跳过] 未找到 _SecTrustEvaluate, 仅处理了 SecTrustEvaluateWithError")
        return changes, already, arch
    sites = find_call_sites(macho, stub)
    if not sites:
        print("  [跳过] 未找到调用 _SecTrustEvaluate 的指令")
        return changes, already, arch
    print("  SecTrustEvaluate 调用点: %s" % ", ".join("0x%011X" % s for s in sites))

    for site in sites:
        je_va, is_patched = find_trust_je(macho, site)
        if je_va is None:
            continue
        je_fo = macho.va_to_fileoff(je_va)
        if is_patched or data[je_fo] == 0xE9:
            print("  [已patch] 0x%011X 已是无条件跳转, 跳过" % je_va)
            already += 1
            continue
        head = bytes(data[je_fo:je_fo + 2])
        if head != b'\x0f\x84':
            print("  [跳过] 0x%011X 不是预期的 je(0F 84): %s" % (je_va, head.hex(' ').upper()))
            continue
        orig, newb = patch_je_to_jmp(data, je_fo)
        changes.append({"va": je_va, "fo": je_fo, "orig": orig, "new": newb})
        print("  [OK] 信任结果成功跳转 je->jmp @ 0x%011X" % je_va)
        print("       原: %s" % orig.hex(' ').upper())
        print("       补: %s" % newb.hex(' ').upper())
    return changes, already, arch


def patch_trust_with_error_stub_arm64(macho, data):
    """新版本 iOS 可能只导入 SecTrustEvaluateWithError(SecTrustRef, CFErrorRef*)。
    该函数只返回 bool, 可以直接把 stub 改成 `mov w0,#1 ; ret`, 永远返回 true。
    已 patch 则返回 already=1。"""
    stub = macho.stub_addr("_SecTrustEvaluateWithError")
    if stub is None:
        return [], 0
    fo = macho.va_to_fileoff(stub)
    if u32(data, fo) == 0x52800021 and u32(data, fo + 4) == 0xD65F03C0:
        print("  [已patch] SecTrustEvaluateWithError 已返回 true @ 0x%011X" % stub)
        return [], 1
    orig = bytes(data[fo:fo + 8])
    data[fo:fo + 4] = struct.pack("<I", 0x52800021)      # mov w0,#1
    data[fo + 4:fo + 8] = struct.pack("<I", 0xD65F03C0)  # ret
    print("  [OK] SecTrustEvaluateWithError -> 永远 true @ 0x%011X" % stub)
    return [{"va": stub, "fo": fo, "orig": orig, "new": bytes(data[fo:fo + 8])}], 0


def patch_trust_with_error_stub_x86(macho, data):
    """x86_64 版: stub(6 字节 jmp) -> `mov eax,1 ; ret`。"""
    stub = macho.stub_addr("_SecTrustEvaluateWithError")
    if stub is None:
        return [], 0
    fo = macho.va_to_fileoff(stub)
    new = b'\xB8\x01\x00\x00\x00\xC3'
    if bytes(data[fo:fo + 6]) == new:
        print("  [已patch] SecTrustEvaluateWithError 已返回 true @ 0x%011X" % stub)
        return [], 1
    orig = bytes(data[fo:fo + 6])
    data[fo:fo + 6] = new
    print("  [OK] SecTrustEvaluateWithError -> 永远 true @ 0x%011X" % stub)
    return [{"va": stub, "fo": fo, "orig": orig, "new": new}], 0


def _patch_slice_arm64(macho, data, stub, arch):
    """arm64 全版本证书校验绕过。

    覆盖 3 条独立路径(不同版本的结构可能略有差异, 缺哪条只跳过哪条):
      1) SecureTransport 回调: SecTrustEvaluate 之后 CSEL 结果 + CBZ 是否成功;
      2) NSURLSession challenge: 全 __text 扫描 "result==1/4" 判定;
      3) NSURLSession dispatch block: 结果 allow 写进 completion block 调用;
    另外若导入 SecTrustEvaluateWithError(新版本 Tendencies), 直接把 stub 改成 true。
    """
    changes = []
    already = 0
    peer_stub = macho.stub_addr("_SSLCopyPeerTrust")
    sites = find_call_sites_arm64(macho, stub) if stub is not None else []
    if stub is None:
        print("  [提示] 未找到 _SecTrustEvaluate, 继续尝试 SecTrustEvaluateWithError / 特征匹配")
    elif not sites:
        print("  [提示] 未找到调用 _SecTrustEvaluate 的 BL 指令")
    else:
        print("  SecTrustEvaluate 调用点: %s" % ", ".join("0x%011X" % s for s in sites))

    ssl_ok = ssl_total = 0
    for site in sites:
        # 锁定自定义校验回调: 该 SecTrustEvaluate 之前应有 SSLCopyPeerTrust 调用
        if peer_stub is not None and not site_preceded_by(macho, site, peer_stub, back=0x80):
            print("  [跳过] 0x%011X 非自定义证书校验回调(前方无 SSLCopyPeerTrust)" % site)
            continue
        ssl_total += 1
        cbz_va, is_patched, reg = find_trust_cbz_arm64(macho, site)
        if cbz_va is None:
            print("  [跳过] 0x%011X 之后未找到信任结果 cbz" % site)
            continue
        fo = macho.va_to_fileoff(cbz_va)
        if is_patched:
            print("  [已patch] 0x%011X 已是无条件 b, 跳过" % cbz_va)
            already += 1
            ssl_ok += 1
            continue
        try:
            orig, newb = patch_cbz_to_b_arm64(data, fo)
        except ValueError as e:
            print("  [跳过] %s" % e)
            continue
        changes.append({"va": cbz_va, "fo": fo, "orig": orig, "new": newb})
        ssl_ok += 1
        print("  [OK] 信任结果成功跳转 cbz w%d->b @ 0x%011X" % (reg, cbz_va))
        print("       原: %s" % orig.hex(' ').upper())
        print("       补: %s" % newb.hex(' ').upper())

    # ---- NSURLSession / HTTP 栈的证书校验(登录等请求) ----
    c, a = patch_urlsession_challenge_arm64(macho, data)
    changes += c
    already += a
    challenge_ok = (len(c) + a) > 0

    c, a, found = patch_urlsession_dispatch_arm64(macho, data, sites)
    changes += c
    already += a
    dispatch_ok = found > 0

    c, a = patch_trust_with_error_stub_arm64(macho, data)
    changes += c
    already += a
    witherror_ok = (len(c) + a) > 0

    print("  [覆盖] SecureTransport %d/%d | URLSession challenge %s | "
          "URLSession block %s | SecTrustEvaluateWithError %s"
          % (ssl_ok, ssl_total,
             "OK" if challenge_ok else "未发现",
             "OK" if dispatch_ok else "未发现",
             "OK" if witherror_ok else "未导入"))
    return changes, already, arch


# ============================================================
#  v2 新增: 容器层 (ipa / zip / .app / 目录/裸二进制)
#          + 加密检测 + 批量扫描 + 重新打包 + JSON 报告
# ============================================================
VERSION = "2.0"

MACHO_MAGIC_BYTES = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                     b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce")
FAT_MAGIC_BYTES = (b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca")
LC_ENCRYPTION_INFO = 0x21
LC_ENCRYPTION_INFO_64 = 0x2C
CPU_NAMES = {CPU_TYPE_X86_64: "x86_64", CPU_TYPE_ARM64: "arm64",
             0x00000007: "i386", 0x0000000C: "arm"}
# zip 里明显不是主二进制的目录/后缀
SKIP_DIR_HINTS = ("_CodeSignature/", "SC_Info/", "__MACOSX/", "Frameworks/",
                  "PlugIns/", "Watch/", "AppClips/", "Extensions/")
SKIP_EXT = (".dylib", ".so", ".a", ".plist", ".plist.bin", ".car", ".nib",
            ".png", ".jpg", ".jpeg", ".json", ".txt", ".xml", ".strings",
            ".metallib", ".momd", ".mom", ".storyboardc", ".lproj", ".dat",
            ".bin", ".der", ".pem", ".cer", ".crt", ".p12", ".mobileprovision",
            ".framework", ".bundle", ".xpc", ".appex", ".kext")


def arch_name(cputype):
    return CPU_NAMES.get(cputype, "0x%08X" % cputype)


def looks_like_macho(head):
    return len(head) >= 8 and (head[:4] in MACHO_MAGIC_BYTES
                               or head[:4] in FAT_MAGIC_BYTES)


def macho_kind(head):
    if len(head) < 8:
        return None
    if head[:4] in MACHO_MAGIC_BYTES:
        return "thin"
    if head[:4] in FAT_MAGIC_BYTES:
        return "fat"
    return None


def _parse_thin_header(buf, base):
    """解析 thin Mach-O 头部, 返回 (cputype, cryptid, 还需要多少字节)。

    数据不够时 cputype 为 None 且第三个返回值为"解析完整所需的缓冲区长度"。"""
    if base + 32 > len(buf):
        return None, None, base + 32
    cputype = u32(buf, base + 4)
    ncmds = u32(buf, base + 16)
    sizeofcmds = u32(buf, base + 20)
    need = base + 32 + sizeofcmds
    if len(buf) < need:
        return None, None, need
    cryptid = 0
    o = base + 32
    end = base + 32 + sizeofcmds
    for _ in range(ncmds):
        if o + 8 > end:
            break
        cmd = u32(buf, o)
        cmdsize = u32(buf, o + 4)
        if cmdsize < 8:
            break
        if cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
            cryptid = u32(buf, o + 16)
        o += cmdsize
    return cputype, cryptid, 0


def thin_cryptid(data, base):
    """已读入完整的 thin 切片 -> cryptid(0=未加密), 找不到返回 0。"""
    if base + 32 > len(data):
        return None
    ncmds = u32(data, base + 16)
    sizeofcmds = u32(data, base + 20)
    o = base + 32
    end = min(len(data), base + 32 + sizeofcmds)
    cryptid = 0
    for _ in range(ncmds):
        if o + 8 > end:
            break
        cmd = u32(data, o)
        cmdsize = u32(data, o + 4)
        if cmdsize < 8:
            break
        if cmd in (LC_ENCRYPTION_INFO, LC_ENCRYPTION_INFO_64):
            cryptid = u32(data, o + 16)
        o += cmdsize
    return cryptid


def analyze_bytes(raw):
    """对完整 Mach-O 数据做架构/加密分析。

    返回 ([(arch, cryptid)], err)。err 非 None 表示不是 Mach-O。"""
    try:
        slices = iter_thin_slices(bytearray(raw))
    except ValueError as e:
        return None, str(e)
    out = []
    for base, ct in slices:
        out.append((arch_name(ct), thin_cryptid(raw, base)))
    return out, None


def probe_zip_entry(zf, zi, max_header=64 * 1024 * 1024):
    """只解压条目开头的若干字节, 快速拿到 (arch, cryptid) 列表。

    返回 [(arch, cryptid)]; 无法判定(非 Mach-O / fat 需要整份数据)返回 None。"""
    with zf.open(zi) as fp:
        head = fp.read(4096)
        if not looks_like_macho(head):
            return None
        if macho_kind(head) == "fat":
            return None  # fat: 交给完整读取判断
        if len(head) < 32:
            return None
        buf = head
        while True:
            cputype, cryptid, need = _parse_thin_header(buf, 0)
            if cputype is not None:
                return [(arch_name(cputype), cryptid)]
            if need > max_header:
                return None
            chunk = fp.read(max(1, need - len(buf)))
            if not chunk:
                return None
            buf += chunk


def parse_coverage(text):
    """从补丁函数打印的日志里提取"覆盖情况"(保持核心匹配代码零改动)。"""
    cov = {"sites": 0, "ssl": None, "challenge": None, "dispatch": None,
           "witherror": None}
    m = re.search(r"SecTrustEvaluate 调用点: ([0-9A-Fa-fx, ]+)", text)
    if m:
        cov["sites"] = len([s for s in m.group(1).split(",") if s.strip()])
    m = re.search(r"\[覆盖\] SecureTransport (\d+)/(\d+) \| "
                  r"URLSession challenge (\S+) \| URLSession block (\S+) \| "
                  r"SecTrustEvaluateWithError (\S+)", text)
    if m:
        cov["ssl"] = "%s/%s" % (m.group(1), m.group(2))
        cov["challenge"] = (m.group(3) == "OK")
        cov["dispatch"] = (m.group(4) == "OK")
        cov["witherror"] = (m.group(5) == "OK")
        cov["path"] = "arm64-securetransport+urlsession"
    else:
        ok = (len(re.findall(r"信任结果成功跳转 je->jmp", text))
              + len(re.findall(r"已是无条件跳转, 跳过", text)))
        cov["ssl"] = "%d/%d" % (ok, cov["sites"])
        cov["challenge"] = False
        cov["dispatch"] = False
        cov["witherror"] = "SecTrustEvaluateWithError -> 永远 true" in text
        cov["path"] = "x86_64-securetransport"
    return cov


def patch_bytes(raw, log=print):
    """在内存里完成定位+修补(支持 fat)。

    返回 (patch 后 bytes, info, err); err 非 None 表示失败。"""
    data = bytearray(raw)
    try:
        slices = iter_thin_slices(data)
    except ValueError as e:
        return None, None, str(e)
    changes, already, cov = [], 0, {}
    for base, cputype in slices:
        arch = arch_name(cputype)
        if log is not None:
            log("* 架构 %s (slice @ 0x%X)" % (arch, base))
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                c, a, _ = patch_slice(data, base)
        except Exception as e:  # 单个架构失败不影响其它架构
            c, a = [], 0
            if log is not None:
                log("  [错误] 架构 %s 修补异常: %s" % (arch, e))
        if log is not None:
            for line in buf.getvalue().splitlines():
                log("  " + line)
        changes += c
        already += a
        cov[arch] = parse_coverage(buf.getvalue())
    return bytes(data), {"changes": changes, "already": already,
                         "coverage": cov,
                         "archs": [arch_name(ct) for _, ct in slices]}, None


# ------------------------------------------------------------
#  在 zip / .app 里定位主二进制
# ------------------------------------------------------------
def _zip_info(zf, name):
    for zi in zf.infolist():
        if zi.filename == name:
            return zi
    return None


def _score_entry(name, size, exec_map):
    n = name.replace("\\", "/")
    if any(h in n for h in SKIP_DIR_HINTS):
        return -1000
    low = n.lower()
    if low.endswith(SKIP_EXT):
        return -1000
    if ".bak" in low or "_patched" in low:
        return -1000
    score = 0
    parts = n.split("/")
    # Payload/<App>.app/<exec>
    if len(parts) >= 3 and parts[0] == "Payload" and parts[1].endswith(".app"):
        app = parts[1]
        prefix = "Payload/" + app
        ex = exec_map.get(prefix)
        base = parts[-1]
        if ex and base == ex and len(parts) == 3:
            score = 600
        elif len(parts) == 3 and os.path.splitext(app[0:-4])[0] == base:
            score = 550
        elif len(parts) == 3:
            score = 400
        else:
            score = 100
    elif "Contents/MacOS/" in n:
        score = 500 if n.count("/") <= 4 else 300
    elif n.count("/") == 0:
        score = 200
    else:
        score = 50
    # 大的通常是主程序
    score += min(150, int(size / (8 * 1024 * 1024)))
    return score


def _zip_exec_map(zf):
    """{Payload/X.app : CFBundleExecutable} —— 少数包是二进制 plist, 用 plistlib。"""
    m = {}
    for zi in zf.infolist():
        n = zi.filename
        if not n.endswith(".app/Info.plist"):
            continue
        if n.count("/") > 2:
            continue
        try:
            pl = plistlib.loads(zf.read(zi))
            ex = pl.get("CFBundleExecutable")
            if ex:
                m[n[:-len("/Info.plist")]] = ex
        except Exception:
            pass
    return m


def pick_zip_main(zf):
    """返回主二进制 ZipInfo; 找不到返回 None。"""
    exec_map = _zip_exec_map(zf)
    best_score, best = -10 ** 9, None
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        name = zi.filename.replace("\\", "/")
        if any(h in name for h in SKIP_DIR_HINTS):
            continue
        low = name.lower()
        if low.endswith(SKIP_EXT) or ".bak" in low or "_patched" in low:
            continue
        try:
            with zf.open(zi) as fp:
                head = fp.read(16)
        except Exception:
            continue
        if not looks_like_macho(head):
            continue
        s = _score_entry(name, zi.file_size, exec_map)
        if s > best_score:
            best_score, best = s, zi
    return best


def _find_inner_container(zf):
    """外层 zip 里没有 Mach-O 时, 找里面套的 .ipa/.zip。"""
    best, best_size = None, 0
    for zi in zf.infolist():
        if zi.is_dir():
            continue
        low = zi.filename.lower()
        if low.endswith((".ipa", ".zip")) and zi.file_size > best_size:
            best, best_size = zi, zi.file_size
    return best


def app_main_binary(appdir):
    """在 .app 目录里找主二进制(优先 Info.plist 的 CFBundleExecutable)。"""
    plist_candidates = []
    if os.path.isdir(os.path.join(appdir, "Contents")):
        plist_candidates.append(os.path.join(appdir, "Contents", "Info.plist"))
    plist_candidates.append(os.path.join(appdir, "Info.plist"))
    ex = None
    for p in plist_candidates:
        if os.path.isfile(p):
            try:
                with open(p, "rb") as f:
                    ex = plistlib.load(f).get("CFBundleExecutable")
            except Exception:
                ex = None
            if ex:
                break
    if ex:
        for p in (os.path.join(appdir, "Contents", "MacOS", ex),
                  os.path.join(appdir, ex)):
            if os.path.isfile(p):
                return p
    cands = []
    for root, dirs, files in os.walk(appdir):
        dirs[:] = [d for d in dirs
                   if d not in ("_CodeSignature", "SC_Info", "Frameworks",
                                "PlugIns", "__MACOSX")]
        for f in files:
            p = os.path.join(root, f)
            try:
                if os.path.getsize(p) < 1024:
                    continue
                with open(p, "rb") as fh:
                    if looks_like_macho(fh.read(8)):
                        cands.append(p)
            except OSError:
                continue
    if not cands:
        return None
    return max(cands, key=os.path.getsize)


# ------------------------------------------------------------
#  输出 / 重新打包
# ------------------------------------------------------------
def _unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for i in range(2, 1000):
        cand = "%s_%d%s" % (stem, i, ext)
        if not os.path.exists(cand):
            return cand
    raise RuntimeError("输出文件重名过多: %s" % path)


def resolve_out(src, opts):
    """计算输出路径。in-place 时返回源路径本身。"""
    if opts.get("in_place"):
        return os.path.abspath(src)
    outdir = opts.get("out")
    if not outdir:
        outdir = os.path.join(os.path.dirname(os.path.abspath(src)), "patched")
    stem, ext = os.path.splitext(os.path.basename(src))
    out = os.path.join(outdir, stem + "_patched" + ext)
    os.makedirs(outdir, exist_ok=True)
    if os.path.exists(out) and not opts.get("force"):
        out = _unique_path(out)
    return out


def _atomic_write(out_path, writer):
    """writer(tmp_path) 写临时文件, 成功后原子替换。"""
    d = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = out_path + ".tmp_sky_patch"
    writer(tmp)
    os.replace(tmp, out_path)
    return out_path


def _write_entry(zout, zi, new_bytes=None, src=None):
    """向新 zip 写一个条目: 有 new_bytes 就替换, 否则从 src 流式复制。"""
    with zout.open(zi, "w") as dst:
        if new_bytes is not None:
            dst.write(new_bytes)
        else:
            shutil.copyfileobj(src, dst, 1 << 20)


def repack_zip(src_path, replacements, out_path):
    """按原顺序重建 zip, replacements={条目名: 新内容}。

    未替换的条目流式复制(不占额外内存), 保留时间戳/属性/压缩方式。
    源 zip 在 os.replace 之前一定已经关闭, Windows 上也能原地覆盖。"""
    d = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(d, exist_ok=True)
    tmp = out_path + ".tmp_sky_patch"
    with zipfile.ZipFile(src_path, "r") as zin:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            zout.comment = zin.comment
            for zi in zin.infolist():
                if zi.is_dir():
                    zout.writestr(zi, b"")
                    continue
                if zi.filename in replacements:
                    _write_entry(zout, zi, new_bytes=replacements[zi.filename])
                else:
                    with zin.open(zi) as fp:
                        _write_entry(zout, zi, src=fp)
    if os.path.abspath(src_path) == os.path.abspath(out_path):
        backup = src_path + ".bak_original"
        if not os.path.exists(backup):
            shutil.copy2(src_path, backup)
    os.replace(tmp, out_path)
    return out_path


def _verify_container(path, entry_name):
    """重新打开产物自检: 主二进制未加密, 且补丁已全部就位(再打一遍应 0 变化)。"""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            zi = _zip_info(zf, entry_name)
            if zi is None:
                return False, "产物里找不到 %s" % entry_name
            raw = zf.read(zi)
    except Exception as e:
        return False, "无法读取产物: %s" % e
    info, err = analyze_bytes(raw)
    if err:
        return False, err
    if any((c or 0) != 0 for _, c in info):
        return False, "产物主二进制仍带加密标记"
    _final, pinfo, err = patch_bytes(raw, log=None)
    if err:
        return False, err
    if pinfo["changes"]:
        return False, "产物里仍有 %d 处未打上" % len(pinfo["changes"])
    return True, ""


# ------------------------------------------------------------
#  单个目标的处理
# ------------------------------------------------------------
def new_result(path, kind):
    return {"path": os.path.abspath(path), "kind": kind, "entry": None,
            "archs": [], "cryptid": [], "status": None, "changes": 0,
            "already": 0, "coverage": {}, "out": None, "error": None,
            "verified": None}


def _apply_patch_info(res, info):
    res["changes"] = len(info["changes"])
    res["already"] = info["already"]
    res["coverage"] = info["coverage"]
    res["archs"] = info["archs"]
    if info["changes"]:
        res["status"] = "patched"
    elif info["already"]:
        res["status"] = "already"
    else:
        res["status"] = "no_patch_point"


def _make_logger(opts):
    def log(msg):
        if not opts.get("quiet"):
            print(msg)
    return log


def process_zip(path, opts, res=None):
    """处理 .ipa/.zip: 读阶段在 with zf 内完成, 写阶段(zip 已关闭)再重建包。"""
    res = res or new_result(path, "ipa" if path.lower().endswith(".ipa") else "zip")
    log = _make_logger(opts)
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        res["status"] = "error"
        res["error"] = "无法作为 zip 打开(文件可能损坏/下载不完整): %s" % e
        return res

    rewrite = None   # (条目名, 新内容)
    nested = None    # (条目名, 内层文件路径, 临时目录)
    try:
        with zf:
            main = pick_zip_main(zf)
            if main is None:
                inner = _find_inner_container(zf)
                if inner is None:
                    res["status"] = "no_target"
                    res["error"] = "包里没有 Mach-O 主二进制(也不是嵌套 ipa/zip)"
                    return res
                tmpdir = tempfile.mkdtemp(prefix="sky_patch_nested_")
                inner_path = os.path.join(tmpdir,
                                          os.path.basename(inner.filename))
                with zf.open(inner) as src, open(inner_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                nested = (inner.filename, inner_path, tmpdir)
            else:
                res["entry"] = main.filename
                log("  主二进制: %s (%.1f MB)" % (main.filename,
                                                main.file_size / 1048576.0))
                enc = probe_zip_entry(zf, main)
                full = None
                if enc is None:
                    full = zf.read(main)
                    analyzed, err = analyze_bytes(full)
                    if err:
                        res["status"] = "error"
                        res["error"] = err
                        return res
                    enc = analyzed
                res["archs"] = [a for a, _ in enc]
                res["cryptid"] = [c for _, c in enc]
                if any((c or 0) != 0 for _, c in enc):
                    res["status"] = "encrypted"
                    res["error"] = ("App Store 加密包 (cryptid=%s), 无法直接修改; "
                                    "请先解密(frida-ios-dump / bagbak 等)再运行"
                                    % "/".join(str(c) for _, c in enc))
                    return res
                if full is None:
                    full = zf.read(main)
                final, info, err = patch_bytes(full, log=log)
                if err:
                    res["status"] = "error"
                    res["error"] = err
                    return res
                _apply_patch_info(res, info)
                if not info["changes"]:
                    return res
                if opts.get("dry_run"):
                    res["status"] = "planned"
                    return res
                rewrite = (main.filename, final)
    finally:
        try:
            zf.close()
        except Exception:
            pass

    if nested is not None:
        return _finish_nested_zip(path, nested, opts, res, log)
    if rewrite is not None:
        out = resolve_out(path, opts)
        repack_zip(path, {rewrite[0]: rewrite[1]}, out)
        res["out"] = out
        ok, why = _verify_container(out, rewrite[0])
        res["verified"] = ok
        if not ok:
            res["error"] = "产物自检失败: %s" % why
    return res


def _finish_nested_zip(path, nested, opts, res, log):
    """外层包没有主二进制: 先处理内层, 再把内层重新塞回外层。"""
    inner_name, inner_path, tmpdir = nested
    log("  外层没有主二进制, 发现嵌套容器: %s" % inner_name)
    try:
        sub_opts = dict(opts)
        sub_opts["out"] = tmpdir
        sub_opts["force"] = True
        sub_opts["in_place"] = False
        sub = process_zip(inner_path, sub_opts)
        res["entry"] = "%s!%s" % (inner_name, sub.get("entry"))
        for k in ("archs", "cryptid", "coverage", "changes", "already",
                  "verified"):
            res[k] = sub.get(k)
        if sub["status"] in ("encrypted", "no_target", "error",
                             "no_patch_point"):
            res["status"] = sub["status"]
            res["error"] = sub.get("error")
            return res
        if sub["status"] == "already":
            res["status"] = "already"
            return res
        if opts.get("dry_run") or not sub.get("out"):
            res["status"] = "planned"
            return res
        with open(sub["out"], "rb") as f:
            new_inner = f.read()
        out = resolve_out(path, opts)
        repack_zip(path, {inner_name: new_inner}, out)
        res["out"] = out
        res["status"] = "patched"
        return res
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def process_macho_file(path, opts, res=None):
    res = res or new_result(path, "binary")
    log = _make_logger(opts)
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        res["status"] = "error"
        res["error"] = "读取失败: %s" % e
        return res
    analyzed, err = analyze_bytes(raw)
    if err:
        res["status"] = "not_macho"
        res["error"] = err
        return res
    res["archs"] = [a for a, _ in analyzed]
    res["cryptid"] = [c for _, c in analyzed]
    if any((c or 0) != 0 for _, c in analyzed):
        res["status"] = "encrypted"
        res["error"] = ("App Store 加密二进制 (cryptid=%s), 无法直接修改; "
                        "请从已解密设备导出后再运行"
                        % "/".join(str(c) for _, c in analyzed))
        return res
    final, info, err = patch_bytes(raw, log=log)
    if err:
        res["status"] = "error"
        res["error"] = err
        return res
    _apply_patch_info(res, info)
    if not info["changes"] or opts.get("dry_run"):
        if info["changes"]:
            res["status"] = "planned"
        return res
    def _writer(tmp):
        with open(tmp, "wb") as fh:
            fh.write(final)

    if opts.get("in_place") or (not opts.get("out") and not opts.get("_scan")):
        # 原地修改(与原版行为一致): 先备份再原子替换
        if not opts.get("_no_bak"):
            backup = path + ".bak_original"
            if not os.path.exists(backup):
                shutil.copy2(path, backup)
        _atomic_write(path, _writer)
        res["out"] = path
    else:
        out = resolve_out(path, opts)
        _atomic_write(out, _writer)
        res["out"] = out
    return res


def process_app_dir(path, opts, res=None):
    """.app 目录: 默认原地改主二进制(带 .bak 备份); 给了 --out 就整目录复制过去改。"""
    res = res or new_result(path, "app")
    log = _make_logger(opts)
    binpath = app_main_binary(path)
    if not binpath:
        res["status"] = "no_target"
        res["error"] = "在 .app 目录内找不到 Mach-O 可执行文件"
        return res
    res["entry"] = os.path.relpath(binpath, path)
    log("  主二进制: %s" % res["entry"])

    if opts.get("out") and not opts.get("in_place"):
        outdir = opts["out"]
        os.makedirs(outdir, exist_ok=True)
        out_app = os.path.join(outdir, os.path.basename(os.path.abspath(path)))
        if os.path.exists(out_app) and not opts.get("force"):
            out_app = _unique_path(out_app)
        log("  复制 .app -> %s" % out_app)
        shutil.copytree(path, out_app,
                        ignore=shutil.ignore_patterns("_CodeSignature",
                                                      "*.bak_original"))
        binpath = os.path.join(out_app, res["entry"])
        res["out"] = out_app

    sub_opts = dict(opts)
    sub_opts["_scan"] = False
    sub_opts["out"] = None
    sub_opts["in_place"] = True
    if res.get("out"):
        # 改的是 --out 的副本, 不需要再留 .bak
        sub_opts["_no_bak"] = True
    sub = process_macho_file(binpath, sub_opts)
    for k in ("archs", "cryptid", "coverage", "changes", "already",
              "status", "error"):
        res[k] = sub.get(k)
    if res.get("out") is None:
        res["out"] = sub.get("out")
    return res


def process_target(path, opts):
    log = _make_logger(opts)
    if os.path.isdir(path):
        low = path.lower()
        if low.endswith(".app") or os.path.isdir(os.path.join(path, "Payload")):
            res = new_result(path, "app")
            log("[*] %s" % path)
            return process_app_dir(path, opts, res)
        return None
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ipa", ".zip"):
        res = new_result(path, "ipa" if ext == ".ipa" else "zip")
        log("[*] %s" % path)
        return process_zip(path, opts, res)
    res = new_result(path, "binary")
    log("[*] %s" % path)
    return process_macho_file(path, opts, res)


# ------------------------------------------------------------
#  目录扫描
# ------------------------------------------------------------
def collect_targets(root):
    targets = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if not d.startswith(".") and d != "patched"
                             and d != "__MACOSX")
        keep = []
        for d in dirnames:
            if d.lower().endswith(".app"):
                targets.append(os.path.join(dirpath, d))
            else:
                keep.append(d)
        dirnames[:] = keep
        for f in sorted(filenames):
            low = f.lower()
            if "_patched" in low or low.endswith(".bak_original"):
                continue
            full = os.path.join(dirpath, f)
            ext = os.path.splitext(f)[1].lower()
            if ext in (".ipa", ".zip"):
                targets.append(full)
                continue
            if ext == "":
                try:
                    with open(full, "rb") as fh:
                        head = fh.read(8)
                except OSError:
                    continue
                if looks_like_macho(head):
                    targets.append(full)
    return targets


STATUS_ORDER = ("patched", "already", "planned", "encrypted", "no_patch_point",
                "no_target", "not_macho", "error")


def print_summary(items, elapsed):
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    print("=" * 72)
    print("  汇总: %d 个目标, 用时 %.1fs" % (len(items), elapsed))
    for st in STATUS_ORDER:
        if counts.get(st):
            print("    %-14s %d" % (st, counts[st]))
    print("=" * 72)
    bad = [it for it in items if it["status"] in ("error", "no_target",
                                                  "no_patch_point")]
    if bad:
        print("  需要关注:")
        for it in bad:
            print("    [%s] %s" % (it["status"], it["path"]))
            if it.get("error"):
                print("           %s" % it["error"])
    enc = [it for it in items if it["status"] == "encrypted"]
    if enc:
        print("  加密(需先解密) %d 个:" % len(enc))
        for it in enc:
            print("    %s  cryptid=%s" % (it["path"],
                                          "/".join(str(c) for c in it["cryptid"])))
    print("=" * 72)


def write_json_report(path, items, root, elapsed):
    counts = {}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1
    report = {"tool": "patch_sky_ios.py", "version": VERSION,
              "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
              "root": os.path.abspath(root) if root else None,
              "elapsed_sec": round(elapsed, 1),
              "summary": counts, "items": items}
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return path


# ------------------------------------------------------------
#  入口
# ------------------------------------------------------------
USAGE_EPILOG = """示例:
  python patch_sky_ios.py Sky.app                      # 直接改 .app 里的主二进制(带 .bak 备份)
  python patch_sky_ios.py Sky.ipa                      # 生成 Sky_patched.ipa
  python patch_sky_ios.py "E:\\Sky" --dry-run --json r.json   # 批量体检, 不写文件
  python patch_sky_ios.py "E:\\Sky" --out D:\\out      # 批量修补, 产物放 D:\\out
"""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="patch_sky_ios.py",
        description="Sky 私服 iOS/macOS 证书校验绕过 (支持 ipa/zip/.app/裸 Mach-O)",
        epilog=USAGE_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="ipa / zip / .app 目录 / Mach-O 文件 / 目录")
    ap.add_argument("--out", metavar="DIR", help="输出目录(默认 <输入目录>/patched)")
    ap.add_argument("--in-place", action="store_true",
                    help="原地修改(仅 .app 目录/裸二进制; ipa 会重建并覆盖原文件)")
    ap.add_argument("--dry-run", action="store_true", help="只分析, 不写任何文件")
    ap.add_argument("--json", metavar="PATH", help="输出 JSON 报告")
    ap.add_argument("--quiet", action="store_true", help="少打印细节")
    ap.add_argument("--force", action="store_true", help="输出文件已存在时直接覆盖")
    ap.add_argument("--version", action="version", version="patch_sky_ios.py " + VERSION)
    opts = vars(ap.parse_args(argv))

    if not opts.get("input"):
        ap.print_help()
        return 1
    src = opts["input"]
    if not os.path.exists(src):
        print("[错误] 路径不存在: %s" % src)
        return 1

    if not opts.get("quiet"):
        print("=" * 72)
        print("  Sky 私服 - iOS/macOS 证书校验绕过工具 v%s" % VERSION)
        print("=" * 72)

    t0 = time.time()
    items = []

    if os.path.isdir(src) and not (
            src.lower().endswith(".app")
            or os.path.isdir(os.path.join(src, "Payload"))):
        targets = collect_targets(src)
        opts["_scan"] = True
        if not opts.get("quiet"):
            print("[*] 扫描 %s: 共 %d 个目标" % (src, len(targets)))
        if not targets:
            print("[提示] 目录里没有找到 ipa/zip/.app/裸 Mach-O")
        for i, t in enumerate(targets, 1):
            if not opts.get("quiet"):
                print("[%d/%d] %s" % (i, len(targets), t))
            res = process_target(t, opts)
            if res is None:
                continue
            items.append(res)
            if not opts.get("quiet"):
                print("      -> %s%s" % (
                    res["status"],
                    ("，%d 处修改" % res["changes"]) if res["changes"] else ""))
    else:
        res = process_target(src, opts)
        if res is None:
            print("[错误] 不支持的输入: %s" % src)
            return 1
        items.append(res)

    elapsed = time.time() - t0
    if len(items) > 1:
        print_summary(items, elapsed)
    else:
        it = items[0]
        print("=" * 72)
        print("  结果: %s" % it["status"])
        if it["changes"]:
            print("  修补 %d 处" % it["changes"])
        if it.get("coverage"):
            for arch, cov in it["coverage"].items():
                print("  [%s] SecureTransport %s | challenge=%s | block=%s | "
                      "withError=%s" % (arch, cov.get("ssl"),
                                        cov.get("challenge"), cov.get("dispatch"),
                                        cov.get("witherror")))
        if it.get("out"):
            print("  输出: %s" % it["out"])
            if it.get("verified") is False:
                print("  [警告] 产物自检失败: %s" % it.get("error"))
            elif it.get("verified"):
                print("  自检: 通过(未加密, 补丁全部就位)")
        if it.get("error"):
            print("  说明: %s" % it["error"])
        print("=" * 72)

    if opts.get("json"):
        write_json_report(opts["json"], items, src if os.path.isdir(src) else None,
                          elapsed)
        if not opts.get("quiet"):
            print("[*] JSON 报告: %s" % opts["json"])

    ok_status = ("patched", "already", "planned")
    return 0 if all(it["status"] in ok_status for it in items) else 2


if __name__ == "__main__":
    sys.exit(main())
