"""Go 的规格与文件校验（语言规则部分）。 Go-specific proposal validation (the language rules part).

中立的骨架在 masa.domain.proposals；这里只有“只有 Go 才知道”的事：包声明、导入预检、go.mod 内容。
The neutral skeleton lives in masa.domain.proposals; this file holds only what Go alone knows: the package clause, the import preflight and the go.mod content.
"""
import re

from masa.domain.models import MasaError

GO_VERSION = '1.27.0'


def entry_package(source):
    """跳过 Go 文件头注释，读取包声明；语法仍由 Go 工具验证。 Read the package after header comments; Go tools still validate syntax."""
    remaining = source.lstrip('﻿')

    def token():
        nonlocal remaining
        while True:
            remaining = remaining.lstrip()
            if remaining.startswith('//'):
                _, _, remaining = remaining.partition('\n')
            elif remaining.startswith('/*'):
                _, end, remaining = remaining.partition('*/')
                if not end:
                    return ''
            else:
                break
        match = re.match(r'[A-Za-z_][A-Za-z0-9_]*', remaining)
        if not match:
            return ''
        remaining = remaining[match.end():]
        return match.group()
    keyword = token()
    return token() if keyword == 'package' else ''


def imports_of(source):
    """提取 import 路径（只用于预检，语法仍由 Go 工具验证）。 Extract import paths for preflight; Go tools still own syntax."""
    paths = []
    for block in re.findall(r'(?m)^\s*import\s*\((.*?)\)', source, re.S):
        paths += re.findall(r'"([^"\n]+)"', block)
    paths += re.findall(r'(?m)^\s*import\s+(?:[A-Za-z_.][A-Za-z0-9_]*\s+)?"([^"\n]+)"', source)
    return paths


def preflight_go(files, module):
    """部署前拦截确定性错误：第三方依赖、同目录包名冲突。让模型带着明确原因重试，而不是等编译失败后反复修。
    Catch deterministic mistakes before verification: third-party imports and mixed packages in one directory."""
    packages = {}
    for path, source in files.items():
        if not path.endswith('.go'):
            continue
        for imported in imports_of(source):
            first = imported.split('/')[0]
            if '.' in first and imported != module and not imported.startswith(module + '/'):
                raise MasaError(f'{path} imports third-party package "{imported}"; only the Go standard library and this module are allowed')
        package = entry_package(source)
        if package:
            directory = path.rpartition('/')[0]
            # 同目录允许 foo 与 foo_test（外部测试包），其余不得混用。 foo and foo_test may share a directory.
            packages.setdefault(directory, {}).setdefault(package[:-5] if package.endswith('_test') and path.endswith('_test.go') else package, path)
    for directory, found in packages.items():
        if len(found) > 1:
            names = sorted(found)
            raise MasaError(f'directory "{directory or "."}" mixes packages {names[0]} ({found[names[0]]}) and {names[1]} ({found[names[1]]}); every Go file in a directory must use the same package')


def check_proposal(files, spec, target_path):
    """逐文件产物：入口文件必须是 package main。 A single-file proposal: the entrypoint must declare package main."""
    content = files[target_path]
    if target_path == spec['entrypoint'] and entry_package(content) != 'main':
        raise MasaError('Go CLI entrypoint must declare package main')


def check_bundle(files, spec):
    """整包：入口 package main、go.mod 与批准一致、导入预检。 The bundle: entry package, go.mod matches the approval, import preflight."""
    if entry_package(files[spec['entrypoint']]) != 'main':
        raise MasaError('Go CLI entrypoint must declare package main')
    if files['go.mod'].strip() != f"module {spec['module']}\n\ngo {GO_VERSION}":
        raise MasaError('go.mod must use the approved module and Go 1.27.0 without dependencies')
    preflight_go(files, spec['module'])
