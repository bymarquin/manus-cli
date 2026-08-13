'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const launcher = require('../bin/manus.js');
const { resolveBinaryPath, platformKey, PLATFORM_MAP, LauncherError } = launcher;

function withFakePlatform(platform, arch, fn) {
  const origPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
  const origArch = Object.getOwnPropertyDescriptor(process, 'arch');
  Object.defineProperty(process, 'platform', { value: platform, configurable: true });
  Object.defineProperty(process, 'arch', { value: arch, configurable: true });
  try {
    return fn();
  } finally {
    Object.defineProperty(process, 'platform', origPlatform);
    Object.defineProperty(process, 'arch', origArch);
  }
}

test('rejeita plataforma/arquitetura não suportada com mensagem clara', () => {
  withFakePlatform('freebsd', 'x64', () => {
    assert.throws(
      () => resolveBinaryPath(),
      (err) => err instanceof LauncherError && /plataforma não suportada \(freebsd-x64\)/.test(err.message)
    );
  });
});

for (const [key, pkgName] of Object.entries(PLATFORM_MAP)) {
  test(`seleciona o pacote nativo correto para ${key}`, () => {
    const [platform, arch] = key.split(/-(.+)/); // split only on first hyphen
    withFakePlatform(platform, arch, () => {
      // nenhum pacote nativo real está instalado neste ambiente de teste isolado,
      // então a ausência do pacote é o sinal observável de que o nome foi resolvido certo.
      assert.throws(
        () => resolveBinaryPath(),
        (err) => err instanceof LauncherError && err.message.includes(pkgName) && err.message.includes('não foi instalado')
      );
    });
  });
}

function makeFakeNativePackage(tmpDir, pkgName, scriptBody) {
  const pkgDir = path.join(tmpDir, pkgName);
  fs.mkdirSync(path.join(pkgDir, 'bin'), { recursive: true });
  fs.writeFileSync(path.join(pkgDir, 'package.json'), JSON.stringify({ name: pkgName, version: '0.0.0' }));
  const binName = process.platform === 'win32' ? 'manus.exe' : 'manus';
  const binPath = path.join(pkgDir, 'bin', binName);
  fs.writeFileSync(binPath, scriptBody, { mode: 0o755 });
  return binPath;
}

test('repassa argv e o exit code do binário nativo', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'manus-launcher-'));
  try {
    const key = platformKey();
    const pkgName = PLATFORM_MAP[key];
    assert.ok(pkgName, `plataforma de teste não suportada pelo launcher: ${key}`);
    makeFakeNativePackage(
      tmp,
      pkgName,
      '#!/usr/bin/env node\nconsole.log(JSON.stringify(process.argv.slice(2)));\nprocess.exit(7);\n'
    );

    const launcherPath = path.join(__dirname, '..', 'bin', 'manus.js');
    const result = spawnSync(process.execPath, [launcherPath, '--foo', 'bar', 'baz'], {
      env: { ...process.env, NODE_PATH: tmp },
      encoding: 'utf8',
    });

    assert.equal(result.status, 7);
    assert.deepEqual(JSON.parse(result.stdout.trim()), ['--foo', 'bar', 'baz']);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test(
  'encerra por sinal quando o binário nativo recebe SIGTERM',
  { skip: process.platform === 'win32' ? 'sinais POSIX não se aplicam no Windows' : false },
  async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'manus-launcher-'));
    try {
      const key = platformKey();
      const pkgName = PLATFORM_MAP[key];
      assert.ok(pkgName, `plataforma de teste não suportada pelo launcher: ${key}`);
      makeFakeNativePackage(tmp, pkgName, '#!/usr/bin/env node\nsetInterval(() => {}, 1000);\n');

      const launcherPath = path.join(__dirname, '..', 'bin', 'manus.js');
      const child = spawn(process.execPath, [launcherPath], {
        env: { ...process.env, NODE_PATH: tmp },
      });

      await new Promise((resolve) => setTimeout(resolve, 400));
      child.kill('SIGTERM');

      const [, signal] = await new Promise((resolve) => {
        child.on('exit', (code, sig) => resolve([code, sig]));
      });

      assert.equal(signal, 'SIGTERM');
    } finally {
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
);
