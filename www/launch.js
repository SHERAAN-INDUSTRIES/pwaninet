window.showVastoLaunchState = state => {
    const params = new URLSearchParams(location.search);
    const route = params.get('route');
    if (state.signedOut) {
        document.querySelector('#state').textContent = 'You are signed out. Connect to sign in again. Managed saved pages are unavailable after sign-out.';
    } else if (state.returning) {
        document.querySelector('#title').textContent = 'Your saved Vasto';
        document.querySelector('#state').textContent = 'Open previously visited pages, or connect to refresh them. A network outage keeps your last saved view available.';
    }
    if (route) {
        document.querySelector('#state').textContent = 'This page could not be reached. Connect for an initial visit, or open a previously saved page using navigation.';
    }
    const resume = document.querySelector('#resume');
    const path = route || state.route || '/';
    const url = new URL(path, 'https://pwaninet.app');
    if (url.origin === 'https://pwaninet.app') {
        url.searchParams.set('offline_saved', '1');
        resume.href = url.href;
    }
    if (state.returning && !route && !sessionStorage.getItem('vasto-resumed')) {
        sessionStorage.setItem('vasto-resumed', '1');
        // Let the local navigation paint before asking the hosted worker for a saved route.
        setTimeout(() => location.assign(resume.href), 150);
    }
};
if (window.AndroidBridge?.notifyPageReady) window.AndroidBridge.notifyPageReady();
