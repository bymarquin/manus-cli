#!/usr/bin/env node
'use strict';

// Fonte única de versão: VERSION (na raiz do repo). pyproject.toml lê esse mesmo
// arquivo via [tool.setuptools.dynamic], então não há como o lado Python divergir.
// Este script garante que cada package.json em npm/ (e os pins de optionalDependencies
// do launcher) casam exatamente com VERSION.

const fs = require('node:fs');
const path = require('node:path');

const repoRoot = path.join(__dirname, '..');
const version = fs.readFileSync(path.join(repoRoot, 'VERSION'), 'utf8').trim();

const npmDir = path.join(repoRoot, 'npm');
const packages = fs
  .readdirSync(npmDir)
  .filter((name) => fs.existsSync(path.join(npmDir, name, 'package.json')))
  .sort();

let hasError = false;

for (const name of packages) {
  const pkgPath = path.join(npmDir, name, 'package.json');
  const pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8'));

  if (pkg.version !== version) {
    console.error(`✗ ${name}: package.json version "${pkg.version}" != VERSION "${version}"`);
    hasError = true;
  }

  for (const [depName, depRange] of Object.entries(pkg.optionalDependencies || {})) {
    if (depRange !== version) {
      console.error(
        `✗ ${name}: optionalDependencies["${depName}"] = "${depRange}" != VERSION "${version}" (precisa ser exata, sem range)`
      );
      hasError = true;
    }
  }
}

if (hasError) {
  console.error(`\nDrift de versão detectado. Fonte única é VERSION ("${version}").`);
  process.exit(1);
}

console.log(`✓ ${packages.length} pacote(s) npm sincronizados com VERSION ("${version}").`);
