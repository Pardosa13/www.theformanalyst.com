#!/usr/bin/env node
const path = require('path');
const { pathToFileURL } = require('url');

async function main() {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (error) {
    console.error('Playwright is not installed. Run: npm install --save-dev playwright && npx playwright install chromium');
    process.exit(1);
  }

  const root = __dirname;
  const shotsDir = path.join(root, 'screenshots');
  const pages = ['dashboard', 'racing', 'afl', 'ufc'];
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 }, deviceScaleFactor: 1 });

  for (const name of pages) {
    const url = pathToFileURL(path.join(root, `${name}.html`)).href;
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.screenshot({ path: path.join(shotsDir, `${name}.png`), fullPage: true });
    console.log(`Saved design-preview/screenshots/${name}.png`);
  }

  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
