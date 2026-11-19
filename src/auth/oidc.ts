/** STAGE-15.3 —— OIDC 登录(PKCE, 零依赖)。2026-11-19 起登录源从 Keycloak 换成服务器上统一的 Authelia
 *  (ZL00 合并登录，KM-需-1208)；Authelia 用 groups 声明带 repo:<仓库> 分组。
 *  未登录:只显示公开卡片(默认组 KMOS/LinzeHomeHub 可见的)
 *  已登录:按 token 里的 repo_access 组显示该用户有权的项目 */
const ISSUER = 'https://kmbid.linzezhang.com/authelia';
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
    client_id: CLIENT_ID, response_type: 'code', scope: 'openid email profile groups',
    redirect_uri: window.location.origin + '/', code_challenge: await sha256(verifier),
    code_challenge_method: 'S256', state: rand(),
  });
  window.location.href = `${ISSUER}/api/oidc/authorization?${p}`;
}

export function logout() {
  localStorage.removeItem(KEY);
  // Authelia 只允许跳回自己的票据域名，这里退出本站后回首页；Authelia 的登录票到期自动失效。
  window.location.href = window.location.origin + '/';
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
      const r = await fetch(`${ISSUER}/api/oidc/token`, {
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
  const groups: string[] = c.repo_access || c.groups || [];
  const repos = groups.filter((g) => /^\/?repo:/.test(g)).map((g) => g.replace(/^\/?repo:/, ''));
  return { name: c.name || c.preferred_username, email: c.email, repos };
}
