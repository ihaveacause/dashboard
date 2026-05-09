/**
 * render.mjs — Remotion render script for I Have a Cause (Sprint 5)
 * Usage: node render.mjs <path/to/data.json> <path/to/output.mp4>
 */

import { bundle }                          from '@remotion/bundler';
import { renderMedia, selectComposition }  from '@remotion/renderer';
import { readFileSync, existsSync }        from 'fs';
import { join, dirname, resolve }          from 'path';
import { fileURLToPath }                   from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

const dataFile   = process.argv[2];
const outputFile = process.argv[3];

if (!dataFile || !outputFile) {
  console.error('Usage: node render.mjs <data.json> <output.mp4>');
  process.exit(1);
}

if (!existsSync(dataFile)) {
  console.error(`Data file not found: ${dataFile}`);
  process.exit(1);
}

const data = JSON.parse(readFileSync(dataFile, 'utf-8'));
const lang = data.language || 'unknown';

console.log('━'.repeat(60));
console.log(`🎬 Rendering: ${lang.toUpperCase()} video`);
console.log(`   Story: ${(data.storyTitle || '').slice(0, 55)}`);
console.log(`   Output: ${outputFile}`);
console.log('━'.repeat(60));

try {
  // 1. Bundle the React/Remotion project
  console.log('\n📦 Bundling Remotion project...');
  const bundled = await bundle({
    entryPoint : join(__dirname, 'src/index.jsx'),
    publicDir  : join(__dirname, 'public'),
    webpackOverride: (config) => config,
  });
  console.log('   ✅ Bundle ready');

  // 2. Select composition
  console.log('\n🎨 Selecting composition...');
  const composition = await selectComposition({
    serveUrl   : bundled,
    id         : 'VideoShort',
    inputProps : { data },
  });
  console.log(`   ✅ ${composition.durationInFrames} frames @ ${composition.fps}fps`);
  console.log(`   ✅ ${composition.width}×${composition.height}px`);

  // 3. Render
  console.log('\n🖥️  Rendering frames...');
  let lastPct = -1;
  await renderMedia({
    composition,
    serveUrl      : bundled,
    codec         : 'h264',
    outputLocation: outputFile,
    inputProps    : { data },
    concurrency   : 2,
    videoBitrate  : '2500k',
    audioBitrate  : '128k',
    onProgress: ({ progress }) => {
      const pct = Math.floor(progress * 100);
      if (pct !== lastPct && pct % 5 === 0) {
        process.stdout.write(`   [${pct.toString().padStart(3)}%] ${'█'.repeat(pct / 5)}${' '.repeat(20 - pct / 5)}\r`);
        lastPct = pct;
      }
    },
  });

  console.log('\n');
  console.log(`✅ Render complete → ${outputFile}`);
  process.exit(0);

} catch (err) {
  console.error('\n❌ Render failed:', err.message);
  if (err.stack) console.error(err.stack.split('\n').slice(1, 4).join('\n'));
  process.exit(1);
}
