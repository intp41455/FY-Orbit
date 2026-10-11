import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../web/package.json',import.meta.url));
const {chromium}=require('playwright');
const dir=path.resolve('deploy/cloudflare/dist');
const out=path.resolve('evidence/audit-20261011');
await fs.mkdir(out,{recursive:true});
const server=http.createServer(async(req,res)=>{
 try { const p=path.resolve(dir,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname === '/' ? '/index.html' : new URL(req.url,'http://localhost').pathname));
 if(!p.startsWith(dir+path.sep)) {res.writeHead(403);res.end();return}
 const b=await fs.readFile(p);
 res.setHeader('Content-Type',p.endsWith('.html')?'text/html; charset=utf-8':p.endsWith('.webp')?'image/webp':'application/octet-stream');
 res.end(b);
 }catch{res.writeHead(404);res.end()}
});
await new Promise(r=>server.listen(8138,'127.0.0.1',r));
let browser;
try {
 browser=await chromium.launch({headless:true});
 const page=await browser.newPage();
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8138',{waitUntil:'networkidle'});
 await page.waitForTimeout(3000);
 const svg=await page.locator('#architecture-graph').innerHTML();
 if(svg.includes('<svg'))await fs.writeFile(path.resolve('.runtime/audit-architecture.svg'),svg);
 await page.locator('#architecture').screenshot({path:path.join(out,'architecture.png')});
 const views=[];
 for(const width of [1440,390]){
 await page.setViewportSize({width,height:900});
 views.push(await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,
 rendered:!!document.querySelector('#architecture-graph svg'),
 fallback:!document.querySelector('#architecture-fallback').hidden,
 deadLinks:document.querySelectorAll('a[href="#"]').length,
 missingImages:[...document.images].filter(i=>!i.complete||i.naturalWidth===0).map(i=>i.getAttribute('src'))})));
 await page.screenshot({path:path.join(out,'landing-'+width+'.png'),fullPage:true});
 }
 // 真服务与真实访客入口：页面正常加载不能被算作模型执行通过。
 await page.goto('http://127.0.0.1:8137/workbench',{waitUntil:'networkidle'});
 const workbench={url:page.url(),title:await page.title(),body:(await page.locator('body').innerText()).slice(0,700)};
 await page.screenshot({path:path.join(out,'workbench-start.png'),fullPage:true});
 const result={views,errors,workbench};
 console.log(JSON.stringify(result,null,2));
 await fs.writeFile(path.join(out,'browser.json'),JSON.stringify(result,null,2));
 if(views.some(v=>v.scrollWidth>v.width||v.deadLinks||v.missingImages.length))process.exitCode=1;
} finally {if(browser)await browser.close();await new Promise(r=>server.close(r))}
