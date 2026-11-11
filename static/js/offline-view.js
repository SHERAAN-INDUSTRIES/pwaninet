/* Local-only behavior for the original saved DOM. No original app/network scripts are replayed. */
(() => {
    const url=new URL(location.href);['offline_saved','view_cache','cached'].forEach(key=>url.searchParams.delete(key));
    const notice=document.createElement('div');notice.dataset.offlineIndicator='true';
    notice.textContent='You are offline viewing cached pages';
    notice.style.cssText='position:fixed;bottom:calc(8px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:2147483647;font:11px system-ui;color:var(--text-muted,#64748b);white-space:nowrap;pointer-events:none';
    document.body.append(notice);
    function placeNotice(){
        let offset=8;
        document.querySelectorAll('.mobile-bottom-nav,.pwanimate-composer-wrapper.input-area').forEach(node=>{
            const rect=node.getBoundingClientRect();
            if(rect.height && rect.top<innerHeight && getComputedStyle(node).display!=='none')offset=Math.max(offset,innerHeight-rect.top+6);
        });
        notice.style.bottom=offset+'px';
    }
    requestAnimationFrame(placeNotice);window.addEventListener('resize',placeNotice);
    navigator.serviceWorker?.addEventListener('message',event=>{
        if(event.data?.type==='PRIVATE_OFFLINE_INVALIDATED') {
            // Access revocation clears private content, but never presents a blocking offline screen.
            document.querySelector('#page-content-target,main')?.replaceChildren();
        }
    });
    const style=document.createElement('style');
    style.textContent='.mobile-bottom-nav.vasto-scroll-hidden{transform:translateY(calc(100% + 30px))!important}.navbar-top.vasto-scroll-hidden{transform:translateY(-120%)!important}[data-mobile-nav-position="top"] .mobile-bottom-nav.vasto-scroll-hidden{transform:translateY(-220%)!important}';
    document.head.append(style);
    const navigation=[...document.querySelectorAll('.navbar-top,.mobile-bottom-nav')];
    const keepUI='.dropdown,.dropdown-menu,[data-bs-toggle],[data-bs-dismiss],[data-offline-theme],video,audio,.media-play-toggle';
    function navigate(value) {
        const target=new URL(value,location.href);if(target.origin!==location.origin)return;
        if(target.hash && target.pathname===location.pathname){document.querySelector(target.hash)?.scrollIntoView();return;}
        ['offline_saved','view_cache','cached'].forEach(key=>target.searchParams.delete(key));
        if(navigator.onLine===false)target.searchParams.set('offline_saved','1');
        location.assign(target.href);
    }
    function theme(value) {
        localStorage.setItem('theme',value);
        const resolved=value==='system'?(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light'):value;
        document.documentElement.dataset.theme=resolved;document.documentElement.dataset.bsTheme=resolved;
        window.AndroidBridge?.setSystemBarTheme?.(resolved);
    }
    const savedTheme=localStorage.getItem('theme');
    if(['light','dark','system'].includes(savedTheme))theme(savedTheme);
    document.addEventListener('click',event=>{
        const target=event.target.closest('button,a,[role="button"]');if(!target)return;
        if(target.matches('.vn-play-btn,.audio-track-play-btn')) {
            event.preventDefault();
            const audio=target.closest('.voice-note-bubble,.audio-track-bubble')?.querySelector('audio[data-offline-media-ready="true"]');
            if(audio){audio.paused?audio.play().catch(()=>{}):audio.pause();}
            return;
        }
        if(target.dataset.offlineTheme){event.preventDefault();theme(target.dataset.offlineTheme);return;}
        if(target.matches(keepUI)||target.closest(keepUI))return;
        if(target.dataset.offlineHref){event.preventDefault();navigate(target.dataset.offlineHref);return;}
        if(target.tagName==='A' && target.getAttribute('href') && !target.hasAttribute('download')) {
            event.preventDefault();navigate(target.getAttribute('href'));return;
        }
        if(target.id==='back-to-top-btn'){event.preventDefault();window.scrollTo({top:0,behavior:'smooth'});return;}
        if(target.id==='chatScrollToBottomBtn'){const list=document.querySelector('#messagesContainer');if(list)list.scrollTo({top:list.scrollHeight,behavior:'smooth'});return;}
        if(target.hasAttribute('hx-get')){event.preventDefault();navigate(target.getAttribute('hx-get'));return;}
        // Mutation controls retain their original DOM and appearance, but cannot send requests.
        if(target.closest('form') || target.hasAttribute('hx-post') || target.hasAttribute('hx-delete'))event.preventDefault();
    },true);
    document.addEventListener('submit',event=>{event.preventDefault();},true);
    document.querySelectorAll('form').forEach(form=>form.title=form.id==='messaging-composer-form'?'Connect to send messages':form.title);
    document.querySelectorAll('textarea').forEach(field=>field.addEventListener('input',()=>{
        field.style.height='auto';field.style.height=Math.min(160,field.scrollHeight)+'px';
    }));
    // Preserve functional navigation/menu buttons; Bootstrap's bundled JS owns dropdowns/collapse.
    document.querySelectorAll('video,audio').forEach(media=>{
        media.removeAttribute('autoplay');media.preload='metadata';
        if(media.dataset.offlineDownloadId){media.src='/offline-media-stream/'+encodeURIComponent(media.dataset.offlineDownloadId);media.controls=true;}
        else if(media.dataset.offlineMediaReady==='true') {
            const bubble=media.closest('.voice-note-bubble,.audio-track-bubble');
            if(bubble) {
                const button=bubble.querySelector('.vn-play-btn,.audio-track-play-btn');
                const icon=button?.querySelector('i');
                button?.classList.remove('vn-download-mode','audio-download-mode');
                if(button){button.title='Play';button.setAttribute('aria-label','Play');}
                bubble.querySelector('.audio-track-status')?.remove();
                const update=()=>{if(icon)icon.className=(media.paused?'bi bi-play-fill ':'bi bi-pause-fill ')+(bubble.matches('.voice-note-bubble')?'vn-play-icon':'track-play-icon');};
                ['play','pause','ended'].forEach(event=>media.addEventListener(event,update));update();
            } else media.controls=true;
        }
        media.addEventListener('error',()=>{media.controls=false;},{once:true});
        media.addEventListener('play',()=>document.querySelectorAll('video,audio').forEach(other=>{if(other!==media)other.pause();}));
    });
    document.addEventListener('click',event=>{
        const stage=event.target.closest('.reel-tap-hitbox,.reel-stage-overlay-btn');
        if(stage){const media=stage.closest('.reel-stage-container,.reel-post-card,.post-card')?.querySelector('video');if(media){event.preventDefault();media.paused?media.play().catch(()=>{}):media.pause();}}
    });
    document.querySelectorAll('img').forEach(image=>image.addEventListener('error',()=>{
        // A transparent fallback preserves the original image box rather than a broken-image icon.
        image.src='data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/%3E';
    },{once:true}));
    const savedKey='vasto-snapshot-scroll:'+url.pathname+url.search;
    let restore;
    try {restore=JSON.parse(sessionStorage.getItem(savedKey)||[...document.querySelectorAll('meta[name="vasto-scroll"]')].at(-1)?.content||'null');}catch(_){}
    function restoreScroll(){
        if(!restore)return;
        window.scrollTo(restore.window?.x||0,restore.window?.y||0);
        Object.entries(restore.elements||{}).forEach(([id,position])=>{const node=document.getElementById(id);if(node){node.scrollLeft=position.x;node.scrollTop=position.y;}});
    }
    requestAnimationFrame(restoreScroll);window.addEventListener('load',restoreScroll,{once:true});
    let lastY=window.scrollY,scrollTimer;
    function position(){
        const value={window:{x:window.scrollX,y:window.scrollY},elements:{}};
        document.querySelectorAll('[id]').forEach(node=>{if(node.scrollTop||node.scrollLeft)value.elements[node.id]={x:node.scrollLeft,y:node.scrollTop};});
        sessionStorage.setItem(savedKey,JSON.stringify(value));
        window.Capacitor?.Plugins?.OfflineStore?.rememberSnapshot({url:url.href,scroll:JSON.stringify(value)}).catch(()=>{});
    }
    document.addEventListener('scroll',event=>{
        clearTimeout(scrollTimer);scrollTimer=setTimeout(position,200);
        if(event.target!==document && event.target!==document.documentElement && event.target!==document.body)return;
        const y=window.scrollY,delta=y-lastY;
        if(Math.abs(delta)>4){navigation.forEach(nav=>nav.classList.toggle('vasto-scroll-hidden',y>100&&delta>0));lastY=y;setTimeout(placeNotice,250);}
    },{capture:true,passive:true});
    window.addEventListener('pagehide',position);
    const pill=document.querySelector('.mobile-bottom-nav .nav-chip-pill'),active=document.querySelector('.mobile-bottom-nav .nav-chip.active');
    if(pill&&active){const parent=pill.parentElement.getBoundingClientRect(),rect=active.getBoundingClientRect();pill.style.width=rect.width+'px';pill.style.transform=`translate3d(${rect.left-parent.left}px,0,0)`;}
    let reconnecting=false,leaving=false;
    window.addEventListener('beforeunload',()=>{leaving=true;});
    async function reconnect(){
        if(!navigator.onLine || reconnecting)return;reconnecting=true;
        try{
            const response=await fetch(url.href,{cache:'no-store'});
            if(!leaving && response.ok && response.headers.get('X-Vasto-Offline')==='rendered-v1' && !response.headers.has('X-Vasto-Saved-At')){position();location.replace(url.href);}
        }catch(_){}finally{reconnecting=false;}
    }
    window.addEventListener('online',reconnect);
    reconnect();const retry=setInterval(reconnect,15000);window.addEventListener('pagehide',()=>clearInterval(retry),{once:true});
    window.AndroidBridge?.notifyPageReady?.();
})();
