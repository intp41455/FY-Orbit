import fs from 'node:fs'; import path from 'node:path';
const ALLOW=new Set(['src/styles/tokens.css','src/components/cabin/gameplay/cozyGameplay.css']);
function walk(d,o=[]){for(const e of fs.readdirSync(d,{withFileTypes:true})){if(['node_modules','dist'].includes(e.name)||e.name.startsWith('.'))continue;const f=path.join(d,e.name);e.isDirectory()?walk(f,o):o.push(f);}return o;}
function strip(s){let o='',i=0;while(i<s.length){const t=s.slice(i,i+2);if(t==='//'){while(i<s.length&&s[i]!=='\n')i++;continue;}if(t==='/*'){i+=2;while(i<s.length&&s.slice(i,i+2)!=='*/'){if(s[i]==='\n')o+='\n';i++;}i+=2;continue;}o+=s[i];i++;}return o;}
// green = G channel clearly dominant over R and B
function isGreenHex(h){const x=h.slice(1);let r,g,b;if(x.length===3){r=parseInt(x[0]+x[0],16);g=parseInt(x[1]+x[1],16);b=parseInt(x[2]+x[2],16);}else if(x.length>=6){r=parseInt(x.slice(0,2),16);g=parseInt(x.slice(2,4),16);b=parseInt(x.slice(4,6),16);}else return false;return g>r+18&&g>b+18;}
function isGreenRgb(m){const n=m.map(Number);return n.length>=3&&n[1]>n[0]+18&&n[1]>n[2]+18;}
const files=walk('src').filter(f=>/\.(css|tsx|ts)$/.test(f));
let n=0;
for(const f of files){const rel=f.split(path.sep).join('/');if(ALLOW.has(rel))continue;const src=strip(fs.readFileSync(f,'utf8'));src.split('\n').forEach((line,i)=>{
 const hits=[];
 for(const m of line.matchAll(/#(?:[0-9a-fA-F]{3,8})\b/g)) if(isGreenHex(m[0])) hits.push(m[0]);
 for(const m of line.matchAll(/rgba?\(([^)]+)\)/g)){const p=m[1].split(/[,\s/]+/).filter(Boolean);if(isGreenRgb(p))hits.push(m[0]);}
 for(const w of ['green','lime','olive','forestgreen','seagreen','mediumseagreen']) if(new RegExp(`(?<![-\\w])${w}(?![-\\w])`,'i').test(line)) hits.push(w);
 if(hits.length){n++;console.log(`${rel}:${i+1}  ${hits.join(' ')}  | ${line.trim().slice(0,88)}`);}
});}
console.log(`\nTOTAL green assignments (tokens.css + pixel-art excluded): ${n}`);
