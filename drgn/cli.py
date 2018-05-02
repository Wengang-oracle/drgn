# Copyright 2018 - Omar Sandoval
# SPDX-License-Identifier: GPL-3.0+

import argparse
import code
import glob
import os
import os.path
import platform
import runpy
import sys
from typing import Any, Dict, List, Tuple, Union

import drgn
from drgn.dwarf import DW_TAG
from drgn.dwarfindex import DwarfIndex
from drgn.elf import parse_elf_phdrs
from drgn.program import Program
from drgn.type import Type
from drgn.typeindex import DwarfTypeIndex
from drgn.typename import TypeName
from drgn.util import parse_symbol_file


def find_vmlinux(release: str) -> str:
    paths = [
        f'/usr/lib/debug/lib/modules/{release}/vmlinux',
        f'/boot/vmlinux-{release}',
        f'/lib/modules/{release}/build/vmlinux',
    ]
    for path in paths:
        if os.path.exists(path):
            return path
    else:
        raise ValueError()


def find_modules(release: str) -> List[str]:
    patterns = [
        f'/usr/lib/debug/lib/modules/{release}/kernel/**/*.ko.debug',
        f'/lib/modules/{release}/kernel/**/*.ko',
    ]
    for pattern in patterns:
        paths = glob.glob(pattern, recursive=True)
        if paths:
            return paths
    else:
        return []


def main() -> None:
    python_version = '.'.join(str(v) for v in sys.version_info[:3])
    version = f'drgn {drgn.__version__} (using Python {python_version})'
    parser = argparse.ArgumentParser(
        prog='drgn', description='Scriptable debugger')
    parser.add_argument(
        '-k', '--kernel', action='store_true',
        help='debug the kernel instead of a userspace program')
    parser.add_argument(
        '-e', '--executable', metavar='PATH', type=str,
        help='use the given executable file')
    parser.add_argument(
        'script', metavar='ARG', type=str, nargs='*',
        help='script to execute instead of running in interactive mode')
    parser.add_argument('--version', action='version', version=version)

    args = parser.parse_args()

    if not args.kernel:
        sys.exit('Only --kernel mode is currently implemented')

    release = platform.release()

    if args.executable is None:
        try:
            args.executable = find_vmlinux(release)
        except ValueError:
            sys.exit('Could not find vmlinux file; install the proper debuginfo package or use --executable')

    paths = find_modules(release)
    if not paths and not args.script:
        print('Could not find kernel modules; continuing anyways',
              file=sys.stderr)
    paths.append(args.executable)

    dwarf_index = DwarfIndex(paths)
    type_index = DwarfTypeIndex(dwarf_index)

    with open('/proc/kallsyms', 'r') as f:
        symbols = parse_symbol_file(f)

    with open('/proc/kcore', 'rb') as core_file:
        phdrs = parse_elf_phdrs(core_file)

        def lookup_variable(name: str) -> Tuple[int, Type]:
            address = symbols[name][-1]
            dwarf_type = dwarf_index.find(name, DW_TAG.variable).type()
            return address, type_index.find_dwarf_type(dwarf_type)

        def read_memory(address: int, size: int) -> bytes:
            for phdr in phdrs:
                if phdr.p_vaddr <= address <= phdr.p_vaddr + phdr.p_memsz:
                    break
            else:
                raise ValueError(f'could not find memory segment containing 0x{address:x}')
            return os.pread(core_file.fileno(), size,
                            phdr.p_offset + address - phdr.p_vaddr)

        init_globals: Dict[str, Any] = {
            'prog': Program(type_index=type_index,
                            lookup_variable_fn=lookup_variable,
                            read_memory_fn=read_memory),
            'drgn': drgn,
        }
        if args.script:
            sys.argv = args.script
            runpy.run_path(args.script[0], init_globals=init_globals,
                           run_name='__main__')
        else:
            init_globals['__name__'] = '__main__'
            init_globals['__doc__'] = None
            banner = version + '\nFor help, type help(drgn).'
            code.interact(banner=banner, exitmsg='', local=init_globals)  # type: ignore
                                                                          # typeshed issue #2024

if __name__ == '__main__':
    main()
