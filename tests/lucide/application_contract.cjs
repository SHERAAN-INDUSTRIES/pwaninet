const assert=require('node:assert/strict');
const fs=require('node:fs');
const {icons}=require('../../static/vendor/lucide/lucide.min.js');
const catalog=require('./application-mappings.json');
const pascal=name=>name.replace(/[-_\s]+(.)/g,(_,c)=>c.toUpperCase()).replace(/^./,c=>c.toUpperCase());
for(const [old,name] of Object.entries(catalog.map)) assert.ok(Object.hasOwn(icons,pascal(name)),old+' -> '+name);
const batch=process.argv[2];
let files=0,locations=0;
for(const [label,entries] of Object.entries(catalog.batches)) {
 if(batch&&label!==batch)continue;
 for(const [file,mapping] of Object.entries(entries)) {
  const source=fs.readFileSync(file,'utf8');
  for(const [old,count] of Object.entries(mapping)) {
   assert.ok(source.includes('data-lucide="'+catalog.map[old]+'"') || source.includes('data-lucide="{%') || source.includes('vasto_icon_name'),file+': missing '+old);
   locations+=count;
  }
  for(const tag of source.matchAll(/<i\b[^>]*data-lucide="([^"{]+)"[^>]*>/g)) {
   assert.ok(Object.hasOwn(icons,pascal(tag[1])),file+': unknown '+tag[1]);
   assert.ok(!/\bbi-/.test(tag[0]),file+': competing Bootstrap glyph');
   assert.ok(tag[0].includes('aria-hidden="true"')||tag[0].includes('aria-label='),file+': unlabelled icon');
  }
  if(file.endsWith('.html')) {
   // The migration must not alter routes, forms, or permission/filter conditions.
   const baseline='/tmp/wp3-baseline/'+file;
   if(fs.existsSync(baseline)) {
    const original=fs.readFileSync(baseline,'utf8');
    for(const regex of [/{%\s*url\b[\s\S]*?%}/g,/{%\s*(?:if|elif|else|endif|for|endfor)\b[\s\S]*?%}/g])
     assert.deepEqual(source.match(regex),original.match(regex),file+': template logic changed');
   }
  }
  files++;
 }
}
console.log(`PASS ${batch||'all'}: ${locations} reviewed icon references in ${files} files; ${Object.keys(catalog.map).length} registry-validated mappings`);
