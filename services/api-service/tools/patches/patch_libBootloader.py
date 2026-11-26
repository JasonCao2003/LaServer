#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sky 私服 - libBootloader.so 自动证书校验绕过脚本
用法:
    python patch_libBootloader.py <libBootloader.so路径>

功能:
    自动定位 BoringSSL 证书校验函数并 patch 为返回成功，
    使游戏信任私服的自签名证书。

支持:
    - ARM64 (AArch64) ELF .so
    - 旧版/有符号版: 自动通过 ELF 动态符号表定位函数地址(无需硬编码偏移)
    - 新版/无符号版(0.19.5+ 静态链接 OpenSSL 1.1.1o, 已 strip):
      符号定位失败时自动用特征码定位证书校验结果存储指令
    - 自动备份原始文件

原理:
    符号定位(旧版):
    1. ssl_verify_cert_chain            -> MOV W0,#1 ; RET  (链校验返回成功)
    2. SSL_CTX_set_cert_verify_callback -> RET              (不注册自定义回调)
    3. SSL_get_verify_result            -> MOV X0,#0 ; RET  (返回 X509_V_OK)
    4. X509_verify_cert                 -> MOV W0,#1 ; RET  (路径校验返回成功)

    特征码定位(新版, 符号被 strip 时):
    - 特征 RET + STR W1,[X0,#0x160], 把 STR W1 改 STR WZR,
      使证书校验结果强制写 0(X509_V_OK)。
"""

import sys
import os
import struct
import shutil

# ARM64 指令（小端字节序）
MOV_W0_1 = bytes([0x20, 0x00, 0x80, 0x52])  # MOV W0, #1
MOV_X0_0 = bytes([0x00, 0x00, 0x80, 0xD2])  # MOV X0, #0
RET      = bytes([0xC0, 0x03, 0x5F, 0xD6])  # RET

# 目标函数及 patch
PATCH_TABLE = {
    "ssl_verify_cert_chain": {
        "patch": MOV_W0_1 + RET,
        "desc": "证书链校验 -> 返回1(成功)",
    },
    "SSL_CTX_set_cert_verify_callback": {
        "patch": RET,
        "desc": "注册自定义回调 -> 空操作(不注册)",
    },
    "SSL_get_verify_result": {
        "patch": MOV_X0_0 + RET,
        "desc": "获取校验结果 -> 返回0(X509_V_OK)",
    },
    "X509_verify_cert": {
        "patch": MOV_W0_1 + RET,
        "desc": "X509路径校验 -> 返回1(成功)",
    },
}


def read_u16(data, offset):
    return struct.unpack_from('<H', data, offset)[0]

def read_u32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]

def read_u64(data, offset):
    return struct.unpack_from('<Q', data, offset)[0]


def parse_elf_sections(data):
    """解析 ELF64 节区表"""
    if data[:4] != b'\x7fELF':
        raise ValueError("不是有效的 ELF 文件")
    if data[4] != 2:
        raise ValueError("不是 ELF64 格式")
    e_machine = read_u16(data, 0x12)
    if e_machine != 0xB7:
        raise ValueError(f"不是 ARM64 架构 (e_machine=0x{e_machine:X})")

    e_shoff = read_u64(data, 0x28)
    e_shentsize = read_u16(data, 0x3A)
    e_shnum = read_u16(data, 0x3C)
    e_shstrndx = read_u16(data, 0x3E)

    strtab_hdr = int(e_shoff) + e_shstrndx * e_shentsize
    strtab_offset = int(read_u64(data, strtab_hdr + 0x18))

    sections = {}
    for i in range(e_shnum):
        sh_off = int(e_shoff) + i * e_shentsize
        name_idx = read_u32(data, sh_off)
        name_start = strtab_offset + name_idx
        name_end = name_start
        while name_end < len(data) and data[name_end] != 0:
            name_end += 1
        sec_name = data[name_start:name_end].decode('ascii', errors='replace')
        sections[sec_name] = {
            'addr': read_u64(data, sh_off + 0x10),
            'offset': read_u64(data, sh_off + 0x18),
            'size': read_u64(data, sh_off + 0x20),
        }
    return sections


def find_symbol_addresses(data, sections, target_names):
    """在 .dynsym 中查找目标符号的虚拟地址"""
    dynsym = sections.get('.dynsym')
    dynstr = sections.get('.dynstr')
    if not dynsym or not dynstr:
        raise ValueError("找不到 .dynsym 或 .dynstr 节区")

    dynsym_off = int(dynsym['offset'])
    dynsym_size = int(dynsym['size'])
    dynstr_off = int(dynstr['offset'])
    dynstr_size = int(dynstr['size'])
    entry_size = 24

    count = dynsym_size // entry_size
    dynstr_bytes = data[dynstr_off:dynstr_off + dynstr_size]

    # 找各符号名在 dynstr 中的相对偏移
    target_offsets = {}
    for name in target_names:
        search = b'\x00' + name.encode('ascii') + b'\x00'
        idx = dynstr_bytes.find(search)
        if idx >= 0:
            target_offsets[name] = idx + 1

    # 遍历 dynsym 匹配
    results = {}
    for i in range(count):
        off = dynsym_off + i * entry_size
        st_name = read_u32(data, off)
        for name, expected in target_offsets.items():
            if st_name == expected:
                st_value = read_u64(data, off + 8)
                st_size = read_u64(data, off + 16)
                results[name] = {'vaddr': int(st_value), 'size': int(st_size)}
    return results


def apply_patches(data, symbols):
    """应用所有 patch 并返回修改后的数据"""
    patched = bytearray(data)
    changes = []

    for func_name, patch_info in PATCH_TABLE.items():
        if func_name not in symbols:
            print(f"  [跳过] {func_name} - 未找到")
            continue

        vaddr = symbols[func_name]['vaddr']
        patch_bytes = patch_info['patch']
        desc = patch_info['desc']
        orig_bytes = bytes(patched[vaddr:vaddr + len(patch_bytes)])

        for i, b in enumerate(patch_bytes):
            patched[vaddr + i] = b

        changes.append({
            'name': func_name,
            'offset': vaddr,
            'original': orig_bytes,
            'patched': patch_bytes,
            'desc': desc,
        })
        print(f"  [OK] {func_name} @0x{vaddr:X}")
        print(f"       {desc}")
        print(f"       原: {orig_bytes.hex(' ').upper()}")
        print(f"       补: {patch_bytes.hex(' ').upper()}")
        print()

    return bytes(patched), changes


# ============================================================
# 特征码定位 (适配新版无符号 .so, 如 0.19.5+ 静态链接 OpenSSL 1.1.1o)
# ============================================================
# 新版 libBootloader.so 完全 strip, 无导出的 BoringSSL/X509 符号,
# 无法用符号定位。改用特征码定位证书校验结果的存储指令:
#   特征: RET(D65F03C0) + STR W1,[X0,#0x160](B9016001)
#   把 STR W1 改成 STR WZR(B901601F), 即证书校验结果强制写 0(X509_V_OK),
#   使游戏信任私服自签证书。已验证与官方改版逐字节一致。
SIG_PATCHES = [
    {
        "name": "cert_verify_result_store",
        # RET + STR W1,[X0,#0x160] (小端字节序)
        "sig": bytes.fromhex("C0035FD6016001B9"),
        "patch_off": 4,             # 跳过 RET, 定位到 STR 指令
        "orig4": 0xB9016001,        # STR W1, [X0,#0x160]
        "new4": 0xB901601F,         # STR WZR, [X0,#0x160]
        "desc": "证书校验结果存储 -> 强制写0(X509_V_OK) (W1->WZR)",
    },
]


def find_and_patch_by_signature(data):
    """用特征码定位并 patch (适配新版无符号 .so)"""
    patched = bytearray(data)
    changes = []
    already = 0
    for sp in SIG_PATCHES:
        sig = sp["sig"]
        # 用 bytes.find 全文扫描, 确认唯一匹配(避免误伤)
        positions = []
        start = 0
        while True:
            idx = patched.find(sig, start)
            if idx < 0:
                break
            positions.append(idx)
            start = idx + 1

        if len(positions) == 0:
            # 没找到原始特征码: 可能已经 patch 过, 检查"已patch"形态
            done_sig = sig[:sp["patch_off"]] + struct.pack('<I', sp["new4"])
            if patched.find(done_sig) >= 0:
                print(f"  [已patch] {sp['name']} - 目标已是补丁状态(无需重复)")
                already += 1
            else:
                print(f"  [跳过] {sp['name']} - 未找到特征码")
            continue
        if len(positions) > 1:
            print(f"  [跳过] {sp['name']} - 特征码匹配到 {len(positions)} 处(不唯一, 为安全跳过)")
            continue

        insn_off = positions[0] + sp["patch_off"]
        orig_bytes = bytes(patched[insn_off:insn_off + 4])
        if struct.unpack_from('<I', orig_bytes, 0)[0] != sp["orig4"]:
            print(f"  [跳过] {sp['name']} - 目标指令不匹配(0x{orig_bytes.hex().upper()})")
            continue

        new_bytes = struct.pack('<I', sp["new4"])
        for i in range(4):
            patched[insn_off + i] = new_bytes[i]

        changes.append({
            'name': sp["name"],
            'offset': insn_off,
            'original': orig_bytes,
            'patched': new_bytes,
            'desc': sp["desc"],
        })
        print(f"  [OK] {sp['name']} @0x{insn_off:X} (特征码定位)")
        print(f"       {sp['desc']}")
        print(f"       原: {orig_bytes.hex(' ').upper()}")
        print(f"       补: {new_bytes.hex(' ').upper()}")
        print()

    return bytes(patched), changes, already


def main():
    if len(sys.argv) < 2:
        print("用法: python patch_libBootloader.py <libBootloader.so路径>")
        print()
        print("示例:")
        print("  python patch_libBootloader.py E:\\Downloads\\libBootloader.so")
        print("  python patch_libBootloader.py /sdcard/libBootloader.so")
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.isfile(filepath):
        print(f"[错误] 文件不存在: {filepath}")
        sys.exit(1)

    print("=" * 60)
    print("  Sky 私服 - libBootloader.so 证书校验绕过工具")
    print("=" * 60)
    print()
    print(f"[*] 读取: {filepath}")
    with open(filepath, 'rb') as f:
        data = f.read()
    print(f"    大小: {len(data):,} 字节")
    print()

    print("[*] 解析 ELF 结构...")
    try:
        sections = parse_elf_sections(data)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    print(f"    .text   addr=0x{sections['.text']['addr']:X}")
    print(f"    .dynstr size=0x{sections['.dynstr']['size']:X}")
    print()

    print("[*] 定位目标函数...")
    symbols = find_symbol_addresses(data, sections, list(PATCH_TABLE.keys()))
    print(f"    符号定位找到 {len(symbols)}/{len(PATCH_TABLE)} 个")
    for name, info in symbols.items():
        print(f"      {name}: 0x{info['vaddr']:X}")
    if not symbols:
        print("    (符号表无目标符号, 将用特征码定位 - 适配新版)")
    print()

    # 备份
    backup_path = filepath + ".bak_original"
    if not os.path.exists(backup_path):
        shutil.copy2(filepath, backup_path)
        print(f"[*] 备份: {backup_path}")
    else:
        print(f"[*] 备份已存在: {backup_path}")
    print()

    # Patch
    print("[*] 应用修补...")
    print()
    patched_data, changes = apply_patches(data, symbols)

    # 符号定位不足(新版无符号), 用特征码定位补充
    sig_already = 0
    if len(changes) < 2:
        print("[*] 符号定位不足, 使用特征码定位(新版)...")
        print()
        patched_data, sig_changes, sig_already = find_and_patch_by_signature(patched_data)
        changes = changes + sig_changes

    # 本次没有新改动, 但目标已经是补丁状态 -> 正常退出
    if not changes and sig_already > 0:
        print("=" * 60)
        print("  该文件已经 patch 过, 无需重复操作")
        print(f"  文件: {filepath}")
        print("=" * 60)
        sys.exit(0)

    if not changes:
        print("[错误] 符号和特征码定位都失败, 可能不是 Sky 的 libBootloader.so")
        sys.exit(1)

    with open(filepath, 'wb') as f:
        f.write(patched_data)

    total_bytes = sum(len(c['patched']) for c in changes)
    print("=" * 60)
    print(f"  完成! 修改 {total_bytes} 字节, {len(changes)} 个函数")
    print(f"  文件: {filepath}")
    print(f"  备份: {backup_path}")
    print("=" * 60)
    print()
    print("还原:")
    print(f'  copy "{backup_path}" "{filepath}"')

    # 保存记录
    rec_path = os.path.splitext(filepath)[0] + "_patch_记录.txt"
    with open(rec_path, 'w', encoding='utf-8') as f:
        f.write("==== libBootloader.so 证书校验绕过修补记录 ====\n")
        f.write(f"文件: {filepath}\n")
        f.write(f"备份: {backup_path}\n")
        f.write(f"大小: {len(data):,} 字节 (不变)\n\n")
        for c in changes:
            f.write(f"函数: {c['name']}  偏移: 0x{c['offset']:X}\n")
            f.write(f"说明: {c['desc']}\n")
            f.write(f"原始: {c['original'].hex(' ').upper()}\n")
            f.write(f"修补: {c['patched'].hex(' ').upper()}\n\n")
        f.write(f"还原: copy \"{backup_path}\" \"{filepath}\"\n")
    print(f"\n记录: {rec_path}")


if __name__ == "__main__":
    main()