#!/usr/bin/env node
'use strict';

const { spawn } = require('node:child_process');
const path = require('node:path');

const PLATFORM_MAP = {
  'darwin-arm64': 'manus-cli-darwin-arm64',
  'darwin-x64': 'manus-cli-darwin-x64',
  'linux-x64': 'manus-cli-linux-x64',
  'linux-arm64': 'manus-cli-linux-arm64',
  // pacote npm chama-se "windows" (não "win32"): o registry do npm sinaliza
  // "win32" como padrão de spam/malware em publicações novas (confirmado
  // testando o mesmo binário sob os dois nomes) — "win32-x64" aqui continua
  // sendo só a chave interna de detecção de plataforma do Node.
  'win32-x64': 'manus-cli-windows-x64',
};

// No Windows, o Node só emula SIGINT/SIGBREAK/SIGHUP de forma limitada, e
// child.kill(signal) força o encerramento imediato do processo-filho
// independente do sinal pedido (ver docs do Node: "Signal Events" e
// "Sending Messages" no child_process). Só encaminhamos os sinais que o
// Windows realmente entende, pra não fingir uma semântica POSIX que não existe.
const FORWARDED_SIGNALS =
  process.platform === 'win32' ? ['SIGINT', 'SIGBREAK', 'SIGHUP'] : ['SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT'];

function platformKey() {
  return `${process.platform}-${process.arch}`;
}

function resolveBinaryPath() {
  const key = platformKey();
  const pkgName = PLATFORM_MAP[key];
  if (!pkgName) {
    const supported = Object.keys(PLATFORM_MAP).join(', ');
    throw new LauncherError(
      `plataforma não suportada (${key}).\n` +
        `Plataformas suportadas via npm: ${supported}.\n` +
        `Alternativa: instale via pipx — veja https://github.com/bymarquin/manus-cli#instalação`
    );
  }

  let pkgJsonPath;
  try {
    pkgJsonPath = require.resolve(`${pkgName}/package.json`);
  } catch {
    throw new LauncherError(
      `o pacote nativo "${pkgName}" não foi instalado.\n` +
        'Isso acontece se a instalação usou --no-optional/--omit=optional, ou se o npm\n' +
        'pulou optionalDependencies para essa plataforma. Reinstale com "npm install manus-cli"\n' +
        'sem excluir dependências opcionais, ou use pipx — veja https://github.com/bymarquin/manus-cli#instalação'
    );
  }

  const pkgDir = path.dirname(pkgJsonPath);
  const binName = process.platform === 'win32' ? 'manus.exe' : 'manus';
  return path.join(pkgDir, 'bin', binName);
}

class LauncherError extends Error {}

function main() {
  let binaryPath;
  try {
    binaryPath = resolveBinaryPath();
  } catch (err) {
    if (err instanceof LauncherError) {
      process.stderr.write(`manus-cli: ${err.message}\n`);
      process.exit(1);
    }
    throw err;
  }

  const args = process.argv.slice(2);
  const child = spawn(binaryPath, args, { stdio: 'inherit' });

  const onSignal = (signal) => () => {
    child.kill(signal);
  };
  const signalHandlers = new Map();
  for (const signal of FORWARDED_SIGNALS) {
    const handler = onSignal(signal);
    signalHandlers.set(signal, handler);
    process.on(signal, handler);
  }

  const cleanupSignalHandlers = () => {
    for (const [signal, handler] of signalHandlers) {
      process.removeListener(signal, handler);
    }
  };

  child.on('error', (err) => {
    cleanupSignalHandlers();
    process.stderr.write(`manus-cli: falha ao executar o binário nativo: ${err.message}\n`);
    process.exitCode = 1;
  });

  child.on('exit', (code, signal) => {
    cleanupSignalHandlers();
    if (signal) {
      // reproduce termination-by-signal on our own process, same as the child experienced
      try {
        process.kill(process.pid, signal);
      } catch {
        // Windows não reconhece todo sinal POSIX em process.kill (ENOSYS) — cai pra exit code.
        process.exitCode = 1;
      }
      return;
    }
    process.exitCode = code === null ? 1 : code;
  });
}

if (require.main === module) {
  main();
}

module.exports = { resolveBinaryPath, platformKey, PLATFORM_MAP, LauncherError };
