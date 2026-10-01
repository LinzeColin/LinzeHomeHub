import type { Project } from '../types';
import type { Session } from './oidc';

/** 公开仓（GitHub 上 PUBLIC）的项目人人可见：这些项目的网站本来就对公网开放，不该要求登录才看得到入口。
 *  已登录:另外加上该用户 repo_access 覆盖的私有仓项目;无 repo 字段者视为公开 */
const PUBLIC_REPOS = ['KMOS', 'LinzeHomeHub', 'MetaDatabase'];

export function filterProjects(all: Project[], session: Session | null): Project[] {
  const allowed = new Set([...PUBLIC_REPOS, ...(session ? session.repos : [])]);
  return all.filter((p) => {
    const repo = (p as any).repo as string | undefined;
    if (!repo) return true;                 // 未标注归属 = 公开入口
    return allowed.has(repo);
  });
}
