import {defineConfig} from '@playwright/test';
export default defineConfig({
 testDir:'./e2e', fullyParallel:false, workers:1, timeout:60000,
 expect:{timeout:10000}, reporter:[['list'],['html',{open:'never',outputFolder:'../artifacts/playwright-report'}]],
 outputDir:'../artifacts/test-results',
 use:{baseURL:'http://127.0.0.1:5188',channel:process.env.PLAYWRIGHT_CHANNEL==='chromium'?undefined:'chrome',headless:true,viewport:{width:1440,height:1080},screenshot:'only-on-failure',trace:'retain-on-failure'},
 webServer:{command:'cd .. && DEMO_MODE=1 PORT=5188 PUBLIC_URL=http://127.0.0.1:5188 uv run --locked python scripts/e2e_server.py',url:'http://127.0.0.1:5188/api/demo/personas',reuseExistingServer:false,timeout:30000}
});
