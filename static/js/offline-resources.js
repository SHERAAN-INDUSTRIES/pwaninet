/* Localize dependencies without changing page elements or their layout. */
(() => {
    const pending = new Map(), complete = new Map(), deferredMedia = new Set(), attemptedMedia = new Set();
    let mediaRoute=location.pathname+location.search;
    const owner = document.body?.dataset.userId;
    if (!owner) return;
    const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))].map(byte => byte.toString(16).padStart(2,'0')).join('');
    const workerSave = async (path, blob) => new Promise((resolve,reject) => {
        const channel = new MessageChannel();
        const timer=setTimeout(()=>reject(new Error('Asset cache timed out')),15000);
        channel.port1.onmessage=event=>{clearTimeout(timer);event.data?.saved?resolve():reject(new Error('Asset not saved'));channel.port1.close();};
        navigator.serviceWorker.controller.postMessage({type:'SAVE_OFFLINE_RESOURCE',payload:{owner,path,blob}},[channel.port2]);
    });
    async function resource(value, base=location.href) {
        if(!value || /^(?:data:|#|https:\/\/(?:localhost|vasto-debug)\/_capacitor_file_|\/__offline_resource__\/)/i.test(value))return {path:value};
        const url=new URL(value,base);url.hash='';
        if(!/^https?:$/.test(url.protocol))return {path:value};
        if(!pending.has(url.href))pending.set(url.href,(async()=>{
            if(window.vastoNativeOffline)try{const asset=await window.vastoNativeOffline.resource(url.href);complete.set(url.href,asset);return asset;}catch(_){/* Keep the browser snapshot path usable during rollout/network failures. */}
            const response=await fetch(url.href,{credentials:url.origin===location.origin?'same-origin':'omit',mode:'cors'});
            if(!response.ok)throw new Error('Asset unavailable');
            const blob=await response.blob();
            const max=/^(?:video|audio)\//.test(blob.type)?30*1024*1024:8*1024*1024;
            if(!blob.size || blob.size>max)throw new Error('Asset too large');
            const path='/__offline_resource__/'+await hash(url.href);
            await workerSave(path,blob);
            const asset={path,type:blob.type,text:blob.type.startsWith('text/css')?await blob.text():undefined};complete.set(url.href,asset);return asset;
        })().catch(error=>{pending.delete(url.href);throw error;}));
        return pending.get(url.href);
    }
    const cssData = text => 'data:text/css;charset=utf-8;base64,'+btoa(unescape(encodeURIComponent(text)));
    async function css(text,base,depth=0) {
        if(depth>4)return text;
        const matches=[...text.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)];
        for(const match of matches) {
            if(!match[2] || /^(?:data:|#)/i.test(match[2]))continue;
            try {
                const asset=await resource(match[2],base);
                const path=asset.type?.startsWith('text/css')?cssData(await css(asset.text,new URL(match[2],base).href,depth+1)):asset.path;
                text=text.replace(match[0],`url("${path}")`);
            } catch(_) {
                if(/[?&](?:token|signature|expires|x-amz-[^=]+)=/i.test(match[2]))text=text.replace(match[0],'none');
            }
        }
        // Quoted @import rules do not necessarily use url().
        for(const match of [...text.matchAll(/@import\s+(['"])([^'"]+)\1/gi)]) {
            try { const asset=await resource(match[2],base); if(asset.text)text=text.replace(match[0],`@import url("${cssData(await css(asset.text,new URL(match[2],base).href,depth+1))}")`); } catch(_) {}
        }
        return text;
    }
    window.vastoSnapshotResources={resource,startMedia(){
        const batch=[...deferredMedia].filter(value=>!attemptedMedia.has(value)).slice(0,Math.max(0,5-attemptedMedia.size));deferredMedia.clear();batch.forEach(value=>attemptedMedia.add(value));
        setTimeout(()=>batch.forEach(value=>resource(value).then(()=>document.dispatchEvent(new Event('vasto:offline-resource-ready'))).catch(()=>{})),0);
    },async localize(clone){
        const route=location.pathname+location.search;
        if(route!==mediaRoute){mediaRoute=route;attemptedMedia.clear();complete.clear();pending.clear();}
        const originals=[...document.querySelectorAll('link[rel="stylesheet"]')];
        const copies=[...clone.querySelectorAll('link[rel="stylesheet"]')];
        for(let i=0;i<copies.length;i++) {
            const original=originals[i],copy=copies[i];if(!original?.href)continue;
            try {
                let text;
                try {text=[...original.sheet.cssRules].map(rule=>rule.cssText).join('\n');}catch(_){text=(await resource(original.href)).text;}
                if(text){copy.href=cssData(await css(text,original.href));copy.removeAttribute('integrity');}
            }catch(_){}
        }
        for(const style of clone.querySelectorAll('style'))style.textContent=await css(style.textContent,location.href);
        for(const node of clone.querySelectorAll('[style]')) {
            if(node.getAttribute('style').includes('url('))node.setAttribute('style',await css(node.getAttribute('style'),location.href));
        }
        const sourceImages=[...document.querySelectorAll('img')],images=[...clone.querySelectorAll('img')];
        await Promise.all(images.map(async(image,index)=>{
            const original=sourceImages.find(source=>source.getAttribute('src')===image.getAttribute('src') && source.getAttribute('srcset')===image.getAttribute('srcset'));
            const value=image.dataset.src || original?.currentSrc || image.getAttribute('src');
            if(!value)return;
            try {image.src=(await resource(value)).path;image.removeAttribute('srcset');image.removeAttribute('loading');}catch(_){}
        }));
        const originalMedia=[...document.querySelectorAll('video,audio')];
        let mediaIndex=0;
        for(const video of clone.querySelectorAll('video,audio')) {
            const original=originalMedia[mediaIndex++];
            if(video.poster)try{video.poster=(await resource(video.poster)).path;}catch(_){}
            const value=video.dataset.src || original?.dataset.src || video.dataset.videoUrl || original?.dataset.videoUrl || original?.currentSrc || original?.getAttribute('src') || video.getAttribute('src') || video.querySelector('source[src]')?.getAttribute('src');
            if(!value || value.startsWith('blob:') || video.dataset.offlineDownloadId)continue;
            try {
                const key=new URL(value,location.href).href;
                const asset=complete.get(key);
                if(!asset){deferredMedia.add(key);continue;}
                video.src=asset.path;if(video.dataset.src)video.dataset.src=asset.path;video.dataset.videoUrl=asset.path;video.dataset.offlineMediaReady='true';
                video.querySelectorAll('source').forEach(source=>source.src=asset.path);
            }catch(_){}
        }
    }};
})();
