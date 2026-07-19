import type { Project } from '../types';
import type { Session } from './oidc';

/** 未登录:仅默认公开仓(KMOS/LinzeHomeHub)的项目
 *  已登录:该用户 repo_access 覆盖的项目;无 repo 字段者视为公开 */
const PUBLIC_REPOS = ['KMOS', 'LinzeHomeHub'];

export function filterProjects(all: Project[], session: Session | null): Project[] {
  const allowed = session ? session.repos : PUBLIC_REPOS;
  return all.filter((p) => {
    const repo = (p as any).repo as string | undefined;
    if (!repo) return true;                 // 未标注归属 = 公开入口
    return allowed.includes(repo);
  });
}
