/** STAGE-15.3 —— account.linzezhang.com OIDC 登录(PKCE, 零依赖)
 *  未登录:只显示公开卡片(默认组 KMOS/LinzeHomeHub 可见的)
 *  已登录:按 token 里的 repo_access 组显示该用户有权的项目 */
const ISSUER = 'https://account.linzezhang.com/realms/linze';
const CLIENT_ID = 'home';
const KEY = 'linze_home_token';

export interface Session { name?: string; email?: string; repos: string[]; }

const b64url = (b: ArrayBuffer) =>
  btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

async function sha256(s: string) {
  return b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
}
const rand = () => b64url(crypto.getRandomValues(new Uint8Array(32)).buffer);

export async function login() {
  const verifier = rand();
  sessionStorage.setItem('pkce_v', verifier);
  const p = new URLSearchParams({
    client_id: CLIENT_ID, response_type: 'code', scope: 'openid email profile',
    redirect_uri: window.location.origin + '/', code_challenge: await sha256(verifier),
    code_challenge_method: 'S256', state: rand(),
  });
  window.location.href = `${ISSUER}/protocol/openid-connect/auth?${p}`;
}

export function logout() {
  localStorage.removeItem(KEY);
  window.location.href =
    `${ISSUER}/protocol/openid-connect/logout?post_logout_redirect_uri=${encodeURIComponent(window.location.origin + '/')}&client_id=${CLIENT_ID}`;
}

function decode(jwt: string): any {
  try { return JSON.parse(atob(jwt.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))); }
  catch { return {}; }
}

/** 回调后换 token;返回当前会话(未登录为 null) */
export async function initSession(): Promise<Session | null> {
  const code = new URLSearchParams(window.location.search).get('code');
  if (code) {
    const verifier = sessionStorage.getItem('pkce_v') || '';
    try {
      const r = await fetch(`${ISSUER}/protocol/openid-connect/token`, {
        method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'authorization_code', client_id: CLIENT_ID, code,
          redirect_uri: window.location.origin + '/', code_verifier: verifier,
        }),
      });
      if (r.ok) localStorage.setItem(KEY, (await r.json()).id_token || '');
    } catch { /* 网络失败则按未登录处理 */ }
    history.replaceState({}, '', window.location.origin + '/');
  }
  const tok = localStorage.getItem(KEY);
  if (!tok) return null;
  const c = decode(tok);
  if (c.exp && c.exp * 1000 < Date.now()) { localStorage.removeItem(KEY); return null; }
  const repos = (c.repo_access || []).map((g: string) => g.replace(/^\/?repo:/, ''));
  return { name: c.name || c.preferred_username, email: c.email, repos };
}
