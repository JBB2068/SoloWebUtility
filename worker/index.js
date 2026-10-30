import htmlContent from './page.html';

async function handleR2Request(request, env, r2Key, contentType, disposition, filename) {
  const rangeHeader = request.headers.get('range');
  let object;
  if (rangeHeader) {
    object = await env.DOWNLOADS_BUCKET.get(r2Key, {
      range: request.headers,
      onlyIf: request.headers,
    });
  } else {
    object = await env.DOWNLOADS_BUCKET.get(r2Key, {
      onlyIf: request.headers,
    });
  }

  if (object === null) {
    return new Response('File Not Found', { status: 404 });
  }

  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set('etag', object.httpEtag);
  headers.set('Accept-Ranges', 'bytes');
  headers.set('Cache-Control', 'public, max-age=3600');
  if (contentType) {
    headers.set('Content-Type', contentType);
  }
  if (disposition && filename) {
    headers.set('Content-Disposition', `${disposition}; filename="${filename}"`);
  }

  if (object.range) {
    const start = object.range.offset;
    const end = object.range.offset + object.range.length - 1;
    headers.set('Content-Range', `bytes ${start}-${end}/${object.size}`);
    headers.set('Content-Length', object.range.length.toString());
    return new Response(object.body, { status: 206, headers });
  }

  headers.set('Content-Length', object.size.toString());
  return new Response(object.body, { status: 200, headers });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const decodedPath = decodeURIComponent(url.pathname);

    // Root landing page
    if (url.pathname === '/' || url.pathname === '/index.html') {
      return new Response(htmlContent, {
        headers: {
          'Content-Type': 'text/html; charset=utf-8',
          'Cache-Control': 'public, max-age=300',
        },
      });
    }

    // Installers: one file each in R2, served under its own name
    const EXE_NAME = 'Solo Web Utility v1.2.1 Installer.exe';
    const APK_NAME = 'Solo Web Utility v1.2.1 Installer.apk';
    const MAC_NAME = 'Solo Web Utility v1.2.1 (Mac).zip';

    if (decodedPath === '/' + EXE_NAME) {
      return await handleR2Request(request, env, EXE_NAME, 'application/octet-stream', 'attachment', EXE_NAME);
    }
    if (decodedPath === '/' + APK_NAME) {
      return await handleR2Request(request, env, APK_NAME, 'application/vnd.android.package-archive', 'attachment', APK_NAME);
    }
    if (decodedPath === '/' + MAC_NAME) {
      return await handleR2Request(request, env, MAC_NAME, 'application/zip', 'attachment', MAC_NAME);
    }

    // Old installer links (earlier names, and any earlier version such as
    // "Solo Web Utility v1.2.1 Installer.exe" in an older built-in manual) redirect to the current files
    const OLD_EXE = /^\/Solo Web Utility (v\d+\.\d+\.\d+ )?Installer\.exe$|^\/Solo Web Utility v\d+\.\d+\.\d+\.exe$/;
    const OLD_APK = /^\/Solo Web Utility (v\d+\.\d+\.\d+ )?Installer\.apk$|^\/Solo Web Utility v\d+\.\d+\.\d+\.apk$/;
    if (OLD_EXE.test(decodedPath) || url.pathname === '/deploy_to_solo.exe') {
      return Response.redirect(new URL('/' + encodeURIComponent(EXE_NAME), url).toString(), 302);
    }
    if (OLD_APK.test(decodedPath)) {
      return Response.redirect(new URL('/' + encodeURIComponent(APK_NAME), url).toString(), 302);
    }
    const OLD_MAC = /^\/Solo Web Utility (v\d+\.\d+\.\d+ )?\(Mac\)\.zip$/;
    if (OLD_MAC.test(decodedPath)) {
      return Response.redirect(new URL('/' + encodeURIComponent(MAC_NAME), url).toString(), 302);
    }

    // SoloLink Viewer app (separate app, not the installer)
    if (url.pathname === '/sololink_viewer.apk') {
      return await handleR2Request(
        request, env, 'sololink_viewer.apk',
        'application/vnd.android.package-archive',
        'attachment',
        'sololink_viewer.apk'
      );
    }

    // Solo Drone Manual PDF (supports inline browser viewing and Range requests)
    if (decodedPath === '/3DR_Solo_User_Manual_Updated_2026.pdf') {
      return await handleR2Request(
        request, env, '3DR_Solo_User_Manual_Updated_2026.pdf',
        'application/pdf',
        'inline',
        '3DR_Solo_User_Manual_Updated_2026.pdf'
      );
    }

    // Solo Web Utility Owner's Manual PDF
    if (decodedPath === '/Solo_Web_Utility_Owners_Manual.pdf') {
      return await handleR2Request(
        request, env, 'Solo_Web_Utility_Owners_Manual.pdf',
        'application/pdf',
        'inline',
        'Solo_Web_Utility_Owners_Manual.pdf'
      );
    }

    // Solo Drone Manual HTML page
    if (decodedPath === '/solo_drone_manual.html') {
      return await handleR2Request(
        request, env, 'solo_drone_manual.html',
        'text/html; charset=utf-8'
      );
    }

    // Solo Web Utility Owner's Manual HTML page
    if (decodedPath === '/manual.html') {
      return await handleR2Request(
        request, env, 'manual.html',
        'text/html; charset=utf-8'
      );
    }

    // Proxy any other assets or paths to the pages deployment
    const targetUrl = new URL(url.pathname + url.search, 'https://open-solo.pages.dev');
    return fetch(targetUrl, request);
  },
};
